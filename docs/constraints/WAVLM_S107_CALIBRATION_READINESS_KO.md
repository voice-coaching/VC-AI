# WavLM S107 calibration-only 구현·실행 준비 기록

최종 갱신: 2026-07-29 KST  
정본 계약:
[WP4 S103 이후 실행 계약](WP4_POST_S103_EXECUTION_CONTRACT_KO.md)  
상태: **production runner·deep validator 구현 및 합성 검증 완료 / 실제
calibration 접근·보정·metric 0건**

## 1. 완료 범위와 결과로 오해하면 안 되는 범위

S107의 config-only validator, fail-closed preflight, binary temperature
calibrator, provisional issue/abstention policy, reliability·risk·slice
reporter, content-addressed publisher, registry·commit·recovery와 read-only
deep validator를 구현했다.

구현 완료 범위는 다음과 같다.

- 실제 calibration locator보다 먼저 실행되는 S106 public deep validator
- S106 commit·final fusion checkpoint·config·validator source의 immutable
  hash binding
- `calibration` prediction만 허용하는 exact Parquet schema와 split guard
- encoder, head, fusion, feature contract와 loss 변경을 모두 금지하는
  frozen-model guard
- binary error probability logit에 적용하는 scalar temperature 한 개
- provisional engineering issue threshold 한 개와 나머지 row 전체 abstention
- 15-bin equal-width ECE, Brier, NLL, reliability bin
- selected issue policy와 고정 coverage grid의 risk-coverage
- L1, gender, proficiency, duration, error-type aggregate slice
- silver/oracle과 D07 expert-gold 지위의 물리적·논리적 분리
- 정확히 7개 artifact의 content-addressed generation, completed registry,
  immutable commit, 실패 registry와 missing-commit recovery
- prediction부터 모든 metric·report·artifact·registry·commit hash를 다시
  계산하는 read-only deep validator
- 120개 phone row, 12개 speaker의 bounded synthetic end-to-end와 tamper,
  concurrency, symlink, recovery, forbidden-split 검증

이 완료는 실제 S107 실험 완료나 S108 진입 가능을 뜻하지 않는다. 현재
실제 S106 commit이 없으므로 정상 preflight는 S106 commit 존재 검사에서
fail-closed 된다. 실제 calibration bundle, execution lock, generation,
registry, commit과 실제 ECE·Brier·temperature·threshold는 하나도 만들지
않았다. 합성 fixture의 수치는 논문 evidence가 아니므로 이 문서에 결과
값으로 기록하지 않는다.

구현 완성도와 실제 실행 완성도를 분리하면 다음과 같다.

| 범위 | 완성도 | 근거 |
|---|---:|---|
| static contract·config-only | 100% | canonical config-only 통과 |
| production runner·publication·deep validator | 100% | bounded synthetic E2E·fault test 통과 |
| 실제 S106 predecessor·calibration bundle | 0% | S106 commit 부재 |
| 실제 S107 calibration fit·metric | 0% | calibration row access 0 |
| D07 expert-valid calibration | 0% | D07 미수집·schema TBD |
| S108 readiness | 0% | preregistration·D07·실제 S107 미완료 |

## 2. S107 전용 파일과 frozen S106 interface

S104, S105, S106 구현, 공통 registry, tracker, 논문과 evidence-map은
수정하지 않았다.

| 역할 | 경로 |
|---|---|
| preregistration·입출력 계약 | `configs/experiments/wavlm_s107_calibration_v1.yaml` |
| fail-closed preflight CLI | `scripts/prepare_wavlm_s107_calibration.py` |
| production calibrate/recovery/validate CLI | `scripts/run_wavlm_s107_calibration.py` |
| preflight·bundle·순수 정책 guard | `src/voice_coach/training/wavlm_s107_calibration_contract.py` |
| calibrator·publisher·deep validator | `src/voice_coach/training/wavlm_s107_calibration_training.py` |
| 정책·metric 단위 테스트 | `tests/unit/test_wavlm_s107_calibration_contract.py` |
| 합성 E2E·fault 통합 테스트 | `tests/integration/test_train_wavlm_s107_calibration.py` |
| 구현·실행 준비 기록 | `docs/constraints/WAVLM_S107_CALIBRATION_READINESS_KO.md` |

S106는 다음 interface로 동결했다.

