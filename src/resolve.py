"""S2 대상 확정 · S3 문서 선별.

질의에 나온 기업명을 실제 기업으로 맞추고, 4,204건에서 대상 문서만 남긴다.

사람은 통용명으로 묻고 데이터는 법인명으로 되어 있다. 현대차와 현대자동차,
KT와 케이티가 서로 다른 문자열이다. 여기서 못 맞추면 문서를 하나도 못 찾는다.

그리고 코퍼스는 70개사뿐이다. 그 밖의 기업을 물으면 "대상에 포함되지 않음"
으로 답해야 하고, 이건 데이터가 없는 것과 다른 사유다. DECISIONS 2026-08-14
의 네 갈래 중 out_of_scope 다.

별칭 표를 따로 두지 않는다. company 테이블에 이미 네 가지 이름이 있고
(corp_name · listed_name · corp_eng_name · stock_code) 전부 제공 데이터
안의 값이다. 우리가 만든 별칭은 근거가 없다.
"""
from __future__ import annotations

import difflib
import re

# 기업명에서 떼도 되는 꼬리. "삼성전자㈜" 와 "삼성전자" 는 같은 회사다.
_SUFFIX = re.compile(r"(주식회사|㈜|\(주\)|Co\.?,?\s*Ltd\.?|Inc\.?|Corp\.?)", re.I)

# 한글로 읽은 영문 사명. 문자 비교로는 안 걸리는 것들이다.
# "포스코" 와 "POSCO홀딩스", "네이버" 와 "NAVER" 는 같은 회사인데
# 글자가 하나도 겹치지 않는다. 데이터에 없는 정보라 여기에 적는다.
# 별칭을 남발하지 않는다. 영문 사명을 한글로 읽는 경우로 한정한다.
_READING = {
    "포스코": "POSCO",
    "네이버": "NAVER",
    "에스케이": "SK",
    "엘지": "LG",
    "케이비": "KB",
    "에이치디": "HD",
    "에이치엠엠": "HMM",
    "오씨아이": "OCI",
    "엘아이지": "LIG",
    "지에스": "GS",
    "씨제이": "CJ",
    "제이와이피": "JYP",
    "엔씨": "NC",
}


def norm(s: str) -> str:
    """비교용으로 다듬는다. 공백·기호·법인격 표시를 뺀다."""
    s = _SUFFIX.sub("", s or "")
    return re.sub(r"[\s\.\,\-·()]", "", s).lower()


def variants(s: str) -> list[str]:
    """한글 독음을 영문으로 바꾼 형태도 함께 낸다."""
    base = norm(s)
    out = [base]
    for ko, en in _READING.items():
        if ko in base:
            out.append(base.replace(ko, en.lower()))
    return out


class Resolver:
    """기업명을 corp_code 로 바꾼다."""

    def __init__(self, con):
        self.con = con
        self.rows = con.execute(
            """SELECT corp_code, corp_name, listed_name, corp_eng_name,
                      stock_code, sector, market
               FROM company""").fetchall()
        # 정규화한 이름 → corp_code. 한 회사가 여러 이름을 갖는다
        self.index: dict[str, str] = {}
        for r in self.rows:
            for key in ("corp_name", "listed_name", "corp_eng_name", "stock_code"):
                v = r[key]
                if v:
                    self.index.setdefault(norm(v), r["corp_code"])
        self.by_code = {r["corp_code"]: r for r in self.rows}

    def resolve(self, name: str) -> dict:
        """하나를 맞춘다.

        결과는 셋 중 하나다.
            exact     정확히 맞았다
            fuzzy     비슷한 것을 찾았다. 후보를 함께 낸다
            not_found 못 찾았다. out_of_scope 로 답해야 한다
        """
        qs = variants(name)
        q = qs[0]
        if not q:
            return {"status": "not_found", "query": name, "candidates": []}
        # 1  정확히 맞는가. 한글 독음 변형도 함께 본다
        for v in qs:
            if v in self.index:
                code = self.index[v]
                return {"status": "exact", "query": name, "corp_code": code,
                        "corp_name": self.by_code[code]["corp_name"]}
        # 2  부분 문자열. "현대차" 가 "현대자동차" 에 들어 있다
        #    다만 질의가 데이터 이름보다 길면 다른 회사다.
        #    "카카오뱅크" 가 "카카오" 에 걸리면 안 된다. 카카오뱅크는
        #    코퍼스에 없고, 없는 것을 있다고 답하면 지표 7 에서 깎인다.
        subs = []
        for v in qs:
            subs += [c for k, c in self.index.items() if v in k]
        if len(set(subs)) == 1:
            code = subs[0]
            return {"status": "exact", "query": name, "corp_code": code,
                    "corp_name": self.by_code[code]["corp_name"]}
        # 3  비슷한 것. 오타나 줄임말
        near = difflib.get_close_matches(q, list(self.index), n=5, cutoff=0.75)
        codes = []
        for k in list(dict.fromkeys(subs)) + [self.index[x] for x in near]:
            c = k if k in self.by_code else self.index.get(k, k)
            if c in self.by_code and c not in codes:
                codes.append(c)
        if codes:
            return {"status": "fuzzy", "query": name,
                    "candidates": [{"corp_code": c,
                                    "corp_name": self.by_code[c]["corp_name"]}
                                   for c in codes[:5]]}
        return {"status": "not_found", "query": name, "candidates": []}

    def resolve_many(self, names: list[str]) -> list[dict]:
        return [self.resolve(n) for n in names]


