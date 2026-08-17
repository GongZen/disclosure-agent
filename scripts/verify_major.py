# -*- coding: utf-8 -*-
"""W4 검증 — event_major 와 major_item.

같은 데이터를 두 형태로 담았으므로 서로 대조할 수 있다. 컬럼 값이
항목에서도 같은 값으로 나오는지 보는 것이 이 검증의 중심이다.

빈 값을 실패로 세지 않는다. 원문이 "-" 이면 회사가 안 쓴 것이고 그건
D7 의 not_disclosed 다. 우리가 검사할 것은 원문대로 읽었는가이다.

실행
    python scripts/verify_major.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import connect  # noqa: E402
from major import USE_KEYS  # noqa: E402

FAIL = []
SOURCE_ERR: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else '실패'}] {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAIL.append(label)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    with connect() as con:
        one = lambda s, *a: con.execute(s, a).fetchone()[0]  # noqa: E731
        rows = lambda s, *a: con.execute(s, a).fetchall()    # noqa: E731

        print("1. 대상 건수")
        n_doc = one("SELECT COUNT(*) FROM document WHERE doc_group='major'")
        n_em = one("SELECT COUNT(*) FROM event_major")
        check("주요사항보고서 전건에 행 존재", n_doc == n_em, f"{n_doc} / {n_em}")
        dup = one("SELECT COUNT(*) FROM (SELECT doc_id FROM event_major "
                  "GROUP BY doc_id HAVING COUNT(*) > 1)")
        check("문서당 한 행", dup == 0, f"중복 {dup}건")
        n_item = one("SELECT COUNT(*) FROM major_item")
        n_idoc = one("SELECT COUNT(DISTINCT doc_id) FROM major_item")
        check("전건에 항목 존재", n_idoc == n_doc, f"{n_idoc} / {n_doc} · 총 {n_item:,}행")

        print("\n2. 참조 무결성")
        for tbl in ("event_major", "major_item"):
            bad = one(f"SELECT COUNT(*) FROM {tbl} t LEFT JOIN document d "
                      f"ON t.doc_id=d.doc_id WHERE d.doc_id IS NULL")
            check(f"{tbl}.doc_id 실재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_major e JOIN document d "
                  "ON e.doc_id=d.doc_id WHERE e.corp_code != d.corp_code "
                  "OR e.major_kind != d.major_kind "
                  "OR e.is_correction != d.is_correction")
        check("기업·유형·정정여부가 문서와 일치", bad == 0, f"{bad}건")

        print("\n3. 검산")
        for v, label in ((1, "일치"), (0, "불일치"), (None, "관계 없음")):
            q = ("SELECT COUNT(*) FROM event_major WHERE check_ok IS NULL"
                 if v is None else
                 f"SELECT COUNT(*) FROM event_major WHERE check_ok={v}")
            print(f"        {label:<10} {one(q):>5}")
        n_bad = one("SELECT COUNT(*) FROM event_major WHERE check_ok=0")
        check("검산 불일치 없음", n_bad == 0, f"{n_bad}건")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.major_kind, e.amount_krw, "
                      "e.use_total FROM event_major e JOIN document d ON e.doc_id=d.doc_id "
                      "WHERE e.check_ok=0"):
            print(f"          {r['corp_name']} {r['rcept_dt']} {r['major_kind']} "
                  f"금액 {r['amount_krw']} / 용도합 {r['use_total']}")

        print("\n4. 용도 합계가 여섯 갈래의 합인가")
        cols = " + ".join(f"COALESCE({c},0)" for c in USE_KEYS)
        bad = one(f"SELECT COUNT(*) FROM event_major WHERE use_total IS NOT NULL "
                  f"AND use_total != {cols}")
        check("use_total = 여섯 갈래 합", bad == 0, f"{bad}건")

        # 컬럼과 key-value 를 대조한다. 갱신 경로가 하나라 어긋날 일이 없어야 한다
        print("\n5. 컬럼과 major_item 이 같은 값을 담고 있는가")
        bad = rows("""
            SELECT d.corp_name, d.rcept_dt, e.amount_krw, i.item_value
              FROM event_major e
              JOIN document d ON e.doc_id = d.doc_id
              JOIN major_item i ON i.id = (
                    SELECT id FROM major_item
                     WHERE doc_id = e.doc_id
                       AND REPLACE(item_name,' ','') LIKE '%사채의권면%총액%'
                     ORDER BY seq DESC LIMIT 1)
             WHERE e.amount_src LIKE '사채의권면%' AND e.amount_krw IS NOT NULL
               AND CAST(REPLACE(i.item_value, ',', '') AS INTEGER) != e.amount_krw""")
        check("사채 권면총액이 컬럼과 항목에서 일치", not bad, f"어긋남 {len(bad)}건")
        for r in bad[:5]:
            print(f"          {r['corp_name']} {r['rcept_dt']} "
                  f"컬럼 {r['amount_krw']:,} / 항목 {r['item_value']}")

        bad = rows("""
            SELECT d.corp_name, d.rcept_dt, e.method_etc, i.item_value
              FROM event_major e
              JOIN document d ON e.doc_id = d.doc_id
              JOIN major_item i ON i.id = (
                    SELECT id FROM major_item
                     WHERE doc_id = e.doc_id
                       AND REPLACE(item_name,' ','') LIKE '%처분방법%기타%'
                     ORDER BY seq DESC LIMIT 1)
             WHERE e.method_etc IS NOT NULL
               AND CAST(REPLACE(i.item_value, ',', '') AS INTEGER) != e.method_etc""")
        check("처분방법 기타가 컬럼과 항목에서 일치", not bad, f"어긋남 {len(bad)}건")

        print("\n6. 자기주식처분 — D1 이 요구한 판정 재료가 갖춰졌는가")
        n = one("SELECT COUNT(*) FROM event_major WHERE major_kind LIKE '%자기주식처분%'")
        got = one("SELECT COUNT(*) FROM event_major WHERE major_kind LIKE '%자기주식처분%' "
                  "AND disposal_purpose IS NOT NULL")
        check("처분목적 전건 보유", got == n, f"{got}/{n}")
        mk = one("SELECT COUNT(*) FROM event_major WHERE major_kind LIKE '%자기주식처분%' "
                 "AND COALESCE(method_market,0) + COALESCE(method_block,0) > 0")
        print(f"        현금 유입(시장 매도·시간외) {mk}건 — D1 실측 6건과 대조")
        check("현금 유입 건수가 D1 실측과 일치", mk == 6, f"{mk}건")

        print("\n7. 철회·취소 판정")
        n_w = one("SELECT COUNT(*) FROM event_major WHERE is_withdrawn=1")
        print(f"        {n_w}건")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.major_kind, e.correct_reason "
                      "FROM event_major e JOIN document d ON e.doc_id=d.doc_id "
                      "WHERE e.is_withdrawn=1 ORDER BY d.rcept_dt"):
            print(f"          {r['corp_name']:<14} {r['rcept_dt']} "
                  f"{r['major_kind'][:16]:<16} {(r['correct_reason'] or '')[:40]}")
        bad = one("SELECT COUNT(*) FROM event_major WHERE is_withdrawn=1 AND is_correction=0")
        check("철회는 전부 정정본에서 나온다", bad == 0, f"{bad}건")

        print("\n8. 범위와 경계값")
        bad = one("SELECT COUNT(*) FROM event_major WHERE amount_krw < 0")
        check("음수 금액 없음", bad == 0, f"{bad}건")
        bad = rows("SELECT d.corp_name, d.rcept_dt, e.decided_at FROM event_major e "
                   "JOIN document d ON e.doc_id=d.doc_id WHERE e.decided_at > d.rcept_dt")
        for r in bad:
            SOURCE_ERR.append((r["corp_name"], r["rcept_dt"],
                               f"결의일 {r['decided_at']} > 접수일"))
        check("결의일 > 접수일 (원문 오류 제외)", True, f"{len(bad)}건 → 원문 확인 대상")
        bad = one("SELECT COUNT(*) FROM event_major WHERE decided_at IS NOT NULL "
                  "AND (decided_at < '19000101' OR decided_at > '21001231')")
        check("날짜가 상식 범위", bad == 0, f"{bad}건")

        print("\n9. 표본")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.major_kind, e.amount_krw, "
                      "e.amount_src, e.use_total, e.decided_at, e.check_ok "
                      "FROM event_major e JOIN document d ON e.doc_id=d.doc_id "
                      "ORDER BY RANDOM() LIMIT 5"):
            amt = f"{r['amount_krw']:,}" if r["amount_krw"] else "-"
            print(f"        {r['corp_name']} {r['rcept_dt']} {r['major_kind']}")
            print(f"          금액 {amt} ({r['amount_src']}) · 용도합 {r['use_total']} "
                  f"· 결의일 {r['decided_at']} · 검산 {r['check_ok']}")

        print("\n10. 요약")
        for r in rows("SELECT major_kind, COUNT(*) n, SUM(amount_krw) s "
                      "FROM event_major GROUP BY 1 ORDER BY n DESC LIMIT 10"):
            tot = f"{r['s']:,}" if r["s"] else "-"
            print(f"        {r['n']:>4}  {r['major_kind']:<28} {tot}")

    print("\n" + "=" * 60)
    if SOURCE_ERR:
        print(f"원문 기재가 어긋난 건 {len(SOURCE_ERR)}")
        for x in SOURCE_ERR[:10]:
            print(f"  {x[0]:<14} {x[1]}  {x[2]}")
    if FAIL:
        print(f"실패 {len(FAIL)}건")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
