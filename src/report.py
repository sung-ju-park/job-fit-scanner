"""평가 결과를 점수순 리포트로 만든다.

- reports/report_날짜.md : 마크다운 리포트 (GitHub 등에서 보기 좋음)
- reports/latest.html    : 브라우저로 바로 여는 리포트 (--html)

여러 모델로 평가했다면 기준 모델(--model) 점수로 정렬하고 다른 모델 점수도 옆에 보여준다.
--since를 주면 그 시각 이후에 평가된 공고만 담는다 (daily.py가 '오늘 새로 들어온 공고'용으로 사용).

사용 예:
    python src/report.py
    python src/report.py --html
    python src/report.py --html --anonymize   # README 캡처용 (회사명·공고 제목 가림)
"""
import argparse
import html
from datetime import datetime

from db import REPORT_DIR, get_conn


def cell(text, limit=80) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def load(args):
    with get_conn() as conn:
        models = [r["model"] for r in conn.execute(
            "SELECT model, COUNT(*) c FROM evaluations GROUP BY model ORDER BY c DESC")]
        if not models:
            return None
        base = args.model or models[0]
        since_eval = "AND e.evaluated_at >= ?" if args.since else ""
        params = [base, args.min_score] + ([args.since] if args.since else [])
        rows = conn.execute(f"""
            SELECT p.url, p.title, p.company, e.score, e.junior_ok, e.reason,
                   e.matched_projects, e.missing_skills
            FROM evaluations e JOIN postings p ON p.url = e.url
            WHERE e.model = ? AND e.score >= ? {since_eval}
            ORDER BY e.score DESC""", params).fetchall()
        scores = {(r["url"], r["model"]): r["score"] for r in conn.execute(
            "SELECT url, model, score FROM evaluations")}
        since_detail = "AND detail_at >= ?" if args.since else ""
        excluded = conn.execute(f"""SELECT title, company, url, prefilter_reason FROM postings
                                    WHERE prefilter_pass = 0 {since_detail} ORDER BY prefilter_reason""",
                                [args.since] if args.since else []).fetchall()
    others = [m for m in models if m != base]
    return base, others, rows, scores, excluded