| 항목 | 값 |
|---|---|
| S106 config | `configs/experiments/wavlm_s106_stack_fusion_v1.yaml` |
| S106 config SHA-256 | `196ed40aa59c77af1f7d4d975941b90229d21a9de16f4109e7621b4c3fbcaabb` |
| S106 commit schema | `voice_coach.wavlm_s106_stack_fusion_commit.v2` |
| S106 validator | `voice_coach.training.wavlm_s106_fusion_training.validate_s106_commit` |
| S106 validator module SHA-256 | `e8366b150f507b12a48a7608fbbb2f9af619fcdd7eb10ffaceda4c9952d88097` |
| required validator status | `valid` |
| final checkpoint artifact | `final_fusion_checkpoint.json` |
| final checkpoint schema | `voice_coach.wavlm_s106_fusion_checkpoint.v1` |
| required family | `standardized_l2_logistic_regression` |

S106 public validator의 결과가 `status=valid`, commit hash 일치,
`stack_only=true`, `calibration_accessed=false`,
`locked_test_accessed=false`,
`full_stack_in_sample_metric_reported=false`를 모두 만족해야 한다.
그 뒤 commit의 canonical binding, config record, 정확히 7개 artifact와
final checkpoint hash·policy를 다시 검사한다.

향후 실제 S106 commit이 생기면 S106 public deep validator는 S106 증거의
무결성을 재계산하기 위해 S106의 제한된 stack source를 읽을 수 있다.
이는 S107 fit feature/label로 stack을 재사용하는 것이 아니다. 현재 실행은
S106 commit 존재 검사에서 먼저 종료됐으므로 S106 validator 호출과 실제
stack read도 0건이다.

## 3. fail-closed 접근 순서

Config-only 경로는 다음까지만 수행한다.

```text
S107 unique-key YAML·static policy 검사
  -> S107 implementation/project/lock source hash 검사
  -> 종료
```

이 경로는 S106 validator를 import하지 않고 S106 commit, calibration
bundle/path/row, 어떤 data split과 output도 읽거나 쓰지 않는다.

정상 preflight 순서는 다음과 같다.

```text
S107 static config + implementation hash validation
  -> frozen S106 config/validator module hash 검사
  -> S106 commit 존재·regular-file·non-symlink 검사
  -> S106 public deep validator
  -> S106 commit binding과 final checkpoint 재검사
  -> 최초로 calibration bundle locator resolve/hash
  -> bundle의 S106 lineage·label status·prediction file hash 검사
  -> Parquet semantic row는 읽지 않고 no-metric execution lock 발행
```

S106 commit이 없거나 validator가 실패하면 calibration bundle resolver에
도달하지 않는다. 합성 fault test에서는 bundle resolver 자체를 canary로
바꿔 S106 commit 부재 때 호출 횟수가 0임을 확인했다.

Runner는 valid execution lock을 재검증한 뒤에만 prediction Parquet를
`split=calibration` filter pushdown으로 읽는다. Bundle의 전체 row count와
filter 결과가 다르면 forbidden split row가 숨겨진 것으로 간주해 실패한다.
Train, dev, stack 또는 locked-test row를 S107 fit에 쓰는 경로와 raw audio,
CUDA 경로는 없다.

## 4. calibration prediction bundle 계약

Runner의 유일한 row-level 입력 진입점은
`calibration_prediction_bundle_commit.json`이며 schema는
`voice_coach.wavlm_s107_calibration_bundle.v1`이다.

Bundle은 다음 exact field를 가진다.

- `status=committed`
- validated S106 commit의 path·SHA-256·size
- validated S106 final checkpoint의 path·SHA-256·size
- `label_status`
- D07 evidence 또는 `null`
- calibration prediction matrix의 path·SHA-256·size·row count
- 문서 자체의 canonical `binding_sha256`

Canonical 실제 run의 label status는
`silver_oracle_provisional`로 고정했다. Config에는
`expert_gold_d07` 상태도 이름으로 분리했지만, D07 completed evidence
schema가 `TBD_PREREGISTRATION`이므로 현재 expert status bundle은
fail-closed 된다. 임의의 D07 claim mapping을 넣어도 실행되지 않는다.

Prediction Parquet의 exact column과 순서는 다음과 같다.