# ── S3 문서 선별 ─────────────────────────────────────────────────────
# 4,204건에서 대상만 남긴다. 이 단계가 검색 품질을 좌우한다.
# W6 임베딩 비교에서 확인했다. 필터 없이 전체에서 찾으면 다른 기업 조각이
# 섞여 검색 방식의 문제가 모델 차이로 보인다.

# 질의에 나오는 말 → doc_subtype
_SUBTYPE = {
    "사업보고서": "annual", "연간": "annual", "연차": "annual",
    "반기보고서": "half", "반기": "half",
    "분기보고서": "quarter", "분기": "quarter",
}
# 질의에 나오는 말 → doc_group
_GROUP = {
    "정기공시": "periodic", "사업보고서": "periodic", "반기보고서": "periodic",
    "분기보고서": "periodic",
    "주요사항": "major", "주요사항보고서": "major",
    "계약": "exchange", "공급계약": "exchange", "수주": "exchange",
    "지분": "holding", "대량보유": "holding",
}


def pick_docs(con, corp_codes: list[str] | None = None,
              years: list[int] | None = None,
              months: list[int] | None = None,
              group: str | None = None,
              subtype: str | None = None,
              exclude_superseded: bool = True) -> list:
    """조건에 맞는 문서를 낸다.

    정정된 원본은 기본으로 뺀다. 정정본이 있으면 그것이 최신 값이고
    원본 값은 이미 고쳐진 값이다. DECISIONS 2026-08-18 참조.
    """
    sql = ["""SELECT doc_id, corp_code, corp_name, doc_group, doc_subtype,
                     report_nm, rcept_dt, base_year, base_month, is_correction
              FROM document WHERE 1=1"""]
    args: list = []
    if corp_codes:
        sql.append(f"AND corp_code IN ({','.join('?' * len(corp_codes))})")
        args += corp_codes
    if years:
        sql.append(f"AND base_year IN ({','.join('?' * len(years))})")
        args += [str(y) for y in years]
    if months:
        sql.append(f"AND base_month IN ({','.join('?' * len(months))})")
        args += [str(m) for m in months]
    if group:
        sql.append("AND doc_group = ?")
        args.append(group)
    if subtype:
        sql.append("AND doc_subtype = ?")
        args.append(subtype)
    if exclude_superseded:
        sql.append("""AND doc_id NOT IN
                      (SELECT to_doc_id FROM doc_relation WHERE to_doc_id IS NOT NULL)""")
    sql.append("ORDER BY rcept_dt DESC")
    rows = con.execute(" ".join(sql), args).fetchall()
    if exclude_superseded:
        rows = _latest_only(rows)
    return rows


def _latest_only(rows: list) -> list:
    """같은 보고서의 정정본이 여럿이면 마지막 것만 남긴다.

    doc_relation 은 정정본이 지목한 원본만 잇는다. 정정본 서식이 최초
    제출일을 적게 되어 있어 세 번 정정하면 셋 다 원본을 가리킨다. 그래서
    원본 하나만 걸러지고 정정본은 다 남는다. KB금융 2025년 사업보고서가
    2026-03-24 와 2026-06-19 두 번 정정돼 둘 다 나왔다.

    정기공시만 적용한다. 계약·주요사항 공시는 같은 (연도, 월) 에 서로 다른
    사건이 여럿 있을 수 있어 하나로 줄이면 안 된다.
    """
    out, seen = [], {}
    for r in rows:
        if r["doc_group"] != "periodic":
            out.append(r)
            continue
        key = (r["corp_code"], r["base_year"], r["base_month"], r["doc_subtype"])
        cur = seen.get(key)
        if cur is None or r["rcept_dt"] > cur["rcept_dt"]:
            seen[key] = r
    out += list(seen.values())
    out.sort(key=lambda r: r["rcept_dt"], reverse=True)
    return out


def parse_period(text: str) -> dict:
    """질의에서 기간을 읽는다. 연도와 보고서 종류.

    "2025년 1분기" · "2025년 사업보고서" · "2023년과 2025년" 같은 표현을 본다.
    S1 이 HCX 로 구조화한 결과가 우선이고 이것은 보조다.
    """
    out: dict = {"years": [], "subtype": None, "group": None, "months": []}
    out["years"] = [int(y) for y in re.findall(r"(20\d\d)\s*년?", text)]
    for k, v in _SUBTYPE.items():
        if k in text:
            out["subtype"] = v
            break
    for k, v in _GROUP.items():
        if k in text:
            out["group"] = v
            break
    m = re.search(r"([1-4])\s*분기", text)
    if m:
        out["months"] = [{1: 3, 2: 6, 3: 9, 4: 12}[int(m.group(1))]]
        out["subtype"] = "half" if m.group(1) == "2" else "quarter"
    if "반기" in text:
        out["months"] = [6]
        out["subtype"] = "half"
    return out
