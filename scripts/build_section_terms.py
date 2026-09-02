"""절 스키마의 낱말을 한 행씩 펼치고 용례를 붙인다.

`section_schema.csv` 는 절이 한 행이라 낱말이 14~20개씩 한 칸에 몰려 있다.
낱말 하나를 판정하려면 그 낱말이 그 절에서 어떤 문맥에 쓰이는지 봐야 하는데,
그러려면 `section.text` 를 봐야 한다. 파트너 쪽 저장소에는 `section` 테이블이
없어 직접 못 뽑는다. 그래서 여기서 만들어 넘긴다.

용례가 왜 필요한지는 `pathmap_review.md` 에 실례가 있다.

    I/5  이유  점수 21.3
    용례  정관변경일 │ 해당주총명 │ 주요변경사항 │ 변경이유

낱말만 보면 "이유" 를 남길지 뺄지 알 수 없다. 용례를 보면 표 머리글에서 온
말이라는 것이 한눈에 보인다.

## 무엇을 내나

작업 대상 165행을 낱말 단위로 펼친다. 절 하나에 낱말 15개면 15행이 된다.
`뺄까` 칸에 O 를 적으면 되고, 그것을 `section_schema.csv` 의 `뺄것` 으로
합치는 것은 나중에 코드가 한다.

## 용례를 어디서 뽑나

그 단위에 속한 절 중 그 낱말이 실제로 든 것을 찾아 앞뒤 26자를 자른다.
`build_section_schema.py` 와 같은 조건으로 절을 고른다 — 정정본은 최신 한 건,
감사보고서 제외, 300자 미만 제외.

낱말이 형태소 분석 결과라 원문에 그 형태 그대로 없을 수 있다. 그때는
빈칸으로 둔다. 실측하면 몇 퍼센트 나온다.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

sys.path.insert(0, str(ROOT / "scripts"))
from build_section_schema import (MIN_CHARS, latest_docs, norm, schema_key)

WIDE = 26       # 낱말 앞뒤로 잘라 낼 글자 수
MAX_SEC = 40    # 단위마다 이만큼의 절에서만 용례를 찾는다


def main(year: int = 2025, subtype: str = "annual") -> int:
    con = connect()
    keep = latest_docs(con, year, subtype)
    q = ",".join("?" * len(keep))
    rows = con.execute(f"""
        SELECT s.path, s.title, s.aclass, s.text
        FROM section s WHERE s.doc_id IN ({q})
          AND s.path <> '' AND s.title <> '' AND s.char_len >= ?""",
        list(keep) + [MIN_CHARS]).fetchall()

    texts: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        k = schema_key(r)
        if k and len(texts[(k[0], k[1])]) < MAX_SEC:
            texts[(k[0], k[1])].append(r["text"] or "")
    print(f"절 {len(rows):,}개 · 단위 {len(texts):,}가지")

    def sample(unit: tuple, word: str) -> str:
        for t in texts.get(unit, []):
            i = t.find(word)
            if i >= 0:
                s = t[max(0, i - WIDE):i + len(word) + WIDE]
                return re.sub(r"\s+", " ", s).strip()
        return ""

    src = ROOT / "data" / "eval" / "section_schema.csv"
    with src.open(encoding="utf-8-sig") as f:
        schema = [r for r in csv.DictReader(f) if r["작업대상"]]

    out = []
    for r in schema:
        unit = (r["대분류"], r["열쇠"])
        ai = {}
        for k, v in (("뺌", "AI뺄것"), ("남김", "AI남길것"), ("애매", "AI애매")):
            for w in (x.strip() for x in r.get(v, "").split(",") if x.strip()):
                ai[w] = k
        for tok in r["낱말"].split():
            w, sc, pr = tok.rsplit(":", 2)
            out.append({
                "대분류": r["대분류"], "열쇠": r["열쇠"], "열쇠뜻": r["열쇠뜻"],
                "기업수": r["기업수"], "낱말": w, "점수": sc, "확률": pr,
                "AI판정": ai.get(w, ""), "용례": sample(unit, w),
                "뺄까": "", "메모": "",
            })

    p = ROOT / "data" / "eval" / "section_terms.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        wr.writeheader()
        wr.writerows(out)

    empty = sum(1 for r in out if not r["용례"])
    print(f"\n{p.relative_to(ROOT)}  {len(out):,}행 (절 {len(schema)}개)")
    print(f"   용례를 못 찾은 낱말 {empty:,}개 ({empty/len(out)*100:.1f}%)")
    from collections import Counter
    c = Counter(r["AI판정"] for r in out)
    print(f"   AI판정  " + " · ".join(f"{k or '(없음)'} {v:,}" for k, v in c.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
