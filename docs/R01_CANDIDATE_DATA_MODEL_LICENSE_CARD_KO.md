# WP7 R01 인간·CosyVoice 후보 데이터·모델·라이선스 카드

최초 작성: 2026-07-29 KST  
상태: **production 정적 준비 계약만 구현 / 실제 후보·음성·모델 실행 0**

## 1. 목적과 현재 결론

R01은 R00에서 고정한 한국어 20문장마다 인간 원어민 녹음과 CosyVoice
inference 후보를 합쳐 8–16개를 만드는 내부 연구 단계다. 이 카드는
후보를 실제로 만들지 않고, 후보 생성 전에 반드시 존재해야 하는
AI Hub 접근·라이선스, 데이터 권리, 모델·환경·GPU·개인정보 계약을 고정한다.

현재 다음 외부 증빙이 없다.

- AI Hub 접근 승인·라이선스와 원어민 데이터 권리 receipt
- 녹음물 연구 사용권과 정확한 녹음 protocol
- CosyVoice code와 weight를 실제 R01에 사용할 수 있다는 artifact별 판단
- standalone weight license 부재를 다룬 서명된 license evidence
- 재현 가능한 runtime lock, local weight bundle manifest와 GPU pin

따라서 현재 full preflight는 R00을 심층 재검증한 직후
`TBD_EXTERNAL_ATTESTATION`에서 종료한다. AI Hub/data-rights attestation 경로,
녹음·후보 data path, audio, model, environment, GPU와 output path는
열거나 해석하지 않는다. 이 상태는 R01 완료나 후보 생성 가능 판정이
아니다.

## 2. R00 선행자

R01의 유일한 텍스트 입력은 project-original CC0-1.0 R00 sentence card다.
Full preflight는 다음을 단순 hash 확인에 그치지 않고 다시 검증한다.

1. 고정 R00 validator의 SHA-256을 확인한다.
2. public sentence card·schema·CC0 notice와 public/aggregate evidence만
   읽어 R00 report를 다시 계산한다.
3. 재계산 report가 immutable validation report와 canonical JSON으로
   정확히 같은지 확인한다.
4. `status=valid`, 문장 20개, `r00_completion_claim_allowed=true`,
   `r01_completion_claim_allowed=false`, TTS/audio·인간평가 0을 확인한다.
5. raw audio와 row-level read가 0인지 확인한다.

R00 문장 텍스트 SHA는
`08dc149ccf10006a587e6216dda0471c19b5c55eab6b320b4348587527d82c4d`다.
문장 교체나 순서 변경은 R00과 R01의 새 version을 모두 요구한다.

## 3. 후보 설계 계약

| 항목 | 고정 정책 |
|---|---|
| 문장 | `R00-01`–`R00-20`, 정확히 20개 |
| 문장당 후보 | 최소 8, 계획 12, 최대 16 |
| source balance | 각 문장 안에서 human과 CosyVoice 수가 정확히 같고 source당 4–8개 |
| 화자 다양성 | 각 문장·source마다 가명 화자/voice 최소 2개 |
| 속도 | `slow`, `neutral`, `fast` cell을 보존 |
| pitch | `low`, `neutral`, `high` cell을 보존 |
| candidate ID | `r01c_` + RFC 4122 UUIDv4 32자리; 문장/source/화자/순서를 encode하지 않음 |
| 후처리 | R02 전에 design cell의 사후 재표기·병합 금지 |

여기서 human의 `speaker_id_sha256`은 동의 receipt에 연결된 가명 ID고,
CosyVoice의 같은 필드는 승인된 voice/speaker condition의 가명 ID다.
속도·pitch bucket은 사전 정의된 생성/녹음 condition을 보존하는
metadata다. R02/R03 결과를 보고 유리하게 다시 붙일 수 없다.

현재 row는 0개이며 UUID, speaker ID, audio object ID도 생성하지 않았다.
Schema의 160–320 row 범위는 미래 private manifest의 전수 계약이지
채워진 데이터가 아니다.

