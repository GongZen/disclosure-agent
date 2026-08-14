# -*- coding: utf-8 -*-
"""정정공시·계약해지 공시에서 원본을 가리키는 단서를 뽑는다.

DART 정정공시는 원본을 접수번호로 지목하지 않는다. 정정 문서 1,002건을 조사한
결과 자기 접수번호 외에 14자리 접수번호가 나온 문서는 1건(0.1%)뿐이었다.
날짜와 서류명으로만 지목하므로 그것을 파싱한다.

문서 유형에 따라 표기가 다르다.

    정기·주요사항·지분   1. 정정대상 공시서류 : 제51기 사업보고서
                        2. 정정대상 공시서류의 최초제출일 : 2024년 03월 12일

    거래소              1. 정정관련 공시서류        단일판매·공급계약 체결
                        2. 정정관련 공시서류제출일   2025-10-30

    계약 해지           ※ 관련공시 2024-10-15 단일판매ㆍ공급계약체결
"""

from __future__ import annotations

import re

__all__ = ["count_matches", "parse_contract_date", "parse_contract_fields",
           "parse_corrected_fields", "parse_correction_target", "parse_prior_values",
           "parse_termination_target", "pick_by_fields"]

# 날짜 표기가 세 가지로 섞여 있다. 같은 필드 안에서도 문서마다 다르다.
#   2024년 03월 12일    한글
#   2023-01-27          하이픈
#   2022.06.28          점
# 구분자가 뒤섞인 원본 오타도 받는다. 실제로 "2025년 08년 28일" 이 있었다.
_DATE = r"(\d{4})\s*[-.년월]\s*(\d{1,2})\s*[-.년월]\s*(\d{1,2})\s*일?"

# 정기·주요사항·지분
_RE_ORIG_DATE = re.compile(r"정정대상\s*공시서류의?\s*최초제출일\s*[:：]?\s*" + _DATE)
# 거래소
_RE_ORIG_DATE_EX = re.compile(r"정정관련\s*공시서류\s*제출일\s*[:：]?\s*\|?\s*" + _DATE)

# 보조 단서 — 원본 서류명
_RE_HINT_KO = re.compile(r"정정대상\s*공시서류\s*[:：]\s*([^\n|]{2,40}?)\s*(?:\||2\.)")
_RE_HINT_EX = re.compile(r"정정관련\s*공시서류\s*\|?\s*([가-힣ㆍ·\s()]{2,30}?)\s*\|?\s*2\.")

# 계약 해지 — "관련공시 2024-10-15 단일판매ㆍ공급계약체결"
_RE_TERM = re.compile(r"관련공시\s*\|?\s*" + _DATE + r"\s*\|?\s*([^\n|]{0,40})")
# 본문 서술 — "본 건은 2024년 10월 15일 공시한 ..."
_RE_TERM_KO = re.compile(_DATE + r"\s*공시한")


def _ymd(m: re.Match) -> str | None:
    """YYYYMMDD 로 정규화한다. 월·일이 범위를 벗어나면 파싱 오류이므로 버린다."""
    y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (2000 <= y <= 2030 and 1 <= mo <= 12 and 1 <= dd <= 31):
        return None
    return f"{y}{mo:02d}{dd:02d}"


def parse_correction_target(text: str) -> tuple[str | None, str | None]:
    """정정공시 본문에서 (원본 제출일 YYYYMMDD, 원본 서류명) 을 뽑는다.

    실패하면 (None, None). 정정 헤더는 문서 앞부분에 있으므로 전문을 볼 필요가 없다.
    """
    head = text[:60_000]

    for pat, hint_pat in ((_RE_ORIG_DATE, _RE_HINT_KO), (_RE_ORIG_DATE_EX, _RE_HINT_EX)):
        m = pat.search(head)
        if m:
            d = _ymd(m)
            if d:
                h = hint_pat.search(head)
                return d, (h.group(1).strip() if h else None)

    return None, None


_RE_ORDER_DATE = re.compile(r"계약\s*\(?\s*수주\s*\)?\s*일자\s*\|?\s*" + _DATE)
_RE_START_DATE = re.compile(r"시작일\s*\|?\s*" + _DATE)


