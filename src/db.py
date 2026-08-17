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


# ─────────────────────────────────────────────────────────────
# 사실 계층 · 정형 — W4
# ─────────────────────────────────────────────────────────────

CONTRACT_SCHEMA = """
-- 거래소 계약 공시에서 뽑은 값. 판본마다 한 행이다.
-- 정정본이 원본을 대체하지 않는다. 계약금액이 몇 번 바뀌었는지 답해야 하기 때문이다.
CREATE TABLE IF NOT EXISTS event_contract (
    id               INTEGER PRIMARY KEY,
    doc_id           TEXT NOT NULL REFERENCES document(doc_id),
    corp_code        TEXT NOT NULL REFERENCES company(corp_code),
    event_type       TEXT NOT NULL,   -- contract | termination | investment
    form             TEXT NOT NULL,   -- 의무 | 자율 | 코스닥 | 해지 | 시설투자
    disclosure_type  TEXT,            -- mandatory | voluntary. 규모 요건 적용 여부

    title            TEXT,            -- 원문 표기 그대로. 근거로 제시할 때 쓴다
    title_norm       TEXT,            -- 정규화한 값. 같은 계약을 묶는 키
    category         TEXT,            -- 계약 구분 · 투자 구분
    counterparty     TEXT,
    counterparty_rel TEXT,            -- 회사와의 관계
    region           TEXT,

    amount_krw       INTEGER,         -- 계약금액 · 해지금액 · 투자금액. 코스닥은 총액
    amount_fixed     INTEGER,         -- 확정 계약금액.  코스닥만
    amount_cond      INTEGER,         -- 조건부 계약금액. 코스닥만
    base_amount      INTEGER,         -- 비교 기준액
    base_kind        TEXT,            -- revenue | equity. 위 값이 무엇인지
    ratio_stated     REAL,            -- 공시에 적힌 비율
    ratio_calc       REAL,            -- amount / base * 100
    ratio_match      INTEGER,         -- 둘이 맞는가. 0 이면 원문 기재가 어긋난다

    start_date       TEXT,            -- YYYYMMDD
    end_date         TEXT,
    signed_at        TEXT,            -- 계약(수주)일자 · 해지일자 · 이사회결의일

    purpose          TEXT,            -- 투자목적. 시설투자만
    terminate_reason TEXT,            -- 해지 주요사유. 해지만
    hold_until       TEXT,            -- 유보기한
    hold_reason      TEXT,
    is_large_corp    INTEGER,         -- 대규모법인여부
    is_correction    INTEGER NOT NULL -- 이 행이 정정본에서 나왔는가
);

-- S6 값 조회 — 기업의 기간별 계약을 찾는다
CREATE INDEX IF NOT EXISTS ix_ec_corp_signed ON event_contract(corp_code, signed_at);
-- 같은 계약의 판본을 모은다. 이력 질의가 여기 걸린다
CREATE INDEX IF NOT EXISTS ix_ec_corp_title  ON event_contract(corp_code, title_norm);
CREATE INDEX IF NOT EXISTS ix_ec_type        ON event_contract(event_type);
CREATE INDEX IF NOT EXISTS ix_ec_doc         ON event_contract(doc_id);
CREATE INDEX IF NOT EXISTS ix_ec_amount      ON event_contract(amount_krw);
"""


def create_contract_schema(con: sqlite3.Connection) -> None:
    con.executescript(CONTRACT_SCHEMA)
    con.commit()


CONTRACT_ITEM_SCHEMA = """
-- 계약 공시 원문의 모든 항목을 이름 그대로 담는다.
-- event_contract 컬럼은 자주 쓰는 축만 담으므로 나머지가 버려진다.
-- "이 계약의 대금지급 조건이 무엇인가" 같은 질의는 여기서 답한다.
--
-- 컬럼은 조회 편의를 위한 것이고 보관은 이 표가 한다. 그래서 컬럼 구성을
-- 나중에 바꿔도 원문을 다시 읽을 필요가 없다.
CREATE TABLE IF NOT EXISTS contract_item (
    id         INTEGER PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES document(doc_id),
    seq        INTEGER NOT NULL,   -- 문서 안 등장 순서. 같은 이름이 반복되므로 필요하다
    item_name  TEXT NOT NULL,      -- 표의 항목 경로. "2. 계약내역 > 계약금액(원)"
    item_value TEXT                -- 원문 표기 그대로. 숫자도 문자열이다
);

CREATE INDEX IF NOT EXISTS ix_ci_doc   ON contract_item(doc_id, seq);
CREATE INDEX IF NOT EXISTS ix_ci_name  ON contract_item(item_name);
"""


