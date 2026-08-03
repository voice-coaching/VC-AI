# D07 독립 전문가 gold 데이터·프라이버시·통계 카드

## 1. 현재 판정

D07은 **STATIC readiness only**다.

- 실제 전문가: 0명
- 실제 평정: 0건
- calibration/locked audio 접근: 0건
- locked expert transaction: 0건
- developer/model locked 접근: 0건
- 합의도·metric·result: 0건
- output·registry·commit: 0건

이 package는 전문가 판정을 만들거나 G04/D07을 통과로 바꾸지 않는다.
현재 full preflight는 actual H00 approval/attestation이 없어서 모든 external,
D1/R1/R0, reviewer, locked, model, metric, output path 전에 멈춘다.

## 2. G04와 D07은 서로 다른 stage다

| 구분 | G04 | D07 |
|---|---|---|
| 목적 | 자동 정렬 240건 수동 정확도 감사 | S1 gold·rubric·calibration용 독립 전문가 재평가 |
| 표본 | 고정 240건 | 별도 1,000–2,000발화 |
| 현재 상태 | aggregate progress `0/240`, incomplete | item 0, rating 0 |
| gate | 최소 228/240, 95% | calibration scalar 0.70 또는 taxonomy 0.65 |
| 재사용 | D07 item/rating으로 재사용 금지 | G04/S203 item 재사용 금지 |
| protected state | 기존 local UI review state | 별도 D07 D1/R1/R0 storage |

Config-only는
`aihub71490_manual_alignment_review_progress.json`이라는 A1 aggregate만
읽어 `expected=240`, `completed=0`, `reviewer=0`,
`review_state_sha256=null`, `export.written=false`를 확인한다. G04
worksheet/detail/UI state/audio를 해석하거나 읽지 않는다. 기존 G04
review state와 UI 구현은 변경하지 않았다.

G04가 나중에 완료돼도 그 240건은 D07 1,000–2,000건의 일부가 아니다.
G04 completion attestation은 calibration agreement를 통과한 D07이
locked-test expert access authorization을 받기 전의 별도 gate로만
사용한다.

## 3. 승인과 접근 순서

```text
C1/A1 static source pins + G04 0/240 aggregate
  -> actual H00 public approval + official-document attestation
  -> D07-specific institutional/expert approval
  -> reviewer attestation: primary 2 + adjudicator 1
  -> frozen D07 preregistration, no TBD
  -> STOP before D1/R1/R0
  -> separate calibration executor: 800 items
  -> calibration agreement report
     -> fail: rubric retraining + separate resample, locked forbidden
     -> pass: rubric/primary freeze, locked authorization 후보
  -> separate G04 completion attestation
  -> D07 expert locked one-shot authorization
  -> separate locked executor: 200–1,200 items
  -> original-1/original-2/adjudicated sensitivity
  -> aggregate D07 completion attestation
```

현재 H00 package의 공식 결정은 C0/C3/C5 학습자 연구 scope를 검증하도록
구현돼 있다. 그 artifact를 D07 승인으로 추론하거나 대체하지 않는다.
D07-specific institutional approval은 기존 제한 audio의 전문가 평정,
calibration 800, locked 최대 1,200, 2인 독립 평가와 disagreement-only
adjudicator를 명시적으로 승인하고 actual H00 hashes에 결합해야 한다.

## 4. 표본과 split

총 발화 수는 1,000–2,000이며 첫 평정 전에 다음처럼 동결한다.

- `calibration`: 정확히 800
- `locked_test`: 200–1,200
- 합계: 1,000–2,000

층화 축은 다음 여섯 개다.

1. L1
2. 성별
3. 숙련도
4. 발화 길이
5. 목표 음소
6. 기존 오류 유무

작은 집단 우선 배정, exact stratum allocation, 모집단 weighting,
weighted/unweighted estimand와 minimum support는 실제 분포·전문가·통계
검토 전에는 정할 근거가 부족하다. 따라서
`TBD_PREREGISTRATION`으로 유지한다. Item manifest는 승인된 exact
allocation의 결과만 받고 각 행에 위 여섯 stratum을 구조화한다.

## 5. Reviewer 역할과 blindness

문서 근거로 고정되는 역할은 정확히 세 개다.

- `primary_reviewer_1`
- `primary_reviewer_2`
- `adjudicator`

Primary 두 명은 모든 rating unit을 서로의 판정과 model output 없이
독립 평가한다. Adjudicator는 preregistered disagreement만 보고 두
원평정의 hash를 보존한 채 판정한다. 원평정은 수정하거나 덮어쓰지 않는다.

허용 qualification family는 다음 중 하나다.

- 한국어 음성학
- 한국어 발음교육
- 강제정렬 검수 경험

