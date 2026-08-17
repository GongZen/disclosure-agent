# -*- coding: utf-8 -*-
"""공시 원문의 표를 구조 그대로 읽어 항목-값 쌍으로 만든다.

평문으로 만들면 구조가 사라진다. 본문 문장이 줄바꿈된 조각까지 항목으로
잡히고, 세로로 병합된 칸의 항목명이 없어지고, 한 행에 값이 둘 이상일 때
하나를 잃는다. 표를 그대로 읽으면 첫 칸이 항목이고 뒤가 값이라는 것이
문서 형식으로 보장된다.

세 종류를 모두 다룬다.

    주요사항보고서   XML   TABLE / TR / TD · TE       깨진 문서가 있어 복구 파싱
    거래소공시       HTML  table / tr / td            확장자는 .xml 이지만 HTML
    지분공시         XML   TABLE / TR / TD · TE

병합된 칸은 풀어서 격자로 만든다. "1. 처분예정주식(주)" 하나가 보통주식과
기타주식 두 행을 덮는 경우, 풀지 않으면 둘째 행의 항목명이 사라진다.
"""

from __future__ import annotations

import re

from lxml import etree
from lxml import html as lhtml

__all__ = ["parse", "iter_tables", "grid", "row_pairs",
           "table_pairs", "extract_items"]

_XML_PARSER = etree.XMLParser(recover=True, huge_tree=True)
_CELL_TAGS = {"td", "th", "te", "tu"}
_ROW_TAG = "tr"
_TABLE_TAG = "table"

# 값처럼 보이는 칸. 숫자·날짜·비율·여부·빈칸이다.
# 이 패턴으로 값의 자리를 찾고 그 앞 칸을 항목명으로 짝짓는다.
_VALUEISH = re.compile(
    r"^\s*(?:[-–—]|해당|미해당|예|아니오|유|무|Y|N"
    r"|\(?-?[\d,]+(?:\.\d+)?\)?%?"
    r"|\d{4}\s*[-.년]\s*\d{1,2}\s*[-.월]\s*\d{1,2}\s*일?)\s*$")


def parse(raw: str):
    """원문을 트리로 만든다. XML 이면 복구 모드, HTML 이면 HTML 파서.

    DART XML 에는 깨진 것이 있다. `NH INVESTMENT & SECURITIES` 처럼 & 가
    이스케이프되지 않았거나, 본문의 `<별표3-3>` 이 태그로 오인된다.
    복구 모드로 읽으면 598건 전부 통과하고 내용도 잃지 않는다.
    """
    head = raw[:400].lower()
    if "<html" in head or "<!doctype html" in head:
        return lhtml.fromstring(raw)
    return etree.fromstring(raw.encode("utf-8"), parser=_XML_PARSER)


def _tag(el) -> str:
    t = el.tag
    return t.lower() if isinstance(t, str) else ""


def _attr(el, name: str) -> str | None:
    """속성 이름의 대소문자가 형식마다 다르다. XML 은 ROWSPAN, HTML 은 rowspan."""
    return el.get(name) or el.get(name.upper())


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def iter_tables(root):
    for el in root.iter():
        if _tag(el) == _TABLE_TAG:
            yield el


def grid(table) -> list[list[str]]:
    """표를 격자로 만든다. 세로 병합은 아래 행에 값을 복사해 푼다."""
    out: list[list[str]] = []
    carry: dict[int, tuple[str, int]] = {}   # 열 → (값, 남은 행 수)
    for tr in table.iter():
        if _tag(tr) != _ROW_TAG:
            continue
        cells = [c for c in tr if _tag(c) in _CELL_TAGS]
        row: list[str] = []
        col = ci = 0
        while ci < len(cells) or col in carry:
            if col in carry:
                val, left = carry[col]
                row.append(val)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (val, left - 1)
                col += 1
                continue
            c = cells[ci]
            ci += 1
            v = _text(c)
            try:
                rspan = int(_attr(c, "rowspan") or 1)
                cspan = int(_attr(c, "colspan") or 1)
            except ValueError:
                rspan = cspan = 1
            for k in range(max(1, cspan)):
                row.append(v if k == 0 else "")
                if rspan > 1:
                    carry[col] = (v if k == 0 else "", rspan - 1)
                col += 1
        out.append(row)
    return out


