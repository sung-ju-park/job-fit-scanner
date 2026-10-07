"""사람인 검색결과 URL에서 공고 목록(제목·회사·상세 URL)을 수집한다.

사람인에 부담을 주지 않도록 기본값은 2페이지(최대 약 100건)만 가져온다.

알아낸 것들
- 페이지 이동은 page 파라미터로 하지만, 같은 세션으로 1페이지를 먼저 열어야
  다음 페이지가 제대로 나온다. 그냥 요청하면 계속 1페이지가 돌아온다.
- 카드 안에서 회사명 링크와 공고 제목 링크가 같은 클래스(a.str_tit)를 쓴다.
  첫 번째 것만 집으면 회사명이 제목으로 들어가므로, 회사명 영역 밖의 링크를 제목으로 쓴다.
- 검색결과에는 일반 공고 링크(rec_idx=...)와 '기업 채용관' 링크가 섞여 있다.
  후자는 링크만으로는 상세 페이지를 열 수 없지만, 카드를 감싸는
  div.list_item의 id="rec-12345678"에 공고 번호가 들어 있어서 이걸로
  두 종류 모두 상세 URL을 만들 수 있다.

사용 예:
    python src/scrape_list.py "<사람인 검색결과 URL>" --max-pages 2
    python src/scrape_list.py "<같은 URL>" --update-only   # 이미 저장된 공고의 제목·회사명만 갱신
"""
import argparse
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from db import get_conn

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
SELECTORS = {"item": "div.list_item", "title": "a.str_tit", "company": "div.company_nm"}
DETAIL_URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={}"


def build_page_url(search_url: str, page: int) -> str:
    parts = urlparse(search_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page)]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def parse_items(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select(SELECTORS["item"]):
        # 회사명 영역(div.company_nm) 안의 링크는 제외하고 공고 제목 링크를 찾는다
        title_tag = next((a for a in item.select(SELECTORS["title"])
                          if not a.find_parent(class_="company_nm")), None)
        if not title_tag:
            continue

        # 1순위: 카드 div의 id="rec-12345678"
        match = re.match(r"rec-(\d+)", item.get("id", ""))
        # 2순위: 일반 공고 링크의 rec_idx
        if not match:
            match = re.search(r"rec_idx=(\d+)", title_tag.get("href", ""))
        if not match:
            continue
        rec_idx = match.group(1)

        company_node = item.select_one(SELECTORS["company"])
        company = ""
        if company_node:
            company = (company_node.select_one("a") or company_node).get_text(strip=True)

        results.append({
            "rec_idx": rec_idx,
            "url": DETAIL_URL.format(rec_idx),
            "title": title_tag.get_text(strip=True),
            "company": company,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="사람인 검색결과 목록 수집")
    parser.add_argument("search_url", help="사람인에서 필터를 걸고 검색한 결과 페이지 URL")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--delay", type=float, default=3.0, help="페이지 사이 대기(초)")
    parser.add_argument("--update-only", action="store_true",
                        help="새 공고는 추가하지 않고, 이미 저장된 공고의 제목·회사명만 갱신")
    args = parser.parse_args()

    session = requests.Session()
    try:
        session.get(build_page_url(args.search_url, 1), headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        raise SystemExit(f"사람인 접속 실패: {e}")
    time.sleep(args.delay)

    total_new = total_updated = 0
    prev_ids = None
    for page in range(1, args.max_pages + 1):
        try:
            resp = session.get(build_page_url(args.search_url, page), headers=HEADERS, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"페이지 {page} 요청 실패: {e}")
            break

        items = parse_items(resp.text)
        if not items:
            print(f"페이지 {page}에서 공고를 찾지 못했습니다. 중단합니다.")
            break

        ids = {it["rec_idx"] for it in items}
        if ids == prev_ids:
            print(f"페이지 {page}가 이전 페이지와 같습니다(페이지 이동 실패). 중단합니다.")
            break
        prev_ids = ids

        new_count = updated = 0
        with get_conn() as conn:
            for it in items:
                exists = conn.execute("SELECT 1 FROM postings WHERE url=?", (it["url"],)).fetchone()
                if exists:
                    conn.execute("UPDATE postings SET title=?, company=? WHERE url=?",
                                 (it["title"], it["company"], it["url"]))
                    updated += 1
                elif not args.update_only:
                    conn.execute("INSERT INTO postings (url, rec_idx, title, company) VALUES (?, ?, ?, ?)",
                                 (it["url"], it["rec_idx"], it["title"], it["company"]))
                    new_count += 1
        total_new += new_count
        total_updated += updated
        print(f"페이지 {page}: {len(items)}건 확인, 신규 {new_count}건, 기존 갱신 {updated}건")
        time.sleep(args.delay)

    print(f"\n완료. 신규 저장 {total_new}건, 기존 갱신 {total_updated}건")
    print("다음 단계: python src/scrape_detail.py --limit 20")


if __name__ == "__main__":
    main()
