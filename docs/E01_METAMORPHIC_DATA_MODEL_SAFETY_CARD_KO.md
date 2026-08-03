# WP8 E01 metamorphic robustness data·model·safety card

상태: **production 정적 준비 계약만 구현 / 실제 perturbation·모델 실행 0**

기준일: 2026-07-29 KST

## 1. 목적과 비범위

E01은 최종 선택 S1 모델이 통제된 음성 변형에서 사전 정의된
invariance/monotonicity를 만족하는지 평가하는 Phase-3 non-holdout
robustness stage다. 이 package는 그 실행 전 계약과 future input/report
validator만 제공한다.

현재 package에는 다음 기능이 없다.

- audio 또는 row-level 입력 읽기
- silence/gain/duration/F0/phone waveform 변형
- 선택 모델·checkpoint·calibrator·threshold loader/inference
- network, download, GPU 접근
- metric/pass-rate 계산 또는 pass/fail 판정
- report·registry·commit 작성, model promotion 또는 publication
- locked test나 S203 expert holdout 접근

## 2. 선행 모델 경계

E01은 실제 final S108 completion을 먼저 deep validation해야 한다.
Completed commit, registry와 required artifact hash chain이 유효하고
decision이 `research_candidate_passed`여야 한다.

E01 model identity는 deeply validated S108 commit SHA로 고정한다.
선택 checkpoint는 S108 `final_fusion_checkpoint`, calibrator는
`calibrator_parameters`, threshold는 `threshold_policy` artifact hash에
각각 bind한다. Canonical 값은 아직 `TBD_S108_COMPLETION`이다.

S108 완료 전에는 predecessor path도 resolve하지 않는다. S108을
검증하더라도 E01 package가 weight를 읽거나 inference하는 것은 아니다.
실제 executor는 별도 구현·검토 대상이다.

## 3. 다섯 perturbation과 미정 magnitude

Tracker/X09와 현재 task 범위가 고정한 family는 다음 다섯 개다.

| ID | 문서가 지지하는 operation | exact magnitude | expected relation |
|---|---|---|---|
| `silence` | 앞·뒤 silence 추가 | `TBD_PREREGISTRATION` | `TBD_PREREGISTRATION` |
| `gain` | 전체 gain 변경 | `TBD_PREREGISTRATION` | `TBD_PREREGISTRATION` |
| `duration` | 한 음절 duration 변형 | `TBD_PREREGISTRATION` | `TBD_PREREGISTRATION` |
| `f0` | global F0 scaling | `TBD_PREREGISTRATION` | `TBD_PREREGISTRATION` |
| `phone` | 특정 음소 교체 합성 | `TBD_PREREGISTRATION` | `TBD_PREREGISTRATION` |

실행계획에는 제한된 배경 잡음도 후보로 등장하지만 tracker X09와 이번
고정 범위에는 포함되지 않는다. 본 package는 사후 여섯 번째 family
삽입을 거부한다. 배경 잡음을 추가하려면 별도 scope amendment와
preregistration이 필요하다.

문서에는 초·dB·ratio·semitone·replacement count 같은 exact magnitude가
없다. 출력별 invariance/monotonic direction, tolerance, comparison
rule도 없다. 따라서 다음을 임의로 채우지 않는다.

- silence leading/trailing level, fill/fade
- gain level, clipping/normalization
- duration scale, target syllable, stretch/pitch-preservation
- F0 unit/scale, voicing/duration/resynthesis
- source-target phone, replacement count/region/synthesis/content preservation
- output metric ID, relation type, tolerance/margin, comparison rule

Approved preregistration에서는 각 relation을 `invariant`,
`monotonic_nonincreasing`, `monotonic_nondecreasing`, `targeted_change`
중 하나로 명시해야 한다. 정적 template은 어느 relation도 추론하지 않는다.

## 4. Pass gate 근거와 미정 계산법