def row_pairs(row: list[str]) -> list[tuple[str, str]]:
    """한 행에서 (항목 경로, 값) 쌍을 뽑는다.

    한 행에 쌍이 여럿 있을 수 있다.
        보유현황 │ 배당가능이익범위 내 취득 │ 보통주식 │ 25,940 │ 비율(%) │ 0.0
        →  … > 보통주식 = 25,940
           … > 비율(%)  = 0.0
    마지막 칸만 값으로 보면 25,940 을 잃는다.
    """
    cells = [c.strip() for c in row if c.strip()]
    if len(cells) < 2:
        return []
    # 첫 칸이 값처럼 보여도 그 자리는 항목명이다
    vpos = [i for i, c in enumerate(cells) if i > 0 and _VALUEISH.match(c)]
    if not vpos:
        return [(" > ".join(cells[:-1]), cells[-1])]
    path = cells[:vpos[0] - 1]
    out = []
    for i in vpos:
        if i - 1 < len(path):
            continue
        name = cells[i - 1]
        out.append((" > ".join([*path, name]) if path else name, cells[i]))
    return out or [(" > ".join(cells[:-1]), cells[-1])]


def _is_header_row(cells: list[str]) -> bool:
    """첫 행이 헤더인가. 칸이 셋 이상이고 값처럼 보이는 것이 없으면 헤더다."""
    live = [c for c in cells if c.strip()]
    if len(live) < 3:
        return False
    return not any(_VALUEISH.match(c) for c in live)


def _header_depth(g: list[list[str]]) -> int:
    """머리글이 몇 줄인가. 위에서부터 연속으로 머리글인 행을 센다.

    지분공시의 표는 머리글이 두 줄인 경우가 많다.

        줄1  관 계 │ 성 명 │ 주 권 │ 신주인수권이 표시된 것 │ 전환사채권 │ …
        줄2  (병합) │ (병합) │ 의결권있는 주식 │ 의결권있는 주식으로 상환될 주식 │ …

    한 줄만 머리글로 보면 둘째 줄이 데이터로 섞이고 열 이름이 밀린다.
    """
    d = 0
    for row in g[:3]:
        if _is_header_row(row):
            d += 1
        else:
            break
    return d


def _merge_headers(g: list[list[str]], depth: int) -> list[str]:
    """여러 줄 머리글을 열마다 합친다. 세로 병합으로 같은 값이 겹치면 하나만 남긴다."""
    width = max(len(r) for r in g[:depth])
    out = []
    for i in range(width):
        parts = []
        for r in g[:depth]:
            v = r[i].strip() if i < len(r) else ""
            if v and v not in parts:
                parts.append(v)
        out.append(" ".join(parts))
    return out


def table_pairs(g: list[list[str]]) -> list[tuple[str, str]]:
    """표 하나를 항목-값 쌍으로. 머리글이 있는 표와 없는 표를 나눠 다룬다.

    머리글이 있는 표는 위쪽 몇 줄이 열 이름이고 이후 행이 데이터다.
    "첫 칸이 항목" 규칙으로 읽으면 값이 이름 자리로 밀린다.
    열 이름과 칸을 자리로 짝지어야 한다.
    """
    if not g:
        return []
    base = [p for row in g for p in row_pairs(row)]

    depth = _header_depth(g)
    if depth == 0:
        return base
    head = _merge_headers(g, depth)

    # 머리글 해석과 행 단위 해석을 둘 다 담는다.
    # 정정사항 표처럼 세로 병합이 겹친 표에서는 머리글 해석이 값을 뭉갠다.
    # 한쪽만 쓰면 그런 표에서 본문 값을 잃으므로 합치고 중복만 없앤다.
    byhead = []
    for row in g[depth:]:
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        label = cells[0] if cells and cells[0] else ""
        for i, v in enumerate(cells):
            if i == 0 or not v:
                continue
            col = head[i] if i < len(head) and head[i] else f"열{i}"
            name = f"{label} > {col}" if label else col
            byhead.append((name, v))

    seen, out = set(), []
    for pair in byhead + base:
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out



def extract_items(raw: str, max_name: int = 160) -> list[tuple[int, str, str]]:
    """원문에서 모든 항목-값 쌍을 순서대로 뽑는다.

    seq 를 붙이는 이유는 같은 항목명이 한 문서에 여러 번 나오기 때문이다.
    타법인 주식 양수 공시에는 대상 법인이 여러 곳이고 각각 성명·지분이
    표로 반복된다. 실측 최대 39회다.
    """
    root = parse(raw)
    if root is None:
        return []
    items, seq = [], 0
    for table in iter_tables(root):
        for name, val in table_pairs(grid(table)):
            if not name or len(name) > max_name:
                continue
            seq += 1
            items.append((seq, name, val))
    return items
