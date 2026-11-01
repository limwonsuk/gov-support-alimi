"""SQLite 저장소 (data/alimi.db)."""
import hashlib
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id          TEXT PRIMARY KEY,
    agency      TEXT NOT NULL,
    scope       TEXT NOT NULL,          -- '전국' | '지역'
    region      TEXT,
    title       TEXT NOT NULL,
    norm        TEXT,                   -- 중복 확인용 제목 (기관이 달라도 같은 공고 찾기)
    url         TEXT,
    direct      INTEGER DEFAULT 1,      -- 1: url 이 그 공고 글, 0: 목록/첫 화면 (제목으로 찾아야 함)
    posted      TEXT,
    deadline    TEXT,
    score       INTEGER DEFAULT 0,
    reasons     TEXT,
    first_seen  TEXT NOT NULL,          -- 처음 수집한 날짜 (YYYY-MM-DD)
    last_seen   TEXT NOT NULL,
    notified_at TEXT                    -- 텔레그램으로 보낸 시각
);
CREATE INDEX IF NOT EXISTS idx_first_seen ON notices(first_seen);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started     TEXT,
    finished    TEXT,
    found       INTEGER,
    new_count   INTEGER,
    sent        INTEGER,
    report      TEXT                    -- 기관별 결과 (JSON)
);
"""


def notice_id(agency, title):
    return hashlib.sha1(f"{agency}|{title}".encode("utf-8")).hexdigest()[:16]


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # 예전 버전으로 만든 DB 에 새 칸 추가
    cols = {r["name"] for r in con.execute("PRAGMA table_info(notices)")}
    if "direct" not in cols:
        con.execute("ALTER TABLE notices ADD COLUMN direct INTEGER DEFAULT 1")
    return con
