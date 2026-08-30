# 인수인계

기준일 2026-08-30. 세션이 바뀌어도 여기서부터 이어서 일할 수 있게 쓴 문서다.

이 문서는 지금 상태만 담는다. 왜 그렇게 됐는지는 `docs/feedback/W*.md`,
무엇을 정했는지는 `DECISIONS.md`, 앞으로 할 일의 전체 지도는 `docs/PLAN.md` 에 있다.

---

## 30초 요약

```
데이터 계층    W2~W6 전부 적재 끝났다. 다시 만들 이유가 지금은 없다
검색 계층      W7 착수. 7층 중 2층까지 만들었다
성적           본문 질의 37건에서 절 1위 38% · 8위내 78%
지금 할 일     층 3 이후. 못 찾는 8건이 두 유형에 몰려 있다
돈             OpenAI 임베딩 약 52,900원 썼다. CLOVA 임베딩은 아직 안 했다
```

---

## 1. 데이터가 지금 어떤 상태인가

실측이다. `data/disclosure.db` 를 직접 세었다.

| 테이블 | 행 수 | 무엇인가 |
|---|---:|---|
| `document` | 4,204 | 공시 문서 한 건이 한 행 |
| `doc_relation` | 1,024 | 정정공시가 어느 원본을 고쳤는지 |
| `event_contract` | 1,169 | 단일판매·공급계약 |
| `event_major` | 598 | 주요사항보고 |
| `event_holding` | 1,083 | 대량보유·임원소유 |
| `fact_financial` | 38,893 | 재무제표 숫자 |
| `section` | 122,871 | 본문을 목차 단위로 자른 것 |
| `chunk` | 171,564 | section 을 검색 단위로 다시 자른 것 |

`chunk` 는 세 컬럼이 다 차 있다.

```
tokens        171,564 / 171,564    BM25 용 형태소
embedding_oa  171,564 / 171,564    OpenAI text-embedding-3-large 3,072차원
embedding           0 / 171,564    CLOVA. 아직 안 했다
```

### 다시 만들면 무엇이 드는가

되돌리기 비용이 다르므로 손대기 전에 본다.

```
section · chunk · tokens    공짜다. 시간만 든다 (합쳐 40분 남짓)
embedding_oa                약 52,900원. 다시 부으면 또 든다
fact_financial              공짜지만 골든 129건 재검증이 따라온다
```

`data/` 는 git 에 안 올린다. 원문에서 다시 만들 수 있기 때문이다. 다만
사람이 만든 것은 예외로 올린다. `data/eval` · `data/golden` · `data/manual` ·
`data/samples` · `data/terms/dictionary.csv` 가 그것이다. 이건 잃으면 복구가 안 된다.

---

## 2. W6 본문 계층 — 끝났다

1차로 만든 것을 전량 버리고 다시 만들었다. 원문 XML 이 표준 XML 이 아니어서
파서가 표를 통째로 삼키고 있었다. 자세한 경위는 `docs/feedback/W6.md`.

### 무엇을 고쳤나

`src/sanitize.py` 가 파싱 전에 원문을 교정한다. 원인이 셋이었다.

```
본문의 꺾쇠        <배틀그라운드> 를 태그로 읽는다      문서 1,008건 · 42,002개
속성 값의 따옴표    ENG=""Snow" 에서 태그가 통째 버려진다  문서 84건 · 손실 31~68%
파일 이어붙이기     루트가 여럿이라 감사보고서가 빠진다   210건
```

전수 1,051건 기준으로 파서 오류 1,008→0 · 깨진 표 468→0 · 글자 손실 84→0 이다.

### 검증 관문 넷

단계 사이마다 관문을 뒀다. 통과 못 하면 다음으로 안 간다. 전부 통과한 상태다.

```
python scripts/verify_section.py      7겹  보존·구조·분포·독립대조·깨진표·파서오류·내용일치
python scripts/verify_chunk.py        7겹  누락·보존·한도·표경계·헤더정합·참조·길이
python scripts/verify_tokens.py       4겹  빠짐·빈토큰·어휘검사·표본확인
python scripts/verify_embedding.py    6겹  빠짐·차원·노름·중복·이웃일관성·왕복
```

관문을 다시 돌리고 싶으면 위 네 줄이면 된다. 데이터를 안 바꾸므로 안전하다.

### 다시 만들어야 한다면

순서가 있다. 앞을 바꾸면 뒤는 전부 무효다.

```
python src/build_section.py --reset      →  verify_section
python src/build_chunk.py   --reset      →  verify_chunk
python src/build_tokens.py               →  verify_tokens
python src/build_emb_oa.py --set=1       →  verify_embedding      돈이 든다
```

