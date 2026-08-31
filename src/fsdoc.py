# -*- coding: utf-8 -*-
"""정기공시에서 재무제표 표를 찾아낸다.

문서 하나에 표가 1,300개까지 있다. 그중 재무제표 본문만 골라야 하는데,
같은 계정 이름이 주석과 부문정보 표에도 나오기 때문에 이름만으로는 못 가린다.
`ifrs-full_Revenue` 가 연결 손익계산서에 36회, 영업부문 표에 82회 나온 실측이
`DECISIONS.md` 2026-08-13 에 있다.

그래서 목차 구간으로 먼저 좁힌다.

    "2. 연결재무제표" ~ "3." 사이   연결 본문
    "4. 재무제표"     ~ "5." 사이   별도 본문

이 구간에는 표가 8~17개뿐이고 주석과 부문정보는 구조적으로 들어올 수 없다.
9개 기업 30여 문서로 확인했고 전수에서 1,051/1,054 건이 잡혔다.

구간 안에서 표 종류를 정할 때는 근거를 강한 것부터 쓴다. 순서는 마지막
보조로만 쓰고, 순서로 정한 것에는 표시를 남긴다.
"""

from __future__ import annotations

import re

from lxml import etree

__all__ = ["FS_KINDS", "iter_statements", "parse_doc"]

_P = etree.XMLParser(recover=True, huge_tree=True)
_CELL = ("TD", "TH", "TE", "TU")

_OPEN = re.compile(r"^([24])[.．]\s*(연결재무제표|재무제표)$")
_CLOSE = re.compile(r"^([35])[.．]")

FS_KINDS = ("bs", "is", "ci", "eq", "cf")

# 이름으로 종류를 정한다. 순서가 중요하다.
# "포괄손익계산서" 안에 "손익계산서" 가 들어 있어 뒤집으면 전부 손익계산서가 된다.
_NAME_RULES = (
    ("재무상태표", "bs"), ("대차대조표", "bs"),
    ("포괄손익계산서", "ci"),
    ("손익계산서", "is"),
    ("현금흐름표", "cf"),
    ("자본변동표", "eq"),
)

# 제목이 없는 문서에서 쓴다. 그 재무제표에만 나오는 계정 조합이다.
# 한 낱말만 보면 오판한다. 당기순이익은 손익계산서에도 자본변동표에도 있다.
_CONTENT_RULES = {
    "bs": (("자산총계", "부채총계", "자본총계", "자산", "부채"), 2),
    "cf": (("영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름",
            "영업활동으로인한현금흐름", "현금및현금성자산"), 2),
    "eq": (("자본금", "이익잉여금", "기타자본", "자본조정", "비지배지분"), 3),
    "ci": (("총포괄손익", "기타포괄손익", "포괄손익"), 1),
    "is": (("매출총이익", "영업이익", "매출액", "영업수익", "당기순이익"), 2),
}

# 법정 서식의 배열. 앞의 근거로 안 갈릴 때만 쓴다.
_ORDER = ("bs", "is", "ci", "eq", "cf")

# 재무제표 5종이 아닌 표. 이익잉여금처분계산서와 결손금처리계산서다.
# 전수 조사에서 미판정 482개 중 167개가 이 계열이었고, 그중 75개가
# _kind_by_content 에서 ci(43) 또는 bs(32) 로 읽혔다.
# 처분계산서에 "기타포괄손익누계액 대체" 행이 있어 ci 규칙(need=1)에 걸린다.
# 지금은 같은 종류가 이미 잡혀 있어 중복으로 버려지지만 우연에 기댄 것이다.
# 포괄손익계산서가 먼저 안 잡힌 문서에서는 미처분이익잉여금이 포괄손익 값이 된다.
# 표 어디에 있어도 처분계산서로 확정되는 말. 자본변동표에는 나오지 않는다.
_NOT_FS = re.compile(
    r"이익잉여금처분계산서|결손금처리계산서"
    r"|처분예정일|처분확정일|처리예정일|처리확정일|처분확정일")