def parse_contract_date(text: str) -> str | None:
    """계약 공시에서 그 계약이 언제 체결됐는지를 뽑는다. YYYYMMDD 또는 None.

    되짚기가 원본에 닿지 못했을 때 사유를 가리는 데 쓴다. 계약 체결 공시는
    체결 시점에 내는 것이므로, 수주일자가 코퍼스 수집 시작 전이면 원본 공시도
    수집 범위 밖이다. 그러면 미연결 사유는 우리가 못 찾은 것이 아니라
    데이터에 없는 것이다.

    수주일자가 없으면 계약기간 시작일을 쓴다. 둘은 대개 같고, 다르면
    시작일이 더 늦으므로 범위 밖 판정이 보수적으로 나온다.
    """
    head = text[:60_000]
    for pat in (_RE_ORDER_DATE, _RE_START_DATE):
        m = pat.search(head)
        if m:
            d = _ymd(m)
            if d:
                return d
    return None


_RE_HEADER_END = re.compile(r"정\s*정\s*사\s*유|정\s*정\s*사\s*항")
_RE_BIGNUM = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}\b")


def parse_prior_values(text: str, limit: int = 4000) -> set[str]:
    """정정 헤더 블록에서 큰 숫자들을 뽑는다. 후보 원본을 가려내는 지문으로 쓴다.

    같은 날 같은 유형 공시가 여럿일 때 날짜만으로는 원본을 특정할 수 없다.
    정정 전 값은 원본에 그대로 들어 있고 정정 후 값은 어느 원본에도 없으므로,
    헤더의 숫자를 후보와 대조하면 일치 수가 많은 쪽이 원본이다.

    정정 전 열만 정확히 분리하지 않고 헤더 전체를 쓴다. 정정 후 값이 섞여도
    어느 후보와도 매칭되지 않아 판정을 흐리지 않는다.
    """
    m = _RE_HEADER_END.search(text[:limit])
    if not m:
        return set()
    block = text[m.end(): m.end() + limit]
    return {n.replace(",", "") for n in _RE_BIGNUM.findall(block)}


def count_matches(candidate_text: str, prior: set[str]) -> int:
    """후보 원문에 정정 전 값이 몇 개나 들어 있는지 센다."""
    if not prior:
        return 0
    nums = {n.replace(",", "") for n in _RE_BIGNUM.findall(candidate_text[:120_000])}
    return len(prior & nums)


# ── 계약 필드 대조 ────────────────────────────────────────────────
#
#  같은 날 같은 유형 공시가 여럿이면 날짜로는 갈리지 않는다. LG에너지솔루션은
#  Ford 와 두 건의 배터리 공급계약을 같은 날 공시했고 계약명·계약상대·공급지역·
#  수주일자가 전부 같다. 다른 것은 계약기간뿐이다.
#
#      exchange_20241015800258   2027-01-01 ~ 2032-12-31   75GWh
#      exchange_20241015800261   2026-10-01 ~ 2030-12-31   34GWh
#
#  정정본은 자기가 무엇을 고쳤는지 "4. 정정사항"에 적어둔다. 거기 없는 항목은
#  원본과 값이 같아야 하므로, 그 항목들로 후보를 가를 수 있다.

_FIELD_PAT = {
    "체결계약명":  r"체결계약명\s*\|?\s*([^\n|]{1,70})",
    "계약금액":    r"계약금액\(원\)\s*\|?\s*([\d,\-]{1,30})",
    "매출액대비":  r"매출액대비\(?%?\)?\s*\|?\s*([\d.\-]{1,12})",
    "계약상대":    r"3\.\s*계약상대\s*\|?\s*([^\n|]{1,40})",
    "공급지역":    r"4\.\s*판매ㆍ공급지역\s*\|?\s*([^\n|]{1,30})",
    "시작일":      r"시작일\s*\|?\s*(\d{4}-\d{2}-\d{2})",
    "종료일":      r"종료일\s*\|?\s*(\d{4}-\d{2}-\d{2})",
    "수주일자":    r"계약\(수주\)일자\s*\|?\s*(\d{4}-\d{2}-\d{2})",
    "유보기한":    r"유보기한\s*\|?\s*(\d{4}-\d{2}-\d{2})",
    # 신규시설투자 서식. 계약 서식에는 없는 항목이라 서로 방해하지 않는다.
    "투자목적":    r"3\.\s*투자목적\s*\|?\s*([^\n|]{1,60})",
    "투자금액":    r"투자금액\(원\)\s*\|?\s*([\d,\-]{1,30})",
    "결의일":      r"이사회결의일\(결정일\)\s*\|?\s*(\d{4}-\d{2}-\d{2})",
}
_FIELD_RE = {k: re.compile(v) for k, v in _FIELD_PAT.items()}

