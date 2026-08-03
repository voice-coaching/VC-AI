# 발음 교정 Feedback GAN 프로토타입

상태: **구조·손실·교대 학습 및 실데이터 1-step 연결 검증 완료 /
정본 학습·품질 평가 미실행**

## 1. 목적

외국어 모국어 학습자의 한국어 발화를 입력받아 목표 문장과 화자 특성은
유지하면서, 오류가 있는 발음·유창성·운율만 한국어 원어민 reference
분포에 가까워지도록 교정하는 연구용 구조다.

목표는 특정 한국인의 목소리를 복제하거나 학습자의 억양·국적을 지우는
것이 아니다. 생성 결과는 사용자가 연습할 수 있는 교정 reference이며,
실제 학습 효과는 사용자가 다시 녹음한 음성이 개선됐을 때만 판정한다.

## 2. 세 모델 구조

```text
canonical phone IDs ───────────────────────────────┐
                                                   │
학습자 음성 ──→ TTS/VC 생성기 G ──→ draft y0      │
     │                    ▲              │          │
     │                    │              ▼          │
     │                    └── detached feedback ─ 평가기1 D1
     │                                      │
     │                                      ▼
     │                         보정된 음성 refined y1
     │                                      │
원어민 reference ────────────────────────────┤
                                            ▼
                                 평가기2 meta-critic D2
                                            │
                                  progression preference
                                            │
                                            ▼
                              D1 MetaRewardAdapter만 보정
```

### 생성기

`CorrectiveTTSGenerator`는 학습자 waveform, canonical phone/text token,
평가기1의 구조화된 feedback을 받는다.

- 첫 번째 pass는 feedback 없이 `draft`를 만든다.
- 평가기1과 평가기2가 만든 feedback은 `detach`하고 크기를 제한한다.
- 두 번째 pass가 `refined` waveform을 만든다.
- 한 번에 적용할 수 있는 waveform edit amplitude를 제한한다.
- 원어민 입력은 그대로 재구성하도록 identity loss를 둔다.
- 다른 화자의 원어민 waveform을 학습자 waveform의 직접 L1 target으로
  사용하지 않는다.

현재 생성기는 전체 TTS 품질을 주장하는 모델이 아니라 PyTorch로 구현한
미분 가능한 source-conditioned waveform prototype이다. 실제 서비스
vocoder는 별도 TTS 환경과 승인된 weight로 교체해야 한다.

### 평가기1

`PrimaryPronunciationEvaluator`는 생성기와 parameter를 공유하지 않는다.

- 원어민 reference-domain/quality logit
- 목표 phone/text 내용 일치 logit
- segmental, fluency, prosody, comprehensibility 점수
- 두 번째 생성 pass에 전달할 bounded feedback
- 화자·음향 보존 비교용 embedding
- 여러 품질 신호를 합치는 `MetaRewardAdapter`

L2 음성 전체를 0으로 라벨링하지 않는다. AI Hub의 실제 phone 오류,
`error_presence`, 1–5점 사람 평가를 0–1 soft target으로 변환해 사용한다.
오류가 없는 L2 발화도 positive가 될 수 있다.

### 평가기2

`ProgressMetaCritic`은 생성기 및 평가기1과 parameter를 공유하지 않는다.
입력은 다음 waveform뿐이다.

- 원 학습자 음성
- 직전 생성 음성
- 현재 생성 음성
- 원어민 reference

평가기1의 logit, alignment, label, embedding은 입력받지 않는다. 평가기2가
판단한 `현재 > 직전` preference는 평가기1 전체가 아니라 작은
`MetaRewardAdapter`에만 distillation된다. 평가기2에서 생성기로 직접
gradient를 보내지 않는다.

이 평가기2는 학습 내부 meta-critic이다. 기존 연구 파이프라인의 독립 S2
감사기를 대체하지 않는다. production S2는 마지막에 별도 backbone,
별도 holdout으로 생성물과 S1 판단을 다시 감사해야 한다.

## 3. 교대 학습

한 iteration은 다음 순서로 고정한다.

1. `G`로 draft/refined를 만든 뒤 waveform을 detach한다.
2. 평가기1 core를 실제 native QC target, L2 soft target, generated fake,
   matched/mismatched content로 업데이트한다.
3. 평가기2를 `L2→native` positive trajectory와 역순 negative trajectory로
   업데이트한다.
4. 평가기2 preference를 평가기1의 `MetaRewardAdapter`에만 전달한다.
5. 평가기1과 평가기2 parameter를 freeze한다.
6. 평가기1 feedback을 detach해 생성기의 두 번째 pass에 넣는다.
7. 생성기를 adversarial/content/reward/monotonic/identity/minimal-edit
   loss로 업데이트한다.

네 optimizer가 소유하는 parameter 집합은 서로 달라야 하며, 학습 step이
실행될 때 이를 전수 검사한다.

## 4. 데이터 사용 경계

### 학습자 데이터

AI Hub 71490의 `train` split 중 QC를 통과하고 예약 prompt가 아닌 발화만
사용한다. `stack`, `calibration`, `locked_test`는 GAN 학습에서 차단한다.

전체 322,380건 중 `error_presence=True`는 89,606건이다. 나머지 발화를
외국인이라는 이유만으로 오답 처리하면 안 된다.

### 원어민 데이터

현재 역할 매트릭스상 TTS 학습에 사용할 수 있는 원어민 코퍼스는 KSS뿐이다.
KSS는 CC-BY-NC-SA-4.0이므로 비상업 연구 artifact로 격리한다.

