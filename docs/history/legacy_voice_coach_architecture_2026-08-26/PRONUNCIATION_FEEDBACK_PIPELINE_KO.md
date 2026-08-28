# 한국어 발음 피드백 폐쇄루프 Pipeline Architecture

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

- 문서 상태: `partially_implemented` (§13에 단계별 실제 상태를 기록한다)
- 구현 상태: Stage A0--B와 gate/Stage H 정책은 `src/voice_coach/pronunciation_feedback/`에
  구현되어 있고, Stage C--E의 실제 모델 backend는 미연결이다. 이 문서는 미연결
  단계를 완료로 승격하지 않는다.
- 목적: 시스템이 제시한 연습 프롬프트와 그 프롬프트를 낭독한 `user_audio`를 받아
  **한국어 L2 학습자**의 발음을 진단하고 근거 기반 코칭을 반환하는 실제 모델
  경로를 현재 저장소의 Oracle/TTS prototype과 분리해 설계한다.
- 대상 코호트: 중국어·일본어 L1 한국어 L2 학습자. 학습·calibration 근거
  (AI Hub 71490)와 동일 코호트이므로 코호트 정합은 성립한다(§11).
  아나운서·모어 화자 규범 진단은 이 설계의 범위가 아니다.
- 범위: 연구용 로컬/내부 실행. 인증, restricted 데이터 반출, 외부 모델 다운로드,
  사용자 음성 저장은 별도 승인·계약 없이는 수행하지 않는다.
- **전달 형태: AI pipeline 전용.** 이 프로젝트는 백엔드 서비스나 프론트엔드를
  산출물로 하지 않는다. 산출물은 in-process Python pipeline과 CLI entrypoint이며,
  HTTP endpoint·세션 서버·UI는 범위 밖이다. §4의 HTTP 계약은 외부 통합이 필요할
  때를 위한 **참고 mapping**이고 구현 대상이 아니다.

## 1. 목표와 현재 판정

### 1.1 목표 사용자 흐름

```text
프롬프트 문장 제시
      |
사용자 낭독 음성 파일 업로드
      |
오디오 검증·임시 decode
      |
프롬프트 expected phones + 음성 content/alignment
      |
S1 하이브리드 발음 채점 ─────┐
                              ├─ 독립성·confidence gate
S2 독립 내용/오류 감사 ───────┘
      |
DiagnosticProfile + evidence + confidence
      |
coach / rerecord / uncertain / abstain
```

이 흐름에서 TTS reference generator는 선택적 보조 경로다. 사용자의 음성을
진단하는 S1/S2 경로와 TTS가 기준 음성을 생성하는 Loop A를 같은 모델 출력으로
간주하지 않는다.

### 1.2 현재 저장소와의 차이

| 영역 | 현재 관찰 | 목표 pipeline에서의 위치 |
|---|---|---|
| `/v1/oracle-attempts` | 이미 계산된 `contentMatch`, `aspects`, `goldIssues`를 받는 계약 검증 API | 테스트용 입력 fixture; 실제 자동 진단 경로가 아님 |
| `/v1/closed-loop/oracle-attempts` | Oracle profile을 바탕으로 rerecord/coach 상태 전이 | 목표 state policy의 contract/reference |
| `AdversarialTTSEngine` | tensor waveform와 token을 받아 draft/refined TTS trajectory 생성 | Loop A generator adapter; 발음 feedback service가 아님 |
| GOP/F0/eGeMAPS/WavLM 계열 모듈 | 개별 feature/model/metric 경계가 존재 | S1 adapter 구현 후보 |
| S1/S2 metric bundle | aggregate 계산과 독립 backend ID 계약 | 실제 backend 실행 결과를 주입하는 service port |
| native reference bank | 명시적 speaker metadata를 요구하며 현재 manifest에서 fail-closed | 기준 음성 reference adapter |
| `voice_coach.pronunciation_feedback` | prompt catalog·audio QC·gate·feedback 구성이 port 주입 구조로 구현됨 | 이 문서의 실행 경로. S1/S2 backend만 미연결 |
| R00 sentence card | 검증된 20문장(CC0) asset이 존재 | Stage A0의 기본 prompt 출처 |

따라서 이 문서는 현재 기능을 완료로 재분류하지 않는다. 실제 구현 여부는
각 단계의 `implemented`, `contract_only`, `target`, `blocked` 상태를 별도로
기록해야 한다.

### 1.3 목적 대비 구조 판정

목적 문장은 세 개의 요구로 분해된다.

1. 연습용 프롬프트를 **모델과 사용자 양쪽에** 제공한다.
2. 사용자가 그 프롬프트를 낭독한 음성 파일을 모델 입력으로 받는다.
3. 모델이 그 발음에 대한 피드백을 반환한다.

계층 구조·gate·fail-closed 정책은 (2)(3)을 수용한다. 아래 항목은 목적을
충족하려면 이 설계에 **추가로 고정해야 하는 경계**이며, 각 항목의 대응
설계는 표에 표시한 절에 기록한다.

| # | 목적 대비 결손 | 현재 문서 상태 | 보완 위치 |
|---|---|---|---|
| G1 | 프롬프트 제공 주체가 없음. Stage A는 client가 보낸 `promptText`를 받는 구조여서 "시스템이 프롬프트를 제시한다"는 요구를 만족하지 않음 | 미기록 | §3 Stage A0, §4.0 |
| G2 | 프롬프트 코퍼스 출처 미지정. 실제로는 검증된 20문장 카드(`data/reference/r00/r00_sentence_card_v1.json`, CC0, 난이도/음소 coverage 고정)가 존재하지만 pipeline에 연결되지 않음 | 미참조 | §3 Stage A0 |
| G3 | 피드백 **표현** 계층 부재. Stage F는 결정(`coach` 등)과 advice ID까지만 정의하고, 사용자가 읽는 문장 위 위치 표시·조언 문구·기준 음성 비교로 변환하는 단계가 없음 | 미기록 | §3 Stage H |
| G4 | 문자↔음소↔시간 index 매핑 계약 부재. `PromptSpec`/`AlignmentObservation`이 음절·어절 offset을 보존하지 않으면 "어디가 틀렸는지"를 사용자 문장 위에 표시할 수 없음 | 미기록 | §2.3, §3 Stage H |
| G5 | 재녹음 루프의 상태 소유자 미지정. session 저장소를 도입하면 AI-only 범위와 `architecture.md`의 "DB 보류" 결정과 충돌 | 충돌 | §5 (무상태 pipeline + 호출자 이력) |
| G6 | 실행 시간 예산 부재. S1 9개 branch + S2(Whisper-large-v3/HuBERT-large)를 attempt마다 실행하는 비용과 모델 상주 주체가 정의되지 않음 | 미기록 | §6 |
| G7 | Stage C의 ASR/CTC backend가 S1·S2 어느 쪽 소속인지 미지정. 그대로 두면 content ASR과 S2 auditor가 같은 backend가 되어 §Stage E의 독립성 요건이 무성립 | 모호 | §3 Stage C, Stage E |
| G8 | threshold 출처 미지정. "frozen threshold"의 calibration split·dataset·profile 근거를 담는 artifact가 없음 | 미기록 | §3 Stage C, §8.3 |
| G9 | (해소) 대상 코호트 불일치. 2026-08-06 목적 재정의로 대상 코호트를 학습·calibration 근거와 동일한 L2 학습자로 맞춤 | 해소 | §11 |
| G10 | (범위 밖) 아나운서 규범(표준 발음법 적용형, 장단음, 낭독 억양·발성) domain 부재 | 범위 밖 | §11 |
| G11 | 사람 gate가 실행 경로에 섞여 있어 자율 실행이 불가. threshold 사전등록·전문가 감사·locked test가 engineering 실행과 같은 경로에 있었음 | 미기록 | §14 |

