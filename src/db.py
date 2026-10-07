"""SQLite 저장소. 모든 스크립트가 이 모듈을 통해 DB에 접근한다.

테이블
- postings    : 공고 목록·상세 본문·사전필터 결과
- evaluations : 모델별 LLM 평가 결과 (같은 공고를 여러 모델로 평가해 비교할 수 있도록 분리)
- labels      : 검증용 직접 판단 결과
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "postings.db"
REPORT_DIR = ROOT / "reports"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    url TEXT PRIMARY KEY,
    rec_idx TEXT,
    title TEXT,
    company TEXT,
    listed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT,
    used_ocr INTEGER DEFAULT 0,
    detail_at TEXT,
    prefilter_pass INTEGER,
    prefilter_reason TEXT
);
CREATE TABLE IF NOT EXISTS evaluations (
    url TEXT NOT NULL,
    model TEXT NOT NULL,
    score INTEGER,
    junior_ok INTEGER,
    reason TEXT,
    matched_projects TEXT,
    missing_skills TEXT,
    evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (url, model)
);
CREATE TABLE IF NOT EXISTS labels (
    url TEXT PRIMARY KEY,
    my_verdict TEXT,
    my_note TEXT,
    labeled_at TEXT
);
"""


@contextmanager
def get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def load_env():
    """프로젝트 폴더의 .env 파일을 읽어 환경변수로 등록한다 (이미 있는 값은 유지)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
