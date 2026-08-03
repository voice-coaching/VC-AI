# WavLM S106 4-component stack fusion 구현·실행 준비 기록

최종 갱신: 2026-07-29 KST  
정본 계약: [WP4 S103 이후 실행 계약](WP4_POST_S103_EXECUTION_CONTRACT_KO.md)  
상태: **B07 포함 production component generator·bundle v3 consumer·deep
validator 구현 및 합성 검증 완료 / 실제 component projection·S106 실행 0건**

## 1. 이번 개정의 결론

기존 S106의 3-component·9 prediction file·20-feature 계약을 다음
4-component production 계약으로 개정했다.

```text
explicit
  -> s104_adapter
  -> s105_selected_hierarchy
  -> gop_acoustic_posterior
```

- component 순서는 위 배열로 고정한다.
- seed `42,43,44`를 component마다 사용하므로 prediction file은 정확히
  `4×3=12`개다.
- component마다 seed mean 기반 6개 feature를 만들고 alignment 2개를
  더하므로 feature는 정확히 `4×6+2=26`개다.
- fixed-threshold component control에 `gop_only`를 추가했다.
- component bundle은 v3, generator commit은 v2, execution lock과 S106
  commit은 v2로 승격했다.
- B07의 기존 dev prediction은 입력으로 사용할 수 없다. 정본 acoustic/error
  serialized checkpoint를 stack row에 재실행한 probability만 허용한다.

이 개정은 실제 S106 결과 완료를 뜻하지 않는다. Production component
generator, reviewed S104/S105 checkpoint loader와 stack identity/label
materializer까지 구현했지만 실제 S104/S105 completed commit이 없다.
따라서 정상 실행은 S104 commit 존재 검사에서 먼저 fail-closed하며 실제
execution lock, 12 prediction file, component bundle, OOF metric,
generation, registry, commit은 모두 0건이다. S106 점수는 계속 `0/1`이다.

## 2. B07 정본 lineage

S106 config는 다음 B07 정본을 exact pin한다.

| 항목 | 값 |
|---|---|
| config SHA-256 | `9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0` |
| commit SHA-256 | `ff31a88e694d23c89996193646d7c062069c9e776b5ed72ef7fc354950f3907c` |
| registry SHA-256 | `5db35282cab879b3a05fa5a6b5cd9a5cac00c5469215ffc77a6dc34abf95eb12` |
| generation ID | `bc1a7eb174c6a8f69627292f30c9229235dcde8e10e69a7e07899847986d6e09` |
| artifact 수 | 21 |
| validator | `voice_coach.training.b07_gop_training.validate_b07_commit` |

Preflight 검증 순서는 반드시 `S104 → S105 → B07`이다. 세 public validator와
선택/adoption evidence가 모두 유효해진 뒤에만 stack path를 resolve한다.
B07 public validator는 얕은 hash 검사만 하는 함수가 아니다. Canonical
train/dev를 다시 읽어 full train/selection/dev를 재계산하고, 각 seed의
저장된 `dev_predictions.parquet`를 읽어 재계산값과 대조한다. S106은 이
접근을 `predecessor_deep_audit`로 기록한다. 그 뒤 fusion component
생성에는 B07 dev prediction을 재사용하지 않고 정본 acoustic/error
serialized checkpoint만 stack row에 적용한다. 즉 검증 중 읽기와 fusion
입력 재사용은 서로 다른 접근 경계다.

정본 checkpoint에 대한 별도 read-only 호환성 probe에서는 B07 seed 42
serialized checkpoint가 1×768 nonzero label-free stack fixture에서 유효한
확률 `0.8058701214`를 만들었고, zero-norm embedding은 GOP guard에서
거부됐다. 정본 explicit seed 42 checkpoint도 exact 6-column label-free
row에서 유효한 확률 `0.5563569149`를 만들었다. 이 값은 호환성 점검값이며
S106 metric이나 논문 evidence가 아니다.

## 3. 구현 파일과 역할