G1--G8은 이 설계 안에서 해결 가능한 구조 결손이다. G9는 목적 재정의로
해소됐고 G10은 범위에서 제외했다. G11은 §14의 이중 트랙으로 해소한다.

## 2. 계층 아키텍처

```text
Entrypoint: CLI (scripts/run_pronunciation_feedback.py) 또는 연구 코드 호출
      |
      v
Application: PronunciationFeedbackPipeline (prompt -> audio -> feedback)
      |
      v
Domain: PracticePrompt, PromptSpec, SyllableSlot, AudioObservation,
       AlignmentObservation, S1ScoreBundle, S2AuditResult,
       DiagnosticIssueDraft, FeedbackDecision, LearnerFeedback, AttemptResult
      |
      v
Ports / interfaces
      |-- PromptCatalogPort
      |-- ExpectedPhonePort
      |-- AudioIntakePort
      |-- ContentAlignmentPort
      |-- S1ScorerPort
      |-- S2AuditorPort
      |-- ThresholdSetPort
      |-- AdviceCatalogPort
      |-- GroundedAdviceGeneratorPort (선택적 local LLM claim selector)
      |-- ReferenceAudioPort (현재 미구현/blocked)
      |
      v
Adapters / infrastructure
      |-- R00 sentence-card catalog reader
      |-- g2pK expected-phone frontend (별도 venv, 미설치 시 unavailable)
      |-- soundfile decoder + QC
      |-- WavLM/HiPAMA + explicit acoustic scorer (미연결)
      |-- HuBERT/Whisper independent auditor (미연결)
      |-- frozen threshold/calibration artifact reader
      |-- coaching advice allowlist reader
      |-- native/announcer reference bank (blocked)
```

`PromptCatalogPort`는 프롬프트 제공 주체를 호출자에서 pipeline으로 옮기기 위해
추가된 경계다. 모델 weight 상주는 별도 port가 아니라 각 scorer adapter가
자기 수명 안에서 책임진다(§6). 서버 세션 저장소·임시 오디오 저장소·HTTP
error mapping은 AI-only 범위에서 제외한다.

의존성 방향은 `entrypoint -> application -> domain/ports -> adapters`로 고정한다.
Domain은 파일 경로, GPU, 환경변수, subprocess, 외부 SDK를 import하지 않는다.

### 2.1 Entrypoint 계층

CLI 또는 연구 코드가 진입점이며 다음만 담당한다.

- 인자 파싱(`--prompt-id`, `--audio`, `--profile`)과 경로 검증
- pipeline 구성(어떤 adapter를 주입할지 선택)
- 결과 dataclass를 JSON으로 직렬화

금지:

- `soundfile.read`, WavLM, Whisper, GOP, coaching catalog 직접 호출
- raw audio, 내부 경로, speaker hash, 상세 annotation을 출력에 포함
- 실패한 gate를 entrypoint에서 재시도하거나 0점으로 보정

### 2.2 Application 계층

`PronunciationFeedbackPipeline`이 다음 연산을 조합한다.

- `list_prompts(difficulty, targets, limit)`
- `prepare_prompt(prompt_id)`
- `run_attempt(prompt_id, audio_path, profile)`

pipeline은 backend 구현을 직접 알지 않고 port를 주입받는다. 한 번의 호출은
단일 attempt를 끝까지 처리하고, 시작만 한 단계를 완료로 표현하지 않는다.
재녹음은 동일 prompt로 `run_attempt`를 다시 호출하는 것이며, attempt 이력은
호출자가 append-only로 보관한다(서버 세션 저장소는 이 프로젝트 범위 밖).

### 2.3 Domain 계층

핵심 도메인 객체는 다음 역할을 분리한다.

| 객체 | 책임 | 포함하지 않는 것 |
|---|---|---|
| `PracticePrompt` | catalog가 사용자에게 제시하는 문장: `prompt_id`, 표시 텍스트, 난이도, 연습 목표(target phone/prosody), 라이선스, card revision | 모델 점수, 사용자 음성, 정답 발음 판정 |
| `PromptSpec` | 제시된 prompt의 정규화 식별자, expected phone contract, prompt version, **문자 offset↔음절 index↔expected phone index 매핑** | raw audio, 모델 예측 |
| `AudioObservation` | decode/QC 결과, duration, sample rate, audio hash | 공개 파일 경로, raw bytes |
| `AlignmentObservation` | target/observed sequence, frame/phone confidence, content match, **phone index↔time range 매핑** | gold label로 가장한 prediction |
| `S1ScoreBundle` | 학습형·명시적 branch 점수, masks, backend revision | S2 판정 |
| `S2AuditResult` | 독립 auditor의 WER/CER·오류·confidence·independence receipt | S1 내부 logits |
| `DiagnosticProfile` | 이슈, 위치, severity, confidence, evidence | raw audio/전사 원문/내부 경로 |
| `FeedbackDecision` | coach/rerecord/uncertain/abstain과 이유 | 임의 생성된 조언 |
| `LearnerFeedback` | 사용자에게 노출되는 최종 형태: 문장 위 표시 span, advice ID/문구, aspect 요약, 재녹음 지시, 표시 불가 항목의 명시적 부재 | 근거 없는 span, catalog 밖 문구, 내부 점수 원본 |
| `AttemptResult` | attempt terminal 상태, failure code, receipts | 진행 중 상태, 서버 session 식별자 |

`PromptSpec`의 offset 매핑과 `AlignmentObservation`의 time 매핑은 G4의 대응
설계다. 두 매핑이 없으면 `DiagnosticProfile`의 issue를 "문장의 어느 음절"로
환원할 수 없고, 사용자에게는 위치 없는 총점만 남는다. 매핑은 prompt
normalization 단계에서 1회 생성해 `prompt_version`에 고정하고, alignment
adapter가 반환한 phone index와만 join한다. join 실패는 span 없는 issue로
degrade시키며 임의 위치를 추정하지 않는다.

점수 누락은 `0.0`으로 대체하지 않는다. `missing`, `low_confidence`,
`out_of_distribution`, `backend_unavailable`을 별도 상태로 보존한다.

## 3. 실제 온라인 pipeline

### Stage A0 — 연습 프롬프트 제공

목적의 "프롬프트가 모델과 사용자에게 제공된다"를 담당하는 단계이며, 이 설계에
추가된 경계다(G1, G2). 프롬프트는 client가 자유 입력하는 값이 아니라 서버가
소유하는 고정 카탈로그에서 선택해 **사용자 표시용 텍스트와 모델용 expected
contract를 같은 `prompt_id`에서 파생**시킨다.

기본 카탈로그 출처:

- `data/reference/r00/r00_sentence_card_v1.json`
  (`status=fixed_validated_sentence_text_only`, 20문장, CC0-1.0,
  난이도 quota 7/7/6, onset·vowel·coda·음운 규칙 coverage 고정,
  PII·금지 표현 검사 통과, AI Hub 예약 prompt hash 비충돌 확인)
- 검증 진입점: `scripts/validate_r00_sentence_card.py`

처리:

1. card schema/`sha256`/`card_id` 검증 후 read-only 로드
2. 난이도·연습 목표·이전 attempt 이력으로 후보 필터
3. 사용자 표시 payload(`prompt_id`, 텍스트, 난이도, 연습 목표)와
   모델 payload(expected phone contract, offset 매핑)를 동일 revision으로 발급
4. 선택 결과를 `PromptSpec`에 고정해 attempt가 다른 문장으로 채점되지 않게 함

출력:

- `PracticePrompt(status=ready|blocked)`와 `prompt_catalog_revision`

실패:

- card hash/schema 불일치: `prompt_catalog_unavailable`(`failed_closed`)
- 요청 난이도·목표에 해당 문장 없음: `prompt_not_available`

자유 입력 prompt 정책:

- 기본값은 **closed catalog only**다.
- 자유 텍스트를 허용하는 경우 `analysisProfile=uncalibrated`로만 처리하고,
  threshold·기준 음성·예약 문장 보장이 없음을 응답에 명시하며 자동 코칭을
  발행하지 않는다(`uncertain` 이하로 상한).
- 자유 텍스트를 calibrated 경로로 승격하려면 §8.3 threshold artifact와
  prompt policy 검사가 먼저 통과해야 한다.

현재 상태: card asset과 catalog adapter 모두 `implemented`(§13).

### Stage A — prompt 준비

입력:

- Stage A0가 발급한 `prompt_id`와 `prompt_catalog_revision`
- optional prompt/version 또는 고정 sentence ID
- 평가 profile(`catalog_calibrated` 또는 `uncalibrated`)
- 자유 입력을 허용한 profile에서만 `prompt_text`

처리:

1. UTF-8/NFC 정규화와 prompt 길이·허용 문자 검증
2. prompt hash 및 version 생성
3. G2P/expected-phone adapter로 target phone sequence 생성
4. expected phone과 observed realized phone을 서로 다른 역할로 저장
5. prompt가 허용 cohort·예약 문장·target profile에 속하는지 확인
6. 문자 offset↔음절 index↔expected phone index 매핑 생성 후 `prompt_version`에 고정
7. phone inventory 일치는 `voice_coach.phonology.expected_phone_agreement`
   계약으로 검증하고, 불일치는 보정하지 않고 fail-closed한다

출력:

- `PromptSpec(status=ready|invalid|blocked)`
- target phone sequence의 내부 hash와 phone count
- offset↔phone 매핑 table과 그 hash

실패:

- prompt가 비어 있거나 정규화 불가: `prompt_invalid`
- expected-phone frontend revision/phone inventory 불일치: `failed_closed`
- prompt policy 미확인: `blocked`

### Stage B — 오디오 수신·QC

입력:

- 한 개의 audio file
- 허용 format/sample rate/channel/duration 정책

`AudioDecoderAdapter`가 다음을 수행한다.

1. MIME뿐 아니라 실제 file signature 확인
2. restricted temporary location에서 decode
3. mono 변환 및 pinned sample rate resample
4. finite PCM, clipping, silence, duration, channel, decode error 검사
5. `audio_sha256`, decoder revision, QC receipt 생성
6. 분석 종료 후 raw temporary file 삭제 또는 TTL 정책에 따른 폐기

출력:

- `AudioObservation(status=qc_passed|qc_failed|decode_failed)`
- model runtime에 전달할 in-memory waveform

실패한 오디오는 S1/S2에 전달하지 않는다. codec 오류와 사용자 재녹음이
필요한 품질 오류를 서로 다른 error code로 반환한다.

### Stage C — content와 alignment

이 단계는 사용자가 prompt를 읽었는지와 음소 위치를 분리해 판정한다.

1. CTC/ASR adapter가 음성의 observed text/phone hypothesis를 생성한다.
2. target prompt와 hypothesis의 WER/CER 또는 phone content score를 계산한다.
3. target expected phones와 frame/phone alignment를 별도 adapter에서 생성한다.
4. alignment confidence가 낮으면 위치별 점수를 `missing`으로 표시한다.
5. frozen threshold를 적용한다. threshold는 코드 상수가 아니라 §8.3의
   `ThresholdSet` artifact에서 로드하고, 사용한 artifact ID를
   `evidenceReceipt`에 남긴다.
6. alignment 결과의 phone index를 Stage A의 offset 매핑에 join해 phone↔time↔
   음절 대응을 확정한다.

backend 소속 규칙(G7):

- 이 단계의 ASR/CTC/alignment backend는 **S1 소속**으로 선언한다.
- Stage E의 S2 auditor는 이 단계에서 사용한 `backend_id`·alignment 경로를
  재사용할 수 없다. 동일 ID가 관측되면 independence receipt를 발급하지 않고
  자동 코칭을 중단한다.
- 따라서 content 판정은 S1 경로에서 1회, S2 경로에서 독립적으로 1회
  계산되며, 두 결과의 불일치는 Stage F의 `disagreement` 입력이 된다.

`content_mismatch=true`이면 발음 오류를 확정하지 않고 `rerecord_required`로
종료한다. prompt와 다른 문장을 읽은 결과를 발음 오류 label로 학습하거나
코칭하지 않는다.

### Stage D — S1 하이브리드 채점

S1은 한 개의 총점이 아니라 다음 축을 보존한다.

| Branch | 입력/출력 | 역할 |
|---|---|---|
| learned | WavLM-large/HiPAMA 계열 representation, phone·word·utterance head | 학습형 발음·aspect score |
| phone-CTC | frame/class log probability | 내용·음소 일치 |
| GOP | target posterior/margin/entropy/top-1 | 음소 오류 후보 |
| MFA+DTW | phone/duration/pause sequence | 말속도·쉼·위치 |
| F0 | torchcrepe/Parselmouth contour | 억양·성조 |
| eGeMAPS | energy/loudness functionals | 강세·음량 |
| nPVI/temporal | phone or syllable duration | rhythm |
| UTMOSv2 | explicit naturalness score | artifact/naturalness |
| WavLM-SV | speaker/style similarity | reference similarity |

각 branch는 `backend_id`, `model_revision`, `config_hash`, confidence와
missing mask를 반환한다. S1 fusion은 confidence-weighted aggregation을
사용하되, 관측되지 않은 branch를 0점으로 간주하지 않는다.

S1 결과의 최소 형태:

```text
S1ScoreBundle(
  content, segmental, duration, pause, intonation, energy,
  rhythm, naturalness, speaker_similarity,
  phone_evidence[], confidence, missing_metrics[], backend_receipts[]
)
```

현재 저장소의 `closed_loop_metrics.py`는 이 결과를 집계하는 경계이며,
실제 WavLM/CTC/MFA/UTMOS 추론을 자동 실행하는 service는 아직 아니다.

### Stage E — S2 독립 감사

S2는 S1과 다음을 공유하지 않아야 한다.

