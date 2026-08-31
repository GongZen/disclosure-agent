"""감사보고서 분리(risk B 방향 A)가 제대로 됐는지 본다. 6겹.

`src/build_audit_doc.py --apply` 뒤에 돌린다. 데이터를 안 바꾸므로 언제 돌려도
안전하다. 한 겹이라도 실패하면 `--revert` 로 되돌릴 수 있다.

겹을 이렇게 나눈 이유는, 이 작업이 "옮기는" 작업이라 잃어버리는 것과
안 옮겨진 것을 따로 봐야 하기 때문이다.

    1  총량 보존      section · chunk 의 전체 행 수가 그대로인가
    2  검색 자산 보존  임베딩 · 토큰이 하나도 안 없어졌는가
    3  이동 완결      옮길 것이 남아 있지 않은가
    4  검색 생존      새 문서가 검색 필터를 통과하는가
    5  참조 정합      section 과 chunk 의 doc_id 가 서로 맞는가
    6  되돌리기 가능   부모를 되찾을 수 있는가
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

# build_audit_doc.py 와 같은 조건이어야 한다
ATTACH = """
    replace(replace(s.src_file, '.xml', ''), '.html', '') <> CAST(d.rcept_no AS TEXT)
"""

# 적용 전 실측값. 이 숫자가 바뀌면 앞 단계가 다시 만들어진 것이다
EXPECT = {"section": 122871, "chunk": 171564, "audit_doc": 415,
          "audit_sec": 4525, "audit_chunk": 13861}

ok_all = True


def check(name: str, cond: bool, msg: str) -> None:
    global ok_all
    mark = "통과" if cond else "실패"
    if not cond:
        ok_all = False
    print(f"  [{mark}] {name} — {msg}")


def main() -> int:
    con = connect()
    q = lambda s, *a: con.execute(s, a).fetchone()[0]

    print("1겹 — 총량 보존")
    n_sec = q("SELECT COUNT(*) FROM section")
    n_chunk = q("SELECT COUNT(*) FROM chunk")
    check("section 행 수", n_sec == EXPECT["section"],
          f"{n_sec:,} (기대 {EXPECT['section']:,})")
    check("chunk 행 수", n_chunk == EXPECT["chunk"],
          f"{n_chunk:,} (기대 {EXPECT['chunk']:,})")

    print("\n2겹 — 검색 자산 보존")
    n_tok = q("SELECT COUNT(*) FROM chunk WHERE tokens IS NOT NULL AND tokens<>''")
    n_oa = q("SELECT COUNT(*) FROM chunk WHERE embedding_oa IS NOT NULL")
    check("tokens", n_tok == EXPECT["chunk"], f"{n_tok:,} / {EXPECT['chunk']:,}")
    check("embedding_oa", n_oa == EXPECT["chunk"], f"{n_oa:,} / {EXPECT['chunk']:,}")

    print("\n3겹 — 이동 완결")
    n_doc = q("SELECT COUNT(*) FROM document WHERE doc_group='audit'")
    n_asec = q("SELECT COUNT(*) FROM section s JOIN document d ON d.doc_id=s.doc_id"
               " WHERE d.doc_group='audit'")
    n_ach = q("SELECT COUNT(*) FROM chunk c JOIN document d ON d.doc_id=c.doc_id"
              " WHERE d.doc_group='audit'")
    left = q(f"""SELECT COUNT(*) FROM section s JOIN document d ON d.doc_id=s.doc_id
                 WHERE d.doc_group<>'audit' AND {ATTACH}""")
    check("감사 문서 수", n_doc == EXPECT["audit_doc"],
          f"{n_doc:,} (기대 {EXPECT['audit_doc']:,})")
    check("감사 section", n_asec == EXPECT["audit_sec"],
          f"{n_asec:,} (기대 {EXPECT['audit_sec']:,})")
    check("감사 chunk", n_ach == EXPECT["audit_chunk"],
          f"{n_ach:,} (기대 {EXPECT['audit_chunk']:,})")
    check("안 옮겨진 첨부", left == 0, f"{left:,}행 남음")

    print("\n4겹 — 검색 생존")
    # Corpus 가 doc_subtype 으로 거르고 base_year 로 최신 연도를 고른다
    bad_sub = q("SELECT COUNT(*) FROM document WHERE doc_group='audit'"
                " AND (doc_subtype IS NULL OR doc_subtype='')")
    bad_year = q("SELECT COUNT(*) FROM document WHERE doc_group='audit'"
                 " AND base_year IS NULL")
    searchable = q("""
        SELECT COUNT(*) FROM chunk c
        JOIN document d ON d.doc_id = c.doc_id
        WHERE d.doc_group='audit' AND c.embedding_oa IS NOT NULL
          AND c.tokens IS NOT NULL AND d.doc_subtype='annual'""")
    check("doc_subtype 비었나", bad_sub == 0, f"{bad_sub}건이 비어 있다")
    check("base_year 비었나", bad_year == 0, f"{bad_year}건이 비어 있다")
    check("검색 가능한 감사 chunk", searchable > 0, f"{searchable:,}개")

    print("\n5겹 — 참조 정합")
    mismatch = q("""SELECT COUNT(*) FROM chunk c JOIN section s
                    ON c.section_id = s.section_id WHERE c.doc_id <> s.doc_id""")
    orphan = q("""SELECT COUNT(*) FROM chunk c
                  LEFT JOIN document d ON d.doc_id = c.doc_id WHERE d.doc_id IS NULL""")
    orphan_s = q("""SELECT COUNT(*) FROM section s
                    LEFT JOIN document d ON d.doc_id = s.doc_id WHERE d.doc_id IS NULL""")
    check("chunk·section doc_id 불일치", mismatch == 0, f"{mismatch:,}행")
    check("문서 없는 chunk", orphan == 0, f"{orphan:,}행")
    check("문서 없는 section", orphan_s == 0, f"{orphan_s:,}행")

    print("\n6겹 — 되돌리기 가능")
    p = ROOT / "data" / "audit_docs.csv"
    n_map = 0
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            n_map = len(list(csv.DictReader(f)))
    by_path = q("""SELECT COUNT(*) FROM document a JOIN document p
                   ON p.file_path = a.file_path AND p.doc_group='periodic'
                   WHERE a.doc_group='audit'""")
    check("매핑 CSV", n_map == n_doc, f"{n_map:,}행 (문서 {n_doc:,}건)")
    check("file_path 로 부모 되찾기", by_path == n_doc,
          f"{by_path:,} / {n_doc:,}")

    print()
    print("전부 통과" if ok_all else "실패한 겹이 있다 — --revert 로 되돌릴 수 있다")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
