# 데이터베이스 스키마

`data/corpus.db` (SQLite). 원문은 여기 들어오지 않는다. 파생 데이터만 담는다.

관련 문서 — 계층 설계는 `PLAN.md`, 조회 주체는 `PIPELINE.md`, 원본 실측은 `DATASET.md`, 시행착오는 `feedback/`

---

## 계층

| 계층 | 테이블 | 작업 | 상태 |
|---|---|---|---|
| 기준 | `company` `document` | W2 | 완료 |
| 관계 | `doc_relation` | W3 | 완료 |
| 사실 | `event_contract` `contract_item` | W4 | 완료 |
| 사실 | `event_major` `major_item` | W4 | 완료 |
| 사실 | `event_holding` `holding_item` | W4 | 완료 |
| 사실 | `fact_financial` | W5 | 완료 |
| 본문 | `section` `chunk` | W6 | 미착수 |

---

# 기준 계층 — W2 완료

주최측이 제공한 `universe.csv`와 `manifest.jsonl`을 그대로 옮긴 것이다. 추출이나 판단이 들어가지 않는다.

질의가 들어오면 여기서 대상을 좁힌다. 문서 안에 무엇이 있는지는 담지 않는다.

## company — 70행

`universe.csv` 원본.

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `corp_code` | TEXT PK | DART 고유번호 8자리. 선행 0 유지 |
| `stock_code` | TEXT | 종목코드 6자리. 선행 0 유지 |
| `corp_name` | TEXT NOT NULL | DART 공식 법인명. `raw/` 폴더명과 동일 |
| `listed_name` | TEXT | 거래소 통용 종목명. 질의에 등장하는 이름 |
| `corp_eng_name` | TEXT | 영문 법인명 |
| `market` | TEXT | KOSPI 61 / KOSDAQ 9 |
| `industry` | TEXT | 업종 대분류 8개 |
| `sector_no` `sector` | INTEGER TEXT | 섹터 20개 |
| `listing_date` | TEXT | 상장일. 정기공시 건수 해석에 필요 |
| `fiscal_month` | INTEGER | 결산월. 전 기업 12 |
| `market_cap` | INTEGER | 시가총액(억원) |
| `n_periodic` `n_major` `n_exchange` `n_holding` | INTEGER | 유형별 문서 수 |
| `note` | TEXT | 예외사항 |

### 인덱스

```sql
ix_company_corp_name    ON company(corp_name)
ix_company_listed_name  ON company(listed_name)
ix_company_sector       ON company(sector)
```

세 컬럼인 이유는 질의가 기업을 세 가지로 지칭하기 때문이다.

```
"현대자동차의 매출"           → corp_name
"현대차의 매출"               → listed_name
"2차전지 기업 A와 B 중"       → sector
```

별칭 테이블은 두지 않는다. 포스코·하이닉스 같은 시장 은어는 제공 데이터 어디에도 없고, 평가 규칙이 외부 데이터 추가를 금지하므로 평가 질의도 제공 데이터 안의 이름을 쓸 가능성이 높다.

## document — 4,204행

`manifest.jsonl` 원본에 두 컬럼을 더했다.

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `doc_id` | TEXT PK | `{doc_group}_{rcept_no}` |
| `corp_code` | TEXT NOT NULL FK | `company(corp_code)` 참조 |
| `corp_name` | TEXT NOT NULL | 조인 없이 쓰기 위한 중복 저장 |
| `doc_group` | TEXT NOT NULL | periodic 1,054 / exchange 1,469 / holding 1,083 / major 598 |
| `doc_subtype` | TEXT | annual · half · quarter · 단일판매공급계약체결 등. major는 전부 NULL |
| `major_kind` | TEXT | 파생. `report_nm`에서 추출. major 전용 |
| `category` | TEXT | 파생. D1 확정 후 채움. 현재 전부 NULL |
| `report_nm` | TEXT NOT NULL | 보고서명. 정정은 `[기재정정]` 접두 |
| `rcept_no` | TEXT NOT NULL | 접수번호 14자리 |
| `rcept_dt` | TEXT NOT NULL | 접수일 YYYYMMDD |
| `flr_nm` | TEXT | 제출인 |
| `base_year` `base_month` | INTEGER | 보고 기준기. 정기공시만. 나머지 3,150건 NULL |
| `is_correction` | INTEGER NOT NULL | 0 / 1. 1이 1,004건 |
| `file_path` | TEXT NOT NULL | 코퍼스 상대경로. NFC 표기 |
| `file_format` | TEXT | xml 4,201 / pdf+html 3 |
| `n_files` | INTEGER | 폴더 내 파일 수. 1건 3,991 / 2건 8 / 3건 205 |

### 인덱스

```sql
ix_doc_corp_group_year  ON document(corp_code, doc_group, base_year)
ix_doc_corp_dt          ON document(corp_code, rcept_dt)
ix_doc_subtype          ON document(doc_group, doc_subtype)
ix_doc_major_kind       ON document(major_kind)
ix_doc_correction       ON document(is_correction)
```

앞의 둘이 주력이다. 정기공시는 회계연도로 좁히고 수시공시는 접수일로 좁힌다. 이 차이가 D5에서 정할 사항이다.

### 파생 컬럼 두 개

`major_kind`는 주요사항보고서의 세부 유형이다. `doc_subtype`이 major 598건 전부 NULL이라 `report_nm`에서 뽑았다. 실측 형태가 셋이었다.

```
592건  주요사항보고서(전환사채권발행결정)
  5건  [첨부추가]주요사항보고서(유상증자결정)     대체 수집분
  1건  유상증자결정                               래퍼 없이 유형명만
```

앞의 대괄호 태그를 걷어내고, 래퍼가 있으면 괄호 안을, 없으면 전체를 쓴다. 598건 전건에서 28종이 추출된다.

문자열 파싱일 뿐 분류가 아니다. 이 28종을 어떤 범주로 묶을지는 D1에서 정하고, 그 결과가 `category`에 들어간다.