def create_contract_item_schema(con: sqlite3.Connection) -> None:
    con.executescript(CONTRACT_ITEM_SCHEMA)
    con.commit()


# ─────────────────────────────────────────────────────────────
# 사실 계층 · 주요사항보고서 — W4
# ─────────────────────────────────────────────────────────────

MAJOR_SCHEMA = """
-- 주요사항보고서 598건. 사건 종류가 28가지이고 유형마다 항목이 다르다.
-- 고유 항목이 678개라 전부 컬럼으로 만들 수 없고, 한 문서에 같은 항목이
-- 최대 39번 반복되기도 해서 컬럼으로는 담기지 않는다.
--
-- 그래서 컬럼에는 자주 쓰는 축만 두고 보관은 major_item 이 한다.
-- 컬럼 구성을 나중에 바꿔도 원문을 다시 읽을 필요가 없다.
CREATE TABLE IF NOT EXISTS event_major (
    id            INTEGER PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES document(doc_id),
    corp_code     TEXT NOT NULL REFERENCES company(corp_code),
    major_kind    TEXT NOT NULL,     -- 28개 유형. document.major_kind 와 같다
    is_correction INTEGER NOT NULL,

    decided_at    TEXT,              -- 이사회결의일(결정일). 19개 유형에 있다
    start_date    TEXT,
    end_date      TEXT,

    amount_krw    INTEGER,           -- 주된 금액. 유형마다 다른 항목에서 온다
    amount_src    TEXT,              -- 그 금액이 어느 항목에서 왔는지
    currency      TEXT,              -- 외화 발행 시 통화
    amount_foreign REAL,

    -- 자금조달의 목적. 유상증자와 사채류 다섯 유형이 똑같이 여섯 갈래로 적는다
    use_facility  INTEGER,           -- 시설자금
    use_business  INTEGER,           -- 영업양수자금
    use_operation INTEGER,           -- 운영자금
    use_debt      INTEGER,           -- 채무상환자금
    use_acquire   INTEGER,           -- 타법인 증권 취득자금
    use_other     INTEGER,           -- 기타자금
    use_total     INTEGER,           -- 위 여섯의 합
    check_ok      INTEGER,           -- 검산 결과. 유형별 관계식이 맞는가

    shares_common INTEGER,           -- 신주·처분·취득 예정 보통주식 수
    shares_other  INTEGER,
    shares_before INTEGER,           -- 증자전 발행주식총수
    price_share   INTEGER,           -- 1주당 발행가액 · 처분 대상 주식가격

    -- 자기주식처분 전용. D1 에서 건별 판정 재료를 담기로 했다
    disposal_purpose TEXT,           -- 처분목적 원문
    method_market INTEGER,           -- 시장을 통한 매도(주)
    method_block  INTEGER,           -- 시간외대량매매(주)
    method_otc    INTEGER,           -- 장외처분(주)
    method_etc    INTEGER,           -- 기타(주). 임직원 교부가 여기 들어간다

    -- 사건이 없던 일이 된 경우. 공시유보와 다르다
    is_withdrawn  INTEGER,           -- 철회·취소·해제
    correct_reason TEXT              -- 정정사유 원문
);

CREATE INDEX IF NOT EXISTS ix_em_corp_dt  ON event_major(corp_code, decided_at);
CREATE INDEX IF NOT EXISTS ix_em_kind     ON event_major(major_kind);
CREATE INDEX IF NOT EXISTS ix_em_amount   ON event_major(amount_krw);
CREATE INDEX IF NOT EXISTS ix_em_doc      ON event_major(doc_id);

-- 원문의 모든 항목을 이름 그대로. 컬럼에 없는 것은 전부 여기서 찾는다.
-- 합병비율 · 전환가액 · 외부평가기관처럼 유형별 고유 항목이 여기 있다.
CREATE TABLE IF NOT EXISTS major_item (
    id         INTEGER PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES document(doc_id),
    seq        INTEGER NOT NULL,   -- 문서 안 등장 순서
    item_name  TEXT NOT NULL,      -- 표의 항목 경로
    item_value TEXT
);

CREATE INDEX IF NOT EXISTS ix_mi_doc   ON major_item(doc_id, seq);
CREATE INDEX IF NOT EXISTS ix_mi_name  ON major_item(item_name);
"""