`docs/IMPLEMENTATION_PLAN_KO.md` Phase 3은
`metamorphic expectation 통과율 95% 이상`을 완료 기준으로 명시한다.
기존 S203 static contract도 이를 S203 primary가 아닌
`phase3_non_holdout` gate로 분리했다. 따라서 E01은 다음 값만 고정한다.

- metric: `metamorphic_expectation_pass_rate`
- operator: `greater_than_or_equal`
- threshold: `0.95`
- scope: Phase-3 non-holdout

다음은 모두 `TBD_PREREGISTRATION`이다.

- numerator/denominator와 evaluable/invalid case 정의
- overall/per-family aggregation과 weighting
- minimum support와 missing/invalid policy
- gate statistic이 point rate인지 CI bound인지 여부
- CI method/level, multiplicity, blocked/failed rule

누락된 결과를 pass로 추론하거나 0.95를 사후 낮출 수 없다.

## 5. 데이터 구역과 input schema

Future input manifest는 `D1_pseudonymized_restricted_non_locked`다. 각 case는
opaque case ID, source item/audio-object/audio content SHA, perturbation ID,
parameter-set/relation SHA와 필요한 target-region SHA만 가진다.

- silence/gain/F0는 target region을 노출하지 않는다.
- duration/phone은 plaintext 대신 opaque target-region SHA가 필요하다.
- audio/model path, raw waveform, transcript, PII는 manifest에 없다.
- 정확히 다섯 family를 포함하고 case ID/semantic tuple은 중복될 수 없다.
- locked test와 S203 expert holdout 표시는 항상 false다.

Object locator와 raw audio는 별도 restricted execution sidecar 범위이며 이
package는 이를 resolve하거나 읽지 않는다. Source audio license·consent와
derivative processing permission도 executor 전에 별도 확인해야 한다.

## 6. A1 report와 privacy

Future report는 `A1_aggregate_only`다. 다섯 family별 case/evaluable/pass/
fail/invalid count, pass rate, optional CI, expectation type과 decision만
허용한다. Overall count는 family 합계와 정확히 일치해야 하고 pass rate는
aggregate count에서 재계산한 값과 일치해야 한다.

Report에는 row/case/item/speaker/participant/audio ID, audio hash/path,
model path, transcript, target text, free text와 case container를 넣을 수
없다. Locked-test와 S203-holdout access count는 0으로 고정한다.
Publication은 false이며 별도 review가 필요하다.

## 7. 안전·라이선스·오용 경계

- Waveform perturbation은 원본 데이터의 consent/license 범위를 확장하지
  않는다. Perturbed audio 공개는 금지한다.
- Phone replacement는 content-preservation rule과 합성 방법이 동결되기
  전 실행할 수 없다.
- Gain/F0/duration 변형은 clipping, voicing 손상, artifact를 만들 수
  있으므로 exact invalid-case policy가 필요하다.
- Result를 본 뒤 case 제외, magnitude/expectation 수정 또는 재실행하는
  것은 금지한다.
- S108 model license와 data role은 E01이 새로 승인하지 않는다.
- E01 pass는 deployment 승인이나 실제 학습자 안전성을 의미하지 않는다.

## 8. 무결성과 현재 actual

Strict validator는 unique-key YAML/JSON, finite number, normalized path,
source/model/prereg hash binding, no-overwrite, symlink, hash drift,
TOCTOU와 atomic report→registry→commit bundle을 검사한다. Future
completed-bundle 검증도 read-only다.

현재 actual 값은 모두 0이다.

- final S108 completion/model binding: 0
- approved E01 preregistration/input manifest: 0
- audio/case/model/weight/network/GPU 접근: 0
- perturbation/model inference/metric/decision: 0
- locked test/S203 expert holdout 접근: 0
- report/registry/commit/publication/completion claim: 0

따라서 E01 pass rate나 robustness 결과를 주장할 수 없다.
