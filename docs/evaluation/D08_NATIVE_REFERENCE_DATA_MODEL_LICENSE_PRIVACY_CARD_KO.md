# D08 원어민 다화자 기준음 데이터·라이선스·개인정보 카드 (개인 연구)

최초 작성: 2026-07-29 KST
상태: **production 정적 준비 계약만 구현 / 실제 모집·동의·녹음·QC 0**

## 1. 결론

D08은 R00에서 실제 완료된 한국어 고정 문장 20개를 동의된 성인 한국어
원어민이 녹음하는 개인 연구 외부 수집 단계다. 이번 패키지는 녹음기가 아니다.
기관/IRB 승인은 선행 gate가 아니며, 개인 연구 scope attestation,
개별 동의·녹음 권리·라이선스·사전등록·R0/R1/D1/A1 경계를 강제하는 config,
strict schema, template, read-only preflight와 합성 validator만 구현했다.

현재 레거시 v1 실행기의 H00 approval SHA가 TBD_EXTERNAL_ATTESTATION이다.
H00는 현재 개인 연구에 적용하지 않는 구형 참여자 연구 경로다. 개인 연구
scope와 AI Hub license/access로 executor를 전환하는 별도 contract amendment
전에는 정본 full preflight가 R00 완료본을 심층 재검증한 다음 레거시 H00
경로에서 중단한다.
실제 화자 모집, 개별 동의, 녹음 권리 부여, 녹음, WAV,
metadata, QC, completion과 commit은 모두 0이다.

## 2. 선행 증거와 실행 순서

실행 순서는 다음처럼 고정한다.

```text
D08 C1 config/source/schema/template
  -> R00 actual completed sentence card deep validation
  -> AI Hub access/license와 개인 연구 데이터 사용 scope
  -> 활성 개별 동의 attestation
  -> 녹음 권리·source·license attestation
  -> QC·화자 완료 하한·adjudication 사전등록
  -> 별도 모집·녹음·QC executor
```

R00은 project-original CC0-1.0 문장 20개만 완료했다. R00 완료에는 원어민
녹음, 사람 발음 검수, TTS, MOS/CMOS 또는 reference bank가 포함되지
않는다. D08 preflight는 고정 R00 validator를 다시 실행해 다음을
확인한다.

- immutable report와 재계산 report의 canonical JSON exact 일치
- `status=valid`, 문장 20개
- `r00_completion_claim_allowed=true`
- `r01_completion_claim_allowed=false`
- R00 audio/TTS 생성과 인간평가 false
- raw audio와 row-level read 0

## 3. 고정 계약과 사전등록 대기값

문서가 실제 규칙으로 명시한 값과 계획 목표 또는 측정 정의가 없는 값을
구분했다.

| 항목 | 현재 계약 | 근거 지위 |
|---|---|---|
| 문장 | `R00-01`–`R00-20`, 정확히 20개 | R00 실제 완료 |
| 화자 | 문장별 성인 한국어 원어민 4명 | **계획 목표**, 완료 하한 아님 |
| 완료 화자 하한 | `TBD_PREREGISTRATION` | 승인된 사전등록 전 실행 금지 |
| take | 화자×문장마다 자연속도 2개, 명료 교육속도 1개 | 고정 protocol |
| format | 48 kHz, 24-bit, mono PCM WAV | 고정 hard gate |
| 환경 | 동일 장비·마이크·공간 profile | 고정 비교 규칙, exact profile은 외부 attestation |
| 거리 | 15–20 cm metadata | 고정 범위 |
| clipping | ratio `<0.001` (`<0.1%`) | 고정 hard gate |
| decoder | 오류 수 0 | 고정 hard gate |
| ASR | 내용 일치 후보 선별만, 최종 QC 금지 | 고정 역할 |
| 사람 청취 | reviewer pool 2명 중 최소 1명 내용·발음 확인 | 고정 최소 확인 |
| DC offset | metric·단위·방향·threshold TBD | 사전등록 필요 |
| 내용 일치 | metric·방향·threshold TBD | 사전등록 필요 |
| 잡음·잔향·중단 | metric·단위·방향·threshold TBD | 사전등록 필요 |
| adjudication | trigger·decision·독립성 규칙 TBD | 사전등록 필요 |