| 역할 | 경로 |
|---|---|
| 4-component preregistration | `configs/experiments/wavlm_s106_stack_fusion_v1.yaml` |
| production component input 계약 | `configs/experiments/wavlm_s106_component_inputs_v1.yaml` |
| fail-closed preflight | `scripts/prepare_wavlm_s106_fusion.py` |
| production component generator CLI | `scripts/generate_wavlm_s106_component_inputs.py` |
| bundle consumer·train·recovery·deep validate CLI | `scripts/train_wavlm_s106_fusion.py` |
| static/preflight contract | `src/voice_coach/training/wavlm_s106_fusion_contract.py` |
| label-free projector interface·bundle builder | `src/voice_coach/training/wavlm_s106_component_projection.py` |
| production loader·materializer·publisher·deep validator | `src/voice_coach/training/wavlm_s106_component_generator.py` |
| fusion consumer·publisher·deep validator | `src/voice_coach/training/wavlm_s106_fusion_training.py` |
| generator unit·합성 E2E·fault test | `tests/unit/test_wavlm_s106_component_generator.py` |
| contract unit test | `tests/unit/test_wavlm_s106_fusion_contract.py` |
| projector/provenance unit test | `tests/unit/test_wavlm_s106_component_projection.py` |
| bounded synthetic E2E·fault test | `tests/integration/test_train_wavlm_s106_fusion.py` |

`wavlm_s106_component_projection.py`는 label 인자를 받지 않는 네 interface를
제공한다.

- explicit: committed phone pipeline의 `predict_proba`
- S104: adapter가 실제 적용된 target-interval pooled representation만
  받는 phone head
- S105: adapter가 적용된 all-layer target-interval pooled representation만
  받는 selected hierarchy head
- B07: serialized acoustic checkpoint로 GOP 4개를 만든 뒤 serialized error
  checkpoint로 probability를 생성

S104 interface는
`representation=adapter_applied_target_interval_pooled`,
`adapter_applied=true`, `target_interval_pooling=true`, `label_free=true`
attestation이 정확히 일치하지 않으면 raw frozen embedding을 거부한다.
S105도 adapter-applied all-layer pooled attestation을 요구한다.

Production generator는 이 interface 위에서 다음을 추가로 강제한다.

- Main S106 config가 고정한 generator config 경로와 SHA-256만 production
  identity를 주장할 수 있다.
- Public production API에는 dependency-injection 인자가 없고 canonical
  backend·preflight·bundle builder callable identity를 실행 중 재검사한다.
- 합성 전용 runner는 production과 다른 bundle/commit/registry schema와
  `synthetic_test_only` producer identity를 사용하므로 public production
  deep validator를 통과할 수 없다.
- Execution lock의 S104/S105 canonical YAML byte와 exact pinned
  WavLM Base+ config/model byte를 private read-only snapshot으로 복제한 뒤,
  세 adapted model과 한 unadapted model을 모두 그 snapshot에서만 복원한다.
  원본 config/model path를 model loader에 넘기지 않으며 load 뒤 private
  snapshot을 즉시 삭제한다.
- 한 audio당 `O_NOFOLLOW` descriptor 하나를 계속 잡고 그 descriptor에서
  얻은 같은 byte로 canonical PCM SHA-256, decode, resample을 수행한다.
  Explicit, S104, S105, B07 projection은 같은 decoded object만 소비하며
  path를 다시 열지 않는다.
- Restricted source Parquet은 임시 파일로 복제하지 않는다. `O_NOFOLLOW`로
  잡아 둔 descriptor에서 `ParquetFileFormat` fragment를 만들고 필요한
  column, stack split, exact eligible raw ID predicate를 scanner에 적용한다.
  Batch size와 batch/fragment readahead는 유한값으로 고정한다.
- S105 all-layer pooling은 canonical S105 head 호출 경로를 그대로 사용하고,
  S104는 그 adapted forward의 final encoder layer를 공유한다. B07의
  unadapted forward는 별도로 한 번 계산해 세 seed가 공유한다.

## 4. 접근 순서와 label 격리

Preflight의 강제 순서는 다음과 같다.

```text
S106 config + implementation pin 검증
  -> public validate_s104_commit (직접 1회)
  -> public validate_s105_commit + adoption 재계산
     -> S105 내부 public validate_s104_commit (중첩 1회)
  -> public validate_b07_commit + full train/dev recomputation
  -> explicit checkpoint 검증
  -> 최초 stack locator resolve/hash
  -> no-metric execution_lock.v2 atomic publish
```

