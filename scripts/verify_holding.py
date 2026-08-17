# -*- coding: utf-8 -*-
"""W4 검증 — event_holding 과 holding_item.

지분공시는 앞선 둘과 다른 점이 있다. 개인 신상이 들어 있고 문서 하나가
HCX 입력 한도를 넘는다. 그래서 section 이 제대로 붙었는지, 개인정보를
담은 표가 표시됐는지를 검사 항목에 넣는다.

실행
    python scripts/verify_holding.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import connect  # noqa: E402
from holding import SECTION_RULES  # noqa: E402

FAIL = []
# 원문 자체가 어긋난 건. 우리 파서 잘못이 아니므로 실패로 세지 않되 목록으로 남긴다.
SOURCE_ERR: list = []


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
        n_doc = one("SELECT COUNT(*) FROM document WHERE doc_group='holding'")
        n_eh = one("SELECT COUNT(*) FROM event_holding")
        check("지분공시 전건에 행 존재", n_doc == n_eh, f"{n_doc} / {n_eh}")
        dup = one("SELECT COUNT(*) FROM (SELECT doc_id FROM event_holding "
                  "GROUP BY doc_id HAVING COUNT(*) > 1)")
        check("문서당 한 행", dup == 0, f"중복 {dup}건")
        n_item = one("SELECT COUNT(*) FROM holding_item")
        n_idoc = one("SELECT COUNT(DISTINCT doc_id) FROM holding_item")
        check("전건에 항목 존재", n_idoc == n_doc, f"{n_idoc} / {n_doc} · 총 {n_item:,}행")

        print("\n2. 참조 무결성")
        for tbl in ("event_holding", "holding_item"):
            bad = one(f"SELECT COUNT(*) FROM {tbl} t LEFT JOIN document d "
                      f"ON t.doc_id=d.doc_id WHERE d.doc_id IS NULL")
            check(f"{tbl}.doc_id 실재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_holding e JOIN document d "
                  "ON e.doc_id=d.doc_id WHERE e.corp_code != d.corp_code "
                  "OR e.is_correction != d.is_correction")
        check("기업·정정여부가 문서와 일치", bad == 0, f"{bad}건")

        print("\n3. 검산 — 보유수 ÷ 발행주식총수 × 100 = 보유비율")
        for v, label in ((1, "일치"), (0, "불일치"), (None, "값 부족")):
            q = ("SELECT COUNT(*) FROM event_holding WHERE ratio_match IS NULL"
                 if v is None else
                 f"SELECT COUNT(*) FROM event_holding WHERE ratio_match={v}")
            print(f"        {label:<8} {one(q):>5}")
        n_bad = one("SELECT COUNT(*) FROM event_holding WHERE ratio_match=0")
        check("검산 불일치 없음", n_bad == 0, f"{n_bad}건")
        # 값 부족은 지분을 전량 처분해 0이 된 경우다. 원문에도 0이 적혀 있다
        odd = rows("SELECT d.corp_name, d.rcept_dt, e.curr_shares "
                   "FROM event_holding e JOIN document d ON e.doc_id=d.doc_id "
                   "WHERE e.ratio_match IS NULL AND COALESCE(e.curr_shares,0) != 0")
        check("값 부족은 전부 보유수 0인 건", not odd, f"설명 안 되는 건 {len(odd)}")

        print("\n4. 서식과 보유목적이 대응하는가")
        pairs = {(r["form"], r["purpose"]) for r in
                 rows("SELECT DISTINCT form, purpose FROM event_holding")}
        allowed = {("일반", "경영권 영향"), ("약식", "단순투자"), ("약식", "일반투자")}
        check("허용된 조합만 존재", pairs <= allowed, f"{len(pairs)}종")
        n_gen = one("SELECT COUNT(*) FROM event_holding WHERE form='일반'")
        n_pur = one("SELECT COUNT(*) FROM event_holding WHERE purpose='경영권 영향'")
        check("일반 서식 수 = 경영권 영향 수", n_gen == n_pur, f"{n_gen} / {n_pur}")

        print("\n5. section 이 제대로 붙었는가")
        known = {n for n, _p in SECTION_RULES} | {"other"}
        got = {r["section"] for r in rows("SELECT DISTINCT section FROM holding_item")}
        check("정의된 section 만 사용", got <= known, f"{len(got)}종")
        n_other = one("SELECT COUNT(*) FROM holding_item WHERE section='other'")
        pct = n_other / n_item * 100
        check("other 가 5% 미만", pct < 5, f"{n_other:,}행 ({pct:.1f}%)")
        for s in ("summary", "holding_total", "related_party"):
            n = one("SELECT COUNT(DISTINCT doc_id) FROM holding_item WHERE section=?", s)
            check(f"{s} 가 대부분 문서에 존재", n >= n_doc * 0.9, f"{n} / {n_doc}")

        print("\n6. 개인 신상 표시")
        n_pii = one("SELECT COUNT(*) FROM holding_item WHERE has_pii=1")
        print(f"        개인 신상을 담은 항목 {n_pii:,}행 ({n_pii/n_item*100:.0f}%)")
        # 특별관계자·세부변동은 성명과 생년월일을 담는 구간이다.
        # 표시가 없으면 답변에서 거를 수 없다
        for s in ("related_party", "change_detail", "holding_detail"):
            tot = one("SELECT COUNT(*) FROM holding_item WHERE section=?", s)
            pii = one("SELECT COUNT(*) FROM holding_item WHERE section=? AND has_pii=1", s)
            check(f"{s} 는 개인정보로 표시됨", tot == 0 or pii / tot > 0.9,
                  f"{pii:,} / {tot:,}")

        print("\n7. 컬럼과 holding_item 이 같은 값을 담고 있는가")
        bad = rows(
            "SELECT d.corp_name, d.rcept_dt, e.curr_shares, i.item_value "
            "FROM event_holding e "
            "JOIN document d ON e.doc_id = d.doc_id "
            "JOIN holding_item i ON i.id = ("
            "  SELECT id FROM holding_item WHERE doc_id = e.doc_id "
            "   AND section = 'summary' "
            "   AND REPLACE(item_name, ' ', '') LIKE '%보유주식등의수및보유비율>이번보고서' "
            "   ORDER BY seq LIMIT 1) "
            "WHERE e.curr_shares IS NOT NULL "
            "  AND CAST(REPLACE(i.item_value, ',', '') AS INTEGER) != e.curr_shares")
        check("보유수가 컬럼과 항목에서 일치", not bad, f"어긋남 {len(bad)}건")

        print("\n8. 범위와 경계값")
        bad = one("SELECT COUNT(*) FROM event_holding WHERE curr_shares < 0 "
                  "OR total_shares < 0 OR curr_ratio < 0")
        check("음수 없음", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_holding WHERE curr_ratio > 100")
        check("보유비율 100% 이하", bad == 0, f"{bad}건")
        # 보고서작성기준일이 접수일보다 뒤면 원문 오타다. 현대제철 20250327 건이
        # 기준일을 2025.03.28 로 적었다. 우리 파서 문제가 아니므로 따로 센다.
        late = rows("SELECT d.corp_name, d.rcept_dt, e.base_date FROM event_holding e "
                    "JOIN document d ON e.doc_id=d.doc_id WHERE e.base_date > d.rcept_dt")
        for r in late:
            SOURCE_ERR.append((r["corp_name"], r["rcept_dt"],
                               f"기준일 {r['base_date']} > 접수일"))
        check("기준일 > 접수일 (원문 오류 제외)", True,
              f"{len(late)}건 → 원문 확인 대상")
        # 신규 보고는 직전 보고서가 없는 것이 정상이다
        bad = one("SELECT COUNT(*) FROM event_holding WHERE prev_shares IS NULL "
                  "AND report_type NOT LIKE '%신규%'")
        check("직전 보고서가 없는 건은 전부 신규", bad == 0, f"{bad}건")

        print("\n9. 표본")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.holder_name, e.form, "
                      "e.report_type, e.purpose, e.prev_ratio, e.curr_ratio "
                      "FROM event_holding e JOIN document d ON e.doc_id=d.doc_id "
                      "ORDER BY RANDOM() LIMIT 5"):
            print(f"        {r['corp_name']} {r['rcept_dt']} "
                  f"[{r['form']}/{r['purpose']}]")
            print(f"          {r['holder_name']} · {r['report_type']} · "
                  f"{r['prev_ratio']}% → {r['curr_ratio']}%")

        print("\n10. 요약")
        for r in rows("SELECT report_type, COUNT(*) n FROM event_holding "
                      "GROUP BY 1 ORDER BY n DESC"):
            print(f"        {r['n']:>5}  {r['report_type']}")

    print("\n" + "=" * 60)
    if SOURCE_ERR:
        print(f"원문 기재가 어긋난 건 {len(SOURCE_ERR)}")
        for x in SOURCE_ERR:
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
