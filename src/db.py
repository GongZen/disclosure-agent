# -*- coding: utf-8 -*-
"""SQLite 연결과 스키마 정의.

데이터베이스는 data/corpus.db 하나다. 원문은 여기 들어오지 않는다.
이 파일은 파생 데이터만 담는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from corpus import DATA_DIR

__all__ = ["DB_PATH", "connect", "create_base_schema"]

DB_PATH = DATA_DIR / "corpus.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """연결을 열고 행을 dict 처럼 다룰 수 있게 설정한다."""
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ─────────────────────────────────────────────────────────────
# 기준 계층 — W2
# ─────────────────────────────────────────────────────────────

BASE_SCHEMA = """
-- 기업 마스터 70행. universe.csv 를 그대로 옮긴다.
CREATE TABLE IF NOT EXISTS company (
    corp_code     TEXT PRIMARY KEY,   -- DART 고유번호 8자리. 선행 0 유지
    stock_code    TEXT,               -- 종목코드 6자리
    corp_name     TEXT NOT NULL,      -- DART 공식 법인명. raw/ 폴더명과 동일
    listed_name   TEXT,               -- 거래소 통용 종목명. 질의에 등장하는 이름
    corp_eng_name TEXT,
    market        TEXT,               -- KOSPI | KOSDAQ
    industry      TEXT,               -- 업종 대분류 8개
    sector_no     INTEGER,
    sector        TEXT,               -- 섹터 20개. "2차전지 기업" 같은 지칭에 쓰임
    listing_date  TEXT,               -- 상장일. 정기공시 건수 해석에 필요
    fiscal_month  INTEGER,
    market_cap    INTEGER,            -- 억원
    n_periodic    INTEGER,
    n_major       INTEGER,
    n_exchange    INTEGER,
    n_holding     INTEGER,
    note          TEXT
);

-- 문서 마스터 4,204행. manifest.jsonl 을 그대로 옮긴다.
CREATE TABLE IF NOT EXISTS document (
    doc_id        TEXT PRIMARY KEY,   -- {doc_group}_{rcept_no}
    corp_code     TEXT NOT NULL REFERENCES company(corp_code),
    corp_name     TEXT NOT NULL,
    doc_group     TEXT NOT NULL,      -- periodic | major | exchange | holding
    doc_subtype   TEXT,               -- major 는 전부 NULL
    major_kind    TEXT,               -- report_nm 괄호 안. major 전용
    category      TEXT,               -- D1 확정 후 채움. 지금은 NULL
    report_nm     TEXT NOT NULL,
    rcept_no      TEXT NOT NULL,
    rcept_dt      TEXT NOT NULL,      -- YYYYMMDD
    flr_nm        TEXT,
    base_year     INTEGER,            -- 정기공시만. 나머지 NULL
    base_month    INTEGER,            -- 정기공시만. 나머지 NULL
    is_correction INTEGER NOT NULL,   -- 0 | 1
    file_path     TEXT NOT NULL,      -- 코퍼스 상대경로. NFC 표기
    file_format   TEXT,               -- xml | pdf+html
    n_files       INTEGER
);

-- S2 대상 확정 — 기업을 세 가지 방식으로 조회한다
CREATE INDEX IF NOT EXISTS ix_company_corp_name   ON company(corp_name);
CREATE INDEX IF NOT EXISTS ix_company_listed_name ON company(listed_name);
CREATE INDEX IF NOT EXISTS ix_company_sector      ON company(sector);

-- S3 문서 선별 — 기업·유형·기간으로 좁힌다
CREATE INDEX IF NOT EXISTS ix_doc_corp_group_year ON document(corp_code, doc_group, base_year);
CREATE INDEX IF NOT EXISTS ix_doc_corp_dt         ON document(corp_code, rcept_dt);
CREATE INDEX IF NOT EXISTS ix_doc_subtype         ON document(doc_group, doc_subtype);
CREATE INDEX IF NOT EXISTS ix_doc_major_kind      ON document(major_kind);
CREATE INDEX IF NOT EXISTS ix_doc_correction      ON document(is_correction);
"""


def create_base_schema(con: sqlite3.Connection) -> None:
    con.executescript(BASE_SCHEMA)
    con.commit()


# ─────────────────────────────────────────────────────────────
# 관계 계층 — W3
# ─────────────────────────────────────────────────────────────

RELATION_SCHEMA = """
-- 문서와 문서를 잇는다. 정정본→원본, 계약해지→원계약.
-- 연결 실패도 사유와 함께 기록한다. 실패가 아니라 답변해야 할 사실이기 때문이다.
CREATE TABLE IF NOT EXISTS doc_relation (
    id           INTEGER PRIMARY KEY,
    from_doc_id  TEXT NOT NULL REFERENCES document(doc_id),  -- 정정본 · 해지공시
    to_doc_id    TEXT REFERENCES document(doc_id),           -- 원본 · 원계약. 미연결이면 NULL
    rel_type     TEXT NOT NULL,      -- correction | termination
    target_date  TEXT,               -- 원문에서 뽑은 원본 제출일 YYYYMMDD
    target_hint  TEXT,               -- 보고서명 · 계약상대방 등 보조 단서
    resolved     INTEGER NOT NULL,   -- 0 | 1
    unresolved_reason TEXT,          -- out_of_scope | ambiguous | extract_failed
    candidates   INTEGER,            -- 후보 문서 수
    chain_depth  INTEGER             -- 연쇄 정정을 몇 단계 따라갔는가. 0이면 직접 연결
);

CREATE INDEX IF NOT EXISTS ix_rel_from    ON doc_relation(from_doc_id);
CREATE INDEX IF NOT EXISTS ix_rel_to      ON doc_relation(to_doc_id);
CREATE INDEX IF NOT EXISTS ix_rel_type    ON doc_relation(rel_type, resolved);
CREATE INDEX IF NOT EXISTS ix_rel_reason  ON doc_relation(unresolved_reason);
"""


def create_relation_schema(con: sqlite3.Connection) -> None:
    con.executescript(RELATION_SCHEMA)
    con.commit()
