# WP4 S103 이후 실행 계약

최초 작성: 2026-07-29 KST
상태: **S103 분기 확정 / S104–S108 정본 실행 계약 유지**

## 1. 목적과 효력

이 문서는 S103의 실제 Base+–Large 비교가 끝난 뒤 S104–S108을 구현하고
평가하는 순서, split 접근권한, label 경계, gate, 산출물과 완료 판정을
고정한다. 계획 문서 사이의 순서나 용어가 충돌하면 이 문서를 WP4
S104–S108의 정본으로 사용한다.

이 문서는 새 실험 결과가 아니다. 작성 시점의 S104–S108 상태, WP4 점수,
전체 진행률, P04 충족 수, 기존 metric과 PENDING 표시는 바꾸지 않는다.
특히 사전 구현, synthetic test, dry-run 또는 provisional 결과만으로
S104–S108을 완료 처리하거나 승격 점수를 부여하지 않는다.

상위 경계는 다음 문서를 함께 따른다.

- 전체 task 상태와 배점:
  [연구 완성 하위 작업 추적기](RESEARCH_COMPLETION_TRACKER_KO.md)
- 개인정보·반출 구역:
  [연구 데이터 개인정보·반출 경계](DATA_PRIVACY_BOUNDARY_KO.md)
- G04 수동 정렬:
  [AI Hub 71490 수동 정렬 감사 절차](AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md)
