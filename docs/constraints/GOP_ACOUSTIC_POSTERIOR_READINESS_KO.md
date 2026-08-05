# B07 음향 사후확률 GOP 기준선 실행 계약

최종 갱신: 2026-07-29 KST  
상태: **production static 구현 100% / 합성 검증 완료 / 실제 학습·dev metric 0%**  
정본 진행 원장:
[연구 완료 추적기](RESEARCH_COMPLETION_TRACKER_KO.md)

## 1. 목적과 현재 판정

구현계획의 S1 Branch B와 Phase 1은 CTC posterior 또는 acoustic
posterior 기반 GOP 기준선을 요구한다. 그러나 지금까지 저장소에는 GOP
feature, 설정, 학습기, checkpoint, prediction, report, registry와 commit이
없었다. B07은 이 결손을 채우는 개발 전용 실험이다.

현재 구현하고 고정한 범위는 다음과 같다.

- WavLM Base+의 **동결된 768차원 target-interval embedding**으로 38종
  정규 phone의 음향 사후확률을 근사한다.
- 정상(match) train 구간만 phone posterior의 class prototype을 학습하는 데
  사용한다.
- 모든 train/dev 구간에서 target phone과 가장 강한 경쟁 phone의
  log-posterior 차이를 GOP로 계산한다.
- GOP와 B03의 여섯 explicit phone feature를 결합한 balanced logistic
  error detector를 speaker-disjoint train 내부에서 선택한다.
- seed 42/43/44별로 full primary train refit 뒤 dev를 정확히 한 번
  추론한다.
- config-only preflight, bounded CPU runner, content-addressed publisher,
  completed registry, immutable commit과 read-only full recomputation
  validator를 구현했다.

이 문서의 수치 중 phone inventory와 row 수는 입력 계약 감사값이다.
성능 수치가 아니다. 합성 실행은 production 경로의 검증일 뿐이며, 실제
학습·metric·registry·commit은 아직 0이다.

## 2. realized-phone 오염을 피하는 label 경계

원천 modeling manifest의 free-form realized 표기는 train primary에서
수백 종의 희소·오염 표기를 만든다. 이를 다중 class posterior 정답으로
직접 사용하지 않는다.

이미 검증된 Base+ phone cache는 다음 열만 모델 입력·감독 경계로
제공한다.

```text
입력:
  utterance_id_sha256, speaker_id_sha256, split, phone_index,
  target_label, embedding_mean, l1, gender, proficiency

감독:
  binary_error_label

존재하지 않으며 읽지 않는 열:
  realized_phone_identity, alignment_operation, error_tag, raw locator
```

`binary_error_label=0`인 train row의 `target_label`은 “이 embedding이
실현한 정규 phone class”로 사용할 수 있다. 따라서 별도의 realized-label
join 없이 38종 clean acoustic class를 학습한다. 오류 row의
`binary_error_label`은 최종 이진 detector의 train target일 뿐 posterior
class 정답으로 사용하지 않는다. Dev의 binary label은 metric 계산에만
사용하고 어떤 fit·temperature·C·threshold 선택에도 사용하지 않는다.

이 경계는 realized-phone identity를 feature 또는 dev-side posterior
target으로 노출하지 않는다. Oracle target interval을 사용하므로 결과는
계속 `oracle_alignment=true`, `deployable_alignment_claim_allowed=false`다.

## 3. 실제 입력 감사

정본 Base+ phone cache:

```text
data/processed/aihub71490/features/wavlm_base_plus_frozen_v1/phone
```

- 전체 train/dev target interval: `2,233,039`
- train: `1,808,724` (`match=1,701,622`, `error=107,102`)
- dev: `424,315` (`match=387,203`, `error=37,112`)
- train/dev target inventory: 각각 정확히 `38`
- train/dev match-only inventory: 각각 정확히 `38`
- embedding: float16 fixed-size list `[768]`
- cache 전체 content SHA-256:
  `af210e324fd941ee636b7abcb6ecbd604ebc7cedf16c5caa97a59ab0aade1b2c`