def write_markdown(base, others, rows, scores, excluded, title):
    header = ["점수"] + [m.split(":", 1)[1] for m in others] + ["신입", "회사 / 공고", "관련 경험", "부족한 점", "이유"]
    lines = [f"# {title}", "",
             f"- 기준 모델: `{base}` / 평가 {len(rows)}건 / 사전 필터 제외 {len(excluded)}건", "",
             "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        extra = [str(scores.get((r["url"], m), "-")) for m in others]
        lines.append("| " + " | ".join(
            [str(r["score"])] + extra +
            ["O" if r["junior_ok"] else "X",
             f"{cell(r['company'], 20)}<br>[{cell(r['title'], 40)}]({r['url']})",
             cell(r["matched_projects"], 50), cell(r["missing_skills"], 50), cell(r["reason"], 90)]) + " |")
    if excluded:
        lines += ["", "## 사전 필터에서 제외된 공고", ""]
        lines += [f"- {r['prefilter_reason']}: {r['company']} | [{cell(r['title'], 50)}]({r['url']})" for r in excluded]
    out = REPORT_DIR / f"report_{datetime.now():%Y%m%d}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_html(base, rows, excluded, title, total, out_name="latest.html"):
    e = html.escape

    def band(score):
        return "high" if score >= 70 else "mid" if score >= 40 else "low"

    items = []
    for rank, r in enumerate(rows, 1):
        matched = e(r["matched_projects"] or "") or "없음"
        missing = e(r["missing_skills"] or "") or "없음"
        items.append(f"""
<li class="item band-{band(r['score'])}" data-score="{r['score']}" data-junior="{int(bool(r['junior_ok']))}" data-url="{e(r['url'])}">
  <div class="rank">{rank}</div>
  <div class="score" aria-label="{r['score']}점">{r['score']}</div>
  <div class="body">
    <p class="company">{e(r['company'])}{'<span class="tag">신입 가능</span>' if r['junior_ok'] else ''}</p>
    <h2><a href="{e(r['url'])}" target="_blank" rel="noopener">{e(r['title'])}</a></h2>
    <p class="reason">{e(r['reason'] or '')}</p>
    <details>
      <summary>관련 경험과 부족한 점</summary>
      <dl>
        <dt>관련 경험</dt><dd>{matched}</dd>
        <dt>부족한 점</dt><dd>{missing}</dd>
      </dl>
    </details>
    <label class="seen"><input type="checkbox"> 확인함</label>
  </div>
</li>""")

    high = sum(r["score"] >= 70 for r in rows)
    mid = sum(40 <= r["score"] < 70 for r in rows)
    junior = sum(bool(r["junior_ok"]) for r in rows)
    shown = f"상위 {len(rows)}건" if total > len(rows) else f"{len(rows)}건"
    summary = (f"전체 {total}건을 평가했고, 그중 점수 {shown}을 보여드려요. 70점 이상은 {high}건이에요."
               if rows else "새로 평가된 공고가 없어요. 다음 실행 때 새 공고가 올라오면 여기에 보여요.")
    ex = "".join(f"<li><span>{e(r['prefilter_reason'])}</span> {e(r['company'])}, "
                 f"<a href='{e(r['url'])}' target='_blank' rel='noopener'>{e(r['title'])}</a></li>" for r in excluded)
    excluded_block = (f"<details class='excluded'><summary>사전 필터에서 제외된 공고 {len(excluded)}건</summary>"
                      f"<ul>{ex}</ul></details>") if excluded else ""

    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root {{
  --bg: #F2F4F6; --paper: #FFFFFF; --ink: #1C2430; --sub: #5B6573; --line: #DDE2E8;
  --high: #1F6F5C; --high-soft: #E3F1EC; --mid: #A86F08; --mid-soft: #FBF1DC;
  --low: #8A94A1; --link: #2450A6;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: "Pretendard", "Malgun Gothic", system-ui, sans-serif;
  font-size: 16px; line-height: 1.6; -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 860px; margin: 0 auto; padding: 48px 24px 80px; }}
header h1 {{ font-size: 30px; line-height: 1.25; letter-spacing: -0.02em; margin: 0 0 8px; font-weight: 800; }}
header .model {{ color: var(--sub); font-size: 13px; margin: 0 0 14px; }}
header .summary {{ font-size: 17px; margin: 0 0 28px; max-width: 60ch; }}

.filters {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 28px; }}
.filters button {{
  font: inherit; font-size: 14px; border: 1px solid var(--line); background: var(--paper); color: var(--ink);
  padding: 7px 14px; border-radius: 999px; cursor: pointer;
}}
.filters button[aria-pressed="true"] {{ background: var(--ink); color: #fff; border-color: var(--ink); }}
.filters label {{ font-size: 14px; color: var(--sub); display: inline-flex; gap: 6px; align-items: center; margin-left: 6px; cursor: pointer; }}
button:focus-visible, a:focus-visible, input:focus-visible, summary:focus-visible {{ outline: 3px solid var(--link); outline-offset: 2px; }}

ol.list {{ list-style: none; margin: 0; padding: 0; background: var(--paper); border-radius: 14px; border: 1px solid var(--line); }}
.item {{ display: grid; grid-template-columns: 34px 76px 1fr; gap: 0 14px; padding: 22px 24px; border-top: 1px solid var(--line); }}
.item:first-child {{ border-top: 0; }}
.item[hidden] {{ display: none; }}
.rank {{ color: var(--sub); font-size: 14px; padding-top: 6px; font-variant-numeric: tabular-nums; }}
.score {{
  font-size: 34px; font-weight: 800; line-height: 1; letter-spacing: -0.03em; font-variant-numeric: tabular-nums;
  align-self: start; text-align: center; padding: 12px 0; border-radius: 10px;
}}
.band-high .score {{ color: var(--high); background: var(--high-soft); }}
.band-mid  .score {{ color: var(--mid);  background: var(--mid-soft); }}
.band-low  .score {{ color: var(--low);  background: var(--bg); }}
.body {{ min-width: 0; }}
.company {{ margin: 0; font-size: 14px; color: var(--sub); }}
.tag {{ margin-left: 8px; font-size: 12px; color: var(--high); border: 1px solid currentColor; border-radius: 4px; padding: 1px 6px; }}
.item h2 {{ font-size: 18px; line-height: 1.4; margin: 2px 0 8px; font-weight: 700; }}
.item h2 a {{ color: var(--ink); text-decoration: none; }}
.item h2 a:hover {{ color: var(--link); text-decoration: underline; text-underline-offset: 3px; }}
.reason {{ margin: 0 0 8px; max-width: 70ch; }}
details summary {{ cursor: pointer; color: var(--sub); font-size: 14px; }}
dl {{ margin: 8px 0 0; display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; font-size: 14px; }}
dt {{ color: var(--sub); }}
dd {{ margin: 0; }}
.seen {{ display: inline-flex; gap: 6px; align-items: center; margin-top: 10px; font-size: 13px; color: var(--sub); cursor: pointer; }}
.item.is-seen .score, .item.is-seen h2, .item.is-seen .reason {{ opacity: .45; }}

.empty {{ padding: 40px 24px; color: var(--sub); text-align: center; }}
.excluded {{ margin-top: 32px; font-size: 14px; color: var(--sub); }}
.excluded ul {{ padding-left: 18px; }}
.excluded li span {{ color: var(--ink); }}
.excluded a {{ color: var(--link); }}

@media (max-width: 600px) {{
  .wrap {{ padding: 28px 14px 60px; }}
  header h1 {{ font-size: 24px; }}
  .item {{ grid-template-columns: 64px 1fr; padding: 18px 16px; }}
  .rank {{ display: none; }}
  .score {{ font-size: 26px; padding: 10px 0; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{e(title)}</h1>
    <p class="model">평가 모델 {e(base)}</p>
    <p class="summary">{e(summary)}</p>
  </header>

  {'' if not rows else f"""<div class="filters" role="group" aria-label="공고 걸러보기">
    <button type="button" data-filter="all" aria-pressed="true">전체 {len(rows)}</button>
    <button type="button" data-filter="high" aria-pressed="false">70점 이상 {high}</button>
    <button type="button" data-filter="mid" aria-pressed="false">40~69점 {mid}</button>
    <label><input type="checkbox" id="onlyJunior"> 신입 가능만 ({junior})</label>
    <label><input type="checkbox" id="hideSeen"> 확인한 공고 숨기기</label>
  </div>"""}

  {f'<ol class="list">{"".join(items)}</ol>' if rows else '<div class="list empty">공고가 없어요.</div>'}
  {excluded_block}
</div>

<script>
(function () {{
  var items = Array.prototype.slice.call(document.querySelectorAll(".item"));
  var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-filter]"));
  var onlyJunior = document.getElementById("onlyJunior");
  var hideSeen = document.getElementById("hideSeen");
  var band = "all";
  var KEY = "jobfit-seen";

  function loadSeen() {{
    try {{ return JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (err) {{ return {{}}; }}
  }}
  function saveSeen(map) {{
    try {{ localStorage.setItem(KEY, JSON.stringify(map)); }} catch (err) {{}}
  }}
  var seen = loadSeen();

  function apply() {{
    items.forEach(function (li) {{
      var s = Number(li.dataset.score);
      var ok = band === "all" || (band === "high" && s >= 70) || (band === "mid" && s >= 40 && s < 70);
      if (onlyJunior && onlyJunior.checked && li.dataset.junior !== "1") ok = false;
      if (hideSeen && hideSeen.checked && seen[li.dataset.url]) ok = false;
      li.hidden = !ok;
    }});
  }}

  buttons.forEach(function (b) {{
    b.addEventListener("click", function () {{
      band = b.dataset.filter;
      buttons.forEach(function (x) {{ x.setAttribute("aria-pressed", String(x === b)); }});
      apply();
    }});
  }});
  if (onlyJunior) onlyJunior.addEventListener("change", apply);
  if (hideSeen) hideSeen.addEventListener("change", apply);

  items.forEach(function (li) {{
    var box = li.querySelector(".seen input");
    var url = li.dataset.url;
    box.checked = !!seen[url];
    li.classList.toggle("is-seen", box.checked);
    box.addEventListener("change", function () {{
      if (box.checked) seen[url] = 1; else delete seen[url];
      saveSeen(seen);
      li.classList.toggle("is-seen", box.checked);
      apply();
    }});
  }});
  apply();
}})();
</script>
</body>
</html>"""
    out = REPORT_DIR / out_name
    out.write_text(page, encoding="utf-8")
    return out


def anonymize(rows, excluded):
    """공개용(README 캡처 등)으로 회사명·공고 제목·링크를 가린다. 점수와 평가 내용은 그대로 둔다.
    공고 제목은 LLM이 이유 앞에 붙인 [부문명]으로 바꾼다."""
    import re

    def label(i):
        letters = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            letters = chr(65 + r) + letters
        return f"회사 {letters}"

    out = []
    for i, r in enumerate(rows):
        d = dict(r)
        m = re.match(r"\s*\[([^\]]+)\]", d.get("reason") or "")
        part = m.group(1).strip() if m else ""
        d["title"] = f"{part} 직무" if part and part != "해당 없음" else "모집 부문 비공개"
        reason = d.get("reason") or ""
        if d.get("company"):
            reason = reason.replace(d["company"], label(i))
        d["reason"] = reason
        d["company"] = label(i)
        d["url"] = "#"
        out.append(d)
    ex = [{"prefilter_reason": r["prefilter_reason"], "company": "회사 (가림)",
           "title": "공고 제목 (가림)", "url": "#"} for r in excluded]
    return out, ex


def main():
    parser = argparse.ArgumentParser(description="점수순 리포트 생성")
    parser.add_argument("--model", help="정렬 기준 모델. 생략하면 평가 건수가 가장 많은 모델")
    parser.add_argument("--min-score", type=int, default=0)
    parser.add_argument("--top", type=int, default=30, help="상위 몇 건까지 보여줄지 (0이면 전체)")
    parser.add_argument("--since", help="이 시각(UTC, 'YYYY-MM-DD HH:MM:SS') 이후 평가된 공고만")
    parser.add_argument("--html", action="store_true", help="reports/latest.html도 생성")
    parser.add_argument("--anonymize", action="store_true",
                        help="회사명·공고 제목·링크를 가린 공개용 HTML(reports/public.html)도 생성")
    args = parser.parse_args()

    data = load(args)
    if data is None:
        raise SystemExit("평가 결과가 없습니다. evaluate.py를 먼저 실행하세요.")
    base, others, rows, scores, excluded = data
    total = len(rows)
    if args.top:
        rows = rows[:args.top]
    label = "오늘의 새 공고" if args.since else "나에게 맞는 공고"
    title = f"{label} ({datetime.now():%Y-%m-%d})"

    REPORT_DIR.mkdir(exist_ok=True)
    print(f"리포트 저장: {write_markdown(base, others, rows, scores, excluded, title)}")
    if args.html:
        print(f"HTML 리포트: {write_html(base, rows, excluded, title, total)}")
    if args.anonymize:
        pub_rows, pub_ex = anonymize(rows, excluded)
        out = write_html(base, pub_rows, pub_ex, title + " (회사명·공고명 가림)", total, out_name="public.html")
        print(f"공개용 HTML: {out}")
    for r in rows[:5]:
        print(f"  {r['score']:>3}점  {r['company']} | {r['title'][:40]}")


if __name__ == "__main__":
    main()
