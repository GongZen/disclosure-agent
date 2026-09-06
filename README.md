# 공시 Agent

제10회 2026 미래에셋증권 AI Festival 출품작.
공시 데이터를 기반으로 자연어 질의에 검색·분석·설명으로 답하는 AI Agent.

예선 마감 2026-09-06 · 평가 기간 서버 상시 가동 09.07 ~ 09.20

## 제출물은 `제출물/` 폴더에 정리해 두었다

과제가 요구한 예선 제출물 3종이 각각 어디 있는지 `제출물/README.md` 에 적었다.

| 제출 항목 | 어디에 있나 |
|---|---|
| ① 소스코드 | 이 저장소 전체. `src/` · `Dockerfile` · `requirements.txt` · 이 문서 |
| ② 기술 제안서 | `제출물/기술제안서.pdf` |
| ③ 평가용 API 서버 정보 | `제출물/API_서버정보.md` · 전체 명세는 `docs/API.md` |

소스코드는 `제출물/` 로 옮기지 않았다. 옮기면 서버와 `Dockerfile` 의 실행
경로가 깨지고, 복사해 두면 같은 코드가 두 벌이 되어 어느 것이 실제로 도는
코드인지 알 수 없게 된다.

자체 평가 결과는 `docs/EVAL_REVIEW.md` 에 있다. 평가 질의 28개를 직접 만들어
운영 서버에 심사와 같은 방식으로 던지고 모범 답안과 대조한 기록이다.

---

## 절대 금지 — 위반 시 실격 또는 규칙 위반

작업 전에 반드시 읽어야 한다. 상세는 `CLAUDE.md` 참조.

1. HyperCLOVA X 외 LLM 사용 금지 — 위반 시 평가 대상 제외
2. 제공 코퍼스 외 데이터 사용 금지 — 뉴스·리포트·위키·크롤링 전부
3. OpenDART 등 외부 공시 API 실시간 호출 금지
4. 공시에 근거 없는 미래 예측·투자 의견 생성 금지 — 확인 불가 시 "확인할 수 없음" 명시

---

## 폴더 구조

```
├── README.md          이 문서. 환경 구성 · 실행 명령어
├── Dockerfile         컨테이너 실행 환경
├── requirements.txt   파이썬 꾸러미
├── env.example        열쇠 서식
├── src/               구현체
│   ├── server.py      평가용 API 서버 (FastAPI)
│   ├── answer.py      질의 → 검색 → 생성 → 검증 조립부
│   ├── retrieval.py   낱말 검색 · 의미 검색 · 매칭표
│   ├── query.py       질의 해석 (기업 · 연도 · 업종 · 검색어)
│   ├── facts.py       재무제표 표에서 값 직접 조회
│   ├── events.py      수시 · 거래소 · 지분 공시를 표에서 직접 조회
│   └── build_*.py     원문 → 데이터베이스 적재
├── scripts/           평가 · 점검 스크립트
├── 제출물/            제출물 3종의 위치 안내 · 기술 제안서 · API 서버 정보
├── docs/
│   ├── API.md         평가용 API 명세서 — 요청 · 응답 스키마
│   ├── EVAL_REVIEW.md 자체 평가지 28문항 대조 결과
│   ├── PIPELINE.md    질의 처리 단계 S1~S11
│   ├── SCHEMA.md      데이터베이스 구조 · 재구축 절차
│   ├── BRIEF.md       과제 요강 · 평가지표 · 제출물
│   └── DATASET.md     코퍼스 분석 보고서 (실측)
├── data/eval/         평가 질의 · 매칭표 · 절 스키마
├── DECISIONS.md       결정 로그 (추가만, 수정·삭제 금지)
├── CLAUDE.md          프로젝트 규칙 · 문서 라우팅
├── reference/         대회 배포 원본 자료
├── assets/            공시 원문 코퍼스 — 저장소에 없음. 아래 참조
└── data/corpus.db     빌드 산출물 5.6GB — 저장소에 없음
```

---

## 데이터 준비

`assets/`는 5.3GB라 저장소에 포함되지 않는다. 주최측 배포본을 직접 받아 아래 경로에 둔다.

```
assets/공시/공시/corpus/
├── README.md
├── data_filter.md
├── universe.csv
├── universe.xlsx
├── manifest.jsonl
└── raw/
    ├── periodic/  <법인명>/{접수번호}_{annual|half|quarter}_{연도}_{월}/*.xml
    ├── major/     <법인명>/{접수번호}/*.xml
    ├── exchange/  <법인명>/{접수번호}/*.xml
    └── holding/   <법인명>/{접수번호}/*.xml
```

배치 후 4,204건이 모두 열리는지 확인한다. 열리지 않으면 `docs/DATASET.md`의 한글 경로 정규화 항목을 볼 것.

### 원문은 읽기 전용

`assets/` 아래 파일은 어떤 경우에도 수정하지 않는다. 모든 산출물은 `data/`에만 만든다. 근거 공시를 표시해야 하므로 원문이 변형되면 그것은 더 이상 근거가 아니다.

---

## 데이터 요약

| 항목 | 값 |
|---|---|
| 기업 | 70개사 (KOSPI 61 / KOSDAQ 9) |
| 기간 | 2023-01-01 ~ 2026-03-31 |
| 문서 | 4,204건 / XML 4,616개 |
| 용량 | 5.56 GB |
| 정정공시 | 1,004건 |

즉시 알아야 할 함정 4가지는 `docs/DATASET.md` 30초 요약에 있다.

---

## 환경 구성

Python 3.12 기준.

```bash
pip install -r requirements.txt
```

