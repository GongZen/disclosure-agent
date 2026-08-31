"""4번 걸음 검산. 두 경로 대조로 안 잡히는 오류를 잡는다.

두 경로가 같은 값을 내도 그 값이 틀릴 수 있다. 같은 표를 서로 다른 방법으로
읽었을 뿐 원문 자체를 잘못 짚었으면 둘 다 같이 틀린다. 그래서 값들 사이의
관계와 문서 사이의 관계로 따로 검산한다.

    항등식     자산총계 = 부채총계 + 자본총계
               한 문서 안에서 성립해야 한다
    연도 이음   올해 보고서의 전기 값 = 작년 보고서의 당기 값
               연도를 밀려 읽으면 여기서 드러난다
    크기       매출 대비 자산, 영업이익 대비 매출의 자릿수
               단위를 1,000배 잘못 읽으면 눈에 띈다
"""
import sys
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import read_raw
from db import connect
from fsvalue import extract, pick, has_fs_tags
from lxml import etree
from fsdoc import _P
from fsvalue import from_tags

# 맥락 없는 문서. 표 파싱으로 넘어가는데 골든에 표본이 없어 따로 본다.
NOCTX = set()


def scaled(g):
    """표기값에 단위 배수를 곱해 원 단위로."""
    if g is None or g.get("mult") is None:
        return None
    return g["value"] * g["mult"]


def main():
    con = connect()
    docs = con.execute("""SELECT doc_id,corp_name,rcept_dt,doc_subtype,base_year,
                                 is_correction,file_path FROM document
                          WHERE doc_group='periodic' AND file_format='xml'
                          ORDER BY corp_name,rcept_dt""").fetchall()
    stat = collections.Counter()
    ident_bad, size_bad = [], []
    # 연도 이음용 — (기업, 회계연도) → 값
    byyear = collections.defaultdict(dict)

    for d in docs:
        try:
            raw = read_raw(d["file_path"])
        except Exception:
            stat["읽기실패"] += 1
            continue
        rows = extract(raw)
        if not rows:
            stat["빈결과"] += 1
            continue
        stat["문서"] += 1
        src = rows[0]["source"]
        stat[f"경로:{src}"] += 1
        if has_fs_tags(raw) and src == "table":
            NOCTX.add(d["doc_id"])

        a = scaled(pick(rows, "total_assets"))
        l = scaled(pick(rows, "total_liabilities"))
        e = scaled(pick(rows, "total_equity"))
        # 항등식
        if None not in (a, l, e):
            stat["항등식 대상"] += 1
            if a == l + e:
                stat["항등식 성립"] += 1
            else:
                gap = a - l - e
                ident_bad.append((d["corp_name"], d["rcept_dt"], d["doc_subtype"],
                                  a, l, e, gap, src, d["doc_id"] in NOCTX))
        # 크기 — 매출이 자산의 1000배를 넘거나 1/10000 미만이면 의심
        r = scaled(pick(rows, "revenue"))
        if a and r:
            stat["크기 대상"] += 1
            ratio = r / a
            if ratio > 100 or ratio < 0.0001:
                size_bad.append((d["corp_name"], d["rcept_dt"], a, r, ratio, src))
        # 연도 이음 — 사업보고서만. 당기 값을 모아 둔다
        if d["doc_subtype"] == "annual" and d["base_year"]:
            byyear[(d["corp_name"], int(d["base_year"]))] = {
                "assets": a, "revenue": r, "src": src, "dt": d["rcept_dt"],
                "corr": d["is_correction"]}

    print(f"── 문서 {stat['문서']:,}건")
    for k in ("경로:xbrl", "경로:table", "빈결과", "읽기실패"):
        if stat[k]:
            print(f"   {k:<12}{stat[k]:>6,}")
    n, ok = stat["항등식 대상"], stat["항등식 성립"]
    print(f"\n── 회계 항등식  {ok:,}/{n:,}  {ok/n*100:.2f}%" if n else "")
    if ident_bad:
        print(f"   어긋남 {len(ident_bad)}건")
        for c, dt, sub, a, l, e, gap, src, nc in ident_bad[:12]:
            mark = " [맥락없음]" if nc else ""
            print(f"      {c:<14}{dt} {sub:<8}[{src}]{mark}")
            print(f"         자산 {a:>22,}")
            print(f"         부채+자본 {l+e:>17,}   차이 {gap:,}")
    print(f"\n── 크기 이상  {len(size_bad)}건 / {stat['크기 대상']:,}")
    for c, dt, a, r, ratio, src in size_bad[:10]:
        print(f"   {c:<14}{dt}  자산 {a:>20,}  매출 {r:>20,}  비 {ratio:.4f}  [{src}]")

    # 연도 이음 — 같은 기업의 이웃 연도 사업보고서를 잇는다
    print("\n── 연도 이음  (사업보고서 당기 값이 해마다 이어지는가)")
    corps = {c for c, _y in byyear}
    jump = []
    nj = 0
    for c in sorted(corps):
        yrs = sorted(y for cc, y in byyear if cc == c)
        for y1, y2 in zip(yrs, yrs[1:]):
            if y2 - y1 != 1:
                continue
            v1, v2 = byyear[(c, y1)], byyear[(c, y2)]
            if not v1["assets"] or not v2["assets"]:
                continue
            nj += 1
            ch = abs(v2["assets"] - v1["assets"]) / v1["assets"]
            if ch > 3:
                jump.append((c, y1, y2, v1["assets"], v2["assets"], ch))
    print(f"   이웃 연도 쌍 {nj}개 · 자산이 4배 넘게 변한 것 {len(jump)}건")
    for c, y1, y2, a1, a2, ch in jump[:8]:
        print(f"      {c:<14}{y1}→{y2}  {a1:>20,} → {a2:>20,}  {ch:.1f}배")
    return 0


if __name__ == "__main__":
    sys.exit(main())
