# WavLM S108 no-locked dry-run 구현·실행 준비 기록

최종 갱신: 2026-07-29 KST  
정본 계약:
[WP4 S103 이후 실행 계약](WP4_POST_S103_EXECUTION_CONTRACT_KO.md)  
상태: **aggregate-only 상태기계·원자 publisher·read-only validator 구현 및
합성 검증 완료 / 실제 locked 접근·최종 평가·S108 결과 0건**

## 1. 완료 범위와 완료로 오해하면 안 되는 범위

S108 final locked evaluation을 실행하기 전의 no-locked checklist를
production 형태로 구현했다.

- S103→S104→S105→S106→S107 순서의 immutable config/source와
  commit·registry·A1 aggregate report 검증
- predecessor 검증이 처음 미완료인 단계에서 멈추는 fail-closed 순서
- G04, D07과 preregistration을 서로 독립된 외부 gate로 표시
- model/fusion checkpoint, calibrator, threshold policy와 전체 predecessor
  commit의 hash binding
- `waiting_s103`, `engineering_in_progress`, 세 종류의 `blocked_*`,
  `ready_for_locked_batch`까지만 도달 가능한 상태기계
- locked manifest locator를 config와 API에 두지 않고 row/audio/label/
  prediction/metric 접근을 모두 0으로 고정한 접근 감사
- A1 checklist 한 개만 가진 content-addressed generation과 append-only
  receipt
- symlink, traversal, no-overwrite, concurrency, TOCTOU, partial write와 네
  fault-injection 지점의 fail-closed 처리
- publication 결과를 외부 data 없이 다시 검증하는 read-only deep
  validator
- 모든 gate를 만족하는 합성 fixture와 현재 실제 미완료 상태의 dry-run

이 구현은 다음을 하지 않는다.

- locked manifest locator를 resolve하거나 저장하지 않는다.
- locked-test row, audio, label, prediction을 읽지 않는다.
- B05+S108 단일 locked batch orchestrator를 구현하거나 실행하지 않는다.
- phone F1, utterance ICC, ECE, issued precision 또는 subgroup gap을
  계산하지 않는다.
- S108 결과, S108 배점, research-candidate pass/fail 또는 PENDING 해제를
  만들지 않는다.
- `ready_for_locked_batch`가 되더라도 이 CLI가 locked batch를 시작하지
  않는다.

구현 준비도와 실제 연구 완료를 분리하면 다음과 같다.

| 범위 | 완성도 | 근거 |
|---|---:|---|
| static config·source pin·config-only | 100% | source 14/14와 no-access contract 검증 |
| aggregate state machine·publisher·validator | 100% | bounded synthetic E2E와 fault test 통과 |
| 현재 실제 predecessor chain | S103까지만 valid | S104 commit 부재·registry running |
| G04 | 0% | 0/240, completion attestation 없음 |
| D07 | 0% | expert calibration/locked gold attestation 없음 |
| final preregistration | 0% | 모든 지정 TBD와 approval/hash freeze 없음 |
| locked-test access·metric·promotion | 0% | 6종 locked 접근 0, metric 0 |
| WP4 S108 실제 artifact·배점 | 0% | dry-run은 완료·배점 근거가 아님 |

## 2. S108 전용 파일

기존 tracker, 원고, evidence-map과 S104–S107 파일은 수정하지 않았다.

| 역할 | 경로 |
|---|---|
| static policy와 predecessor/external/output 계약 | `configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml` |
| config-only·dry-run·receipt validation CLI | `scripts/prepare_wavlm_s108_no_locked_dry_run.py` |
| 상태기계·aggregate validator·publisher·deep validator | `src/voice_coach/training/wavlm_s108_dry_run.py` |
| 순수 정책·bounded E2E·fault 단위 테스트 | `tests/unit/test_wavlm_s108_dry_run.py` |
| 실제 config-only와 발행 receipt CLI 통합 테스트 | `tests/integration/test_prepare_wavlm_s108_no_locked_dry_run.py` |
| 이 기록 | `docs/constraints/WAVLM_S108_NO_LOCKED_DRY_RUN_READINESS_KO.md` |

## 3. frozen predecessor interface

Config-only에서 아래 config와 validator source SHA-256을 전부 확인한다.
이 검사는 predecessor artifact, data split 또는 output을 읽지 않는다.

| 단계 | config SHA-256 | validator/source SHA-256 |
|---|---|---|
| S103 | `4db7ae9d9d7df9e9f35df65534d18fb6d3c46cb98f45dfaa35fadcdd5fe47e27` | `8a6a9757b1ef0cfc3322935e975d9377b9580225346d4e4c6553bfabc1393381` |
| S104 | `aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9` | `a7b41d507385c7f45b102f2d1603ad4214f0247ad9e0e08c6a3640f2df6bed80` |
| S105 | `e7818240f477af9957c4e13b88479a69820bbb44d48ece2c47e9a54cfc964ea0` | `e2f7269c757ddee82f33de6787df466b29cbe9e1e2b1fed90130eaaec1c53ad1` |
| S106 | `196ed40aa59c77af1f7d4d975941b90229d21a9de16f4109e7621b4c3fbcaabb` | `e8366b150f507b12a48a7608fbbb2f9af619fcdd7eb10ffaceda4c9952d88097` |
| S107 | `6702ea196481784319c03e413d8ee6844a060e83407aed204f9344cb0230909f` | `201973a25e75da77ec86468a60094c22ac3d3376d936d5e2d089531d52b5d6d3` |

S108는 predecessor의 row-level prediction, checkpoint 본문 또는
train/dev/stack/calibration data를 다시 열지 않는다. 대신 각 stage의
production publisher가 deep validation 뒤 발행한 다음 A1 chain만
검사한다.

- commit schema/status/canonical binding
- artifact metadata exact count와 content-addressed generation ID
- completed registry hash/status/failure-null/artifact count
- stage별 aggregate report hash/schema/status
- 이전 stage commit hash lineage
- stage별 split/leakage와 no-promotion flag

Aggregate 검증에서 row-level artifact의 상대경로·hash·size metadata는
commit의 일부로 확인하지만 해당 파일을 resolve/open하지 않는다.
S104–S107 public deep validator module은 source hash로 고정하며, S108
dry-run에서 실행하지 않는다. 이를 실행하면 predecessor의 제한
row-level evidence를 다시 읽게 되어 S108 aggregate-only 경계를 깨기
때문이다.

Commit binding과 generation ID는 stage 원본 publisher의 canonical JSON
규칙까지 구분한다. S103은 기본 separator+trailing newline, S104–S106은
compact separator+trailing newline, S107은 compact separator+newline
없음이다. 이 구분은 실제 S103 binding과 각 stage source의 serializer
계약으로 회귀 검사한다.

## 4. 상태기계

Dry-run이 도달할 수 있는 상태는 다음 여섯 개뿐이다.

```text
waiting_s103
  -> engineering_in_progress
  -> blocked_preregistration
  -> blocked_g04
  -> blocked_d07
  -> ready_for_locked_batch
```

`locked_batch_running` 이후의 research/deployment 상태는 config와
validator에서 명시적으로 금지한다.

전체 상태는 다음 우선순위로 정한다.

1. S103이 없거나 invalid이면 `waiting_s103`
2. S104–S107 중 하나라도 미완료면 `engineering_in_progress`
3. predecessor가 모두 valid이고 preregistration이 미완료면
   `blocked_preregistration`
4. G04가 미완료면 `blocked_g04`
5. D07이 미완료면 `blocked_d07`
6. 전부 통과하고 hash가 일치할 때만 `ready_for_locked_batch`

전체 상태가 engineering이어도 세 외부 gate의 현재 상태를 각각
`blocked_preregistration`, `blocked_g04`, `blocked_d07`로 보고한다.
따라서 한 blocker가 다른 blocker를 숨기지 않는다.

## 5. preregistration fail-closed 계약

현재 config의 아래 값은 모두 `TBD_PREREGISTRATION`이다.

- primary ICC outcome
- word PCC/2-of-3 유지 또는 approved protocol amendment
- minimum issued phone count
- minimum issued coverage
- precision CI 방법과 confidence level
- point estimate 또는 CI lower-bound gate
- speaker-cluster uncertainty 단위
- subgroup별 minimum speaker/positive/negative support
- missing subgroup 처리

Preregistration attestation은
`voice_coach.wavlm_s108_preregistration_freeze.v1` schema,
`status=frozen`, 연구책임자 승인, locked 결과 미열람, locked 접근 전
승인을 모두 요구한다.

Word family는 다음 둘 중 정확히 하나여야 한다.

1. 독립 word gold/schema hash와 원래
   `phone macro F1 + word PCC + primary utterance ICC`의 2-of-3
2. 독립 자료를 보지 않고 승인된 amendment hash, word PCC를 제외한 새
   primary metric 목록과 필요한 성공 수

두 번째 경우 `word_pcc`를 새 목록에 남길 수 없다. 첫 번째 경우 독립
word gold가 없으면 통과하지 않는다.

Frozen hash mapping은 다음 9개 exact key를 갖고 실제 aggregate chain과
모두 일치해야 한다.

- S103–S107 commit SHA-256 다섯 개
- S106 `final_fusion_checkpoint` SHA-256
- S107 `calibrator_parameters` SHA-256
- S107 `threshold_policy_report` SHA-256
- 위 여덟 hash를 canonical JSON으로 묶은 `source_chain_sha256`

## 6. G04와 D07 aggregate attestation

### G04

현재 진행 report는 privacy-safe aggregate JSON만 읽는다. Completion
attestation에는 다음이 모두 필요하다.

- schema `voice_coach.g04_alignment_completion_attestation.v1`
- `status=scorer_status=passed`
- expected/completed `240/240`
- correct `>=228`, accuracy `>=0.95`와 count 산술 일치
- privacy-safe report SHA-256
- reviewer 자격 확인과 연구책임자 승인
- identifier/row/audio/label/prediction 포함 여부 전부 false
- `locked_access_count=0`

### D07

Completion attestation에는 다음이 모두 필요하다.

- schema `voice_coach.d07_gold_completion_attestation.v1`
- `status=completed`
- calibration gold 800건
- sealed locked gold 1–1,200건
- 독립 평가자 2인, adjudicator 1인
- 두 원평정 보존과 adjudication 완료
- scalar ICC/QWK `>=0.70` 또는 taxonomy agreement `>=0.65`
- label definition freeze, model 결과 미열람과 연구책임자 승인
- calibration gold와 sealed gold의 SHA-256만 포함
- identifier/row/audio/label/prediction 포함 여부 전부 false
- `locked_access_count=0`

S108은 attestation의 sealed gold hash만 비교한다. 그 hash가 가리키는
locator, row 또는 label을 받거나 resolve하는 field/API는 없다.

## 7. no-locked 접근 경계

Checklist와 validator가 다음 카운터를 정확히 0으로 강제한다.

```text
locked_locator_resolution_count = 0
locked_row_read_count = 0
locked_audio_read_count = 0
locked_label_read_count = 0
locked_prediction_read_count = 0
locked_access_count = 0
```

추가로 다음 카운터도 0이다.

- row-level artifact read
- protected data access
- raw audio access
- environment file access

Aggregate reader는 config에 적힌 predecessor commit/registry/report,
privacy-safe G04 progress와 외부 aggregate attestation만 받는다. 모든
입력은 workspace-relative regular file이어야 하고 symlink chain과
`..` traversal을 거부한다. File descriptor의 device/inode/size/mtime을
읽기 전후 비교하고 publication 직전 모든 aggregate input hash를 다시
확인한다.

## 8. 원자·content-addressed publication

한 generation에는 A1 aggregate
`no_locked_checklist.json` 한 파일만 있다.