`category`는 지금 비어 있다. D1이 정해지면 채운다.

---

# 관계 계층 — W3 완료

문서와 문서를 잇는다. 정정본이 어느 원본을 고친 것인지, 계약 해지가 어느 계약을 끝낸 것인지다.

연결 실패도 사유와 함께 기록한다. 실패가 아니라 답변해야 할 사실이기 때문이다. 사유 값은 D7에서 정한 것을 쓴다.

여기서 담는 것과 담지 않는 것을 구분해 둔다.

```
담는다      어느 정정본이 어느 원본을 고쳤는가
            못 이었다면 그 사유가 무엇인가

담지 않는다  계약금액도 수주일자도 이 테이블에 없다
            값의 판본 선택은 W4·W5 가 event_contract·fact_financial 에서 정한다
```

원문에는 기재 오류가 있다. 자기 접수일을 원본 제출일로 적은 정정본, 계약 종료일보다 뒤인 수주일자, `2043-05-31` 같은 미래 날짜가 실제로 나왔다. 그 판정은 여기서 끝내고 결과만 남긴다. HyperCLOVA X 는 조각만 받으므로 이런 모순을 볼 근거가 없다. 판단을 앞으로 당겨야 재현되고 검토된다.

## doc_relation — 1,024행

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `id` | INTEGER PK | |
| `from_doc_id` | TEXT NOT NULL FK | 정정본 · 해지공시 |
| `to_doc_id` | TEXT FK | 원본 · 원계약. 미연결이면 NULL |
| `rel_type` | TEXT NOT NULL | `correction` 1,004 / `termination` 20 |
| `target_date` | TEXT | 원문에서 뽑은 원본 제출일 YYYYMMDD |
| `target_hint` | TEXT | 원본 서류명 · 계약상대방 등 보조 단서 |
| `resolved` | INTEGER NOT NULL | 0 / 1 |
| `unresolved_reason` | TEXT | `out_of_scope` / `ambiguous` / `extract_failed` |
| `candidates` | INTEGER | 되짚기가 도달한 원본의 수. 0이면 원본에 못 닿았다 |
| `chain_depth` | INTEGER | 연쇄 깊이. 0이면 직접 연결 |

용어는 `GLOSSARY.md` 고정 용어 절을 따른다. 정정본 · 원본 · 지목일 · 후보 · 연쇄 깊이 · 미연결 사유 여섯 개만 쓴다.

`candidates`가 후보 수가 아니라 원본 수라는 점에 주의한다. 컬럼명과 뜻이 어긋나 있다. W4에서 이름을 바꾼다.

### 인덱스

```sql
ix_rel_from    ON doc_relation(from_doc_id)
ix_rel_to      ON doc_relation(to_doc_id)
ix_rel_type    ON doc_relation(rel_type, resolved)
ix_rel_reason  ON doc_relation(unresolved_reason)
```

## 연결 방법

DART 정정공시는 원본을 접수번호로 지목하지 않는다. 정정 문서 1,002건을 조사한 결과 자기 접수번호 외에 14자리 접수번호가 나온 문서는 1건(0.1%)뿐이었다. 날짜와 서류명으로만 지목한다.

### 날짜 표기가 세 가지다

같은 필드 안에서도 문서마다 다르다.

```
2024년 03월 12일    한글
2023-01-27          하이픈
2022.06.28          점
```

월이 13이거나 일이 0인 값이 나오면 파싱 오류이므로 버린다. `20250008` 같은 값이 실제로 나왔다.

표기가 왜 셋인지, 어떻게 발견했는지는 `feedback/W3.md` 참조.

### 후보를 좁힌다

지목일에 그 기업 공시가 여러 건이면 후보가 여럿이 된다. 유형으로 좁힌다.

```
정기공시        doc_subtype (annual · half · quarter)
주요사항보고서   doc_subtype 이 전부 비어 있으므로 major_kind 를 쓴다
                같은 날 교환사채 발행과 자기주식 처분이 함께 공시되는 경우가 실제로 있다
```

좁힌 후보 중 정정본이 아닌 것이 원본이다. "최초제출일"은 정의상 원본의 제출일이므로, 같은 지목일에 정정본이 함께 있어도 원본을 택한다.

유형이 일치하는 후보가 하나도 없으면 미연결로 둔다. 필터를 포기하고 아무 문서나 잡으면 오연결이 생긴다. 틀린 이력을 답변에 내보내는 것보다 미연결이 낫다.

### 되짚기는 너비 우선이다

후보가 전부 정정본이면 각자의 지목일로 다시 간다. 어느 갈래가 맞는지 알 수 없으므로 한 갈래만 따라가지 않고 도달 가능한 원본을 전부 모은다. 갈래가 달라도 결국 같은 원본에 닿으면 답은 하나로 확정된다.

```
정정본 X  지목일 2024-05-13   후보 2건 (정정본 A, B)
            A  지목일 2024-03-11   후보 1건 (원본 C)
            B  지목일 2024-03-11   후보 1건 (원본 C)
          도달한 원본 {C} 하나 → 확정. 연쇄 깊이 1
```

연쇄 깊이 상한은 40이다. 상한은 순환 방지 장치가 아니다. 순환은 방문한 지목일을 기록해 막는다.

상한을 10으로 두었을 때 현대건설 3건이 깊이 10의 원본에 닿기 직전에 멈췄다. 정정을 열 번 넘게 거친 계약이 실제로 있다. 실측 최대 깊이는 12다.

### 후보에서 다른 계약을 뺀다

같은 날 여러 계약을 공시하는 기업이 있다. 유형 필터만으로는 무관한 계약의 원본이 후보로 남는다.

```
한화오션 정정본   계약명 FCS(필드제어설비) 1기   수주일자 2022-01-10
지목일 20231222 후보 3건
    정정  수상함 1척            수주 2018-12-06   → 코퍼스 밖
    정정  FCS(필드제어설비) 1기   수주 2022-01-10   → 코퍼스 밖   진짜 짝
    원본  잠수함 1척            수주 2023-12-22   ← 이것이 잡혔다
```