```text
utterance_id_sha256, speaker_id_sha256, phone_index, split,
uncalibrated_probability, reference_binary_error_label,
l1_group, gender_group, proficiency_group, duration_bucket,
error_type_group, qc_flagged, reserved_prompt
```

`(utterance_id_sha256, phone_index)` 중복, SHA-256 형식 오류,
non-calibration split, non-finite/out-of-range probability, binary가 아닌
label, QC flag, reserved prompt, control character와 reserved missing token을
거부한다. 두 binary class가 모두 존재해야 한다.

## 5. 고정 calibrator와 metric 정의

### 5.1 Temperature scaling

Uncalibrated error probability \(p\)를 `[1e-6, 1-1e-6]`로 clip하고
\(z=\log(p/(1-p))\)를 만든다. 유일한 fitted model parameter는 양의 scalar
temperature \(T\)다.

```text
calibrated_probability = sigmoid(z / T)
T in [0.05, 20.0]
objective = calibration binary negative log likelihood
optimizer = deterministic scipy bounded scalar
intercept = not fitted
fit_count = 1
```

Encoder, phone/utterance head, S106 fusion weight, feature order, loss와 model
family를 바꾸는 API가 없다. Artifact와 registry가 이 다섯 항목의
`retrained/changed=false`를 각각 기록하며 deep validator가 재검사한다.

### 5.2 ECE, Brier와 reliability

Primary ECE는 binary error probability와 binary event rate의 15-bin
equal-width ECE다.

- bin은 `[i/15, (i+1)/15)`, 마지막 bin만 오른쪽 끝 1.0 포함
- 빈 bin도 15개 목록에서 제거하지 않음
- 빈 bin은 `count=0`, 평균 probability·event rate·gap=`null`
- 빈 bin ECE contribution은 정확히 0
- ECE는 각 bin의 `count/N * abs(mean_probability-event_rate)` 합
- Brier는 `mean((probability-label)^2)`

Uncalibrated/calibrated ECE·Brier·NLL과 두 reliability bin 목록을 모두
보고한다. `ECE<=0.10`이어도 status는 `pass_engineering_only`이며 silver
결과의 promotion evidence가 아니다. `(0.10,0.15]`는 gray-zone promotion
fail, `>0.15`는 hard fail이다.

## 6. provisional issue·abstention 정책

현재 정본 계약에서 final minimum issue count/coverage, CI 방법·level,
point estimate 대 lower-bound gate와 동일 발화 내 phone cluster 방법은
모두 TBD다. 결과를 본 뒤 조용히 확정하지 않기 위해 현재 runner에는 다음
**provisional engineering policy**만 고정했다.

```text
threshold grid = 0.50..0.99, step 0.01
issued = calibrated_error_probability >= threshold
abstained = every eligible row not issued
provisional target point precision = 0.90
provisional minimum issued count = 30
provisional minimum issued coverage = 0.01
selection = eligible candidate 중 maximum coverage
tie-break = lower threshold
no eligible candidate = threshold 1.0, abstain all
```

동일 calibration label로 threshold를 선택했으므로 이 precision은
provisional engineering in-sample 값이다. Promotion gate로 쓰지 않는다.

불확실성도 final gate가 아니라 명시적 provisional 분석이다.

- speaker-cluster percentile bootstrap
- 95% interval, 2,000 replicates, seed 107
- threshold 선택에 CI 미사용
- promotion gate에 point estimate와 CI 모두 미사용

이는 최종 cluster 규칙을 승인한 것이 아니다. 정본 문서의
`within_utterance_cluster_uncertainty` 포함 5개 TBD를 report와 commit
chain에 그대로 남긴다.

## 7. risk-coverage와 slice

Risk-coverage는 calibrated error probability 내림차순으로 phone을
retaining했을 때의 issued precision과
`risk = 1 - issued_precision`을 보고한다. Target coverage는
`[0.01, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 1.00]`으로 고정하고
selected threshold point도 별도로 포함한다.

Slice axis는 다음 다섯 개다.

- L1
- gender
- proficiency
- duration bucket
- error-type group

각 group에서 row/speaker/positive/negative support, ECE, Brier, issued
count·coverage·precision을 계산한다. Phone row가 30 미만인 group은 이름을
외부 aggregate에 내보내지 않고 suppressed group 수와 총 row 수만
기록한다. Final minimum speaker, positive/negative support는
`TBD_PREREGISTRATION`이므로 모든 slice에
`promotion_gap_eligible=false`를 기록한다.