```text
artifacts/experiments/aihub71490_wavlm_s108_no_locked_dry_run_v1/
  generations/<generation_id>/no_locked_checklist.json
  receipts/<generation_id>.json
```

Generation ID는 checklist artifact의 name/path/SHA-256/size/format/scope와
schema를 canonical JSON으로 직렬화한 SHA-256이다.

- `.part-<uuid>`에서 완전한 파일을 fsync한다.
- 완전한 디렉터리만 atomic rename으로 공개한다.
- 동일 generation의 동시 writer는 같은 내용으로 수렴한다.
- 다른 내용의 기존 generation/receipt는 덮어쓰지 않는다.
- Receipt는 hard-link create-only publication으로 발행한다.
- generation 공개 뒤 receipt 직전 실패는 orphan generation을 보존하며,
  재실행이 같은 generation을 검증한 뒤 receipt만 복구한다.
- 임시 cleanup은 정확한 generation root의 `.part-*` 한 단계만 허용한다.

Receipt는 `published_no_locked_dry_run_only`이지 S108 canonical commit이
아니다. Completed S108 registry도 생성하지 않는다.

## 9. read-only deep validator

Receipt validator는 다음을 다시 검사한다.

- 현재 config와 source pin 14개
- receipt exact schema와 canonical binding
- generation root/name과 정확히 1개인 파일 layout
- checklist hash·size와 generation ID
- predecessor 순서, 상태와 aggregate-only validation depth
- 세 외부 gate와 전체 상태의 논리적 동치
- blocker 목록
- ready 조건
- S108 completion/score/PENDING/promotion false
- six locked counters와 row/protected/audio/environment access 0

Validator는 predecessor/external data를 다시 열거나 어떤 파일도 쓰지
않는다.

## 10. 실행 명령과 검증 결과

Static contract만 검사:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s108_no_locked_dry_run.py --config-only
```

현재 aggregate 상태를 새 snapshot으로 발행:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s108_no_locked_dry_run.py
```

발행 receipt를 read-only 검증:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s108_no_locked_dry_run.py \
  --validate-receipt \
  artifacts/experiments/aihub71490_wavlm_s108_no_locked_dry_run_v1/receipts/\