# 행의 첫 칸에 올 때 처분계산서로 확정되는 계정.
# "미처분이익잉여금" 은 쓸 수 없다. 표본 300건에서 처분계산서 101회,
# 그 밖의 표 14회로 양쪽에 나온다. 자본변동표는 열 이름으로 쓰고
# 재무상태표는 자본을 세분화할 때 행으로 쓴다. 한전기술이 그렇다.
# 아래 셋은 처분계산서에만 나온다. 같은 표본에서 그 밖의 표 0회다.
_NOT_FS_HEAD = re.compile(r"차기이월미처분이익잉여금|이익잉여금처분액|임의적립금이입액")
# 이 계정이 행 머리에 있으면 재무제표 5종이다. 배제를 취소한다.
# 처분계산서에는 나오지 않는다. 표본에서 처분계산서 0회.
_FS_SURE = re.compile(r"자산총계|부채총계|자본총계|영업이익|매출총이익"
                      r"|영업활동|투자활동|재무활동")

# 값이 든 칸인가. 쉼표·괄호·△를 포함한 네 글자 이상이면 숫자로 본다.
_NUMCELL = re.compile(r"[\(\)△▲\-\d,\.]{4,}")


def _has_value(table) -> bool:
    """표에 값 칸이 하나라도 있는가.

    데이터가 없으면 재무제표일 수 없다. 순서 채움이 주석 문장 표에
    5종 이름을 붙이는 것을 막는다. order 판정 116개 중 44개가 그랬다.
    금융지주는 손익계산서를 따로 내지 않는데, 아직 안 나온 종류가 is 하나뿐일 때
    남은 주석 표에 그 이름이 붙었다. 없는 재무제표를 만들어낸 것이다.
    """
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        cs = _cells(tr)
        if any(_NUMCELL.fullmatch(c) for c in cs[1:]):
            return True
    return False


def _is_not_fs(table) -> bool:
    """재무제표 5종이 아닌 표인가.

    두 갈래로 본다. 표 전체에서 찾는 말과 행 머리에서만 찾는 말이다.
    가르는 이유는 자본변동표만 계정이 가로로 눕기 때문이다.
    """
    heads = []
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        cs = _cells(tr)
        if cs:
            heads.append(_sq(cs[0]))
    flat = " ".join(heads)
    if _FS_SURE.search(flat):
        return False                 # 5종이 확실하다. 배제하지 않는다
    if _NOT_FS.search(" ".join(_all_cells(table, 8))):
        return True
    return bool(_NOT_FS_HEAD.search(flat))


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _tag(el) -> str:
    t = el.tag
    return t.upper() if isinstance(t, str) else ""


def _cells(tr) -> list[str]:
    out = [re.sub(r"\s+", " ", "".join(c.itertext())).strip()
           for c in tr if _tag(c) in _CELL]
    return [c for c in out if c]


def _first_row(table) -> list[str]:
    for tr in table.iter():
        if _tag(tr) == "TR":
            return _cells(tr)
    return []


def _all_cells(table, limit: int = 8) -> list[str]:
    """표의 앞부분 칸을 전부 모은다.

    제목 표는 행이 여럿이다. 단위가 넷째 행에 있는 경우가 있어
    첫 행만 보면 못 읽는다.

        연결 재무상태표
        제 57 기 1분기말 2025.03.31 현재
        제 56 기말 2024.12.31 현재
        (단위 : 백만원)          ← 여기
    """
    out, n = [], 0
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        out.extend(_cells(tr))
        n += 1
        if n >= limit:
            break
    return out


def _kind_by_name(text: str) -> str:
    k = _sq(text)
    if not k or len(k) > 26:
        return ""
    for word, code in _NAME_RULES:
        if word in k:
            return code
    return ""


def _kind_by_content(table) -> str:
    """표 안의 계정 이름으로 종류를 정한다. 제목이 없는 문서에서 쓴다."""
    if _is_not_fs(table):
        return ""
    words = set()
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        cs = _cells(tr)
        if cs:
            words.add(_sq(cs[0]))
        if len(words) > 60:
            break
    best, best_hit = "", 0
    for kind, (keys, need) in _CONTENT_RULES.items():
        hit = sum(1 for k in keys if any(k in w for w in words))
        if hit >= need and hit > best_hit:
            best, best_hit = kind, hit
    return best


