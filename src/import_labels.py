"""판단 결과 CSV(rec_idx, verdict, note)를 검증 표본(labels)에 넣는다.

verdict는 지원/보류/제외 또는 apply/hold/skip 중 하나.

사용 예:
    python src/import_labels.py reports/labels.csv
"""
import csv
import sys
from datetime import datetime

from db import get_conn

KO = {"지원": "apply", "보류": "hold", "제외": "skip"}


def main():
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python src/import_labels.py <CSV 경로>")
    with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    done, missing = 0, []
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        for r in rows:
            verdict = KO.get(r["verdict"].strip(), r["verdict"].strip())
            if verdict not in ("apply", "hold", "skip"):
                raise SystemExit(f"알 수 없는 판단값: {r['verdict']} (rec_idx={r['rec_idx']})")
            cur = conn.execute("""UPDATE labels SET my_verdict=?, my_note=?, labeled_at=?
                                  WHERE url IN (SELECT url FROM postings WHERE rec_idx=?)""",
                               (verdict, r.get("note", "").strip(), now, r["rec_idx"].strip()))
            if cur.rowcount:
                done += 1
            else:
                missing.append(r["rec_idx"])
    print(f"{done}건 저장")
    if missing:
        print(f"검증 표본에서 찾지 못한 공고: {', '.join(missing)}")
    print("다음 단계: python src/validate.py report")


if __name__ == "__main__":
    main()
