# 공시 Agent — 평가용 API 서버
#
# 재현 가능한 실행 환경을 하나로 묶는다. 실제 운영은 네이버 클라우드의
# Windows Server 에서 uvicorn 을 직접 띄우는 방식이고, 이 파일은 다른
# 환경에서 같은 결과를 내기 위한 것이다.
#
# 데이터는 이미지에 넣지 않는다:
#   assets/  주최측 배포 원문 5.3GB — 재배포할 수 없다
#   data/    빌드 산출물 5.6GB — 이미지에 넣으면 다루기 어렵다
# 둘 다 실행할 때 볼륨으로 붙인다.
#
#   docker build -t disclosure-agent .
#   docker run --rm -p 8000:8000 --env-file .env \
#              -v "$PWD/data:/app/data" -v "$PWD/assets:/app/assets" \
#              disclosure-agent

FROM python:3.12-slim

# kiwipiepy 와 numpy 는 휠로 설치되지만, 없을 때를 대비해 빌드 도구를 둔다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 꾸러미를 먼저 깐다. 코드만 바뀌었을 때 이 층을 다시 만들지 않는다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY data/eval/ ./data/eval/
COPY README.md env.example ./

# 열쇠는 이미지에 넣지 않는다. --env-file 이나 -e 로 넘긴다.
#   CLOVA_API_KEY · CLOVA_EMB_URL · CLOVA_EMB_DIM
# 서식은 env.example 에 있다.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "src.server:app", \
     "--host", "0.0.0.0", "--port", "8000"]
