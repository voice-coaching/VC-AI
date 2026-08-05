# WP7 R01 인간·CosyVoice 후보 생성 production 정적 준비도 (개인 연구)

최초 작성: 2026-07-29 KST  
상태: **정적 preflight 구현 완료 / 실제 human·TTS 후보·audio·GPU 실행 0**

## 1. 결론

R01 전용 strict config, candidate manifest JSON Schema, human/CosyVoice
external attestation template, read-only fail-closed contract/CLI, 데이터·모델·
라이선스 카드와 bounded fault test를 구현했다.

Runtime gate는 다음 순서를 강제한다.

```text
R01 C1 config/source/schema/template
  -> R00 public/aggregate deep validation + immutable report exact match
  -> AI Hub access/license와 데이터 사용 scope
  -> human recording data-rights receipt
  -> CosyVoice code/model/weight-license attestation
  -> offline runtime lock + local bundle + exact GPU pin
  -> 별도 production recording/inference package
```

현재 AI Hub/data-rights attestation SHA가 `TBD_EXTERNAL_ATTESTATION`이므로 canonical full
preflight는 R00 deep validation 직후 종료된다. 해당 attestation path 자체,
CosyVoice attestation, protected data/audio, model/environment/GPU와 output
path는 읽거나 해석하지 않는다.

Source ledger의 CosyVoice repo/model revision은 **관측 사실**로만 보존했다.
Standalone weight LICENSE가 관측되지 않았고 실제 사용 승인·runtime/GPU
증빙도 없으므로 code LICENSE나 model-card metadata에서 권한을 추론하지
않는다. GRPO는 R01 범위가 아니며 false로 고정했다.

이 package는 후보 생성기가 아니다. Audio, candidate ID, manifest row,
metric, R02 결과, publication과 R01 completion artifact는 모두 0이다.

## 2. R01 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/r01_human_cosyvoice_candidates_v1.yaml` | R00→human→CosyVoice→env/GPU 순서와 8–16개 후보 정책 |
| `configs/reference/r01_candidate_manifest_v1.schema.json` | D1 private candidate row/audio binding schema |
| `configs/reference/r01_human_recording_attestation_v1.template.json` | 데이터 사용권·녹음 권리·protocol aggregate 양식(기관 승인 비적용) |
| `configs/reference/r01_cosyvoice_model_attestation_v1.template.json` | code/model/weight license·runtime·GPU 서명 양식 |
| `src/voice_coach/references/r01_candidate_contract.py` | strict YAML/JSON, R00 deep validation, attestation·schema·privacy 검증, symlink/hash/TOCTOU/no-overwrite guard |
| `scripts/prepare_r01_human_cosyvoice_candidates.py` | config-only/full read-only CLI |
| `tests/unit/test_r01_candidate_contract.py` | policy·schema·TBD·hash·order·symlink·TOCTOU·no-overwrite fault test |
| `tests/integration/test_prepare_r01_human_cosyvoice_candidates.py` | 실제 workspace의 zero-runtime config-only와 R00-first full failure |
| `docs/constraints/R01_CANDIDATE_DATA_MODEL_LICENSE_CARD_KO.md` | human/CosyVoice/data/audio/license/privacy/publication 경계 |
| `docs/constraints/R01_CANDIDATE_GENERATION_READINESS_KO.md` | 본 실행 준비 증거 |

기존 tracker, 정본 원고/evidence-map, S104–S203과 R00 파일은 수정하지
않았다. Recorder, downloader, TTS inference, GPU query, candidate builder,
publisher와 R02 scorer도 만들지 않았다.

## 3. R00 deep-validation

Config pin만 맞으면 R00을 통과시키지 않는다. Full preflight는 고정 R00
validator를 동적으로 실행해 public sentence card/schema/CC0 notice와
public·aggregate evidence에서 validation report를 다시 계산한다.

다음을 모두 요구한다.

- 재계산 report와 immutable report의 canonical JSON exact 일치
- `status=valid`, 문장 20개
- `r00_completion_claim_allowed=true`
- `r01_completion_claim_allowed=false`
- R00 TTS/audio 생성과 human evaluation 모두 false
- R00 raw audio/row-level read 0
- R00 validator와 report의 source pin/hash/identity 불변

R00을 통과한 다음 canonical source와 report snapshot을 다시 비교해
source-to-attestation 사이 TOCTOU를 차단한다. 그 뒤에만 external
attestation 여부를 본다.

## 4. AI Hub 및 데이터 권리 attestation gate

Canonical template는 승인 문서가 아니며 speaker receipt를 만들지 않는다.
다음 값은 전부 외부 서명된 aggregate attestation에서 와야 한다.