열쇠는 저장소 뿌리의 `.env` 에 둔다. 코드에 직접 쓰지 않는다. `env.example`
을 복사해 값을 채운다.

```bash
cp env.example .env
```

무엇이 필요한지는 `env.example` 에 적혀 있다. 환경변수로 지정하면 `.env`
보다 그쪽을 먼저 본다. `.env` 는 `.gitignore` 가 막고 있어 저장소에
올라가지 않는다.

---

## 평가용 API 서버

네이버 클라우드 플랫폼(NCP)에서 운영한다. 요청·응답 스키마와 사용법은
`docs/API.md` 에 있다.

```
End-point   http://211.188.57.111:8000
평가 창구    GET /answer?question_id={id}&question={평가 질의}
운영 기간    2026.09.07 ~ 09.20 상시
인증        없음
```

| 항목 | 값 |
|---|---|
| OS | Windows Server 2022 |
| 스펙 | s2-g3a (vCPU 2, Memory 8GB), 스토리지 30GB |
| 공인 IP · 포트 | 211.188.57.111 : 8000 |
| 방화벽 | Windows 인바운드 8000/TCP 허용 · NCP ACG 인바운드 8000 · 아웃바운드 443 |
| 자동 재시작 | Windows 작업 스케줄러 `DisclosureAgentAPI` · 부팅 시 자동 실행 |

무인 재부팅으로 서버가 스스로 올라오는 것을 확인했다. 로그인 없이 외부에서
`/health` 가 응답한다.

기동할 때 형태소 분석기와 기업 마스터를 미리 올린다. 그래서 뜨는 데 10초
남짓 걸리고, 대신 첫 질의부터 정상 속도가 나온다.

### 응답 시간

2026-09-06 실측. 노트북에서 서버로 외부 호출한 값이다.

| 질의 성격 | 예시 | 걸린 시간 |
|---|---|---|
| 정형 숫자 | 삼성전자의 2025년 주당 현금배당금은 얼마인가 | 2.3초 |
| 공시 조회 | 삼성전자가 2025년 7월 28일 공시한 공급계약의 계약금액은 | 3.5초 |
| 정성 서술 | SK하이닉스의 배당 정책을 알려줘 | 9.5초 |
| 살아 있는지 | `/health` | 0.12초 |

자체 평가지 28문항을 잇달아 던졌을 때 평균 8.4초, 최대 31.0초였다.

대부분이 HyperCLOVA X 가 답을 만드는 시간이다. 검색은 0.2초 안에 끝난다.
호출 측 시간 제한은 120초를 권한다.

### 직접 띄우기

저장소 뿌리에서 실행한다. `.env` 와 `data/corpus.db` 가 있어야 한다.

```bash
pip install -r requirements.txt
cp env.example .env          # 값을 채운다
python -m uvicorn src.server:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` 이 있어야 바깥에서 들어올 수 있다. 빼면 그 컴퓨터 안에서만
열린다. `data/corpus.db` 를 원문에서 새로 만드는 방법은 `docs/SCHEMA.md`
파일 구성에 있다.

컨테이너로 띄우려면 `Dockerfile` 을 쓴다.

```bash
docker build -t disclosure-agent .
docker run --rm -p 8000:8000 --env-file .env            -v "$PWD/data:/app/data" -v "$PWD/assets:/app/assets"            disclosure-agent
```

`data/` 와 `assets/` 는 이미지에 넣지 않고 붙인다. 합쳐 11GB 라 이미지에
넣으면 다루기 어렵다.

### 확인

```bash
curl "http://127.0.0.1:8000/health"
curl -G "http://127.0.0.1:8000/answer"      --data-urlencode "question_id=Q-001"      --data-urlencode "question=삼성전자의 주주환원 정책이 어떻게 되는지 알려줘"
```

### 엔드포인트

```
GET /answer     평가 창구. question · question_id · trace
GET /health     살아 있는지
GET /docs       사람이 보는 명세 화면. 여기서 바로 질의를 던져 볼 수 있다
GET /openapi.json   기계가 읽는 명세 원본
```

응답은 과제가 요구하는 네 필드다.

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고 · 추론 · 도구 사용 과정",
  "answer": "최종 생성 답변"
}
```

답을 못 하는 경우에도 HTTP 200 과 네 필드를 보낸다. 평가 중 한 질의가
실패해도 나머지가 이어지도록 하기 위해서다.

자세한 요청·응답 스키마와 `think_trace` 의 단계별 설명은 `docs/API.md` 에 있다.

## 진행 상태

| 단계 | 상태 |
|---|---|
| 데이터 실사 | 완료 — `docs/DATASET.md` |
| 데이터 구조화 · 데이터베이스 적재 | 완료 — 조각 171,564개 |
| 검색 파이프라인 | 완료 — 낱말 · 의미 · 매칭표 세 갈래 |
| 정형 표 조회 | 완료 — 재무 수치 · 수시 · 거래소 · 지분 공시 |
| 답변 생성 · 출처 표기 · 출력 검증 | 완료 |
| 서버 구축 · 자동 재시작 검증 | 완료 — 무인 재부팅 복귀 28초 |
| 자체 평가지 28문항 대조 | 완료 — `docs/EVAL_REVIEW.md` |

---

## 작업 규칙

결정은 `DECISIONS.md`에 남긴다. 추가만 하고 기존 항목은 수정·삭제하지 않는다. 번복은 새 항목으로 쓰고 이유에 무엇을 번복하는지 밝힌다.

수치를 인용할 때는 전수 측정인지 표본인지 추정인지 구분해 적는다.
