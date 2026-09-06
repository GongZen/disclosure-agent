"""수시공시·거래소공시·지분공시를 표에서 직접 조회한다.

왜 필요한가. 2026-09-06 자체 평가지 28문항 실측에서 틀린 11건 중 9건이 같은
원인이었다. 이 세 종류가 검색에 아예 없다.

    문서 종류        문서 수    검색 조각 수
    정기공시          1,054     157,703
    감사보고서           415      13,861
    거래소공시         1,469           0
    지분공시          1,083           0
    주요사항보고서        598           0

문서 4,619건 중 3,150건(68%)이 본문 조각으로 안 쪼개져 있다. 그래서 검색이
못 본다. 대신 앱은 사업보고서의 `XI/1 공시내용 진행 및 변경사항` 을 근거로
답하는데, 그 절은 "그 해에 어떤 공시를 냈다" 는 목록이라 금액이 없다.

    질의   SK하이닉스가 2024년 상반기에 공시한 신규시설투자 내용을 확인해줘
    모범   투자금액 5조 2,962억원 · 자기자본 대비 9.90% · 청주 M15X 건설
    앱     "'신규 시설 투자 등(2024.04.24)' 보고서를 참조하면 됩니다"

그런데 내용이 없는 것이 아니다. 정형 표로는 이미 뽑아 두었다.

    event_contract    1,169행   공급계약 체결·해지·시설투자
    event_major         598행   유상증자·전환사채·자기주식·합병
    event_holding     1,083행   대량보유상황

본문을 새로 쪼개는 것보다 이 표를 조회하는 쪽이 빠르고 정확하다. 계약금액·
투자금액·지분율은 표에 숫자 그대로 있다. `facts.py` 가 재무 수치에 대해
하는 일을 이 파일이 수시·지분 공시에 대해 한다.

연도는 접수일로 읽는다. `DECISIONS.md` 2026-08-31 에서 정기공시는 회계연도로,
수시공시는 접수일로 읽기로 했다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["find_kinds", "lookup", "as_context", "Event"]


# ── 어떤 공시를 물었는지 알아본다 ──────────────────────────────────
#
# 낱말 하나로 판정하지 않는다. "계약" 은 임원 계약·리스 계약에도 쓰이고
# "투자" 는 투자부동산·지분투자에도 쓰인다. 공시 이름에 가까운 말만 본다.
#
# 값은 (표, 걸러낼 조건) 이다. 조건은 lookup 이 SQL 로 옮긴다.
TRIGGER: list[tuple[tuple[str, ...], str, str]] = [
    # 거래소공시 — 단일판매·공급계약
    (("공급계약", "판매계약", "수주", "단일판매", "납품계약"), "contract", "contract"),
    (("계약해지", "계약 해지", "해지된 계약", "해지 내역", "공급계약해지"),
     "contract", "termination"),
    (("신규시설투자", "신규 시설투자", "신규 시설 투자", "시설증설", "설비투자",
      "시설 투자", "공장 신설", "공장 신축"), "contract", "investment"),
    # 주요사항보고서
    (("유상증자", "무상증자", "증자결정"), "major", "증자"),
    (("전환사채", "신주인수권부사채", "교환사채", "영구채", "조건부자본증권",
      "사채발행", "자금조달"), "major", "사채"),
    (("자기주식", "자사주"), "major", "자기주식"),
    (("감자", "감자결정"), "major", "감자"),
    (("합병", "분할", "영업양수", "영업양도", "주식교환"), "major", "구조"),
    # 지분공시
    (("대량보유", "5%", "지분 변동", "지분율", "보유상황보고", "주식등의대량보유"),
     "holding", "holding"),
]


def find_kinds(text: str) -> list[tuple[str, str]]:
    """질의에서 조회할 공시 종류를 고른다. (표, 조건) 목록."""
    out = []
    for words, table, cond in TRIGGER:
        if any(w in text for w in words) and (table, cond) not in out:
            out.append((table, cond))
    # 해지를 물었으면 체결은 뺀다.
    #
    # "공급계약 해지 내역" 에는 "공급계약" 이 들어 있어 체결도 함께 걸린다.
    # 그러면 체결 공시가 자리를 다 먹어 해지가 안 실린다. 실측에서 문항 9가
    # 해지를 물었는데 체결 목록을 답했다.
    if ("contract", "termination") in out:
        out = [x for x in out if x != ("contract", "contract")]
    return out


def find_years(text: str) -> list[int]:
    """연도. 접수일 기준으로 거를 때 쓴다."""
    ys = {int(m) for m in re.findall(r"(?<!\d)(20\d\d)(?!\d)", text)}
    for m in re.findall(r"(?<!\d)(\d\d)년", text):
        if 20 <= int(m) <= 30:
            ys.add(2000 + int(m))
    return sorted(ys)


def find_dates(text: str) -> list[str]:
    """날짜를 하루 단위로 짚었으면 그것도 뽑는다. YYYYMMDD.

    질의가 "2025년 7월 28일 공시한" 처럼 날짜를 못박는 경우가 있다. 그러면
    그날 접수된 공시만 보는 것이 정확하다. 연도로만 거르면 같은 해의 다른
    공시가 앞자리를 차지한다. 실측에서 문항 4가 그랬다.
    """
    out = []
    for y, m, d in re.findall(r"(20\d\d)[.\-년/]\s*(\d{1,2})[.\-월/]\s*(\d{1,2})", text):
        out.append(f"{y}{int(m):02d}{int(d):02d}")
    return sorted(set(out))


# ── 조회 결과 ──────────────────────────────────────────────────────
@dataclass
class Event:
    """공시 하나. 표에서 꺼낸 값과 그 출처를 함께 들고 다닌다."""
    corp: str
    report: str
    rcept: str
    kind: str
    fields: list[tuple[str, str]] = field(default_factory=list)

    def source(self) -> str:
        d = (f"{self.rcept[:4]}-{self.rcept[4:6]}-{self.rcept[6:8]}"
             if len(self.rcept or "") == 8 else "")
        return " · ".join(x for x in (self.corp, self.report,
                                      f"접수 {d}" if d else "") if x)

    def line(self) -> str:
        return " · ".join(f"{k} {v}" for k, v in self.fields if v)


def han(v: int) -> str:
    """조·억 단위 표기를 함께 만든다. 22764764160000 → 22조 7,647억"""
    n = abs(int(v))
    jo, rest = divmod(n, 10 ** 12)
    eok = rest // 10 ** 8
    if jo and eok:
        return f"{jo:,}조 {eok:,}억"
    if jo:
        return f"{jo:,}조"
    if eok:
        return f"{eok:,}억"
    man = n // 10 ** 4
    return f"{man:,}만" if man else f"{n:,}"


def _won(v) -> str:
    """숫자와 조·억 표기를 함께 준다.

    모델이 원 단위를 억·십억으로 옮기다 자릿수를 틀린다. 실측에서
    3,921,711,000,000원을 "3,921억 7,110만원" 으로, 4,965,798,552,980원을
    "4,965,798십억 원" 으로 적었다. 우리가 미리 환산해 주면 모델이 옮겨
    적기만 하면 된다. 계산을 모델에게 시키지 않는다.
    """
    if v in (None, "", 0):
        return ""
    return f"{int(v):,}원 ({han(v)}원)"


def _date(v) -> str:
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def _pct(v) -> str:
    return f"{float(v):g}%" if v not in (None, "") else ""


def _contract(r) -> list[tuple[str, str]]:
    base = {"revenue": "최근매출액", "equity": "자기자본",
            "asset": "자산총액"}.get(r["base_kind"] or "", "기준금액")
    out = [
        ("구분", {"contract": "체결", "termination": "해지",
                  "investment": "투자"}.get(r["event_type"], r["event_type"] or "")),
        ("계약명", r["title"] or ""),
        ("유형", r["category"] or ""),
        ("상대", r["counterparty"] or ""),
        ("금액", _won(r["amount_krw"])),
        (base, _won(r["base_amount"])),
        ("대비", _pct(r["ratio_stated"])),
        ("기간", " ~ ".join(x for x in (_date(r["start_date"]),
                                        _date(r["end_date"])) if x)),
        ("계약일", _date(r["signed_at"])),
        ("목적", (r["purpose"] or "")[:60]),
        ("해지사유", (r["terminate_reason"] or "")[:60]),
    ]
    if r["hold_until"]:
        out.append(("공시유보", f"{_date(r['hold_until'])}까지"
                              f"({r['hold_reason'] or '사유 미기재'})"))
    return out


def _major(r) -> list[tuple[str, str]]:
    use = [(k, r[c]) for k, c in (("시설자금", "use_facility"),
                                  ("영업양수자금", "use_business"),
                                  ("운영자금", "use_operation"),
                                  ("채무상환자금", "use_debt"),
                                  ("타법인증권취득자금", "use_acquire"),
                                  ("기타자금", "use_other")) if r[c]]
    out = [
        ("종류", r["major_kind"] or ""),
        ("결의일", _date(r["decided_at"])),
        ("금액", _won(r["amount_krw"])),
        ("산출", r["amount_src"] or ""),
        ("신주(보통주)", f"{int(r['shares_common']):,}주" if r["shares_common"] else ""),
        ("발행가", _won(r["price_share"])),
        ("발행전주식수", f"{int(r['shares_before']):,}주" if r["shares_before"] else ""),
        ("기간", " ~ ".join(x for x in (_date(r["start_date"]),
                                        _date(r["end_date"])) if x)),
        ("처분목적", (r["disposal_purpose"] or "")[:50]),
    ]
    if use:
        out.append(("자금용도", " / ".join(f"{k} {int(v):,}원" for k, v in use)))
    if r["is_correction"]:
        out.append(("정정", (r["correct_reason"] or "정정공시")[:50]))
    return out


def _holding(r) -> list[tuple[str, str]]:
    return [
        ("보고자", r["holder_name"] or ""),
        ("보고구분", r["report_type"] or ""),
        ("사유", (r["report_reason"] or "").replace("\n", " ")[:70]),
        ("보유목적", r["purpose"] or ""),
        ("직전", (f"{int(r['prev_shares']):,}주 {_pct(r['prev_ratio'])}"
                  if r["prev_shares"] else "")),
        ("이번", (f"{int(r['curr_shares']):,}주 {_pct(r['curr_ratio'])}"
                  if r["curr_shares"] else "")),
        ("발행주식총수", f"{int(r['total_shares']):,}주" if r["total_shares"] else ""),
        ("기준일", _date(r["base_date"])),
    ]


SPEC = {
    "contract": ("event_contract", _contract),
    "major": ("event_major", _major),
    "holding": ("event_holding", _holding),
}

# 조건을 SQL 로 옮긴다. major 는 공시 이름으로 거른다.
WHERE = {
    "contract": "e.event_type = 'contract'",
    "termination": "e.event_type = 'termination'",
    "investment": "e.event_type = 'investment'",
    "holding": "1=1",
    # 증자와 감자를 가른다. 한데 묶으면 "유상증자를 공시했나" 에 감자결정이
    # 걸려 엉뚱한 답이 나온다. 실측에서 자체제작 6이 그랬다.
    "증자": "e.major_kind LIKE '%유상증자%' OR e.major_kind LIKE '%무상증자%'",
    "감자": "e.major_kind LIKE '%감자결정%'",
    "사채": "e.major_kind LIKE '%사채%' OR e.major_kind LIKE '%자본증권%'",
    "자기주식": "e.major_kind LIKE '자기주식%' OR e.major_kind LIKE '자기전환사채%'",
    "구조": ("e.major_kind LIKE '%합병%' OR e.major_kind LIKE '%분할%'"
             " OR e.major_kind LIKE '%영업양%' OR e.major_kind LIKE '%주식교환%'"
             " OR e.major_kind LIKE '%양수결정' OR e.major_kind LIKE '%양도결정'"),
}


def lookup(corp: str, kinds: list[tuple[str, str]], years: list[int] | None = None,
           limit: int = 6, dates: list[str] | None = None) -> list[Event]:
    """기업 하나에 대해 해당하는 공시를 표에서 꺼낸다.

    연도를 주면 접수일로 거른다. 안 주면 최근 것부터 준다. 정정공시가 있으면
    같은 사안이 두 번 나올 수 있는데, 그것도 사실이므로 지우지 않는다.
    최초와 정정을 나란히 보여야 답할 수 있는 질의가 있다(문항 11).
    """
    from db import connect
    con = connect()
    out: list[Event] = []
    for table, cond in kinds:
        tbl, fmt = SPEC[table]
        w = [f"({WHERE[cond]})", "d.corp_name = ?"]
        args: list = [corp]
        if years:
            w.append("(" + " OR ".join("d.rcept_dt LIKE ?" for _ in years) + ")")
            args += [f"{y}%" for y in years]
        # 날짜를 못박았으면 그날 접수분을 먼저 준다. 정정공시가 며칠 뒤에
        # 나오므로 그 뒤 한 주까지 함께 본다. 최초와 정정을 같이 봐야
        # "최종적으로 어떤 조건으로 확정되었나" 를 답할 수 있다.
        order = "d.rcept_dt DESC"
        if dates:
            w.append("(" + " OR ".join("d.rcept_dt >= ?" for _ in dates) + ")")
            args += list(dates)
            order = "d.rcept_dt ASC"
        sql = (f"""SELECT e.*, d.corp_name, d.report_nm, d.rcept_dt
                   FROM {tbl} e JOIN document d ON e.doc_id = d.doc_id
                   WHERE {' AND '.join(w)}
                   ORDER BY {order} LIMIT ?""")
        rows = con.execute(sql, args + [limit]).fetchall()

        # 연도로 걸러 아무것도 안 나오면 연도를 풀고 다시 본다.
        #
        # 사건이 일어난 해와 공시가 나온 해가 다른 경우가 있다. "2024년에
        # 체결한 공급계약 중 이후 해지된 계약" 이 그렇다. 체결은 2024년이고
        # 해지 공시는 2025년이다. 연도를 그대로 걸면 해지 공시가 빠져
        # "확인되지 않는다" 는 답이 나온다. 실측에서 문항 15가 그랬다.
        if not rows and years:
            w2 = [f"({WHERE[cond]})", "d.corp_name = ?"]
            rows = con.execute(
                f"""SELECT e.*, d.corp_name, d.report_nm, d.rcept_dt
                    FROM {tbl} e JOIN document d ON e.doc_id = d.doc_id
                    WHERE {' AND '.join(w2)}
                    ORDER BY d.rcept_dt DESC LIMIT ?""",
                [corp, limit]).fetchall()
        for r in rows:
            out.append(Event(corp=r["corp_name"] or corp,
                             report=r["report_nm"] or "",
                             rcept=str(r["rcept_dt"] or ""),
                             kind=table, fields=fmt(r)))
    return out


def as_context(events: list[Event]) -> str:
    """근거 글의 맨 앞에 넣을 형태로 만든다.

    `[공시 N]` 으로 시작한다. 본문에서 온 `[근거 N]`, 재무제표 표에서 온
    `[값 N]` 과 구분하기 위해서다. 모델이 출처를 그대로 옮겨 적게 한다.
    """
    return "\n\n".join(f"[공시 {i}] {e.source()}\n{e.line()}"
                       for i, e in enumerate(events, 1))
