"""정기공시를 목차 단위로 자른다.

경계는 TITLE 태그다. 문서가 스스로 그어둔 것이라 우리가 규칙을 만들지 않는다.
그래서 DART 화면 목차와 같은 구조가 그대로 나온다.

TABLE-GROUP 안의 TITLE 도 경계가 되므로 주석 하나하나가 한 조각이 된다.
삼성전자 2025년 사업보고서의 연결재무제표 주석 34개가 34조각이 된다.
"""
from __future__ import annotations

import re

from lxml import etree

from fsdoc import _P, _tag, _cells

# 표의 셀 구분자. 표라는 것이 눈에 보이고 검색에도 무해하다.
CELL_SEP = " │ "

# I. 회사의 개요 · II. 사업의 내용
_MAJOR = re.compile(r"^\s*([IVXLC]+)\s*[\.．]\s*(.+)")
# 1. 사업의 개요 · 16. 우발부채와 약정사항
_MINOR = re.compile(r"^\s*(\d+(?:-\d+)?)\s*[\.．]\s*(.+)")


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# 깨진 표에 남기는 표시. 원문에는 없는 이름이라 충돌하지 않는다.
_MARK = "_BROKEN"


def _in_table(e) -> bool:
    """표 안에 있는가. 다만 깨진 표는 표로 치지 않는다.

    표시를 미리 붙여 두고 여기서는 읽기만 한다. 요소마다 _broken() 을
    부르면 그 표 전체를 매번 다시 훑게 되어 문서 6건에 87.7초가 걸렸다.
    파싱 자체는 0.7초다.
    """
    for p in e.iterancestors():
        if _tag(p) == "TABLE" and p.get(_MARK) is None:
            return True
    return False


def _mark_broken(root) -> int:
    """깨진 표에 표시를 남긴다. 표마다 한 번만 판정한다."""
    n = 0
    for t in root.iter():
        if _tag(t) != "TABLE":
            continue
        for c in t.iterdescendants():
            if _tag(c) == "TITLE":
                t.set(_MARK, "1")
                n += 1
                break
    return n


def _broken(table) -> bool:
    """닫히지 않은 표인가. 지금은 안전망이다.

    원문의 이스케이프 누락 때문에 표가 문서 나머지를 통째로 삼키는 일이
    있었다. CJ제일제당 2024년 사업보고서에서 "5. 정관에 관한 사항" 의 표
    하나가 899,194자였고 그 안에 II. 사업의 내용부터 문서 끝까지가 들어
    있었다. 문서 468건(44.5%)이 그런 상태였다.

    원인은 `sanitize` 로 없앴다. 파싱 전에 이스케이프를 바로잡으므로 깨진
    표가 애초에 안 생긴다. 전수 1,051건에서 0 이 확인됐다.

    그래도 이 함수를 남긴다. sanitize 가 못 잡는 유형이 나왔을 때 문서
    전체를 잃는 것보다 그 표만 우회하는 편이 낫다. `verify_section.py` 의
    5번 검사가 원문을 직접 보므로 우회했다는 사실 자체는 관문에서 드러난다.

    표 안에 TITLE 이 있으면 깨진 것으로 본다. 정상인 표에는 목차 제목이
    들어갈 이유가 없다.
    """
    for c in table.iterdescendants():
        if _tag(c) == "TITLE":
            return True
    return False


def _own_row(tr, table) -> bool:
    """이 행이 이 표의 것인가. 중첩된 안쪽 표의 행이면 아니다."""
    for p in tr.iterancestors():
        if p is table:
            return True
        if _tag(p) == "TABLE":
            return False
    return False


def _table_text(table) -> str:
    """표를 행마다 줄바꿈해서 낸다.

    한 줄로 이어 붙이면 나중에 조각을 나눌 때 행 경계를 못 지킨다.
    정관 변경 대조표 하나가 90만 자짜리 한 줄이 되는데, 그것을 글자 수로
    자르면 머리글과 내용이 갈려 무엇의 값인지 알 수 없게 된다.

    중첩된 안쪽 표의 행은 건너뛴다. 바깥 셀을 읽을 때 itertext() 가 안쪽
    표 전체를 이미 담기 때문이다. 두 번 담으면 그만큼 글자가 부풀고,
    검색에서 같은 내용이 중복으로 잡힌다. NC 2025년 3분기보고서에
    중첩 표가 57개 있었고 29,272자(14.9%)가 늘어나 있었다.
    """
    rows = []
    for tr in table.iter():
        if _tag(tr) != "TR" or not _own_row(tr, table):
            continue
        cs = _cells(tr)
        if cs:
            rows.append(CELL_SEP.join(cs))
    if rows:
        return "\n".join(rows)
    return _flat("".join(table.itertext()))