정답은 범위 밖인데 무관한 계약에 연결됐다. "원본을 우선한다"는 규칙만으로는 이런 경우를 못 거른다.

계약을 식별하는 항목으로 거른다. 체결계약명과 수주일자다. 둘 다 어긋나면 다른 계약으로 보고 후보에서 뺀다.

```
차륜형장갑차 4차양산   vs  차륜형장갑차 4차 양산사업   계약명만 다르다. 남긴다
FCS(필드제어설비) 1기  vs  잠수함 1척                둘 다 다르다. 뺀다
```

계약금액과 매출액대비는 쓰지 않는다. 정정으로 자주 바뀌고 서로 연동된다. 금액이 바뀌면 매출액대비도 함께 바뀌는데 이것을 둘로 세면 멀쩡한 정정을 다른 계약으로 판정한다.

이 규칙으로 오연결 23건이 사라졌다. 그만큼 연결 수는 줄고 범위 밖이 늘었다.

### 정정 전 값으로 확정

도달한 원본이 둘 이상이면 정정 헤더의 큰 숫자를 후보 원문과 대조한다. 정정 전 값은 원본에 그대로 들어 있고 정정 후 값은 어느 원본에도 없으므로, 일치 수가 많은 쪽이 원본이다.

정정 전 열만 분리하지 않고 헤더 전체를 쓴다. 정정 후 값이 섞여도 어느 후보와도 매칭되지 않아 판정을 흐리지 않는다.

이 방법으로 20건을 추가 확정했다.

### 대체 수집분은 PDF 를 읽는다

`file_format=pdf+html` 3건은 뷰어 HTML 에 문서 목록만 들어 있다. 텍스트로 뽑으면 350~570자뿐이고 본문이 없다. 내용은 PDF 에만 있다.

```
KB금융      사업보고서 (2025.12)   PDF 1,085쪽 + 뷰어 HTML 132KB
한화오션     분기보고서 (2024.03)   PDF   252쪽 + 뷰어 HTML 111KB
한화에어로스페이스 분기보고서 (2026.03)   PDF 1,695KB + 뷰어 HTML 3.6MB
```

앞의 둘이 정정본이고 정정 헤더가 PDF 5쪽에 있다. `corpus.read_pdf_text()`가 `pypdf`로 읽되 전부 읽지 않는다. 찾는 문자열이 나오면 멈춘다. 1,085쪽 문서에서 0.4초다.

W5 재무 추출에서도 이 경로가 필요하다. 세 번째 문서는 정정본이 아니지만 본문이 PDF에만 있다.

### 정정사항에 없는 항목으로 확정

값 대조는 쉼표가 두 번 이상 들어간 큰 숫자만 본다. 공시유보 상태로 냈던 계약은 원본의 금액이 `-` 라 대조할 숫자가 없다.

정정본은 "4. 정정사항"에 자기가 고친 항목을 적어둔다. 거기 없는 항목은 원본과 값이 같아야 한다.

```
LG에너지솔루션 정정본   기간 2027-01-01 ~ 2032-12-31
    후보1   2027-01-01 ~ 2032-12-31   일치
    후보2   2026-10-01 ~ 2030-12-31

정정사항  계약금액 · 매출액대비 · 유보사유 · 유보기한
          계약기간이 없으므로 후보1 이 원본이다
```

같은 건에서 정정사항의 정정 전 값도 후보1을 가리킨다. 유보기한 정정 전 값이 `2032-12-31`이고 후보1의 유보기한이 그것이다.

해지 공시에도 같은 방법을 쓴다. 해지 공시에는 정정사항 표가 없으므로 제외할 항목이 없고 본문의 계약기간이 그대로 대조에 쓰인다.

전부 일치를 요구하지 않고 일치 수가 가장 많은 후보를 택한다. 되짚기가 깊으면 중간 정정본들이 값을 바꿔놓아 원본과 어긋나는 항목이 남기 때문이다.

```
삼성E&A  정정본 종료일 2026-03-31
         후보A 2025-10-31 · 후보B 2027-04-30    어느 쪽과도 다르다
         그러나 계약명과 시작일은 후보A 와 같다    → 후보A
```

동점이거나 아무도 일치하지 않으면 미연결로 둔다.

이 방법으로 정정 7건과 해지 1건을 확정했다.

### 지목일을 못 믿으면 내용으로 찾는다

일부 정정본은 자기 접수일을 원본 제출일로 적어둔다. 파싱 오류가 아니라 원문이 그렇게 돼 있다.

```
에코프로비엠  접수 20241022   원문 "정정관련 공시서류제출일 2024-10-22"
              실제 원본은 20230523 에 있고 코퍼스 안에 있다
```

되짚기가 제자리를 돌면 지목일을 버리고 내용으로 찾는다. 같은 기업·같은 유형의 이전 원본 중 투자목적·결의일·계약명 같은 항목이 가장 많이 맞는 것을 택한다.

지목일 후보를 좁힐 때보다 가드를 세게 건다. 코퍼스 전체를 뒤지기 때문이다. 식별 항목이 어긋나지 않는 것으로는 부족하고 하나라도 실제로 일치해야 한다.

```
현대건설  정정본 파나마 메트로 3호선 공사
          잘못 붙은 원본 샤힌 프로젝트 공사 PKG1
          원본에 수주일자가 없어 어긋난 항목이 계약명 하나뿐이었다
```

계약명이 다르고 수주일자를 비교조차 못 하면 같은 계약이라는 근거가 없다. 어긋나지 않는 것과 맞는 것은 다르다.

식별 항목은 서식마다 다르다. 계약 서식은 체결계약명과 수주일자, 신규시설투자는 투자목적과 결의일이다.

두 항목 이상 맞고 2위와 차이가 나야 확정한다. 이 방법으로 3건을 이었다.

