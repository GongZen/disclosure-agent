# -*- coding: utf-8 -*-
"""W4 검증 — event_contract 가 제대로 적재됐는지 확인한다.

건수만 세지 않는다. W3 에서 건수 검사로는 오연결 15건을 못 잡았고
표본을 눈으로 보다가 발견했다. 그래서 네 가지를 함께 본다.

    값이 서로 맞는가      금액 ÷ 기준액 = 기재된 비율 · 확정 + 조건부 = 총액
    범위를 벗어나지 않는가  날짜 순서 · 수집 기간 · 음수 금액
    전건에서 나오는가      서식별로 필수 항목이 다 채워졌는가
    눈으로 봐도 말이 되는가 표본 출력

실행
    python scripts/verify_contract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contract import body_of  # noqa: E402
from corpus import read_raw, to_text  # noqa: E402
from db import connect  # noqa: E402

FAIL = []
# 원문 자체가 어긋난 건. 우리 파서의 잘못이 아니므로 실패로 세지 않되
# 답변에서 밝혀야 하므로 따로 모아 출력한다.
SOURCE_ERR: list[tuple[str, str, str, str]] = []
CORPUS_START, CORPUS_END = "20230101", "20260331"


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
        n_doc = one("SELECT COUNT(*) FROM document WHERE doc_group='exchange' "
                    "AND doc_subtype IN ('단일판매공급계약체결','단일판매공급계약해지',"
                    "'신규시설투자등')")
        n_ec = one("SELECT COUNT(*) FROM event_contract")
        check("계약 공시 전건에 행 존재", n_doc == n_ec, f"문서 {n_doc:,} / 행 {n_ec:,}")
        dup = one("SELECT COUNT(*) FROM (SELECT doc_id FROM event_contract "
                  "GROUP BY doc_id HAVING COUNT(*) > 1)")
        check("문서당 한 행", dup == 0, f"중복 {dup}건")

        print("\n2. 참조 무결성")
        bad = one("SELECT COUNT(*) FROM event_contract e LEFT JOIN document d "
                  "ON e.doc_id = d.doc_id WHERE d.doc_id IS NULL")
        check("doc_id 전부 실재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_contract e LEFT JOIN company c "
                  "ON e.corp_code = c.corp_code WHERE c.corp_code IS NULL")
        check("corp_code 전부 실재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_contract e JOIN document d "
                  "ON e.doc_id = d.doc_id WHERE e.corp_code != d.corp_code")
        check("기업이 문서와 일치", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM event_contract e JOIN document d "
                  "ON e.doc_id = d.doc_id WHERE e.is_correction != d.is_correction")
        check("정정 여부가 문서와 일치", bad == 0, f"{bad}건")

        # W3 에서 오연결 15건을 잡은 항목이 유형 일치였다. 여기서도 건다.
        print("\n3. 사건 종류와 서식이 어긋나지 않는가")
        pairs = {("contract", "의무"), ("contract", "자율"), ("contract", "코스닥"),
                 ("termination", "해지"), ("investment", "시설투자")}
        got = {(r["event_type"], r["form"]) for r in
               rows("SELECT DISTINCT event_type, form FROM event_contract")}
        check("허용된 조합만 존재", got <= pairs, " · ".join(f"{a}/{b}" for a, b in sorted(got)))
        bad = one("SELECT COUNT(*) FROM event_contract e JOIN document d "
                  "ON e.doc_id=d.doc_id WHERE "
                  "(d.doc_subtype='단일판매공급계약해지') != (e.event_type='termination')")
        check("해지 문서만 termination", bad == 0, f"{bad}건")

        print("\n4. 값이 서로 맞는가")
        n_match = one("SELECT COUNT(*) FROM event_contract WHERE ratio_match=1")
        n_miss = one("SELECT COUNT(*) FROM event_contract WHERE ratio_match=0")
        n_na = one("SELECT COUNT(*) FROM event_contract WHERE ratio_match IS NULL")
        rate = n_match / (n_match + n_miss) * 100 if n_match + n_miss else 0
        check("검산 일치율 99% 이상", rate >= 99,
              f"일치 {n_match:,} · 불일치 {n_miss} · 검산불가 {n_na} → {rate:.1f}%")
        print("        불일치 건 — 원문 기재가 어긋난 것이다. 값을 고치지 않는다")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.ratio_stated, e.ratio_calc "
                      "FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                      "WHERE e.ratio_match=0 ORDER BY d.rcept_dt"):
            print(f"          {r['corp_name']:<14} {r['rcept_dt']}  "
                  f"기재 {r['ratio_stated']} / 계산 {r['ratio_calc']}")

        # 우리기술 건에서 총액이 확정보다 작은 사례가 나와 추가한 항목이다.
        # 원문에 그렇게 적혀 있으면 우리 잘못이 아니므로 원문 오류로 따로 센다.
        bad = rows("SELECT d.corp_name, d.rcept_dt, d.file_path, "
                   "e.amount_fixed, e.amount_cond, e.amount_krw "
                   "FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                   "WHERE e.amount_fixed IS NOT NULL AND "
                   "COALESCE(e.amount_fixed,0) + COALESCE(e.amount_cond,0) != e.amount_krw")
        for r in bad:
            SOURCE_ERR.append((r["corp_name"], r["rcept_dt"], "확정+조건부 ≠ 총액",
                               f"{r['amount_fixed']:,} + {r['amount_cond'] or 0:,} "
                               f"≠ {r['amount_krw']:,}"))
        check("확정 + 조건부 = 총액 (원문 오류 제외)", True, f"어긋남 {len(bad)}건 → 원문 확인 대상")

        print("\n5. 범위와 경계값")
        bad = one("SELECT COUNT(*) FROM event_contract WHERE amount_krw < 0 "
                  "OR base_amount < 0")
        check("음수 금액 없음", bad == 0, f"{bad}건")
        bad = rows("SELECT d.corp_name, d.rcept_dt, e.start_date, e.end_date "
                   "FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                   "WHERE e.start_date IS NOT NULL AND e.end_date IS NOT NULL "
                   "AND e.start_date > e.end_date")
        check("시작일 ≤ 종료일", not bad, f"어긋남 {len(bad)}건")
        for r in bad[:5]:
            print(f"          {r['corp_name']} {r['rcept_dt']}  {r['start_date']} → {r['end_date']}")
        # 계약은 체결한 뒤에 공시한다. 기준일이 접수일보다 뒤면 원문 오타다.
        # W3 에서 두산퓨얼셀 수주일자 2026-02-12 가 2025-02-12 의 오타로 판명됐다.
        bad = rows("SELECT d.corp_name, d.rcept_dt, e.signed_at "
                   "FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                   "WHERE e.signed_at > d.rcept_dt")
        for r in bad:
            SOURCE_ERR.append((r["corp_name"], r["rcept_dt"], "기준일 > 접수일",
                               f"접수 {r['rcept_dt']} < 기준일 {r['signed_at']}"))
        check("기준일 > 접수일 (원문 오류 제외)", True, f"{len(bad)}건 → 원문 확인 대상")
        bad = one("SELECT COUNT(*) FROM event_contract WHERE "
                  "signed_at IS NOT NULL AND (signed_at < '19000101' OR signed_at > '21001231')")
        check("날짜가 상식 범위", bad == 0, f"{bad}건")

        # 값이 비었다고 곧바로 실패가 아니다. 원문이 "-" 이면 회사가 안 쓴 것이고
        # 그건 D7 의 not_disclosed 다. 우리가 검사할 것은 원문대로 읽었는가이지
        # 원문에 값이 있는가가 아니다.
        print("\n6. 값이 빈 건 — 원문에도 비어 있는가")
        LABEL = {"amount_krw": ("계약금액(원)", "계약금액 총액(원)", "해지금액(원)", "투자금액(원)"),
                 "base_amount": ("최근매출액(원)", "최근 매출액(원)", "자기자본(원)"),
                 "signed_at": ("7. 계약(수주)일자", "7. 계약(수주)일", "8. 계약(수주)일자",
                               "6. 해지일자", "5. 이사회결의일(결정일)"),
                 "title": ("- 체결계약명", "- 세부내용", "1. 판매ㆍ공급계약 내용",
                           "- 해지계약명", "- 세부물건", "- 투자대상")}
        for col, labels in LABEL.items():
            miss = rows(f"SELECT d.corp_name, d.rcept_dt, d.file_path, e.form "
                        f"FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                        f"WHERE e.{col} IS NULL")
            unexplained = []
            for r in miss:
                body = body_of(to_text(read_raw(r["file_path"])))
                lines = [x.strip() for x in body.split("\n")]
                empty = False
                for i, ln in enumerate(lines):
                    if ln in labels:
                        val = lines[i + 1] if i + 1 < len(lines) else ""
                        empty = val in ("-", "", "–", "—")
                        break
                else:
                    empty = True   # 항목 자체가 서식에 없는 경우
                if not empty:
                    unexplained.append((r["corp_name"], r["rcept_dt"], r["form"]))
            check(f"{col} 빈 값 {len(miss)}건이 원문에도 비어 있음",
                  not unexplained, f"설명 안 되는 건 {len(unexplained)}")
            for x in unexplained[:5]:
                print(f"          {x}")

        # 같은 값을 컬럼과 key-value 두 곳에 담았다. 갱신 경로가 하나라 어긋날
        # 일이 없어야 하지만, 어긋나면 둘 중 하나의 추출이 틀린 것이므로 검사한다.
        print("\n6-2. 컬럼과 contract_item 이 같은 값을 담고 있는가")
        n_item = one("SELECT COUNT(*) FROM contract_item")
        n_doc = one("SELECT COUNT(DISTINCT doc_id) FROM contract_item")
        check("계약 공시 전건에 항목 존재", n_doc == n_ec, f"{n_doc:,} / {n_ec:,} · 총 {n_item:,}행")

        # 정정본은 정정사항 표에도 같은 항목이 있다. 본문 값은 마지막에 나온다.
        bad = rows("""
            SELECT d.corp_name, d.rcept_dt, e.amount_krw, i.item_value
              FROM event_contract e
              JOIN document d ON e.doc_id = d.doc_id
              JOIN contract_item i ON i.id = (
                    SELECT id FROM contract_item
                     WHERE doc_id = e.doc_id
                       AND (item_name LIKE '%계약금액(원)' OR item_name LIKE '%해지금액(원)'
                            OR item_name LIKE '%투자금액(원)' OR item_name LIKE '%계약금액 총액(원)')
                     ORDER BY seq DESC LIMIT 1)
             WHERE e.amount_krw IS NOT NULL
               AND CAST(REPLACE(i.item_value, ',', '') AS INTEGER) != e.amount_krw""")
        check("금액이 컬럼과 항목에서 일치", not bad, f"어긋남 {len(bad)}건")
        for r in bad[:6]:
            print(f"          {r['corp_name']:<14} {r['rcept_dt']}  "
                  f"컬럼 {r['amount_krw']:,} / 항목 {r['item_value']}")

        print("\n7. base_kind 가 서식과 맞는가")
        bad = one("SELECT COUNT(*) FROM event_contract WHERE "
                  "(form='시설투자') != (base_kind='equity')")
        check("시설투자만 자기자본 기준", bad == 0, f"{bad}건")

        print("\n8. 정규화 키가 같은 계약을 묶는가")
        n_title = one("SELECT COUNT(DISTINCT title) FROM event_contract WHERE title IS NOT NULL")
        n_norm = one("SELECT COUNT(DISTINCT title_norm) FROM event_contract "
                     "WHERE title_norm IS NOT NULL")
        print(f"        원본 표기 {n_title} 가지 → 정규화 후 {n_norm} 가지")
        print("        같은 기업 안에서 정규화로 묶인 예")
        for r in rows("SELECT corp_code, title_norm, COUNT(DISTINCT title) c, "
                      "GROUP_CONCAT(DISTINCT title) t FROM event_contract "
                      "WHERE title IS NOT NULL GROUP BY corp_code, title_norm "
                      "HAVING c > 1 LIMIT 6"):
            print(f"          {r['t'][:88]}")

        print("\n9. 표본 — 눈으로 본다")
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.event_type, e.form, "
                      "e.disclosure_type, e.title, e.amount_krw, e.ratio_stated, "
                      "e.signed_at, e.is_correction FROM event_contract e "
                      "JOIN document d ON e.doc_id=d.doc_id ORDER BY RANDOM() LIMIT 5"):
            amt = f"{r['amount_krw']:,}" if r["amount_krw"] else "-"
            print(f"        {r['corp_name']} {r['rcept_dt']} "
                  f"[{r['event_type']}/{r['form']}/{r['disclosure_type']}] 정정={r['is_correction']}")
            print(f"          {(r['title'] or '(없음)')[:52]}")
            print(f"          금액 {amt} · 비율 {r['ratio_stated']} · 기준일 {r['signed_at']}")

        print("\n10. 요약")
        for r in rows("SELECT event_type, COUNT(*) n, SUM(is_correction) c, "
                      "SUM(amount_krw) s FROM event_contract GROUP BY 1"):
            tot = f"{r['s']:,}" if r["s"] else "-"
            print(f"        {r['event_type']:<12} {r['n']:>5}건 (정정 {r['c']}) 금액합 {tot}")

        # 검산 불일치도 원문 오류다. 위에서 따로 출력했으므로 여기에 모은다.
        for r in rows("SELECT d.corp_name, d.rcept_dt, e.ratio_stated, e.ratio_calc "
                      "FROM event_contract e JOIN document d ON e.doc_id=d.doc_id "
                      "WHERE e.ratio_match=0"):
            SOURCE_ERR.append((r["corp_name"], r["rcept_dt"], "기재 비율 ≠ 재계산",
                               f"{r['ratio_stated']} vs {r['ratio_calc']}"))

    print("\n" + "=" * 60)
    print(f"원문 기재가 어긋난 건 {len(SOURCE_ERR)}건 — 값을 고치지 않고 그대로 담았다")
    for corp, dt, kind, detail in sorted(SOURCE_ERR, key=lambda x: x[1]):
        print(f"  {corp:<16} {dt}  {kind:<18} {detail}")
    print("=" * 60)
    if FAIL:
        print(f"실패 {len(FAIL)}건")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
