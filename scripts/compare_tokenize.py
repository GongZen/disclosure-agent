"""토큰화 방식 넷을 같은 질의로 나란히 비교한다.

`probe_bm25.py` 로 지금 상태를 재니 문제가 두 갈래로 갈렸다.

    매출채권 · 순이익 · 영업이익    상위 10 중 10개 정확. 문제가 없었다
    미지급비용                    상위 10 중 5개. 임원 보수 조각이 걸린다
    자산총계                      상위 10 중 3개. 부문별 보고가 걸린다

원인이 다르다.

    미지급비용   '미' 가 체언 접두사(XPN)라 버려져 '지급비용' 이 된다
                 임원 보수 조각에 '지급' 과 '비용' 이 흔해 함께 걸린다

    자산총계     '자산' 과 '총계' 가 둘 다 흔하다. 부문별 보고 조각에
                 둘 다 여러 번 나와 빈도 점수가 실제 자산총계 조각을 넘는다

그래서 네 방식을 견준다.

    A  지금 그대로            명사·동사·어근만. 접두사는 버린다
    B  접두사 보존            XPN·XSN 을 앞뒤 명사에 붙인다. 사전이 필요 없다
    C  사전 + 합집합          복합어를 사전에 넣고 원형과 조각을 다 낸다
    D  사전만                 복합어를 사전에 넣고 원형만 낸다

B 는 규칙이라 사전 관리가 필요 없다. C·D 는 사전을 만들어야 한다.
어느 쪽이 실제로 낫는지는 재봐야 안다.
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

KEEP = ("XR", "SL", "SN", "SH")
def _load_terms():
    """확정된 사전을 읽는다. 없으면 비교용 표본으로 대신한다."""
    import csv
    p = ROOT / "data" / "terms" / "dictionary.csv"
    if p.exists():
        out = {}
        with p.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[r["term"]] = r["parts"].split()
        return out
    return {w: [] for w in
            ["미지급비용", "자산총계", "부채총계", "자본총계", "매출채권",
             "당기순이익", "영업이익", "유형자산", "무형자산", "이익잉여금"]}


TERM_PARTS = _load_terms()
TERMS = list(TERM_PARTS)

PROBES = ["매출채권", "순이익", "미지급비용", "영업이익", "자산총계",
          "부채총계", "미수금", "매출원가"]

_kiwi_a = _kiwi_d = None


def _base():
    global _kiwi_a
    if _kiwi_a is None:
        from kiwipiepy import Kiwi
        _kiwi_a = Kiwi()
    return _kiwi_a


def _dict():
    global _kiwi_d
    if _kiwi_d is None:
        from kiwipiepy import Kiwi
        _kiwi_d = Kiwi()
        for w in TERMS:
            _kiwi_d.add_user_word(w, "NNP")
        print(f"   (사전 {len(TERMS):,}개 등록)")
    return _kiwi_d


def tok_a(text: str) -> list[str]:
    """지금 그대로. 명사·동사·어근만 남기고 접두사는 버린다."""
    out = []
    for t in _base().tokenize(text[:20000]):
        if t.tag[0] in "NV" or t.tag in KEEP:
            if len(t.form) > 1 or t.tag in ("SL", "SN"):
                out.append(t.form)
    return out


def tok_b(text: str) -> list[str]:
    """접두사를 앞 명사에 붙인다. 사전 없이 규칙만 쓴다.

    '미지급비용' 은 미(XPN) + 지급(NNG) + 비용(NNG) 으로 잘린다.
    XPN 을 버리면 '지급비용' 이 되어 뜻이 뒤집힌다. 붙이면 '미지급' 이
    살아나고 '지급' 만 든 조각과 갈린다.
    """
    out, pend = [], ""
    for t in _base().tokenize(text[:20000]):
        tag, form = t.tag, t.form
        if tag == "XPN":                       # 미· 비· 재· 등
            pend = form
            continue
        if tag[0] in "NV" or tag in KEEP:
            w = pend + form
            pend = ""
            if len(w) > 1 or tag in ("SL", "SN"):
                out.append(w)
                if pend == "" and w != form and len(form) > 1:
                    out.append(form)           # 조각도 함께 남긴다
        else:
            pend = ""
    return out


def tok_c(text: str) -> list[str]:
    """사전 + 합집합. 사전 낱말이면 그 조각도 함께 낸다."""
    out = []
    for t in _dict().tokenize(text[:20000]):
        if t.tag[0] in "NV" or t.tag in KEEP:
            if len(t.form) > 1 or t.tag in ("SL", "SN"):
                out.append(t.form)
                ps = TERM_PARTS.get(t.form)
                if ps:
                    out += [x for x in ps if x != t.form]
    return out


def tok_d(text: str) -> list[str]:
    """사전만. 원형만 내고 조각은 안 낸다."""
    out = []
    for t in _dict().tokenize(text[:20000]):
        if t.tag[0] in "NV" or t.tag in KEEP:
            if len(t.form) > 1 or t.tag in ("SL", "SN"):
                out.append(t.form)
    return out


WAYS = [("A 지금 그대로", tok_a), ("B 접두사 보존", tok_b),
        ("C 사전+합집합", tok_c), ("D 사전만", tok_d)]


def main(corp: str = "삼성전자", topk: int = 10) -> int:
    from rank_bm25 import BM25Okapi
    con = connect()
    t0 = time.time()
    rows = con.execute("""SELECT c.chunk_id, c.header, c.text, c.tokens, s.title
                          FROM chunk c
                          JOIN section s ON c.section_id = s.section_id
                          JOIN document d ON c.doc_id = d.doc_id
                          WHERE d.corp_name = ? AND c.tokens IS NOT NULL
                            AND c.tokens <> '' AND c.char_len >= 100""",
                       (corp,)).fetchall()
    print(f"대상 {corp} · 조각 {len(rows):,}개 · 적재 {time.time()-t0:.0f}초")

    texts = [f"{r['header']} {r['text']}" for r in rows]
    index = {}
    for name, fn in WAYS:
        t0 = time.time()
        corpus = [fn(t) for t in texts]
        index[name] = (BM25Okapi(corpus), corpus)
        ntok = sum(len(c) for c in corpus)
        print(f"   {name:<14} 토큰 {ntok:>9,} · {time.time()-t0:>5.0f}초")

    print(f"\n{'질의':<12}{'원문에 있음':>10}" +
          "".join(f"{n.split()[0]:>8}" for n, _ in WAYS))
    detail = []
    for q in PROBES:
        total = sum(1 for r in rows if q in r["text"])
        line = f"{q:<12}{total:>10,}"
        for name, fn in WAYS:
            bm, _ = index[name]
            sc = bm.get_scores(fn(q))
            top = sorted(range(len(rows)), key=lambda i: -sc[i])[:topk]
            good = sum(1 for i in top if q in rows[i]["text"])
            line += f"{good:>8}"
            detail.append((q, name, [(sc[i], rows[i]) for i in top]))
        print(line)

    print(f"\n── 어긋난 것만 자세히  (상위 {topk} 중 절반 이하만 맞은 경우)")
    for q, name, hits in detail:
        good = sum(1 for _s, r in hits if q in r["text"])
        if good > topk // 2:
            continue
        print(f"\n   [{name}] 질의 \"{q}\"  {good}/{topk}")
        for i, (s, r) in enumerate(hits[:4], 1):
            mark = "O" if q in r["text"] else "X"
            body = re.sub(r"\s+", " ", r["text"])[:56]
            print(f"      {i}. [{mark}] {s:6.2f} {r['title'][:20]:<22}{body}")
    return 0


if __name__ == "__main__":
    c, k = "삼성전자", 10
    for a in sys.argv[1:]:
        if a.startswith("--corp="):
            c = a.split("=", 1)[1]
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(corp=c, topk=k))