Error-type slice는 기존 binary label을 분해한 새로운 typed-issue
정확도가 아니다. Runner는 typed correction이나 coaching claim을
허용하지 않는다.

## 8. 정확한 artifact, registry와 recovery

한 generation에는 다음 7개 파일만 허용한다.

| artifact | schema/format | scope |
|---|---|---|
| `calibration_predictions.parquet` | `voice_coach.wavlm_s107_calibrated_predictions.v1` | D1 |
| `calibrator_parameters.json` | `voice_coach.wavlm_s107_calibrator_parameters.v1` | D1 |
| `threshold_policy_report.json` | `voice_coach.wavlm_s107_threshold_policy.v1` | A1 |
| `reliability_report.json` | `voice_coach.wavlm_s107_reliability_report.v1` | A1 |
| `risk_coverage_report.json` | `voice_coach.wavlm_s107_risk_coverage_report.v1` | A1 |
| `slice_report.json` | `voice_coach.wavlm_s107_slice_report.v1` | A1 |
| `calibration_only_report.json` | `voice_coach.wavlm_s107_calibration_report.v1` | A1 |

Artifact 이름·상대경로·SHA-256·size·format·scope로 generation ID를
content-addressing한다. `.part-<uuid>`에서 완전한 generation을 만든 뒤
exact-layout 검사에 통과한 디렉터리만 원자 publish한다.

Registry는 create-only hard link로 `running` writer를 선점한다. 두 번째
writer는 calibration fit 전에 실패한다. 성공은 `completed`, 실패는
restricted row ID·path·원문 exception을 넣지 않은 sanitized `failed`
registry로 남는다. 실패 registry를 자동 덮어쓰지 않는다. Completed
registry와 generation이 있지만 commit publication만 중단된 경우에는 두
증거를 깊게 검증하고 immutable commit만 복구한다.

Commit schema는
`voice_coach.wavlm_s107_calibration_commit.v1`이다. Config, execution
lock, S106 commit/checkpoint, calibration bundle, 7개 artifact, completed
registry와 canonical binding을 고정한다. Commit에는
`label_status=silver_oracle_provisional`,
`promotion_decision_allowed=false`, `s108_ready=false`가 항상 들어간다.

## 9. deep validator가 다시 계산하는 것

`--validate-commit-only`는 hash 존재만 확인하지 않는다.

- S107 implementation source와 S106 config/validator/commit/checkpoint
- execution lock과 calibration bundle lineage
- exact input schema, split/QC/reserved/ID/duplicate/class guard
- clipped logit, temperature optimization과 모든 calibrated probability
- threshold grid 전체, 선택 tie-break와 abstention
- provisional speaker-cluster bootstrap interval
- 15개 empty-bin 포함 reliability, ECE, Brier와 NLL
- risk-coverage point와 다섯 slice의 suppression·metric
- calibrated prediction Parquet의 모든 row
- 7개 artifact exact layout/content/hash와 generation ID
- completed registry의 input/split/frozen/runtime/metric/selection
- immutable commit의 전체 hash chain과 promotion/S108 false 상태

검증 종료 직전 implementation, S106 evidence, bundle/prediction, registry와
7개 artifact를 다시 hash해 TOCTOU 변경을 거부한다. Validator는 어떤
파일도 쓰지 않는다. Synthetic E2E에서 validator 전후 모든 파일의
SHA-256, size와 mtime이 동일함을 확인했다.

## 10. 실행 명령과 현재 검증 결과

실제 S106 commit과 calibration prediction bundle이 준비된 뒤 순서는
다음과 같다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s107_calibration.py

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/run_wavlm_s107_calibration.py

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/run_wavlm_s107_calibration.py --validate-commit-only
```

이번 구현 검증에는 다음을 실행했다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s107_calibration.py --config-only

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s107_calibration.py

PYTHONPATH=src ./.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_wavlm_s107_calibration_contract.py \
  tests/integration/test_train_wavlm_s107_calibration.py

PYTHONPATH=src ./.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_wavlm_s106_fusion_contract.py \
  tests/integration/test_train_wavlm_s106_fusion.py \
  tests/unit/test_wavlm_s107_calibration_contract.py \
  tests/integration/test_train_wavlm_s107_calibration.py

./.venv-scorer/bin/ruff format --check <S107 source and tests>
./.venv-scorer/bin/ruff check <S107 source and tests>
PYTHONPATH=src ./.venv-scorer/bin/mypy \
  --cache-dir=/dev/null --follow-imports=skip <S107 source and tests>
```

