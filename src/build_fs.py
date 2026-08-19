"""정기공시 재무제표 값을 fact_financial 에 담는다.

당기 값만 담는다. 이유는 스키마 주석에 적었다.
연결과 별도는 둘 다 담는다. D4 의 "연결 기본값" 은 답변에서 무엇을 먼저
보여줄지에 대한 결정이지 적재 범위가 아니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import read_raw
from db import connect, create_fact_financial_schema
from fsvalue import extract, KIND

# 정정본이 있으면 그쪽 값을 담는다. 원본 값은 이미 고쳐진 값이다.
SUPERSEDED = """
    SELECT DISTINCT to_doc_id FROM doc_relation
    WHERE to_doc_id IS NOT NULL
"""


def current_only(rows: list[dict]) -> list[dict]:
    """당기 값만 남긴다.

    태그 경로는 era 가 CFY 인 것, 표 파싱은 열 위치가 앞쪽인 것이다.
    표 파싱에서 앞쪽 두 열을 당기로 보는 이유는 반기·분기보고서가
    3개월치와 누적을 나란히 두기 때문이다. 전기는 그 뒤에 온다.
    """
    tagged = [r for r in rows if r.get("era")]
    if tagged:
        return [r for r in tagged if r["era"] == "CFY"]
    return [r for r in rows if r.get("col", 0) <= 1]


def main(reset: bool = False) -> int:
    con = connect()
    create_fact_financial_schema(con)
    if reset:
        con.execute("DELETE FROM fact_financial")
        con.commit()

    old = {r[0] for r in con.execute(SUPERSEDED)}
    docs = con.execute("""SELECT doc_id,corp_code,corp_name,rcept_dt,base_year,
                                 base_month,doc_subtype,file_path FROM document
                          WHERE doc_group='periodic' AND file_format='xml'
                          ORDER BY corp_name,rcept_dt""").fetchall()
    # 같은 보고서의 정정본이 여럿일 때 마지막 것만 남긴다.
    # doc_relation 은 정정본이 지목한 원본만 잇는다. 정정본이 또 정정되면
    # 중간 정정본이 지목되지 않고 남는다. 카카오 2023 사업보고서가
    # 2024-03-21 · 03-28 · 04-18 세 번 정정돼 세 행이 들어갔다.
    # 값은 같으나 조회 결과가 여럿 나오고 근거 공시가 정해지지 않는다.
    latest = {}
    for d in docs:
        if d["doc_id"] in old:
            continue
        key = (d["corp_code"], d["base_year"], d["base_month"], d["doc_subtype"])
        cur = latest.get(key)
        if cur is None or d["rcept_dt"] > cur["rcept_dt"]:
            latest[key] = d
    keep = {d["doc_id"] for d in latest.values()}

    n_doc = n_row = n_skip = n_old = 0
    seen = set()
    for d in docs:
        if d["doc_id"] in old or d["doc_id"] not in keep:
            n_old += 1
            continue
        try:
            rows = extract(read_raw(d["file_path"]))
        except Exception:
            n_skip += 1
            continue
        if not rows:
            n_skip += 1
            continue
        n_doc += 1
        year = int(d["base_year"]) if d["base_year"] else None
        month = int(d["base_month"]) if d["base_month"] else None
        for r in current_only(rows):
            code = r["item_code"]
            if code not in KIND:
                continue
            basis = r.get("basis") or "연결"
            period = r.get("period_type") or "annual"
            key = (d["doc_id"], code, year, month, period, basis)
            if key in seen:
                continue
            seen.add(key)
            mult = r.get("mult")
            con.execute(
                """INSERT OR IGNORE INTO fact_financial
                   (doc_id,corp_code,item_code,value,value_raw,unit_mult,
                    unit_label,fiscal_year,base_month,period_type,basis,source,
                    item_name,tag)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["doc_id"], d["corp_code"], code,
                 r["value"] * mult if mult else None, r["value"], mult,
                 r.get("unit"), year, month, period, basis, r["source"],
                 r.get("name"), r.get("tag")))
            n_row += 1
    con.commit()
    print(f"문서 {n_doc:,}건 적재 · 행 {n_row:,}")
    print(f"   건너뜀 {n_skip} · 정정된 원본 제외 {n_old}")
    return 0


if __name__ == "__main__":
    sys.exit(main(reset="--reset" in sys.argv))
