# WavLM S105 incremental hierarchy 구현 준비 기록

최초 작성: 2026-07-29 KST  
상태: **production runner·심층 validator·합성 fault test 완료 / S104 commit·실제 GPU 실행 전**

## 1. 결론

S105를 S104 결과를 본 뒤 설계하지 못하도록 정본 config와 source hash를
먼저 고정했다. Config-only 검사는 S104, data, raw audio, CUDA를 읽지
않고 출력도 쓰지 않는다. Full preflight는 completed S104 commit이 없으면
data scan 전에 실패한다. 2026-07-29 실제 검사에서도 S104 commit 부재로
종료 코드 1, S105 산출물 수 0개를 확인했다.

현재 구현 범위는 다음과 같다.

- 다섯 누적 variant의 phone→word→utterance head
- 최대 양의 overlap과 낮은 word index tie-break를 쓰는 interval mapping
- word를 unlabeled intermediate representation으로만 쓰는 loss 경계
- train-inner 선택과 variant·task·seed별 dev 정확히 1회 ledger
- candidate-control 및 네 adjacent pair의 paired speaker bootstrap
- primary 2개와 incremental 8개의 분리된 Holm family
- 사전 고정 subgroup eligibility와 `worst_group_drop`
- 동일 시드 S104 adapter를 완전 동결하고 raw audio에서 모든 encoder
  layer를 online 추출하는 production runner
- utterance당 encoder forward 1회를 다섯 variant가 공유하되 optimizer,
  early stopping, threshold는 variant별로 독립인 학습 orchestration
- 시드당 단일 physical dev scan과 variant·task·seed별 exact ledger
- 정확히 82개인 generation, variant별 5개 completed registry,
  content-addressed publish와 commit-only visibility
- output 경로의 symlink ancestor/destination 거부, `RENAME_NOREPLACE`와
  hard-link create-only publish, 고정 registry prefix의 process lock으로
  동시 실행 claimant 사이의 무덮어쓰기·단일 소유권 보장
- Arrow schema, source identity/order, checkpoint tensor allowlist·shape·dtype·
  finiteness, metric/slice/bootstrap/Holm/subgroup/gate/table을 다시 계산하는
  심층 commit validator
- OOM, publication failure, commit failure, 무재학습 recovery를 검증한
  bounded synthetic fault-injection test

구현 blocker는 제거됐지만 completed S104 commit이 아직 없으므로 실제
S105 raw-audio/GPU 실행은 시작하지 않았다. 따라서 이 문서는 S105 성능
결과가 아니라 **실행 가능한 정본 패키지와 선행조건 기록**이다.

## 2. 서브태스크 완성도

| ID | 서브태스크 | 완성도 | 증거와 남은 조건 |
|---|---|---:|---|
| S105-R01 | S104 이전 사전등록 config | 100% | variant, primary, bootstrap, Holm, subgroup, split, output 계약 고정 |
| S105-R02 | S104 독립 검증·fail-closed preflight | 100% 구현 | public S104 validator 통과 전 data scan/output 금지; 실제 commit 대기 |
| S105-R03 | grouped interval mapping | 100% 구현 | maximum positive overlap, lower-index tie, orphan bucket, zero-word 처리 |
| S105-R04 | 5단계 incremental hierarchy head | 100% 구현 | simple→flat→final hierarchy→all-layer→multi-aspect |
| S105-R05 | word supervision 금지 | 100% 구현 | word head 없음, word loss nonzero 거부, PCC `not_evaluable` |
| S105-R06 | train-inner loss/selection·dev pass guard | 100% 구현 | shared stream, 독립 selection, gradient/finiteness, exact dev ledger와 history 재검증 |
| S105-R07 | paired bootstrap·Holm·adoption comparator | 100% 구현 | persisted prediction에서 두 primary·8 incremental·subgroup·gate 전부 재계산 |
| S105-R08 | artifact/registry/commit validator | 100% 구현 | 82-file, Parquet/source, checkpoint, 5 registry, transitive S104 hash-chain 심층 검증 |
| S105-R09 | online all-layer GPU orchestrator | 100% 구현 | same-seed S104 load/freeze, shared all-layer forward, atomic publish/recovery 구현·합성 검증 |
| S105-R10 | 실제 5 variant×3 seed 실험 | 0% | completed S104 commit과 R09 뒤 실행 |
| S105-R11 | 실제 결과 독립 검증·논문 반영 | 0% | committed S105 generation 전에는 금지 |

구현 준비 패키지의 단순 평균 완성도와 실제 연구 stage 완성도를 혼용하지
않는다. **S105 실제 실험 완성도는 0%**다.

## 3. 사전 고정된 variant

순서는 다음과 같으며 뒤 variant는 바로 앞 요소를 누적한다.

1. `simple_head_control`
   - 같은 seed의 frozen S104 adapter encoder
   - phone/utterance 독립 projection
2. `flat_shared_multitask`
   - final layer와 shared projection
   - word hierarchy 없음
3. `final_layer_hierarchy`
   - phone→word, word→utterance masked attention
4. `layerwise_weighted_hierarchy`
   - 모든 encoder layer의 learned softmax weighted sum