### 원본에 닿지 못하면 계약 체결일을 본다

되짚기가 원본에 하나도 닿지 못하는 경우가 있다. 정정본이 자기 접수일을 지목해 제자리를 도는 것이 대표적이다.

```
현대로템   접수 20240514   지목일 20240514
             후보 2건이 둘 다 정정본이고 둘 다 20240514 를 지목한다
```

이때 계약 체결일을 본다. 계약 체결 공시는 체결 시점에 내므로, 수주일자가 수집 시작 전이면 원본 공시도 범위 밖이다.

```
현대로템        수주일자 2021-07-14   →  원본이 코퍼스에 있을 수 없다
삼성E&A        수주일자 2020-11-19   →  같음
```

수주일자가 없으면 계약기간 시작일을 쓴다. 둘은 대개 같고, 다르면 시작일이 더 늦으므로 범위 밖 판정이 보수적으로 나온다.

원본에 하나라도 닿은 건은 건드리지 않는다. 그건 진짜 모호이지 범위 밖이 아니다.

이 규칙은 가정 하나에 기대고 있다. 공시유보였다가 나중에 해제되며 처음 공시되는 계약이라면 수주일자가 과거여도 최초 공시가 코퍼스 안에 있을 수 있다. 다만 그 경우 되짚기가 원본에 닿으므로 이 판정 대상이 되지 않는다.

이 방법으로 18건의 사유를 바로잡았다.

## 결과

| 구분 | 정정공시 | 계약 해지 |
|---|---|---|
| 대상 | 1,004건 | 20건 |
| 연결 성공 | 534건 | 6건 |
| 그중 연쇄 추적 | 42건 | 1건 |
| 그중 값 대조로 확정 | 20건 | — |
| 그중 필드 대조로 확정 | 7건 | 1건 |
| 그중 내용 검색으로 확정 | 3건 | 0건 |
| 원본이 범위 밖 | 470건 | 14건 |
| 원본 미확정 | 0건 | 0건 |
| 날짜 추출 실패 | 0건 | 0건 |
| 연결 가능분 기준 | 100.0% | 100.0% |

코퍼스에 원본이 있는 건은 전부 이었다. 남은 475건과 14건은 원본이 코퍼스에 없는 것이다.

연결 가능분은 범위 밖을 뺀 모수 기준이다. 코퍼스에 원본이 있는 것 중 몇 퍼센트를 이었는가다.

해지 연결도 정정과 같은 체인 추적을 쓴다. 해지 공시가 지목한 날짜에 정정본만 있는 경우가 있어, 그 정정본을 원계약으로 잡으면 "언제 체결한 계약인가"에 정정일자를 답하게 된다.

## 미연결의 정체

### 범위 밖 484건 — 구조적 불가

미연결은 전부 여기 모였다. 정정 470건과 해지 14건이다.

```
지목일이 2023-01-01 이전                    코퍼스에 처음부터 없다
되짚다 도달한 지목일에 같은 유형 문서가 없다     정정의 정정을 거슬러 올라가면 범위를 벗어난다
후보가 다른 계약이라 걸러졌다                 진짜 짝은 코퍼스 밖이다
되짚기가 제자리를 돌고 내용으로도 못 찾음        원본이 코퍼스에 없다
계약 체결일이 수집 시작 전이다                지목일을 못 읽어도 원본이 없음이 확정된다
```

다섯 경로 모두 "우리가 못 찾은 것"이 아니라 "데이터에 없는 것"이다.

이건 실패가 아니라 답변해야 할 사실이다. D7의 `out_of_scope`로 기록한다.

### 원본 미확정 0건

되짚기가 원본에 닿지 못하면 예전에는 `ambiguous`로 기록했다. 그러나 그건 "여러 후보 중 못 골랐다"는 뜻이므로 사실과 다르다.

지금은 지목일을 버리고 내용으로 찾아본 뒤, 그래도 못 찾으면 `out_of_scope`로 둔다. 깊이 상한에 걸려 멈춘 경우만 `ambiguous`로 남기는데 현재 해당 건이 없다.


### 날짜 추출 실패 0건

KB금융과 한화오션 2건이 여기 있었다. 뷰어 HTML 에 정정 헤더가 없어 구조적 한계로 판단했으나, PDF 를 읽으면 나온다. 앞의 연결 방법 참조.

원본 공시의 오타도 하나 살렸다. 미래에셋증권 문서에 `2025년 08년 28일`처럼 구분자가 잘못 쓰인 경우가 있어 `년·월·-·.`을 모두 받도록 했다.


---

# 사실 계층 · 계약 — W4 진행 중

문서 안의 값을 꺼내 담는다. 여기까지 오면 "삼성전자가 어떤 계약을 얼마에 땄나"에 SQL로 답할 수 있다.

## event_contract — 1,169행

거래소 계약 공시 전건이다. 체결 1,106 · 해지 20 · 신규시설투자 43.

```sql
event_contract
  id               INTEGER PK
  doc_id           TEXT   → document
  corp_code        TEXT   → company
  event_type       TEXT   contract | termination | investment
  form             TEXT   의무 | 자율 | 코스닥 | 해지 | 시설투자
  disclosure_type  TEXT   mandatory | voluntary

  title            TEXT   원문 표기 그대로
  title_norm       TEXT   정규화한 값. 같은 계약을 묶는 키
  category         TEXT   계약 구분 · 투자 구분
  counterparty     TEXT
  counterparty_rel TEXT
  region           TEXT

  amount_krw       INTEGER  계약금액 · 해지금액 · 투자금액
  amount_fixed     INTEGER  확정 계약금액.   코스닥만
  amount_cond      INTEGER  조건부 계약금액. 코스닥만
  base_amount      INTEGER  비교 기준액
  base_kind        TEXT     revenue | equity
  ratio_stated     REAL     공시에 적힌 비율
  ratio_calc       REAL     amount / base * 100
  ratio_match      INTEGER  둘이 맞는가

  start_date       TEXT   YYYYMMDD
  end_date         TEXT
  signed_at        TEXT   계약(수주)일자 · 해지일자 · 이사회결의일

  purpose          TEXT   투자목적.      시설투자만
  terminate_reason TEXT   해지 주요사유.  해지만
  hold_until       TEXT
  hold_reason      TEXT
  is_large_corp    INTEGER
  is_correction    INTEGER
```