# 본문 시작 표지. 정정본은 앞쪽에 정정 전후 값을 담은 헤더를 두므로, 본문부터
# 읽지 않으면 정정 전 값을 본문 값으로 잘못 뽑는다.
#
# 거래소 서식마다 첫 항목이 다르다. 계약 서식만 넣어두었더니 신규시설투자와
# 투자판단 서식에서 표지를 못 찾아 문서 전체를 본문으로 취급했다. 그러면
# 정정사항 표까지 본문에 섞여 들어간다.
_BODY_MARKS = ("1. 판매ㆍ공급계약 구분", "1. 투자구분", "1. 제목")

# 정정사항 표가 끝나는 지점. 표 뒤에는 서식 제목이 오고 그다음이 본문이다.
_FORM_TITLES = ("단일판매ㆍ공급계약 체결", "단일판매ㆍ공급계약체결",
                "신규 시설투자 등", "투자판단 관련 주요경영사항")

# 정정사항 블록에서 찾을 표기 → 제외할 필드.
#
# 항목명 표기가 매우 다양하다. 거래소공시 정정본을 전수 조사한 결과다.
#
#     5. 계약기간 - 종료일 / 5. 계약기간 종료일 / 5. 계약기간 / 종료일 / 5.계약기간-종료일
#     2. 계약내역 - 계약금액(원) / 2.계약내역 / '2. 계약내역'의 '계약금액(원)'
#     7.계약(수주)일자
#
# 필드명이 항목명의 부분문자열이 아닌 경우가 있다. "수주일자" 는 "계약(수주)일자" 에
# 들어 있지 않다. 괄호가 사이에 끼어 있기 때문이다. 이걸 놓쳐 두산퓨얼셀 정정본이
# 수주일자를 고쳤다고 명시했는데도 대조에서 빼지 못했다.
#
# 절 이름만 적힌 경우도 받는다. "5. 계약기간" 하나면 시작일과 종료일 둘 다 뺀다.
# 값 칸의 서술에 우연히 섞여도 과잉 제외일 뿐이라 오연결로 이어지지 않는다.
# 덜 거르는 것보다 더 거르는 쪽이 안전하다.
_CORRECTED_MARK = {
    "체결계약명": {"체결계약명"},
    "계약금액": {"계약금액"}, "매출액대비": {"매출액대비"},
    "계약내역": {"계약금액", "매출액대비"},
    "계약상대": {"계약상대"}, "공급지역": {"공급지역"},
    "시작일": {"시작일"}, "종료일": {"종료일"},
    "계약기간": {"시작일", "종료일"},
    "수주)일자": {"수주일자"}, "수주일자": {"수주일자"},
    "유보기한": {"유보기한"}, "공시유보": {"유보기한"},
    "투자목적": {"투자목적"}, "결의일": {"결의일"},
    "투자금액": {"투자금액"}, "투자내역": {"투자금액"},
}


def _body(text: str) -> str:
    """정정 헤더를 건너뛰고 본문만 돌려준다.

    표지 앞으로 물러나지 않는다. 150자 앞에서 시작했더니 정정사항 표의 꼬리가
    딸려 들어왔다. 삼성전자 정정본은 그 자리에 "체결계약명, 계약상대, 주요
    계약조건은 유보기한일의 다음 영업일에 공개될 예정임" 이라는 문장이 있어
    계약명으로 잘못 뽑혔다. 뽑는 항목은 전부 표지 뒤에 있다.
    """
    for mark in _BODY_MARKS:
        i = text.rfind(mark)
        if i > 0:
            return text[i:]
    return text


def parse_contract_fields(text: str) -> dict[str, str]:
    """계약 공시 본문에서 대조용 필드를 뽑는다. 값이 `-` 면 미공개이므로 버린다."""
    b = _body(text)
    out = {}
    for k, rx in _FIELD_RE.items():
        m = rx.search(b)
        if m:
            v = m.group(1).strip()
            if v and v != "-":
                out[k] = v
    return out


