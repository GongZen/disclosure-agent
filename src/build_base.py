# -*- coding: utf-8 -*-
"""W2 — 기준 계층 적재.

universe.csv 와 manifest.jsonl 을 SQLite 로 옮긴다. 추출이나 판단이 없다.
원문은 읽지 않는다.

실행
    python src/build_base.py
"""

from __future__ import annotations

import re
import sys

import pandas as pd

from corpus import load_manifest, load_universe
from db import connect, create_base_schema

# 주요사항보고서의 유형은 doc_subtype 이 아니라 report_nm 안에 있다.
# 실측 결과 형태가 셋이다 (major 598건 기준).
#   592건  주요사항보고서(전환사채권발행결정)
#     5건  [첨부추가]주요사항보고서(유상증자결정)      대체 수집분. DATASET.md 7-2 참조
#     1건  유상증자결정                                래퍼 없이 유형명만
# 앞의 대괄호 태그를 먼저 걷어내고, 래퍼가 있으면 괄호 안을, 없으면 전체를 쓴다.
_RE_TAG_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
_RE_MAJOR_KIND = re.compile(r"^주요사항보고서\((.+)\)\s*$")


def extract_major_kind(report_nm: str) -> str | None:
    """report_nm 에서 주요사항보고서 세부 유형을 뽑는다.

    문자열 파싱일 뿐 분류가 아니다. 이 값을 어떤 범주로 묶을지는 D1 에서 정한다.
    """
    s = _RE_TAG_PREFIX.sub("", report_nm).strip()
    m = _RE_MAJOR_KIND.match(s)
    return (m.group(1) if m else s).strip() or None


def build_company(con) -> int:
    u = load_universe()
    cols = ["corp_code", "stock_code", "corp_name", "listed_name", "corp_eng_name",
            "market", "industry", "sector_no", "sector", "listing_date",
            "fiscal_month", "market_cap", "n_periodic", "n_major",
            "n_exchange", "n_holding", "note"]
    missing = [c for c in cols if c not in u.columns]
    if missing:
        raise KeyError(f"universe.csv 에 없는 컬럼: {missing}")

    df = u[cols].where(pd.notna(u[cols]), None)
    con.executemany(
        f"INSERT INTO company ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        df.itertuples(index=False, name=None),
    )
    con.commit()
    return len(df)


def build_document(con) -> int:
    m = load_manifest().copy()

    m["major_kind"] = [
        extract_major_kind(nm) if g == "major" else None
        for nm, g in zip(m.report_nm, m.doc_group)
    ]
    m["category"] = None                      # D1 확정 후 채움
    m["is_correction"] = m.is_correction.astype(int)
    for c in ("base_year", "base_month"):
        m[c] = m[c].astype("Int64")

    cols = ["doc_id", "corp_code", "corp_name", "doc_group", "doc_subtype",
            "major_kind", "category", "report_nm", "rcept_no", "rcept_dt",
            "flr_nm", "base_year", "base_month", "is_correction",
            "file_path", "file_format", "n_files"]
    df = m[cols].astype(object).where(pd.notna(m[cols]), None)

    con.executemany(
        f"INSERT INTO document ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        df.itertuples(index=False, name=None),
    )
    con.commit()
    return len(df)


def reset(con) -> None:
    """document 가 company 를 참조하므로 삭제는 역순으로 한다."""
    con.execute("DELETE FROM document")
    con.execute("DELETE FROM company")
    con.commit()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with connect() as con:
        create_base_schema(con)
        reset(con)
        n_company = build_company(con)
        n_document = build_document(con)

        orphan = con.execute(
            "SELECT COUNT(*) FROM document d "
            "LEFT JOIN company c ON d.corp_code = c.corp_code "
            "WHERE c.corp_code IS NULL"
        ).fetchone()[0]

    print(f"company   {n_company:>6,}행")
    print(f"document  {n_document:>6,}행")
    print(f"고아 문서  {orphan:>6,}건  (company 에 없는 corp_code)")
    return 0 if orphan == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