- AI Hub 접근 승인·라이선스 receipt와 개인 연구 데이터 사용 scope
- consent protocol/form hash와 목적·철회·보존·삭제 정책
- 정확한 recording protocol, 장비, 거리, sample format와 QC hash
- 최소 2명 각각의 namespaced speaker ID, consent receipt hash,
  recording-license grant hash, 원어민 attestation, 활성·비철회·비만료
  상태와 R00 문장 20개 허용 범위

개별 receipt schema는 직접 식별 field의 추가를 거부한다. TTS 학습,
voice cloning, public release, commercial use는 모두 false다.

현재 config의 AI Hub/data-rights attestation SHA가 TBD이므로 attestation path read
attempt도 0이다. 파일 존재 여부를 probing하지 않는다.

## 5. CosyVoice artifact/license/runtime/GPU gate

Ledger observation:

- repo `QwenAudio/CosyVoice`
- code revision `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`
- code Apache-2.0 LICENSE SHA `c71d239d…d0ab4`
- candidate model `FunAudioLLM/CosyVoice2-0.5B`
- model revision `eec1ae6c79877dbd9379285cf8789c9e0879293d`
- card metadata `apache-2.0`
- standalone weight LICENSE 미관측
- GRPO 공개 recipe 존재만 확인, 본 프로젝트 효과 미검증

이 observation에서 authorization을 추론할 수 없도록
`authorization_inferred_from_ledger=false`다. Signed attestation은 exact
repo/model/revision 외에도 code/weight 각각의 license evidence SHA,
research inference 허용 판단, weight manifest, offline environment lock,
inference entrypoint, local weight bundle, machine/GPU UUID/model/ordinal/
memory pin을 모두 가져야 한다.

External attestation SHA와 config의 exact artifact/runtime/GPU binding은
동시에 TBD에서 확정값으로 바뀌어야 한다. 하나만 채우면 static
validation에서 거부한다. 승인 후에도 network download, GRPO,
fine-tuning, voice cloning과 concurrent GPU training은 false다.

현재 canonical config는 이 전부가 TBD라 model/environment/GPU path
resolution과 GPU query가 0이다. 실행 중인 S104 GPU를 침범하지 않았다.

## 6. Candidate·privacy·publication 계약

### 후보 수와 균형

- 문장 `R00-01`–`R00-20`
- 문장당 8–16개, 계획 12개
- 각 문장에서 human = CosyVoice
- source당 4–8개
- 각 문장·source에서 distinct speaker/voice 최소 2개
- `slow/neutral/fast`, `low/neutral/high` cell 보존
- R02 전 사후 cell 재표기·병합 금지

### ID와 manifest

Candidate ID는 `r01c_` 뒤 RFC 4122 UUIDv4 32자리다. 문장, source, speaker,
순서가 ID에서 드러나면 거부한다. Candidate/audio object ID와 audio hash는
전역 중복을 허용하지 않는다.

Future manifest는 160–320개 row, 문장별 8–16개와 source 균형을 전수
재계산한다. 선언된 aggregate count를 신뢰하지 않는다. Prompt 평문,
transcript, raw speaker identity, 원천 path, 자유서술, R02 score와 MOS/CMOS
field는 schema와 deep validator 모두에서 거부한다.

### D1·동의

인간 raw capture는 R0, identity mapping은 R1이다. 유효한 consent/license를
확인하고 metadata·path를 제거한 가명 candidate row/audio만 D1으로 옮긴다.
Human audio는 D1에서도 개인정보이며 공개 자료가 아니다. CosyVoice candidate
audio와 manifest도 R02 전 D1이다.

### R02 hard gate

Manifest와 모든 row는 `r02_hard_gate_status=not_run`,
`publication_allowed=false`다. ASR content match, artifact, pause,
target-phone hard gate를 통과하기 전 candidate publication은 금지된다.
R01 자체로 reference bank, 품질 우위, blind MOS/CMOS 또는 학습 효과를
주장할 수 없다.

## 7. Canonical 실행 결과

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_r01_human_cosyvoice_candidates.py --config-only
```

결과: `valid_config_only`.

- source pin read: 13
- R00 deep validation: 0
- human/CosyVoice attestation read attempt: 0/0
- recording/candidate data, audio read/write: 모두 0
- model path/metadata/weight/download: 모두 0
- runtime environment path/read, GPU access: 모두 0
- candidate ID/manifest read/write: 모두 0
- metric/R02/publication/output path/output: 모두 0

### Full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_r01_human_cosyvoice_candidates.py
```

결과: 의도된 exit 1,
`stage=human_attestation`,
`error=human attestation is TBD_EXTERNAL_ATTESTATION`.

- source pin read: 13
- R00 validator load/deep validation: 1/1
- R00 public·aggregate evidence access count: 33
  - public card/schema/notice와 aggregate source 13건
  - privacy-safe reserved prompt hash 값 20건