“문장별 최소 4명을 목표”라는 문장을 완료 hard floor로 바꾸지 않았다.
향후 사전등록이 binding floor를 정하고, A1 completion은 binding floor와
4명 계획 목표 달성 여부를 서로 다른 필드로 보고한다. 사전등록 floor가
4보다 낮고 실제 문장별 화자가 4보다 적으면 completion과 별개로
`planning_target_met=false`를 숨기지 않는다.

## 4. 데이터 권리와 사용 범위

### 현재 사용하지 않는 H00 참여자 연구 경로

현재 개인 연구에서는 기관·법적 승인을 요구하지 않는다. 참여자 연구를
선택할 때만 기관 승인 attestation은 승인/결정 ID, revision, 승인 문서 hash와
유효기간을 가져야 한다. 성인 원어민 모집, 음성 녹음, 자동 QC, 사람
청취 QC와 연구 기준음 사용 범위가 명시돼야 한다. 접근권한, 보존,
폐기, 철회와 사고 대응 계획 및 R0/R1/D1/A1 경계도 승인 범위에
포함한다.

Canonical template는 승인서가 아니다. `status`, 서명, 문서 hash가 모두
TBD이고 `d08_collection_allowed=false`다.

### 개별 동의

각 화자는 namespaced lowercase SHA-256 가명 ID로 다음 receipt에
연결된다.

- 활성 개별 동의 receipt SHA-256
- 성인·한국어 원어민 attestation
- 철회되지 않고 만료되지 않은 상태와 유효기간
- R00 문장 20개 허용 범위
- 음성 녹음, 연구 기준음, 자동 QC, 사람 청취 QC 범위

모델 입력 여부, 제3자 제공 여부, 공개와 상업 이용은 하나의 포괄 동의로
합치지 않고 별도 결정으로 저장한다. Template는 이 결정들을
`TBD_EXTERNAL_ATTESTATION`으로 두며 권리를 추정하지 않는다. 공개와
상업 이용은 현재 false다.

### 녹음 권리와 source/license 정직성

각 동의 receipt에는 별도의 recording-license grant SHA-256이 대응해야
한다. 권리는 활성·비철회·비만료이고 20문장 연구 reference와 QC 범위를
명시해야 한다.

허용 source label은
`newly_recorded_consented_adult_native_korean_human`이다. 공개 corpus나
TTS 음성을 사람 녹음으로 위장하는 것은 금지한다. 각 reference의
human/TTS/source/license는 명시하며 checksum, 공개 repository 이름 또는
모델 카드 metadata만으로 사용권·공개권을 추론하지 않는다.

## 5. 데이터 구역과 strict schema

| 자료 | 구역 | 허용 내용 | Git/공개 |
|---|---|---|---|
| 원본 PCM WAV | R0 | 생체·개인정보인 원음 | 금지 |
| locator·신원 대응·세션 metadata | R1 | 제한 상대 locator, 장비/공간/take, receipt binding | 금지 |
| pseudonymized row-level QC·embedding | D1 | 가명 recording ID, 측정값·pass code | 개인 scope attestation·license 전 금지 |
| completion 집계 | A1 | 문장별·전체 count와 gate 상태만 | 별도 반출 검토 후 가능 |
| schema/config/code/docs | C1 | 값 없는 계약과 상대 예시 경로 | 가능 |

### R1 recording metadata

`d08_recording_metadata_v1.schema.json`은 추가 필드를 금지하고 각 row에
다음을 요구한다.

- opaque recording ID와 namespaced speaker ID
- R00 sentence ID와 text SHA binding
- natural 1/2 또는 clear-educational 1의 exact take
- session/equipment/microphone/room profile SHA
- R0 locator token과 audio SHA
- 48 kHz/24-bit/mono PCM WAV와 15–20 cm
- consent receipt·recording-right grant SHA
- `source_kind=human_recording`, R0/R1 zone, publication false