# 단위는 제목 표 안에 함께 들어 있다.
#   "연결 재무상태표 │ 제 57 기 1분기말 … │ (단위 : 백만원)"
# 표 앞 3행만 훑었을 때 200만 개 ADECIMAL 대비 10건밖에 안 잡힌 이유가 이것이다.
_UNIT = re.compile(r"단위\s*[:：]?\s*(백만원|십억원|억원|천원|천달러|백만달러|원|USD|EUR|JPY|주)")
_UNIT_MULT = {"원": 1, "천원": 1_000, "백만원": 1_000_000,
              "억원": 100_000_000, "십억원": 1_000_000_000}


def unit_of(cells: list[str]) -> tuple[str, int | None]:
    """칸 목록에서 단위를 찾는다. (표기, 배수). 배수를 모르면 None."""
    for c in cells:
        m = _UNIT.search(c)
        if m:
            w = m.group(1)
            return w, _UNIT_MULT.get(w)
    return "", None


def _title_cell(cells: list[str]) -> str:
    """제목 표인가. 첫 칸이 재무제표 이름이면 그렇다.

    두 형태가 있다.
        칸 1개   "재무상태표"
        칸 4개   "연결 재무상태표 │ 제 57 기 1분기말 … │ (단위 : 백만원)"
    처음에는 칸이 하나인 것만 제목으로 봤는데, 뒤 형태에서 제목 표가
    데이터표로 오인되고 단위도 못 읽었다.
    """
    if not cells:
        return ""
    head = cells[0]
    if len(head) > 40:
        return ""
    return head if _kind_by_name(head) else ""


def _is_unit_only(cells: list[str]) -> bool:
    """이름은 없지만 단위를 든 제목 표인가.

    제목이 표 둘로 쪼개진 문서가 있다. KB금융이 그렇다.

        표0   제 16기 반기말 : 2023년 6월 30일 현재
        표1   주식회사 KB금융지주와 그 종속기업 │ (단위: 백만원)
        표2   과 목 │ 제16기 반기말 │ …            ← 데이터

    표1 을 데이터 표로 보면 단위를 잃고 미판정 표가 하나 는다.
    숫자 칸이 하나도 없으면 데이터 표가 아니다. 전수에서 157개가
    이 조건에 걸리고, 기존에 데이터 표로 쓰이던 것이 걸리는 경우는 없다.
    """
    if not unit_of(cells)[1]:
        return False
    return not any(_NUMCELL.fullmatch(c) for c in cells)


# 열마다 붙는 기간 표시. 전수 1,374개 손익계산서에서 "3개월" 1,900회와
# "누적" 1,900회가 정확히 쌍으로 나왔다. "누계" 는 8회의 변형 표기다.
_COL_Q = re.compile(r"3개월|3개월간|삼개월|당분기|전분기")
_COL_A = re.compile(r"누적|누계")
_COL_PERIOD = re.compile(r"3개월|3개월간|삼개월|누적|누계|당분기|전분기")


