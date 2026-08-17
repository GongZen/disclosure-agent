# -*- coding: utf-8 -*-
"""W4 — 사실 계층 · 거래소 계약 공시 적재.

정정본마다 한 행을 넣는다. 정정본이 원본을 대체하지 않는다.
"계약금액이 몇 번 바뀌었나" 에 답하려면 판본이 남아 있어야 하고,
판본을 남겨두면 최신만 뽑는 것은 쉽지만 그 반대는 불가능하기 때문이다.

원문의 값 오류는 고치지 않는다. 회사가 정정하면서 본문 일부 칸을
갱신하지 않은 건이 4건 있는데, 값을 고쳐 넣으면 근거 공시와 어긋난다.
대신 ratio_match 에 검산 결과를 남겨 답변에서 밝힐 수 있게 한다.

실행
    python src/build_contract.py
"""

from __future__ import annotations

import sys

from contract import extract
from corpus import read_raw, to_text
from db import connect, create_contract_item_schema, create_contract_schema
from docitem import extract_items

SUBTYPES = ("단일판매공급계약체결", "단일판매공급계약해지", "신규시설투자등")

# 문서 유형을 사건 종류로 옮긴다. 서식(form)과는 다른 축이다.
# 서식은 어떻게 적혀 있는가이고 사건 종류는 무슨 일이 있었는가다.
EVENT_TYPE = {
    "단일판매공급계약체결": "contract",
    "단일판매공급계약해지": "termination",
    "신규시설투자등": "investment",
}

COLUMNS = ("doc_id", "corp_code", "event_type", "form", "disclosure_type",
           "title", "title_norm", "category", "counterparty", "counterparty_rel",
           "region", "amount_krw", "amount_fixed", "amount_cond",
           "base_amount", "base_kind", "ratio_stated", "ratio_calc", "ratio_match",
           "start_date", "end_date", "signed_at", "purpose", "terminate_reason",
           "hold_until", "hold_reason", "is_large_corp", "is_correction")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    with connect() as con:
        create_contract_schema(con)
        create_contract_item_schema(con)
        con.execute("DELETE FROM event_contract")
        con.execute("DELETE FROM contract_item")

        rows = con.execute(
            "SELECT doc_id, corp_code, corp_name, rcept_dt, is_correction, "
            "doc_subtype, report_nm, file_path FROM document "
            f"WHERE doc_group='exchange' AND doc_subtype IN ({','.join('?' * len(SUBTYPES))}) "
            "ORDER BY rcept_dt", SUBTYPES).fetchall()

        print(f"대상 {len(rows):,}건")

        recs, failed, items = [], [], []
        for r in rows:
            raw = read_raw(r["file_path"])
            # 컬럼은 조회 편의를 위한 것이고 보관은 contract_item 이 한다.
            # 원문의 모든 항목을 표 구조 그대로 담아 무엇이든 답할 수 있게 한다.
            for seq, name, val in extract_items(raw):
                items.append((r["doc_id"], seq, name, val))
            f = extract(to_text(raw))
            if not f["form"]:
                failed.append((r["corp_name"], r["rcept_dt"], r["report_nm"]))
                continue
            recs.append({
                **f,
                "doc_id": r["doc_id"],
                "corp_code": r["corp_code"],
                "event_type": EVENT_TYPE[r["doc_subtype"]],
                # 자율공시는 규모 요건과 무관하게 회사가 스스로 내는 것이다.
                # 답변에서 "규모 요건에 못 미치는 건도 포함" 을 밝히는 데 쓴다.
                "disclosure_type": "voluntary" if "자율공시" in r["report_nm"] else "mandatory",
                "is_correction": r["is_correction"],
            })

        con.executemany(
            f"INSERT INTO event_contract ({','.join(COLUMNS)}) "
            f"VALUES ({','.join(':' + c for c in COLUMNS)})",
            [{c: rec.get(c) for c in COLUMNS} for rec in recs])
        con.executemany(
            "INSERT INTO contract_item (doc_id, seq, item_name, item_value) "
            "VALUES (?,?,?,?)", items)
        con.commit()

        print(f"적재 {len(recs):,}건 · 서식 판별 실패 {len(failed)}건")
        for x in failed:
            print("   ", x)
        print(f"contract_item {len(items):,}행 · 문서당 평균 {len(items)/len(rows):.0f}개")

        print("\n사건 종류 × 서식")
        for row in con.execute(
            "SELECT event_type, form, COUNT(*) n FROM event_contract "
            "GROUP BY 1,2 ORDER BY n DESC"):
            print(f"  {row['n']:>5}  {row['event_type']:<12} {row['form']}")

        print("\n공시 구분")
        for row in con.execute(
            "SELECT disclosure_type, COUNT(*) n FROM event_contract GROUP BY 1"):
            print(f"  {row['n']:>5}  {row['disclosure_type']}")

        print("\n검산")
        for row in con.execute(
            "SELECT ratio_match, COUNT(*) n FROM event_contract GROUP BY 1"):
            label = {1: "일치", 0: "불일치", None: "검산 불가"}[row["ratio_match"]]
            print(f"  {row['n']:>5}  {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