따라서 성공한 preflight의 deep-audit invocation count는 정확히
`S104=2, S105=1, B07=1`이다. S104는 fit/dev metadata와 seed별 dev
prediction을 읽고, S105는 manifest/reservation/labels와 dev prediction을
읽으며 내부에서 S104 전체 validator를 다시 호출한다. B07은 canonical
train/dev와 저장된 dev prediction을 읽는다. Execution lock은 이를
`predecessor_deep_audit`의 true/count evidence로 기록한다. 반면
`fusion_feature_train_read=false`, `fusion_feature_dev_read=false`,
`predecessor_dev_predictions_reused_as_components=false`를 별도
`fusion_input_access`에 기록한다.

각 predecessor의 execution-lock record에는 `config`,
`validator_module`, `report` 세 canonical source가 모두 exact
`{path, sha256, size_bytes}` 구조로 들어간다. Config와 validator module은
정본 경로·정본 SHA-256까지 고정하고, report는 predecessor commit의
generation path 및 report artifact record와 다시 결합한다. S104는
top-level `experiment_report_sha256`도 함께 대조한다. S105 comparison
report와 B07 aggregate report도 같은 방식으로 actual path/hash/size를
재확인한다.

이 source chain 검사는 preflight 발행 직전, component bundle document
생성, execution-lock 검증, bundle 검증, run 시작, precommit 재검증,
completed-registry recovery, `--validate-commit-only`에서 반복된다.
따라서 lock 발행 뒤 config·validator module·report 중 하나라도 drift하면
row-level bundle이나 최종 commit을 신뢰하지 않고 fail-closed한다. 이
보강은 기존 public-validator 호출 의미를 줄이지 않는다. Preflight의
S104 직접 1회와 S105 내부 중첩 1회는 그대로 유지한다.

Production component generator의 실제 호출 순서는 다음과 같다.

```text
S104→S105→B07→explicit predecessor 검증
  -> 최초 stack locator resolve
  -> label-free identity/target/word interval scan
  -> exact eligible raw source ID를 transient memory에만 보존
  -> frozen checkpoint probe
  -> private read-only model/config snapshot 생성
  -> CUDA/model 초기화
  -> 같은 descriptor byte로 audio decode/resample
  -> identity + component×seed 12 prediction 생성
  -> sealed content-addressed producer generation publish·fsync
  -> canonical feature-freeze receipt를 별도 inode public view로 publish
  -> exact eligible raw ID predicate를 push down한 뒤에만 phone_alignments open
  -> anonymous·unlinked staging에 label row를 bounded batch로 streaming
  -> 물리적으로 분리된 label matrix 생성
  -> generator commit·completed registry 준비
  -> producer evidence tree deep validation
  -> component bundle v3를 마지막 visibility commit으로 publish
```

Label materializer는 raw `id`를 artifact에 기록하지 않는다. Label-free scan이
보존한 exact eligible raw ID 집합을 `split == stack AND id IN (...)`
predicate로 Parquet scanner에 push down한다. 반환된 모든 raw ID를 다시
frozen utterance hash 집합과 대조하며, 비대상 ID·중복·순서·target label
불일치는 실패한다. Target label은 canonical cleaner와 동일하게
`str(value or "").strip()`한 뒤 비교한다.

Bundle consumer의 실제 호출 순서는 다음과 같다.

```text
execution lock + frozen checkpoint file 전수 재검증
  -> generator production identity·exact producer evidence tree와
     bundle.v3 및 12 prediction provenance 검증
  -> label-free feature_identity matrix open
  -> 12 stack prediction open, seed aggregation, exact-key join
  -> 26-feature array 완성
  -> canonical feature-freeze SHA-256 계산 + NumPy writeable=false
  -> 그 뒤에만 별도 label matrix content/computed-hash read/exact-key join
  -> speaker-disjoint 5-fold OOF selection + final fit
```

`build_component_matrix`는 label matrix를 먼저 열지 않는다. Instrumented
integration test가 `identity → component×12 → feature_freeze → labels`
event 순서를 검사하고, label open 시 feature array가 read-only임을 확인한다.
Bundle 검증 단계에서는 label의 선언 path, SHA-256 문자열 형식, stat,
row count를 볼 수 있지만 label content와 computed SHA-256은 feature freeze
뒤까지 지연한다.