- backbone: S1 WavLM 계열과 다른 HuBERT-large 또는 Whisper-large-v3
- alignment: S1의 MFA/CTC segmentation과 다른 alignment-free CTC/DTW 경로
- training/evaluation labels와 holdout split
- S1 내부 logits, fused score, feedback text

S2는 최소한 다음을 반환한다.

- hypothesis content WER/CER
- independent issue/confidence
- `approved`, `disagreement`, `uncertain`, `not_run`
- S1/S2 independence receipt

S1·S2 backend ID가 같거나 independence receipt가 없으면 자동 코칭을
발행하지 않는다. 현재 저장소의 S2 함수는 transcript와 backend ID를 받는
집계/독립성 계약이며 실제 Whisper/HuBERT 추론 결과가 아니다.

### Stage F — gate와 coaching decision

```text
content mismatch ----------------------> rerecord_required
S1/S2 unavailable ----------------------> uncertain
S1/S2 disagreement ---------------------> uncertain 또는 expert_review
low confidence/OOD ---------------------> rerecord_required
supported issue + evidence + confidence -> coaching_ready
no supported issue ----------------------> completed_no_issue
```

`CoachingDecisionService`는 다음을 모두 통과해야 `coach`를 반환한다.

- target content match 통과
- 필요한 S1/S2 metric 존재
- confidence threshold 통과
- issue evidence에 위치·유형·근거가 존재
- advice catalog allowlist에 issue type 존재
- raw prediction이 아니라 검증된 domain issue로 변환됨

그 외에는 `rerecord`, `uncertain`, `expert_review`, `abstain` 중 하나를
반환한다. 조언을 생성형 LLM에 위임하는 경우에도 먼저 이 결정과 allowlist를
통과시키며, LLM은 근거 없는 오류를 새로 만들 수 없다.

### Stage G — response와 재녹음

response에는 다음만 포함한다.

- `attempt_id`, `status`, `next_action`
- prompt version/target identifier
- content match summary
- aspect scores와 confidence
- issue type, time range, severity, evidence IDs
- coaching advice ID/message 또는 rerecord 이유
- model/config/backend revision

포함하지 않는다.

- raw audio bytes 또는 restricted path
- speaker identifier
- 원본 전사/상세 annotation
- 내부 filesystem 경로와 secret
- 실패한 branch의 가짜 점수

재녹음은 같은 prompt로 `run_attempt`를 다시 호출하는 것이다. pipeline은 이전
attempt를 보관하지 않으며, 호출자가 attempt별 audio hash와 gate receipt를
append-only로 비교한다(§5).

### Stage H — 학습자 피드백 구성

G3의 대응 설계다. Stage F까지는 결정과 근거이고, 목적의 "피드백을 제공한다"는
그 결정을 사용자가 읽을 수 있는 형태로 변환하는 이 단계에서 완성된다.
`FeedbackCompositionService`는 새로운 판정을 만들지 않고 변환만 수행한다.

입력: `DiagnosticProfile`, `FeedbackDecision`, `PromptSpec` 매핑,
`AlignmentObservation` time 매핑, advice catalog.

처리:

1. `decision != coach`이면 조언 문구를 만들지 않고 사유·다음 행동만 구성한다.
2. issue를 문장 위 표시 span으로 변환한다. span은 `(음절 index range,
   문자 offset range, time range)` 3-tuple을 모두 가질 때만 발행하고,
   하나라도 없으면 문장 전체 수준 요약으로 degrade한다.
3. issue type을 advice catalog에서 조회한다. catalog에 없으면 문구를 생성하지
   않고 `expert_review`로 되돌린다.
4. aspect 요약은 관측된 축만 노출하고 `missing` 축은 "측정되지 않음"으로
   구분해 표시한다. 미관측을 낮은 점수로 표현하지 않는다.
5. 기준 음성 비교는 `ReferenceAudioPort`가 승인된 reference를 반환할 때만
   추가한다. 현재 native/아나운서 reference bank는 `blocked`이므로 기본
   응답에 비교 항목이 없다(§11).

advice catalog 현황:

- `voice_coach.coaching.catalog.ADVICE_CATALOG`: 4개 문구, legacy MVP 계약
- `configs/coaching/korean_error_advice_ontology_v1.json`: 17-tag draft,
  `target_phone_mapping.empirical_status=pending`, 전문가 검토 미완료

즉 issue 탐지가 성공해도 발행 가능한 조언의 범위는 현재 4개 문구로 상한이
잡혀 있다. 이 상한을 넘기는 문구는 C03 전문가 감사 통과 전까지 발행하지 않는다.

선택적 HyperCLOVA X 경로는 입력을 이미 통과한 issue·span·severity·confidence와
승인된 claim으로 제한한다. 모델은 자유 문장을 쓰지 않고 claim ID·표현 style·
candidate disclosure만 선택하며, 최종 문장은 검증된 plan과 catalog 원문으로
결정론적으로 렌더링한다. invalid/timeout/abstain은 catalog 원문으로 폴백한다.
상세 계약과 실행법은
[`HYPERCLOVAX_GROUNDED_FEEDBACK_KO.md`](HYPERCLOVAX_GROUNDED_FEEDBACK_KO.md)를
따른다.

출력: `LearnerFeedback` 또는 명시적 부재 사유.

## 4. Pipeline 호출 계약과 HTTP 참고 mapping

이 프로젝트의 구현 대상은 아래 **in-process 호출 계약**이다.

```python
pipeline = build_default_pipeline(profile="uncalibrated")
prompts = pipeline.list_prompts(difficulty="beginner")
result = pipeline.run_attempt(prompt_id="R00-01", audio_path=path)
result.status          # coaching_ready | completed_no_issue | rerecord_required
                       # | uncertain | failed_closed
result.failure_code    # 실패 사유 (성공 시 None)
result.learner_feedback  # Stage H 결과 또는 None
result.receipts        # 단계별 backend/revision/threshold 수령증
```

CLI: `python scripts/run_pronunciation_feedback.py prompts`,
`python scripts/run_pronunciation_feedback.py attempt --prompt-id R00-01 --audio a.wav`.

아래 §4.0--4.3의 HTTP endpoint는 **구현 대상이 아니라** 외부 시스템이 이
pipeline을 감쌀 때의 참고 mapping이다. 이 저장소에 endpoint를 추가하지 않으며,
`docs/api/`의 실제 endpoint 목록에도 넣지 않는다.

### 4.0 연습 프롬프트 조회

`GET /api/v1/practice-prompts`

Query: `difficulty`, `target`, `limit`. 모두 optional.

Response `200`:

- `promptCatalogRevision: string`
- `prompts[]`: `promptId`, `displayText`, `difficulty`, `practiceTargets[]`,
  `syllableCount`, `licenseSpdx`

`GET /api/v1/practice-prompts/{prompt_id}`는 단일 프롬프트를 반환한다.
두 endpoint 모두 expected phone sequence·정답 발음 판정·내부 hash를 노출하지
않는다. 모델용 contract는 session 생성 시 서버 내부에서 같은 `promptId`로
파생한다.

Error:

- `404 prompt_not_available`
- `503 prompt_catalog_unavailable`

### 4.1 Session 생성

`POST /api/v1/pronunciation-sessions`

Request JSON:

- `promptId: string`, required (Stage A0 카탈로그 ID)
- `promptText: string | null`, `analysisProfile=uncalibrated`에서만 허용
- `promptVersion: string | null`, optional
- `maxAttempts: integer`, optional, default 3
- `analysisProfile: string`, optional, default `catalog_calibrated`

`promptId`와 `promptText`가 동시에 오면 `422 prompt_source_conflict`로
거부한다. 두 값 모두 없으면 `422 prompt_invalid`다.

Response `201`:

- `sessionId: UUID`
- `promptId: SHA-256`
- `status: awaiting_audio`
- `promptVersion`
- `promptCatalogRevision`
- `analysisProfile`
- `expiresAt | null`

Error:

- `422 prompt_invalid`
- `422 prompt_source_conflict`
- `409 prompt_policy_blocked`
- `503 prompt_frontend_unavailable`
- `503 prompt_catalog_unavailable`

### 4.2 음성 attempt 제출

`POST /api/v1/pronunciation-sessions/{session_id}/attempts`

Request `multipart/form-data`:

- `audioFile: binary`, required
- `clientAttemptId: UUID`, optional idempotency key
- `promptId: SHA-256`, required

Response `200` for synchronous analysis or `202` for worker execution:

- `attemptId`
- `status`: `analyzing | coaching_ready | rerecord_required | uncertain | failed_closed`
- `diagnosticProfile | null`
- `decision`
- `learnerFeedback | null` (Stage H 결과; `decision != coach`이면 사유·다음 행동만)
- `evidenceReceipt`

Error mapping:

| Code | Condition | Automatic action |
|---|---|---|
| `audio_invalid` | signature, format, size, duration 오류 | 새 파일 요청 |
| `audio_qc_failed` | silence, clipping, non-finite, noise gate 실패 | rerecord |
| `prompt_mismatch` | WER/CER 또는 content threshold 초과 | 발음 판정 금지, rerecord |
| `analysis_low_confidence` | S1/S2 confidence 부족 | rerecord/uncertain |
| `s1_s2_disagreement` | 독립 auditor 불일치 | 자동 코칭 중단 |
| `backend_unavailable` | 모델 revision/adapter unavailable | `failed_closed`, retry 가능 여부 명시 |
| `session_expired` | session TTL 초과 | 새 session 생성 |

### 4.3 결과 조회

`GET /api/v1/pronunciation-sessions/{session_id}/attempts/{attempt_id}`

비동기 실행을 선택한 경우에만 필요하다. 결과가 `analyzing`이면 중간 모델
점수를 노출하지 않고 상태만 반환한다.

## 5. Attempt 상태와 재녹음 (AI-only)

한 번의 `run_attempt` 호출은 아래 terminal 상태 중 하나로 끝난다. 중간 상태를
결과로 반환하지 않는다.

```text
run_attempt(prompt_id, audio_path)
  |-> coaching_ready        : gate·allowlist 통과, LearnerFeedback 발행
  |-> completed_no_issue    : 근거 있는 오류 없음
  |-> rerecord_required     : audio QC 실패, content mismatch, 저신뢰
  |-> uncertain             : S1/S2 불일치, 필수 metric 결손, OOD
  |-> failed_closed         : 계약·backend·threshold·독립성 불완전
```

판정 규칙:

- `coaching_ready`: content match·필수 S1 축·S2 승인·threshold·advice allowlist를
  모두 통과하고 span 근거가 존재
- `rerecord_required`: 사용자 행동으로 회복 가능한 실패(재녹음)
- `uncertain`: 모델이 판단을 보류한 경우. 자동 코칭을 발행하지 않는다
- `failed_closed`: 실행 환경·계약 결손. 점수나 조언을 대체 생성하지 않는다

재녹음 루프는 호출자가 같은 `prompt_id`로 `run_attempt`를 다시 호출해 만든다.
pipeline은 무상태이며 attempt 간 비교·이력 보관은 호출자(연구 스크립트,
실험 runner)의 책임이다. 서버 session 저장소, TTL, idempotency key, DB는
이 프로젝트 범위 밖이다(G5는 이 결정으로 해소한다).

이력을 남길 때 보관 가능한 항목: `prompt_id`, `prompt_version`, `audio_sha256`,
attempt 상태, failure code, aspect 상태, 사용한 threshold artifact ID,
backend revision. raw audio와 전사 원문은 보관하지 않는다(§8.1).

## 6. 실행 예산과 모델 수명 (G6)

목적은 사용자가 낭독하고 피드백을 받아 재녹음하는 루프이므로, 1 attempt의
응답 시간이 설계 제약이다. S1 9개 branch와 S2 독립 backend를 attempt마다
전부 실행하면 large 모델 다수의 forward가 직렬로 누적된다.

고정할 항목:

- **모델 상주**: WavLM-large, HuBERT-large/Whisper-large-v3, UTMOSv2 등은
  `ModelRuntimePort` 뒤에서 프로세스 수명 동안 1회 로드한다. adapter가
  request마다 checkpoint를 읽는 구현은 금지한다.
- **branch 예산**: branch별 wall-clock 상한과 timeout을 profile에 기록하고,
  초과 branch는 `missing`으로 표시한다. timeout을 0점으로 환산하지 않는다.
- **필수/선택 분리**: content·segmental·duration을 필수 축으로, naturalness·
  speaker_similarity 등을 선택 축으로 선언한다. 선택 축 실패는 `coach`를
  막지 않고, 필수 축 실패는 `uncertain` 이하로 내린다.
- **호출 형태**: `run_attempt`는 동기 함수이며 부분 결과를 반환하지 않는다.
  예산을 초과하는 profile은 batch/offline 실행으로 분리하고, 대화형 연습
  루프용 profile과 같은 이름을 쓰지 않는다.
- **동시성**: GPU worker 동시 실행 수와 queue 상한을 명시하고, 초과 요청은
  대기 상태를 반환한다. 조용한 지연이나 부분 결과 반환은 금지한다.
- **측정 의무**: 실제 backend 연결 시 branch별 latency와 peak memory를
  측정해 artifact에 기록한다. GB10 통합 메모리 환경에서 OOM은 VRAM 여유가
  아니라 측정으로 판단한다.

현재 상태: 어떤 실측 latency artifact도 존재하지 않으므로 대화형 지연 적합성은
`unverified`다. §10 구현 순서에서 S1 branch 연결과 함께 측정을 수행한다.

## 7. Offline 학습·승격 pipeline

온라인 추론과 별도로 모델을 개선하는 경로는 다음과 같다.

```text
manifest/data card/license/privacy audit
  -> label role/QC audit
  -> speaker-disjoint split + prompt reservation
  -> S1 smoke train
  -> checkpoint reload
  -> dev evaluation + slice/calibration audit
  -> independent S2 evaluation
  -> metamorphic tests
  -> model/reference promotion decision
  -> immutable registry/artifact
```

각 단계의 결과 상태는 `static_ready`, `running`, `completed`, `failed_closed`,
`blocked`를 구분한다.

- data audit 실패: training 실행 금지
- speaker split 또는 prompt reservation 실패: 실행 금지
- smoke/reload 실패: full training 승격 금지
- dev metric만 존재: locked-test 또는 production quality로 해석 금지
- S1/S2 독립성 실패: coaching service에 연결 금지
- checkpoint/metadata/config hash 불일치: reload·promotion 금지
- native multi-speaker metadata 부재: reference bank 생성 금지

