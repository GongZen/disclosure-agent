# -*- coding: utf-8 -*-
"""S2 — 대상 확정.

질의에 등장한 이름을 실제 기업으로 확정한다.
사용자는 통용명으로 묻고 데이터는 법인명으로 되어 있으므로 여기서 잇는다.

읽는 것은 company 한 테이블뿐이고 아무것도 쓰지 않는다.

조회 방식은 docs/SCHEMA.md 기준 계층 절을 따른다.
별칭 테이블은 두지 않는다. 다섯 컬럼 값이 서로 겹치지 않아 동시 조회로 판별된다
(실측 2026-08-18: corp_name·listed_name·sector·industry·market 쌍별 교집합 0).

실행
    python src/match.py "삼성전자" "전력기기" "현대"
"""

from __future__ import annotations

import difflib
import sqlite3
import sys
import unicodedata as ud
from typing import Iterable

from db import connect

__all__ = ["match_target", "match_targets", "MATCH_COLUMNS"]

# 조회 순서. 개별 기업을 먼저 보고 묶음으로 내려간다.
# 값이 겹치지 않으므로 순서가 결과를 바꾸지는 않는다. 읽는 사람을 위한 배치다.
MATCH_COLUMNS = ("corp_name", "listed_name", "sector", "industry", "market")

# 개별 기업을 가리키는 컬럼과 묶음을 가리키는 컬럼.
# 뒤 단계에서 하는 일이 다르다. 개별은 그 회사만, 묶음은 속한 회사 전부로 넓힌다.
_COMPANY_COLS = ("corp_name", "listed_name")
_GROUP_COLS = ("sector", "industry", "market")

_SIMILARITY_CUTOFF = 0.6   # difflib 기본값. 오타 후보를 고를 때만 쓴다


def _norm(s: str) -> str:
    """비교용 정규화. 표기가 흔들리는 세 가지를 같게 만든다.

    1. 한글 자모 분리(NFD)와 완성형(NFC)   manifest 의 file_path 와 같은 문제
                                          (docs/DATASET.md 한글 경로 항목)
    2. 공백                                "JYP Ent" 와 "JYPEnt"
    3. 영문 대소문자                        "jyp" 와 "JYP"

    셋 다 같은 이름의 다른 표기일 뿐 다른 이름이 아니다. 별칭이 아니라 정규화다.
    소문자 접기로 서로 다른 값이 겹치지 않음을 확인했다
    (실측 2026-08-19: 다섯 컬럼 전체 값 100종에서 충돌 0).
    """
    return ud.normalize("NFC", s).replace(" ", "").strip().lower()


