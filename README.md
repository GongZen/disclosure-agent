# 공시 Agent

제10회 2026 미래에셋증권 AI Festival 출품작.
공시 데이터를 기반으로 자연어 질의에 검색·분석·설명으로 답하는 AI Agent.

예선 마감 2026-09-06 · 평가 기간 서버 상시 가동 09.07 ~ 09.20

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
├── CLAUDE.md          프로젝트 규칙 · 문서 라우팅
├── DECISIONS.md       결정 로그 (추가만, 수정·삭제 금지)
├── MEMO.md            아이디어 · 브레인스토밍 원본
├── docs/
│   ├── BRIEF.md       과제 요강 · 평가지표 · 제출물 · CLOVA Studio 스펙
│   └── DATASET.md     코퍼스 분석 보고서 (실측)
├── reference/         대회 배포 원본 자료
├── assets/            공시 원문 코퍼스 — 저장소에 없음. 아래 참조
└── data/              생성 산출물 — 저장소에 없음
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

CLOVA Studio API 키는 환경변수로 둔다. 코드에 직접 쓰지 않는다.

```powershell
[Environment]::SetEnvironmentVariable("CLOVA_API_KEY", "발급받은키", "Machine")
```

---

## 평가용 API 서버

네이버 클라우드 플랫폼(NCP)에서 운영한다.

| 항목 | 값 |
|---|---|
| OS | Windows Server 2022 |
| 스펙 | s2-g3a (vCPU 2, Memory 8GB), 스토리지 30GB |
| 포트 | 80 (HTTP) |
| 자동 재시작 | 작업 스케줄러 `DisclosureAgentAPI` |

### 실행

```powershell
cd C:\app
python -m uvicorn main:app --host 0.0.0.0 --port 80
```

### 엔드포인트

```
GET /answer?question_id={id}&question={질의}
```

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고 · 추론 · 도구 사용 과정",
  "answer": "최종 생성 답변"
}
```

자동 생성 문서는 `/docs`, API 명세서는 `/openapi.json`에서 확인한다.

---

## 진행 상태

| 단계 | 상태 |
|---|---|
| 데이터 실사 | 완료 — `docs/DATASET.md` |
| 서버 구축 · 파이프라인 관통 | 완료 |
| 자동 재시작 검증 | 완료 |
| 데이터 구조화 | 진행 예정 |
| 검색 · 답변 파이프라인 | 미착수 |

---

## 작업 규칙

결정은 `DECISIONS.md`에 남긴다. 추가만 하고 기존 항목은 수정·삭제하지 않는다. 번복은 새 항목으로 쓰고 이유에 무엇을 번복하는지 밝힌다.

수치를 인용할 때는 전수 측정인지 표본인지 추정인지 구분해 적는다.