Identity, label, 12개 component Parquet은 no-follow descriptor와
descriptor/path inode·size·mtime·ctime 결합을 parse 전후에 대조한다.
Restricted source stack과 label scan은 위 held-descriptor fragment scanner를
사용하며 disk snapshot을 만들지 않는다. Label writer는 anonymous
`TemporaryFile`을 사용하므로 materialization 전 staging inode의 link count는
0이고, exact row를 8192 이하 batch로 흘린다. 따라서 hash 뒤
replace/restore, 무제한 source batch, restricted Parquet 임시 복제,
feature freeze 전 label visibility를 fusion input으로 만들 수 없다.
Bundle evidence의 중첩 mapping은 방어적으로 deep-copy/deep-freeze하고,
precommit과 read-only validator는 정본 bundle과 producer
commit/registry/receipt/generation을 다시 읽어 normalized record digest와
feature-freeze digest를 재계산한다.

### alignment confidence fail-safe

현재 modeling Parquet에는 독립 `alignment_confidence` column이 없다.
`phone_alignments` struct에는 `realized_label`과 `operation`이 함께 있어
feature 단계에서 이 struct를 열면 realization/label leakage가 생길 수 있다.
따라서 canonical identity loader는 현재 `alignment_confidence=null`만
허용하고 다음 두 값을 강제로 만든다.

```text
alignment__target_interval_confidence = 0.0
alignment__confidence_missing = 1.0
```

Reviewed realized-independent confidence source가 생기기 전에는 non-null
confidence를 거부한다. 이 source의 부재는 canonical null+missing 경로로
결정론적으로 처리되므로 실행 blocker가 아니며
`stack_projection.current_blockers`에도 포함하지 않는다. 구현된 label
materializer만 feature-freeze receipt 발행 뒤 `phone_alignments`를 열어
exact target interval/key와 정규화한 target label을 확인한 뒤 operation을
binary target으로 변환한다.

## 5. bundle v3와 prediction provenance

Bundle schema는
`voice_coach.wavlm_s106_component_bundle_commit.v3`다. 다음을 exact
binding한다.

- canonical execution lock path
  `artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1/execution_lock.json`
  과 실제 pretty-canonical document SHA-256
- lock에 고정된 stack source path/SHA-256
- lock의 `frozen_components` 전체
- label-free feature identity matrix
- 물리적으로 분리된 5-column label matrix
- 네 component×세 seed의 12 prediction record
- projection audit와 canonical `binding_sha256`
- production generator commit v2, completed registry, feature-freeze receipt,
  producer identity와 sealed content-addressed generation tree

Feature identity column은 다음 7개다.

```text
utterance_id_sha256, speaker_id_sha256, phone_index, split,
alignment_confidence, qc_flagged, reserved_prompt
```

Label matrix column은 다음 5개다.

```text
utterance_id_sha256, speaker_id_sha256, phone_index, split,
reference_binary_error_label
```

Prediction Parquet column은 다음 5개다.

```text
utterance_id_sha256, speaker_id_sha256, phone_index, split, probability
```

Prediction record는 path/hash/size/row count 외에 schema v2, exact component,
seed, `split=stack`, source lineage SHA-256, seed별 source checkpoint SHA-256
목록과 projection attestation을 가진다. Path는 다음 형식으로 고정한다.

```text
.../stack_component_predictions/<component>/seed_<seed>_stack_predictions.parquet
```

Parquet metadata에도 schema/component/seed/split/source lineage/checkpoint
목록/attestation hash를 중복 기록한다. Record와 metadata가 다르거나
dev/train/locked path, B07 dev reuse, binary label 사용, realized identity
사용 attestation이 있으면 실패한다. 특히 B07 seed record는 lock에 고정된
acoustic와 error checkpoint 두 hash가 모두 일치해야 한다.

한 production generation의 tree는 receipt, identity, 네 component×세
seed prediction 12개만 허용한다. Generation ID는 feature digest와 같아야
한다. Generation root부터 모든 entry를 `lstat`해 symlink, 비정규 파일,
추가 entry와 hard-link를 거부한다. Sealed tree directory/file mode는
각각 `0550`/`0440`, public identity/receipt/prediction/label view는
`0440`, link count는 1이어야 한다. Public view는 generation 원본과 byte가
같지만 device+inode가 다른 별도 copy여야 한다. Label은 feature-freeze 뒤
별도 경계에서만 발행한다. Rename 뒤 seal 전 crash가 발생한 경우에는
바이트와 exact tree가 같은 no-link generation만 재시도에서 seal하며,
변경되거나 추가된 tree는 거부한다.

