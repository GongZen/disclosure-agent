"""CLOVA Studio 임베딩 호출.

키는 .env 에서 읽는다. 코드에 넣지 않는다. .gitignore 에 등록돼 있으나
그것만 믿지 않고 프로젝트 밖으로 옮길 수 있게 경로를 환경변수로도 받는다.

임베딩 모델은 대회 제약 대상이 아니다. 생성 모델만 HyperCLOVA X 로 제한된다.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: str | Path | None = None) -> dict:
    """.env 를 읽는다. 이미 환경변수에 있으면 그것을 우선한다."""
    p = Path(path or os.environ.get("CLOVA_ENV") or ROOT / ".env")
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    for k in ("CLOVA_API_KEY", "CLOVA_EMB_URL", "CLOVA_EMB_DIM"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


class Embedder:
    """임베딩 한 건씩 호출한다.

    CLOVA 임베딩 v2 는 한 번에 텍스트 하나만 받는다. 묶음 호출이 없어
    조각 수만큼 호출해야 하고, 그래서 호출 제한이 소요 시간을 정한다.

    실측에서 300건을 연속으로 던지니 앞 100건은 분당 295건이 나오다가
    429 가 쌓이면서 분당 88건으로 떨어졌다. 짧게 던지면 안 걸리고 오래
    던지면 걸린다. 고정 간격으로는 이 경계를 못 맞춘다. 그래서 스스로
    조절한다. 429 가 나오면 간격을 늘리고 연속으로 성공하면 줄인다.
    """

    # 간격 상한을 낮게 둔다. 실측에서 5초까지 올렸더니 분당 10건으로
    # 떨어졌다. 429 를 맞더라도 밀어붙이는 편이 빠르다. 거부된 요청은
    # 과금되지 않고 재시도가 곧 성공한다. 간격 0 으로 300건을 연속으로
    # 던졌을 때가 분당 88건으로 가장 빨랐다.
    #
    # 다만 이 조절만으로는 부족하다. 2026-09-03 에 서버가 응답 머리글로
    # 알려주는 실제 한도를 확인했다.
    #
    #     x-ratelimit-limit-requests   60       분당 60회
    #     x-ratelimit-limit-tokens     40000    분당 40,000 토큰
    #     x-ratelimit-reset-tokens     36s      이만큼 뒤에 다시 채워진다
    #
    # 묶이는 쪽은 토큰이다. 긴 조각은 한 건에 수천 토큰을 쓰므로 금방
    # 바닥난다. 바닥난 상태에서는 무엇을 던져도 60초가 지나기 전에는 다
    # 거부된다. 그래서 간격 조절과 별개로 남은 예산을 보고 기다린다.
    # 아래 _budget 과 _wait_budget 이 그 일을 한다.
    MIN_GAP = 0.0
    MAX_GAP = 1.0        # 요청 한도가 분당 60회라 1초보다 촘촘해도 소용없다

    def __init__(self, env: dict | None = None, timeout: int = 30):
        self.env = env or load_env()
        self.key = self.env.get("CLOVA_API_KEY", "")
        self.url = self.env.get("CLOVA_EMB_URL", "")
        self.timeout = timeout
        self.gap = 0.0            # 호출 사이 간격. 스스로 조절한다
        self.ok_streak = 0
        self.n_call = self.n_429 = 0
        # 서버가 알려준 남은 예산. 아직 한 번도 안 불렀으면 None 이다
        self.rem_tokens: int | None = None
        self.rem_reqs: int | None = None
        self.reset_at = 0.0       # 예산이 다시 채워지는 시각 (monotonic)
        self.n_wait = 0           # 예산이 없어 미리 기다린 횟수
        self.t_wait = 0.0         # 그렇게 기다린 시간의 합
        if not self.key or not self.url:
            raise RuntimeError(".env 에 CLOVA_API_KEY 와 CLOVA_EMB_URL 이 필요하다")

    def _slow_down(self):
        """429 를 만났다. 간격을 조금 늘린다."""
        self.gap = min(self.MAX_GAP, self.gap + 0.05)
        self.ok_streak = 0

    def _speed_up(self):
        """연속으로 성공했다. 빠르게 되돌린다."""
        self.ok_streak += 1
        if self.ok_streak >= 5 and self.gap > self.MIN_GAP:
            self.gap = max(self.MIN_GAP, self.gap - 0.05)
            self.ok_streak = 0

    @staticmethod
    def _secs(v: str) -> float:
        """'36s' · '1m30s' · '500ms' 같은 값을 초로 바꾼다."""
        v = (v or "").strip().lower()
        if not v:
            return 0.0
        m = re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", v)
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
        m = re.fullmatch(r"(\d+(?:\.\d+)?)ms", v)
        if m:
            return float(m.group(1)) / 1000
        try:
            return float(v)
        except ValueError:
            return 0.0

    def _budget(self, headers) -> None:
        """응답 머리글에서 남은 예산과 리셋 시각을 읽어 둔다."""
        try:
            rt = headers.get("x-ratelimit-remaining-tokens")
            rr = headers.get("x-ratelimit-remaining-requests")
            rs = (headers.get("x-ratelimit-reset-tokens")
                  or headers.get("x-ratelimit-reset-requests"))
            if rt is not None:
                self.rem_tokens = int(rt)
            if rr is not None:
                self.rem_reqs = int(rr)
            if rs:
                self.reset_at = time.monotonic() + self._secs(rs)
        except Exception:      # 머리글 형태가 바뀌어도 호출은 계속돼야 한다
            pass

    def _wait_budget(self, text: str) -> None:
        """예산이 모자라면 채워질 때까지 기다린다. 429 를 맞기 전에 피한다.

        토큰 수를 미리 알 수 없으므로 글자 수로 어림잡는다. 한국어는 보통
        글자 수보다 토큰이 적으므로 이 어림은 넉넉한 쪽이다. 넉넉하게 잡아
        조금 더 기다리는 편이, 부족하게 잡아 429 를 맞고 재시도를 다섯 번
        헛되이 쓰는 것보다 낫다.
        """
        if self.rem_tokens is None:
            return
        need = max(64, len(text))
        short = self.rem_tokens < need or (self.rem_reqs is not None
                                           and self.rem_reqs < 1)
        if not short:
            return
        left = self.reset_at - time.monotonic()
        if left <= 0:
            self.rem_tokens = self.rem_reqs = None      # 이미 채워졌을 것이다
            return
        time.sleep(min(65.0, left + 0.5))
        self.n_wait += 1
        self.t_wait += min(65.0, left + 0.5)
        self.rem_tokens = self.rem_reqs = None          # 다음 응답에서 다시 읽는다

    def embed(self, text: str, retry: int = 5) -> tuple[list[float] | None, str]:
        """(벡터, 상태) 를 낸다. 실패하면 벡터가 None 이고 상태에 이유가 담긴다."""
        if self.gap:
            time.sleep(self.gap)
        self._wait_budget(text)
        body = json.dumps({"text": text}).encode("utf-8")
        last = ""
        for i in range(retry):
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("Content-Type", "application/json")
            try:
                self.n_call += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._budget(r.headers)
                    d = json.loads(r.read().decode("utf-8"))
                # 응답 형태가 계정·버전에 따라 다를 수 있어 둘 다 본다
                vec = (d.get("result", {}) or {}).get("embedding") or d.get("embedding")
                if vec:
                    self._speed_up()
                    return vec, "ok"
                return None, f"형태 예상 밖: {str(d)[:120]}"
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")[:120]
                last = f"HTTP {e.code}: {raw}"
                if e.code == 429:
                    self.n_429 += 1
                    self._slow_down()
                    self._budget(e.headers)
                    # 예산이 다시 채워질 때까지 기다린다. 전에는 Retry-After 를
                    # 찾다가 없으면 최대 4초만 쉬었는데, CLOVA 는 그 머리글을
                    # 안 주고 리셋은 최대 60초다. 그래서 다섯 번을 다 헛되이
                    # 쓰고 조각을 실패로 넘겼다. 2026-09-03 에 분당 3.4건까지
                    # 떨어진 원인이 이것이다.
                    wait = e.headers.get("Retry-After")
                    if wait:
                        rest = self._secs(wait)
                    else:
                        rest = self.reset_at - time.monotonic()
                    if rest <= 0:
                        rest = min(4, 0.5 * (i + 1))
                    time.sleep(min(65.0, rest + 0.5))
                    self.rem_tokens = self.rem_reqs = None
                    continue
                if e.code >= 500:            # 서버 쪽 일시 오류
                    time.sleep(min(30, 2 ** i))
                    continue
                return None, last
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(10, 2 ** i))
        return None, last


def normalize(vec: list[float]) -> list[float]:
    """길이를 1로 맞춘다.

    CLOVA 임베딩은 정규화되지 않은 채로 온다. 실측에서 노름이 26.22였다.
    저장할 때 나눠 두면 코사인 유사도가 내적만으로 끝난다. 조회할 때마다
    나누면 조각 수만큼 계산이 반복된다.
    """
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec
