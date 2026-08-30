# -*- coding: utf-8 -*-
"""원문 XML 의 이스케이프 누락을 파싱 전에 바로잡는다.

DART 원문에는 세 가지 누락이 있다. 셋 다 파서가 태그로 오인하거나 속성을
못 읽어 구조를 무너뜨린다.

    1  본문 텍스트의 꺾쇠      <배틀그라운드>  <전기말>  <PlayStation 5>
    2  속성 값 안의 따옴표      ENG=""Snow Corporation"
    3  XML 에 없는 엔티티       &reg;  &lsquo;

원문 자체는 멀쩡하다. 1,051건 전수에서 <TABLE> 과 </TABLE> 개수가 전부
맞았다. 이스케이프만 빠져 있고, XML 규칙상 그것이 태그 시작으로 읽힌다.

## 무엇이 일어나는가

가짜 태그가 하나 열리면 그 뒤 닫는 태그가 한 칸씩 밀린다.

    원문     <TABLE><TR><TD>제 <전기말> 기</TD></TR></TABLE>
             <TITLE>II. 사업의 내용</TITLE>

    </TD>    →  <전기말> 을 닫는다
    </TR>    →  <TD> 를 닫는다
    </TABLE> →  <TR> 을 닫는다
                <TABLE> 은 닫히지 못하고 그 뒤 문서 전체를 삼킨다

속성 값 따옴표는 파서가 그 태그를 통째로 버리게 만들어 같은 밀림을 낳는다.
현대자동차 2025년 반기보고서에서 밀림이 DOCUMENT 까지 올라가 본문의 31.9%가
사라졌다.

## 후처리로는 안 된다

파싱 결과를 보고 깨진 표를 걸러내는 방식을 먼저 시도했다. 원인 2 는 파싱
단계에서 태그가 사라진 것이라 결과에 그 내용이 아예 없다. 없는 것은
되살릴 수 없다.

## 결과

    1,051건 전수         고치기 전    고친 뒤
      파서 오류 있는 문서    1,008  →     0
      깨진 표 있는 문서        468  →     0
      글자 손실 0.1% 초과       84  →     0

글자 수가 아니라 문자열 자체를 대조했다. 원문에서 표준 태그만 걷어낸
텍스트와 파싱 결과가 표본 16건에서 한 글자도 다르지 않았다.

자세한 경위는 `docs/feedback/W6.md` 에 있다.
"""
from __future__ import annotations

import re

__all__ = ["sanitize", "STD"]

# DART 정기공시 서식의 태그 33종. 전수 집계로 확정했다.
#
# 원문에 나오는 태그 이름이 1,056종인데 그중 1,023종이 본문 텍스트였다.
# 두 기준으로 갈랐다.
#
#     기준 1   문서 1,051건 전부에 나오는가        31종
#     기준 2   여는 것과 닫는 것 개수가 맞는가      A · CORRECTION 추가
#
# <배틀그라운드> 는 42,002개가 열리고 0개가 닫힌다. A 는 8,662/8,662 다.
# 역방향도 확인했다. STD 밖인데 여닫이가 맞는 태그는 전수에서 0종이다.
#
# 이 목록은 지금 코퍼스에서 관찰된 것이다. 코퍼스가 바뀌면 다시 집계한다.
STD = {
    "DOCUMENT", "DOCUMENT-NAME", "FORMULA-VERSION", "COMPANY-NAME", "SUMMARY",
    "EXTRACTION", "BODY", "COVER", "COVER-TITLE", "LIBRARY", "CORRECTION",
    "SECTION-1", "SECTION-2", "SECTION-3", "TITLE", "P", "SPAN", "TABLE-GROUP",
    "TABLE", "THEAD", "TBODY", "TR", "TD", "TH", "TE", "TU", "COLGROUP", "COL",
    "PGBRK", "IMAGE", "IMG", "IMG-CAPTION", "A",
}