Production publication 전체는 `/tmp`의 persistent `flock` single-writer
lock으로 직렬화한다. Process crash 시 kernel이 lock을 회수하며, lock
inode의 owner/mode/link count를 검사한다. 각 staged file은 `O_EXCL`로
생성하고 `0440` chmod와 file fsync를 끝낸 뒤에만 hard-link visibility
commit을 수행한다. Rename/link 뒤 parent directory도 fsync하며,
성공 반환 전 production commit/registry/receipt/generation/public view를
deep validation한다. Private Base+ snapshot은 성공·예외 모두에서 삭제하고
부모 directory까지 fsync해 잔존을 허용하지 않는다.

## 6. 26-feature OOF와 출력

네 component 각각은 seed aggregation 뒤 다음 6개만 제공한다.

1. probability mean
2. mean probability에서 파생한 logit
3. `abs(2p-1)` margin
4. binary entropy
5. population seed probability variance
6. missing indicator

여기에 alignment confidence와 missing indicator 두 개를 더해 exact
26-feature allowlist를 만든다. Partial seed presence는 실패하고, 한
component의 세 seed가 모두 빠진 row만 neutral imputation을 허용한다.

Speaker SHA-256 namespace modulo 5로 fold를 고정한다. Standardized L2
logistic regression의 `C=[0.01,0.1,1,10]`, threshold
`0.10..0.90 step 0.05`를 aggregate OOF speaker-macro phone-macro F1로만
선택한다. Control은 `explicit_only`, `s104_adapter_only`,
`s105_selected_hierarchy_only`, `gop_only`,
`unweighted_component_probability_mean`이다.

선택 뒤 full stack을 한 번 fit하지만 in-sample prediction/metric은
계산·보고하지 않는다. 출력 generation은 기존과 동일하게 정확히 7개이며,
row-level artifact는 D1, aggregate report는 A1이다. Commit schema는
`voice_coach.wavlm_s106_stack_fusion_commit.v2`다. Completed registry 뒤
commit write만 중단된 경우 deep validation 후 commit만 복구한다.

## 7. 합성 검증 범위

관련 unit/integration test는 다음을 포함한다.

- 4 component, 12 prediction, 26 feature, `gop_only`
- exact production API signature와 alternate generator config identity 거부
- production/synthetic bundle·commit·registry schema 격리
- bundle v3의 exact generator commit/registry/receipt/producer identity
  lineage와 문서-only 수동 fixture 거부
- config-only predecessor/checkpoint/stack/raw/GPU/output 접근 0
- S104→S105→B07 validator ordering
- strong execution-lock document/path binding mutation
- dev prediction path와 source checkpoint provenance mutation
- same-held-byte audio decode와 path swap 거부
- private immutable model/config snapshot 복원과 정본 source 재검증
- exact eligible raw ID predicate pushdown과 raw ID 비영속성
- held descriptor Parquet fragment scanner의 column/filter pushdown,
  bounded batch/readahead와 restricted disk snapshot 0
- anonymous unlinked label staging과 bounded label-row streaming
- explicit pipeline class order `[0,1]`와 S105 exact all-layer head 경로
- B07 seed/document cross-binding 거부
- exact generation tree, generation ID, public-copy inode 분리
- raw frozen S104 representation attestation 거부
- synthetic serialized B07 checkpoint의 label-free probability replay
- 40 phone row·10 speaker·5-fold end-to-end generation/registry/commit
- identity→feature freeze/hash→label open 순서 instrumentation
- 동일 byte snapshot Parquet read와 parse 중 mutation 거부
- BundleEvidence nested record deep-freeze와 redirect mutation 거부
- 세 predecessor의 config/module/report 9개 source drift 전수 거부
- bundle build, run, precommit, commit-only source 재검증
- partial seed, speaker mismatch, forbidden label column
- lock/frozen lineage/artifact/full-fit metric claim tamper
- concurrent writer, immutable publication, completed-registry recovery
- crash-released persistent single-writer `flock`, exact mode/link count,
  rename→seal crash recovery와 changed/extra tree 거부