## 4. 인간 녹음 attestation

템플릿
`configs/reference/r01_human_recording_attestation_v1.template.json`은
증빙 양식일 뿐 승인 문서가 아니다. 상태와 모든 외부 값이
`TBD_EXTERNAL_ATTESTATION`이고 `speaker_receipts=[]`이므로 그대로는
validator를 통과할 수 없다.

서명된 aggregate attestation은 최소한 다음을 정확히 고정해야 한다.

### AI Hub·데이터 권리

- AI Hub 접근·라이선스 receipt와 개인 연구 데이터 사용 scope
- 동의 protocol과 동의서 revision·SHA-256
- 허용 목적: R01 후보 생성, 자동 hard gate, blind 연구평가
- 철회 절차, 보존기간, 폐기 protocol
- TTS 학습, voice cloning, public release, commercial use는 모두 false

### 개별 화자 receipt

최소 2명의 서로 다른 namespaced SHA-256 화자 ID와 다음 receipt를
저장한다.

- 개별 동의 receipt SHA-256
- 녹음물 연구 사용권 grant SHA-256
- 원어민 한국어 화자 attestation
- 동의 활성·비철회·비만료 상태와 유효기간
- 허용된 R00 문장 ID 20개

이 aggregate에는 이름, 연락처, 생년월일 같은 직접 식별자를 넣지 않는다.
신원 대응표는 별도 R1 구역에 둔다.

### 녹음 protocol

정확한 protocol/revision/SHA, R00 card binding, 방음·공간 조건, microphone,
audio interface, 입–microphone 거리, sample rate, bit depth, channel,
container, operator training revision과 QC protocol SHA를 고정한다.
현재 이 값은 하나도 승인되지 않았으므로 임의 장비·format으로 녹음을
시작할 수 없다.

## 5. CosyVoice 모델·라이선스 경계

정본 source ledger에서 관측한 사실은 다음 범위뿐이다.

| 항목 | ledger 관측 |
|---|---|
| canonical code repo | `https://github.com/QwenAudio/CosyVoice` |
| code revision | `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc` |
| code LICENSE | Apache-2.0, SHA-256 `c71d239d…d0ab4` |
| model candidate | `FunAudioLLM/CosyVoice2-0.5B` |
| model revision | `eec1ae6c79877dbd9379285cf8789c9e0879293d` |
| model card metadata | `apache-2.0` |
| standalone weight LICENSE | 관측하지 못함 |
| GRPO recipe | 공개 존재만 확인 |

이 표는 사용 허가가 아니다. Code LICENSE를 weight 권리로 확대하지 않고,
model-card metadata를 standalone LICENSE 파일과 합치지 않는다. 공개 GRPO
recipe의 존재는 R01에서 GRPO를 실행하거나 효과를 주장할 근거도 아니다.
R01은 inference-only이며 GRPO, fine-tuning, voice cloning은 모두 금지한다.

서명된 CosyVoice attestation에는 다음이 동시에 있어야 한다.

- exact repo URL/revision과 code license evidence SHA
- exact provider/model/revision과 weight manifest SHA
- weight license SPDX, 독립 license evidence SHA와 연구 inference 허용 판단
- redistribution/commercial-use 판단을 별도 boolean으로 기록
- environment lock SHA, container digest, Python/PyTorch/CUDA version
- inference entrypoint SHA와 local weight bundle manifest SHA
- network download false, local-files-only true
- machine ID, GPU UUID/model/visible ordinal/minimum memory
- 다른 학습 process와 동시 GPU 사용 금지

공식 이름이나 revision만 맞고 위 권리·실행 증빙 중 하나라도 없으면
`TBD_EXTERNAL_ATTESTATION`으로 남는다.

## 6. 개인정보와 D1 경계