def columns(table) -> list[str]:
    """값 열마다 기간 유형을 낸다. 데이터 행의 두 번째 칸부터에 대응한다.

    반기·분기 보고서는 머리글이 2단이다.

        ['제 55 기 반기', '제 54 기 반기']              기수 행
        ['3개월', '누적', '3개월', '누적']               기간 행
        ['수익(매출액)', 60,005,533, 123,750,904, …]    데이터. 값 4개

    기간 행의 칸이 값 개수와 같으므로 그대로 짝지으면 된다. 계정명 칸이
    함께 든 형태(칸이 하나 많은 경우)는 앞을 떼고 쓴다.

    기간 행이 없는 문서가 있다. 사업보고서 전부와 반기·분기의 5% 가량이다.
    그때는 기수 행에서 "반기"·"분기" 표시를 본다. KB금융이 그 형태다.

        ['과 목', '제16기 반기', '제15기 반기', '제15기']
          →  cumulative · cumulative · annual

    반환값은 quarter · cumulative · annual 셋 중 하나씩이다.
    """
    heads, ncol = [], 0
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        cs = _cells(tr)
        if not cs:
            continue
        if any(_NUMCELL.fullmatch(c) for c in cs[1:]):
            ncol = len(cs) - 1
            break
        heads.append(cs)
        if len(heads) >= 4:
            break
    if ncol <= 0:
        return []

    def fit(hs):
        if len(hs) == ncol:
            return hs
        if len(hs) == ncol + 1:
            return hs[1:]
        return None

    for hs in heads:
        if not any(_COL_PERIOD.search(c) for c in hs):
            continue
        cand = fit(hs)
        if cand:
            return ["quarter" if _COL_Q.search(c) else "cumulative" for c in cand]
    for hs in heads:
        cand = fit(hs)
        if cand and any("기" in c for c in cand):
            return ["cumulative" if re.search(r"반기|분기", c) else "annual"
                    for c in cand]
    return ["annual"] * ncol


def _is_period_start(cells: list[str]) -> bool:
    """기간 표시 표인가. "…말" 이면 시점, "…부터" 면 기간이다."""
    if len(cells) != 1:
        return False
    t = _sq(cells[0])
    return bool(re.search(r"제\d+기|20\d\d[.\-년]", t)) and len(t) < 60


def iter_statements(root):
    """재무제표 구간을 훑어 (기준, 종류, 표, 근거) 를 내놓는다.

    기준은 연결·별도, 종류는 bs·is·ci·eq·cf, 근거는 어떤 방법으로
    종류를 정했는지다. 근거를 남기는 이유는 신뢰도가 다르기 때문이다.
    """
    basis, pending, buf, unit = None, "", [], ("", None)
    for e in root.iter():
        t = _tag(e)
        if t == "TITLE":
            txt = _sq("".join(e.itertext()))
            m = _OPEN.match(txt)
            if m:
                basis = "연결" if m.group(1) == "2" else "별도"
                pending, buf, unit = "", [], ("", None)
            elif basis and _CLOSE.match(txt):
                yield from _resolve(basis, buf)
                basis, pending, buf, unit = None, "", [], ("", None)
            elif basis and re.match(r"^[24]-\d", txt):
                k = _kind_by_name(txt)
                if k:
                    pending = k
        elif t == "TABLE" and basis:
            cells = _first_row(e)
            head = _title_cell(cells)
            allc = _all_cells(e)
            if head:
                pending = _kind_by_name(head)
                w, mult = unit_of(allc)
                if mult:
                    unit = (w, mult)
                continue
            if _is_unit_only(allc):
                w, mult = unit_of(allc)
                if mult:
                    unit = (w, mult)
                continue             # 이름 없는 제목 표. 단위만 받아둔다
            if _is_period_start(cells):
                continue             # 기간 표시 표. 다음 표가 데이터다
            if pending:
                buf.append((pending, e, "name", unit))
                pending = ""
            else:
                buf.append(("", e, "", unit))
    if basis:
        yield from _resolve(basis, buf)


def _resolve(basis: str, buf: list):
    """종류가 안 정해진 표를 내용과 순서로 채운다."""
    known = {k for k, _t, _s, _u in buf if k}
    for kind, table, src, unit in buf:
        if kind:
            yield basis, kind, table, src, unit
            continue
        if _is_not_fs(table):
            continue                 # 5종이 아니다. 순서 채움 대상에서 뺀다
        if not _has_value(table):
            continue                 # 값이 없다. 주석·제목 표다
        k = _kind_by_content(table)
        if k and k not in known:
            known.add(k)
            yield basis, k, table, "content", unit
            continue
        # 마지막 수단. 아직 안 나온 종류를 법정 순서로 채운다.
        left = [x for x in _ORDER if x not in known]
        if left:
            known.add(left[0])
            yield basis, left[0], table, "order", unit
        else:
            yield basis, "", table, "", unit