- file/directory fsync, private snapshot 강제 cleanup과 production
  post-publish deep validation
- read-only commit validator의 workspace hash/size/mtime 불변

S106 focused unit/integration은 **98/98**, S107/S108 focused 회귀는
**44/44**, 직접 downstream 회귀는 **226/226** 통과했다. 동결 exact8
기준 Ruff format/lint 9파일, 기본 mypy production 5파일,
`--ignore-missing-imports` mypy 9파일이 통과했다. 기본 mypy 9파일의
유일한 두 진단은 project에 설치되지 않은 PyArrow stub
(`pyarrow`, `pyarrow.dataset`)이다. 최종 문서·hash freeze 뒤 저장소 전체
회귀를 한 번 실행하고 최종 결과는 연구 완료 tracker에 기록한다.

Synthetic metric은 논문 evidence가 아니므로 기록하지 않는다. 현재 정본
config-only는 predecessor/checkpoint/stack/raw/GPU/output 접근이 모두
0이다. 정상 generator 실행은 S104 commit 부재에서 exit 1로 멈췄고
component output과 registry는 0건이다. 따라서 실제 predecessor audit
train/dev/dev-prediction 접근, stack path/row/label, calibration/locked-test,
raw audio, environment file, CUDA 접근과 실제 S106 output은 모두 0이다.
다만 S104/S105가 준비되어 성공 preflight에 도달하면 위에 명시한
predecessor deep-audit train/dev/dev-prediction 접근은 의도적으로 true가
되며, fusion input 재사용만 계속 false다.

## 8. 현재 blocker와 다음 실행 순서

정본 `stack_projection.current_blockers`의 exact blocker는 다음 두 가지다.

1. `s104_validated_commit_absent`
2. `s105_validated_commit_absent`

Realized-independent alignment confidence source도 없지만, 이는 canonical
v2의 null+missing fail-safe로 feature leakage 없이 처리하므로 세 번째
blocker로 세지 않는다.

다음 순서는 다음과 같다.

1. S104 actual completed commit을 만들고 public validator를 통과시킨다.
2. S105 actual completed/adoption commit을 만들고 public validator를
   통과시킨다.
3. `S104→S105→B07` preflight를 통과해 execution lock v2를 발행한다.
4. Production generator로 exact 12 prediction, feature-freeze receipt,
   별도 label matrix와 component bundle v3를 발행하고 public deep
   validator를 통과시킨다.
5. S106 OOF/final fit, 7-artifact generation, registry, commit v2를 발행하고
   별도 `--validate-commit-only`와 실제 C/H/M 감사를 통과시킨다.
6. 유효한 S106 commit 뒤에만 S107 calibration을 시작한다.

전체 점수 `48/100`, WP4 `11/18`, S106 `0/1`은 바꾸지 않는다.

## 9. 동결 source audit

| 파일 | SHA-256 |
|---|---|
| generator config | `feafe24c7bcd32130519b884e39bf27fe4ae77d372004de8137cf06d963572b0` |
| generator CLI | `249a97476ce9635dcf7fcc481a7cd1b13272a110c92e9eab927f4dba59cf4314` |
| generator module | `31b15dde4d4a2ae4ee0b069a6268a03e951cf0593b39a0e047cde64d4311df5b` |
| projection module | `baeb6192517989d9c6ca42859a5c27e379853652842008e6893baa355a12340e` |
| contract module | `8210581f6d3a9b15b8af6c8d769d0b0a1d51e6b7410801f4210903b5ec037793` |
| fusion training module | `e8366b150f507b12a48a7608fbbb2f9af619fcdd7eb10ffaceda4c9952d88097` |
| fusion training CLI | `b1ed2979e086d32aa0fdbbed70e64bb9c5f8039617132cc48195edacc888d88c` |
| S106 main config | `196ed40aa59c77af1f7d4d975941b90229d21a9de16f4109e7621b4c3fbcaabb` |

이 hash는 source/config 동결값이다. Readiness 문서 hash와 전체 회귀 수는
최종 문서 동결 뒤 별도로 tracker에 기록한다.