Deep validator는 모든 문장 coverage, 사전등록 speaker floor,
화자×문장별 정확한 세 take, 동일 장비·마이크·공간, 고유 recording/audio/
locator와 receipt lineage를 전수 재계산한다. 이름·연락처·생년월일·raw
speaker ID와 자유서술은 금지한다.

### D1 QC report

`d08_qc_report_v1.schema.json`은 metadata recording마다 정확히 한 row를
요구한다. Clipping과 decoder gate는 고정값으로, DC/content/noise/reverb/
interruption은 승인된 preregistration의 exact metric·threshold로 다시
계산한다. ASR은 triage-only여야 하며 최종 내용·발음 확인에는 사람 청취
receipt가 필요하다. Adjudication이 발동하면 preregistered rule과 receipt를
요구한다. R0 locator, 직접 식별자, 자유서술과 publication은 금지한다.

### A1 completion

`d08_completion_v1.schema.json`은 row, speaker/recording ID, audio/locator
hash, consent/license receipt와 자유서술을 금지한다. 문장별 집계는
speaker count, 자연/교육속도 accepted take count, binding floor와 4명
계획 목표 달성 여부, exact take pattern과 QC pass만 가진다. 모든 selected
take가 QC를 통과하고 승인·동의·권리·사전등록·lineage가 exact 일치할
때만 미래 `completion_claim_allowed=true`가 가능하다. A1 completion도
자동 공개 허가는 아니므로 `publication_allowed=false`다.

## 6. 현재 접근·생성 결과

Canonical config-only:

- C1/public·aggregate source pin 19개 read
- R00 deep validation 0
- AI Hub/데이터권리/사전등록 path·read 0
- 모집·동의수집·R0/R1/D1·오디오·마이크·모델·네트워크·GPU·QC·출력 0

Canonical full:

- R00 validator load/deep validation 1/1
- R00 public·aggregate evidence access 33
- R00 raw audio/row-level read 0/0
- 정적 source TOCTOU 재검증 19
- 레거시 H00 SHA가 TBD라 H00 path resolution/read attempt/read 0/0/0
- 이후 동의·권리·사전등록·runtime·output 모두 0

따라서 현재 실제 speaker, consent receipt, recording-right grant, microphone
profile, session, take, WAV, R1 metadata, D1 QC, A1 completion, publication과
commit은 모두 0이다.

## 7. 주장 경계

현재 허용되는 주장은 “D08 외부 수집을 위한 정적 fail-closed 계약이
구현·합성 검증됐다”뿐이다. 다음 주장은 금지한다.

- D08 모집·동의·녹음·QC 또는 reference 완료
- 문장별 4명 실제 달성
- 원어민 발음·교육 적합성 또는 음질 검증
- human reference bank 완성·공개
- TTS 대비 자연스러움·품질 우위
- MOS/CMOS, R04 blind 평가 또는 학습 효과

## 8. 다음 실행

1. AI Hub 접근 승인·라이선스와 개인 연구 데이터 사용 scope를 확정한다.
2. QC metric·speaker floor·adjudication을 audio 접근 전에 사전등록한다.
3. 성인 원어민을 모집하고 분리된 범위의 개별 동의 receipt를 수집한다.
4. 녹음 권리와 exact 장비·마이크·공간 profile을 서명한다.
5. 새 config version에서 external SHA와 exact profile binding을 함께
   고정하고 full preflight를 다시 통과시킨다.
6. 별도 감사된 recorder/QC executor로 R0 원음, R1 metadata, D1 QC를
   원자 publish하고 실패 산출물을 격리한다.
7. strict completion validator와 독립 감사 뒤에만 D08 상태를 갱신한다.

그 전에는 실제 모집, 녹음, audio/microphone 접근과 완료 주장을 하지
않는다.
