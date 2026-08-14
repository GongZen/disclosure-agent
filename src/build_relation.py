# -*- coding: utf-8 -*-
"""W3 — 관계 계층 적재.

정정본을 원본과, 계약 해지를 원계약과 잇는다. 연결 실패는 사유와 함께 기록한다.

연쇄 정정 처리
    정정본 B 가 가리키는 날짜에 또 정정본 A 가 있으면 A 의 원본을 다시 따라간다.
    최종 원본에 도달하거나 범위를 벗어날 때까지 반복하고 깊이를 기록한다.

실행
    python src/build_relation.py
"""

from __future__ import annotations

import sys
from collections import defaultdict

from corpus import read_pdf_text, read_raw, to_text
from db import connect, create_relation_schema
from relation import (count_matches, parse_contract_date, parse_contract_fields,
                      parse_corrected_fields, parse_correction_target, parse_prior_values,
                      parse_termination_target, pick_by_fields)

# 계약을 식별하는 항목. 어느 계약인가를 말하지 그 계약의 현재 값을 말하지 않는다.
# 서식마다 다르다. 신규시설투자에는 계약명이 없고 투자목적과 결의일이 그 자리다.
_IDENTITY = ("체결계약명", "수주일자")
_IDENTITY_INVEST = ("투자목적", "결의일")

CORPUS_START = "20230101"      # 수집 시작일. 이보다 이르면 구조적으로 연결 불가
MAX_CHAIN = 40                 # 연쇄 정정 추적 상한

# 상한은 순환 방지 장치가 아니다. 순환은 방문한 지목일을 기록해 막는다.
# 상한을 10 으로 두었더니 현대건설 3건이 깊이 10 의 원본에 닿기 직전에 멈췄다.
# 정정을 열 번 넘게 거친 계약이 실제로 있다. 40 은 실측 최대의 두 배 이상이다.


def load_docs(con) -> tuple[dict, dict]:
    """문서 목록과 (기업, 유형, 접수일) → 문서 색인을 만든다."""
    rows = con.execute(
        "SELECT doc_id, corp_code, doc_group, doc_subtype, major_kind, rcept_dt, "
        "is_correction, file_path, file_format, report_nm FROM document"
    ).fetchall()
    docs = {r["doc_id"]: dict(r) for r in rows}

    by_key = defaultdict(list)
    for d in docs.values():
        by_key[(d["corp_code"], d["doc_group"], d["rcept_dt"])].append(d["doc_id"])
    return docs, by_key


def _narrow(cands: list[str], docs, subtype: str | None, kind: str | None) -> list[str]:
    """같은 세부 유형만 남긴다. 일치하는 후보가 없으면 빈 목록을 돌려준다.

    정기공시 사업보고서의 정정본은 사업보고서를 가리키지 분기보고서를 가리키지 않는다.
    주요사항보고서는 doc_subtype 이 전부 비어 있으므로 major_kind 를 쓴다. 같은 날
    교환사채 발행과 자기주식 처분이 함께 공시되는 경우가 실제로 있다.

    후보가 없을 때 필터를 포기하고 아무 문서나 잡으면 안 된다. 그렇게 했더니
    "회사합병결정의 정정본"이 "타법인주식및출자증권양수결정"에 연결되는 오류가
    15건 나왔다. 틀린 이력을 답변에 내보내는 것보다 미연결이 낫다.
    """
    for field, want in (("doc_subtype", subtype), ("major_kind", kind)):
        if want is None:
            continue
        cands = [i for i in cands if docs[i][field] == want]
    return cands