### 인덱스

```sql
ix_ec_corp_signed  (corp_code, signed_at)   기업의 기간별 계약
ix_ec_corp_title   (corp_code, title_norm)  같은 계약의 판본을 모은다
ix_ec_type         (event_type)
ix_ec_doc          (doc_id)
ix_ec_amount       (amount_krw)
```

## 서식이 다섯 갈래다

같은 "단일판매·공급계약체결"인데 항목 이름이 다르다. 1,169건 전수를 열어 확인했다.

| 담을 값 | 의무 1,022 | 자율 58 | 코스닥 26 | 해지 20 | 시설투자 43 |
|---|---|---|---|---|---|
| 제목 | `- 체결계약명` | `- 세부내용` | `1. 판매ㆍ공급계약 내용` | `- 해지계약명` | `- 투자대상` |
| 금액 | `계약금액(원)` | `계약금액(원)` | `계약금액 총액(원)` | `해지금액(원)` | `투자금액(원)` |
| 기준액 | `최근매출액(원)` | `최근매출액(원)` | `최근 매출액(원)` | `최근매출액(원)` | `자기자본(원)` |
| 기준일 | `7. 계약(수주)일자` | `7. 계약(수주)일` | `8. 계약(수주)일자` | `6. 해지일자` | `5. 이사회결의일(결정일)` |

번호로 찾으면 안 된다. 같은 뜻의 항목이 서식마다 다른 번호를 달고 있다. 항목 이름으로 찾는다.

항목 이름 앞에는 번호나 하이픈이 붙는다. `- 체결계약명`의 하이픈을 빠뜨려 1,022건을 통째로 못 읽은 적이 있다.

## 판본마다 한 행이다

정정본이 원본을 대체하지 않는다. 한 계약이 최대 열두 번까지 정정되므로 그만큼 행이 생긴다.

```
2023-06-26  원본    계약금액 1,000억
2025-04-18  정정본  계약금액 1,200억
2025-07-08  정정본  계약금액 1,400억     → 세 행. doc_id 로 출처를 구분한다
```

담아두면 최신만 뽑는 것은 쉽지만 최신만 담으면 이력을 되살릴 수 없어서 이쪽을 골랐다. 같은 계약은 `title_norm`으로 묶거나 `doc_relation`으로 따라간다.

## 계약금액이 셋인 서식이 있다

코스닥 서식은 확정분과 조건부를 나눠 적는다. 조건부는 발주처의 추가 발주 같은 조건이 붙어 아직 확정되지 않은 금액이다.

```
확정 계약금액     8,486,086,000
조건부 계약금액   -
계약금액 총액(원) 8,486,086,000
```

총액만 답하면 안 들어올 수도 있는 돈이 포함된다. 세 값을 다 담아 답변에서 구분해 제시한다.

## 검산으로 걸러낸 것

공시가 스스로 적어둔 비율을 우리가 뽑은 두 값으로 재계산해 대조한다.

```
일치        1,099건
불일치          4건
검산 불가      66건    금액이 "-" 인 공시유보 건
일치율       99.6%
```

불일치 4건은 전부 원문 기재가 어긋난 것이다. 회사가 정정하면서 본문 일부 칸을 갱신하지 않았다.

```
삼성바이오로직스 20230925  정정사항 표는 11.97 인데 본문 비율이 11.79
현대건설       20260305  표는 5.71 인데 본문이 5.17
우리기술       20260210  확정은 고쳤는데 총액이 옛 값
LIG디펜스      20240207  정정본이 아닌데 어긋난다. 원인 미상
```

값을 고치지 않는다. 모든 답변에 근거 공시를 표시해야 하므로 우리가 고친 값은 근거와 어긋난다. `ratio_match`에 결과만 남겨 답변에서 밝힌다.

원문 오류는 이것 말고 둘 더 있다. 두산퓨얼셀 수주일자가 접수일보다 1년 뒤이고(W3에서 오타로 판명), 우리기술 건은 확정 + 조건부가 총액과 맞지 않는다.

## 값이 비어 있는 건

`amount_krw` 66건, `signed_at` 8건, `title` 6건, `base_amount` 1건이 비어 있다. 전부 원문에 `-`로 적혀 있음을 확인했다. 추출 실패가 아니라 회사가 쓰지 않은 것이고, D7의 `not_disclosed`에 해당한다.

대부분 공시유보다. 계약 상대방과의 비밀유지 약정 때문에 금액과 계약명을 가리고 나중에 재공시한다.

## contract_item — 24,558행

계약 공시 원문의 모든 항목을 이름 그대로 담는다. `event_contract` 컬럼은 자주 쓰는 축만 담으므로 나머지가 버려진다. `계약금ㆍ선급금 유무`, `대금지급 조건 등` 같은 것이 여기 있다.

```sql
contract_item
  id          INTEGER PK
  doc_id      TEXT   → document
  seq         INTEGER   문서 안 등장 순서
  item_name   TEXT      표의 항목 경로. "2. 계약내역 > 계약금액(원)"
  item_value  TEXT      원문 표기 그대로
```

---

# 사실 계층 · 주요사항보고서 — W4 완료

## event_major — 598행 · major_item — 78,967행

사건 종류가 28가지이고 유형마다 항목이 다르다. 컬럼으로 다 담을 수 없어 둘로 나눴다.

```
event_major   한 문서에 한 행. 자주 쓰는 축만 컬럼으로. 빠른 조회와 집계
major_item    한 문서에 여러 행. 원문의 모든 항목. 무엇이든 답할 수 있게
```

컬럼으로 담을 수 없는 이유가 둘이다.