현재 결과:

- config-only: `status=config_valid`
- config-only access/write: S106 import 0, calibration path/row 0, 모든 split 0,
  GPU 0, metric 0, output 0
- canonical full preflight: exit 1,
  `S107 preflight failed: S106 commit does not exist`
- full preflight access/write: S106 deep validator 0, stack/calibration/locked 0,
  GPU 0, output 0
- S107 targeted: **15 passed**
- S106→S107 regression: **70 passed**
- Ruff format/lint: passed
- targeted mypy: passed
- synthetic E2E: 120 phone rows, 12 speakers, 7-artifact
  generation→registry→commit→deep validation 통과
- recovery: completed registry 뒤 missing commit 복구 통과
- safety: non-symlink workspace-relative input/output, immutable no-overwrite,
  concurrent writer rejection, read-only validation 통과
- fault injection: missing S106 ordering canary, forbidden split, expert-status
  without D07 schema, source symlink, aggregate artifact tamper를 모두 거부
- 실제 calibration row, metric, execution lock, artifact, registry, commit: 0

## 11. 최종 source audit

| 파일 | SHA-256 |
|---|---|
| S107 config | `6702ea196481784319c03e413d8ee6844a060e83407aed204f9344cb0230909f` |
| preflight CLI | `6afe337409b16f2072809dc9de164d371fad9cc94851aa5a621936d0ea572d46` |
| calibration CLI | `8fbee41b5676207fc59686744aa4ddd789a39c038c04a17dce77544ca73133cd` |
| contract module | `04909937b7b8eb4a135a0f7e44c7ec0afc8a438c29aae2d2cbe3de4dcdafac1e` |
| calibration module | `201973a25e75da77ec86468a60094c22ac3d3376d936d5e2d089531d52b5d6d3` |
| unit test | `04c8464c3263732a8bd665c96c3c43b8a82f16ef3213f4ffffaa75f5a837bbc2` |
| integration test | `566e4cece1c65234c024e0e5bdc72eeff873abc823bc5ddb66de1826162f1ec9` |

Config는 네 implementation source와 project manifest, scorer lock을 직접
pin한다. Config-only 실행도 pin 하나가 바뀌면 실패한다.

## 12. C/H/M 위험 감사

구현·합성 범위의 미해결 critical/high/medium(C/H/M) blocker는
**0/0/0**이다.

- Critical: S106 검증 전 calibration locator/row 접근, train/dev/stack
  label 재사용, locked 접근, encoder/head/fusion 재학습, silver를
  expert/promotion으로 승격, 임의 D07 claim
- High: forbidden split filter 은폐, duplicate/hash/QC/reserved row,
  temperature·threshold·ECE 재계산 불일치, empty-bin 누락, threshold
  no-candidate 자동 발행, artifact/registry/commit tamper
- Medium: small-cell group 노출, non-finite JSON/YAML, source/TOCTOU 변경,
  symlink traversal, concurrent writer, partial generation, failure message
  row ID 유출, read-only validator 쓰기

각 항목은 static guard, source hash, deep recomputation 또는 synthetic
fault test로 fail-closed 되는 것을 확인했다. 실제 S107 결과가 생기면
별도의 실제-data C/H/M 감사를 다시 실행해야 한다.

## 13. 실제 다음 순서

1. 실제 S106 completed commit을 public validator로 독립 검증한다.
2. Frozen S106 checkpoint로 calibration split prediction을 한 번 만들고,
   exact bundle schema와 content hash를 가진 bundle commit을 발행한다.
3. 정상 S107 preflight로 no-row/no-metric execution lock을 발행한다.
4. Production CLI로 temperature와 provisional issue policy를 한 번 fit하고
   7-artifact generation, registry와 commit을 발행한다.
5. 별도 `--validate-commit-only`와 실제-data C/H/M 감사를 통과시킨다.
6. Silver 결과는 provisional engineering으로만 기록한다.
7. Final issue count/coverage/CI/cluster와 slice support preregistration,
   G04와 D07 expert calibration이 완료되기 전에는 S108 ready를 선언하지
   않는다.
