"""LLM에 보내기 전에 명백히 안 맞는 공고를 규칙으로 걸러낸다.

LLM 호출 횟수(=시간, 무료 등급 한도)를 아끼는 게 목적이라 일부러 보수적으로 만들었다.
걸러진 공고도 지우지 않고 이유를 남겨서, 리포트 마지막에 따로 보여준다.

규칙
1. 본문이 너무 짧으면 제외 (수집 실패 또는 OCR로도 못 읽은 이미지 공고)
2. '신입' 또는 '경력무관' 표기가 있으면 통과
3. 그런 표기 없이 '경력 N년 이상'(또는 'N~M년')을 요구하고 N이 --max-years보다 크면 제외
   단, 바로 뒤에 '우대'가 붙은 경우는 필수 요건이 아니므로 무시
4. 경력 표기를 못 찾으면 통과시키고 LLM이 판단하게 둔다

사용 예:
    python src/prefilter.py            # 기본: 필수 경력 3년 요구까지 통과
"""
import argparse
import re

from db import get_conn

MIN_TEXT_LENGTH = 200
ENTRY_OK = re.compile(r"신입|경력\s*무관")
EXP_MIN = re.compile(r"경력\s*(\d{1,2})\s*년\s*(?:이상|↑|\+)")
EXP_RANGE = re.compile(r"경력\s*(\d{1,2})\s*년?\s*[~∼\-]\s*\d{1,2}\s*년")


def required_years(text: str) -> list[int]:
    years = []
    for pattern in (EXP_MIN, EXP_RANGE):
        for m in pattern.finditer(text):
            if "우대" in text[m.end():m.end() + 8]:
                continue
            years.append(int(m.group(1)))
    return years


def judge(text: str, max_years: int):
    if not text or len(text) < MIN_TEXT_LENGTH:
        return False, "본문 부족(수집 실패 또는 이미지 공고)"
    if ENTRY_OK.search(text):
        return True, "신입/경력무관 표기 있음"
    years = required_years(text)
    if years:
        need = min(years)
        if need > max_years:
            return False, f"경력 {need}년 이상 요구"
        return True, f"경력 {need}년 요구(허용 범위)"
    return True, "경력 표기 없음, LLM이 판단"


def main():
    parser = argparse.ArgumentParser(description="경력 요건 사전 필터")
    parser.add_argument("--max-years", type=int, default=3, help="이 연차까지 요구하는 공고는 통과")
    parser.add_argument("--redo", action="store_true", help="이미 판정한 공고도 다시 판정")
    args = parser.parse_args()

    where = "raw_text IS NOT NULL AND raw_text != ''"
    if not args.redo:
        where += " AND prefilter_pass IS NULL"

    passed = failed = 0
    with get_conn() as conn:
        rows = conn.execute(f"SELECT url, raw_text FROM postings WHERE {where}").fetchall()
        for row in rows:
            ok, reason = judge(row["raw_text"], args.max_years)
            conn.execute("UPDATE postings SET prefilter_pass=?, prefilter_reason=? WHERE url=?",
                         (int(ok), reason, row["url"]))
            passed += ok
            failed += not ok

    print(f"판정 {len(rows)}건: 통과 {passed}, 제외 {failed}")
    print("다음 단계: python src/evaluate.py")


if __name__ == "__main__":
    main()