```
고유 항목이 678개      그중 442개가 한 유형에만 나온다
같은 항목이 반복된다    한 문서에 성명 33회 · 지분(%) 33회 · 현지금융 39회
                     타법인 주식 양수 공시는 대상 법인마다 표가 한 벌씩 붙는다
```

`event_major`의 주요 컬럼이다.

```sql
event_major
  major_kind      28개 유형
  decided_at      이사회결의일(결정일)
  amount_krw      주된 금액. 유형마다 다른 항목에서 온다
  amount_src      그 금액이 어느 항목에서 왔는지
  use_facility · use_business · use_operation
  use_debt · use_acquire · use_other · use_total    자금조달의 목적 여섯 갈래
  check_ok        검산 결과
  shares_common · shares_other · shares_before · price_share
  disposal_purpose · method_market · method_block · method_otc · method_etc
  is_withdrawn · correct_reason                     철회·취소 여부와 사유
```

## 조회는 라우팅이 먼저다

`event_major`를 먼저 보고 부족하면 `major_item`으로 넘어가는 것이 아니다. 질의를 해석할 때 어디를 볼지 정해진다.

```
"2025년 자금조달 총액"    집계가 필요하다      →  event_major.amount_krw
"CB 를 발행한 기업"        유형으로 거른다      →  event_major.major_kind
"이 합병의 합병비율"       컬럼에 없는 항목     →  major_item
```

`major_item`이 하는 다른 일이 D7 판정이다. 컬럼이 비었을 때 이유를 가른다.

```
major_item 에 항목이 있고 값이 "-"      →  not_disclosed   회사가 안 썼다
major_item 에 항목 자체가 없다          →  not_disclosed   서식에 없는 칸이다
major_item 에 값이 있는데 컬럼이 비었다   →  extract_failed  우리가 놓쳤다
```

## 자금조달 유형에 공통 구조가 있다

유상증자와 사채류 네 유형이 자금 용도를 똑같이 여섯 갈래로 나눠 적는다. 이것이 검산 항목이 된다.

```
사채류        용도 합계 = 사채의 권면총액              102 / 102
유상증자      용도 합계 ≈ 신주 수 × 발행가액           52 / 52
자기주식      예정주식 × 주식가격 = 예정금액           157 / 157
                                                  합계 302건 전부 일치
```

유상증자에 근사 기호를 쓴 이유가 있다. 용도 합계는 발행제비용을 뺀 금액이라 조달금액보다 작다. LG씨엔에스 건이 1.0% 적었고 대표주관 3사가 붙은 대형 공모였다. 반대로 미세하게 큰 경우도 있는데 발행가액이 반올림된 값이라 곱셈이 근사치이기 때문이다. 그래서 −0.01% ~ +3%를 허용한다.

## 철회·취소는 공시유보와 다르다

값이 비어 있는 것이 같아 보여도 뜻이 반대다.

```
공시유보     지금은 안 밝히고 유보기한 뒤에 밝힌다      값이 나중에 나온다
철회·취소    그 증자·합병이 없던 일이 됐다             값이 영영 없다
```

12건을 찾았다. 정정사유에 철회·취소·해제·가처분이 들어간 건이다.

```
에스엠 20230306        신주 및 전환사채 발행금지가처분 인용 결정에 따른 계약 해제
OCI홀딩스 20240408     주식매매 및 현물출자계약 해제에 따른 유상증자 결정 철회
두산로보틱스 20240829   포괄적 주식교환 계약 해제
파마리서치 20250708     회사분할결정 철회
```

"자금조달 목적이 기재되지 않았습니다"로 답하면 틀린다. "이 유상증자는 철회되었습니다"가 맞다.

---

# 사실 계층 · 지분공시 — W4 완료

## event_holding — 1,083행 · holding_item — 1,530,018행

대량보유상황보고서다. 5% 룰 공시라고 부른다. 어떤 회사 주식을 5% 이상
가지면 신고해야 하고 이후 1% 이상 변동마다 다시 낸다.

앞선 둘과 다른 점이 둘이다. 문서 하나에 항목이 최대 1만 7천 개까지 나오고,
특별관계자 구간에 성명과 생년월일이 들어 있다.

```
문서 크기      중앙값 102KB · 최대 3MB
문서당 항목    중앙값 417 · 평균 1,413 · 최대 17,442
```

항목이 폭증하는 곳은 세부 변동내역이다. 외국 자산운용사가 보고하면 산하
펀드가 수백 개이고 펀드마다 한 줄씩 붙는다.

## section 으로 표의 역할을 남긴다

`holding_item` 에 `section` 과 `has_pii` 컬럼을 둔다.

```
change_detail   653,696 (개인정보)   세부 변동내역
holding_detail  339,671 (개인정보)   관계별 보유 상세
related_party   154,494 (개인정보)   특별관계자 명단
contract        114,220 (개인정보)   담보·대차 계약 당사자
loan             58,683              대출·담보 조건
reporter         49,271              보고자의 자산·부채
holding_total    48,158              변동내역 총괄표
summary          18,988              요약정보              ← 핵심
other             1,420 (0.1%)
```

개인 신상을 담은 항목이 128만 행으로 전체의 84% 다.

`section` 이 필요한 이유가 둘이다. 문서 하나가 HCX 입력 한도를 넘어
통째로 넣을 수 없고, 묻지 않은 개인 신상이 답변에 딸려 나가면 안 된다.

조회는 폴백이 아니라 라우팅이다. 질의를 해석할 때 어느 구간이 필요한지
정해지고, 그 구간만 꺼내 넘긴다. 넘기지 않은 것은 답변에 나올 수 없다.

```
지분을 얼마나 보유했나     summary · holding_total
특별관계자가 누구인가      related_party        ← 물었으므로 넘긴다
어느 펀드가 얼마를 샀나    change_detail
```

## 서식과 보유목적이 정확히 대응한다