def parse_doc(raw: str):
    """원문에서 재무제표 표를 찾는다. [(기준, 종류, 표, 근거)]"""
    root = etree.fromstring(raw.encode("utf-8"), parser=_P)
    if root is None:
        return []
    return list(iter_statements(root))


# ─────────────────────────────────────────────────────────────
# 대체 수집분 — 본문이 PDF 에만 있는 문서
# ─────────────────────────────────────────────────────────────
#
# 3건이 있는데 셋이 서로 다르다. 한 묶음으로 다루면 안 된다.
#
#   한화에어로스페이스   viewer.html 에 본문이 있다. 표 2,045개. XML 경로 그대로 쓴다
#   KB금융 · 한화오션    viewer.html 은 목록뿐. PDF 를 읽어야 한다
#
# PDF 에서도 원칙은 같다. 목차 구간으로 먼저 좁히고 그 안에서 표를 읽는다.
# 좁히지 않으면 "1. 요약재무정보" 의 요약연결재무상태표가 본문으로 섞인다.
# KB금융 218쪽이 그 경우다.

_PDF_OPEN = re.compile(r"([24])\s*[.．]\s*(연결재무제표|재무제표)\s*$", re.M)
_PDF_SUB = re.compile(r"([24])\s*-\s*(\d)\s*[.．]\s*(연결\s*)?"
                      r"(재무상태표|손익계산서|포괄손익계산서|자본변동표|현금흐름표|대차대조표)")
_PDF_CLOSE = re.compile(r"([35])\s*[.．]\s*(연결재무제표\s*주석|재무제표\s*주석)")
_SUMMARY = re.compile(r"1\s*[.．]\s*요약재무정보|요약\s*연결\s*재무상태표|요약\s*재무상태표")


def _kind_by_rows(rows: list[list[str]]) -> str:
    """행 목록에서 종류를 정한다. PDF 표는 lxml 객체가 아니라 행 리스트다."""
    words = {_sq(r[0]) for r in rows[:80] if r and r[0]}
    best, best_hit = "", 0
    for kind, (keys, need) in _CONTENT_RULES.items():
        hit = sum(1 for k in keys if any(k in w for w in words))
        if hit >= need and hit > best_hit:
            best, best_hit = kind, hit
    return best


def pdf_basis_map(pdf_path, max_pages: int = 1200) -> dict[int, str]:
    """쪽마다 연결인지 별도인지 표시한다.

    종류는 여기서 정하지 않는다. 표가 쪽을 넘어 잘리면 앞 재무제표의 뒷부분이
    다음 쪽 머리글을 달게 되기 때문이다. KB금융에서 실제로 그랬다.
    연결·별도만 머리글로 잡고 종류는 표 내용으로 정한다.
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    out, basis = {}, None
    for pno in range(min(doc.page_count, max_pages)):
        head = doc[pno].get_text()[:600]
        if _SUMMARY.search(head) or _PDF_CLOSE.search(head):
            basis = None
        else:
            m = _PDF_OPEN.search(head) or _PDF_SUB.search(head)
            if m:
                basis = "연결" if m.group(1) == "2" else "별도"
        if basis:
            out[pno] = basis
    doc.close()
    return out


def parse_pdf(pdf_path) -> list[tuple[str, str, list, str]]:
    """PDF 에서 (기준, 종류, 표 행 목록, 근거) 를 뽑는다.

    쪽 머리글로는 연결·별도만 정하고 종류는 표 내용으로 판정한다.
    표가 쪽을 넘어 잘려도 각 조각이 같은 판정을 받아 자연히 합쳐진다.
    """
    import fitz
    bmap = pdf_basis_map(pdf_path)
    if not bmap:
        return []
    doc = fitz.open(str(pdf_path))
    merged: dict[tuple[str, str], list] = {}
    for pno, basis in sorted(bmap.items()):
        for t in doc[pno].find_tables().tables:
            rows = []
            for r in t.extract():
                cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in r]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            kind = _kind_by_rows(rows)
            if not kind:
                continue
            merged.setdefault((basis, kind), []).extend(rows)
    doc.close()
    return [(b, k, rows, "pdf") for (b, k), rows in merged.items() if rows]
