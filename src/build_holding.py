# -*- coding: utf-8 -*-
"""W4 — 사실 계층 · 지분공시 적재.

두 테이블을 함께 채운다.
    event_holding   한 문서에 한 행. 누가 얼마나 갖고 있고 어떻게 변했나
    holding_item    한 문서에 여러 행. 원문의 모든 항목 + 표의 역할(section)

section 을 남기는 이유가 둘이다. 문서 하나가 최대 1만 7천 항목이라
HCX 입력 한도를 넘어 통째로 넣을 수 없고, 특별관계자 구간에 성명과
생년월일이 들어 있어 묻지 않은 신상이 답변에 딸려 나가면 안 된다.

실행
    python src/build_holding.py
"""

from __future__ import annotations

import sys
import time

from corpus import read_raw
from db import connect, create_holding_schema
from docitem import grid, iter_tables, parse, table_pairs
from holding import classify_section, extract, has_pii

COLUMNS = ("doc_id", "corp_code", "is_correction", "form", "holder_name",
           "report_type", "report_reason", "purpose",
           "prev_shares", "prev_ratio", "curr_shares", "curr_ratio",
           "total_shares", "ratio_calc", "ratio_match",
           "base_date", "obligation_date")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    with connect() as con:
        create_holding_schema(con)
        con.execute("DELETE FROM event_holding")
        con.execute("DELETE FROM holding_item")

        rows = con.execute(
            "SELECT doc_id, corp_code, corp_name, rcept_dt, report_nm, "
            "is_correction, file_path FROM document "
            "WHERE doc_group='holding' ORDER BY rcept_dt").fetchall()
        print(f"대상 {len(rows):,}건")

        t0 = time.time()
        recs, items, empty = [], [], []
        for r in rows:
            doc_items, seq = [], 0
            for table in iter_tables(parse(read_raw(r["file_path"]))):
                g = grid(table)
                if not g:
                    continue
                first = [x.strip() for x in g[0] if x.strip()]
                sec = classify_section(first)
                pii = int(has_pii(first))
                for name, val in table_pairs(g):
                    if not name or len(name) > 160:
                        continue
                    seq += 1
                    doc_items.append((seq, sec, name, val, pii))
            if not doc_items:
                empty.append((r["corp_name"], r["rcept_dt"]))
                continue
            for seq, sec, name, val, pii in doc_items:
                items.append((r["doc_id"], seq, sec, pii, name, val))
            form = "약식" if "약식" in r["report_nm"] else "일반"
            f = extract(doc_items, form)
            recs.append({**f, "doc_id": r["doc_id"], "corp_code": r["corp_code"],
                         "is_correction": r["is_correction"]})

        con.executemany(
            f"INSERT INTO event_holding ({','.join(COLUMNS)}) "
            f"VALUES ({','.join(':' + c for c in COLUMNS)})",
            [{c: rec.get(c) for c in COLUMNS} for rec in recs])
        con.executemany(
            "INSERT INTO holding_item (doc_id, seq, section, has_pii, item_name, item_value) "
            "VALUES (?,?,?,?,?,?)", items)
        con.commit()

        print(f"event_holding {len(recs):,}건 · 항목 없음 {len(empty)}건 "
              f"· {time.time()-t0:.0f}초")
        print(f"holding_item {len(items):,}행 · 문서당 평균 {len(items)/len(rows):.0f}개")

        print("\nsection 분포 · 개인 신상을 담은 표")
        for row in con.execute(
            "SELECT section, COUNT(*) n, SUM(has_pii) p FROM holding_item "
            "GROUP BY 1 ORDER BY n DESC"):
            tag = f"  개인정보 {row['p']:,}" if row["p"] else ""
            print(f"  {row['n']:>8,}  {row['section']:<16}{tag}")

        print("\n검산 — 보유수 ÷ 발행주식총수 × 100 = 보유비율")
        for row in con.execute(
            "SELECT ratio_match, COUNT(*) n FROM event_holding GROUP BY 1"):
            label = {1: "일치", 0: "불일치", None: "값 부족"}[row["ratio_match"]]
            print(f"  {row['n']:>5}  {label}")

        print("\n서식 × 보유목적")
        for row in con.execute(
            "SELECT form, purpose, COUNT(*) n FROM event_holding GROUP BY 1,2 "
            "ORDER BY n DESC"):
            print(f"  {row['n']:>5}  {row['form']:<4} {row['purpose']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