임베딩은 10개 기업씩 7 묶음으로 나눠 붓는다. 배정은 `data/eval/batches.csv` 에 있고
`scripts/make_batches.py` 가 만든다. 나눈 이유는 손실 제한이다. 잘못 부으면
전부가 아니라 1/7 만 잃는다.

---

## 3. W7 검색 — 7층 중 2층까지

### 층 구조

검색을 일곱 겹으로 나눠 놓고 순서대로 만든다.

```
층 1  질의 다듬기     질의에서 필터와 검색어를 가른다      만들었다
층 2  후보 좁히기     볼 만한 절을 짚는다                  만들었다
층 3  찾기            BM25 + 벡터 + RRF                    돌아간다. 조율 중
층 4  용어 확장       "실적" 을 "손익계산서" 로 넓힌다     아직
층 5  맥락 넓히기     찾은 조각의 앞뒤를 붙인다            아직
층 6  답 만들기       HyperCLOVA X 로 생성                 W8
층 7  검증            근거가 실제로 있는지 본다            W8
```

### 층 1 — `src/query.py`

질의 문자열을 받아 구조를 낸다. 규칙만 쓴다. LLM 을 안 부른다.

```python
Query(raw, corps, sectors, markets, years, dates,
      subtype, doc_group, intents, terms, search_text)
```

평가 질의 28개 중 27개에서 대상 기업을 뽑았다. 못 뽑은 하나는 기업을
안 밝힌 질의였다.

`STOP` 은 검색어로 쓰지 말 낱말 목록이다. "알려줘" 의 "알리" 같은 것이다.
갈래별로 켜고 끄며 성적을 잴 수 있다. `scripts/eval_body.py --mode=stop`.

### 층 2 — `data/eval/pathmap.csv`

절마다 그 절을 가리키는 낱말을 데이터에서 뽑아 뒀다. 44행이다.

```
III/6   배당에 관한 사항      배당성향 40.9 · 배당수익률 41.2 · 결산배당 35.4
VIII/2  임원의 보수 등        보수총액 · 퇴직소득 · 등기이사
II/1    사업의 개요           부문 3.8                          이것뿐이다
```

점수는 `(그 절에 나오는 비율) ÷ (전체 절에 나오는 비율)` 이다. 크면 그 절에서만
쓰이는 낱말이다. 질의가 아니라 절 본문에서 뽑았다. 평가 질의를 보고 만들면
그 질의에만 맞춰지기 때문이다. `scripts/build_pathmap.py` 가 만든다.

짚은 경로의 조각을 앞으로 당기기만 하고 후보에서 빼지는 않는다. 잘못 짚으면
정답을 버리게 되기 때문이다.

### 성적

`python scripts/eval_body.py --mode=path` 로 잰 값이다. 사례 37건.

```
              1위      3위내    8위내
경로 없음     12/37    22/37    29/37
경로 적용     14/37    23/37    29/37
```

절 단위로 채점한다. 조각은 찾기 위한 단위이고 답을 주는 단위는 절이기 때문이다.
같은 절의 다른 조각이 걸려도 맞은 것으로 센다.

### 못 찾는 8건

두 유형에 몰려 있다. 무작위가 아니다.

```
1. 사업의 개요               대표 낱말이 "부문" 하나뿐이다. 내용이 일반적이어서
                             그 절만의 낱말이 안 나온다
IV. 이사의 경영진단 및 분석의견  "실적 변화" 같은 질의가 절 이름과 안 겹친다
무형자산 주석                 "전속 연예인" 이 회계 계정 이름과 멀다
```

앞의 둘은 층 4 용어 확장으로 잡을 수 있다고 본다. 확인된 것은 아니다.

---

## 4. 확정하지 않은 값

바꿔도 되는 값이다. 고정하지 않은 이유가 각각 있다.

| 값 | 지금 | 어디 | 왜 안 고정했나 |
|---|---|---|---|
| RRF 가중치 | 1:1 | `scripts/eval_body.py` `W_DEFAULT` | 사례 9개에서는 2:1 이 나았는데 37개로 늘리니 1:1 이 됐다. 한두 건에 뒤집힌다 |
| `PATH_MIN` | 15.0 | `src/retrieval.py` | 경로를 짚을 확신의 문턱. 낮추면 오탐, 높이면 안 짚는다 |
| `PATH_BOOST` | 20 | `src/retrieval.py` | 짚은 경로를 몇 위까지 당길지 |
| `SHARE_MAX` | 0.40 | `src/terms.py` | 조각 토큰에서 뺄 기준 |
| `MIN_SCORE` | 3.0 | `scripts/build_pathmap.py` | 대표 낱말로 칠 점수 하한 |

가중치는 8위내를 우선해서 골랐다. 생성 모델에 조각 여럿을 넣으므로 1위 적중보다
넓게 담는 쪽이 낫다고 봤다. 1위 적중만 보면 1:5 가 낫다 (18/37).