def _refine(cands: list[str], fp: dict, skip: set, fields_of) -> list[str]:
    """지목일에 후보가 여럿이면 계약 내용이 어긋나는 것을 뺀다.

    같은 날 여러 계약을 공시하는 기업이 있다. 그때 "원본을 우선한다"는 규칙만으로는
    무관한 계약의 원본을 잡는다.

        한화오션 정정본   계약명 FCS(필드제어설비) 1기   수주일자 2022-01-10
        지목일 20231222 후보 3건
            정정  수상함 1척            수주 2018-12-06   → 코퍼스 밖
            정정  FCS(필드제어설비) 1기   수주 2022-01-10   → 코퍼스 밖   진짜 짝
            원본  잠수함 1척            수주 2023-12-22   ← 이것이 잡혔다

    이 건의 정답은 out_of_scope 인데 무관한 계약에 연결됐다. 계약명과 수주일자로
    거르면 진짜 짝만 남고, 그것을 따라가면 범위 밖에 닿는다.

    후보가 하나뿐일 때도 거른다. 지목일에 그 계약과 무관한 문서 하나만 있으면
    진짜 원본은 코퍼스 밖이라는 뜻이므로, 잡지 않고 미연결로 두는 것이 맞다.

    거르는 데는 계약을 식별하는 항목만 쓴다. 계약명과 수주일자다. 금액이나
    매출액대비는 정정으로 자주 바뀌고 서로 연동되므로 쓰지 않는다. 금액이 바뀌면
    매출액대비도 함께 바뀌는데 이것을 둘로 세면 멀쩡한 정정을 다른 계약으로 본다.

    두 항목이 모두 어긋나야 뺀다. 계약명 하나만 다른 것은 표기 차이인 경우가 있다.

        차륜형장갑차 4차양산  vs  차륜형장갑차 4차 양산사업   계약명만 다름. 남긴다
        FCS(필드제어설비) 1기 vs  잠수함 1척                 계약명·수주일자가
                                                        모두 다름. 뺀다
    """
    if not cands or not fp:
        return cands
    keep = []
    for c in cands:
        cf = fields_of(c)
        miss = sum(1 for k in _IDENTITY
                   if k not in skip and k in fp and k in cf and cf[k] != fp[k])
        if miss < len(_IDENTITY):
            keep.append(c)
    return keep


def resolve_chain(start_date: str, corp: str, group: str, subtype: str | None,
                  kind: str | None, docs, by_key, targets,
                  fp: dict | None = None, skip: set | None = None, fields_of=None):
    """날짜를 따라가 최종 원본을 찾는다. 너비 우선으로 탐색한다.

    한 날짜에 정정본이 여럿이면 어느 쪽을 따라갈지 알 수 없다. 그러나 그것들이
    결국 같은 원본을 가리키면 답은 하나로 확정된다. 그래서 한 갈래만 따라가지 않고
    도달 가능한 원본을 전부 모은다.

        정정본 X → 2024-05-13 (정정본 A, B 둘 다 존재)
                     A → 2024-03-11 원본 C
                     B → 2024-03-11 원본 C      → 원본 집합 {C}. 확정
    반환 (to_doc_id, candidates, depth, reason, 모호할 때의 원본 후보 집합)
    """
    frontier: set[str] = {start_date}
    originals: set[str] = set()
    reasons: set[str] = set()
    seen_dates: set[str] = set()
    depth = 0

    while frontier and depth < MAX_CHAIN:
        nxt: set[str] = set()
        for date in frontier:
            if date in seen_dates:          # 순환 방지
                continue
            seen_dates.add(date)

            if date < CORPUS_START:
                reasons.add("out_of_scope")
                continue

            cands = _narrow(by_key.get((corp, group, date), []), docs, subtype, kind)
            if fp and fields_of:
                cands = _refine(cands, fp, skip or set(), fields_of)
            if not cands:
                reasons.add("out_of_scope")
                continue

            for c in cands:
                if not docs[c]["is_correction"]:
                    originals.add(c)
                elif c in targets:
                    nxt.add(targets[c])
                else:
                    reasons.add("extract_failed")

        if originals or not nxt:            # 원본에 도달했거나 더 갈 곳이 없다
            break
        frontier = nxt
        depth += 1

    if len(originals) == 1:
        return originals.pop(), 1, depth, None, ()
    if len(originals) > 1:
        return None, len(originals), depth, "ambiguous", tuple(sorted(originals))
    if "extract_failed" in reasons and "out_of_scope" not in reasons:
        return None, 0, depth, "extract_failed", ()
    if reasons:
        return None, 0, depth, "out_of_scope", ()

    # 여기까지 왔다면 탐색이 끝났는데 원본을 하나도 못 만났다.
    #
    #   에코프로비엠  접수 20241022  지목일 20241022
    #                 그 날 그 유형 문서가 자기 자신 하나뿐이고, 그것이 다시
    #                 20241022 를 지목한다. 제자리를 돈다.
    #
    # 이건 모호한 것이 아니다. 방문한 지목일 어디에도 원본이 없었으니 원본이
    # 코퍼스에 없는 것이다. 같은 날 정정본이 원본을 갈음해 배포되면 이렇게 된다.
    # ambiguous 로 두면 D7 이 "여러 건이 있어 특정할 수 없다"고 답하는데 사실과 다르다.
    #
    # 다만 깊이 상한에 걸려 멈춘 것은 우리 쪽 한계이므로 구분해 남긴다.
    if depth >= MAX_CHAIN:
        return None, 0, depth, "ambiguous", ()
    return None, 0, depth, "no_original", ()


