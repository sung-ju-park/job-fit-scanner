"""파이프라인 단계별 진행 현황을 보여준다. 사람인에 접속하지 않는다.

사용 예:
    python src/stats.py
"""
from db import DB_PATH, get_conn


def main():
    if not DB_PATH.exists():
        print("아직 DB가 없습니다. scrape_list.py부터 실행하세요.")
        return
    with get_conn() as conn:
        def q(sql):
            return conn.execute(sql).fetchone()[0]

        listed = q("SELECT COUNT(*) FROM postings")
        detailed = q("SELECT COUNT(*) FROM postings WHERE raw_text != ''")
        short = q("SELECT COUNT(*) FROM postings WHERE raw_text != '' AND LENGTH(raw_text) < 200")
        ocr = q("SELECT COUNT(*) FROM postings WHERE used_ocr = 1")
        passed = q("SELECT COUNT(*) FROM postings WHERE prefilter_pass = 1")
        failed = q("SELECT COUNT(*) FROM postings WHERE prefilter_pass = 0")
        sampled = q("SELECT COUNT(*) FROM labels")
        labeled = q("SELECT COUNT(*) FROM labels WHERE my_verdict IS NOT NULL")

        print(f"목록 수집        {listed}건")
        print(f"상세 수집 완료   {detailed}건 (본문 200자 미만 {short}건, OCR {ocr}건)")
        print(f"사전 필터        통과 {passed}건 / 제외 {failed}건")
        for r in conn.execute("SELECT model, COUNT(*) c, ROUND(AVG(score), 1) a FROM evaluations GROUP BY model"):
            print(f"LLM 평가         {r['model']}: {r['c']}건 (평균 {r['a']}점)")
        print(f"검증 샘플        {sampled}건 중 직접 판단 {labeled}건")


if __name__ == "__main__":
    main()
