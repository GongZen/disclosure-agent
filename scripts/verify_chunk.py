"""chunk 를 검증한다. 토큰·임베딩으로 넘어가기 전 관문이다.

section 을 임베딩 한도에 맞춰 나눈 결과를 본다. 나누는 일은 우리가 하므로
원문 파싱과 달리 돌발이 적지만, 여기서 잃으면 그 위의 임베딩이 전부 헛것이
된다. 임베딩 재수행은 2시간과 42,474원이다.

여섯 겹을 둔다.

    1 누락       내용이 있는 section 중 조각이 안 만들어진 것
    2 보존       section 글자와 조각 글자 합이 맞는가
    3 한도       임베딩 한도를 넘는 조각이 있는가
    4 표 경계     머리글 없이 숫자만 남은 표 조각이 얼마나 되는가
    5 헤더 정합   조각의 헤더가 그 section 의 것인가
    6 참조       부모 없는 조각이 있는가

4번은 실패로 세지 않고 비율로 낸다. 임계값을 미리 정하면 근거 없는 숫자가
된다. 실측 후 정한다.

주의 — 이 스크립트는 아직 실제 데이터로 검증되지 않았다. chunk 를 다시 만든
뒤에야 각 항목이 제대로 잡는지 확인된다. 검증되지 않은 검증기를 믿는 것이
W6 실패의 축소판이 될 수 있다.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from chunk import LIMIT
from db import connect

# 표 머리글로 인정할 줄. 셀 구분자가 있고 짧다.
CELL = "│"
HEAD_MAX = 400
# 값처럼 보이는 칸
_NUM = re.compile(r"[\d,.\-()△▲%\s]+")
# 숫자로 보이지만 열 이름인 것. "제 17 기 1분기말" · "2023.3.31 현재"
_PERIOD = re.compile(r"20\d\d[.\-/년]|'\d\d[.\-/]|제\s*\d+\s*기"
                     r"|당기|전기|당분기|전분기|반기")
# 조각 글자 합이 section 보다 늘어나는 것은 머리글 반복 때문이다.
# 큰 표가 여러 조각으로 나뉠 때마다 머리글이 한 줄씩 붙는다.
GROW_TOL = 0.30


def main(limit: int | None = None) -> int:
    con = connect()
    q = lambda s, *a: con.execute(s, *a).fetchall()
    fail = 0

    print("── 규모")
    r = q("""SELECT COUNT(*) n, COUNT(DISTINCT section_id) s,
                    COUNT(DISTINCT doc_id) d, SUM(char_len) c FROM chunk""")[0]
    print(f"   조각 {r['n']:,} · section {r['s']:,} · 문서 {r['d']:,} "
          f"· 글자 {r['c'] or 0:,}")

    # ── 1 누락
    print("\n1 누락      내용이 있는데 조각이 안 만들어진 section")
    rows = q("""SELECT s.section_id, s.char_len, d.corp_name, s.title
                FROM section s
                JOIN document d ON s.doc_id = d.doc_id
                LEFT JOIN chunk c ON s.section_id = c.section_id
                WHERE s.char_len > 0 AND c.chunk_id IS NULL""")
    print(f"   {len(rows):,}건")
    for x in rows[:8]:
        print(f"      {x['corp_name']:<14}{x['char_len']:>9,}자  {x['title'][:40]}")
    fail += len(rows)

    # ── 2 보존
    #
    # section.char_len 이 아니라 length(section.text) 를 기준으로 삼는다.
    # char_len 은 text_len + table_len 이라 조각을 잇는 줄바꿈이 빠져 있다.
    # 실측에서 section 67,300건이 둘 사이가 어긋났다. 그 값으로 재면
    # 멀쩡한 chunk 가 "늘어났다" 로 잡힌다.
    print("\n2 보존      section 본문 길이와 조각 글자 합")
    rows = q(f"""SELECT s.section_id, length(s.text) sc, SUM(c.char_len) cc,
                        COUNT(*) n, d.corp_name, s.title
                 FROM section s
                 JOIN chunk c ON s.section_id = c.section_id
                 JOIN document d ON s.doc_id = d.doc_id
                 GROUP BY s.section_id
                 HAVING cc < sc * 0.98 OR cc > sc * {1 + GROW_TOL}""")
    lost = [x for x in rows if x["cc"] < x["sc"]]
    grown = [x for x in rows if x["cc"] > x["sc"]]
    print(f"   줄어든 section {len(lost):,}  ·  {GROW_TOL:.0%} 넘게 늘어난 section {len(grown):,}")
    for x in (lost + grown)[:8]:
        d = (x["cc"] - x["sc"]) / max(x["sc"], 1) * 100
        print(f"      {x['corp_name']:<14}section {x['sc']:>8,} → 조각 {x['cc']:>8,}"
              f" ({d:+.1f}%, {x['n']}조각)  {x['title'][:26]}")
    fail += len(lost)          # 줄어든 것만 실패. 늘어난 것은 머리글 반복

    # ── 3 한도
    print(f"\n3 한도      {LIMIT:,}자를 넘는 조각")
    rows = q("SELECT chunk_id, char_len FROM chunk WHERE char_len > ?", (LIMIT,))
    print(f"   {len(rows):,}건")
    for x in rows[:5]:
        print(f"      chunk {x['chunk_id']}  {x['char_len']:,}자")
    fail += len(rows)

    # ── 4 표 경계
    #
    # 조각 어디에든 셀 구분자가 있으면 표 조각으로 세면 안 된다. 앞쪽이
    # 문장이고 뒤에 표가 붙은 조각이 그렇게 잡힌다. 실측에서 56,918건 중
    # 상당수가 "당사는 원재료 취득을 목적으로…" 같은 문장으로 시작했다.
    # 첫 줄이 표 행인 조각만 대상이다. 그때만 머리글이 필요하다.
    print("\n4 표 경계    표로 시작하는 조각인데 머리글이 없는 것  [검토 목록]")
    rows = [x for x in q("""SELECT chunk_id, seq, text FROM chunk
                            WHERE seq > 0 AND text LIKE '%' || ? || '%'""",
                         (CELL,))
            if CELL in x["text"].split("\n", 1)[0]]
    noheader, nameless = [], []
    for x in rows:
        first = x["text"].split("\n", 1)[0]
        # 머리글은 값이 아니라 항목 이름들이다. 숫자 칸이 대부분이면
        # 머리글이 아니라 데이터 행이다. 다만 "제 17 기 1분기말" 이나
        # "2023.3.31" 같은 열 이름은 숫자로 보이지만 머리글이다.
        cells = [c.strip() for c in first.split(CELL.strip()) if c.strip()]
        if not cells or len(first) > HEAD_MAX:
            noheader.append(x)
            continue
        num = sum(1 for c in cells if _NUM.fullmatch(c))
        if num / len(cells) <= 0.5 or _PERIOD.search(first):
            continue
        noheader.append(x)
        # 첫 칸까지 숫자면 무엇의 값인지 알 길이 없다. 이것만 실패다.
        if _NUM.fullmatch(cells[0]):
            nameless.append(x)
    rate = len(noheader) / len(rows) * 100 if rows else 0
    print(f"   표로 시작하는 조각 {len(rows):,} 중 {len(noheader):,} ({rate:.1f}%)")
    print(f"      그중 행 머리까지 숫자인 것 {len(nameless):,}")
    if noheader:
        print("      나머지는 행 머리에 항목명이 있어 무엇인지는 알 수 있다.")
        print("      열 이름(어느 기)만 없고, 검색 뒤 section 전체를 꺼내면 복원된다.")
        print("      DART 가 재무상태표의 열 이름과 데이터를 별도 TABLE 로 나눠 담아")
        print("      표 첫 행이 곧 머리글이 아닌 경우가 있다. 원문 구조의 성질이다.")
        for x in noheader[:3]:
            print(f"      chunk {x['chunk_id']} seq {x['seq']}: "
                  f"{x['text'][:90]!r}")
    fail += len(nameless)

    # ── 5 헤더 정합
    print("\n5 헤더 정합  조각 헤더가 그 문서·절과 맞는가")
    rows = q("""SELECT c.chunk_id, c.header, d.corp_name, s.title
                FROM chunk c
                JOIN section s ON c.section_id = s.section_id
                JOIN document d ON c.doc_id = d.doc_id
                WHERE c.header IS NULL
                   OR instr(c.header, d.corp_name) = 0""")
    print(f"   기업 이름이 헤더에 없는 조각 {len(rows):,}")
    for x in rows[:5]:
        print(f"      chunk {x['chunk_id']}  {x['corp_name']}  헤더 {x['header']!r}")
    fail += len(rows)

    # ── 6 참조
    print("\n6 참조      부모 없는 조각")
    for label, sql in [
        ("section 참조", """SELECT COUNT(*) n FROM chunk c
             LEFT JOIN section s ON c.section_id=s.section_id
             WHERE s.section_id IS NULL"""),
        ("document 참조", """SELECT COUNT(*) n FROM chunk c
             LEFT JOIN document d ON c.doc_id=d.doc_id WHERE d.doc_id IS NULL"""),
        ("company 참조", """SELECT COUNT(*) n FROM chunk c
             LEFT JOIN company p ON c.corp_code=p.corp_code
             WHERE p.corp_code IS NULL"""),
        ("빈 본문", "SELECT COUNT(*) n FROM chunk WHERE text IS NULL OR text=''"),
        ("글자 수 불일치", "SELECT COUNT(*) n FROM chunk WHERE char_len<>length(text)"),
    ]:
        n = q(sql)[0]["n"]
        fail += n
        print(f"   {label:<16}{n:>8,}")

    # ── 7 section 길이 정합
    #
    # section.char_len 은 text_len + table_len 이고 text 는 조각을 \n 으로
    # 이은 것이라 둘이 어긋난다. 어긋난 만큼이 조각 사이 줄바꿈 수다.
    # 지금 데이터에서 67,300건이 다르다.
    #
    # 실패로 세지 않는다. 값을 잃은 것이 아니라 세는 방식이 다른 것이다.
    # 다만 char_len 을 쓰는 모든 계산이 조금씩 어긋나므로 section.py 에서
    # char_len = len(body) 로 바꾸는 것이 맞다. 그러면 이 항목이 0 이 된다.
    print("\n7 section 길이  char_len 과 본문 길이가 다른 section  [검토 목록]")
    r = q("""SELECT COUNT(*) n, SUM(length(text) - char_len) d
             FROM section WHERE text IS NOT NULL AND char_len <> length(text)""")[0]
    print(f"   {r['n']:,}건 · 합계 차이 {r['d'] or 0:,}자")
    if r["n"]:
        print("      char_len = text_len + table_len 이라 조각을 잇는 줄바꿈이 빠진다.")
        print("      section.py 에서 char_len = len(body) 로 바꾸면 0 이 된다.")

    print(f"\n{'통과' if fail == 0 else f'실패 {fail:,}건'}"
          + (f" · 검토 필요 {len(noheader) + r['n']:,}건"
             if (noheader or r["n"]) else ""))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    lim = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            lim = int(a.split("=")[1])
    sys.exit(main(limit=lim))