- modeling manifest SHA-256:
  `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1`

고정 phone inventory는 다음과 같다.

```text
spn, ㄱ, ㄲ, ㄴ, ㄷ, ㄸ, ㄹ, ㅁ, ㅂ, ㅃ, ㅅ, ㅆ, ㅇ, ㅈ, ㅉ,
ㅊ, ㅋ, ㅌ, ㅍ, ㅎ, ㅏ, ㅐ, ㅑ, ㅓ, ㅔ, ㅕ, ㅖ, ㅗ, ㅘ, ㅚ,
ㅛ, ㅜ, ㅝ, ㅟ, ㅠ, ㅡ, ㅢ, ㅣ
```

결합할 explicit phone cache는 동일한
`(utterance_id_sha256, phone_index)` universe를 가져야 한다. Join은
row 수뿐 아니라 key, speaker, split, target label과 binary label의 exact
일치를 검사한다. 순서가 같다는 가정만으로 결합하지 않는다.

## 4. 사전 고정 모델

### 4.1 speaker cross-fit acoustic posterior

각 seed에서 primary train speaker를 namespaced SHA-256 규칙으로 5개 fold에
배정한다. Fold `k`의 row에는 나머지 4개 fold의 match-only row로 계산한
prototype만 적용한다. 이렇게 얻은 모든 train GOP feature는 해당 speaker를
학습하지 않은 acoustic posterior에서 나온다.

각 embedding `x`와 class prototype은 L2 정규화한다.

```text
c_j = normalize(mean(normalize(x_i)) for match train i with target j)
z_j(x) = cosine(normalize(x), c_j) / temperature
P(j | x) = exp(z_j) / sum_k exp(z_k)
```

Class prior는 uniform으로 고정한다. Temperature 후보는 train 내부
speaker-validation의 match row에서만 multiclass negative log-likelihood로
선택한다. 후보는 config 정본의
`[0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]`이고 tie-break는 작은
temperature다.

단일 OOF feature를 detector outer validation에 재사용하면 outer fold의
match embedding이 다른 fold prototype에 간접 유입될 수 있으므로 다음
nested 규칙을 구현했다.

- fold 0은 temperature validation 전용이고 detector metric에서 제외한다.
- detector outer validation은 fold 1/2/3/4를 각각 한 번 사용한다.
- outer fold `k`의 temperature는 fold 0 match NLL로 고르되 prototype은
  fold 0과 `k`를 모두 제외한다.
- detector training row의 acoustic fold가 `j`이면 prototype은 `k`와
  `j`를 직접 제외한 allowed-fold sum으로 계산한다. `full_sum -
  excluded_sum`을 쓰지 않아 부동소수점 cancellation 의존도 제거한다.
- outer validation row의 prototype은 `k` 전체를 제외한다.
- preprocessing, class weight와 detector fit도 `k` speaker를 보지 않는다.

최종 detector refit용 cross-fit feature에는 fold별 leave-one-speaker-fold-out
prototype과 fold 0에서 선택한 global temperature를 사용한다. Dev에는
선택 완료 후 full primary-train match row로 refit한 38개 prototype을
사용한다.

### 4.2 GOP feature

Target class가 `t`일 때 다음을 exact acoustic feature로 사용한다.

```text
target_log_posterior = log P(t | x)
gop_margin = log P(t | x) - max_{j != t} log P(j | x)
posterior_entropy = -sum_j P(j | x) log P(j | x)
target_is_top1 = 1[argmax_j P(j | x) == t]
```

NaN, 무한대, inventory 밖 target, 빈 class, 0-norm embedding/prototype은
조용히 보간하지 않고 실패시킨다.

### 4.3 이진 오류 detector

GOP 네 특징에 B03의 exact explicit allowlist를 결합한다.

```text
categorical:
  target_label, previous_target_label, next_target_label

numeric:
  oracle_duration_seconds, segment_rms, segment_zero_crossing_rate,
  target_log_posterior, gop_margin, posterior_entropy, target_is_top1
```