현재 저장소에서 실제로 확인된 것은 prototype TTS multi-batch/resume 경로와
일부 metric/contract test다. full-corpus convergence, 실제 S1/S2 backend,
native speaker bank, 외부 generator weight는 이 설계의 target/blocked 영역이다.

## 8. 저장·개인정보·증거 경계

### 8.1 기본 저장 정책

- raw audio: 분석 중 restricted temporary storage만 사용하고 기본 미보존
- audio identity: `audio_sha256`만 evidence에 기록
- prompt: 응답에는 prompt ID/version을 우선 사용하고 raw text 로그는 금지
- model: revision/checksum/config hash 기록
- metric: aggregate와 confidence/missing mask만 기록
- attempt 이력(호출자 보관): 상태, failure code, gate receipt, result digest만 저장
- 공개 artifact: raw audio, transcript, speaker hash, restricted path, checkpoint 제외

### 8.2 외부 연동

각 adapter는 다음 typed result를 반환한다.

- `backend_id`, `revision`, `license_status`
- `request_digest`, `input_digest`
- `started_at`, `finished_at`, `timeout`, `retryable`
- `status`, `error_code`, `failure_detail_public`

모델 hub, dataset portal, GPU worker, object storage, human-review export는
각각 별도의 adapter다. 인증 token·cookie·API key는 코드·문서·report에
기록하지 않는다. revision/checksum/license가 확인되지 않은 backend는
`backend_unavailable`로 fail-closed한다.

### 8.3 Threshold와 calibration artifact (G8)

content mismatch, confidence, OOD, severity의 모든 임계값은 코드 상수가 아니라
`ThresholdSet` artifact에서 로드한다. artifact는 최소한 다음을 포함한다.

- `threshold_set_id`, `frozen_at`, `analysis_profile`
- 산출에 사용한 dataset·split ID와 `manifest_sha256`
- calibration 방법과 대상 지표, 신뢰구간
- 적용 대상 branch/metric 목록과 각 임계값
- 이 artifact가 **적용 가능한 코호트**의 명시(§11)

관련 기존 계약: `configs/coaching/e02_confidence_guard_*`,
`src/voice_coach/evaluation/calibration.py`,
`src/voice_coach/evaluation/b05_locked_contract.py`.

규칙:

- artifact 없이 실행하는 profile은 `uncalibrated`로만 표기하고 자동 코칭을
  발행하지 않는다.
- dev split에서 산출한 임계값을 locked-test 또는 production 임계값으로
  기술하지 않는다.
- artifact가 선언한 코호트 밖의 사용자 음성에 적용할 때는 결과를
  `out_of_distribution`으로 표시한다.

## 9. 검증 계획과 완료 기준

| 단계 | 검증 | 완료 조건 |
|---|---|---|
| Pipeline 계약 | `run_attempt` terminal 상태·failure code 매핑 | 중간 상태를 결과로 반환하지 않고 결손을 점수로 대체하지 않음 |
| Prompt catalog | card hash/schema 검증, 난이도·coverage 필터, 자유 입력 거부 | 사용자 표시 문장과 모델 contract가 동일 `promptId`에서 파생됨 |
| Prompt | NFC/G2P golden cases, inventory audit | expected phone version/hash 고정 |
| Offset 매핑 | 음절·offset·phone index golden cases, join 실패 case | 근거 없는 span을 발행하지 않음 |
| Audio | valid WAV, corrupt file, silence, clipping, duration | 모든 실패가 명시적 QC state로 종료 |
| Content | known prompt/mismatch recordings | mismatch에서 발음 issue를 생성하지 않음 |
| S1 | synthetic feature fixtures + real approved smoke | branch receipt, missing mask, confidence 저장 |
| S2 | independent backend fixture + disagreement fixture | 동일 backend/정렬 재사용 차단 |
| Coaching | allowlist/OOD/low confidence cases | unsupported·uncertain 자동 코칭 금지 |
| Feedback 구성 | span 결손, catalog 미등재 issue, missing 축 표시 | 결정 없이 문구가 생성되지 않고 미측정이 저점수로 표시되지 않음 |
| Runtime 예산 | branch별 latency·peak memory 실측, timeout case | profile 예산이 실측으로 뒷받침됨 |
| Reload | model/config/optimizer checkpoint reload | hash·device·state 일치 |
| 재녹음 | attempt 1→rerecord→attempt 2 | 동일 prompt/version 재사용, 결과 독립 |
| 코호트 | 대상 사용자 표본에서의 error detection/calibration | L2 코호트 결과를 모어·아나운서 코호트 성능으로 기술하지 않음 |
| Privacy | response/log/artifact scan | raw audio/path/speaker/secret 유출 0 |
| Human validation | blind listening/expert review가 필요한 경우 별도 실행 | 사람 평가를 모델 성공으로 대체하지 않음 |

연구 실행 순서는 `data audit → split/label/QC → smoke → reload → evaluation →
human validation`을 유지한다. 한 gate의 통과는 다음 gate나 production readiness를
자동으로 의미하지 않는다.

## 10. 구현 순서

1. `pronunciation_feedback` 패키지 경계와 domain/ports를 추가하되, 현재 Oracle
   계약과 분리한다.
2. `PromptCatalogPort`와 R00 card adapter, `list_prompts`와 CLI `prompts`
   명령을 구현한다. 이 단계까지 모델은 필요하지 않고 목적의 (1)이 먼저 성립한다.
3. `AudioDecoderPort`와 temporary store를 구현하고 audio QC만 먼저 통합한다.
4. `PromptFrontendPort`와 expected-phone contract, offset↔phone 매핑을 고정한다.
5. 무상태 `run_attempt` 호출로 재녹음 루프를 성립시킨다(§5). session 저장소는
   도입하지 않는다.
6. content/CTC/alignment adapter를 연결하고 mismatch fail-closed를 구현한다.
   이 단계에서 backend 소속(S1)과 `backend_id`를 선언한다.
7. S1 scorer ports를 branch별로 연결하고 aggregate/missing/confidence receipt를
   만든다. 동시에 branch별 latency·memory를 실측해 §6 예산을 채운다.
8. S2 independent adapter를 별도 runtime·revision으로 연결하고 Stage C backend와의
   ID 충돌 검사를 test로 고정한다.
9. `ThresholdSetPort`와 calibration artifact를 연결한다. 이전까지 모든 profile은
   `uncalibrated`다.
10. coaching decision service와 `FeedbackCompositionService`(Stage H)를 연결한다.
11. flow/state/component/directory 문서를 실제 구현과 함께 갱신한다.
12. synthetic contract → approved smoke → reload → evaluation 순으로 검증한다.
13. 모든 선행 gate가 충족된 뒤에만 full training, reference bank, external generator
    또는 사람 평가를 별도 run으로 수행한다.

2--6단계는 모델 품질과 무관하게 "프롬프트 제시 → 낭독 업로드 → 상태·QC 응답"
루프를 성립시킨다. 발음 판정 품질은 7단계 이후에만 논의한다.

## 11. 대상 코호트 (2026-08-06 재정의)

### 11.1 재정의 내용

목적의 대상 사용자를 **한국어 L2 학습자**(중국어·일본어 L1)로 확정한다.
아나운서·모어 화자 낭독 규범 진단은 이 설계의 범위에서 제외한다.

