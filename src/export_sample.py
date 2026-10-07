"""검증 표본(labels에 뽑힌 공고)을 텍스트 파일로 내보낸다. AI 점수는 넣지 않는다.

다른 사람(또는 다른 AI)에게 판단을 부탁할 때 쓴다. 결과는 reports/validation_sample.txt
(reports 폴더는 .gitignore에 있어 GitHub에 올라가지 않음)

사용 예:
    python src/export_sample.py
"""
from db import REPORT_DIR, get_conn
from textutil import focus_text


def main():
    with get_conn() as conn:
        rows = conn.execute("""SELECT p.rec_idx, p.company, p.title, p.url, p.raw_text
                               FROM labels l JOIN postings p ON p.url = l.url
                               ORDER BY l.rowid""").fetchall()
    if not rows:
        raise SystemExit("검증 표본이 없습니다. validate.py sample을 먼저 실행하세요.")
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / "validation_sample.txt"
    parts = []
    for i, r in enumerate(rows, 1):
        parts.append(f"===== [{i}] rec_idx={r['rec_idx']} =====\n"
                     f"회사: {r['company']}\n제목: {r['title']}\nURL: {r['url']}\n\n"
                     f"{focus_text(r['raw_text'] or '', 6000)}\n")
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(rows)}건 저장: {out}")


if __name__ == "__main__":
    main()