def parse_corrected_fields(text: str) -> set[str]:
    """정정본이 고쳤다고 적은 항목의 이름을 돌려준다. 대조에서 제외할 것들이다."""
    i = text.find("4. 정정사항")
    if i < 0:
        return set()
    seg = text[i:]
    end = min((p for m in _BODY_MARKS + _FORM_TITLES if (p := seg.find(m, 20)) > 0),
              default=len(seg))

    # 블록 전체를 뒤지면 값 칸의 서술문에 걸린다. LG에너지솔루션은 "9. 기타 투자판단"
    # 값에 "공급물량 및 계약기간 등의 계약조건은 …" 이라고 적혀 있어 계약기간이
    # 정정된 것으로 오인됐고, 그 결과 후보를 가를 항목이 전부 사라졌다.
    # 항목명은 짧으므로 짧은 칸에서만 찾는다.
    cells = [c.strip() for ln in seg[:end].splitlines() for c in ln.split("|")]
    labels = [c for c in cells if 0 < len(c) <= 40]

    out = set()
    for mark, fields in _CORRECTED_MARK.items():
        if any(mark in c for c in labels):
            out |= fields
    return out


def pick_by_fields(corr_text: str, cand_texts: dict[str, str],
                   min_score: int = 1) -> str | None:
    """정정본과 후보 원본들의 필드를 대조해 하나로 확정한다. 못 가르면 None.

    가르는 항목은 두 조건을 모두 만족해야 한다.
        정정사항 목록에 없다      이 정정본이 바꾸지 않았다
        후보들끼리 값이 다르다     같으면 가르는 데 쓸 수 없다

    그런 항목에서 일치 수를 세고, 가장 많이 일치한 후보가 유일할 때만 확정한다.

    전부 일치를 요구하지 않는 이유는 연쇄 정정 때문이다. 되짚기가 깊으면 중간
    정정본들이 값을 바꿔놓았으므로 원본과 어긋나는 항목이 남는다. 그 항목들은
    이 정정본의 정정사항 목록에 없다.

        삼성E&A  정정본 종료일 2026-03-31
                 후보A 2025-10-31 · 후보B 2027-04-30      어느 쪽과도 다르다
                 그러나 계약명과 시작일은 후보A 와 같다     → 후보A

    동점이거나 아무도 일치하지 않으면 미연결로 둔다. 틀린 이력을 답변에
    내보내는 것보다 낫다.
    """
    if len(cand_texts) < 2:
        return None
    mine = parse_contract_fields(corr_text)
    if not mine:
        return None
    skip = parse_corrected_fields(corr_text)
    cands = {d: parse_contract_fields(t) for d, t in cand_texts.items()}

    score = dict.fromkeys(cands, 0)
    for key, mv in mine.items():
        if key in skip:
            continue
        vals = [c.get(key) for c in cands.values()]
        if len(set(vals)) < 2:
            continue                      # 후보들이 모두 같은 값이면 못 가른다

        # 일부 후보에만 있는 항목도 쓴다. 한미약품은 후보 3건 중 하나만
        # 유보기한을 갖고 있고 그 값이 정정본과 같다. 없는 쪽을 "판단 불가"로
        # 빼면 이 단서를 통째로 버리게 된다. 없으면 다른 값으로 본다.
        for d, c in cands.items():
            score[d] += c.get(key) == mv

    rank = sorted(score.items(), key=lambda kv: -kv[1])
    if rank[0][1] >= min_score and rank[0][1] > rank[1][1]:
        return rank[0][0]
    return None


def parse_termination_target(text: str) -> tuple[str | None, str | None]:
    """계약 해지 공시에서 (원계약 공시일 YYYYMMDD, 단서) 를 뽑는다.

    '관련공시' 필드를 먼저 보고, 없으면 본문의 '~년 ~월 ~일 공시한' 서술을 본다.
    """
    m = _RE_TERM.search(text)
    if m:
        d = _ymd(m)
        if d:
            return d, (m.group(4).strip() or None)

    m = _RE_TERM_KO.search(text)
    if m:
        d = _ymd(m)
        if d:
            return d, None

    return None, None