```
일반 607건  =  경영권 영향 607건
약식 476건  =  단순투자 372건 + 일반투자 104건
```

서식이 목적에 따라 갈리기 때문이다. 경영권에 영향을 주려는 경우가 일반이고
단순투자거나 전문투자자면 약식이다. 그래서 일반 서식에는 보유목적 항목이
따로 없고 서식 자체가 목적을 말한다.

## 검산과 남은 것

```
보유주식수 ÷ 의결권있는 발행주식총수 × 100 = 보유비율
   일치 1,073 · 불일치 0 · 값 부족 10
```

값 부족 10건은 보유수와 비율이 모두 0이다. 지분을 전량 처분해 5% 밑으로
떨어졌을 때 내는 마지막 보고이고 원문에 0이 적혀 있다.

정식 산정식은 `[A+H / I+H-(E+F+G)] × 100` 이다. H 는 보유잠재주식이고
E·F·G 는 그중 교환사채권·증권예탁증권·기타다. 우리 검산은 근사식이라
허용 오차 0.05%p 를 둔다. 자세한 것은 `feedback/W4.md` 참조.

---

# 사실 계층 · 재무 — W5 완료

## fact_financial — 38,845행 · 문서 895 · 기업 70

정기공시 재무제표에서 뽑은 값. `item_code` 26종을 담는다.

```
fact_financial
    fact_id      PK
    doc_id       FK   어느 공시에서 나왔나. 근거 제시에 쓴다
    corp_code    FK
    item_code         total_assets · revenue · capex …  26종
    value             원 단위로 환산한 값. 기업 간 비교는 이것으로
    value_raw         표기된 그대로. 근거를 댈 때 원문과 맞춰야 한다
    unit_mult         환산 배수. 1 · 1000 · 1000000
    unit_label        원 · 천원 · 백만원
    fiscal_year       회계연도
    base_month        3 · 6 · 9 · 12. 어느 시점·어느 기간까지인가
    period_type       instant · annual · cumulative · quarter
    basis             연결 · 별도
    source            xbrl · table. 신뢰도가 다르다
    item_name         원문 계정 이름. 표 파싱일 때만
    tag               XBRL 태그. 태그 경로일 때만

    UNIQUE (doc_id, item_code, fiscal_year, base_month, period_type, basis)
    IDX  (corp_code, item_code, fiscal_year, base_month, basis, period_type)
```

## 담은 항목 26종

| 묶음 | item_code |
|---|---|
| 재무상태표 총계 | total_assets · total_liabilities · total_equity |
| 재무상태표 구분 | current_assets · noncurrent_assets · current_liabilities · noncurrent_liabilities |
| 대분류 예외 | held_for_sale_assets · held_for_sale_liabilities · financial_business_assets · financial_business_liabilities |
| 손익 | revenue · cost_of_sales · gross_profit · sga · operating_income · pretax_income · net_income |
| 현금흐름표 | cf_operating · cf_investing · cf_financing · capex |
| 금융업 | net_interest_income · net_fee_income |
| 보험 | insurance_result · insurance_revenue |

업종에 따라 수익 항목이 갈린다. D3 결정이다.

```
제조·서비스   revenue              63개 기업
은행지주      net_interest_income   5개 기업
보험          insurance_result      2개 기업
```

## 값을 뽑는 경로가 둘이다

```
태그 경로    ACODE 로 항목, ACONTEXT 로 좌표, ADECIMAL 로 단위를 안다
             판정할 것이 없어 실수도 없다

표 파싱      계정 이름으로 행을 찾고 columns() 로 열을, 제목 표에서 단위를 읽는다
```

문서가 회계 태그를 갖고 있는지로 고른다. 2023~2024년 문서에도 `ACODE` 가 있으나
그것은 `CRP_NM`(회사명) `EST_DT`(설립일) 같은 DART 서식 필드 코드다.
재무제표 본문에 회계 태그가 붙은 것은 562건이다.

태그가 있어도 표 파싱으로 넘기는 경우가 둘 있다.

```
ACONTEXT 없음   당기인지 전기인지, 누적인지 3개월치인지 모른다
ADECIMAL 없음   단위를 몰라 원 단위 환산이 안 된다
```

좌표가 빠지면 태그의 이점이 사라지므로 전부 읽어내는 표 파싱이 낫다.

## 당기 값만 담고 최종 정정본만 담는다

사업보고서는 한 문서에 3개년 열이 있다. 전기 값까지 담으면 2023년 값이
2024·2025·2026년 사업보고서 셋에 들어간다. 소급재작성 225건(21.4%)에서 그 값이
갈리고 근거 공시도 정해지지 않는다.

정정본이 여럿일 때는 최종 접수분만 담는다. 정정본 서식이 최초 제출일을 적게
되어 있어 세 번 정정하면 셋 다 원본을 가리킨다. `doc_relation` 으로는 원본
하나만 걸러진다.

## 검증

```
회계 항등식   자산총계 = 부채총계 + 자본총계        1,735건 어긋남 0
              매출액 − 매출원가 = 매출총이익         2,349건 어긋남 0

참고 지표     매출총이익 − 판관비 = 영업이익          88.08%
              유동 + 비유동 = 자산총계               99.30%
              유동 + 비유동 = 부채총계               99.75%
```

뒤의 셋은 기업의 표시 방식에 따라 어긋날 수 있어 실패로 세지 않는다. 판관비 밖에
기타영업비용을 따로 적거나, 유동·비유동 밖에 대분류를 더 두는 경우다.

두 경로 대조는 태그 있는 문서 474건에서 2,833쌍 전부 일치했다. 골든 데이터셋은
사람이 원문을 읽어 채운 22건 132행이고 그중 48건이 대조 대상이다.

---

# 검증

```bash
python scripts/verify_base.py
python scripts/verify_relation.py
python scripts/verify_contract.py
python scripts/verify_major.py
python scripts/verify_holding.py
```