---

## 5. 다음에 할 일

우선순위 순이다.

```
1  층 4 용어 확장
     "실적"        → 손익계산서 · 매출액 · 영업이익
     "전속 연예인"  → 무형자산
   못 찾는 8건 중 몇을 잡는지로 판정한다

2  사람이 만드는 2겹 경로표
   지금 pathmap 은 데이터에서 뽑은 것이라 II/1 처럼 특징 없는 절을 못 짚는다
   목차가 70개사 전부 같으므로 사람이 절과 낱말을 직접 이어 붙일 수 있다
   사용자가 제안한 방향이다

3  층 5 맥락 넓히기
   찾은 조각의 앞뒤를 붙여 생성 모델에 넘긴다

4  W8 답변 생성
   여기서 처음 HyperCLOVA X 를 쓴다

5  CLOVA 임베딩
   94시간 · 약 50,265원. 대회 규칙상 생성 모델만 CLOVA 강제이고
   임베딩은 제약 대상이 아니다. 해야 하는지부터 판단이 필요하다
```

---

## 6. 미결 위험

```
risk A   sanitize 를 corpus.read_raw() 에 넣을 것인가
           지금은 section 경로에서만 쓴다
           넣으면 재무 추출 경로도 바뀌어 fact_financial 38,893행과
           골든 129건을 다시 검증해야 한다
           안 넣으면 두 경로가 서로 다른 원문을 보는 상태가 남는다

risk B   감사보고서 415건을 별도 문서로 등록할 것인가
           지금은 같은 doc_id 아래 이어 담았다. section 에 src_file 로 출처를 남겼다
           되돌릴 수 있는 선택이다

D2       설비투자의 범위. capex 로 담겨 있어 연간 총액과 개별 공시 중
         무엇을 쓸지만 정하면 된다
D5       기간 해석. "최근" 이 몇 년인가
```

---

## 7. 함정 — 다시 밟지 말 것

겪은 것만 적는다. 자세한 경위는 `docs/feedback/W6.md` · `W7.md`.

```
측정 코드를 여럿 두지 마라
  스크립트 셋이 서로 다른 숫자를 냈다. 설정을 바꿔도 성적이 안 변하는 일까지 겪었다
  지금은 src/retrieval.py 가 실행을, scripts/eval_body.py 가 평가를 맡는다
  새 평가 스크립트를 만들지 말고 --mode 를 늘려라

같은 처리를 두 곳에서 하지 마라
  query.py 가 "보고서" 를 정규식과 STOP 두 곳에서 지우고 있었다
  STOP 을 바꿔도 효과가 없어 원인을 못 찾았다

검증이 통과했다고 맞는 것이 아니다
  1차 산출물은 검증을 다 통과했는데도 표를 통째로 잃고 있었다
  검증은 아는 유형만 잡는다

목차 조각을 후보에 넣지 마라
  절 제목을 나열한 조각이라 어떤 질의와도 낱말이 겹친다
  벡터 1위(0.7274)를 차지해 정답을 11위로 밀어냈다
  retrieval.SKIP_TITLE 이 뺀다

float32 저장 오차를 결함으로 오해하지 마라
  왕복 유사도가 1.0 이 아니다. 표본 40건에서 최소 0.9971 이었다
  기준은 0.99 다. 벡터가 실제로 뒤바뀌면 0.5 아래로 떨어진다
```

---

## 8. 자주 쓰는 명령

```bash
# 검색 성적
python scripts/eval_body.py --mode=weights   # RRF 가중치별
python scripts/eval_body.py --mode=stop      # STOP 갈래별
python scripts/eval_body.py --mode=path      # 경로 필터 켜고 끄고
python scripts/eval_body.py --mode=show      # 검색 결과를 눈으로 본다

# 관문
python scripts/verify_section.py
python scripts/verify_chunk.py
python scripts/verify_tokens.py
python scripts/verify_embedding.py

# 상태
python scripts/emb_status.py                 # 임베딩 진행률
```

`.env` 에 `OPENAI_API_KEY` 와 CLOVA 키가 있다. git 에 안 올라간다.

---

## 9. 사람이 만든 자산 — 잃으면 복구가 안 된다

```
data/EVALSET_QUESTION.md      사용자가 만든 평가 질의
data/EVALSET_SOURCE.md        그 질의의 정답 위치
data/eval/body_cases.csv      위 둘에서 옮긴 채점용 사례 37건
data/eval/batches.csv         임베딩 7 묶음 배정
data/golden/                  재무 골든 데이터셋 129건
data/manual/pdf_facts.csv     PDF 에서 사람이 옮긴 값
data/terms/dictionary.csv     회계 용어 사전 12,750개
```

전부 git 에 올라간다. `.gitignore` 에서 예외로 뒀다.
