"""수작업으로 채운 PDF 문서의 값을 fact_financial 에 담는다.

PDF 로 온 문서 3건은 파서로 값을 뽑지 못한다. 1,054건 중 3건이라 PDF 표
파서를 만드는 것보다 사람이 원문을 읽어 적는 편이 빠르고 정확하다.

source 를 'manual' 로 남긴다. XML 에서 뽑은 값과 구분되어야 나중에
어느 값이 어디서 왔는지 알 수 있고, 검증할 때도 성격이 다르다.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect, create_fact_financial_schema

SRC = ROOT / "data" / "manual" / "pdf_facts.csv"
UNIT = {"원": 1, "천원": 1_000, "백만원": 1_000_000,
        "억원": 100_000_000, "십억원": 1_000_000_000}


def to_num(s: str) -> int | None:
    t = re.sub(r"[^\d\-\(\)△▲]", "", (s or "").strip())
    if not t:
        return None
    neg = t.startswith(("(", "-", "△", "▲"))
    d = re.sub(r"[^\d]", "", t)
    if not d:
        return None
    return -int(d) if neg else int(d)


def main(dry: bool = False) -> int:
    if not SRC.exists():
        print(f"{SRC} 가 없다")
        return 1
    con = connect()
    create_fact_financial_schema(con)
    rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))

    ok = skip = bad = 0
    todo = []
    for r in rows:
        v = to_num(r["값"])
        if v is None:
            if "없음" in (r["비고"] or ""):
                skip += 1          # 원문에 그 항목이 없다. 정상이다
            else:
                bad += 1           # 아직 안 채웠다
            continue
        mult = UNIT.get((r["단위"] or "").strip())
        if not mult:
            print(f"   단위 모름: {r['기업']} {r['항목']} '{r['단위']}'")
            bad += 1
            continue
        corp = con.execute("SELECT corp_code FROM document WHERE doc_id=?",
                           (r["doc_id"],)).fetchone()
        if not corp:
            print(f"   문서 없음: {r['doc_id']}")
            bad += 1
            continue
        todo.append((r["doc_id"], corp["corp_code"], r["item_code"],
                     v * mult, v, mult, (r["단위"] or "").strip(),
                     int(r["회계연도"]), int(r["기준월"]),
                     r["기간유형"], r["기준"], "manual", r["항목"], None))
        ok += 1

    print(f"채워짐 {ok} · 원문에 없음 {skip} · 미입력/오류 {bad}")
    if dry:
        print("   --apply 를 주면 담는다")
        return 0
    if not todo:
        return 0
    con.executemany(
        """INSERT OR REPLACE INTO fact_financial
           (doc_id,corp_code,item_code,value,value_raw,unit_mult,unit_label,
            fiscal_year,base_month,period_type,basis,source,item_name,tag)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", todo)
    con.commit()
    print(f"   fact_financial 에 {len(todo)}행 담았다")
    n = con.execute("SELECT COUNT(*) n FROM fact_financial WHERE source='manual'"
                    ).fetchone()["n"]
    print(f"   source='manual' 총 {n}행")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--apply" not in sys.argv))
