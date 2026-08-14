# -*- coding: utf-8 -*-
"""W3 검증 — 관계 계층이 제대로 적재됐는지 확인한다.

연결 건수만 세지 않는다. 연결이 실제로 말이 되는지, 미연결 사유가 데이터와
일치하는지 본다. 미연결은 실패가 아니라 답변해야 할 사실이므로 사유가 정확해야 한다.

실행
    python scripts/verify_relation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_raw, to_text  # noqa: E402
from db import connect  # noqa: E402
from build_relation import MAX_CHAIN  # noqa: E402
from relation import (parse_contract_date, parse_contract_fields,  # noqa: E402
                      parse_corrected_fields)

FAIL = []


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
        n_corr_doc = one("SELECT COUNT(*) FROM document WHERE is_correction = 1")
        n_corr_rel = one("SELECT COUNT(*) FROM doc_relation WHERE rel_type = 'correction'")
        check("정정공시 전건에 관계 행 존재", n_corr_doc == n_corr_rel,
              f"문서 {n_corr_doc:,} / 관계 {n_corr_rel:,}")

        n_term_doc = one("SELECT COUNT(*) FROM document WHERE doc_group='exchange' "
                         "AND doc_subtype='단일판매공급계약해지'")
        n_term_rel = one("SELECT COUNT(*) FROM doc_relation WHERE rel_type = 'termination'")
        check("계약 해지 전건에 관계 행 존재", n_term_doc == n_term_rel,
              f"문서 {n_term_doc} / 관계 {n_term_rel}")

        print("\n2. 참조 무결성")
        bad = one("SELECT COUNT(*) FROM doc_relation r LEFT JOIN document d "
                  "ON r.from_doc_id = d.doc_id WHERE d.doc_id IS NULL")
        check("from_doc_id 전부 실재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r LEFT JOIN document d "
                  "ON r.to_doc_id = d.doc_id WHERE r.to_doc_id IS NOT NULL AND d.doc_id IS NULL")
        check("to_doc_id 전부 실재", bad == 0, f"{bad}건")

        print("\n3. 상태 일관성")
        bad = one("SELECT COUNT(*) FROM doc_relation WHERE resolved=1 AND to_doc_id IS NULL")
        check("resolved=1 이면 to_doc_id 존재", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation WHERE resolved=0 AND to_doc_id IS NOT NULL")
        check("resolved=0 이면 to_doc_id 없음", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation WHERE resolved=1 AND unresolved_reason IS NOT NULL")
        check("resolved=1 이면 사유 없음", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation WHERE resolved=0 AND unresolved_reason IS NULL")
        check("resolved=0 이면 사유 있음", bad == 0, f"{bad}건")

        print("\n4. 사유 값이 D7 정의 안에 있는가")
        allowed = {"out_of_scope", "ambiguous", "extract_failed"}
        got = {r[0] for r in rows("SELECT DISTINCT unresolved_reason FROM doc_relation "
                                  "WHERE unresolved_reason IS NOT NULL")}
        check("정의된 사유만 사용", got <= allowed, " · ".join(sorted(got)))

        print("\n5. 연결의 방향과 종류")
        bad = one("SELECT COUNT(*) FROM doc_relation r JOIN document d ON r.from_doc_id=d.doc_id "
                  "WHERE r.rel_type='correction' AND d.is_correction=0")
        check("correction 의 출발점은 정정본", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r JOIN document d ON r.to_doc_id=d.doc_id "
                  "WHERE r.rel_type='correction' AND d.is_correction=1")
        check("correction 의 도착점은 원본", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r "
                  "JOIN document a ON r.from_doc_id=a.doc_id "
                  "JOIN document b ON r.to_doc_id=b.doc_id "
                  "WHERE a.corp_code != b.corp_code")
        check("연결된 두 문서는 같은 기업", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r "
                  "JOIN document a ON r.from_doc_id=a.doc_id "
                  "JOIN document b ON r.to_doc_id=b.doc_id "
                  "WHERE b.rcept_dt > a.rcept_dt")
        check("원본이 정정본보다 이르다", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r "
                  "JOIN document a ON r.from_doc_id=a.doc_id "
                  "JOIN document b ON r.to_doc_id=b.doc_id "
                  "WHERE r.rel_type='correction' AND ("
                  "     COALESCE(a.doc_subtype,'') != COALESCE(b.doc_subtype,'') "
                  "  OR COALESCE(a.major_kind,'') != COALESCE(b.major_kind,''))")
        check("정정 연결은 세부 유형이 같다", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation r "
                  "JOIN document b ON r.to_doc_id=b.doc_id "
                  "WHERE r.rel_type='termination' AND b.doc_subtype != '단일판매공급계약체결'")
        check("해지 연결의 도착점은 계약 체결 공시", bad == 0, f"{bad}건")

        # 계약 공시는 같은 날 여러 건이 나온다. 유형 필터만으로는 무관한 계약의
        # 원본을 잡을 수 있으므로, 이어진 두 문서가 같은 계약인지 원문으로 확인한다.
        # 계약명과 수주일자가 둘 다 어긋나면 다른 계약이다. 하나만 다른 것은
        # 표기 차이인 경우가 있어 통과시킨다.
        pairs = rows("SELECT a.file_path, b.file_path FROM doc_relation r "
                     "JOIN document a ON r.from_doc_id=a.doc_id "
                     "JOIN document b ON r.to_doc_id=b.doc_id "
                     "WHERE r.resolved=1 AND a.doc_group='exchange' AND a.doc_subtype IN "
                     "('단일판매공급계약체결','단일판매공급계약해지')")
        # 지목일이 정정본 자기 접수일이면 내용 검색으로 이은 것이다
        selfref = {(x, y): True for x, y in rows(
            "SELECT a.file_path, b.file_path FROM doc_relation r "
            "JOIN document a ON r.from_doc_id=a.doc_id "
            "JOIN document b ON r.to_doc_id=b.doc_id "
            "WHERE r.resolved=1 AND r.target_date = a.rcept_dt "
            "AND b.rcept_dt != r.target_date")}
        bad = weak = 0
        for pa, pb in pairs:
            try:
                ta, tb = to_text(read_raw(pa)), to_text(read_raw(pb))
            except FileNotFoundError:
                continue
            fa, fb = parse_contract_fields(ta), parse_contract_fields(tb)
            skip = parse_corrected_fields(ta)
            miss = [k for k in ("체결계약명", "수주일자")
                    if k not in skip and k in fa and k in fb and fa[k] != fb[k]]
            bad += len(miss) == 2
            # 지목일을 버리고 내용으로 찾은 건은 근거가 약하므로 따로 본다.
            # 식별 항목이 하나도 일치하지 않으면 같은 계약이라는 근거가 없다.
            if selfref.get((pa, pb)):
                agree = [k for k in ("체결계약명", "수주일자")
                         if k in fa and k in fb and fa[k] == fb[k]]
                weak += not agree
        check("계약 연결은 같은 계약을 가리킨다", bad == 0, f"{len(pairs)}건 중 {bad}건 어긋남")
        check("내용으로 찾은 연결은 식별 항목이 하나 이상 일치", weak == 0,
              f"{sum(selfref.values())}건 중 근거 없음 {weak}건")

        print("\n6. 자기 참조와 순환")
        bad = one("SELECT COUNT(*) FROM doc_relation WHERE from_doc_id = to_doc_id")
        check("자기 자신을 가리키지 않음", bad == 0, f"{bad}건")
        bad = one("SELECT COUNT(*) FROM doc_relation a JOIN doc_relation b "
                  "ON a.from_doc_id = b.to_doc_id AND a.to_doc_id = b.from_doc_id")
        check("상호 참조 없음", bad == 0, f"{bad}건")

        print("\n7. out_of_scope 판정이 맞는가")
        n_early = one("SELECT COUNT(*) FROM doc_relation WHERE unresolved_reason='out_of_scope' "
                      "AND target_date < '20230101'")
        n_oos = one("SELECT COUNT(*) FROM doc_relation WHERE unresolved_reason='out_of_scope'")
        print(f"        범위 밖 {n_oos}건 중 수집 시작일 이전 {n_early}건, "
              f"기간 내이나 문서 부재 {n_oos - n_early}건")
        # 범위 밖 판정 경로는 둘이다. 지목일을 읽어 그 날짜가 범위를 벗어난 경우와,
        # 지목일을 못 읽었지만 계약 체결일이 수집 시작 전이라 원본이 있을 수 없는
        # 경우다. 뒤쪽은 target_date 가 비어 있는 것이 정상이므로 원문을 다시 읽어
        # 근거를 확인한다.
        nodate = rows("SELECT r.from_doc_id, d.file_path FROM doc_relation r "
                      "JOIN document d ON r.from_doc_id = d.doc_id "
                      "WHERE r.unresolved_reason='out_of_scope' AND r.target_date IS NULL")
        bad = []
        for did, path in nodate:
            try:
                cd = parse_contract_date(to_text(read_raw(path)))
            except FileNotFoundError:
                cd = None
            if not cd or cd >= "20230101":
                bad.append(did)
        check("지목일 없는 범위 밖은 체결일이 수집 시작 전",
              not bad, f"{len(nodate)}건 중 근거 없음 {len(bad)}건")

        print("\n8. 연쇄 정정")
        n_chain = one("SELECT COUNT(*) FROM doc_relation WHERE chain_depth > 0 AND resolved=1")
        max_d = one("SELECT COALESCE(MAX(chain_depth), 0) FROM doc_relation")
        check("연쇄 추적 상한 이내", max_d <= MAX_CHAIN,
              f"최대 깊이 {max_d} / 상한 {MAX_CHAIN} · 연쇄 연결 {n_chain}건")

        print("\n9. 결과 요약")
        for t in ("correction", "termination"):
            tot = one("SELECT COUNT(*) FROM doc_relation WHERE rel_type=?", t)
            res = one("SELECT COUNT(*) FROM doc_relation WHERE rel_type=? AND resolved=1", t)
            print(f"\n  {t}  총 {tot:,}건 · 연결 {res:,}건 ({res/tot*100:.1f}%)")
            for reason, n in rows("SELECT unresolved_reason, COUNT(*) FROM doc_relation "
                                  "WHERE rel_type=? AND resolved=0 "
                                  "GROUP BY unresolved_reason ORDER BY 2 DESC", t):
                print(f"        {reason:<16} {n:>5,}")
            struct = one("SELECT COUNT(*) FROM doc_relation WHERE rel_type=? "
                         "AND unresolved_reason='out_of_scope'", t)
            if tot > struct:
                print(f"        연결 가능분 기준 {res/(tot-struct)*100:.1f}%")

        print("\n10. 표본 확인 — 실제로 이어졌는가")
        for r in rows("SELECT a.corp_name, a.report_nm, a.rcept_dt, "
                      "b.report_nm, b.rcept_dt, x.chain_depth "
                      "FROM doc_relation x JOIN document a ON x.from_doc_id=a.doc_id "
                      "JOIN document b ON x.to_doc_id=b.doc_id "
                      "WHERE x.rel_type='correction' ORDER BY RANDOM() LIMIT 3"):
            print(f"        {r[0]}  {r[1][:34]} ({r[2]})")
            print(f"          → {r[3][:34]} ({r[4]})  깊이 {r[5]}")

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
