# -*- coding: utf-8 -*-
"""회계·공시 용어 사전. BM25 검색에서 복합 용어가 쪼개지는 것을 막는다.

kiwipiepy 는 일반 한국어를 대상으로 만들어져 회계 용어를 하나의 낱말로 알지
못한다. 그대로 두면 이렇게 잘린다.

    자산총계    →  자산 · 총계
    미지급비용  →  지급 · 비용        '미' 가 접두사라 버려져 뜻이 뒤집힌다

실측으로 확인한 결과다. 삼성전자 조각 2,331개에서 상위 10개 중 질의 낱말이
원문에 실제로 든 것의 수다.

    질의          지금  접두사보존  사전
    미지급비용        5      10      10
    자산총계          3       3      10
    부채총계          9       9      10
    매출채권         10      10      10
    순이익          10      10      10

문제가 두 갈래고 해법이 다르다.

    접두사 소실       규칙으로 해결된다. 사전이 필요 없다
    흔한 조각의 조합   사전이 필요하다
                     "자산" 과 "총계" 가 둘 다 여러 번 나오는 부문별 보고
                     조각이, 정작 "자산총계" 가 한 번 적힌 재무상태표보다
                     점수가 높았다

## 선별 지표를 쓰지 않는 이유

"쪼개면 나빠지는 것" 을 지표로 가리려다 두 번 어긋났다. 독립비율은
자산총계를 네 번째로 놓았고, 혼동도는 영업이익을 자산총계보다 위험하다고
했다. 실측 순서를 어느 조합으로도 재현하지 못했다.

BM25 점수는 IDF·TF·문서 길이가 얽힌 값이라 단일 지표로 근사가 안 된다.
그래서 선별을 포기하고 넣을 자격만 본다. 사전에 넣어서 나빠진 사례를
실측에서 하나도 못 찾았기 때문이다.

## 사전을 만드는 규칙

    가  표의 행 머리에서 나온 것          계정명이 거기 있다
    나  전수에서 20회 이상               드물면 검색에 안 쓰인다
    다  길이 3~14자
    라  명사로만 이루어진 것              '및' '와' 로 이은 계정명은 살린다
    마  실제로 쪼개지는 것만
    바  lookup.py 의 LABEL·KEYWORD      사람이 이미 정한 것

    제외  연속 숫자 4자리 이상             "20230101기초자본"
          숫자로 시작하는 7자 이하          "3개월" · "1년이내"
          8자 미만인데 숫자가 든 것         "수준1" · "등급1"
          표 서식 낱말                    "구분" · "합계"

만드는 과정은 `scripts/build_terms.py` 와 `scripts/make_dictionary.py`,
목록은 `data/terms/dictionary.csv` 에 있다.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

__all__ = ["load", "terms", "parts_of", "dropped_parts"]

_DIR = Path(__file__).resolve().parents[1] / "data" / "terms"
_CSV = _DIR / "dictionary.csv"
_PARTS = _DIR / "parts_df.csv"

# ── 조각을 거르는 두 규칙 ─────────────────────────────────────────────
#
# 사전 낱말의 조각을 전부 토큰으로 내면 쓸모없는 것이 섞인다.
#
#     당분기말  →  당분 · 기말      '당분' 은 설탕이라는 뜻이다
#     당분기    →  당분             형태소 분석기가 잘못 자른 조각이다
#     전분기    →  전분             '전분' 은 녹말이다
#     기업명    →  기업             너무 포괄적이라 변별력이 없다
#
# 조각 3,755개를 전수로 재서 두 신호로 갈랐다.
#
#     DF 비율      그 조각이 든 조각 수 ÷ 전체 조각 수
#                  높으면 어디에나 있어 변별력이 없다
#     원어 대비     조각의 문서빈도 ÷ 그것을 쓰는 용어의 문서빈도
#                  1 에 가까우면 그 조각이 원어 밖에서 사실상 안 쓰인다
#
# 실측값이다.
#
#     조각      DF      비율    원어대비   판정
#     당분   29,894  17.42%    1.02   원어 전용
#     전분   17,282  10.07%    1.01   원어 전용
#     기업   84,058  49.00%    2.88   너무 흔함
#     기타   95,219  55.50%    3.00   너무 흔함
#     기말   75,739  44.15%    3.57   너무 흔함
#     단위  127,600  74.37%   47.65   너무 흔함
#
#     순이익 21,263  12.39%    1.57   유지
#     잉여금 13,747   8.01%    1.18   유지
#     총계   12,343   7.19%    1.74   유지
#     채권   46,474  27.09%    2.04   유지
#
# 145개(3.9%)가 걸린다. IDF 가 낮아 원래 점수 기여가 없던 것들이라
# 검색 품질은 거의 안 변하고 토큰만 줄어든다.
#
# 임계값은 분포를 재고 지적받은 사례가 어디 놓이는지 본 뒤 정했다.
# 더 느슨하게 잡으면(35% · 1.10) '재고' · '포괄' 도 빠지는데, 그것들은
# 단독 질의에 쓰일 만해서 남겼다.
SHARE_MAX = 0.40      # 이 비율 이상이면 너무 흔하다
VS_OWNER_MIN = 1.05   # 이 값 미만이면 원어 안에서만 나온다


@lru_cache(maxsize=1)
def dropped_parts() -> frozenset[str]:
    """부분 토큰에서 뺄 조각. 측정 파일이 없으면 아무것도 안 뺀다."""
    if not _PARTS.exists():
        return frozenset()
    out = set()
    with _PARTS.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            share = float(r["share"])
            vs = float(r["vs_owner"])
            if share >= SHARE_MAX or 0 < vs < VS_OWNER_MIN:
                out.add(r["part"])
    return frozenset(out)


@lru_cache(maxsize=1)
def load() -> dict[str, tuple[str, ...]]:
    """용어 → 그 조각들. 파일이 없으면 빈 사전을 낸다.

    빈 사전이어도 동작은 한다. 사전 없이 만든 토큰과 같은 결과가 나온다.
    """
    if not _CSV.exists():
        return {}
    drop = dropped_parts()
    out: dict[str, tuple[str, ...]] = {}
    with _CSV.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = r["term"].strip()
            if t:
                out[t] = tuple(p for p in r["parts"].split()
                               if p != t and p not in drop)
    return out


def terms() -> tuple[str, ...]:
    return tuple(load())


def parts_of(word: str) -> tuple[str, ...]:
    return load().get(word, ())