def parse(raw: str):
    """문서 하나를 조각 목록으로 낸다.

    각 조각은 TITLE 하나와 그 뒤 내용이다. 다음 TITLE 을 만나면 끊는다.
    문서 맨 앞의 제목 없는 부분도 한 조각으로 담는다. 표지와 목차가 거기 있다.
    """
    root = etree.fromstring(raw.encode("utf-8"), parser=_P)
    _mark_broken(root)
    out = []
    cur = {"title": "", "aclass": None, "atocid": None,
           "path": "", "level": "head",
           "parts": [], "n_table": 0, "text_len": 0, "table_len": 0}
    # 중분류가 두 겹인 곳이 있다. III. 재무에 관한 사항 아래
    # "3. 연결재무제표 주석" 이 오고 그 아래 다시 "16. 우발부채" 가 온다.
    # 한 변수로 덮어쓰면 경로가 III/16 이 되어 어느 절 아래인지 잃는다.
    # ACLASS 가 붙은 조각은 주석이므로 그 앞의 중분류를 부모로 남긴다.
    major = minor = sub = None

    def flush():
        if cur["parts"] or cur["title"]:
            body = "\n".join(p for p in cur["parts"] if p)
            # char_len 은 본문 길이 그대로다. text_len + table_len 으로 두면
            # 조각을 잇는 줄바꿈이 빠져 length(text) 와 어긋난다. 실측에서
            # section 67,300건이 그랬고 합계 1,406,808자 차이가 났다.
            # 그 값을 기준으로 chunk 보존을 검사하면 멀쩡한 것이 실패로 잡힌다.
            out.append({**cur, "text": body, "char_len": len(body)})

    for e in root.iter():
        t = _tag(e)
        if t == "TITLE":
            flush()
            txt = _flat("".join(e.itertext()))
            # 상위가 TABLE-GROUP 이면 그 ACLASS 가 이 조각의 정체다
            aclass = None
            for p in e.iterancestors():
                if _tag(p) == "TABLE-GROUP":
                    a = p.get("ACLASS") or ""
                    if "XBRL" in a:
                        aclass = a.replace("{XBRL}", "").strip()
                    break
            m, n = _MAJOR.match(txt), _MINOR.match(txt)
            if m:
                major, minor, sub = m.group(1), None, None
                level, path = "major", major
            elif n and aclass and minor:
                # 주석 항목. 앞의 중분류가 부모다
                sub = n.group(1)
                level = "group"
                path = f"{major}/{minor}/{sub}"
            elif n:
                minor, sub = n.group(1), None
                level = "minor"
                path = f"{major}/{minor}" if major else minor
            else:
                level = "group"
                path = "/".join(x for x in (major, minor, sub) if x)
            cur = {"title": txt, "aclass": aclass,
                   "atocid": e.get("ATOCID"),
                   "path": path, "level": level,
                   "parts": [], "n_table": 0, "text_len": 0, "table_len": 0}
            continue
        if _in_table(e):
            continue
        if t == "TABLE":
            if e.get(_MARK) is not None:
                # 깨진 표다. 통째로 삼키지 않고 안쪽 구조를 따라 들어간다.
                # 그 안의 TITLE 이 절 경계 노릇을 하고, 표는 그 아래에서
                # 다시 잡힌다.
                continue
            body = _table_text(e)
            cur["parts"].append(body)
            cur["n_table"] += 1
            cur["table_len"] += len(body)
            continue
        if e.text and e.text.strip():
            s = _flat(e.text)
            cur["parts"].append(s)
            cur["text_len"] += len(s)
        if e.tail and e.tail.strip() and not _in_table(e):
            s = _flat(e.tail)
            cur["parts"].append(s)
            cur["text_len"] += len(s)
    flush()
    return out