# 태그처럼 생긴 것. 이름이 있어야 걸린다.
_MARKUP = re.compile(r"<(/?)([A-Za-z가-힣0-9][^\s<>/]*)([^<>]*?)(/?)>")
# XML 선언 <?xml ?> 과 주석·DOCTYPE. 이름이 없어 _MARKUP 에 안 걸린다.
_DECL = re.compile(r"<[?!][^<>]*>")
# XML 이 아는 엔티티는 다섯뿐이다. &reg; 는 정의되지 않아 오류가 난다.
_AMP = re.compile(r"&(?!(?:lt|gt|amp|quot|apos|#\d{1,6}|#x[0-9a-fA-F]{1,5});)")
_HTMLENT = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{1,9});")
# 속성. 닫는 따옴표는 그 뒤에 다른 속성이나 태그 끝이 오는 것이다.
_ATTR = re.compile(
    r'([A-Za-z_:][-\w.:]*)\s*=\s*"(.*?)"(?=\s+[A-Za-z_:][-\w.:]*\s*=|\s*$)')
_HOLD = re.compile("\x00(\\d+)\x01")

# XML 밖 엔티티를 뜻하던 문자로 되돌린다. &amp;reg; 로 바꾸면 본문에
# "&reg;" 라는 글자가 남는다. 전수에서 reg 30 · lsquo 4 · rsquo 4 뿐이다.
# 목록에 없는 것은 원문 그대로 두고 &amp; 처리로 넘어간다.
_NAMED = {
    "reg": "®", "lsquo": "‘", "rsquo": "’", "nbsp": " ",
    "copy": "©", "trade": "™", "ldquo": "“", "rdquo": "”",
    "middot": "·", "hellip": "…", "ndash": "–", "mdash": "—",
    "deg": "°", "plusmn": "±", "times": "×", "divide": "÷",
    "sup2": "²", "sup3": "³", "frac12": "½", "bull": "•",
}


def _ent(m) -> str:
    """XML 밖 엔티티를 문자로 되돌린다. XML 다섯은 그대로 둔다."""
    name = m.group(1)
    if name in ("lt", "gt", "amp", "quot", "apos"):
        return m.group(0)
    return _NAMED.get(name) or m.group(0)


def _fix_attrs(s: str) -> str:
    """속성 값 안의 따옴표를 &quot; 로 바꾼다.

    원문에 ENG=""Snow Corporation" 처럼 값 안의 따옴표가 그대로 있다.
    작성자가 뜻한 값은 따옴표까지 포함한 문자열인데 &quot; 로 적지 않았다.

    닫는 따옴표는 그 뒤에 다른 속성이나 태그 끝이 오는 것으로 가린다.
    값 안에 "이름=" 형태가 들어 있으면 이 판정이 어긋날 수 있어 전수로
    세었다. 7개였고 전부 ENG="Others, current=" 형태다. 값이 등호로 끝나는
    정상 속성이라 규칙이 건드리지 않는다.
    """
    if s.count('"') <= 2:
        return s
    return _ATTR.sub(
        lambda m: f'{m.group(1)}="{m.group(2).replace(chr(34), "&quot;")}"', s)


def sanitize(s: str) -> str:
    """마크업으로 인정한 것만 남기고 나머지 꺾쇠를 전부 이스케이프한다.

    표준 태그와 선언을 잠깐 자리표시자로 빼둔 뒤 남은 < 를 모두 &lt; 로
    바꾼다. 정규식만으로 걸러내면 놓치는 형태가 있다. OCI홀딩스 2024년
    반기보고서에 "<VI. 이사회 등 회사의 기관에 관한 사항 -" 이 닫는 꺾쇠
    없이 줄이 끝나 태그 매칭에 걸리지 않았다.

    자리표시자를 쓰지 않는 가벼운 방식을 시도했다가 되돌렸다. 정확성이
    떨어지고(표본 16건 중 11건 불일치) 속도도 더 느렸다.

    비용은 원문 90.6M자에 52.8초다. 파싱(8.2초)보다 6배 무겁다.
    전수 1,051건이면 약 58분이 추가된다.
    """
    s = _AMP.sub("&amp;", _HTMLENT.sub(_ent, s))
    keep: list[str] = []

    def hold(m):
        keep.append(m.group(0))
        return "\x00%d\x01" % (len(keep) - 1)

    s = _DECL.sub(hold, s)

    def rep(m):
        if m.group(2).upper() not in STD:
            return "&lt;" + m.group(0)[1:-1] + "&gt;"
        keep.append(f"<{m.group(1)}{m.group(2)}"
                    f"{_fix_attrs(m.group(3))}{m.group(4)}>")
        return "\x00%d\x01" % (len(keep) - 1)

    s = _MARKUP.sub(rep, s).replace("<", "&lt;")
    return _HOLD.sub(lambda m: keep[int(m.group(1))], s)