근거 정합:

| 요소 | 코호트 | 정합 |
|---|---|---|
| 감독 학습 데이터 | AI Hub 71490, 중국어·일본어 L1 L2 학습자 1,830화자 | 대상과 동일 |
| calibration split | 같은 코퍼스의 화자 비중첩 split | 대상과 동일 |
| coaching 오류 태그 | 같은 코퍼스의 L2 오류 annotation 파생 | 대상과 동일 |
| expected phone | g2pK 규칙 적용 표준 발음 | L2 학습 목표로 타당 |

이 재정의로 §1.3의 G9(코호트 불일치)는 해소된다. 대상 사용자가 학습·보정
근거와 같은 모집단이므로, 점수·임계값을 out-of-distribution으로 선언할 필요가
없다. 단 일반화 주장 범위는 여전히 중국어·일본어 L1로 한정한다: 다른 L1
학습자는 slice 평가 없이 지원한다고 기술하지 않는다.

### 11.2 범위에서 제외한 것

아나운서 경로(G10)는 다음이 모두 필요해 이 설계에서 분리한다.

1. 아나운서/모어 화자 낭독 표본과 규범 위반 라벨(장단음, 표준 발음법, 낭독 prosody)
2. 해당 코호트 전용 `ThresholdSet`
3. 규범 오류 태그 ontology와 전문가 감사
4. 권리·metadata 요건을 충족한 기준 음성 bank

1--4는 모두 사람의 발화·판단·동의를 요구하므로 자율 실행으로 도달할 수
없다. 향후 별도 work package로 다룬다.

### 11.3 재정의가 바꾸지 않는 것

- 코호트가 맞아도 **품질 주장은 별개**다. §14의 claim ceiling을 따른다.
- 다른 L1, 아동, 방언 화자, 잡음 환경은 여전히 미검증 slice다.
- gold 라벨·전문가 판정을 요구하는 gate(D07, C03, S108 locked)는
  코호트 재정의로 면제되지 않는다.

## 12. 이 설계가 아직 주장하지 않는 것

이 문서는 다음을 주장하지 않는다.

- 현재 pipeline이 음성 파일을 받아 **자동 발음 진단 점수를 산출한다**는 주장
  (Stage C--E backend 미연결 상태에서는 `failed_closed`로 종료한다)
- 현재 checkpoint가 한국어 발음 feedback 품질을 달성했다는 주장
- S1/S2 실제 backend 평가가 완료됐다는 주장
- native multi-speaker reference bank가 ready라는 주장
- CosyVoice2/StyleTTS2/F5R-TTS weights가 실행 가능하다는 주장
- 전문가 평가, 기관 승인, 학습 효과 또는 production readiness 주장
- 한국어 모어 화자 또는 아나운서 코호트에서의 진단 타당성 주장(§11.2에서 범위 제외)
- 중국어·일본어 L1 외 L2 학습자, 아동, 방언 화자, 잡음 환경에 대한 일반화 주장
- 실측 없이 attempt 응답이 연습 루프에 적합한 지연 안에 완료된다는 주장(§6)

## 13. 구현 현황 (2026-08-23 기준)

코드 경계: `src/voice_coach/pronunciation_feedback/`,
entrypoint: `scripts/run_pronunciation_feedback.py`,
test: `tests/unit/test_pronunciation_feedback_{prompts,audio_intake,pipeline}.py`
(43 test 통과. 실제 모델 backend를 실행한 결과는 포함하지 않는다.)

| 단계 | 모듈 | 상태 | 근거/제약 |
|---|---|---|---|
| Stage A0 prompt 제공 | `adapters/catalog_r00.py` | `implemented` | R00 card 20문장 서빙, schema/card_id/status·정규화·sha256·PII/금지표현 검사 후 fail-closed |
| Stage A prompt 준비 | `pipeline.prepare_prompt`, `adapters/syllables.py` | `implemented` | 음절↔문자 offset 매핑과 `syllable_map_sha256`를 `prompt_version`에 고정 |
| Stage A expected phones | `adapters/expected_phones.py` | `contract_only` | g2pK 0.9.4가 별도 venv에만 있어 기본 환경에서는 `expected_phones_unavailable`. calibrated profile은 fail-closed, `uncalibrated`만 진행 |
| Stage B audio QC | `adapters/audio_intake.py` | `implemented` | soundfile decode + mono/16 kHz, duration·silence·clipping·finite 검사, decode 실패와 QC 실패를 다른 code로 분리 |
| Stage C content/alignment | `ports.ContentAlignmentPort` | `target` | 실제 CTC/ASR·alignment adapter 미구현. backend 소속은 S1으로 선언됨 |
| Stage D S1 채점 | `ports.S1ScorerPort` | `target` | 9 branch adapter 미연결. `S1ScoreBundle`의 branch state/missing mask 계약만 고정 |
| Stage E S2 감사 | `ports.S2AuditorPort` | `target` | 미연결. backend ID 재사용 시 `s1_s2_not_independent`로 fail-closed하는 검사는 구현·test됨 |
| Stage F gate | `gate.py` | `implemented` | content·alignment·필수 branch·S1/S2 confidence·독립성·calibration·evidence·allowlist를 모두 veto로 적용 |
| Stage H feedback 구성 | `feedback.py` | `implemented` | span은 (음절 index, 문자 offset, time range) 3-tuple이 모두 있을 때만 발행, 미측정 축은 상태로 표기, 코호트·비보정·기준음성 부재 notice 부착 |
| threshold artifact | `adapters/threshold_store.py`, `thresholds.py` | `implemented` (artifact 없음) | `configs/pronunciation_feedback/thresholds/<profile>.json`이 없으면 `thresholds_unavailable`. `uncalibrated` profile은 실행되지만 코칭 불가 |
| advice catalog | `adapters/advice.py` | `implemented` (범위 제한) | legacy MVP 4문구만 허용. C01 17-tag는 `pending`이라 미연결 |
| grounded LLM 표현 | `grounded_advice.py`, `adapters/advice_generator_hyperclova.py` | `implemented` (기본 비활성, 단일 통합 smoke) | local loopback에서 승인 claim 선택만 허용. strict validation 실패·timeout·abstain은 catalog 폴백. 2026-08-23 고정 revision 32B load와 동일 입력 2회 strict 통합 smoke 통과. 품질·부하 검증은 별도 |
| seungun phone bridge | `seungun/.../frozen_detector.py`, `seungun_bridge.py` | `implemented` (단일 dev smoke) | frozen CTC/soft/layer-18 detector 출력을 generic phone claim으로 연결. 다수 WAV parity와 phone별 전문가 조음 catalog는 미검증 |
| 기준 음성 비교 | `ReferenceAudioPort` | `blocked` | 권리·speaker metadata 요건 미충족. 응답에 비교 항목 없음 |
| latency 실측 | - | `not_started` | branch adapter 연결 시 함께 측정 |

기본 wiring(`build_default_pipeline`)으로 attempt를 실행하면 prompt 제공 →
audio QC 통과 → threshold 로드까지 진행한 뒤
`failed_closed / content_backend_unavailable`로 종료하며, 각 단계의 receipt와
`audio_sha256`를 반환한다. 이는 결손을 점수로 대체하지 않는 설계된 동작이다.

