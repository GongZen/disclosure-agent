# -*- coding: utf-8 -*-
"""W4 — 사실 계층 · 주요사항보고서 적재.

두 테이블을 함께 채운다.
    event_major   한 문서에 한 행. 자주 쓰는 축만 컬럼으로
    major_item    한 문서에 여러 행. 원문의 모든 항목을 이름 그대로

컬럼은 조회 편의를 위한 것이고 보관은 major_item 이 한다. 고유 항목이
678개이고 한 문서에 같은 항목이 최대 39번 반복되므로 컬럼으로는 담기지 않는다.
합병비율·전환가액처럼 컬럼에 없는 항목은 major_item 에서 찾는다.

실행
    python src/build_major.py
"""

from __future__ import annotations

import sys

from corpus import read_raw
from db import connect, create_major_schema
from docitem import extract_items
from major import extract

COLUMNS = ("doc_id", "corp_code", "major_kind", "is_correction",
           "decided_at", "start_date", "end_date",
           "amount_krw", "amount_src", "currency", "amount_foreign",
           "use_facility", "use_business", "use_operation", "use_debt",
           "use_acquire", "use_other", "use_total", "check_ok",
           "shares_common", "shares_other", "shares_before", "price_share",
           "disposal_purpose", "method_market", "method_block",
           "method_otc", "method_etc", "is_withdrawn", "correct_reason")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    with connect() as con:
        create_major_schema(con)
        con.execute("DELETE FROM event_major")
        con.execute("DELETE FROM major_item")

        rows = con.execute(
            "SELECT doc_id, corp_code, corp_name, rcept_dt, major_kind, "
            "is_correction, file_path FROM document "
            "WHERE doc_group='major' ORDER BY rcept_dt").fetchall()
        print(f"대상 {len(rows):,}건")

        recs, items, empty = [], [], []
        for r in rows:
            its = extract_items(read_raw(r["file_path"]))
            if not its:
                empty.append((r["corp_name"], r["rcept_dt"], r["major_kind"]))
                continue
            for seq, name, val in its:
                items.append((r["doc_id"], seq, name, val))
            f = extract(its, r["major_kind"])
            recs.append({**f, "doc_id": r["doc_id"], "corp_code": r["corp_code"],
                         "is_correction": r["is_correction"]})

        con.executemany(
            f"INSERT INTO event_major ({','.join(COLUMNS)}) "
            f"VALUES ({','.join(':' + c for c in COLUMNS)})",
            [{c: rec.get(c) for c in COLUMNS} for rec in recs])
        con.executemany(
            "INSERT INTO major_item (doc_id, seq, item_name, item_value) "
            "VALUES (?,?,?,?)", items)
        con.commit()

        print(f"event_major {len(recs):,}건 · 항목 추출 실패 {len(empty)}건")
        for x in empty:
            print("   ", x)
        print(f"major_item {len(items):,}행 · 문서당 평균 {len(items)/len(rows):.0f}개")

        print("\n검산")
        for row in con.execute(
            "SELECT check_ok, COUNT(*) n FROM event_major GROUP BY 1"):
            label = {1: "일치", 0: "불일치", None: "검산 관계 없음"}[row["check_ok"]]
            print(f"  {row['n']:>5}  {label}")

        print("\n자금 용도를 적은 유형")
        for row in con.execute(
            "SELECT major_kind, COUNT(*) n, SUM(use_total IS NOT NULL) u "
            "FROM event_major GROUP BY 1 HAVING u > 0 ORDER BY n DESC"):
            print(f"  {row['n']:>4}건 중 {row['u']:>4}건   {row['major_kind']}")

        print("\n철회·취소로 판정된 건")
        for row in con.execute(
            "SELECT d.corp_name, d.rcept_dt, e.major_kind, e.correct_reason "
            "FROM event_major e JOIN document d ON e.doc_id=d.doc_id "
            "WHERE e.is_withdrawn=1 ORDER BY d.rcept_dt LIMIT 12"):
            print(f"  {row['corp_name']:<14} {row['rcept_dt']} "
                  f"{row['major_kind'][:18]:<18} {(row['correct_reason'] or '')[:42]}")
        n_w = con.execute("SELECT COUNT(*) FROM event_major WHERE is_withdrawn=1").fetchone()[0]
        print(f"  합계 {n_w}건")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
