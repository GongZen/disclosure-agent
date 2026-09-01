"""70개사 포괄 절 스키마를 만든다. 절마다 빈번한 낱말을 함께 낸다.

## 왜 pathmap.csv 와 따로 만드는가

`pathmap.csv` 는 경로(path)를 열쇠로 쓴다. 그런데 `III` 재무에 관한 사항에서는
경로가 기업마다 흔들린다. 주석 항목 개수가 달라 번호가 밀리기 때문이다.

    "무형자산" 제목의 절이 64곳에 있는데 경로가 32가지다

그래서 열쇠를 갈아 끼운다. 자세한 경위는 `docs/TOC_SCHEMA.md`.

기존 `pathmap.csv` 는 건드리지 않는다. `src/retrieval.py` 가 지금 그것을 쓰고
있고, 새 스키마로 갈아타기 전까지 성적의 기준이 되어야 하기 때문이다.

## 열쇠를 무엇으로 삼는가

    III        aclass (XBRL 분류 코드).  단 U 코드는 제외한다
    나머지 11개  정규화 제목.  이쪽에는 aclass 가 아예 없다

`_U` 로 시작하는 코드는 쓰지 않는다. 기업이 스스로 만드는 확장 슬롯이라
"사용자 정의 주석 1번" 자리에 회사마다 다른 것을 넣기 때문이다.

    D 코드 404종   코드당 제목 4.2가지 · 주 제목 점유율 74%
    U 코드  38종   코드당 제목 9.0가지 · 주 제목 점유율 32%

    NT_C_U800100  41절인데 제목이 26가지
        기타금융자산 · 당기손익-공정가치측정금융자산 · 보험가입자산 · 비지배지분 …

U 코드로 묶으면 서로 무관한 절이 한 단위가 되어 공통 낱말이 하나도 안 나온다.
실측에서 U 코드 15개가 전부 낱말 0개였다. 그래서 정규화 제목으로 떨어뜨린다.

`aclass` 를 쓰는 이유는 같은 항목의 다른 이름을 묶어 주기 때문이다.

    NT_C_D818000  60곳  ←  특수관계자거래 · 특수관계자 · 특수관계자와의거래

제목만 쓰면 이 넷이 15곳 · 19곳 … 으로 흩어져 전부 문턱 아래로 떨어진다.

실측이다. 5곳 임계 · U 코드 제외 · 빈 절 제외 기준이다.

    제목 정규화만        단위 976가지 · 5곳 이상 143개 · III 본문 커버 67.5%
    태그 우선 (U 제외)   단위 584가지 · 5곳 이상 123개 · III 본문 커버 72.7%

단위가 20개 줄면서 커버리지가 5.2퍼센트포인트 오른다.

## 표에는 전부 담고 작업 대상만 표시한다

5곳 미만인 단위도 행으로 남긴다. 빼 버리면 "왜 없지" 를 나중에 다시 묻게 된다.
`작업대상` 칸에 `O` 가 붙은 것만 지금 채운다. 나머지는 나중에 보강한다.

## 정정본은 최신 한 건만 쓴다

같은 기업이 2025년 사업보고서를 여러 번 낸다. 정정공시 때문이다.

    전체 85건 · 기업 70곳
    3건 낸 곳  효성중공업 · 메리츠금융지주 · 고려아연 · KB금융
    2건 낸 곳  한화오션 · 한전기술 · 크래프톤 · 와이지엔터테인먼트 등 7곳

정정본은 같은 사실을 고쳐 다시 낸 것이라 내용이 거의 같다. 그대로 세면
그 11개 기업의 표현이 2~3배 가중된다. 그래서 `doc_relation` 이 지목한 원본을
빼고 기업·연도·종류마다 접수일이 가장 늦은 것만 남긴다.

`src/build_fs.py` 가 재무 값을 담을 때 쓰는 규칙과 같다.

    문서 85건 → 70건 · 기업 70곳 (1:1)
    대상 절 9,877개 → 8,131개 (17.7% 감소)

## 감사보고서는 뺀다

감사보고서는 2026-08-31 에 별도 문서(`doc_group='audit'`)로 떼어냈는데
`doc_subtype` 을 부모에서 물려받아 `annual` 이다. 그대로 두면 경로 `1`~`4`
(감사대상업무·감사참여자·주요 감사실시내용·커뮤니케이션) 564절이 섞인다.
사업보고서 목차 I~XII 와 다른 체계이므로 `doc_group='periodic'` 으로 거른다.

## 빈 껍데기 절을 걸러야 한다

상위 절과 하위 절의 제목이 같은 경우가 있다.

    I    "I. 회사의 개요"   84절 · 평균 0자    내용이 없는 컨테이너
    I/1  "1. 회사의 개요"   84절 · 내용 있음

정규화하면 둘 다 `회사의개요` 라 한 단위로 합쳐진다. 그러면 절수가 168이 되고
`MIN_COVER 0.7` 이 118절 이상을 요구하는데 내용은 84절에만 있어 최대 50% 다.
낱말이 전멸한다. 실측에서 실제로 그렇게 됐다.

그래서 `char_len >= 300` 으로 빈 절을 먼저 뺀다. `build_pathmap.py` 도 같은
하한을 쓴다. 다만 상한(30,000)은 두지 않는다. 낱말은 본문 앞 4,000자만 보므로
큰 절을 배제할 이유가 없고, 배제하면 주석처럼 큰 절을 통째로 잃는다.

## 사람이 읽을 이름을 옆에 붙인다

`NT_C_D818000` 만 보면 무엇인지 알 수 없다. 그래서 `열쇠` 바로 옆에
`열쇠뜻` 을 둔다. 그 단위에서 가장 많이 쓰인 제목에서 앞 번호만 뗀 것이다.

한 칸에 합치지 않는 이유는 나중에 검색 코드가 `section.aclass` 와 대조할 때
`열쇠` 를 그대로 써야 하기 때문이다. 문자열을 갈라 쓰면 약해진다.

태그가 여러 제목을 묶은 경우가 많으므로 `다른제목` 에 2~4번째로 흔한 이름을
함께 싣는다. 무엇이 한 단위로 묶였는지 보여야 판정이 된다.

`이름가짓수` 는 번호를 뗀 뒤의 가짓수다. 원본 제목 그대로 세면 `13. 무형자산`
과 `15. 무형자산` 이 다른 것으로 세어져 "제목 19가지" 처럼 부풀려진다.
번호는 기업 사정이지 이름의 차이가 아니다.

## 정규화 규칙

    1  앞 번호 제거     "13. 무형자산"   → "무형자산"
    2  공백 전부 제거   "공정가치 측정"   → "공정가치측정"
    3  꼬리표 제거      "(연결)" · "(제조서비스업)" · "(금융업)"

## 낱말 점수

`build_pathmap.py` 와 같은 방식이다. 단위만 경로에서 스키마 열쇠로 바뀐다.

    점수 = (그 단위에 나오는 절 비율) ÷ (전체 절에 나오는 비율)

크면 그 단위에서만 쓰이는 낱말이다. 확률은 방향이 반대다 —
그 낱말이 나온 절 중 이 단위가 몇 %인가. 판정에는 이쪽이 더 가깝다.

## 낱말 칸은 하나다

`낱말:점수:확률` 을 공백으로 이어 붙인다.

    배당성향:40.9:100% 배당수익률:41.2:96% 결산배당:35.4:91%

낱말 목록·점수·확률을 따로 두면 낱말이 세 번 반복된다. 하나로 두면 낱말
하나를 판정할 때 필요한 것이 한자리에 모인다.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from search import tokenize

MIN_CHARS = 300     # 이 아래는 빈 껍데기다. 아래 설명 참조
MIN_CORP = 5        # 이 기업 수 미만이면 작업 대상이 아니다. 표에는 남긴다
MIN_COVER = 0.7     # 그 단위의 70% 이상 절에 나와야 낱말 후보가 된다
MIN_DF = 5          # 전체에서 5개 절 이상에 나와야 한다
MIN_SCORE = 3.0     # 이 점수 아래면 그 단위만의 낱말이 아니다
TOP_N = 20          # 단위당 낱말 상한. pathmap 의 12보다 넉넉히 둔다
HEAD = 4000         # 본문 앞부분만 본다. build_pathmap 과 같다

ORD = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
NUM = re.compile(r"^\s*[\(\[]?\s*[0-9IVXivx]+[\-\.0-9]*\s*[\)\].]?\s*")
TOP = re.compile(r"^(XII|XI|VIII|VII|VI|IV|IX|V|X|III|II|I)(?:/|$)")


def readable(title: str) -> str:
    """사람이 읽을 이름. 앞 번호만 떼고 띄어쓰기는 살린다.

    번호를 떼는 이유는 기업마다 다르기 때문이다. 어떤 회사는 13번이
    무형자산이고 어떤 회사는 15번이다. 번호를 남기면 열쇠의 뜻이 아니라
    한 회사의 사정이 표에 박힌다.
    """
    return re.sub(r"\s+", " ", NUM.sub("", title)).strip()


def norm(title: str) -> str:
    t = NUM.sub("", title)
    t = re.sub(r"\((제조서비스업|금융업)\)", "", t)
    t = re.sub(r"\(연결\)", "", t)
    return re.sub(r"\s+", "", t).strip()


def major(path: str) -> str | None:
    """대분류 로마숫자. I~XII 로 시작하지 않으면 None."""
    m = TOP.match(path)
    return m.group(1) if m else None


def usable_tag(aclass: str | None) -> bool:
    """쓸 수 있는 태그인가. U 코드는 기업별 확장 슬롯이라 뜻이 없다."""
    return bool(aclass) and "_U" not in aclass


def schema_key(row) -> tuple[str, str, str] | None:
    t = major(row["path"])
    if t is None:
        return None
    if t == "III" and usable_tag(row["aclass"]):
        return t, row["aclass"], "tag"
    return t, norm(row["title"]), "title"


def latest_docs(con, year: int, subtype: str) -> set[str]:
    """정정본을 정리한 문서 집합. build_fs.py 와 같은 규칙이다."""
    old = {r[0] for r in con.execute(
        "SELECT DISTINCT to_doc_id FROM doc_relation WHERE to_doc_id IS NOT NULL")}
    docs = con.execute("""
        SELECT doc_id, corp_code, base_year, base_month, doc_subtype, rcept_dt
        FROM document
        WHERE doc_subtype = ? AND base_year = ? AND doc_group = 'periodic'""",
        (subtype, year)).fetchall()
    latest: dict[tuple, object] = {}
    for d in docs:
        if d["doc_id"] in old:
            continue
        k = (d["corp_code"], d["base_year"], d["base_month"], d["doc_subtype"])
        cur = latest.get(k)
        if cur is None or d["rcept_dt"] > cur["rcept_dt"]:
            latest[k] = d
    return {d["doc_id"] for d in latest.values()}


def main(year: int = 2025, subtype: str = "annual") -> int:
    con = connect()
    keep_doc = latest_docs(con, year, subtype)
    q = ",".join("?" * len(keep_doc))
    rows = con.execute(f"""
        SELECT s.path, s.title, s.aclass, s.char_len, s.text, d.corp_name
        FROM section s JOIN document d ON s.doc_id = d.doc_id
        WHERE s.doc_id IN ({q})
          AND s.path <> '' AND s.title <> '' AND s.char_len >= ?""",
        list(keep_doc) + [MIN_CHARS]).fetchall()
    print(f"정정본 정리 후 문서 {len(keep_doc)}건")
    keyed = [(schema_key(r), r) for r in rows]
    skipped = sum(1 for k, _ in keyed if k is None)
    keyed = [(k, r) for k, r in keyed if k]
    print(f"{year}년 {subtype} 정기공시 · 절 {len(rows):,}개"
          f" (대분류 밖 {skipped}개 제외)")

    corp = defaultdict(set)
    paths = defaultdict(Counter)
    titles = defaultdict(Counter)
    chars, nsec, kind = Counter(), Counter(), {}
    for (t, k, kd), r in keyed:
        u = (t, k)
        kind[u] = kd
        corp[u].add(r["corp_name"])
        paths[u][r["path"]] += 1
        titles[u][readable(r["title"])] += 1
        chars[u] += r["char_len"] or 0
        nsec[u] += 1

    target = {u for u, v in corp.items() if len(v) >= MIN_CORP}
    print(f"단위 {len(corp):,}가지 · 작업 대상({MIN_CORP}곳 이상) {len(target)}개")

    tf = defaultdict(Counter)
    df = Counter()
    perkey = defaultdict(Counter)
    for i, ((t, k, _kd), r) in enumerate(keyed, 1):
        if i % 2000 == 0:
            print(f"   토큰화 {i:,}/{len(keyed):,}")
        u = (t, k)
        ts = set(tokenize((r["text"] or "")[:HEAD]))
        tf[u].update(ts)
        df.update(ts)
        for w in ts:
            perkey[w][u] += 1
    n_all = len(keyed)

    out = []
    for u in sorted(corp, key=lambda x: (ORD.index(x[0]), -len(corp[x]), x[1])):
        c = nsec[u]
        scored = []
        for w, n in tf[u].items():
            if n < c * MIN_COVER or df[w] < MIN_DF:
                continue
            s = (n / c) / (df[w] / n_all)
            if s >= MIN_SCORE:
                scored.append((s, w, perkey[w][u] / df[w]))
        scored.sort(reverse=True)
        scored = scored[:TOP_N]
        ps = paths[u].most_common()
        out.append({
            "작업대상": "O" if u in target else "",
            "대분류": u[0],
            "열쇠": u[1],
            "열쇠뜻": titles[u].most_common(1)[0][0],
            "다른제목": " / ".join(t for t, _n in titles[u].most_common(4)[1:]),
            "이름가짓수": len(titles[u]),
            "열쇠종류": kind[u],
            "기업수": len(corp[u]),
            "절수": c,
            "글자수": chars[u],
            "경로수": len(ps),
            "경로": " ".join(f"{p}:{n}" for p, n in ps[:5]),
            "낱말수": len(scored),
            "낱말": " ".join(f"{w}:{s:.1f}:{p*100:.0f}%" for s, w, p in scored),
            "top_score": f"{scored[0][0]:.1f}" if scored else "",
            "뺄것": "", "더할것": "", "메모": "",
        })

    p = ROOT / "data" / "eval" / "section_schema.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    tg = [r for r in out if r["작업대상"]]
    cov = sum(chars[u] for u in target) / sum(chars.values())
    print(f"\n{p.relative_to(ROOT)}  {len(out)}행 (작업 대상 {len(tg)}행)")
    print(f"   작업 대상이 덮는 본문 {cov*100:.1f}%")
    print(f"   낱말 안 잡힌 단위   작업대상 {sum(1 for r in tg if r['낱말수']==0)}개"
          f" · 전체 {sum(1 for r in out if r['낱말수']==0)}개")
    print(f"   낱말 평균 (작업대상) {sum(r['낱말수'] for r in tg)/len(tg):.1f}개")
    print("\n   대분류별 (작업대상/전체)")
    for t in ORD:
        a = sum(1 for r in out if r["대분류"] == t)
        b = sum(1 for r in tg if r["대분류"] == t)
        print(f"      {t:<6}{b:>4} / {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