5. `levelwise_multi_aspect_hierarchy`
   - 각 hierarchy level의 독립 4-aspect attention

모든 variant에서 S104 adapter와 WavLM backbone은 frozen이고 S105 head만
학습한다. S104 seed 42/43/44 checkpoint는 S105의 같은 seed에만 연결한다.
Dev metric을 본 뒤 다른 seed나 checkpoint를 선택하지 않는다.

## 4. 데이터와 label 경계

허용 split은 `train`, `dev`뿐이다. 선택은 train의 speaker-disjoint
inner fit/validation만 사용한다. 각 variant·task·seed의 checkpoint와
threshold가 고정된 뒤 dev를 정확히 한 번 평가한다. Dev는 variant,
hyperparameter, threshold 재선택에 쓰지 않는다.

다음 split은 config, preflight, runner, report에서 모두 금지한다.

- `stack`
- `calibration`
- `locked_test`

Word interval은 다음 역할만 가진다.

- phone representation의 중간 grouping
- unlabeled word representation과 utterance aggregation

Word score, word head, word loss, phone-derived word label, word PCC를 만들지
않는다. Train/dev metadata를 S104 결과와 무관하게 별도 읽은
representation-readiness 감사 결과는 다음과 같다.

| 항목 | 값 |
|---|---:|
| primary-scope utterances | 167,506 |
| train/dev speakers | 860 / 196 |
| train-dev speaker overlap | 0 |
| target phone intervals | 2,199,869 |
| word intervals | 402,803 |
| positive-overlap이 없는 phone | 0 |
| word interval이 0개인 utterance | 2 |

두 zero-word utterance는 모든 phone을 utterance orphan bucket에 넣고
word label을 합성하지 않는다.

## 5. Adoption 분석 계약

Control은 `simple_head_control`, candidate는
`levelwise_multi_aspect_hierarchy`다. Primary family는 정확히 두 개다.

1. phone macro F1
2. comprehensibility ICC(2,1)

각 effect는 `candidate - control`의 seed 평균이다. 화자를 cluster로 한
paired percentile bootstrap을 5,000회, 95% CI, seed 42로 실행하고
two-sided zero p-value에 plus-one correction을 쓴다. 두 primary p-value는
Holm family 1개로, 네 adjacent pair×두 outcome은 별도 8개 Holm family로
보정한다.

정본 adoption gate는 CI나 p-value가 아니라 사전 문서의 point rule을
그대로 따른다.

```text
(phone F1 effect >= 0.03 OR comprehensibility ICC effect >= 0.03)
AND worst_group_drop < 0.05
```

CI, raw p-value와 Holm-adjusted p-value는 불확실성 보고용이며 gate를
사후 강화하거나 완화하지 않는다.

Subgroup metric은 phone macro F1이고 fixed groups는 다음과 같다.

- L1: Chinese, Japanese
- gender: F, M
- proficiency: Beginner, Intermediate, Advance, Fluent

Group eligibility는 unique speaker 20명 이상, positive phone 20개 이상,
negative phone 20개 이상이다. 미달 group은 수치와 support를 보고하되
gate에서 제외한다.

```text
group_drop = F1_control,g - F1_candidate,g
worst_group_drop = max(group_drop over eligible groups)
```

음수 개선을 0으로 자르지 않는다. Eligible group이 하나도 없으면
adoption 판정을 실패 처리한다.

## 6. 산출물 계약

각 variant·seed는 다음 다섯 파일을 가진다.

1. `hierarchy_head.safetensors`
2. `phone_dev_predictions.parquet`
3. `utterance_dev_predictions.parquet`
4. `dev_slice_metrics.json`
5. `resource_report.json`

각 variant의 `variant_report.json` 1개와 global
`ablation_comparison_report.json`, `ablation_table.md`를 더한다.

```text
5 variants × (3 seeds × 5 files + 1 variant report)
+ 2 global files
= 82 files
```

Variant별 completed registry는 자기 variant의 16개 파일을 등록한다.
Global commit은 82개 전체 hash, 5개 registry, S105 execution lock, S104
commit/generation, adoption status와 selected variant를 결합한다.

Generation은 Linux `renameat2(RENAME_NOREPLACE)`로, immutable JSON과
commit pointer는 동일 filesystem의 hard-link create-only 연산으로
게시한다. 목적지나 상위 경로에 symlink가 있으면 실패하고, 경쟁 실행이
먼저 만든 목적지의 바이트는 덮어쓰지 않는다. 실행 전체에서는 고정
registry prefix별 advisory lock을 먼저 원자적으로 선점하므로 최초
registry 생성과 commit recovery도 한 실행만 소유한다.

- adoption pass: `selected_variant=levelwise_multi_aspect_hierarchy`
- adoption fail: `selected_variant=simple_head_control`

독립 validator entry point는
`voice_coach.training.wavlm_hierarchy_training.validate_s105_commit`이다.

Validator는 다음을 산출물에서 독립 재계산한다.

