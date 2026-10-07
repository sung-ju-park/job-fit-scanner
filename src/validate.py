"""LLM 점수가 내 판단과 얼마나 맞는지 검증한다.

1) sample : 평가된 공고 중 20건을 점수 구간(높음/중간/낮음)별로 고르게 뽑는다.
            상위권만 뽑으면 '좋은 공고를 좋다고 하는지'만 보게 되기 때문.
2) label  : 뽑힌 공고를 하나씩 보여주고 지원/보류/제외를 직접 고른다.
            LLM 점수는 일부러 보여주지 않는다(점수를 보면 판단이 끌려간다).
3) report : 모델별 일치율, Cohen's kappa, 순위 상관(Spearman), 혼동행렬,
            그리고 판단이 엇갈린 공고 목록을 reports/validation.md로 만든다.

사용 예:
    python src/validate.py sample --model ollama:qwen2.5:3b --n 20
    python src/validate.py label
    python src/validate.py report
"""
import argparse
import random
import sys
from datetime import datetime

from db import REPORT_DIR, get_conn
from textutil import focus_text

VERDICTS = ["apply", "hold", "skip"]
VERDICT_KO = {"apply": "지원", "hold": "보류", "skip": "제외"}
ORDINAL = {"apply": 2, "hold": 1, "skip": 0}
APPLY_MIN, HOLD_MIN = 70, 40  # 점수 -> 판단 변환 기준


def score_to_verdict(score: int) -> str:
    if score >= APPLY_MIN:
        return "apply"
    if score >= HOLD_MIN:
        return "hold"
    return "skip"


# ---------- sample ----------
def cmd_sample(args):
    with get_conn() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        if existing and not args.reset:
            sys.exit(f"이미 샘플 {existing}건이 있습니다. 다시 뽑으려면 --reset (기존 판단도 지워짐)")
        if args.reset:
            conn.execute("DELETE FROM labels")
        rows = conn.execute("SELECT url, score FROM evaluations WHERE model=?", (args.model,)).fetchall()

    if len(rows) < args.n:
        sys.exit(f"{args.model} 평가 결과가 {len(rows)}건뿐이라 {args.n}건을 뽑을 수 없습니다.")

    bins = {v: [r["url"] for r in rows if score_to_verdict(r["score"]) == v] for v in VERDICTS}
    rng = random.Random(args.seed)
    for urls in bins.values():
        rng.shuffle(urls)

    picked = []
    while len(picked) < args.n and any(bins.values()):
        for v in VERDICTS:  # 구간을 돌아가며 하나씩
            if bins[v] and len(picked) < args.n:
                picked.append(bins[v].pop())
    rng.shuffle(picked)  # 점수 순서가 드러나지 않게 섞기

    with get_conn() as conn:
        conn.executemany("INSERT INTO labels (url) VALUES (?)", [(u,) for u in picked])
    counts = {VERDICT_KO[v]: sum(score_to_verdict(r["score"]) == v for r in rows if r["url"] in picked)
              for v in VERDICTS}
    print(f"샘플 {len(picked)}건 저장 (LLM 기준 구간 분포: {counts})")
    print("다음 단계: python src/validate.py label")


# ---------- label ----------
def cmd_label(args):
    with get_conn() as conn:
        rows = conn.execute("""SELECT l.url, p.title, p.company, p.raw_text FROM labels l
                               JOIN postings p ON p.url = l.url
                               WHERE l.my_verdict IS NULL""").fetchall()
    if not rows:
        print("판단할 공고가 없습니다. (모두 끝났거나 sample을 먼저 실행해야 함)")
        return
    print(f"남은 공고 {len(rows)}건. 공고 원문은 URL로 브라우저에서 보는 게 편합니다.")
    print("입력: 1=지원  2=보류  3=제외  s=건너뛰기  q=종료\n")

    for i, row in enumerate(rows, 1):
        print("=" * 70)
        print(f"[{i}/{len(rows)}] {row['company']} | {row['title']}")
        print(row["url"])
        print("-" * 70)
        print(focus_text(row["raw_text"], args.chars))
        print("-" * 70)
        while True:
            key = input("판단 (1/2/3/s/q): ").strip().lower()
            if key in ("1", "2", "3", "s", "q"):
                break
        if key == "q":
            break
        if key == "s":
            continue
        verdict = VERDICTS[int(key) - 1]
        note = input("메모 (판단 이유, 엔터로 생략): ").strip()
        with get_conn() as conn:
            conn.execute("UPDATE labels SET my_verdict=?, my_note=?, labeled_at=? WHERE url=?",
                         (verdict, note, datetime.now().isoformat(timespec="seconds"), row["url"]))
    print("\n다음 단계: python src/validate.py report")


