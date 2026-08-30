"""CLOVA Studio 임베딩 호출.

키는 .env 에서 읽는다. 코드에 넣지 않는다. .gitignore 에 등록돼 있으나
그것만 믿지 않고 프로젝트 밖으로 옮길 수 있게 경로를 환경변수로도 받는다.

임베딩 모델은 대회 제약 대상이 아니다. 생성 모델만 HyperCLOVA X 로 제한된다.
"""
from __future__ import annotations

import json
import math
import os
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
    MIN_GAP = 0.0
    MAX_GAP = 0.5

    def __init__(self, env: dict | None = None, timeout: int = 30):
        self.env = env or load_env()
        self.key = self.env.get("CLOVA_API_KEY", "")
        self.url = self.env.get("CLOVA_EMB_URL", "")
        self.timeout = timeout
        self.gap = 0.0            # 호출 사이 간격. 스스로 조절한다
        self.ok_streak = 0
        self.n_call = self.n_429 = 0
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

    def embed(self, text: str, retry: int = 5) -> tuple[list[float] | None, str]:
        """(벡터, 상태) 를 낸다. 실패하면 벡터가 None 이고 상태에 이유가 담긴다."""
        if self.gap:
            time.sleep(self.gap)
        body = json.dumps({"text": text}).encode("utf-8")
        last = ""
        for i in range(retry):
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("Content-Type", "application/json")
            try:
                self.n_call += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
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
                    # 서버가 대기 시간을 알려주면 그것을 따른다
                    wait = e.headers.get("Retry-After")
                    time.sleep(float(wait) if wait else min(4, 0.5 * (i + 1)))
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