def _all_values(con: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """컬럼별 {정규화값: 원본값} 사전. 70행이라 통째로 올려도 부담이 없다."""
    out: dict[str, dict[str, str]] = {}
    for col in MATCH_COLUMNS:
        rows = con.execute(f"SELECT DISTINCT {col} FROM company WHERE {col} IS NOT NULL")
        out[col] = {_norm(r[0]): r[0] for r in rows}
    return out


def match_target(name: str, con: sqlite3.Connection,
                 _cache: dict | None = None) -> dict:
    """이름 하나를 확정한다.

    돌려주는 것
        raw         질의에 있던 원문자열
        status      resolved | ambiguous | out_of_scope
        matched_by  걸린 컬럼. 못 찾으면 None
        kind        company | group. 못 찾으면 None
        companies   확정된 기업 목록
        candidates  못 찾았거나 여럿일 때 제시할 후보

    status 값의 뜻
        resolved      한 컬럼에서 정확히 걸렸다
        ambiguous     부분 일치로 여럿이 걸렸다. 되묻지 않고 후보를 답변에 제시한다
                      (docs/PIPELINE.md S2)
        out_of_scope  제공 데이터 70개사 밖이다. docs/GLOSSARY.md D7 의 out_of_scope
    """
    values = _cache if _cache is not None else _all_values(con)
    key = _norm(name)

    # 1. 완전 일치. 다섯 컬럼을 동시에 본다.
    for col in MATCH_COLUMNS:
        if key in values[col]:
            actual = values[col][key]
            rows = con.execute(
                f"SELECT corp_code, corp_name, listed_name, sector, industry, market "
                f"FROM company WHERE {col} = ? ORDER BY corp_name",
                (actual,),
            ).fetchall()
            return {
                "raw": name,
                "status": "resolved",
                "matched_by": col,
                "kind": "company" if col in _COMPANY_COLS else "group",
                "companies": [dict(r) for r in rows],
                "candidates": [],
            }

    # 2. 분류 부분 일치. "반도체·전자부품" 을 "반도체" 로 줄여 부르는 경우다.
    #
    #    sector 값 20종 중 8종이 '·' 로 묶인 복합어다. 사람은 그 한쪽만 부른다.
    #    여기 도달했다는 것은 1단계에서 기업 완전 일치가 없었다는 뜻이므로,
    #    분류 이름의 조각으로 읽히는 말은 기업 부분 일치보다 분류를 앞세운다.
    #
    #    이 순서가 없으면 "자동차" 가 현대자동차 한 곳으로 조용히 확정된다.
    #    틀렸다는 신호가 남지 않아 지표 6 안전성에서 가장 위험한 형태다.
    ghits: list[tuple[str, str]] = []
    for col in _GROUP_COLS:
        for k, actual in values[col].items():
            if key and key in k:
                ghits.append((col, actual))
    if ghits:
        uniq = sorted({a for _, a in ghits})
        if len(uniq) == 1:
            col = next(c for c, a in ghits if a == uniq[0])
            rows = con.execute(
                f"SELECT corp_code, corp_name, listed_name, sector, industry, market "
                f"FROM company WHERE {col} = ? ORDER BY corp_name",
                (uniq[0],),
            ).fetchall()
            return {
                "raw": name, "status": "resolved", "matched_by": col,
                "kind": "group", "companies": [dict(r) for r in rows],
                "candidates": [],
            }
        # "소비재" 처럼 여러 분류에 걸리는 말이다. 되묻지 않고 후보를 넘긴다.
        return {
            "raw": name, "status": "ambiguous", "matched_by": None, "kind": None,
            "companies": [], "candidates": uniq,
        }

    # 3. 기업 부분 일치. "현대" 처럼 줄여 부르는 경우다.
    hits: list[tuple[str, str]] = []
    for col in _COMPANY_COLS:
        for k, actual in values[col].items():
            if key and key in k:
                hits.append((col, actual))
    if hits:
        seen, names = set(), []
        for col, actual in hits:
            if actual not in seen:
                seen.add(actual)
                names.append((col, actual))
        if len(names) == 1:
            col, actual = names[0]
            rows = con.execute(
                f"SELECT corp_code, corp_name, listed_name, sector, industry, market "
                f"FROM company WHERE {col} = ?", (actual,)).fetchall()
            return {
                "raw": name, "status": "resolved", "matched_by": col,
                "kind": "company", "companies": [dict(r) for r in rows],
                "candidates": [],
            }
        return {
            "raw": name, "status": "ambiguous", "matched_by": None, "kind": None,
            "companies": [], "candidates": sorted({a for _, a in names}),
        }

    # 4. 유사도. 오타로 보고 후보만 제시한다. 확정하지 않는다.
    pool = list(values["corp_name"]) + list(values["listed_name"])
    close = difflib.get_close_matches(key, pool, n=3, cutoff=_SIMILARITY_CUTOFF)
    cands = []
    for k in close:
        cands.append(values["corp_name"].get(k) or values["listed_name"].get(k))
    return {
        "raw": name, "status": "out_of_scope", "matched_by": None, "kind": None,
        "companies": [], "candidates": [c for c in cands if c],
    }


def match_targets(names: Iterable[str],
                  con: sqlite3.Connection | None = None) -> list[dict]:
    """이름 여러 개를 확정한다. S1 이 뽑은 기업·분류 목록을 그대로 넘기면 된다."""
    own = con is None
    con = con or connect()
    try:
        cache = _all_values(con)
        return [match_target(n, con, cache) for n in names]
    finally:
        if own:
            con.close()


def _fmt(r: dict) -> str:
    if r["status"] == "resolved":
        head = f"{r['raw']}  →  {r['matched_by']} ({r['kind']}) · {len(r['companies'])}개사"
        body = "\n".join(f"      {c['corp_code']}  {c['corp_name']}" for c in r["companies"][:8])
        more = f"\n      … 외 {len(r['companies'])-8}개사" if len(r["companies"]) > 8 else ""
        return head + ("\n" + body + more if body else "")
    if r["status"] == "ambiguous":
        return f"{r['raw']}  →  여럿에 걸림. 후보: {', '.join(r['candidates'])}"
    tail = f" 비슷한 이름: {', '.join(r['candidates'])}" if r["candidates"] else ""
    return f"{r['raw']}  →  out_of_scope (제공 데이터 70개사 밖).{tail}"


if __name__ == "__main__":
    args = sys.argv[1:] or ["삼성전자", "현대차", "전력기기", "필수소비재",
                            "KOSDAQ", "현대", "네이버파이낸셜"]
    for r in match_targets(args):
        print(_fmt(r))