def create_major_schema(con: sqlite3.Connection) -> None:
    con.executescript(MAJOR_SCHEMA)
    con.commit()


# ─────────────────────────────────────────────────────────────
# 사실 계층 · 지분공시 — W4
# ─────────────────────────────────────────────────────────────

HOLDING_SCHEMA = """
-- 대량보유상황보고서 1,083건. 5% 룰 공시다.
-- 어떤 회사 주식을 5% 이상 가지면 신고해야 하고, 이후 1% 이상 변동마다 다시 낸다.
CREATE TABLE IF NOT EXISTS event_holding (
    id            INTEGER PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES document(doc_id),
    corp_code     TEXT NOT NULL REFERENCES company(corp_code),
    is_correction INTEGER NOT NULL,
    form          TEXT,        -- 일반 | 약식.  목적에 따라 서식이 갈린다
    holder_name   TEXT,        -- 보고자
    report_type   TEXT,        -- 신규 | 변동 | 변경 | 변동ㆍ변경
    report_reason TEXT,        -- 보고사유
    purpose       TEXT,        -- 경영권 영향 | 단순투자 | 일반투자

    prev_shares   INTEGER,     -- 직전 보고서 보유주식등의 수
    prev_ratio    REAL,
    curr_shares   INTEGER,     -- 이번 보고서
    curr_ratio    REAL,
    total_shares  INTEGER,     -- 의결권 있는 발행주식총수
    ratio_calc    REAL,        -- 우리가 재계산한 비율
    ratio_match   INTEGER,     -- 기재값과 맞는가

    base_date     TEXT,        -- 보고서작성기준일
    obligation_date TEXT       -- 보고의무발생일
);

CREATE INDEX IF NOT EXISTS ix_eh_corp   ON event_holding(corp_code, base_date);
CREATE INDEX IF NOT EXISTS ix_eh_holder ON event_holding(holder_name);
CREATE INDEX IF NOT EXISTS ix_eh_ratio  ON event_holding(curr_ratio);
CREATE INDEX IF NOT EXISTS ix_eh_doc    ON event_holding(doc_id);

-- 원문의 모든 항목. section 으로 표의 역할을 남긴다.
--
-- section 이 필요한 이유가 둘이다. 문서 하나가 최대 1만 7천 항목이라
-- HCX 입력 한도를 넘어 통째로 넣을 수 없고, 특별관계자 구간에 성명과
-- 생년월일이 들어 있어 묻지 않은 신상이 답변에 딸려 나가면 안 된다.
-- 질의가 요구하는 구간만 꺼내 넘긴다. 넘기지 않은 것은 답변에 나올 수 없다.
CREATE TABLE IF NOT EXISTS holding_item (
    id         INTEGER PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES document(doc_id),
    seq        INTEGER NOT NULL,
    section    TEXT NOT NULL,   -- summary · holding_total · related_party · change_detail …
    has_pii    INTEGER NOT NULL,-- 성명·생년월일·주소가 들어간 표인가
    item_name  TEXT NOT NULL,
    item_value TEXT
);

CREATE INDEX IF NOT EXISTS ix_hi_doc  ON holding_item(doc_id, section);
CREATE INDEX IF NOT EXISTS ix_hi_name ON holding_item(item_name);
CREATE INDEX IF NOT EXISTS ix_hi_pii  ON holding_item(has_pii);
"""


def create_holding_schema(con: sqlite3.Connection) -> None:
    con.executescript(HOLDING_SCHEMA)
    con.commit()
