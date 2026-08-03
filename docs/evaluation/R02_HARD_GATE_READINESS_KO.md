# WP7 R02 후보 hard gate production 정적 준비도

최초 작성: 2026-07-29 KST  
상태: **정적 preflight·future deep validator 구현 완료 / 실제 R02 0**

## 1. 결론

R02 전용 strict config, gate report JSON Schema, preregistration/ASR/artifact
attestation template, R00→R01→preregistration→attestation 순서를 강제하는
read-only contract/CLI, future R02 completion deep validator와 bounded fault
test를 구현했다.

현재 R01 completion이 `TBD_R01_COMPLETION`이므로 canonical full preflight는
R00의 public/aggregate evidence를 재검증한 뒤 R01 commit path를 열지 않고
종료한다. D1 candidate/audio, model/checkpoint, GPU, metric, S1/S2,
gold/locked data와 output은 전부 0이다.

이 package는 R02 metric runner가 아니다. 실제 report, pass/fail,
eligible/quarantine, registry, commit과 R02 완료 점수는 0이다.

## 2. R02 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/r02_candidate_hard_gate_v1.yaml` | exact predecessor, TBD gate, blindness/leakage, 실패보존/R03 계약 |
| `configs/reference/r02_hard_gate_report_v1.schema.json` | source-blind D1 full gate report schema |
| `configs/reference/r02_gate_preregistration_v1.template.json` | metric/threshold/normalization/segmentation/support/aggregate 정책 양식 |
| `configs/reference/r02_asr_attestation_v1.template.json` | Whisper-family exact artifact/license/preprocessing 양식 |
| `configs/reference/r02_artifact_attestation_v1.template.json` | UTMOS/pause-rhythm/target-phone exact artifact/license 양식 |
| `src/voice_coach/references/r02_hard_gate_contract.py` | strict parser, hash/symlink/TOCTOU/no-overwrite, predecessor·report·completion deep validator |
| `scripts/prepare_r02_candidate_hard_gate.py` | config-only/full read-only CLI |
| `tests/unit/test_r02_hard_gate_contract.py` | policy/report/partition/hash/order/symlink/TOCTOU bounded fault tests |
| `tests/integration/test_prepare_r02_candidate_hard_gate.py` | 실제 workspace zero-runtime와 R00-first/R01-stop 검증 |
| `docs/evaluation/R02_HARD_GATE_DATA_MODEL_CARD_KO.md` | data/model/license/privacy/blindness 경계 |
| `docs/evaluation/R02_HARD_GATE_READINESS_KO.md` | 본 준비 증거 |

R00/R01/S104–S203 구현 파일과 정본 원고/evidence-map은 수정하지 않았다.
Tracker는 상위 orchestration 요청에 따라 준비도 사실만 별도 반영한다.

## 3. 실행 순서

```text
R02 C1 config/source/schema/template
  -> R00 validator 재실행 + immutable report exact match
  -> exact R01 completion commit
  -> R01 registry/manifest/human/CosyVoice attestation deep validation
  -> 모든 source/predecessor snapshot TOCTOU 재검사
  -> locked R02 preregistration
  -> ASR + artifact/pause/phone artifact/license attestation
  -> 그 뒤에만 D1 candidate/audio root resolve
  -> source-blind execution manifest + sealed lineage sidecar
  -> 별도 production metric executor
```

R01 commit SHA가 TBD이면 경로 존재 여부조차 확인하지 않는다. Preregistration
SHA가 TBD이면 ASR/artifact path를 보지 않는다. Attestation까지 통과해도
본 정적 package는 audio/model을 읽거나 metric을 계산하지 않고 별도 실행
package를 요구한다.

## 4. R01 future deep validation

공개 함수:

- `deep_validate_r01_completion(...) -> R01Evidence`
- `deep_validate_r02_completion(...) -> R02CompletedEvidence`
- `validate_gate_report_payload(...)`

R01 validator는 future completion commit, completed registry, candidate
manifest, human/CosyVoice signed attestation을 SHA와 내용 양쪽에서 검증한다.
Manifest 160–320행의 문장별 8–16개, source balance, UUID, text/audio/source
binding, speaker/speed/pitch 다양성과 금지 field를 기존 R01 production
validator로 전수 재계산한다.

R02 completion validator는 다음을 독립적으로 다시 연다.

- completed commit/registry
- preregistration/ASR/artifact attestation
- blinded execution manifest
- sealed source-lineage sidecar
- full gate report
- eligible/quarantine partition

Eligible와 quarantine의 순서·합집합, 문장별 산술, 네 gate decision,
minimum support와 aggregate transcript-error를 R01 manifest부터 다시
계산한다. R03는 `R02CompletedEvidence.eligible_candidate_ids`만 사용할 수
있고 quarantine은 별도 snapshot으로 보존된다.

## 5. Exact gate fail-closed

계획이 family/역할만 지지하고 exact 값은 제공하지 않으므로 canonical
config와 template는 다음을 고정한다.

- metric·threshold·normalization·segmentation·minimum support:
  `TBD_PREREGISTRATION`
- exact model/checkpoint/implementation/license:
  `TBD_ATTESTATION`
- external approval와 research-use authorization: false

네 gate와 candidate-flow 값은 전부 함께 고정해야 한다. 일부 값만 채운
config, prerequisite SHA 일부만 확정한 config, source별 threshold나
normalization은 static validation에서 거부한다.

2% aggregate transcript-error는 계획에 있는 완료 경계로만 보존했다.
Candidate threshold로 재사용할 수 없고 exact aggregate metric ID/정의/
aggregation은 preregistration 전 TBD다.

## 6. Blindness·leakage·failure