- FLEURS: ASR·alignment 외부 평가용
- ZEROTH-Korean: ASR·alignment 기준선용
- Seoul Corpus: phone alignment·운율 평가용
- KSS: 비상업 연구 TTS baseline

현재 L2 prompt 229개와 정확히 일치하는 KSS prompt는 3개뿐이고 KSS 자체도
사실상 단일 화자이므로, paired waveform 학습이나 “한국어 원어민 전체
분포”라는 주장을 할 수 없다. 기본 학습 전략은 `unpaired_conditional`이며,
실제 승격 전에는 동의가 확보된 다화자 동일문장 원어민 데이터가 필요하다.

## 5. 안전 제약

- L1, 국적, 성별, 숙련도를 모델 입력으로 사용하지 않는다.
- 이 값들은 sampling 균형과 slice audit에만 사용한다.
- 두 domain에 동일한 resample, 길이 bucket, padding, codec/noise 처리를
  적용해 평가기가 마이크나 코퍼스를 구분하는 shortcut을 막는다.
- 사용자 음색 보존 모드는 명시적 동의가 있을 때만 사용한다.
- 생성 음성에는 생성물 표시와 보존·삭제 정책을 적용한다.
- raw audio, transcript, source path는 집계 report에 저장하지 않는다.
- 다른 원어민의 waveform을 학습자 waveform의 직접 정답으로 사용하지
  않는다.

## 6. 파일

| 파일 | 역할 |
|---|---|
| `src/voice_coach/models/adversarial_tts.py` | 생성기, 평가기1, 평가기2, two-pass 조합 |
| `src/voice_coach/training/adversarial_tts.py` | 네 단계 교대 학습과 gradient 격리 |
| `src/voice_coach/training/adversarial_tts_contract.py` | Torch 이전 split/license/privacy preflight |
| `src/voice_coach/data/adversarial_speech.py` | AI Hub·KSS 필터, 제한구역 audio resolver, duration matching, missing-label mask와 collate |
| `src/voice_coach/application/adversarial_tts.py` | 생성 WAV와 AI 생성물 sidecar의 원자적 저장 |
| `configs/experiments/adversarial_tts_feedback_v1.yaml` | 고정 실험·안전 계약 |
| `scripts/train_adversarial_tts.py` | read-only 검증, synthetic 및 bounded real-data CPU smoke |

## 7. 실행

Torch를 import하지 않고 설정과 manifest hash를 검증한다.

```bash
.venv-scorer/bin/python scripts/train_adversarial_tts.py
```

실제 데이터를 열지 않고 CPU에서 임의 tensor 한 batch로 네 optimizer와
gradient 연결을 검사한다.

```bash
CUDA_VISIBLE_DEVICES="" \
  .venv-scorer/bin/python scripts/train_adversarial_tts.py \
  --synthetic-smoke
```

`synthetic-smoke`의 loss는 연구 결과나 모델 성능이 아니다. 실제 학습을
시작하기 전 실제 제한구역 음원과 tensor 연결만 검사하려면 다음을
실행한다.

```bash
CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .venv-scorer/bin/python scripts/train_adversarial_tts.py \
  --real-data-smoke \
  --real-record-limit 16 \
  --real-batch-size 2 \
  --real-max-duration-seconds 2 \
  --report artifacts/experiments/adversarial_tts_feedback_v1/real_data_wiring_smoke_20260729.json
```

이 모드는 AI Hub `train`과 KSS의 실제 audio를 열지만 다음 경계를
강제한다.

- learner `stack`·`calibration`·`locked_test`, QC flag와 예약 prompt 제외
- native source의 license·role·split을 catalog와 exact match
- 원어민 canonical G2P sidecar가 없으면 content loss를 mask
- 관측되지 않은 사람 평정 축을 masked BCE/MSE에서 제외
- 최대 128개 record, batch 1~4, audio 10초 이하
- CPU 단일 update만 수행하고 checkpoint를 만들지 않음
- report에는 raw transcript, 원천 audio path, item·speaker ID를 저장하지
  않음

## 8. 2026-07-29 실데이터 연결 스모크

정본 manifest와 제한구역 audio를 사용한 실제 결과는 다음과 같다.

| 항목 | 결과 |
|---|---|
| 실행 상태 | `real_data_wiring_smoke_completed` |
| device / optimizer step | CPU / 1 |
| learner | 16건 로드, batch waveform `[2, 27308]` |
| native | 16건 로드, batch waveform `[2, 27016]` |
| learner 관측 aspect / content | 4 / 2 |
| native 관측 aspect / content | 0 / 0 |
| checkpoint / 연구 결과 | 생성 안 함 / 아님 |
| report SHA-256 | `24cb23347cb7b99bf659e67be48cc8333c41a418923809dbbced3000eb471055` |

네 optimizer의 loss와 gradient norm은 모두 finite였다. 그러나 생성기
reward는 이 한 step에서 `0.530526`에서 `0.530431`로 소폭 낮아졌다.
따라서 이 실행은 **실제 음원 decode, 필터, mask, collate와 gradient
배선 검증**일 뿐이고, 생성 품질 개선이나 GAN 학습 성공의 근거가 아니다.

정본 학습과 논문 품질 실험을 시작하려면 다음이 먼저 필요하다.

1. KSS에 없는 원어민 canonical token sidecar 또는 검토된 G2P 경로
2. 원어민 다화자 데이터 사용 동의와 training role 승인
3. 외부 speaker/content encoder와 실제 vocoder weight 고정
4. 독립 S2 및 전문가 평가 preregistration
5. 실제 StyleTTS2/F5R-TTS 경로와 reward-hacking audit
6. 현재 실행 중인 S104 GPU job 종료와 별도 TTS 환경 확보