- R00 raw audio/row-level read: 0/0
- human attestation read attempt/success: 0/0
- CosyVoice attestation read attempt/success: 0/0
- data/audio/model/env/GPU/candidate/metric/R02/publication/output: 모두 0
- stdout: empty
- 선언 output 3개: 생성 0

R00의 `prompt_hash_values_read=20`은 prompt 원문이나 row 접근이 아니다.
R00 정본이 사용하는 privacy-safe exact-hash collision 검사다.

## 8. Test와 정적 품질

- R01 unit fault test: **21 passed**
- R01 integration test: **3 passed**
- R01 targeted 합계: **24 passed**
- R00 regression 포함: **37 passed**
- Ruff check: passed
- Ruff format check: passed
- mypy contract/CLI: passed

Fault 범위:

- duplicate YAML/JSON과 NaN
- source hash drift·source pin order
- config/source symlink와 path traversal 경계
- snapshot 변경 TOCTOU
- existing output·symlink parent no-overwrite
- source/output overlap
- human/CosyVoice TBD template의 승인 오용
- CosyVoice external SHA와 exact binding의 부분 확정
- UUIDv4/opaque-ID 경계
- D1 schema에 plaintext/path field 삽입
- 빈/fake candidate manifest와 publication claim
- R00-before-human 실행 순서와 모든 실패의 output 0

## 9. 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `7a7167574b083af8d90653199411ee2ecdf3937bd75a89c53d63224923cb6cb8` |
| candidate manifest schema | `7ffe1b152276b12621575680d022d9b87cd9c19e7fe36374a3186cf7449e1470` |
| human attestation template | `aeeebf426b86427f3f2a1f4fd1adc1f4382aa3ace6d88474feed3d8081ecd096` |
| CosyVoice attestation template | `2ce20ac3fd17714ff487c47f45b227b490e75fa8bebe6e7596bb4ce8328abc2e` |
| contract | `e70bd5ed2fcf678537ef8ec41ceb2b76bfba3037cd40a2fc4b617dee31619a95` |
| CLI | `e0daf9c1b83db51a0501f5e3e7663ba8890a58a904dc5178a41e1f4b98d9bac5` |
| references package marker | `d9a0d9f2f7aa7dc10c3d62db7a214382ca73b1dda8421d21d2801dc78c910d4e` |
| unit test | `d589c83b71939a2ec9fde9d5228d4e6cb0b4de11337b2e9bf39de9b69d6cf8e3` |
| integration test | `69de05e6be504c15903abbc177e43aa05121c1f5473a3f1ecd8e2a23a5741e07` |
| data/model/license card | `f2d25a6dafe24416e8b745ba42af76ff6a3801363e7fd69536a51d55dfe36d40` |

Readiness 문서 자체 hash는 자기참조를 피하기 위해 상위 tracker/handoff에서
기록한다.

## 10. C/H/M 위험 감사

현재 정적·합성 package 범위의 open implementation finding:

- Critical: 0
- High: 0
- Medium: 0

검사 범위는 predecessor 우회, 권리 없는 human/model 사용, code/weight
license 혼합, GRPO 범위 확대, data/model/output 조기 resolution,
동시 GPU 침범, 개인식별·path·prompt 누수, source 불균형, 다양성 collapse,
opaque ID 위반, R02 전 publication, symlink/hash/TOCTOU/no-overwrite와
허위 완료 주장이다.

다음은 구현 결함을 숨긴 것이 아니라 의도적으로 실행을 막는 외부 blocker다.

1. AI Hub 접근·라이선스와 원어민 데이터 사용권·녹음 권리 receipt 없음
2. 고정된 recording protocol 없음
3. CosyVoice weight license와 실제 R01 사용 판단 없음
4. offline runtime/local weight bundle/GPU pin 없음
5. 별도 production recorder/inference package 없음
6. 실제 candidate/audio/private manifest/R02 report 없음

실제 외부 증빙과 production package가 생기면 별도 C/H/M 감사를 다시
실행해야 한다.

## 11. 다음 실행

1. AI Hub 접근·라이선스와 data-rights/recording protocol을 고정하고
   최소 2명 speaker receipt를 aggregate attestation으로 서명한다.
2. CosyVoice code와 weight 권리를 분리 검토해 artifact authorization을
   서명한다.
3. S104 GPU가 끝난 뒤 별도 TTS environment와 local weight bundle을 만들고
   exact GPU pin을 승인한다.
4. Config의 external SHA와 exact binding을 함께 새 version으로 동결한다.
5. Full preflight가 두 attestation을 통과한 뒤 별도 recorder/inference
   package를 구현·독립 감사한다.
6. 문장당 8–16개 balanced private 후보를 원자 publish하고 manifest를
   전수 검증한다.
7. R02 hard gate 뒤에도 candidate publication은 별도 승격 판단을 거친다.

그 전에는 실제 녹음, download, TTS/GPU 실행과 후보 생성이 금지된다.