모델 family는 balanced L2 logistic regression이다. `C`와 decision
threshold는 seed별 speaker-disjoint inner validation에서 primary
macro F1으로만 선택한다. Tie-break는 `높은 F1 → 작은 C → 0.5에 가까운
threshold → 작은 threshold`다.

고정 grid는 다음과 같다.

```text
C: [0.01, 0.1, 1.0, 10.0]
threshold: start=0.20, stop=0.80, step=0.05, inclusive
```

Fold 1~4 outer-OOF probability를 row-pooled하고 그 전체에서 macro F1을
계산한다. Class weight는 outer-fold training label에서만 계산한다.
수치 feature의 median imputation과 population mean/scale, 범주 vocabulary는
fold training row에서만 fit한다. Missing/unknown token은 각각
`__MISSING__`, `__UNKNOWN__`으로 직렬화한다. 선택된 C로 모든 primary train
cross-fit feature를 refit하고, full-train prototype으로 dev를 정확히 한 번
추론한다.

비교 대상은 B03의 feature family를 같은 fold·grid로 **이번 실행 안에서
refit한 explicit control**이다. 기존 canonical B03 checkpoint/prediction/
metric을 읽거나 그것과 동일하다고 주장하지 않는다.

## 5. split·평가·누수 계약

- fit/tuning 허용: `train`만
- metric 허용: `dev`만
- 금지: `stack`, `calibration`, `locked_test`
- primary scope: L1 `Chinese/Japanese`, gender `F/M`
- seed: `42, 43, 44`
- primary metric: phone macro F1
- secondary metric: error-class AUPRC
- slice: L1, gender, proficiency, target phone
- 기존 engineering gate `macro F1 >= 0.60`을 낮추지 않는다.
- within-run explicit refit control과 같은 dev row universe에서 seed별
  paired 비교를 기록한다.
- seed 요약은 arithmetic mean과 sample SD(`ddof=1`)다. Seed uncertainty
  feature의 population variance와 혼동하지 않는다.
- Dev 결과로 model family, inventory, temperature grid, C grid, threshold
  grid를 바꾸지 않는다.
- B07의 성공·실패와 무관하게 locked test는 열지 않는다.

## 6. 필요한 immutable 산출물

정본 실행은 다음 21개 generation file과 두 terminal pointer를 모두
발행해야 완료다.

1. config와 source/input hash가 고정된 execution lock
2. seed별 acoustic/error checkpoint 3개
3. seed별 all-dev prediction Parquet 3개
4. seed별 metric·slice report 3개
5. aggregate report와 within-run explicit refit paired comparison
6. completed experiment registry
7. content-addressed generation과 immutable commit
8. 원시 prediction에서 metric·selection·hash chain을 재계산하는
   `--validate-commit-only` 독립 검증

실패한 실행은 failure registry만 남기고 completed commit을 만들지 않는다.
`.env`와 API key는 input fingerprint·registry·report에 포함하지 않는다.

Publisher 순서는 `temporary generation 작성 → exact 21-file hash →
content-addressed rename → raw metric/selection/error-checkpoint precommit
검증 → config/source/cache report+shard/generation 재hash → completed
registry → 최종 RSS/CPU boundary 재검증 → immutable commit`이다. 실패
generation은 commit 없이 recovery할 수 있다.

## 6.1 자원·실행 권한 계약

- embedding은 Arrow fixed-size-list values buffer를 8,192-row 이하
  float32 block으로 읽는다. `embedding_mean.to_pylist()`와
  `2.23M×768` full matrix materialization은 금지한다.
- prototype accumulator와 posterior score는 float64다.
- peak RSS commit gate는 8 GiB다.
- GPU/torch/accelerator probe는 사용하지 않는다.
  Production CLI는 import 전에 `CUDA_VISIBLE_DEVICES=""`와 BLAS/OpenMP
  cap 4를 설정하고 torch 미import를 fail-closed 검사한다.
- cap 4는 process 안의 thread 객체 총수가 아니라 각 native
  BLAS/OpenMP threadpool의 active limit이다. 실제 `threadpool_info()`를
  실행·validator 안에서 검사한다.
