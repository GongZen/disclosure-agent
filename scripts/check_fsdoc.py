"""재무제표 표 판정의 상태를 전수로 잰다. 고치기 전과 뒤를 같은 잣대로 비교한다.

세 수치를 낸다.
    오분류   5종이 아닌 표가 5종으로 판정된 수. 구조 조건이라 0이어야 한다
    단위없음  값을 적재할 수 없는 표
    종류없음  어느 재무제표인지 못 정한 표

오분류를 앞에 둔 이유가 있다. 값을 못 뽑으면 빈칸이 보이지만
잘못 뽑으면 그대로 답이 된다.
"""
import sys, json, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import read_raw
from db import connect
from fsdoc import parse_doc, _is_not_fs


def main(out_path: str | None = None):
    con = connect()
    docs = con.execute(
        "SELECT doc_id,corp_name,file_path FROM document "
        "WHERE doc_group='periodic' AND file_format='xml'"
    ).fetchall()

    stat = collections.Counter()
    bysrc = collections.Counter()
    bad = []
    for d in docs:
        try:
            raw = read_raw(d["file_path"])
        except Exception:
            stat["읽기실패"] += 1
            continue
        for basis, kind, table, src, unit in parse_doc(raw):
            stat["표"] += 1
            bysrc[src or "미판정"] += 1
            if kind and _is_not_fs(table):
                stat["오분류"] += 1
                bad.append((d["doc_id"], d["corp_name"], basis, kind, src))
            if not unit[1]:
                stat["단위없음"] += 1
            if not kind:
                stat["종류없음"] += 1

    tot = stat["표"]
    print(f"문서 {len(docs):,}건 · 표 {tot:,}개")
    for k in ("오분류", "단위없음", "종류없음"):
        v = stat[k]
        print(f"   {k:<8}{v:>7,}   {v / tot * 100:5.2f}%")
    print("── 종류 판정 근거")
    for k, v in bysrc.most_common():
        print(f"   {k:<8}{v:>7,}   {v / tot * 100:5.1f}%")
    if bad:
        print(f"── 오분류 {len(bad)}건 (상위 10)")
        for r in bad[:10]:
            print("   " + " ".join(str(x) for x in r))

    if out_path:
        Path(out_path).write_text(
            json.dumps({"stat": dict(stat), "src": dict(bysrc), "bad": bad[:200]},
                       ensure_ascii=False), encoding="utf-8")
    return 0 if stat["오분류"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
