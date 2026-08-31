"""section 을 임베딩 한도에 맞춰 조각으로 나눈다.

한도를 넘는 것만 나눈다. 대부분의 section 은 그대로 한 조각이 된다.

나눌 때 표 경계를 지킨다. 머리글과 숫자가 갈리면 어느 항목의 값인지 알 수
없게 된다. 표 하나가 한도를 넘으면 그것만 잘라서 담는다.

작은 표들을 한도까지 이어 붙이는 이유가 있다. 표마다 나누면 조각이 20만 개가
되고 중앙값이 137자다. "매출채권 │ 622,027,314,668" 만 떼어 놓으면 어느 기업
어느 해인지 알 수 없어 검색이 오히려 어려워진다. 한도까지 묶으면 2만 4천 개다.
묶어서 생기는 희석은 헤더로 완화한다.
"""
from __future__ import annotations

# 임베딩 한도는 8,192 토큰(약 13,107자)이나 그보다 훨씬 작게 잡는다.
#
# 호출 제한이 건수가 아니라 토큰량 기준으로 걸린다. 조각 크기별 429 발생률을
# 재보니 이렇게 갈렸다.
#
#     100~500자     15/15 성공 · 429 0건  · 분당 423건
#     5천~1만2천자    3/15 성공 · 429 12건 · 분당  20건
#
# 큰 조각 하나가 작은 조각 서른 개보다 오래 걸린다. 한도를 낮추면 조각이
# 늘지만 전체 시간은 줄어든다. 12,000자면 30.5시간이고 5,000자면 14.5시간이다.
# 2,000자까지 낮추면 조각이 25만 개가 되어 26.1시간으로 되레 늘어난다.
LIMIT = 5_000
TOKEN_PER_CHAR = 0.625


def est_tokens(s: str) -> int:
    return int(len(s) * TOKEN_PER_CHAR)


def make_header(corp: str, report: str, path: str, title: str) -> str:
    """조각 앞에 붙일 맥락.

    조각만 떼어 놓으면 어느 기업 어느 시점의 이야기인지 알 수 없다.
    검색에도 쓰이고 답변의 근거 표시에도 쓰인다.
    """
    parts = [x for x in (corp, report, path, title) if x]
    return " · ".join(parts)


def split(text: str, limit: int = LIMIT) -> list[str]:
    """한도를 넘는 본문을 나눈다.

    한도를 억지로 채우지 않는다. 3,000자 표와 4,000자 표가 이어져 있으면
    합쳐서 7,000자가 되므로 끊는다. 결과는 3,000자 조각과 4,000자 조각
    둘이다. 표 경계를 지켜야 머리글과 숫자가 안 갈린다.

    build_section 이 표를 행마다 줄바꿈해 담으므로 줄 단위를 지키면
    표의 행도 안 갈린다. 큰 표가 여러 조각으로 나뉠 때는 머리글을 반복해
    붙인다. 두 번째 조각부터 머리글이 없으면 무엇의 값인지 알 수 없다.
    """
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    out, cur, n = [], [], 0

    def flush():
        nonlocal cur, n
        if cur:
            out.append("\n".join(cur))
            cur, n = [], 0

    # 표의 머리글. 표마다 다르므로 따라가며 갱신한다.
    #
    # 앞 5줄에서만 찾던 때는 section 이 문장으로 시작하면 머리글을 못 찾았고,
    # 표가 여럿이면 첫 표의 머리글을 뒤 표에 붙였다. 실측에서 표로 시작하는
    # 조각 44,289개 중 13,324개(30.1%)가 머리글 없이 숫자로 시작했다.
    #
    # 셀 구분자가 없는 줄이 나오면 표가 끝난 것이고, 다시 셀 구분자가 있는
    # 줄이 나오면 그것이 새 표의 첫 행이다. 그 행을 머리글로 삼는다.
    head = ""
    in_table = False

    for line in lines:
        is_table = "│" in line
        if is_table and not in_table:
            head = line if len(line) < 400 else ""
        in_table = is_table
        w = len(line) + 1
        if w > limit:
            # 줄 하나가 한도를 넘는다. 셀 구분자로 더 잘라 본다.
            flush()
            if "│" in line:
                buf, m = [], 0
                for c in line.split("│"):
                    # 셀 하나가 한도를 넘으면 그것만 글자 수로 자른다.
                    # 안 그러면 그 셀이 통째로 한 조각이 되어 한도를 넘긴다.
                    # 한화솔루션 종속회사 목록이 한 셀에 10,954자였다.
                    if len(c) + 1 > limit:
                        if buf:
                            out.append("│".join(buf))
                            buf, m = [], 0
                        for j in range(0, len(c), limit):
                            out.append(c[j:j + limit])
                        continue
                    if m + len(c) + 1 > limit and buf:
                        out.append("│".join(buf))
                        buf, m = [], 0
                    buf.append(c)
                    m += len(c) + 1
                if buf:
                    out.append("│".join(buf))
            else:
                for j in range(0, len(line), limit):
                    out.append(line[j:j + limit])
            continue
        if n + w > limit and cur:
            flush()
            # 표 중간에서 끊겼다면 머리글을 반복한다.
            # 머리글을 붙여 한도를 넘게 되면 붙이지 않는다. 그러지 않으면
            # 조각이 머리글 길이만큼 한도를 초과한다.
            if head and is_table and line != head \
                    and len(head) + 1 + w <= limit:
                cur.append(head)
                n += len(head) + 1
        cur.append(line)
        n += w
    flush()
    return [x for x in out if x.strip()]
