# C02 결정론적 coaching selection 데이터·모델·안전 카드

작성일: 2026-07-29 KST  
상태: **production static preflight 구현 완료, 실제 coaching 실행 미승인**

## 1. 현재 판정

- 작업 단위: WP6 C02, C01 ontology 기반 top-1/top-3 coaching selection
- 구현 범위: `production_static_preflight_only`
- C01 engineering ontology deep validation: 가능
- 실제 S107 completion/threshold artifact: 없음
- 승인·동결된 C02 preregistration: 없음
- 실제 diagnostic issue row read, coaching selection, effect estimation: 모두 `0`
- runtime model, GPU, network, LLM, 자유 문장 생성: 모두 `0`
- output/registry/commit 생성과 production release: 모두 `0`
- C02 연구 완료 또는 coaching 효과 주장: 불가

이 카드는 실제 coaching 결과 카드가 아니다. 미래의 별도 executor가 따라야
할 데이터 구조, calibration/evidence gate, ontology allowlist, 개인정보 및
생성 안전 경계를 정적으로 고정한다.

## 2. 선행 데이터와 증거

### 2.1 C01 ontology

C02는 다음 C01 정본을 SHA-256으로 고정하고 deep recomputation한다.

- catalog:
  `configs/coaching/korean_error_advice_ontology_v1.json`
- schema:
  `configs/coaching/korean_error_advice_ontology_v1.schema.json`
- immutable validation report:
  `artifacts/coaching/c01_korean_error_advice_ontology_v1.validation.json`
- privacy-safe aggregate:
  `data/processed/aihub71490/manifests/aihub71490_data_card.json`

현재 C01은 17개 tag, 176,523개 오류 interval, unknown 0을 검증한다.
engineering contract는 valid이지만 조언은 `engineering_draft`이고 C03 전
production release는 금지된다. C02 full preflight에서 C01 validator가
aggregate 1개만 읽어 report를 재계산하며 row/raw/locator/environment/
identifier read는 모두 0이어야 한다.

### 2.2 S107 calibration

C02가 요구하는 것은 설정 파일이나 실행 예정 경로가 아니라 실제 완료된
S107 commit과 그 안의 `threshold_policy_report.json`이다.

현재 두 SHA는 모두 `TBD_S107_COMPLETION`이다. 따라서 C02는 commit 경로를
해석하거나 읽지 않는다. 미래에는 다음 조건을 모두 만족해야 한다.

- completion status `committed`
- deep validator status `valid`
- label status `silver_oracle_provisional`
- calibration-only true
- train/dev/stack/locked-test/raw-audio/GPU access false
- promotion 및 S108-ready false
- threshold report policy
  `provisional_engineering_not_promotion`
- threshold selection status `selected_provisional_engineering`
- commit, registry, generation artifact, 선택 threshold의 hash·값 일치

S107이 `abstain_all_no_provisional_candidate`를 반환하면 completion 자체가
유효하더라도 C02 coaching threshold로 승계하지 않고 fail closed한다.

S107 threshold만 존재한다고 coaching을 허용하지 않는다. 같은 threshold를
명시적으로 승인한 C02 preregistration이 추가로 필요하다.

### 2.3 C02 preregistration

현재 템플릿은 다음 값을 의도적으로 미정으로 둔다.

- S107 completion commit SHA
- S107 threshold report SHA
- calibrated confidence의 정확한 의미
- phone probability에서 issue confidence로의 집계 규칙
- 정확한 threshold
- threshold 승인 규칙
- issue type별 지원 evidence matrix
- 최소 지원 evidence 수
- 승인 protocol과 signed receipt

이 값은 실제 S107 완료 뒤 독립 protocol owner가
`approved_and_frozen` 상태로 동결해야 한다. `TBD_*`가 하나라도 남으면
diagnostic issue 경로 전에 fail closed한다.

## 3. 데이터 모델

### 3.1 diagnostic input

스키마:
`configs/coaching/c02_diagnostic_issues_input_v1.schema.json`

- zone: `D1_pseudonymized_restricted_experiment`
- top-k: 정확히 1 또는 3
- C01 report, S107 commit, S107 threshold report, C02 preregistration SHA 결합
- issue 위치: `start_ms`, `end_ms`
- 오류 유형: C01 stable ID 또는 안전한 unknown ID
- priority 입력: severity, calibrated confidence,
  comprehensibility impact, trainability
- 고정 evidence ID만 허용
- calibration row, claim evidence, target-overlap receipt는 SHA-256으로 결합

이름, 연락처, raw ID, transcript/target text, audio/source/model path,
filename, 자유서술, prompt, completion, generated text, LLM response는
재귀적으로 금지한다. SHA-256 request/issue ID도 익명 정보가 아니라
가명처리된 D1 키로 취급한다.

### 3.2 coaching selection output

스키마:
`configs/coaching/c02_coaching_selection_output_v1.schema.json`

