"""감사보고서 첨부를 별도 문서로 등록한다. risk B 방향 A.

## 무엇을 하는가

정기공시 폴더에는 본문 파일과 첨부 감사보고서 파일이 함께 들어 있다.
지금은 둘 다 같은 `doc_id` 아래 담겨 있어, 감사의견을 근거로 답할 때
근거 공시가 사업보고서로 표시된다. 첨부분을 별도 `document` 행으로 떼어낸다.

## 왜 가벼운가

`section` 과 `chunk` 의 `doc_id` 칼럼만 바꾼다. 본문을 다시 자르지 않고
임베딩도 다시 붓지 않는다. `chunk_id` 가 그대로이므로 `embedding` ·
`embedding_oa` · `tokens` 가 전부 살아 있다.

`fact_financial` 은 건드리지 않는다. `build_fs.py` 는 `section` 이 아니라
원문 파일에서 값을 뽑으므로 이 작업의 영향을 받지 않는다.

## 어느 것이 첨부인가

`section.src_file` 의 확장자를 뗀 이름이 그 문서의 `rcept_no` 와 다르면
첨부다. 본문 파일은 접수번호와 이름이 같다.

## 새 문서 행이 무엇을 물려받는가

검색이 `doc_subtype` 으로 거르고 `base_year` 로 최신 연도를 고르므로
(`src/retrieval.py` 의 `Corpus`), 이 둘을 부모에서 그대로 물려받아야
첨부분이 검색에서 사라지지 않는다.

`doc_group` 만 `audit` 으로 새로 둔다. `build_fs.py` 가
`doc_group='periodic'` 으로 거르므로 감사보고서에 딸린 재무제표가 그 기업의
주 재무제표로 다시 잡히는 일을 막는다.

## 되돌리기

`--revert` 로 되돌린다. 새 문서 행의 `file_path` 가 부모와 같은 폴더를
가리키므로 부모를 다시 찾을 수 있다. 매핑은 `data/audit_docs.csv` 에도 남긴다.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

MAP_CSV = ROOT / "data" / "audit_docs.csv"

# 첨부 section 을 찾는 조건. src_file 에서 확장자를 뗀 이름이 접수번호와 다르다.
ATTACH = """
    replace(replace(s.src_file, '.xml', ''), '.html', '') <> CAST(d.rcept_no AS TEXT)