73f85af411619e341b19cd78966f39acaf84e4cf9346bb34195cb222af0aaa92.json
```

현재 실제 dry-run 결과:

| 항목 | 값 |
|---|---|
| overall state | `engineering_in_progress` |
| 다음 predecessor | `s104` |
| S103 | `valid` |
| S104 | `incomplete: commit_missing_registry_running` |
| preregistration | `blocked_preregistration: attestation_missing` |
| G04 | `blocked_g04: attestation_missing`, progress 0/240 |
| D07 | `blocked_d07: attestation_missing` |
| ready for locked batch | `false` |
| aggregate file reads | 6 |
| row/protected/raw/environment access | 0/0/0/0 |
| locked locator/row/audio/label/prediction/total | 0/0/0/0/0/0 |
| S108 completion/score/PENDING/promotion | false/false/false/false |

현재 immutable no-locked evidence:

| 항목 | SHA-256 |
|---|---|
| generation ID | `73f85af411619e341b19cd78966f39acaf84e4cf9346bb34195cb222af0aaa92` |
| checklist | `dd34ec5cdb65a3eaf210f806645f5cccd780bf3fd062dc89daf2ff7199d9375f` |
| receipt | `d90cb96fdb699938e9e6e1517412384b3a61efcf3dc4abfe475fec3cc41ffa88` |

Source/hash pin을 동결하기 전 생성된
`fee2eb9c…6927ff`, `cb7d7ad3…713c24`, `ddc264ee…6e3e09` generation과
receipt는 append-only history로 보존했다. 세 snapshot은 당시 source/config
상태의 중간 기록이며 최종 canonical readiness receipt가 아니다. 삭제하거나
현재 validator 통과 증거로 재사용하지 않는다.

검증 결과:

- S108 unit+integration: **29 passed**
- S107→S108 combined regression: **44 passed**
- repository 전체 회귀: **687 passed**
- Ruff format/lint: passed
- targeted mypy: passed
- config-only: source 14/14, protected/locked/output 0
- bounded ready E2E: 모든 predecessor와 세 외부 gate가 valid일 때도
  `ready_for_locked_batch=true`만 만들고 locked launch/completion/score는
  false
- fault injection: temporary write 뒤, generation publish 직전/직후,
  receipt publish 직전 모두 partial receipt 0
- recovery, same-content concurrency, differing-content no-overwrite 통과
- traversal/source symlink/config symlink/input mutation/receipt tamper 거부
- aggregate validation에서 Parquet/WAV/FLAC/prediction file open 0

## 11. 최종 source audit

| 파일 | SHA-256 |
|---|---|
| S108 config | `038947cc9ee77840870cc0df31a6d66e9384f89908a7d91a8706cda0757e65aa` |
| CLI | `467ef6011338ec48ef1485ce118466f5e440b4f7828f57e1bf6767622703b6b6` |
| module | `38002184f5a48924c79eab88ef3ce9cc9b29beebe3474ab2408c8cdeb31b2ba2` |
| unit test | `9849f481fbf6cd23b8a939b73c49d8b233e2206134c427de80140675bb42573f` |
| integration test | `a86cb7d8e99599b053094e2278462e2f367f3f1698965f37f071fd022a24efe6` |

Config는 CLI/module, project manifest, scorer lock과 S103–S107 config/
validator source를 pin한다. Source 하나가 달라지면 predecessor/data/output
접근 전에 config-only가 실패한다.

## 12. C/H/M 위험 감사

구현·합성 범위의 미해결 critical/high/medium(C/H/M) blocker는
**0/0/0**이다.

- Critical: locked locator/row/audio/label/prediction 접근, incomplete
  predecessor/external gate에서 ready, dry-run의 S108 completion/score/
  PENDING/promotion 허용, B05 없는 locked batch, 결과 기반 재선택/재실행
- High: fabricated/mismatched commit binding, registry/report/hash lineage,
  unresolved ICC/word/precision/subgroup preregistration, G04 240/240·228
  미달, D07 수·평가자·합의도 미달, model/calibrator/policy hash mismatch
- Medium: duplicate/non-finite YAML·JSON, traversal/symlink, source/input
  TOCTOU, partial generation/receipt, concurrent writer, overwrite, receipt
  tamper, aggregate가 아닌 file access

각 항목은 static guard, exact schema/hash, 상태 논리 validator 또는 bounded
fault test로 fail-closed 되는 것을 확인했다. 실제 S104–S107과 외부
attestation이 생기면 해당 실제 chain에 대한 C/H/M 감사를 다시 수행해야
한다.

## 13. 실제 다음 순서

1. S104 canonical commit과 completed registry가 생긴 뒤 독립 검증한다.
2. S105, S106, S107을 정본 순서로 실행하고 각 commit/report/registry를
   검증한다.
3. S108 dry-run을 다시 실행해 predecessor chain이 전부 valid인지 확인한다.
4. 결과를 보지 않은 상태에서 primary ICC, word/2-of-3 또는 amendment,
   precision count/coverage/CI/cluster와 subgroup support를 승인·freeze한다.
5. G04 240/240·228 이상과 reviewer 자격 attestation을 완료한다.
6. D07 calibration 800건과 sealed locked gold, 2인+adjudication,
   agreement gate와 research-lead attestation을 완료한다.
7. Dry-run이 `ready_for_locked_batch`를 내더라도 이 구현으로 locked
   data를 열지 않는다.
8. 별도 승인·사전등록된 B05+S108 단일 one-shot batch 구현·감사 뒤에만
   final locked evaluation을 실행한다.
9. Final promotion report가 실제 gate를 판정하기 전에는 S108 배점,
   research candidate 또는 PENDING 해제를 기록하지 않는다.