def build(con) -> dict:
    docs, by_key = load_docs(con)
    stat = defaultdict(int)
    rows = []

    txt_cache: dict[str, str] = {}
    fld_cache: dict[str, dict] = {}

    def doc_text(doc_id: str) -> str:
        """문서 본문. 대체 수집분은 뷰어 HTML 에 본문이 없어 PDF 로 넘어간다.

        KB금융과 한화오션의 정정본은 뷰어 HTML 을 텍스트로 뽑으면 567자·353자뿐이고
        문서 목록만 들어 있다. 정정 헤더는 PDF 에만 있다. 이 둘을 구조적 한계로
        보고 넘겼으나 pypdf 로 5쪽만 읽으면 0.4초에 나온다.
        """
        if doc_id not in txt_cache:
            path = docs[doc_id]["file_path"]
            try:
                txt = to_text(read_raw(path))
                if len(txt) < 2_000 and docs[doc_id]["file_format"] == "pdf+html":
                    txt = read_pdf_text(path, stop="최초제출일")
            except FileNotFoundError:
                txt = ""
            txt_cache[doc_id] = txt
        return txt_cache[doc_id]

    def doc_fields(doc_id: str) -> dict:
        if doc_id not in fld_cache:
            fld_cache[doc_id] = parse_contract_fields(doc_text(doc_id))
        return fld_cache[doc_id]

    # ── 1단계. 정정본 전건에서 원본 제출일과 계약 지문을 뽑는다 ────
    corrections = [d for d in docs.values() if d["is_correction"]]
    targets: dict[str, str] = {}
    hints: dict[str, str] = {}
    skips: dict[str, set] = {}        # 정정본이 고쳤다고 적은 항목. 대조에서 뺀다
    amb_pool: dict[str, tuple] = {}   # 모호한 건의 원본 후보. 2-B 에서 값 대조로 가른다

    for i, d in enumerate(corrections, 1):
        if i % 200 == 0:
            print(f"  정정 파싱 {i}/{len(corrections)}", flush=True)
        txt = doc_text(d["doc_id"])
        if not txt:
            continue
        date, hint = parse_correction_target(txt)
        if date:
            targets[d["doc_id"]] = date
            if hint:
                hints[d["doc_id"]] = hint
        skips[d["doc_id"]] = parse_corrected_fields(txt)

    # ── 2단계. 날짜를 따라 원본을 확정한다 ──────────────────────
    for d in corrections:
        did = d["doc_id"]
        date = targets.get(did)
        if not date:
            rows.append((did, None, "correction", None, None, 0, "extract_failed", 0, 0))
            stat["correction_extract_failed"] += 1
            continue

        to_id, cands, depth, reason, pool = resolve_chain(
            date, d["corp_code"], d["doc_group"], d["doc_subtype"],
            d["major_kind"], docs, by_key, targets,
            doc_fields(did), skips.get(did, set()), doc_fields
        )
        if pool:
            amb_pool[did] = pool
        rows.append((did, to_id, "correction", date, hints.get(did),
                     1 if to_id else 0, reason, cands, depth))
        stat["correction_resolved" if to_id else f"correction_{reason}"] += 1
        if to_id and depth > 0:
            stat["correction_via_chain"] += 1

    # ── 2-B. 후보가 여럿인 건을 정정 전 값으로 확정한다 ──────────
    #  날짜만으로는 같은 날 같은 유형 공시를 구분할 수 없다.
    #  정정 전 값은 원본에 있고 정정 후 값은 어느 원본에도 없다는 성질을 쓴다.
    for i, row in enumerate(rows):
        did, to_id, rtype, date, hint, resolved, reason, ncand, depth = row
        if rtype != "correction" or reason != "ambiguous" or not date:
            continue
        cands = list(amb_pool.get(did, ()))
        if len(cands) < 2:
            continue

        prior = parse_prior_values(doc_text(did))
        scores = [(count_matches(doc_text(c), prior), c) for c in cands]
        scores.sort(reverse=True)
        if scores[0][0] > 0 and scores[0][0] > scores[1][0]:
            rows[i] = (did, scores[0][1], rtype, date, hint, 1, None, ncand, depth)
            stat["correction_ambiguous"] -= 1
            stat["correction_resolved"] += 1
            stat["correction_by_value"] += 1

    # ── 3단계. 계약 해지를 원계약과 잇는다 ──────────────────────
    terms = [d for d in docs.values()
             if d["doc_group"] == "exchange" and d["doc_subtype"] == "단일판매공급계약해지"]
    for d in terms:
        txt = doc_text(d["doc_id"])
        if not txt:
            continue
        date, hint = parse_termination_target(txt)
        if not date:
            rows.append((d["doc_id"], None, "termination", None, None, 0, "extract_failed", 0, 0))
            stat["termination_extract_failed"] += 1
            continue

        if date < CORPUS_START:
            rows.append((d["doc_id"], None, "termination", date, hint, 0, "out_of_scope", 0, 0))
            stat["termination_out_of_scope"] += 1
            continue

        # 해지 공시가 지목한 날짜에 정정본만 있을 수 있다. 그 경우 정정 연결과
        # 똑같이 체인을 따라가 원계약에 도달해야 한다. 정정본을 원계약으로 잡으면
        # "언제 체결한 계약인가"에 정정일자를 답하게 된다.
        to_id, ncand, depth, reason, pool = resolve_chain(
            date, d["corp_code"], "exchange", "단일판매공급계약체결",
            None, docs, by_key, targets,
            doc_fields(d["doc_id"]), set(), doc_fields
        )
        if pool:
            amb_pool[d["doc_id"]] = pool
        rows.append((d["doc_id"], to_id, "termination", date, hint,
                     1 if to_id else 0, reason, ncand, depth))
        stat["termination_resolved" if to_id else f"termination_{reason}"] += 1
        if to_id and depth > 0:
            stat["termination_via_chain"] += 1

    # ── 3-B. 남은 후보를 계약 필드로 가른다 ──────────────────────
    #  값 대조는 쉼표가 두 번 이상 들어간 큰 숫자만 본다. 공시유보 상태로 냈던
    #  계약은 원본의 금액이 "-" 라 대조할 숫자가 없다. 날짜만 바뀐 정정도 마찬가지다.
    #
    #  그래서 필드를 직접 맞춘다. 정정본은 "4. 정정사항"에 자기가 고친 항목을
    #  적어두므로, 거기 없는 항목은 원본과 값이 같아야 한다.
    #
    #      LG에너지솔루션  정정본 기간 2027-01-01 ~ 2032-12-31
    #                        후보1  2027-01-01 ~ 2032-12-31   일치
    #                        후보2  2026-10-01 ~ 2030-12-31
    #                      정정사항에 계약기간이 없으므로 후보1 이 원본이다.
    #
    #  해지 공시에도 같은 방법을 쓴다. 해지 공시에는 정정사항 표가 없으므로
    #  제외할 항목이 없고, 본문의 계약기간이 그대로 대조에 쓰인다.
    for i, row in enumerate(rows):
        did, to_id, rtype, date, hint, resolved, reason, ncand, depth = row
        if resolved or reason != "ambiguous":
            continue
        cands = list(amb_pool.get(did, ()))
        if len(cands) < 2:
            continue
        pick = pick_by_fields(doc_text(did), {c: doc_text(c) for c in cands})
        if pick:
            rows[i] = (did, pick, rtype, date, hint, 1, None, ncand, depth)
            stat[f"{rtype}_ambiguous"] -= 1
            stat[f"{rtype}_resolved"] += 1
            stat[f"{rtype}_by_fields"] += 1

    # ── 3-C. 지목일이 쓸모없으면 내용으로 원본을 찾는다 ──────────
    #  일부 정정본은 자기 접수일을 원본 제출일로 적어둔다. 파싱 오류가 아니라
    #  원문이 그렇게 돼 있다.
    #
    #      에코프로비엠  접수 20241022  "정정관련 공시서류제출일 2024-10-22"
    #                    실제 원본은 20230523 에 있고 코퍼스 안에 있다
    #
    #  날짜를 못 믿으므로 내용으로 찾는다. 같은 기업·같은 유형의 이전 원본 중
    #  투자목적·결의일·계약명 같은 항목이 가장 많이 맞는 것을 택한다.
    #  틀린 짝을 만들지 않도록 두 항목 이상 맞고 2위와 차이가 나야 확정한다.
    for i, row in enumerate(rows):
        did, to_id, rtype, date, hint, resolved, reason, ncand, depth = row
        if reason != "no_original":
            continue
        d = docs[did]
        want_sub = "단일판매공급계약체결" if rtype == "termination" else d["doc_subtype"]
        pool = {
            o["doc_id"]: doc_text(o["doc_id"])
            for o in docs.values()
            if not o["is_correction"] and o["corp_code"] == d["corp_code"]
            and o["doc_group"] == d["doc_group"] and o["doc_subtype"] == want_sub
            and (rtype == "termination" or o["major_kind"] == d["major_kind"])
            and o["rcept_dt"] < d["rcept_dt"]
        }
        # 내용 검색은 지목일을 버리고 코퍼스 전체를 뒤지므로 가드를 더 세게 건다.
        # 지목일 후보는 "식별 항목이 어긋나지 않으면 통과"로 충분하지만, 여기서는
        # 하나라도 실제로 일치해야 한다. 어긋나지 않는 것과 맞는 것은 다르다.
        #
        #     현대건설  정정본 파나마 메트로 3호선 공사
        #               붙은 원본 샤힌 프로젝트 공사 PKG1
        #               원본에 수주일자가 없어 어긋난 항목이 계약명 하나뿐이었다
        #
        # 계약명이 다르고 수주일자를 비교조차 못 하면 같은 계약이라는 근거가 없다.
        my = doc_fields(did)
        ident = _IDENTITY_INVEST if "투자목적" in my else _IDENTITY
        pool = {k: v for k, v in pool.items()
                if any(f in my and my[f] == doc_fields(k).get(f) for f in ident)}

        pick = None
        if len(pool) > 1:
            pick = pick_by_fields(doc_text(did), pool, min_score=2)
        elif len(pool) == 1:
            pick = next(iter(pool))
        if pick:
            rows[i] = (did, pick, rtype, date, hint, 1, None, ncand, depth)
            stat[f"{rtype}_resolved"] += 1
            stat[f"{rtype}_by_content"] += 1
        else:
            rows[i] = (did, None, rtype, date, hint, 0, "out_of_scope", ncand, depth)
            stat[f"{rtype}_out_of_scope"] += 1

    # ── 4단계. 원본에 닿지 못한 건의 사유를 계약 체결일로 가른다 ──
    #  되짚기가 원본에 닿지 못하면 지금까지 ambiguous 로 뭉뚱그렸다. 그러나 그중
    #  다수는 모호한 것이 아니라 원본이 처음부터 없는 것이다. 정정본이 자기 접수일을
    #  지목해 제자리를 도는 경우가 대표적이다.
    #
    #      현대로템  접수 20240514  지목일 20240514  수주일자 2021-07-14
    #
    #  계약 체결 공시는 체결 시점에 내므로 수주일자가 수집 시작 전이면 원본 공시도
    #  범위 밖이다. D7 에서 두 사유는 다른 답변을 낳는다. ambiguous 는 우리 한계로,
    #  out_of_scope 는 데이터 한계로 읽힌다. 뭉쳐두면 데이터 한계를 우리 한계로
    #  잘못 답한다.
    #
    #  원본에 하나라도 닿은 건(candidates >= 1)은 건드리지 않는다. 그건 진짜 모호다.
    for i, row in enumerate(rows):
        did, to_id, rtype, date, hint, resolved, reason, ncand, depth = row
        if resolved or ncand or reason not in ("ambiguous", "extract_failed"):
            continue
        cd = parse_contract_date(doc_text(did))
        if not cd or cd >= CORPUS_START:
            continue
        rows[i] = (did, to_id, rtype, date, hint, 0, "out_of_scope", ncand, depth)
        stat[f"{rtype}_{reason}"] -= 1
        stat[f"{rtype}_out_of_scope"] += 1
        stat[f"{rtype}_by_contract_date"] += 1

    con.execute("DELETE FROM doc_relation")
    con.executemany(
        "INSERT INTO doc_relation (from_doc_id, to_doc_id, rel_type, target_date, "
        "target_hint, resolved, unresolved_reason, candidates, chain_depth) "
        "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()

    stat["_total"] = len(rows)
    stat["_correction"] = len(corrections)
    stat["_termination"] = len(terms)
    return dict(stat)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with connect() as con:
        create_relation_schema(con)
        st = build(con)

    print()
    print(f"정정공시 {st['_correction']:,}건")
    for k, label in [("correction_resolved", "  연결 성공"),
                     ("correction_via_chain", "    그중 연쇄 추적"),
                     ("correction_by_value", "    그중 값 대조로 확정"),
                     ("correction_by_fields", "    그중 필드 대조로 확정"),
                     ("correction_out_of_scope", "  원본이 범위 밖"),
                     ("correction_by_contract_date", "    그중 체결일로 판정"),
                     ("correction_ambiguous", "  원본 미확정"),
                     ("correction_extract_failed", "  날짜 추출 실패")]:
        print(f"{label:<22} {st.get(k, 0):>5,}")

    print(f"\n계약 해지 {st['_termination']:,}건")
    for k, label in [("termination_resolved", "  연결 성공"),
                     ("termination_via_chain", "    그중 연쇄 추적"),
                     ("termination_by_fields", "    그중 필드 대조로 확정"),
                     ("termination_out_of_scope", "  원계약이 범위 밖"),
                     ("termination_by_contract_date", "    그중 체결일로 판정"),
                     ("termination_ambiguous", "  원본 미확정"),
                     ("termination_extract_failed", "  날짜 추출 실패")]:
        print(f"{label:<22} {st.get(k, 0):>5,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