어떤 문서·경력 증거가 충분한지, adjudication disagreement의 exact
정의·trigger·protocol은 사람이 승인할 사항이므로
`TBD_ATTESTATION`/`TBD_PREREGISTRATION`이다. Actual reviewer
attestation은 reviewer별 가명 SHA, qualification receipt, rubric
training, 기밀, model blind, 이해상충 해소를 결합한다.

## 6. Item과 rating schema

### Item manifest

`d07_gold_item_manifest_v1.schema.json`은 D1 제한 artifact다.

- namespaced item/speaker SHA
- split
- audio object SHA; path/URL/bytes 금지
- 여섯 stratification field
- target phone count
- reviewer order commitment SHA
- model/system condition 비노출
- G04/S203 reuse false

Item manifest에는 raw ID, file stem, path, transcript, prompt text,
model output이 들어갈 수 없다.

### Rating manifest

각 phone rating은 다음을 가진다.

- item/reviewer hash와 `target_phone_index`
- `phone_realization`: correct/substitution/deletion/uncertain
- `error_type`: C01의 17종 ontology 또는 별도 `uncertain`
- `boundary_acceptable`: yes/no/uncertain; 50 ms reference와 청지각
- pronunciation/comprehensibility/fluency: 정수 1–5
- reviewer confidence: 정수 1–4
- model/system condition blind receipt

C01의 `other`는 이미 17종 안에 있다. `uncertain`은 18번째 source
ontology가 아니라 사람 판정 상태다.

Primary rating에는 peer 판정, disagreement receipt, adjudication note가
없어야 한다. Adjudication rating에는 서로 다른 원평정 hash 2개,
disagreement receipt, reason code, 제한 note가 필요하다.

Note 최대 길이는 preregistration에서 정한다. Contract는 그 길이를
적용하고 newline/control, email/전화 형식, 이름·연락처·소속·출신기관·
기관명·학교명·URL marker를 거부한다. 사람 검토 없이 자동 필터만으로
비식별을 주장하지 않으며 승인 담당자가 반출 전 다시 검토해야 한다.

## 7. Calibration agreement

Calibration report는 adjudication 이전의 두 원평정만으로 agreement를
계산한다.

| label family | 필수 보고 |
|---|---|
| phone 이진/유형 | macro F1, Cohen's kappa, prevalence |
| 1–5 scores | QWK, ICC(2,1), MAE, score distribution |
| ontology | macro agreement, Krippendorff alpha |

전체 calibration gate는 S108 정적 계약과 동일하다.

```text
scalar ICC/QWK >= 0.70 OR taxonomy agreement >= 0.65
```

어떤 score의 ICC/QWK를 scalar gate로 쓸지와 taxonomy macro
agreement/alpha 중 무엇을 gate로 쓸지는 preregistration에서 동결한다.
두 값이 모두 미달이면:

- `failed_retrain_and_resample`
- rubric retraining 필수
- 별도 calibration 재표집 필수
- 실패 표본 재사용 금지
- rubric/primary freeze 금지
- locked authorization 후보 자격 없음

Adjudication으로 낮은 원평정 합의를 숨길 수 없다.

Pass는 locked access 자체의 허가가 아니다. G04 completion과 one-shot
authorization이 추가로 필요하며 그전까지
`locked_test_rating_allowed_before_g04_and_one_shot_authorization=false`다.

## 8. Locked one-shot과 최종 보고

Locked expert access authorization은 다음에 모두 결합한다.

- frozen preregistration와 item manifest
- passed calibration report
- 별도 G04 completion attestation
- frozen rubric와 primary definition
- prior expert locked transaction count 0
- 정확히 한 번의 transaction ID/authorization receipt
- developer/model result access false

Locked 시작 뒤 rubric과 primary metric 정의를 바꿀 수 없다. Final
aggregate report는 다음 세 basis를 모두 별도로 보고한다.

1. primary reviewer 1 original
2. primary reviewer 2 original
3. adjudicated gold

CI, multiplicity, cluster, minimum support, weighted/unweighted slice의 exact
방법은 preregistration에서 결정하고 final report에는 방법 SHA만
기록한다.

`voice_coach.d07_gold_completion_attestation.v1`은 S108 aggregate
validator와 호환된다. 여기서 `locked_access_count=0`은 developer/model
locked 결과 접근이 없었다는 뜻이다. 별도
`expert_locked_access_transaction_count=1`은 승인된 전문가가 sealed
audio를 단일 transaction으로 평정했다는 뜻이므로 둘을 혼동하지 않는다.

## 9. Privacy zone

| zone | D07 용도 |
|---|---|
| R0 | 원음, 필요시 제한 adjudication source |
| R1 | locator, raw ID, reviewer 대응표 |
| D1 | 가명 item·원평정·adjudication |
| A1 | agreement, locked sensitivity, completion attestation |
| C1 | code, schema, config, public template/approval |

Reviewer SHA도 익명화가 아니라 가명 키다. 원음, 자유 note, reviewer
mapping, row label을 공개하지 않는다. Checksum은 무결성 식별자이지
재배포 허가가 아니다.