출력은 다음 둘 중 하나만 포함한다.

1. C01 catalog의 `advice_id`와 `message_ko`를 byte-exact하게 복사한
   `coach`
2. 문구가 모두 `null`인 `rerecord` 또는 `expert_review` abstention

output은 input/config/preregistration/C01/S107 hash, top-k, threshold,
priority 식, tie-break, ranked window, action과 generation non-execution
receipt를 포함한다. production release는 항상 false다.

개별 output은 D1이다. 미래 A1 aggregate report에는 request/issue ID,
개별 issue row, 조언 문구 또는 calibration row receipt를 넣을 수 없다.

## 4. 결정론적 selection 계약

priority는 다음 식으로 계산한다.

`severity × calibrated_confidence × comprehensibility_impact × trainability`

- JSON number를 base-10 `Decimal`로 변환한다.
- 출력은 소수점 12자리, round-half-even이다.
- 내림차순 priority 뒤 tie-break는 start, end, issue type, target,
  observed, 정렬된 evidence IDs, issue ID 순이다.
- top-1 또는 top-3 window를 먼저 고정한다.
- window의 blocked issue를 버리고 k 밖의 issue로 보충하지 않는다.
- confidence가 threshold와 같으면 통과하며, 낮으면 `rerecord`다.
- unknown, `other`, evidence 부족, 허용 matrix 밖 evidence,
  localization/overlap receipt 불일치는 `expert_review`다.
- phone-localized tag는 target과 positive overlap receipt가 모두 필요하다.
- `no_target_phone_claim`과 `prosodic_span_only`는 target/overlap receipt가
  없어야 한다.

threshold와 evidence matrix는 현재 추정하지 않는다. S107 완료 및 frozen
preregistration 전에는 위 순수 함수가 실제 D1 row에 연결되지 않는다.

## 5. 모델·GPU·네트워크 경계

- C02 package는 모델을 학습하거나 추론하지 않는다.
- S107은 완료 증거로 deep validation할 미래 predecessor이며 C02 runtime
  scorer가 아니다.
- C01 catalog lookup 외 생성 모델은 없다.
- LLM prompt/completion 필드와 자유생성 fallback을 허용하지 않는다.
- catalog advice ID 또는 한국어 문구의 rewrite/paraphrase를 허용하지 않는다.
- 모델/GPU/network/LLM access counter가 하나라도 증가한 결과는 C02
  canonical output으로 인정할 수 없다.

향후 더 자연스러운 표현을 위해 LLM을 추가하려면 C02 계약을 우회할 수
없다. 별도 연구 단계, 별도 승인, 새 schema와 안전 감사로 분리해야 한다.

## 6. 안전 정책

### 6.1 낮은 confidence

`calibrated_confidence < exact_threshold`이면 조언을 만들지 않고
`rerecord`를 반환한다. threshold equality는 통과다.

### 6.2 지원되지 않은 claim

다음은 모두 문구 없이 `expert_review`다.

- unknown 또는 `other`
- preregistered support matrix가 없는 issue
- evidence가 최소 개수보다 적음
- 허용 matrix 밖 evidence 포함
- phone-localized claim에 target 또는 positive overlap receipt가 없음
- no-target/prosody tag에 target phone 또는 overlap receipt가 있음
- C01 advice가 `draft_coach`/`engineering_draft`가 아님

### 6.3 자유 생성 방지

- output `message_ko`는 C01 문자열과 정확히 일치해야 한다.
- message SHA-256도 같은 UTF-8 문자열에서 재계산한다.
- output 전체를 input과 frozen policy로 재계산해 exact comparison한다.
- prompt, completion, generated text 또는 LLM response key는 schema와
  recursive denylist 양쪽에서 거부한다.

## 7. 보안·재현성

- JSON/YAML duplicate key와 NaN/Infinity 거부
- exact root/nested key set과 `additionalProperties=false`
- canonical source 19개 SHA-256 고정
- workspace-relative path만 허용
- traversal, symlink chain, non-regular file 거부
- open 전/중 inode·size·mtime과 content hash 검증
- future output은 no-overwrite, unique target, source/output 비중첩,
  atomic publish 요구
- 동일 input/policy/catalog는 동일 rank/action/advice를 만든다.

## 8. 제한 및 비주장

현재 존재하지 않거나 계산하지 않은 항목:

- 실제 S107 완료 commit과 threshold report
- 승인된 C02 preregistration와 evidence support matrix
- 실제 diagnostic issue manifest
- 실제 top-1/top-3 coaching output
- 사용자 rerecord 또는 expert-review 결과
- 이해가능성·학습효과·안전성 효과 추정
- C02 commit/registry/aggregate report
- C03 전문가 200건 감사
- production/publication 승인

따라서 이 package와 synthetic test를 실제 coaching 품질, 안전성 또는
학습효과의 실험 근거로 인용해서는 안 된다.
