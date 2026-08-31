"""fact_financial 을 검증한다. 추출 단계가 아니라 적재된 DB 를 본다.

4번 걸음에서 같은 관계를 재었으나 그것은 extract() 결과였다. 담는 과정에서
행이 빠지거나 좌표가 어긋났을 수 있어 DB 를 대상으로 다시 잰다.

원문 자체가 어긋난 것은 실패로 세지 않고 목록으로 낸다. 우리가 고칠 수 없고
고쳐서도 안 되는 값이다. 다만 답변에서 그 값을 쓸 때 알고 있어야 한다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

# 회계 항등식을 원 단위로 맞춰 보면 천원 단위 반올림으로 1,000원까지 어긋난다.
# 원문이 그렇게 적혀 있는 것이라 오류가 아니다.
ROUNDING = 1_000


def main():
    con = connect()
    q = lambda s, *a: con.execute(s, *a).fetchall()
    fail = 0

    print("── 규모")
    r = q("""SELECT COUNT(*) n, COUNT(DISTINCT doc_id) d, COUNT(DISTINCT corp_code) c
             FROM fact_financial""")[0]
    print(f"   행 {r['n']:,} · 문서 {r['d']:,} · 기업 {r['c']}")

    print("\n── 무결성")
    checks = [
        ("document 참조", """SELECT COUNT(*) n FROM fact_financial f
             LEFT JOIN document d ON f.doc_id=d.doc_id WHERE d.doc_id IS NULL"""),
        ("company 참조", """SELECT COUNT(*) n FROM fact_financial f
             LEFT JOIN company c ON f.corp_code=c.corp_code WHERE c.corp_code IS NULL"""),
        ("값 비어 있음", "SELECT COUNT(*) n FROM fact_financial WHERE value IS NULL"),
        ("단위 비어 있음", "SELECT COUNT(*) n FROM fact_financial WHERE unit_mult IS NULL"),
        ("연도 비어 있음", "SELECT COUNT(*) n FROM fact_financial WHERE fiscal_year IS NULL"),
        ("출처 비어 있음", "SELECT COUNT(*) n FROM fact_financial WHERE source NOT IN ('xbrl','table')"),
        ("기간 유형 이상", """SELECT COUNT(*) n FROM fact_financial
             WHERE period_type NOT IN ('instant','annual','cumulative','quarter')"""),
        ("기준 이상", "SELECT COUNT(*) n FROM fact_financial WHERE basis NOT IN ('연결','별도')"),
        ("중복", """SELECT COUNT(*) n FROM (SELECT 1 FROM fact_financial
             GROUP BY doc_id,item_code,fiscal_year,period_type,basis HAVING COUNT(*)>1)"""),
    ]
    for label, sql in checks:
        n = q(sql)[0]["n"]
        fail += n
        print(f"   {label:<18}{n:>6,}")

    print("\n── 회계 항등식  자산총계 = 부채총계 + 자본총계")
    rows = q("""
        SELECT a.doc_id, d.corp_name, d.rcept_dt, a.basis, a.source,
               a.value av, l.value lv, e.value ev
        FROM fact_financial a
        JOIN fact_financial l ON a.doc_id=l.doc_id AND a.basis=l.basis
             AND l.item_code='total_liabilities'
        JOIN fact_financial e ON a.doc_id=e.doc_id AND a.basis=e.basis
             AND e.item_code='total_equity'
        JOIN document d ON a.doc_id=d.doc_id
        WHERE a.item_code='total_assets'""")
    bad = [r for r in rows if abs(r["av"] - r["lv"] - r["ev"]) > ROUNDING]
    near = [r for r in rows if 0 < abs(r["av"] - r["lv"] - r["ev"]) <= ROUNDING]
    print(f"   대상 {len(rows):,} · 어긋남 {len(bad)} · 반올림 차이 {len(near)}")
    fail += len(bad)
    for r in bad[:10]:
        gap = r["av"] - r["lv"] - r["ev"]
        print(f"      {r['corp_name']:<14}{r['rcept_dt']} {r['basis']} [{r['source']}]"
              f"  차이 {gap:,}")

    print("\n── 2차 항목 관계")
    # 골든에 정답지가 없는 대신 값들끼리 맞물리는 관계가 있다.
    # 다만 넷이 성질이 다르다.
    #
    #   반드시 성립   회계 정의상 어긋날 수 없다. 어긋나면 우리 오류다
    #   대체로 성립   기업의 표시 방식에 따라 어긋날 수 있다
    #                 유동·비유동 밖에 대분류를 더 두거나
    #                 판관비 밖에 영업비용을 따로 적는 경우다
    #                 참고 지표로 두고 실패로 세지 않는다
    for label, a, b, c, op, strict in [
        ("매출액 − 매출원가 = 매출총이익",
         "revenue", "cost_of_sales", "gross_profit", "-", True),
        ("매출총이익 − 판관비 = 영업이익",
         "gross_profit", "sga", "operating_income", "-", False),
        ("유동자산 + 비유동자산 = 자산총계",
         "current_assets", "noncurrent_assets", "total_assets", "+", False),
        ("유동부채 + 비유동부채 = 부채총계",
         "current_liabilities", "noncurrent_liabilities", "total_liabilities", "+", False),
    ]:
        # 매각예정 처분집단은 유동·비유동 어느 쪽도 아니라 따로 더한다
        # 유동·비유동 어느 쪽도 아닌 대분류들. 총계에 더해야 맞는다.
        #   매각예정 처분집단   K-IFRS 1105호. 팔기로 한 사업부
        #   금융업 자산·부채    금융 자회사를 둔 지주회사가 따로 적는다
        extras = {"total_assets": ("held_for_sale_assets",
                                   "financial_business_assets"),
                  "total_liabilities": ("held_for_sale_liabilities",
                                        "financial_business_liabilities")
                  }.get(c, ())
        rows = q("""
            SELECT x.doc_id, d.corp_name, d.rcept_dt, x.basis, x.source,
                   x.value xv, y.value yv, z.value zv
            FROM fact_financial x
            JOIN fact_financial y ON x.doc_id=y.doc_id AND x.basis=y.basis
                 AND x.period_type=y.period_type AND y.item_code=?
            JOIN fact_financial z ON x.doc_id=z.doc_id AND x.basis=z.basis
                 AND x.period_type=z.period_type AND z.item_code=?
            JOIN document d ON x.doc_id=d.doc_id
            WHERE x.item_code=?""", (b, c, a))
        bad = []
        n = 0
        for r in rows:
            if None in (r["xv"], r["yv"], r["zv"]):
                continue
            n += 1
            want = r["xv"] - r["yv"] if op == "-" else r["xv"] + r["yv"]
            # 보조 항목을 무조건 더하면 안 된다. 기업마다 포함 관계가 다르다.
            #   CJ제일제당   매각예정자산을 유동·비유동 밖에 따로 적는다
            #   카카오       매각예정자산은 비유동자산 안에 들어 있고
            #                금융업자산만 밖에 있다
            # 어느 조합을 더해야 맞는지는 문서마다 다르므로 전부 시도한다.
            if abs(want - r["zv"]) > ROUNDING and extras:
                vals = []
                for code in extras:
                    got = q("""SELECT value v FROM fact_financial
                               WHERE doc_id=? AND basis=? AND item_code=?""",
                            (r["doc_id"], r["basis"], code))
                    if got and got[0]["v"]:
                        vals.append(got[0]["v"])
                from itertools import combinations
                for k in range(1, len(vals) + 1):
                    if abs(want - r["zv"]) <= ROUNDING:
                        break
                    for combo in combinations(vals, k):
                        if abs(want + sum(combo) - r["zv"]) <= ROUNDING:
                            want += sum(combo)
                            break
            if abs(want - r["zv"]) > ROUNDING:
                bad.append(r)
        rate = f"{(n-len(bad))/n*100:6.2f}%" if n else "     -"
        if strict:
            fail += len(bad)
        mark = "" if strict else "  (참고)"
        print(f"   {label:<28}대상 {n:>5,} · 어긋남 {len(bad):>4}{rate}{mark}")
        for r in bad[:3]:
            print(f"      {r['corp_name']:<12}{r['rcept_dt']} {r['basis']} [{r['source']}]"
                  f"  {r['xv']:,} / {r['yv']:,} / {r['zv']:,}")

    print("\n── capex 부호  설비투자는 양수로 통일한다")
    r = q("SELECT COUNT(*) n FROM fact_financial WHERE item_code='capex' AND value<0")[0]
    fail += r["n"]
    print(f"   음수 {r['n']}건")

    print("\n── 크기 이상  매출이 자산의 100배 초과 또는 1만분의 1 미만")
    rows = q("""
        SELECT d.corp_name, d.rcept_dt, a.value av, r.value rv, r.source
        FROM fact_financial a
        JOIN fact_financial r ON a.doc_id=r.doc_id AND a.basis=r.basis
             AND r.item_code='revenue'
        JOIN document d ON a.doc_id=d.doc_id
        WHERE a.item_code='total_assets' AND a.value>0 AND r.value>0""")
    odd = [r for r in rows if r["rv"] / r["av"] > 100 or r["rv"] / r["av"] < 0.0001]
    print(f"   대상 {len(rows):,} · 이상 {len(odd)}")
    fail += len(odd)
    for r in odd[:8]:
        print(f"      {r['corp_name']:<14}{r['rcept_dt']}  자산 {r['av']:>20,}  매출 {r['rv']:>20,}")

    print("\n── 연도 간 연속성  같은 기업 사업보고서의 자산총계")
    rows = q("""
        SELECT d.corp_name, f.fiscal_year, f.value
        FROM fact_financial f JOIN document d ON f.doc_id=d.doc_id
        WHERE f.item_code='total_assets' AND f.basis='연결'
          AND d.doc_subtype='annual'
        ORDER BY d.corp_name, f.fiscal_year""")
    byc = {}
    for r in rows:
        byc.setdefault(r["corp_name"], {})[r["fiscal_year"]] = r["value"]
    jump, npair = [], 0
    for corp, ys in byc.items():
        for y in sorted(ys):
            if y + 1 not in ys or not ys[y]:
                continue
            npair += 1
            ch = abs(ys[y + 1] - ys[y]) / ys[y]
            if ch > 3:
                jump.append((corp, y, ys[y], ys[y + 1], ch))
    print(f"   이웃 연도 쌍 {npair} · 자산이 4배 넘게 변한 것 {len(jump)}")
    fail += len(jump)
    for corp, y, a, b, ch in jump[:6]:
        print(f"      {corp:<14}{y}→{y+1}  {a:>20,} → {b:>20,}  {ch:.1f}배")

    print("\n── 원문 오류 목록  (실패로 세지 않는다)")
    if near:
        print(f"   회계 항등식이 반올림 범위에서 어긋난 것 {len(near)}건")
        for r in near:
            gap = r["av"] - r["lv"] - r["ev"]
            print(f"      {r['corp_name']:<14}{r['rcept_dt']} {r['basis']}  차이 {gap:,}원")
            print(f"         천원 단위로 적으며 마지막 자리를 반올림한 결과다")

    print(f"\n{'통과' if fail == 0 else f'실패 {fail}건'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
