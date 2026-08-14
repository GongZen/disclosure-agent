# -*- coding: utf-8 -*-
"""W2 검증 — 기준 계층이 제대로 적재됐는지 확인한다.

행 수만 세는 것이 아니라 실제 조회가 되는지 본다. S2 대상 확정과
S3 문서 선별이 이 테이블 위에서 동작하므로, 그 조회 형태로 시험한다.

실행
    python scripts/verify_base.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import corpus_root, load_manifest, resolve  # noqa: E402
from db import connect  # noqa: E402

FAIL = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "실패"
    print(f"  [{mark}] {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAIL.append(label)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"코퍼스 위치  {corpus_root()}\n")

    with connect() as con:
        q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
        one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731

        print("1. 행 수")
        n_c = one("SELECT COUNT(*) FROM company")
        n_d = one("SELECT COUNT(*) FROM document")
        check("company 70행", n_c == 70, f"{n_c}행")
        check("document 4,204행", n_d == 4204, f"{n_d}행")

        print("\n2. 참조 무결성")
        orphan = one("SELECT COUNT(*) FROM document d LEFT JOIN company c "
                     "ON d.corp_code = c.corp_code WHERE c.corp_code IS NULL")
        check("고아 문서 없음", orphan == 0, f"{orphan}건")
        dup = one("SELECT COUNT(*) FROM (SELECT doc_id FROM document "
                  "GROUP BY doc_id HAVING COUNT(*) > 1)")
        check("doc_id 중복 없음", dup == 0, f"{dup}건")

        print("\n3. 선행 0 보존")
        z_corp = one("SELECT COUNT(*) FROM company WHERE LENGTH(corp_code) = 8")
        z_stock = one("SELECT COUNT(*) FROM company WHERE LENGTH(stock_code) = 6")
        check("corp_code 8자리", z_corp == 70, f"{z_corp}/70")
        check("stock_code 6자리", z_stock == 70, f"{z_stock}/70")

        print("\n4. S2 대상 확정 — 세 가지 지칭으로 조회")
        r = q("SELECT corp_name FROM company WHERE listed_name = ?", "현대차")
        check("listed_name '현대차' → 현대자동차",
              len(r) == 1 and r[0][0] == "현대자동차",
              r[0][0] if r else "미발견")
        r = q("SELECT corp_name FROM company WHERE listed_name = ?", "KT")
        check("listed_name 'KT' → 케이티",
              len(r) == 1 and r[0][0] == "케이티", r[0][0] if r else "미발견")
        r = q("SELECT corp_name FROM company WHERE sector = ? ORDER BY market_cap DESC",
              "2차전지")
        check("sector '2차전지' → 3개사", len(r) == 3,
              " · ".join(x[0] for x in r))

        print("\n5. S3 문서 선별 — 실제 질의 형태")
        r = q("SELECT d.doc_id, d.report_nm FROM document d "
              "JOIN company c ON d.corp_code = c.corp_code "
              "WHERE c.listed_name = ? AND d.doc_group = 'periodic' "
              "AND d.doc_subtype = 'annual' AND d.base_year = ? "
              "AND d.is_correction = 0", "삼성전자", 2025)
        check("삼성전자 2025년 사업보고서 1건", len(r) == 1,
              r[0][1] if r else "미발견")

        r = q("SELECT COUNT(*) FROM document d JOIN company c "
              "ON d.corp_code = c.corp_code WHERE c.sector = ? "
              "AND d.doc_group = 'exchange'", "2차전지")
        check("2차전지 3개사의 거래소공시 조회", r[0][0] > 0, f"{r[0][0]}건")

        print("\n6. 분포 — DATASET.md 실측값과 대조")
        got = dict(q("SELECT doc_group, COUNT(*) FROM document GROUP BY doc_group"))
        want = {"exchange": 1469, "holding": 1083, "periodic": 1054, "major": 598}
        for k, v in want.items():
            check(f"{k} {v:,}건", got.get(k) == v, f"{got.get(k, 0):,}건")
        n_corr = one("SELECT COUNT(*) FROM document WHERE is_correction = 1")
        check("정정공시 1,004건", n_corr == 1004, f"{n_corr:,}건")

        print("\n7. major_kind 추출")
        n_major = one("SELECT COUNT(*) FROM document WHERE doc_group = 'major'")
        n_kind = one("SELECT COUNT(*) FROM document WHERE doc_group = 'major' "
                     "AND major_kind IS NOT NULL")
        n_uniq = one("SELECT COUNT(DISTINCT major_kind) FROM document "
                     "WHERE major_kind IS NOT NULL")
        check("주요사항 전건에서 유형 추출", n_kind == n_major, f"{n_kind}/{n_major}")
        check("유형 28종", n_uniq == 28, f"{n_uniq}종")
        leak = one("SELECT COUNT(*) FROM document WHERE doc_group != 'major' "
                   "AND major_kind IS NOT NULL")
        check("major 외에는 NULL", leak == 0, f"{leak}건")

        print("\n8. 결측 — 있어야 정상인 것과 아닌 것")
        for col in ("doc_id", "corp_code", "doc_group", "report_nm", "rcept_dt", "file_path"):
            n = one(f"SELECT COUNT(*) FROM document WHERE {col} IS NULL")
            check(f"{col} 결측 없음", n == 0, f"{n}건")
        n_sub = one("SELECT COUNT(*) FROM document WHERE doc_subtype IS NULL")
        check("doc_subtype 결측 598건 (전부 major)", n_sub == 598, f"{n_sub}건")
        n_by = one("SELECT COUNT(*) FROM document WHERE base_year IS NULL")
        check("base_year 결측 3,150건 (정기공시 외)", n_by == 3150, f"{n_by}건")

        print("\n9. 인덱스")
        idx = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='index' "
                               "AND name LIKE 'ix_%'")}
        for name in ("ix_company_corp_name", "ix_company_listed_name", "ix_company_sector",
                     "ix_doc_corp_group_year", "ix_doc_corp_dt", "ix_doc_subtype",
                     "ix_doc_major_kind", "ix_doc_correction"):
            check(name, name in idx)

        print("\n10. 원문 접근 — 표본 200건")
        rows = q("SELECT file_path FROM document ORDER BY RANDOM() LIMIT 200")
        bad = []
        for (fp,) in rows:
            try:
                resolve(fp)
            except FileNotFoundError:
                bad.append(fp)
        check("표본 200건 전부 접근 가능", not bad,
              f"{len(rows) - len(bad)}/{len(rows)}" + (f" · 실패 예: {bad[0]}" if bad else ""))

        print("\n11. manifest 원본과 대조")
        m = load_manifest()
        check("행 수 일치", len(m) == n_d, f"manifest {len(m):,} / DB {n_d:,}")
        check("doc_id 집합 일치", set(m.doc_id) == {r[0] for r in q("SELECT doc_id FROM document")})

    print("\n" + "=" * 60)
    if FAIL:
        print(f"실패 {len(FAIL)}건")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