- exact Arrow schema와 15개 prediction pair의 row identity/order
- manifest·explicit label에서 재구성한 dev truth
- checkpoint metadata, variant별 exact state-dict allowlist, tensor
  shape/dtype/finiteness, S104 same-seed checkpoint SHA
- train-inner selection history와 best epoch/threshold/early-stop 요약
- aggregate·slice metric, paired speaker bootstrap, 두 Holm family,
  subgroup eligibility/worst drop, adoption gate와 Markdown table
- 82개 hash, 5개 completed registry, execution lock, independently
  revalidated S104 commit/generation의 transitive binding

## 7. 현재 실행 결과

Config-only:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s105_hierarchy.py \
  --config configs/experiments/wavlm_s105_hierarchy_v1.yaml \
  --config-only
```

결과:

- `status=config_valid`
- `s104_read=false`
- `s104_dev_result_read=false`
- `data_read=false`
- `raw_audio_opened=false`
- `cuda_initialized=false`
- `output_written=false`

Full preflight:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s105_hierarchy.py \
  --config configs/experiments/wavlm_s105_hierarchy_v1.yaml
```

현재 결과:

- 종료 코드 1
- 원인: completed S104 commit 부재
- 실행 전/후 S105 artifact 수: 0 / 0
- data scan, raw audio, CUDA, GPU 학습: 실행하지 않음

이는 계획된 fail-closed 상태다.

## 8. 검증 기록

2026-07-29 KST 기준:

- S105 unit/integration: **53 passed**
- 공용 metric·registry·S104 adapter 회귀: **63 passed**
- Ruff check: 통과
- Ruff format check: 통과
- mypy production source 오류: 0
- config-only 실제 CLI: 통과
- S104-absent full preflight: 실패 확인, output 0
- synthetic exact generation: 82개와 5개 completed registry 확인
- 2-layer online pooling이 embedding layer를 제외한 모든 encoder layer를
  보존하고, shared dev scan 1회가 다섯 variant를 모두 공급함을 확인
- Parquet metric·row identity, checkpoint NaN·비허용 tensor 변조: 모두 거부
- publication 직후 fault: generation 제거, commit 0, 5개 safe failed registry
- synthetic OOM: automatic retry 0, partial generation 0, `oom=true`
- commit 직전 fault: generation과 5개 completed registry 보존, 재실행 시
  builder 호출 없이 commit 복구
- symlink ancestor/generation destination: publish 전에 거부
- generation·immutable JSON·commit의 동시 claimant: 선점자가 만든
  바이트를 보존하고 후발 publish 실패
- 동일 registry prefix 동시 실행: 최초 registry 생성 전 단일 owner만
  lock을 획득
- predecessor S104 commit SHA 변경: public S105 commit validator가 거부
- model variant 전체: output shape, score bound, backward, 모든 trainable
  tensor gradient 확인
- C/H/M 독립 논리 감사(정본 source·합성 evidence 범위):
  **Critical 0 / High 0 / Medium 0**

마지막 C/H/M은 production source와 bounded synthetic evidence에 대한
감사다. 실제 S105 metric, GPU resource 수치, completed real registry는
아직 존재하지 않으므로 감사 범위에 포함하지 않았다.

## 9. 다음 작업

1. S104 GPU run과 `validate_s104_commit`을 완료한다.
2. S105 full preflight로 S104 commit, same-seed checkpoint와 train/dev
   hierarchy data를 묶은 immutable execution lock을 만든다.
3. 고성능 GPU에서 정본 trainer를 실행해 5 variant×3 seed를 완료한다.
4. `--validate-commit-only`로 82 files·5 registries·S104 lineage를 독립
   검증한다.
5. validator가 통과한 뒤에만 aggregate ablation table과
   development-only 한계를 논문에 반영한다.

## 10. 고정 SHA-256

| 파일 | SHA-256 |
|---|---|
| S105 config | `e7818240f477af9957c4e13b88479a69820bbb44d48ece2c47e9a54cfc964ea0` |
| preflight CLI | `c99530bd4b1e713d54d36a6bdde22e393f0efe74e827a00b09f48a5f02854cd3` |
| trainer CLI | `913fc26f9e04979aa12c948269b807739fa0cf15f574e8183ab877415b41dba1` |
| contract module | `ba906cf4fa8693a23c8fe6138170b251ad0f5aba846db3f3b582a0ac307ffef6` |
| training/comparison module | `e2f7269c757ddee82f33de6787df466b29cbe9e1e2b1fed90130eaaec1c53ad1` |
| online execution module | `9ed1e67123449446c2a06d3db60651987605408737baa8d2b8246cc04b9466d8` |
| hierarchy model | `42fdbd4c2bb5e95bbc22f1ffb434ff59d1f5474a62613777783baff5e2cedce8` |
| S104 independent validator pin | `a7b41d507385c7f45b102f2d1603ad4214f0247ad9e0e08c6a3640f2df6bed80` |
| metrics module pin | `b80550bf80629382431e1d9a3bf246ac57f17b0544642245ed7c3d7ec0f3c452` |

위 hash는 production 역할의 source를 고정한다. 해당 source가 바뀌면
config-only가 실패해야 하며, config hash와 검증 기록을 함께 갱신해야
한다.