`verify_base.py`는 11개 항목, `verify_relation.py`는 10개 항목, `verify_contract.py`는 10개 절을 본다. 행 수와 참조 무결성뿐 아니라 실제 조회 형태로 시험한다.

## verify_base.py

| 항목 | 확인하는 것 |
|---|---|
| 1 행 수 | 70 / 4,204 |
| 2 참조 무결성 | 고아 문서, `doc_id` 중복 |
| 3 선행 0 보존 | `corp_code` 8자리, `stock_code` 6자리 |
| 4 S2 조회 | 현대차 → 현대자동차, 2차전지 → 3개사 |
| 5 S3 조회 | 삼성전자 2025년 사업보고서 1건 |
| 6 분포 | `DATASET.md` 실측값과 대조 |
| 7 `major_kind` | 598/598 추출, 28종 |
| 8 결측 | 있어야 정상인 것과 아닌 것 구분 |
| 9 인덱스 | 8개 존재 |
| 10 원문 접근 | 표본 200건 경로 해석 |
| 11 원본 대조 | manifest와 행 수·`doc_id` 집합 일치 |

8번이 특히 중요하다. `doc_subtype` 598건 결측과 `base_year` 3,150건 결측은 정상이고, 이걸 오류로 잡으면 안 된다.

## verify_relation.py

| 항목 | 확인하는 것 |
|---|---|
| 1 대상 건수 | 정정 1,004 · 해지 20 전건에 관계 행 존재 |
| 2 참조 무결성 | `from_doc_id` · `to_doc_id` 실재 |
| 3 상태 일관성 | `resolved`와 `to_doc_id`·`unresolved_reason`의 짝이 맞는가 |
| 4 사유 값 | D7에서 정의한 값만 쓰는가 |
| 5 방향과 종류 | 출발점이 정정본, 도착점이 원본, 같은 기업, 원본이 더 이름, 세부 유형 일치 |
| 6 자기 참조·순환 | 자기 자신·상호 참조 없음 |
| 7 범위 밖 판정 | 날짜가 실제로 범위를 벗어나는가 |
| 8 연쇄 정정 | 추적 깊이가 상한 이내인가 |
| 9 결과 요약 | 사유별 건수와 연결 가능분 기준 비율 |
| 10 표본 확인 | 실제로 이어진 문서 쌍을 눈으로 본다 |

5번이 핵심이다. 건수가 맞아도 방향이 틀리면 무의미하다. 유형 일치 검사가 실제로 오연결 15건을 잡아냈다.

각 검증 항목이 왜 들어갔는지는 `feedback/W3.md` 참조.

---

# 파일 구성

```
src/corpus.py             코퍼스 루트 자동 탐지 · 경로 정규화 · 원문 읽기
src/db.py                 연결과 스키마 정의
src/relation.py           정정 헤더 · 관련공시 파싱
src/docitem.py            표 구조를 그대로 읽어 항목-값 쌍으로. XML·HTML 공용
src/contract.py           거래소 계약 공시 서식 판별 · 항목 추출 · 계약명 정규화
src/major.py              주요사항보고서 항목 추출 · 검산 · 철회 판정
src/holding.py            지분공시 section 판별 · 개인정보 표시 · 요약 축 추출
src/build_base.py         W2 적재
src/build_relation.py     W3 적재
src/build_contract.py     W4 적재 — event_contract · contract_item
src/build_major.py        W4 적재 — event_major · major_item
src/build_holding.py      W4 적재 — event_holding · holding_item
src/fsdoc.py              재무제표 표 판별 — 구간·연결별도·종류·단위·열
src/fsvalue.py            재무 항목 값 추출 — 태그 경로와 표 파싱 경로
src/build_fs.py           W5 적재 — fact_financial
scripts/verify_base.py    W2 검증
scripts/verify_relation.py W3 검증
scripts/verify_contract.py W4 검증 — 계약
scripts/verify_major.py   W4 검증 — 주요사항
scripts/verify_holding.py W4 검증 — 지분
scripts/check_fsdoc.py    W5 표 판별 상태 측정
scripts/verify_fs.py      W5 골든 데이터셋 대조
scripts/audit_fs.py       W5 추출 단계 검산
scripts/verify_fact_fs.py W5 적재 검증
data/golden/              사람이 원문을 읽어 채운 정답지
data/corpus.db            생성물 (gitignore)
```

`data/corpus.db` 는 저장소에 없다. 코퍼스를 `assets/` 아래 두고 아래를 순서대로 돌리면 다시 만들어진다.

```bash
python src/build_base.py          # company 70 · document 4,204
python scripts/verify_base.py
python src/build_relation.py      # doc_relation 1,024
python scripts/verify_relation.py
python src/build_contract.py      # event_contract 1,169 · contract_item 24,558
python scripts/verify_contract.py
python src/build_major.py         # event_major 598 · major_item 83,641
python scripts/verify_major.py
python src/build_holding.py       # event_holding 1,083 · holding_item 1,530,018
python scripts/verify_holding.py
python src/build_fs.py            # fact_financial 38,845
python scripts/verify_fact_fs.py
```

각 적재 스크립트는 자기 테이블을 지우고 다시 쓴다. 몇 번 돌려도 결과가 같다. 의존 패키지는 `requirements.txt` 에 있고, 대체 수집분 PDF 를 읽는 데 `pypdf` 가 필요하다.

## 경로 처리

`manifest`의 `file_path`는 NFC(완성형)인데 파일시스템은 NFD(자모 분리)일 수 있다. 압축을 푸는 방식에 따라 달라진다. `resolve()`가 양쪽을 모두 시도한다.

코퍼스 위치도 고정하지 않는다. `assets/` 아래를 훑어 `manifest.jsonl`이 있는 폴더를 루트로 판별한다. 현재 배포본은 `assets/공시/공시/corpus/`이지만 이 값에 의존하지 않는다.

## 원문 보호

`corpus.py`는 읽기 함수만 노출하고 쓰기 경로를 두지 않는다. 모든 산출물은 `data/` 아래에만 만든다.
