"""명령어 하나로 전체 과정을 돌린다. 두 가지 모드가 있다.

1) 매일 모드 (기본)
   새 공고 목록 → 새 공고 본문(최대 DAILY_DETAIL_LIMIT건) → 사전 필터 → Gemini 평가
   → '오늘의 새 공고' 리포트. 하루에 한 번만 돈다 (다시 돌리려면 --force).
   작업 스케줄러에 등록해서 아침마다 자동으로 쓰는 용도.

2) 전체 훑기 모드 (--full)
   검색 결과 전체 목록 → 본문을 50건씩 받고 사이사이 쉬면서 끝까지 수집 → 사전 필터
   → 전체 평가 → 전체 중 상위 30건 리포트. 처음 한 번, 또는 검색 조건을 바꿨을 때 쓴다.
   공고 수백 건이면 쉬는 시간 포함 몇 시간 걸리니 자기 전에 돌려두는 걸 권장.

설정은 .env에서 읽는다
- SEARCH_URL (필수)
- DAILY_MAX_PAGES=2, DAILY_DETAIL_LIMIT=40     (매일 모드)
- FULL_BATCH=50, FULL_REST_MIN=20              (전체 모드: 한 번에 받을 본문 수, 사이 휴식 분)

사용 예:
    python src/daily.py --full "사람인 검색 URL"   # URL을 직접 넣어서 전체 훑기
    python src/daily.py --full                     # .env의 SEARCH_URL로 전체 훑기
    python src/daily.py --force                    # 매일 모드를 지금 바로 실행
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone

from db import DATA_DIR, REPORT_DIR, ROOT, get_conn, load_env

LAST_RUN = DATA_DIR / "last_run.txt"
MODEL_KEY = "gemini:gemini-3.5-flash-lite"


def step(name, *args):
    print(f"\n===== {name} =====", flush=True)
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    if result.returncode != 0:
        print(f"[경고] {name} 단계가 실패했습니다 (코드 {result.returncode}). 다음 단계로 진행합니다.", flush=True)
    return result.returncode == 0


def pending_details() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM postings WHERE raw_text IS NULL OR raw_text = ''").fetchone()[0]


def collect_all_details(batch: int, rest_min: float):
    """본문이 없는 공고가 없을 때까지 batch건씩 수집하고, 사이사이 rest_min분 쉰다.
    한 번 돌렸는데 남은 수가 줄지 않으면(연속 실패로 중단 = 차단 의심) 멈춘다."""
    round_no = 0
    while True:
        before = pending_details()
        if before == 0:
            print("\n본문 수집 완료: 남은 공고 없음", flush=True)
            return
        round_no += 1
        step(f"2-{round_no}. 본문 수집 (남은 {before}건 중 {min(batch, before)}건)",
             "src/scrape_detail.py", "--limit", str(batch))
        after = pending_details()
        if after >= before:
            print("\n[중단] 이번 회차에 새로 받은 본문이 없습니다. 접속이 막혔을 수 있으니 "
                  "몇 시간 뒤 다시 실행하세요. 이미 받은 공고로 평가를 계속합니다.", flush=True)
            return
        if after == 0:
            print("\n본문 수집 완료", flush=True)
            return
        resume = datetime.now().timestamp() + rest_min * 60
        print(f"\n{rest_min:g}분 쉬었다가 이어서 수집합니다 "
              f"(다시 시작: {datetime.fromtimestamp(resume):%H:%M}, 남은 {after}건)", flush=True)
        time.sleep(rest_min * 60)


def open_report(no_open: bool):
    report = REPORT_DIR / "latest.html"
    if report.exists() and not no_open and hasattr(os, "startfile"):
        os.startfile(report)  # Windows에서 기본 브라우저로 열기


def run_full(search_url: str, no_open: bool):
    batch = int(os.environ.get("FULL_BATCH", "50"))
    rest_min = float(os.environ.get("FULL_REST_MIN", "20"))
    print(f"##### 전체 훑기 시작 {datetime.now():%Y-%m-%d %H:%M} #####")
    step("1. 목록 수집 (검색 결과 전체)", "src/scrape_list.py", search_url, "--max-pages", "100")
    collect_all_details(batch, rest_min)
    step("3. 사전 필터", "src/prefilter.py")
    step("4. 평가 (Gemini)", "src/evaluate.py", "--backend", "gemini", "--limit", "1000")
    step("5. 리포트 (전체 중 상위 30건)", "src/report.py", "--html", "--model", MODEL_KEY)
    open_report(no_open)
    print("\n##### 전체 훑기 완료 #####")


def run_daily(search_url: str, force: bool, no_open: bool):
    today = date.today().isoformat()
    if not force and LAST_RUN.exists() and LAST_RUN.read_text().strip() == today:
        print(f"{today}에 이미 실행했습니다. 다시 돌리려면 --force")
        return
    max_pages = os.environ.get("DAILY_MAX_PAGES", "2")
    detail_limit = os.environ.get("DAILY_DETAIL_LIMIT", "40")

    # SQLite CURRENT_TIMESTAMP와 같은 형식(UTC)으로 시작 시각을 기록해 '이번 실행분'만 리포트에 담는다
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"##### 일일 실행 {datetime.now():%Y-%m-%d %H:%M} #####")
    step("1. 목록 수집", "src/scrape_list.py", search_url, "--max-pages", max_pages)
    step("2. 본문 수집", "src/scrape_detail.py", "--limit", detail_limit)
    step("3. 사전 필터", "src/prefilter.py")
    step("4. 평가 (Gemini)", "src/evaluate.py", "--backend", "gemini", "--limit", "200")
    step("5. 리포트", "src/report.py", "--since", started, "--html", "--model", MODEL_KEY)

    DATA_DIR.mkdir(exist_ok=True)
    LAST_RUN.write_text(today)
    open_report(no_open)
    print("\n##### 완료 #####")


def main():
    parser = argparse.ArgumentParser(description="전체 과정 한 번에 실행")
    parser.add_argument("url", nargs="?", help="사람인 검색결과 URL. 생략하면 .env의 SEARCH_URL 사용")
    parser.add_argument("--full", action="store_true", help="검색 결과 전체를 훑는 모드")
    parser.add_argument("--force", action="store_true", help="(매일 모드) 오늘 이미 돌았어도 다시 실행")
    parser.add_argument("--no-open", action="store_true", help="끝나고 리포트를 열지 않음")
    args = parser.parse_args()

    load_env()
    search_url = args.url or os.environ.get("SEARCH_URL")
    if not search_url:
        raise SystemExit('검색 URL이 없습니다. python src/daily.py --full "사람인 검색 URL" 처럼 넣거나 '
                         '.env에 SEARCH_URL을 적어주세요.')

    if args.full:
        run_full(search_url, args.no_open)
    else:
        run_daily(search_url, args.force, args.no_open)


if __name__ == "__main__":
    main()