| 단계·자료 | 구역 | 정책 |
|---|---|---|
| 인간 원시 capture | R0 | 직접 식별 가능 음성, 승인 접근자만 처리 |
| 신원–가명 대응표 | R1 | candidate 자료와 분리 |
| 동의 확인·metadata 제거·가명 object key 적용 후 candidate audio | D1 | 여전히 개인정보인 제한 연구 음성, 공개 금지 |
| candidate manifest row | D1 | SHA-256 ID와 조건 metadata만 허용 |
| CosyVoice candidate audio | D1 | R02 전 내부 제한 artifact |
| 집계 결과 | A1 후보 | R02 이후 별도 license·privacy·승격 검토 |

D1은 익명·공개 구역이 아니다. 인간 음성은 가명화 후에도 생체·개인정보로
취급한다. 철회 receipt가 들어오면 연결된 row/audio를 quarantine하고 승인된
삭제 protocol을 실행해야 한다.

Candidate manifest에는 다음을 넣을 수 없다.

- 이름, 연락처, 주소, 생년월일과 raw speaker ID
- 원천/audio/recording path
- prompt 평문과 transcript
- 자유서술 note
- 아직 실행하지 않은 R02 score, MOS, CMOS

허용 join key는 namespaced lowercase SHA-256뿐이다. Checksum은 권리나 공개
허가가 아니다.

## 7. Candidate manifest와 R02 publication gate

Machine schema:
`configs/reference/r01_candidate_manifest_v1.schema.json`

각 row는 opaque candidate ID, R00 sentence ID와 text SHA, source kind,
source attestation SHA, 가명 speaker/audio object ID, audio SHA,
speed/pitch/preservation group과 `r02_hard_gate_status=not_run`만 가진다.
Manifest와 모든 row는 다음 상태로 시작한다.

- privacy zone: `D1_pseudonymized_restricted_experiment`
- publication: false
- R02 hard gate: not run
- status: `r01_private_candidates_pending_r02`

R02의 ASR 내용 일치, artifact, 휴지, 목표 음소 hard gate 전에는 후보
하나도 공개할 수 없다. R01 완료는 reference bank, 품질 우위, MOS/CMOS,
학습 효과 또는 production 공개를 뜻하지 않는다. R02를 통과해도 R03
선택과 R04 blind 인간평가는 별도 단계다.

## 8. 현재 접근·생성 결과

Canonical config-only는 C1/public·aggregate source pin 13개만 읽는다.
R00, external attestation, data/audio/model/environment/GPU/output 접근은
모두 0이다.

Canonical full preflight는 R00을 심층 검증한 뒤 human attestation SHA가
TBD임을 확인하고 종료한다.

- R00 deep validation: 1
- R00 raw audio/row-level read: 0/0
- human attestation path read attempt/success: 0/0
- CosyVoice attestation path read attempt/success: 0/0
- recording/candidate data path resolution: 0/0
- audio read/write: 0/0
- model path/metadata/weight/download: 0/0/0/0
- runtime environment path/read, GPU access: 0/0/0
- candidate ID/manifest read/write: 0/0/0
- metric, R02, publication, output resolution/write: 모두 0

따라서 실제 R01 candidate manifest, human/TTS audio, model bundle, metric,
result, completion artifact는 모두 0이다.

## 9. 완료에 필요한 후속 산출물

1. AI Hub 접근·라이선스와 데이터 권리를 확인한 human aggregate attestation
2. 유효한 개별 화자 consent/license receipt와 고정 recording protocol
3. CosyVoice artifact별 license·runtime 권리를 확인한 attestation
4. 격리된 TTS environment lock, local weight manifest와 GPU pin
5. 별도 production recorder/inference package의 symlink/hash/TOCTOU,
   no-overwrite, atomic publish와 failure cleanup 감사
6. 문장당 8–16개·source 균형·speaker/speed/pitch 보존을 통과한 private
   candidate manifest
7. R02 hard-gate report와 탈락 candidate quarantine
8. 독립 validation receipt 뒤에만 R01 완료 상태 갱신

현재 package는 1–4가 없을 때 5–8을 실행하지 못하도록 만드는 정적
준비 계약이다.
