"""목록에 저장된 공고마다 상세 본문(상세요강)을 가져와 저장한다.

알아낸 것들
- 공고 페이지(relay/view)를 그냥 요청하면 사이트 메뉴와 '불러오는 중' 화면만 온다.
  본문은 별도 주소(relay/view-detail)에서 불러와 페이지 안에 끼워 넣는 구조라,
  이 주소로 직접 요청하면 본문만 깔끔하게 받을 수 있다.
- 처음엔 Playwright(headless 브라우저)로 공고 페이지를 열었는데, 사람인이 자동화
  브라우저 접속을 끊었다(ERR_CONNECTION_RESET). 일반 브라우저와 일반 요청은 정상이었다.
  브라우저 위장으로 우회하지 않고, 본문 주소만 가볍게 요청하는 방식으로 바꿨다.
- 본문 안에 회사 채용 사이트를 iframe으로 넣어둔 공고는 iframe 주소를 한 번 더 요청한다.
- 본문이 이미지뿐인 공고는 Tesseract OCR(kor+eng)로 글자를 뽑는다(설치된 경우에만).

요청 예절
- 요청 간격 기본 5초, 연속 3번 실패하면 바로 멈춘다.

사용 예:
    python src/scrape_detail.py --limit 20
"""
import argparse
import io
import os
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from db import get_conn

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
DETAIL_URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={}&rec_seq=0"
MIN_TEXT_LENGTH = 200
MAX_IMAGES_PER_POSTING = 8
MAX_CONSECUTIVE_FAILURES = 3
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

try:
    import pytesseract
    from PIL import Image
    if os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def html_to_text(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True), soup


def ocr_images(session, soup, base_url) -> str:
    if not OCR_AVAILABLE:
        return ""
    texts = []
    for img in soup.select("img")[:MAX_IMAGES_PER_POSTING]:
        src = img.get("src") or img.get("data-src") or ""
        if not src or any(k in src for k in ("banner", "adserver", "logo", "icon")):
            continue
        try:
            r = session.get(urljoin(base_url, src), headers=HEADERS, timeout=10)
            r.raise_for_status()
            t = pytesseract.image_to_string(Image.open(io.BytesIO(r.content)), lang="kor+eng")
            if t.strip():
                texts.append(t.strip())
        except Exception as e:
            print(f"  이미지 OCR 실패: {e}")
    return "\n\n".join(texts)


def fetch_detail_text(session, rec_idx: str, delay: float):
    """(본문 텍스트, OCR 사용 여부)를 반환한다. 네트워크 오류는 그대로 올려보낸다."""
    url = DETAIL_URL.format(rec_idx)
    r = session.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    text, soup = html_to_text(r.text)
    if len(text) >= MIN_TEXT_LENGTH:
        return text, False

    # 회사 채용 사이트를 iframe으로 넣어둔 공고
    for frame in soup.select("iframe[src]"):
        src = urljoin(url, frame["src"])
        if not src.startswith("http"):
            continue
        time.sleep(delay)
        try:
            fr = session.get(src, headers=HEADERS, timeout=15)
            frame_text, _ = html_to_text(fr.text)
            if len(frame_text) >= MIN_TEXT_LENGTH:
                return text + "\n\n" + frame_text, False
        except requests.RequestException as e:
            print(f"  iframe 요청 실패: {e}")

    # 이미지 공고
    ocr_text = ocr_images(session, soup, url)
    if len(ocr_text) >= MIN_TEXT_LENGTH:
        return text + "\n\n" + ocr_text, True
    return (text + "\n\n" + ocr_text).strip(), bool(ocr_text)


def main():
    parser = argparse.ArgumentParser(description="공고 상세 본문 수집")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--delay", type=float, default=5.0, help="요청 사이 대기(초). 5초 미만 비권장")
    args = parser.parse_args()

    with get_conn() as conn:
        pending = conn.execute(
            "SELECT url, rec_idx, title FROM postings WHERE raw_text IS NULL OR raw_text = '' LIMIT ?",
            (args.limit,),
        ).fetchall()
    print(f"상세 수집 대상: {len(pending)}건 (간격 {args.delay}초)")
    if not OCR_AVAILABLE:
        print("참고: pytesseract가 없어 이미지 공고 OCR은 건너뜁니다.")

    session = requests.Session()
    ok = short = failures = 0
    for i, row in enumerate(pending, 1):
        try:
            text, used_ocr = fetch_detail_text(session, row["rec_idx"], args.delay)
            failures = 0
        except requests.RequestException as e:
            failures += 1
            print(f"[{i}] 실패 ({failures}회 연속): {row['url']} - {e}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print("\n연속 실패가 반복돼 중단합니다. 몇 시간 쉬었다가 다시 실행하세요.")
                break
            time.sleep(args.delay * 2)
            continue

        with get_conn() as conn:
            conn.execute(
                "UPDATE postings SET raw_text=?, used_ocr=?, detail_at=CURRENT_TIMESTAMP WHERE url=?",
                (text, int(used_ocr), row["url"]),
            )
        ok += 1
        flag = " (OCR)" if used_ocr else ""
        if len(text) < MIN_TEXT_LENGTH:
            short += 1
            flag += " - 본문이 짧음, 직접 확인 필요"
        print(f"[{i}] {len(text):,}자{flag}: {row['title'][:40]}")
        time.sleep(args.delay)

    print(f"\n완료. 저장 {ok}건, 그중 본문이 짧은 것 {short}건")
    print("다음 단계: python src/prefilter.py")


if __name__ == "__main__":
    main()