"""


def collect(con) -> list[dict]:
    """떼어낼 첨부를 (부모 문서, 첨부 파일) 단위로 모은다."""
    rows = con.execute(f"""
        SELECT s.doc_id AS parent_id, s.src_file,
               d.corp_code, d.corp_name, d.doc_subtype, d.rcept_dt,
               d.base_year, d.base_month, d.is_correction, d.file_path,
               d.flr_nm, COUNT(*) AS n_sec
        FROM section s
        JOIN document d ON d.doc_id = s.doc_id
        WHERE {ATTACH}
        GROUP BY s.doc_id, s.src_file
        ORDER BY s.doc_id, s.src_file""").fetchall()
    out = []
    for r in rows:
        stem = r["src_file"].rsplit(".", 1)[0]
        ym = ""
        if r["base_year"]:
            ym = f" ({r['base_year']}.{int(r['base_month'] or 12):02d})"
        out.append({
            "doc_id": f"audit_{stem}",
            "parent_id": r["parent_id"],
            "src_file": r["src_file"],
            "corp_code": r["corp_code"],
            "corp_name": r["corp_name"],
            "doc_group": "audit",
            "doc_subtype": r["doc_subtype"],   # 부모를 물려받는다. 검색 필터가 이걸 본다
            "report_nm": f"감사보고서{ym}",
            "rcept_no": stem,
            "rcept_dt": r["rcept_dt"],
            "flr_nm": r["flr_nm"],
            "base_year": r["base_year"],       # 부모를 물려받는다. 최신 연도 판정이 이걸 본다
            "base_month": r["base_month"],
            "is_correction": r["is_correction"],
            "file_path": r["file_path"],
            "file_format": "xml",
            "n_files": 1,
            "n_sec": r["n_sec"],
        })
    return out


def show(items: list[dict], con) -> None:
    n_sec = sum(i["n_sec"] for i in items)
    n_chunk = con.execute(f"""
        SELECT COUNT(*) n FROM chunk c JOIN section s ON c.section_id = s.section_id
        JOIN document d ON d.doc_id = s.doc_id WHERE {ATTACH}""").fetchone()["n"]
    print(f"떼어낼 첨부 문서   {len(items):,}건")
    print(f"옮길 section       {n_sec:,}행")
    print(f"옮길 chunk         {n_chunk:,}행   임베딩은 그대로 살아 있다")
    print(f"부모 문서          {len({i['parent_id'] for i in items}):,}건")
    dup = len(items) - len({i["doc_id"] for i in items})
    if dup:
        print(f"경고 — doc_id 중복 {dup}건")
    print()
    for i in items[:3]:
        print(f"  {i['doc_id']}  <- {i['parent_id']}")
        print(f"     {i['corp_name']} · {i['report_nm']} · {i['doc_subtype']}"
              f" · FY{i['base_year']} · section {i['n_sec']}")


def apply(con, items: list[dict]) -> None:
    exist = {r[0] for r in con.execute("SELECT doc_id FROM document")}
    clash = [i for i in items if i["doc_id"] in exist]
    if clash:
        raise SystemExit(f"중단 — doc_id 가 이미 있다: {clash[0]['doc_id']}")

    cols = ("doc_id corp_code corp_name doc_group doc_subtype report_nm rcept_no "
            "rcept_dt flr_nm base_year base_month is_correction file_path "
            "file_format n_files").split()
    con.executemany(
        f"INSERT INTO document ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(i[c] for c in cols) for i in items])

    n_s = n_c = 0
    for i in items:
        cur = con.execute(
            "UPDATE section SET doc_id=? WHERE doc_id=? AND src_file=?",
            (i["doc_id"], i["parent_id"], i["src_file"]))
        n_s += cur.rowcount
        cur = con.execute("""
            UPDATE chunk SET doc_id=? WHERE section_id IN
                (SELECT section_id FROM section WHERE doc_id=?)""",
            (i["doc_id"], i["doc_id"]))
        n_c += cur.rowcount
    con.commit()

    MAP_CSV.parent.mkdir(exist_ok=True)
    with MAP_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "parent_id", "src_file", "corp_name", "n_sec"])
        for i in items:
            w.writerow([i["doc_id"], i["parent_id"], i["src_file"],
                        i["corp_name"], i["n_sec"]])

    print(f"문서 {len(items):,}행 추가 · section {n_s:,}행 이동 · chunk {n_c:,}행 이동")
    print(f"매핑을 {MAP_CSV.relative_to(ROOT)} 에 남겼다")


def revert(con) -> None:
    """되돌린다. 매핑 CSV 가 없으면 file_path 로 부모를 되찾는다."""
    if MAP_CSV.exists():
        with MAP_CSV.open(encoding="utf-8-sig") as f:
            pairs = [(r["doc_id"], r["parent_id"]) for r in csv.DictReader(f)]
    else:
        pairs = [(r[0], r[1]) for r in con.execute("""
            SELECT a.doc_id, p.doc_id FROM document a
            JOIN document p ON p.file_path = a.file_path AND p.doc_group='periodic'
            WHERE a.doc_group='audit'""")]
        print(f"매핑 CSV 가 없어 file_path 로 부모를 되찾았다: {len(pairs)}쌍")

    n_s = n_c = 0
    for did, pid in pairs:
        cur = con.execute("UPDATE chunk SET doc_id=? WHERE doc_id=?", (pid, did))
        n_c += cur.rowcount
        cur = con.execute("UPDATE section SET doc_id=? WHERE doc_id=?", (pid, did))
        n_s += cur.rowcount
    con.execute("DELETE FROM document WHERE doc_group='audit'")
    con.commit()
    print(f"되돌렸다 — section {n_s:,}행 · chunk {n_c:,}행 · 문서 {len(pairs):,}행 삭제")


def main(mode: str = "dry") -> int:
    con = connect()
    if mode == "revert":
        revert(con)
        return 0
    items = collect(con)
    if not items:
        print("떼어낼 첨부가 없다. 이미 적용됐을 수 있다.")
        return 0
    show(items, con)
    if mode == "apply":
        print()
        apply(con, items)
    else:
        print("\n확인만 했다. 실제로 옮기려면 --apply")
    return 0


if __name__ == "__main__":
    m = "dry"
    for a in sys.argv[1:]:
        if a == "--apply":
            m = "apply"
        elif a == "--revert":
            m = "revert"
    sys.exit(main(m))