- Gate input/report/partition에는 `source_kind`가 없다.
- Source label은 별도 D1 sidecar에 봉인하고 immutable gate commit 전
  unblind할 수 없다.
- Human/TTS는 동일 metric/threshold/normalization/support를 쓴다.
- S1/S2 prediction·score·checkpoint·split, learner gold, locked test는
  모두 금지된다.
- R00 target phone/rule는 coverage target이지 observed realization/gold가
  아니다.
- 한 gate라도 실패하면 candidate는 quarantine이다.
- 탈락 audio/lineage는 D1에 보존하고 delete/move/overwrite하지 않는다.
- R03는 exact eligible partition만 읽는다.
- R02 pass도 publication을 허용하지 않는다.

## 7. Canonical 실행 결과

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_r02_candidate_hard_gate.py --config-only
```

결과: `valid_config_only`.

- canonical source pin read: 16/16
- R00 deep validation: 0
- R01 commit/registry/manifest/attestation: 0
- R02 preregistration/ASR/artifact attestation: 0
- D1 manifest/audio path resolution·audio read: 0
- model path/metadata/weight/download: 0
- GPU/metric/S1/S2/gold/locked/source-label gate input: 0
- output path/write/publication: 0

### Full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_r02_candidate_hard_gate.py
```

결과: 의도된 exit 1,
`stage=r01_completion_commit`,
`error=R01 completion commit is TBD_R01_COMPLETION`.

- source pin read: 16
- R00 validator load/deep validation: 1/1
- R00 public·aggregate evidence read: 33
- R00 target-rule aggregate row read: 20
- R00 raw audio/row-level read: 0/0
- R01 commit path read attempt: 0
- R01 registry/manifest/human/CosyVoice attestation: 0
- R02 preregistration/ASR/artifact attestation: 0
- D1 manifest/audio resolution/read: 0
- model/download/GPU/metric/S1/S2/gold/locked: 0
- output path/write/publication: 0
- 선언 output 8개: 생성 0

R00 target-rule read 20은 public R00 sentence card의 target phone/rule ID를
coverage audit 대상으로 읽은 것이며 protected row나 gold realization
접근이 아니다.

## 8. Test와 정적 품질

- R02 unit+integration targeted: **22 passed**
- R00→R01→R02 관련 회귀: **59 passed**
- Ruff check/format: passed
- mypy contract/CLI: passed

Fault 범위:

- duplicate YAML/JSON, NaN/nonfinite
- source hash drift·pin order·path traversal
- config/source symlink와 snapshot TOCTOU
- output/source overlap, existing output와 symlink-parent no-overwrite
- R01/prerequisite 일부 SHA만 확정하는 partial promotion
- template의 fake approval·research authorization
- source label/plaintext/path/S1/S2/gold field 누수
- source별 threshold drift와 false pass
- insufficient metric/per-sentence support
- aggregate 2% boundary 실패
- eligible/quarantine partition 바꿔치기
- quarantine을 R03 input으로 승격
- completed commit/registry/report 산술·hash 불일치

## 9. C/H/M 위험 감사

검증 시점 파일 SHA-256:

| 파일 | SHA-256 |
|---|---|
| config | `e43c3fd5b3f54b5d9d11e29a5bb2b6d77a9ec91776516581bca3601f44a8282f` |
| gate report schema | `60525a3b685574a3dce193a7423d77a811050c9c38d36b2c3ba947b71f06a92b` |
| preregistration template | `07b53aa937d0490116c4b8aeedc5d6aab1754479990404ccb318dd2dd98528c0` |
| ASR attestation template | `f1c0ea10e08baf425b36d1fd3ad936b93557af5224f043df374d19648a2df53f` |
| artifact attestation template | `940f36e3c1c0f82c1eaa408256bf9461961f7bb354c8f81e833dce8a7a2191cc` |
| contract | `a50e6287ffdb56c9ec2f22859a9be800cc3fb787b7c3390f4330052412eaf5bf` |
| CLI | `c40ab4b0f829d0dae1d93fa5bf55e12e7e61933fffae8aa31c7aa904ee8e3be5` |
| unit test | `22c83a3feadb0f576a97408db292abac7268f41060d444ff810e6b262ff981d8` |
| integration test | `4caa1f7f75d6e276b8ee9774c1b029e1641f9b02b4e4261cc4e769143f9bb5a1` |
| data/model/license card | `7cb9692472c32e89029a455d28f3ed65f2854860264932a06198eec7f203a61f` |

Readiness 문서 자체 hash는 자기참조를 피하기 위해 상위 tracker/handoff에서
기록한다.

정적·합성 package 범위 open implementation finding:

- Critical: 0
- High: 0
- Medium: 0

감사 범위는 predecessor 우회, D1 조기 접근, 임의 metric/threshold,
model/checkpoint/license 추론, human/TTS 차등 gate, source unblinding,
S1/S2·gold·locked leakage, 탈락 artifact 손실, R03 quarantine 유입,
symlink/hash/order/TOCTOU/no-overwrite와 허위 완료 주장이다.

실제 모델·audio·metric 결과가 없으므로 empirical 성능·license 승인·
external attestation risk는 해결됐다고 주장하지 않는다.

## 10. 남은 일

1. R01 external approval와 private candidate/audio 완료
2. R01 completed bundle 독립 deep validation
3. R02 exact metric/threshold/support와 aggregate 정의 사전등록
4. ASR/artifact/pause/phone exact artifact/license attestation
5. 별도 source-blind metric runner·atomic publisher 구현
6. 실제 R02 실행과 completed bundle 독립 validation
7. Eligible partition만 R03 Pareto/diversity selection에 전달

그 전까지 actual R02와 tracker 배점은 0이다.
