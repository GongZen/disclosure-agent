"""section 을 검증한다. chunk·임베딩으로 넘어가기 전 첫 관문이다.

W6 에서 이 관문 없이 바로 임베딩했다가 141,524건을 다시 하게 됐다.
적재 후 검증은 있었으나 스키마가 온전한지만 봤다. 참조 무결성 0 · 중복 0 ·
빈 조각 0 을 전부 통과했는데, 문서 468건에서 표 하나가 나머지를 통째로
삼켜 TITLE 30,426개가 제 자리를 잃은 상태였다.

    검사한 것    스키마가 온전한가      담는 과정의 오류를 본다
    안 한 것     원문과 같은 내용인가    만드는 과정의 오류를 본다

담는 과정은 우리가 통제한다. 만드는 과정은 원문에 달려 있다. 후자가 훨씬
자주 틀리는데 전자만 검사했다.

일곱 겹을 둔다. 각각 다른 종류의 오류를 잡는다. 하나로는 부족하다.

    1 보존         잃어버리거나 중복된 글자가 없는가
    2 구조         원문 목차와 조각이 일대일인가
    3 분포         한 절이 문서를 독차지하지 않는가
    4 독립 대조     다른 방법으로 세어도 같은가
    5 깨진 표       표 안에 TITLE 이 들어 있지 않은가
    6 파서 오류     파싱이 오류 없이 끝났는가
    7 내용 일치     원문 텍스트와 파싱 결과가 문자열로 같은가

6·7 은 W6 원인 규명 후에 넣었다. 6 은 구조 붕괴의 예고이고, 7 은 글자 수가
같아도 순서가 뒤바뀐 경우를 잡는다. 1 은 길이만 보므로 7 을 대신하지 못한다.

3번에 걸리면 실패로 세지 않고 검토 목록에 올린다. 원래 긴 절일 수도 있고
구조가 무너진 것일 수도 있는데 둘은 열어보기 전에 구분되지 않는다.
목록이 비어 있지 않으면 반드시 표본을 열어 확인한다. 설명으로 덮지 않는다.

원문은 파일별로 따로 파싱한다. 이어붙이면 <?xml ?> 선언이 문서 중간에 와서
루트가 여럿이 되고, 파서가 첫 루트만 읽고 나머지를 버린다. 사업보고서 210건의
첨부 감사보고서가 그렇게 통째로 빠져 있었다.
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from lxml import etree

from corpus import doc_files
from db import connect
from sanitize import STD, sanitize
from section import CELL_SEP

_P = etree.XMLParser(recover=True, huge_tree=True)

# 문자열에서 TITLE 을 찾는다. 태그 구조를 쓰지 않으므로 중첩과 무관하다.
_TITLE_RE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_DECL_RE = re.compile(r"<[?!][^<>]*>")
_STD_RE = re.compile(r"</?(?:" + "|".join(sorted(STD, key=len, reverse=True))
                     + r")(?:\s[^<>]*)?/?>", re.I)

# 한 절이 문서 글자의 이 비율을 넘으면 검토 목록에 올린다
SKEW = 0.50
# 보존 검증 허용 오차. 공백 정규화 차이가 있다
KEEP_TOL = 0.02


def _tag(el) -> str:
    t = el.tag
    return t.upper() if isinstance(t, str) else ""


def plain_len(xml: str) -> int:
    """태그를 걷어낸 순수 텍스트 길이. 공백은 하나로 줄인다."""
    return len(re.sub(r"\s+", " ", _TAG_RE.sub(" ", xml)).strip())


def strip_std(xml: str) -> str:
    """표준 태그만 걷어낸 텍스트. 파서를 쓰지 않는 독립 경로다.

    파싱 결과와 이것을 대조하면 파서가 무엇을 옮기거나 버렸는지 드러난다.
    """
    s = _STD_RE.sub("", _DECL_RE.sub("", xml))
    return re.sub(r"\s+", "", html.unescape(s))


def count_titles_by_regex(xml: str) -> int:
    """정규식으로 TITLE 개수를 센다. 방법 B 다.

    lxml 이 깨진 문서를 복구하며 태그를 옮기는데, 정규식은 원문 그대로 센다.
    두 방법이 다른 수를 내면 어느 쪽이든 믿을 수 없다는 뜻이다.

    빈 TITLE 도 센다. `section.parse()` 가 제목이 비었어도 조각을 만들기
    때문이다. 내용 있는 것만 세면 크래프톤 2025년 반기보고서에서 109 대
    117 로 어긋난다. 실제로는 TITLE 116 + head 조각 1 = 117 로 맞는다.
    """
    return sum(1 for _ in _TITLE_RE.finditer(xml))


def main(limit: int | None = None, sample: int | None = None) -> int:
    con = connect()
    q = """SELECT doc_id, corp_name, report_nm, file_path FROM document
           WHERE doc_group='periodic' AND file_format='xml'"""
    if sample:
        q += f" ORDER BY RANDOM() LIMIT {int(sample)}"
    elif limit:
        q += f" ORDER BY doc_id LIMIT {int(limit)}"
    else:
        q += " ORDER BY doc_id"
    docs = con.execute(q).fetchall()

    fail = 0
    keep_bad, struct_bad, skew_list = [], [], []
    broken_docs, err_docs, text_bad = [], [], []
    n = 0
    for d in docs:
        files = [f for f in doc_files(d["file_path"])
                 if f.suffix.lower() in (".xml", ".html")]
        if not files:
            continue
        n += 1

        want_len = want_txt = 0
        got_txt = ""
        n_re = n_err = n_broken = 0
        raw_all = ""
        for f in files:
            raw = sanitize(f.read_text(encoding="utf-8", errors="replace"))
            raw_all += raw
            want_len += plain_len(raw)
            want_txt += len(strip_std(raw))
            n_re += count_titles_by_regex(raw)
            p = etree.XMLParser(recover=True, huge_tree=True)
            root = etree.fromstring(raw.encode("utf-8"), parser=p)
            n_err += len(p.error_log)
            if root is None:
                continue
            got_txt += re.sub(r"\s+", "", "".join(root.itertext()))
            for e in root.iter():
                if _tag(e) != "TABLE":
                    continue
                if any(_tag(x) == "TABLE" for x in e.iterancestors()):
                    continue
                if any(_tag(c) == "TITLE" for c in e.iterdescendants()):
                    n_broken += 1

        secs = con.execute(
            """SELECT path, title, char_len, text FROM section
               WHERE doc_id=? ORDER BY seq""", (d["doc_id"],)).fetchall()
        if not secs:
            struct_bad.append((d["corp_name"], d["report_nm"][:24],
                               "조각 없음", 0, 0))
            continue
        tot = sum(s["char_len"] for s in secs)

        # 1 보존
        #
        # char_len 과 원문 글자 수를 바로 견주면 안 된다. section 은 표 셀을
        # " │ " 로 잇고 조각을 줄바꿈으로 잇는다. 크래프톤 2025년 반기보고서
        # 에서 셀 구분자 16,673개가 50,019자, 줄바꿈이 4,954자를 더해
        # 원문보다 16.9% 길게 나왔다. 값을 잃은 것이 아니라 더한 것이다.
        #
        # 그래서 넣은 글자를 도로 빼고 공백을 지운 뒤 대조한다. 이러면
        # 원문 텍스트와 정확히 같아야 한다.
        #
        # title 을 함께 세는 것이 중요하다. parse() 는 TITLE 텍스트를 title
        # 컬럼에 담고 text 에는 안 넣는다. text 만 세면 목차 제목만큼 모자라
        # 문서마다 1,200~2,600자가 빠진 것처럼 보인다.
        got_sec = re.sub(r"\s+", "",
                         "".join((s["title"] or "") + (s["text"] or "")
                                 for s in secs).replace(CELL_SEP, ""))
        if want_txt and abs(len(got_sec) - want_txt) / want_txt > KEEP_TOL:
            keep_bad.append((d["corp_name"], d["report_nm"][:24], want_txt,
                             len(got_sec),
                             round((len(got_sec) - want_txt) / want_txt * 100, 1)))

        # 2 구조 · 4 독립 대조
        #
        # 조각 수는 TITLE 수와 같거나, 파일 수만큼 더 많다. 파일마다 제목 없는
        # 맨 앞 조각(표지·목차)이 하나씩 생기기 때문이다. 그 조각은 내용이
        # 없으면 안 만들어지므로 범위로 본다.
        n_sec = len(secs)
        if not (n_re <= n_sec <= n_re + len(files)):
            struct_bad.append((d["corp_name"], d["report_nm"][:24],
                               "TITLE 수 불일치", n_re, n_sec))

        # 3 분포
        top = max(s["char_len"] for s in secs)
        if tot and top / tot > SKEW:
            t = next(s["title"] for s in secs if s["char_len"] == top)
            skew_list.append((d["corp_name"], d["report_nm"][:24], t[:24],
                              round(top / tot * 100, 1), tot))

        # 5 깨진 표
        if n_broken:
            broken_docs.append((d["corp_name"], d["report_nm"][:24], n_broken))

        # 6 파서 오류
        if n_err:
            err_docs.append((d["corp_name"], d["report_nm"][:24], n_err))

        # 7 내용 일치
        if want_txt != len(got_txt):
            text_bad.append((d["corp_name"], d["report_nm"][:24],
                             want_txt, len(got_txt)))

    print(f"── 문서 {n:,}건 검증\n")

    print(f"1 보존      원문 텍스트와 조각 텍스트가 어긋난 문서 {len(keep_bad):,}")
    for x in keep_bad[:8]:
        print(f"      {x[0]:<14}{x[1]:<26}원문 {x[2]:>10,} · 조각 {x[3]:>10,} · {x[4]:+.1f}%")
    fail += len(keep_bad)

    print(f"\n2 구조      TITLE 수와 조각 수가 어긋난 문서   {len(struct_bad):,}")
    for x in struct_bad[:8]:
        print(f"      {x[0]:<14}{x[1]:<26}{x[2]:<14}원문 {x[3]:>4} · 조각 {x[4]:>4}")
    fail += len(struct_bad)

    print(f"\n5 깨진 표    표 안에 TITLE 이 있는 문서        {len(broken_docs):,}")
    for x in broken_docs[:8]:
        print(f"      {x[0]:<14}{x[1]:<26}깨진 표 {x[2]}개")
    fail += len(broken_docs)

    print(f"\n6 파서 오류  파싱 중 오류가 난 문서            {len(err_docs):,}")
    for x in err_docs[:8]:
        print(f"      {x[0]:<14}{x[1]:<26}오류 {x[2]}건")
    fail += len(err_docs)

    print(f"\n7 내용 일치  원문 텍스트와 파싱 결과가 다른 문서 {len(text_bad):,}")
    for x in text_bad[:8]:
        print(f"      {x[0]:<14}{x[1]:<26}원문 {x[2]:>10,} · 파싱 {x[3]:>10,}"
              f" · 차이 {x[2]-x[3]:>+9,}")
    fail += len(text_bad)

    print(f"\n3 분포      한 절이 {int(SKEW*100)}% 초과   {len(skew_list):,}건  [검토 목록]")
    for x in sorted(skew_list, key=lambda v: -v[3])[:12]:
        print(f"      {x[0]:<14}{x[1]:<26}{x[2]:<26}{x[3]:>6.1f}%  총 {x[4]:>10,}")
    if skew_list:
        print("\n      이 목록이 비어 있지 않으면 반드시 표본을 열어 확인한다.")
        print("      원래 긴 절일 수도 있고 구조가 무너진 것일 수도 있다.")
        print("      둘은 열어보기 전에 구분되지 않는다.")

    print(f"\n{'통과' if fail == 0 else f'실패 {fail}건'}"
          + (f" · 검토 필요 {len(skew_list)}건" if skew_list else ""))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    lim = smp = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            lim = int(a.split("=")[1])
        elif a.startswith("--sample="):
            smp = int(a.split("=")[1])
    sys.exit(main(limit=lim, sample=smp))
