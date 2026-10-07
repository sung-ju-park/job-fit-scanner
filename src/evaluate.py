"""사전 필터를 통과한 공고를 내 프로필과 비교해 LLM으로 적합도를 평가한다.

결과는 evaluations 테이블에 '백엔드:모델' 이름으로 저장되므로, 같은 공고를
Ollama와 Gemini로 각각 평가해 나중에 비교할 수 있다. 이미 평가한 공고는 건너뛴다.

사용 예:
    python src/evaluate.py                    # 기본: Gemini
    python src/evaluate.py --backend ollama   # 로컬 모델 (인터넷·API 한도 없이 쓸 때)
"""
import argparse
import os
import time

from db import ROOT, get_conn, load_env
from llm import BACKENDS, DEFAULT_DELAYS, DEFAULT_MODELS, LLMError, LLMTimeout, QuotaExceeded, parse_json
from textutil import focus_text

SYSTEM_PROMPT = """너는 신입·주니어 개발자 채용 공고를 검토하는 채용 담당자다.
지원자 프로필과 채용공고 본문을 비교해서 이 지원자가 서류를 낼 만한지 평가한다.

규칙
- 공고 본문에 실제로 적힌 내용만 근거로 삼는다. 적혀 있지 않은 요건을 추측하지 않는다.
- 지원자 프로필에 없는 경험을 있다고 가정하지 않는다.
- 한 공고에서 여러 부문(직무)을 모집하면, 지원자에게 가장 잘 맞는 부문 하나를 골라
  그 부문의 업무와 자격요건만 기준으로 평가한다. 다른 부문 때문에 점수를 깎지 않는다.
  이때 reason은 반드시 "[부문명]"으로 시작한다. 맞는 부문이 하나도 없으면 "[해당 없음]"으로 시작한다.
- 경력 요건은 필수인지 우대인지 구분한다. 필수 경력 연차가 지원자보다 높으면 점수를 크게 낮춘다.
  단, '또는 이에 준하는 역량', '관련 프로젝트 경험' 같은 대체 경로가 명시돼 있으면 그만큼 감안한다.

점수 기준 (match_score, 0~100)
- 80~100: 핵심 업무와 필수 요건 대부분을 프로필 경험으로 설명할 수 있고 신입/주니어 지원이 가능
- 50~79: 일부 요건이 맞지만 중요한 기술이나 경험이 빠져 있음
- 20~49: 핵심 기술 스택이나 경력 요건이 맞지 않음
- 0~19: 직군 자체가 다름 (영업, 디자인, 하드웨어 설계 등)

반드시 아래 JSON 형식으로만 답한다.
{
  "match_score": 정수,
  "junior_ok": true 또는 false,
  "reason": "[평가 기준 부문명] 점수를 준 핵심 이유 한두 문장",
  "matched_projects": ["공고와 관련 있는 프로필 속 프로젝트·경험 이름"],
  "missing_skills": ["공고가 요구하지만 프로필에 없는 기술·경험"]
}"""

USER_TEMPLATE = """[지원자 프로필]
{profile}

[채용공고]
제목: {title}
회사: {company}
본문:
{body}"""


def load_profile() -> str:
    path = ROOT / "profile.md"
    if not path.exists():
        raise SystemExit("profile.md가 없습니다. profile.example.md를 복사해서 내 정보로 채워주세요.")
    return path.read_text(encoding="utf-8")


def as_text(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value or "")


def normalize(result: dict) -> dict:
    score = int(float(result.get("match_score", 0)))
    junior = result.get("junior_ok")
    if isinstance(junior, str):
        junior = junior.strip().lower() in ("true", "yes", "예", "가능")
    return {
        "score": max(0, min(100, score)),
        "junior_ok": int(bool(junior)),
        "reason": as_text(result.get("reason")),
        "matched_projects": as_text(result.get("matched_projects")),
        "missing_skills": as_text(result.get("missing_skills")),
    }


def main():
    parser = argparse.ArgumentParser(description="LLM 적합도 평가")
    parser.add_argument("--backend", choices=BACKENDS.keys(), default="gemini")
    parser.add_argument("--model", help="기본: gemini=gemini-3.5-flash-lite, ollama=qwen2.5:3b")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--delay", type=float, help="호출 사이 대기(초). 기본: ollama 0, gemini 5")
    parser.add_argument("--only-sample", action="store_true", help="검증 샘플(labels)에 든 공고만 평가")
    parser.add_argument("--redo", action="store_true",
                        help="이미 평가한 공고도 다시 평가 (profile.md나 프롬프트를 고친 뒤 사용)")
    parser.add_argument("--max-chars", type=int, default=4000,
                        help="LLM에 보낼 공고 본문 최대 글자 수. 모델 비교 시에는 같은 값을 쓸 것")
    args = parser.parse_args()

    load_env()
    model = args.model or DEFAULT_MODELS[args.backend]
    model_key = f"{args.backend}:{model}"
    delay = args.delay if args.delay is not None else DEFAULT_DELAYS[args.backend]
    call = BACKENDS[args.backend]
    profile = load_profile()

    sample_clause = "AND p.url IN (SELECT url FROM labels)" if args.only_sample else ""
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT p.url, p.title, p.company, p.raw_text FROM postings p
            WHERE p.prefilter_pass = 1 {sample_clause}
              {"" if args.redo else "AND p.url NOT IN (SELECT url FROM evaluations WHERE model = ?)"}
            LIMIT ?""", ((args.limit,) if args.redo else (model_key, args.limit))).fetchall()
    print(f"평가 대상 {len(rows)}건, 모델 {model_key}{' (재평가)' if args.redo else ''}")

    done = errors = 0
    for i, row in enumerate(rows, 1):
        user_msg = USER_TEMPLATE.format(profile=profile, title=row["title"],
                                        company=row["company"], body=focus_text(row["raw_text"], args.max_chars))
        started = time.time()
        result = None
        for attempt in range(2):  # JSON이 깨지면 한 번 더 시도
            try:
                result = normalize(parse_json(call(model, SYSTEM_PROMPT, user_msg)))
                break
            except QuotaExceeded as e:
                print(f"\n{e} 지금까지 {done}건 저장됨. 내일 다시 실행하면 이어서 평가합니다.")
                return
            except LLMTimeout as e:
                print(f"[{i}] 시간 초과로 건너뜀: {row['company']} ({e})")
                break  # 같은 공고를 다시 시도하면 또 오래 걸리므로 재시도하지 않음
            except (LLMError, ValueError, KeyError) as e:
                print(f"[{i}] 응답 처리 실패 ({attempt + 1}/2): {e}")
        if result is None:
            errors += 1
            continue

        with get_conn() as conn:
            conn.execute("""INSERT OR REPLACE INTO evaluations
                (url, model, score, junior_ok, reason, matched_projects, missing_skills)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row["url"], model_key, result["score"], result["junior_ok"], result["reason"],
                 result["matched_projects"], result["missing_skills"]))
        done += 1
        print(f"[{i}] {result['score']:>3}점 ({time.time() - started:.1f}s) {row['company']} | {row['title'][:35]}")
        time.sleep(delay)

    print(f"\n완료. 저장 {done}건, 실패 {errors}건")
    print("다음 단계: python src/validate.py sample --model " + model_key)


if __name__ == "__main__":
    main()