# ---------- report ----------
def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    r = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def cohen_kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(v) / n) * (b.count(v) / n) for v in VERDICTS)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def cmd_report(args):
    with get_conn() as conn:
        labels = {r["url"]: r for r in conn.execute("""
            SELECT l.url, l.my_verdict, l.my_note, p.title, p.company FROM labels l
            JOIN postings p ON p.url = l.url WHERE l.my_verdict IS NOT NULL""")}
        evals = conn.execute("SELECT * FROM evaluations WHERE url IN (SELECT url FROM labels)").fetchall()
    if not labels:
        sys.exit("직접 판단한 공고가 없습니다. label을 먼저 진행하세요.")

    by_model = {}
    for e in evals:
        if e["url"] in labels:
            by_model.setdefault(e["model"], {})[e["url"]] = e

    lines = [f"# LLM 평가 검증 결과 ({datetime.now():%Y-%m-%d})", "",
             f"- 직접 판단한 공고: {len(labels)}건",
             f"- 점수 -> 판단 변환: {APPLY_MIN}점 이상 지원, {HOLD_MIN}~{APPLY_MIN - 1}점 보류, 그 미만 제외", ""]
    summary = []
    for model, ev in sorted(by_model.items()):
        urls = [u for u in labels if u in ev]
        if len(urls) < len(labels):
            lines.append(f"> {model}: 판단한 {len(labels)}건 중 {len(urls)}건만 평가돼 있음\n")
        mine = [labels[u]["my_verdict"] for u in urls]
        llm = [score_to_verdict(ev[u]["score"]) for u in urls]
        exact = sum(a == b for a, b in zip(mine, llm)) / len(urls)
        near = sum(abs(ORDINAL[a] - ORDINAL[b]) <= 1 for a, b in zip(mine, llm)) / len(urls)
        kappa = cohen_kappa(mine, llm)
        rho = spearman([ev[u]["score"] for u in urls], [ORDINAL[m] for m in mine])
        summary.append((model, len(urls), exact, near, kappa, rho))

        lines += [f"## {model}", "",
                  "| 지표 | 값 |", "|---|---|",
                  f"| 판단 일치율 (3단계) | {exact:.0%} |",
                  f"| 한 단계 이내 일치율 | {near:.0%} |",
                  f"| Cohen's kappa | {kappa:.2f} |",
                  f"| Spearman (점수 vs 내 판단) | {rho:.2f} |", "",
                  "혼동행렬 (행: 내 판단, 열: LLM)", "",
                  "| | " + " | ".join(VERDICT_KO[v] for v in VERDICTS) + " |",
                  "|---|" + "---|" * len(VERDICTS)]
        for mv in VERDICTS:
            cells = [sum(1 for a, b in zip(mine, llm) if a == mv and b == lv) for lv in VERDICTS]
            lines.append(f"| {VERDICT_KO[mv]} | " + " | ".join(map(str, cells)) + " |")
        lines += ["", "### 판단이 엇갈린 공고", ""]
        for u in urls:
            m, l = labels[u]["my_verdict"], score_to_verdict(ev[u]["score"])
            if m != l:
                lines.append(f"- **{labels[u]['company']}** {labels[u]['title']}  ")
                lines.append(f"  내 판단 {VERDICT_KO[m]} / LLM {ev[u]['score']}점({VERDICT_KO[l]})  ")
                lines.append(f"  LLM 이유: {ev[u]['reason']}  ")
                if labels[u]["my_note"]:
                    lines.append(f"  내 메모: {labels[u]['my_note']}")
        lines.append("")

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / "validation.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    # 콘솔 요약에는 회사명을 넣지 않는다 (README에 그대로 옮겨 적기 좋게)
    print(f"{'모델':<32}{'건수':>5}{'일치율':>8}{'1단계내':>8}{'kappa':>8}{'spearman':>10}")
    for model, n, exact, near, kappa, rho in summary:
        print(f"{model:<32}{n:>5}{exact:>8.0%}{near:>8.0%}{kappa:>8.2f}{rho:>10.2f}")
    print(f"\n상세 결과: {out}")


def main():
    parser = argparse.ArgumentParser(description="LLM 평가 검증")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--model", required=True, help="예: ollama:qwen2.5:3b")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reset", action="store_true")
    p = sub.add_parser("label")
    p.add_argument("--chars", type=int, default=1500, help="화면에 보여줄 본문 길이")
    sub.add_parser("report")
    args = parser.parse_args()
    {"sample": cmd_sample, "label": cmd_label, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