- D07 전문가 gold:
  [retired contract 통합 안내](../README.md#retired-contracts)
- 현재 원고의 결과·계획 경계:
  [방법·윤리·재현성 독립 갭 감사](../paper/METHODS_ETHICS_REPRODUCIBILITY_GAP_KO.md)

## 2. 용어와 식별자

이 문서에서 plain `G04`는 tracker의 **240건 수동 정렬 감사**만 뜻한다.
[P00 주장 맵](../paper/CLAIM_GAP_MAP_KO.md)의 gate 표에 있는 동명 `G04`는
후속 모델의 seed·bootstrap·Holm 분석 계약이므로 `CLAIM-G04`라고 부른다.
두 항목을 같은 gate로 취급하지 않는다.

평가 지위는 다음처럼 분리한다.

| 지위 | 의미 | 허용되는 주장 |
|---|---|---|
| engineering | train 내부 선택과 제한된 dev 평가 | 개발 결과, 실패 분석 |
| adoption | WP4 안에서 다음 후보를 선택하는 비교 | 다음 단계 입력 후보 |
| research candidate | S108의 사전 고정 최종 S1 연구 후보 | 후속 S2 연구 시작 여부 |
| deployment | S2·metamorphic·coaching 사람 감사까지 통과 | 제한 범위의 배포 검토 |

`research candidate`는 배포 승인이 아니다. S108이 통과해도 S2,
metamorphic, coaching 감사와 predicted-alignment 검증을 생략하지 않는다.

## 3. 정본 DAG와 S103 분기

```text
S103 completed comparison
  |
  +-- Large - Base+ phone macro F1 >= 0.03
  |     -> Large adapter/top-block 후보
  |     -> 필요하면 Base+ adapter를 고정 control로 병행
  |
  +-- Large - Base+ phone macro F1 < 0.03
        -> Large 확대 중단
        -> Base+ adapter 후보

selected S104 candidate
  -> S105 incremental hierarchy ablation
  -> S106 stack-only confidence-aware fusion
  -> S107 calibration-only frozen scorer
  -> S108 no-locked dry-run
  -> G04 + D07 + preregistration freeze
  -> B05와 S108을 묶은 단일 locked evaluation batch
  -> S108 research-candidate pass/fail
  -> S200 이후 S2 / E01 / C03
  -> 별도 deployment decision
```

S103 gate는 **frozen backbone 분기**다. S105의 구조 채택 gate와 같은
판정이 아니다. S103이 실패하면 Large의 full/partial 확대를 중단하지만
Base+ adapter까지 자동 중단하지 않는다.

각 단계는 바로 앞 단계의 immutable report·registry·checkpoint hash를
입력으로 고정한다. 뒤 단계 결과를 본 뒤 앞 단계의 후보, loss,
hyperparameter 또는 split을 다시 선택하지 않는다.

### 3.1 실제 S103 분기 기록

2026-07-29 정본 비교에서 Large − Base+ phone macro F1 점추정은
`0.001465695617904439`, 5,000회 paired speaker-cluster bootstrap 95% CI는
`[0.0000061248957422009065, 0.0029903885141528744]`였다. 사전
`>= 0.03` point-estimate rule을 충족하지 못했으므로 gate 상태는
`failed`, S104 선택 후보는 **WavLM Base+**다.

비교 commit/report/receipt SHA-256은 각각
`e45cf869ab54beff6bcf5a3c674c0bdff0d75460ab8daabe50c7477afca16c6e`,
`90db58ee3af3bb47b88a8ae1700e6a0a9ce9f16feeaa06b8e1c5d7ffbe778213`,
`342a9bd2b97de87a1bfd38c3ed0a22819b1151eb37836d90e941951781f620a6`다.
이 분기는 development-only·oracle-alignment·silver-label 결과이며
Large의 다른 outcome 열세나 배포 부적합을 뜻하지 않는다.

## 4. split 접근 계약

모든 행 접근은 QC flag와 예약 prompt 제외, namespaced SHA-256 식별자,
화자 비중첩, label/input role 분리와 원자 publish를 유지한다. 아래 허용은
해당 stage의 전용 runner에만 적용되며 범용 데이터 접근권한이 아니다.

| Stage | 학습·선택에 허용 | 평가·산출에 허용 | 금지 |
|---|---|---|---|
| S104 | `train`의 speaker-disjoint inner train/validation | checkpoint 고정 뒤 `dev` task·seed당 1회 | stack, calibration, locked-test |
| S105 | `train`의 speaker-disjoint inner train/validation | variant·task·seed당 사전 고정된 `dev` 1회 | stack, calibration, locked-test |
| S106 | `stack` 안의 speaker-disjoint OOF fold | OOF stack prediction과 선택 후 full-stack final fit | train/dev를 fusion 선택에 재사용, calibration, locked-test |
| S107 | frozen S106 scorer의 `calibration` prediction만 | calibrator·threshold·reliability·risk report | encoder/head/fusion 재학습, train/dev/stack label 재사용, locked-test |
| S108 dry-run | aggregate report·registry·hash만 | `locked_access_count=0`인 checklist | locked-test row/audio/label/prediction |
| S108 final | 사전 고정된 model/calibrator/policy만 | 승인된 단일 locked evaluation batch | locked 결과를 이용한 재선택·재학습·재보정·재실행 |

### 4.1 S104–S105

- optimizer, learning rate, epoch, early stopping, loss weight와 phone
  threshold는 train 내부 화자 비중첩 validation에서만 결정한다.
- 최종 checkpoint와 threshold를 고정한 뒤 dev를 한 번 추론한다.
- dev는 후보 추가, hyperparameter 재탐색 또는 threshold 재조정에 쓰지
  않는다.
- 결과는 G04와 D07 전까지 `development-only`, `oracle-alignment`,
  `silver-label`, `promotion_decision_allowed=false`다.

### 4.2 S106

- explicit branch와 S104/S105 SSL branch의 checkpoint를 먼저 동결한다.
- stack의 각 화자는 정확히 한 OOF validation fold에만 속한다.
- 각 fold model은 그 fold의 화자·label로 학습하지 않는다.
- architecture와 hyperparameter는 OOF aggregate로 한 번 선택하고, 선택
  이후 같은 설정을 full stack에 최종 fit한다.
- full-stack fit의 in-sample metric은 S106 evidence로 보고하지 않는다.
- fusion feature에는 component logit/probability, margin/entropy,
  사전 고정한 uncertainty, missingness와 허용된 alignment confidence만
  넣는다. 정답, realized phone, alignment operation과 error tag는
  feature가 될 수 없다.
- seed별 prediction을 별도 channel로 쓸지 seed ensemble을 쓸지는 stack을
  열기 전에 config에 고정한다.

### 4.3 S107

- S106의 encoder, head, fusion weight와 feature contract를 고정한 뒤
  calibration split을 연다.
- calibration에서는 temperature와 발행·abstention threshold만 맞춘다.
  모델 family, encoder weight, fusion feature와 loss를 바꾸지 않는다.
- AI Hub silver/oracle calibration은 G04 전에 provisional engineering으로
  실행할 수 있다. D07의 전문가 calibration 800건이 없으면
  expert-valid calibration이나 최종 promotion 근거로 쓰지 않는다.
- calibration을 본 뒤 model 구조를 바꾸면 기존 calibration 결과를
  폐기하고 새 독립 calibration 자료 없이는 S108 final로 가지 않는다.

### 4.4 S108와 locked test

- G04 전에는 aggregate-only dry-run만 허용한다.
- G04와 D07이 완료되고, 미해결 preregistration 항목이 모두 해소·고정되며
  모든 hash가 고정된 뒤에만 `ready_for_locked_batch`가 될 수 있다.
- B05 explicit baseline과 S108 최종 S1은 같은 사전등록 batch manifest에
  넣고 locked test를 **한 번만** 연다. 두 모델을 순차 실행해 첫 결과를
  본 뒤 두 번째 실행을 바꾸지 않는다.
- batch가 끝나면 baseline B05 report와 S108 promotion report를 함께
  publish한다. 결과가 gate에 미달해도 locked test를 다시 열지 않는다.
  모델을 수정하려면 새로 승인된 독립 test set이 필요하다.

## 5. label 가용성

| 단위·label | 현재 가용성 | 현재 허용 용도 | 금지·해제 조건 |
|---|---|---|---|
| phone binary error | AI Hub target–realized operation에서 파생한 silver label 가용 | S104–S107 provisional 학습·평가 | 독립 gold로 표현 금지; D07 sensitivity 필요 |
| pronunciation 1–5 | 대부분 가용, 0 sentinel mask | outcome별 masked loss/metric | 결측을 0점으로 학습 금지 |
| comprehensibility 1–5 | 대부분 가용, 0 sentinel mask | outcome별 masked loss/metric | 결측을 0점으로 학습 금지 |
| fluency 1–5 | 부분 가용 | 관측 행만 masked loss/metric | 세 outcome의 표본 수가 같다고 가정 금지 |
| word interval/embedding | 408,790개 unlabeled representation 가용 | hierarchy의 중간 표현·aggregation | word loss·PCC 금지 |
| word accuracy human label | 없음 | 없음 | 독립 word-level 인간 label과 protocol 필요 |
| 17종 error-type interval | R1 annotation에는 존재 | coverage·taxonomy planning | target-phone multi-hot mapping과 safe label schema 전에는 학습·metric 금지 |
| D07 phone realization/error type | 미수집 | 향후 gold sensitivity | 2인 독립 평정·adjudication 뒤 사용 |
| predicted alignment | 미구현 | 없음 | oracle 결과를 배포 성능으로 표현 금지 |

현재 D07 schema에는 독립 word-level score가 없다. D07 수집 전에
`word_index`와 word-level rating 정의를 별도 amendment로 추가하지 않으면
D07이 끝나도 word PCC를 계산할 수 없다.

Error-type multi-label을 시작하려면 다음을 먼저 사전 고정한다.

1. error interval과 target phone의 overlap/경계 tie 규칙
2. 한 phone의 복수 tag, unmatched tag와 `other/uncertain` 처리
3. label-only 열과 feature 열의 물리적·논리적 분리
4. class별 support, macro/micro F1·AUPRC와 undefined-class 정책
5. silver annotation 결과와 D07 gold 결과의 명시적 구분

## 6. 네 종류의 gate

### 6.1 Engineering gate

S104/S105의 engineering 평가는 phone macro F1 0.75와 사전 지정한
utterance outcome의 ICC(2,1) 0.65를 개발 목표로 보고한다. 이는 dev-only
후보 품질 진단이며 S108 승격이 아니다. Word PCC는 현재
`not_evaluable`이다.

Metric이 미달해도 실패 report·checkpoint·registry가 계약을 모두
충족하면 해당 engineering artifact의 완성 여부는 기록할 수 있다.
하지만 metric 실패를 삭제하거나 목표값을 낮추지 않는다.

### 6.2 Adoption gate

서로 다른 두 adoption 판정을 분리한다.

- **S103 backbone branch**:
  `mean(Large phone macro F1 - Base+ phone macro F1) >= 0.03`의 사전
  고정 point-estimate rule
- **S105 architecture adoption**:
  simple control 대비 utterance ICC가 0.03 이상 또는 phone macro F1이
  0.03 이상 개선되고, 아래 `worst_group_drop < 0.05`를 동시에 만족

S105 비교의 primary outcome, point estimate/CI 사용 방식, multiplicity
family는 dev를 열기 전에 config에 고정한다. S103 gate를 S105 구조
채택의 대체 증거로 쓰지 않는다.

### 6.3 S108 research-candidate gate

다음 항목을 모두 판정한 promotion report가 필요하다.

- phone macro F1 `>= 0.80`
- 사전 지정 utterance outcome의 ICC(2,1) `>= 0.70`
- ECE `<= 0.10`
- issued phone correction precision `>= 0.90`
- `max_worst_group_gap <= 0.10`
- G04, D07, 재현 hash-chain과 단일 locked batch 계약 통과

세 utterance outcome 중 어느 것을 primary ICC로 쓸지는
`TBD_PREREGISTRATION`이다. Dev 또는 calibration 결과에서 가장 높은
outcome을 사후 선택하지 않으며, S104 첫 dev 실행 전에 고정한다.

Word PCC와 “세 주요 지표 중 두 개 이상 유의 개선” 규칙은 현재
`pending_preregistration`이다. Word label이 없으므로 word PCC를 0, pass,
대리 label 또는 phone-derived label로 바꾸지 않는다. Word가 `N/A`인
상태를 2-of-3의 성공 한 개로 세지 않으며, 다른 metric으로 조용히
대체하지 않는다. 다음 중 하나를 locked test를 열기 전에 명시적으로
승인해야 한다.

1. 독립 word label을 수집해 원래 2-of-3을 유지
2. 독립 자료를 보지 않은 protocol amendment로 새 primary family를 고정

둘 다 없으면 S108은 `blocked_preregistration`이며 pass가 아니다.

### 6.4 Deployment gate

S108 research candidate는 S200을 시작할 수 있는 S1 후보일 뿐이다.
Deployment 검토에는 별도로 다음이 필요하다.

- S2/S203 독립 감사 gate
- E01 metamorphic pass rate `>= 0.95`
- C03 200건 blind 전문가 감사에서 위치·유형 정확도 각각 `>= 0.90`,
  unsupported claim rate `< 0.02`
- predicted-alignment 성능과 oracle-alignment sensitivity
- 개인정보·라이선스·개인 연구 scope attestation과 운영 rollback 경로

이 deployment 판정은 WP4/S108의 1점을 추가로 주는 milestone이 아니다.

## 7. subgroup gap 정의

두 종류의 gap을 혼용하지 않는다.

### 7.1 S105 adoption의 candidate 하락

사전 고정한 eligible subgroup 집합을 \(G\)라 할 때:

```text
worst_group_drop =
  max_g(F1_simple_control,g - F1_candidate,g)
```

`worst_group_drop >= 0.05`이면 candidate를 채택하지 않는다. 음수 개선을
0으로 자르지 않고 group별 차이도 함께 보고한다.

### 7.2 S108의 group 간 격차

각 slice axis \(a\)의 eligible group 집합을 \(G_a\)라 할 때:

```text
axis_gap_a = max_g(F1_g) - min_g(F1_g)
max_worst_group_gap = max_a(axis_gap_a)
overall_to_worst_gap_a = F1_overall - min_g(F1_g)
```

S108 gate는 `max_worst_group_gap <= 0.10`이다.
`overall_to_worst_gap_a`도 보조 지표로 함께 기록한다. Primary axis는
L1(Chinese/Japanese), gender(F/M), proficiency이고 duration과 error-type은
감사 slice로 보고한다. Group의 최소 화자 수, positive/negative support와
결측 처리 기준은 `TBD_PREREGISTRATION`이며 locked test 전에 고정하지
않으면 S108 gap gate를 판정할 수 없다.

## 8. calibration과 발행 정책

### 8.1 ECE 상태

15-bin ECE를 primary로 하되 bin 경계와 빈 bin 처리, equal-width 여부를
calibration config에 고정한다.

| ECE | 상태 | 자동 발행 정책 |
|---:|---|---|
| `<= 0.10` | calibration gate pass | 다른 gate도 통과할 때만 후보 |
| `> 0.10` and `<= 0.15` | gray zone, promotion fail | offline 분석·abstention만; 자동 교정 발행 금지 |
| `> 0.15` | hard fail | 자동 coaching 비활성화, 데이터·label·loss 단계로 복귀 |

Gray zone을 반올림해 pass로 만들지 않는다. Calibration split을 다시 보고
temperature나 threshold를 반복 탐색한 결과도 새 독립 calibration 자료
없이는 승격 근거가 아니다.

### 8.2 issued phone correction precision

현재 binary phone scorer의 발행 단위는
`(utterance_id_sha256, phone_index)`다.

```text
issued = not_abstained and calibrated_error_probability >= issue_threshold
correct_issue = issued and reference_binary_error_label == 1
issued_precision = sum(correct_issue) / sum(issued)
issued_coverage = sum(issued) / number_of_eligible_phone_rows
```

Error type을 발행하지 않는 binary model에서 이 값은 **오류 위치 발행
precision**이다. 오류 유형과 coaching 문장 정확도를 뜻하지 않는다.
Type을 함께 발행하는 후속 model은 같은 위치에서 type까지 일치하는 별도
`typed_issue_precision`을 보고한다.

다음 값은 아직 문서 근거가 없어 `TBD_PREREGISTRATION`이다.

- `minimum_issued_phone_count`
- `minimum_issued_coverage`
- CI 방법과 confidence level
- gate가 point precision인지 CI lower bound인지
- 동일 발화의 여러 phone을 고려하는 cluster uncertainty 방식

이 값들을 calibration label이나 결과를 본 뒤 정하지 않는다. 모두
사전등록되기 전에는 precision 0.90만으로 S107/S108 pass를 선언할 수 없다.
AI Hub silver label precision과 D07 expert-gold precision을 별도 필드로
보고하고, 최종 S108에는 expert-gold 판정을 요구한다.

연속 1–5 bounded regression 출력에는 현재 class probability가 없으므로
phone ECE/Brier를 utterance calibration으로 확대 해석하지 않는다.
Utterance calibration을 요구하려면 ordinal logit/distribution 또는 별도
interval-coverage metric을 S105 전에 고정한다.

## 9. 단계별 구현·산출물 계약

모든 registry는 config/data/model/source/checkpoint hash, seed,
runtime/GPU/git, metric split, artifact hash와 failure를 기록한다.
`.env*`, raw locator, 원본 stem과 직접 식별자는 등록하지 않는다.

| Stage | 필수 구현·검사 | 필수 실험 산출물 | 논문 후속 반영 |
|---|---|---|---|
| S104 | online encoder trainer, trainable-parameter allowlist, protected-split guard, OOM·atomic failure test | variant config, seed별 checkpoint·phone/utterance dev prediction·slice, resource/trainable-param report, aggregate report, completed registry | adapter/partial 방법과 dev-only ablation row |
| S105 | grouped phone/word/utterance loader, interval mapping, masks, incremental variant comparator | variant별 checkpoint/prediction/slice/report/registry, hash-validated ablation table | HiPAMA-like 구성요소별 ablation과 limitation |
| S106 | frozen component inference, stack row/hash join, speaker OOF guard, label-feature leakage test | stack component matrix, fold manifest, OOF prediction, final fusion checkpoint, stack-only report/registry | explicit/SSL/fusion 비교와 disagreement |
| S107 | binary calibrator, split guard, reliability/risk/threshold runner | calibration prediction, calibrator params, threshold policy, ECE/Brier/reliability/risk/slice report와 registry | reliability diagram과 risk-coverage |
| S108 dry-run | state machine, external-gate/hash/locked-count fault tests | no-locked checklist report | 결과 수치나 PENDING 해제 없음 |
| S108 final | one-shot locked batch orchestrator, fail-closed promotion evaluator | batch manifest, B05+S108 predictions, confusion/slice/bootstrap/Holm, promotion report/registry | 최종 S1/B05 표, forest/reliability/risk와 evidence-map |

Prediction과 row-level matrix는 D1 제한구역에 둔다. 논문 표·그림과 외부
후보 report에는 A1 aggregate만 사용한다. Stage별 artifact 수는 config에서
사전 고정하고 누락·중복·추가 artifact가 있으면 completed로 판정하지
않는다.

### 9.1 S104 구현 순서

1. S103 branch와 base model revision을 pin한다.
2. adapter/LoRA를 먼저 구현하고 trainable parameter를 전수 검사한다.
3. adapter 결과와 자원 측정 뒤에만 top-N block partial 후보를 연다.
4. 최초 실험에서 Large full fine-tuning을 실행하지 않는다.
5. pooled frozen cache는 encoder gradient를 전달할 수 없으므로 raw audio를
   R0/R1 안에서 읽는 online path 또는 별도 승인된 layer/frame cache를
   사용한다.

### 9.2 S105 구현 순서

한 번에 여러 요소를 합치지 않는다.

1. 선택된 S104 encoder와 독립 simple head control
2. flat shared multi-task control
3. final-layer phone→word→utterance aggregation
4. layer-wise weighted sum
5. level별 multi-aspect attention

현재 unlabeled word는 중간 representation으로만 쓴다. Word supervised
head를 빈 label이나 phone aggregate로 학습하지 않는다. Layer-wise
weighted sum은 final hidden pooled cache만으로 구현할 수 없으므로 online
hidden states 또는 새 cache contract가 필요하다.

### 9.3 논문·추적기 갱신 순서

각 stage의 실제 report와 registry를 독립 검증한 뒤에만 다음을 수행한다.

1. P02의 해당 ablation/calibration 표에 aggregate 값을 추가
2. P04 방법·통계·실패·한계 문장을 완료 시제와 계획 시제로 분리
3. evidence-map에 새 artifact hash와 claim을 추가
4. PENDING marker는 정확한 해제 artifact가 모두 생긴 경우에만 변경
5. tracker의 상태와 고정 배점 원장을 증거에 맞게 갱신
6. P05 validator로 artifact/claim/PENDING/locked guard를 재검증

Dry-run, synthetic fixture 또는 provisional silver result는 위 결과
marker의 해제 근거가 아니다.

## 10. S108 상태기계와 점수 금지 조건

허용 상태 전이는 다음과 같다.

```text
waiting_s103
  -> engineering_in_progress
  -> blocked_preregistration | blocked_g04 | blocked_d07
  -> ready_for_locked_batch
  -> locked_batch_running
       +-> research_candidate_failed
       +-> research_candidate_passed
             -> deployment_pending
             -> deployment_approved | deployment_rejected
```

`blocked_*`는 조건이 충족되면 `ready_for_locked_batch`를 다시 평가할 수
있지만 locked row를 미리 읽을 수 있다는 뜻이 아니다.

다음 조건에서는 S108 완료 또는 WP4 S108 점수를 부여하지 않는다.

- dry-run 또는 synthetic test만 완료
- G04 240/240·228/240 gate 미완료
- D07 calibration/locked gold와 합의도 미완료
- word/2-of-3 또는 precision CI·coverage preregistration 미해결
- model, fusion, calibrator, threshold, source 또는 data hash 누락
- stack/calibration split 역할 위반
- locked test 조기 접근, 두 번째 접근 또는 결과 기반 재선택
- oracle-only 결과를 predicted-alignment/deployment 결과로 표현
- ECE gray/hard fail, issued precision, phone/ICC 또는 subgroup gate 미달
- failure를 숨기거나 registry가 `completed`가 아님

`research_candidate_failed`는 재현 가능한 최종 결과지만 **승격 완료**가
아니다. 실패 artifact는 보존하고 후속 단계 중지 사유를 기록한다.

## 11. 외부 gate의 분리

### 11.1 G04

G04는 고정된 240건의 독립 수동 정렬 감사다. 240/240 유효 판정,
최소 228건 correct, scorer `status=passed`, privacy-safe export/report
hash와 연구책임자의 reviewer 자격 확인이 모두 필요하다.

### 11.2 D07

D07은 G04와 다른 표본·목적이다.

- 전문가 2인 독립 평가와 제3 전문가 adjudication
- calibration split 800건
- locked-test 최대 1,200건
- calibration 800건의 ICC/QWK 0.70 또는 taxonomy 합의 0.65 gate
- locked 평가 시작 뒤 label 정의와 primary metric 변경 금지
- 두 원평정과 adjudicated gold에 대한 sensitivity

G04의 240건을 D07 완료로 재사용하지 않는다. G04 통과만으로 word label,
error-type gold, expert calibration 또는 issued correction precision이
생기지 않는다.

## 12. 기존 문서와의 충돌 해소

| 기존 문구·문서 | 이 계약의 해석 |
|---|---|
| 고성능 GPU 계획 G1의 “동일 seed와 locked test” | 동일 seed만 유지한다. Locked test는 G04/D07/preregistration 뒤 B05+S108 단일 batch에서만 연다. |
| 구현 계획 Phase 2의 hierarchy/fusion 뒤 Large 순서 | 역사적 설계 순서다. 현재 실행은 S103 Large 비교 뒤 S104→S105→S106→S107→S108이다. |
| 개인정보 경계의 train/dev 허용 문구 | S104/S105에 적용한다. S106/S107은 이 문서의 stage-specific stack/calibration D1 contract만 예외로 허용한다. |
| CLAIM-G04와 tracker G04 | CLAIM-G04는 통계 계약 alias, plain G04는 수동 정렬만 뜻한다. |
| B05 locked run과 S108 locked run | 별도 접근이 아니라 하나의 사전등록 locked evaluation batch다. |
| S108 promotion과 일반 모델 승격 | S108은 research candidate, S2/E01/C03 뒤의 별도 판정이 deployment다. |
| word PCC와 2-of-3 | 독립 word label 또는 사전 승인 amendment 전에는 pending이며 pass로 세지 않는다. |

이 표는 과거 문서의 역사적 설명이나 이미 생성된 결과를 삭제하지 않는다.
S104–S108을 새로 실행할 때의 권한과 판정만 정본화한다.