## 14. 이중 트랙과 자율 실행 계약 (G11)

### 14.1 문제

사람 gate(threshold 사전등록 사람 승인, 전문가 gold, 전문가 코칭 감사,
locked one-shot, blind 청취)가 실행 경로에 섞여 있으면 pipeline 자체가
자율적으로 end-to-end 동작할 수 없다. 반대로 그 gate들을 그냥 통과시키면
engineering 결과가 검증된 결과로 오인된다.

해소 방법은 gate를 없애는 것이 아니라 **트랙을 분리**하는 것이다. 실행
자율성과 주장 권한을 분리해, 자율 트랙은 끝까지 돌지만 주장 상한이 낮고,
주장 권한이 높은 트랙은 사람 gate를 그대로 유지한다.

### 14.2 두 트랙

| | Track E: `engineering_provisional` | Track R: `research_locked` |
|---|---|---|
| 실행 주체 | 자율(사람 개입 없음) | 사람 승인 필수 |
| threshold 출처 | calibration split에서 **사전 고정된 규칙**으로 산출한 artifact (S107) | 사람 승인 사전등록 |
| S2 auditor | 독립 backbone·정렬·split로 구성한 engineering auditor | S201--S203 사전등록 auditor |
| 사용 split | train/dev/calibration | + locked-test (one-shot) |
| coaching 범위 | 승인된 legacy allowlist 4종만 | C03 감사를 통과한 ontology |
| gold 라벨 | 코퍼스 annotation | + D07 독립 전문가 gold |
| 기준 음성 | 없음 | 권리 확인된 reference bank |
| 산출물 표기 | `claim_scope=engineering_provisional` | `claim_scope=research_validated` |
| 허용 주장 | "이 구성에서 이 dev/calibration 지표가 나왔다" | 진단 타당성·성능·일반화 |
| 금지 | production/교육효과/전문가 동의/일반화 주장, locked-test 접근 | 사람 gate 우회 |

Track E는 Track R의 예비 단계가 아니라 **별도 결과 등급**이다. Track E
결과를 Track R 결과로 승격하는 유일한 경로는 사람 gate 통과이며, 재계산·
재실행·재표기로는 승격되지 않는다.

### 14.3 자율 실행 계약

Track E에서 자율 실행이 허용되는 조건:

1. **사전 고정**: threshold 산출 규칙, split 역할, metric 정의, gate 임계를
   실행 **전에** artifact로 동결하고 hash를 기록한다. 결과를 본 뒤 규칙을
   바꾸면 그 run은 무효다.
2. **locked 무접근**: locked-test와 전문가 holdout 경로에 대한 read가 0임을
   실행 계측으로 증명한다. 접근이 관측되면 즉시 `failed_closed`.
3. **독립성 유지**: S2는 S1과 backbone·정렬·split·목표함수를 공유하지 않으며,
   independence receipt를 자동 생성한다. 같은 backend ID가 관측되면 코칭 중단.
4. **claim scope 표기**: 모든 결과 artifact와 사용자 응답에
   `claim_scope=engineering_provisional`을 포함한다.
5. **실패 보존**: 중단·timeout·OOM·비수렴은 성공으로 표기하지 않고 원본
   증거와 함께 deviation ledger에 남긴다.
6. **점수 미가산**: Track E 완료는 연구 완성 배점을 획득하지 않는다.
   배점은 Track R gate가 통과할 때만 부여한다.

### 14.4 사람 gate가 그대로 남는 항목

Track E를 끝까지 실행해도 다음은 열리지 않는다. 자율 실행 계획은 이 항목에
접근하지 않는 것을 성공 조건으로 삼는다.

| 항목 | 왜 사람이 필요한가 |
|---|---|
| G04 정렬 감사 잔여 | 정렬 정답 판단 |
| D07 독립 전문가 gold | 전문가 2인 재평가 |
| C03 코칭 감사 | 조언 정확도·안전 판정 |
| S108 locked one-shot | 사전등록 승인 + G04 + D07 |
| S201--S203 | threshold 사전등록 승인, 전문가 holdout |
| D08 / R01 / R04 / R05 | 원어민 녹음, 권리, blind 청취, 승인 |
| H00--H04 | 참여자 연구 |

### 14.5 Track E의 완료 정의

Track E는 다음이 모두 성립할 때 `engineering_e2e_complete`다.

- 카탈로그 prompt로 시작해 사용자 음성 파일 하나로 `coaching_ready`
  또는 `completed_no_issue`에 도달하는 실행이 재현 가능하다
- Stage C/D/E backend가 실제 모델로 연결되어 있고 각 receipt에 revision이 있다
- threshold artifact가 존재하고 `claim_scope=engineering_provisional`이다
- locked/전문가 경로 접근 계측이 0이다
- branch별 latency·peak memory 실측치가 artifact에 있다
- metamorphic·confidence guard 자동 검증을 통과한다

이 상태는 "발음 진단 품질이 검증됐다"가 아니라 "설계된 경로가 실제 모델로
끝까지 동작하고, 그 구성과 지표가 재현 가능하게 기록됐다"를 뜻한다.

## 15. XLS-R soft information 런타임 계약 (2026-08-09)

상태: **구현 및 합성 단위 검증 완료 / 실제 학습자·전문가 보정 미완료**.

기본 S1 경로의 `kresnik/wav2vec2-large-xlsr-korean`은 hard transcript만 쓰지
않는다. 모델의 `(frame, 1,205 syllable vocabulary)` log-posterior 전체를 한 번
계산하고, 같은 격자를 content alignment와 GOP scorer가 공유한다. Viterbi가
각 기대 음절에 배정한 anchor frame에서 다음 typed evidence를 계산한다.

```text
target_log_posterior = mean_t log P(expected | x_t)
gop_margin = mean_t log P(expected | x_t)
             - max_{j != expected} mean_t log P(j | x_t)
posterior_entropy = mean_t -sum_j P(j | x_t) log P(j | x_t)
target_is_top1 = 1[argmax_j mean_t log P(j | x_t) == expected]
```

- `TokenAlignment`가 위 soft summary를 보존한다.
- `GopS1Scorer`가 음절별 `CtcSoftInformation`을 `S1ScoreBundle`에 싣는다.
- 받침 삭제·모음 품질처럼 posterior 경쟁으로 검출한 segmental advice에는 해당
  음절의 typed soft evidence를 연결한다.
- 휴지·음절 길이 issue는 posterior identity가 아니라 정렬 시간 규칙의 결과이므로
  soft evidence를 원인처럼 붙이지 않는다.
- NaN/Inf posterior는 보간하거나 hard decode로 fallback하지 않고 fail-closed한다.
- 기존 segmental score, issue severity/confidence, frozen threshold는 변경하지
  않았다. Entropy/top-1에 새 gate를 추가하려면 별도 학습자·전문가 calibration과
  threshold artifact 재동결이 먼저 필요하다.

따라서 이 변경은 XLS-R 분포의 정보 손실을 줄이고 audit 가능한 근거를 추가하지만,
ASR posterior를 "정확한 발음일 확률"로 승격하거나 발음 진단 성능·학습 효과가
검증됐다고 주장하지 않는다. 모델 재학습과 전체 학습 실행은 수행하지 않았다.