- static config는 두 실행 permission이 모두 false다. 실제 실행 전에는
  두 flag를 모두 true로 바꾸고 status를
  `approved_for_canonical_execution`으로 바꾼 reviewed config를 새 hash로
  고정해야 한다. Mixed mode는 실패한다.
- precommit은 추가 dev embedding scan 없이 raw prediction metric,
  selection tie-break와 serialized error checkpoint를 재계산한다.
  `--validate-commit-only`는 read-only로 bounded full train selection/dev
  결과와 acoustic/error checkpoint를 다시 계산한다.

## 7. S106 편입 조건

B07의 completed commit과 독립 validator가 모두 통과한 뒤에만 S106
fusion 계약을 개정한다.

- component order에 `gop_acoustic_posterior`를 추가한다.
- 세 seed prediction을 stack join 전에 mean/variance로 집계한다.
- component별 여섯 fusion feature를 동일하게 추가한다.
- 20-feature allowlist를 26-feature allowlist로 개정한다.
- `gop_only` control을 추가한다.
- 기존 explicit/S104/S105 lineage와 stack-only OOF 규칙은 유지한다.

S106을 먼저 실행한 뒤 GOP를 사후 추가하거나 같은 stack label로 두 번
선택하지 않는다.

## 8. 하위 작업 완성도

| 하위 작업 | 현재 | 완료 기준 |
|---|---:|---|
| 결손·문헌·실행계획 감사 | 100% | GOP 요구와 누락 경로를 추적기에 연결 |
| train/dev 입력·inventory 감사 | 100% | row 수·38 inventory·hash·금지 열 확인 |
| label/feature/split 계약 | 100% | realized-free, train-only 선택, dev 1회 고정 |
| config·순수 GOP 함수 | 100% | exact grid/schema/수식 guard와 unit test 통과 |
| cross-fit 학습·publisher | 100% static | nested 3-seed runner와 immutable generation/registry/commit 합성 검증 |
| 실제 train/dev 실행 | 0% | 세 seed checkpoint/prediction/report 생성 |
| 독립 결과 검증 구현 | 100% static | raw prediction·selection·checkpoint·hash chain 합성 tamper/fault/recovery 검증 |
| 실제 결과 독립 검증 | 0% | 정본 commit에서 read-only full recomputation 통과 |
| S106 amendment | 0% | B07 commit 뒤 4-component 계약 재동결 |
| 논문 반영 | 0% | 결과·실패·한계·evidence-map 수치 연결 |

설계·production static 준비도와 실제 실험 완료도를 합치지 않는다. B07의
현재 실제 연구 완성도는 `0%`이며, 전체 연구 점수도 이 문서만으로
올리지 않는다. Static config의 두 permission이 false이므로 이 구현 작업
중에는 canonical train/dev metric을 열지 않았다.

## 9. 구현 검증 증거

2026-07-29 KST의 production static snapshot에서 다음을 확인했다.

- config-only preflight는 정본 source 11개의 exact path/SHA를 검증했고
  보호 cache 경로 resolve, train/dev embedding·metadata, dev metric,
  `.env`, raw manifest, stack/calibration/locked test 접근과 출력이 모두
  0건이었다.
- B07 unit·contract·합성 integration 집중 검증 20건이 통과했다.
  합성 integration은 21-file generation, fault 후 no-commit, recovery,
  commit-only full recomputation, artifact/registry/commit tamper 실패를
  포함한다.
- 전체 저장소 회귀 검증 1,205건이 통과했다.
- 실행 전에 다른 테스트가 `torch`를 import한 process에서도 합성
  integration 2건이 통과했고, production CLI의 fresh-process
  torch/CUDA fail-closed 경계는 별도로 유지된다.
- Ruff format/check와 production·test 8개 파일의 mypy 검사가 통과했다.

이 검증은 합성 입력과 static contract에 대한 것이다. 정본 train/dev
학습, dev metric 계산, completed registry와 immutable commit 생성은
실행하지 않았다.
