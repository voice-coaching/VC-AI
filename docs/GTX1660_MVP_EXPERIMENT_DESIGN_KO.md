# GTX 1660 6GB 기반 한국어 AI 보이스 코칭 MVP 실험 설계

## 1. 실험 목적

이 실험의 목적은 논문의 전체 성능 상한을 GTX 1660에서 재현하는 것이 아니다. 다음 최소 연구 가설을 제한된 하드웨어에서 검증하는 것이 목적이다.

1. AI Hub 71490의 목표 음소·실현 음소·오류 구간을 신뢰 가능한 학습 데이터로 변환할 수 있는가?
2. 명시적 음향 특징보다 frozen WavLM 특징이 발음 오류와 발화 점수 예측을 개선하는가?
3. 음소 -> 단어 -> 발화 계층 head가 단순 pooling head보다 유의미하게 좋은가?
4. 명시적 branch와 SSL branch를 결합하면 정확도와 calibration이 함께 개선되는가?
5. 예측 근거와 confidence만으로 안전한 코칭 메시지를 생성할 수 있는가?
6. 사전 생성된 다중 참조음을 포함한 한 번의 폐쇄루프를 로컬에서 재현할 수 있는가?

MVP 성공은 WavLM-large, StyleTTS 2, F5R-TTS 또는 GRPO 학습을 의미하지 않는다. `음성 -> 진단 -> 근거 제한 코칭 -> 참조음 -> 재녹음 비교` 한 사이클이 재현 가능하고, 진단 모델이 사전 정의한 오프라인 지표를 통과하는 것을 의미한다.

## 2. 하드웨어 운영 한계

실측 환경:

- GPU: NVIDIA GeForce GTX 1660
- VRAM: 6,144MiB
- Windows WDDM 및 화면 프로세스 사용량: 약 1.2-1.4GB
- 실사용 가능 VRAM: 약 4.6-4.8GB
- Compute capability: 7.5
- 현재 Python: 3.14

실험 환경은 Python 3.11을 별도로 구성한다. WavLM 공식 모델은 16kHz 입력을 전제로 하므로 SSL branch는 16kHz mono 파생본을 사용한다. 원본 48kHz 파일은 운율·F0·energy 분석을 위해 보존한다.

환경은 다음처럼 격리한다.

- `assess-py311`: 데이터, explicit features, WavLM, head 학습, 평가
- `tts-py310`: CosyVoice 계열 후보를 검증할 때만 WSL2/Linux에서 사용
- F5-TTS 실험은 별도 Python 3.11 환경 또는 컨테이너

전역 Python 3.14는 변경하지 않는다. scorer, ASR, TTS는 동시에 GPU에 상주시키지 않고 job queue concurrency를 1로 고정한다. `empty_cache()`만 의존하지 않고 단계가 바뀔 때 프로세스를 종료해 VRAM을 회수한다.

### 2.1 VRAM 규칙

- PyTorch 프로세스 peak allocated VRAM 목표: 3,500MiB 이하
- PyTorch 프로세스 hard stop: 4,000MiB
- 실험 시작 전 전체 free VRAM: 4,500MiB 이상
- 실행 중 시스템 free VRAM: 600MiB 이상
- feature extraction batch size: 1
- 최대 입력 길이: 8초
- 8초 초과 발화: 경계 정렬 기반 chunk + 1초 overlap
- WavLM encoder: 항상 frozen, `eval()`, `no_grad()`
- TTS와 scorer를 동시에 GPU에 올리지 않음
- vLLM, WavLM-large full fine-tuning, 대형 Whisper 학습은 금지

FP16 autocast는 smoke benchmark에서 FP32보다 peak VRAM 또는 처리 시간이 실제로 개선될 때만 사용한다. GTX 1660에서 FP16이 항상 더 빠르다고 가정하지 않는다.

## 3. 데이터 준비

### 3.1 현재 데이터 상태

| 구분 | 라벨 발화 수 | 해제 WAV |
|---|---:|---:|
| Training 일반 Speech | 102,632 | 102,632 |
| Training Pronunciation | 286,560 | 0 |
| Validation 일반 Speech | 12,829 | 0 |
| Validation Pronunciation | 35,820 | 0 |
| 합계 | 437,841 | 102,632 |

발음 음성과 Validation 음성 ZIP은 다운로드되어 있으므로 첫 작업은 해제와 3-way pairing audit다.

### 3.2 감독 신호

- JSON phone: 목표 발음 음소와 시간 구간
- TextGrid phone: 실제 실현 음소와 시간 구간
- TextGrid tag: 초성·중성·종성 등 오류 위치
- JSON utterance score: 발음숙련도, 유창성, 이해도
- 화자 조건: L1, 숙련도, 성별, 학습 기간 등

JSON과 TextGrid phone 배열을 index로 직접 결합하지 않는다. 공백 interval 때문에 길이가 다를 수 있으므로 시간 overlap과 50ms 허용 오차를 사용한다.

### 3.3 오디오 파생본

두 오디오 경로를 분리한다.

1. `prosody_48k`: 표준 decoder로 읽은 원본 sample을 정상 WAV 헤더로 재인코딩
2. `ssl_16k`: amplitude를 임의로 loudness-normalize하지 않고 16kHz mono로 resample

운율 branch는 normalization 전 신호를 사용하고, WavLM 입력 normalization은 processor 설정에 맡긴다.

### 3.4 split 재구성

AI Hub 공식 Training과 Validation은 화자가 전부 겹치므로 그대로 사용하지 않는다.

1. 전체 데이터를 `UserID` 기준으로 그룹화한다.
2. speaker-disjoint Train/Dev/Stack/Calibration/Locked-test를 50/10/10/10/20으로 생성한다.
3. Dev는 layer·head·loss 선택에만 사용한다.
4. Stack은 고정된 branch의 fusion gate 학습에만 사용한다.
5. Calibration은 확률 보정과 abstention threshold 고정에만 사용한다.
6. Locked-test는 모든 선택이 끝난 뒤 한 번만 평가한다.
7. Locked-test 내부에 prompt-disjoint challenge subset을 별도로 표시한다.
8. L1, 성별, 숙련도, 오류 유형 분포를 층화한다.
9. `ZA`, `O` gender, 비정상 메타데이터는 주평가에서 분리해 audit slice로 보존한다.

L1·성별·숙련도는 기본 모델 입력으로 사용하지 않고 평가 slice로만 사용한다. Train에서만 희귀 오류를 추가 sampling할 수 있으며 Dev/Stack/Calibration/Locked-test는 원래 오류 유병률을 유지한다.

### 3.5 세 단계 subset

#### Parser smoke subset

- train 256
- dev 128
- test 128
- 목적: parser, OOM, loss 감소, metric pipeline 검증
- seed: 42

#### Model smoke subset

- 총 2,000
- 목적: WavLM layer 6/9/12, chunk 길이, cache 크기, head overfit 가능성 검증
- 이 단계에서 선택한 layer를 pilot 전체에서 고정

#### MVP pilot subset

- Train 10,000
- Dev 2,000
- Stack 2,000
- Calibration 2,000
- Locked-test 4,000
- 총 20,000
- 전체 고유 화자 최소 700
- 한 화자당 기본 최대 20발화
- Train에서만 희귀 오류 발화를 화자당 최대 10개 추가 가능
- 중국어/일본어, 성별, 숙련도는 각 split에서 최소 slice 수를 보장
- Locked-test는 중복 없이 고정하고 모델 선택에 사용하지 않음

주 평가 모집단은 ZH/JA와 F/M 화자다. `SpeakerMetadata.proficiency`의 Advance와 Fluent는 희소 셀에서 Upper로 묶는다. 파일명의 A/B는 proficiency가 아니라 recording level이므로 split 층화에 사용하지 않는다. `ZA` 언어 코드와 gender `O` 화자는 학습·튜닝에서 제외하되 audit-only 세트로 보존한다.

화자당 발화는 RW/RS/RP 과제의 다양성을 보존해 선택한다. Train에서만 희귀 오류를 보강하며 동일 `화자 x 오류유형` 최대 3건, 자연 분포 대비 최대 5배를 넘지 않는다. 별도 오류 head는 다음 조건을 모두 만족할 때만 만든다.

- Train 오류 interval 500개 이상
- Dev와 Locked-test 각각 오류 interval 50개 이상
- Train 고유 화자 20명 이상

조건에 못 미치는 오류는 `other_rule`로 합치거나 정성 challenge로만 보고한다. Train batch에는 `L1 x 성별 x 숙련도` inverse-square-root 가중치를 적용하되 최대 3배로 제한한다. Dev 이후 split은 oversampling하지 않는다.

#### Scale-up subset

- Train 최대 100,000
- Dev/Stack/Calibration은 필요 시 각 5,000까지 확대
- Locked-test는 MVP pilot 4,000을 그대로 유지
- MVP pilot에서 선택된 단 하나의 모델 구성만 확장
- scale-up 예상 시간이 7일을 넘으면 수행하지 않음

### 3.6 데이터 게이트 G0

다음을 모두 통과해야 모델 실험을 시작한다.

- WAV-JSON-TextGrid 결합률 99% 이상
- split 간 `UserID` 중복 0
- PCM hash 기준 split 간 중복 음성 0
- JSON parse 성공률 100%
- 음소·단어 시간 구간이 오디오 범위를 벗어나는 비율 1% 미만
- 비공백 phone 시간 coverage 95% 이상
- 대표 200건 수동 pairing 정확도 95% 이상
- WAV decoder 실패율 0.1% 미만
- subset의 L1·성별·숙련도·오류 분포 리포트 생성
- 정규화 prompt hash 누수 리포트 생성
- 최종 판정용 전문가 subset 800건 확보 계획 수립

전문가 subset은 자연분포 400건과 오류 강화 400건으로 구성하고 2인이 독립 평가한다. 오류 유형 합의 kappa 0.65, 종합 점수 ICC/QWK 0.70 미만이면 모델의 최종 성능 주장을 중단하고 rubric을 수정한다.

## 4. 예측 과제

### Task P1. Phone error detection

입력:

- 목표 phone
- 실제 발화 음향 특징
- phone 구간과 좌우 context

출력:

- correct/error 이진 확률
- 오류 위치 coarse class
- 가능할 경우 target phone -> realized phone

평가:

- macro F1
- AUPRC
- event F1: 50ms tolerance 또는 temporal IoU 0.5
- 오류 유형별 recall

### Task U1. Utterance multi-aspect score

출력:

- pronunciation
- fluency
- comprehensibility

점수를 연속값으로만 취급하지 않고 ordinal 구조를 보존한다.

평가:

- QWK
- Spearman
- MAE
- ICC는 전문가 독립 holdout 확보 후 보고

### Task C1. Confidence and abstention

출력:

- calibrated confidence
- accept/abstain

평가:

- ECE
- Brier score
- risk-coverage curve
- 80% coverage에서 오류율

### Task F1. Coaching faithfulness

평가:

- 진단 evidence에 없는 오류 언급 비율
- 오류 위치·목표 음소 일치율
- 저신뢰 발화 재녹음 요청률
- 전문가 조음 지시 적합도

## 5. 특징 추출

### 5.1 명시적 branch

MVP에서 계산할 특징:

- duration: phone, syllable, word, utterance
- pause: count, ratio, mean, misplaced pause
- speech/articulation rate
- F0: median, range, slope, voiced ratio, boundary movement
- energy/loudness: RMS, range, clipping
- rhythm: vowel duration nPVI
- alignment confidence
- 목표/실현 phone identity

UTMOS는 MVP 필수 항목이 아니다. 사용하더라도 학습자 점수로 쓰지 않고 TTS artifact gate로만 사용한다.

### 5.2 SSL branch

기본 모델:

- `microsoft/wavlm-base-plus`
- 16kHz
- encoder frozen
- batch 1
- 8초 chunk

공식 모델 카드는 WavLM Base Plus가 영어 중심 사전학습 모델임을 명시한다. 따라서 한국어 L2 전이는 실험 가설이며, explicit baseline을 이기지 못하면 WavLM branch를 유지하지 않는다.

캐시 단위:

- phone pooled embedding
- word pooled embedding
- utterance mean/std embedding
- dtype float16
- shard 단위 저장

전체 frame embedding은 저장하지 않는다. 768차원 phone/word embedding이 너무 크면 train split에만 고정 projection을 적용한 256차원 캐시를 별도 ablation으로 만든다.

50Hz frame 전체를 FP16으로 저장하면 768차원 기준 약 76.8KB/초이므로 대규모 데이터에서 수백 GB가 된다. phone pooled cache만 저장하고 2,000발화 단위 shard를 임시 파일에 쓴 뒤 atomic rename한다. cache key에는 audio checksum, 모델 revision, preprocessing hash, layer, dtype을 포함한다.

Pilot에서 train-only IncrementalPCA 256차원을 비교한다. 768차원 대비 phone macro F1 하락이 1%p 이내이면 scale-up은 256차원 cache를 사용한다. PCA는 Dev/Stack/Calibration/Test에 다시 fit하지 않는다.

### 5.3 긴 발화 처리

1. TextGrid 단어 경계에 가까운 지점에서 최대 8초 chunk 생성
2. 1초 overlap
3. phone/word가 chunk 경계에 걸리면 더 많은 음성을 포함한 chunk 하나에만 할당
4. utterance score는 chunk embedding의 attention 또는 mean pooling으로 집계

## 6. 모델 구성

### M0. Oracle loop

예측 모델 없이 gold error tag와 gold score를 `DiagnosticProfile`로 변환한다. 코칭과 UI 계약이 모델 오류와 독립적으로 동작하는지 검증한다.

### M1. Explicit baseline

- 입력: 명시적 특징
- phone: class-weighted logistic regression 또는 histogram gradient boosting
- utterance: ordinal logistic/regression baseline
- GPU 불필요

### M2. Frozen WavLM simple head

- phone pooled 768 -> LayerNorm -> MLP 256 -> output
- utterance mean/std 1536 -> MLP 256 -> 3 aspect heads
- encoder는 학습 그래프에서 제외

### M3. GTX-HiPAMA-lite

- cached phone embedding 768 -> linear projection 256
- 1-layer BiGRU hidden 128 또는 2-layer Transformer, 4 heads
- phone head
- phone -> word attention pooling
- word head
- word -> utterance attention pooling
- pronunciation/fluency/comprehensibility ordinal heads
- 총 trainable parameter 목표: 1.5M 이하

BiGRU와 Transformer 중 peak VRAM 4,000MiB 이하이며 Dev primary metric이 높은 하나만 선택한다.

### M4. Confidence-aware fusion

- 입력: M1 명시적 score/confidence + M3 score/confidence
- logistic/MLP gate
- alignment confidence가 낮으면 duration/GOP 계열 가중치 감소
- noise/clipping이 높으면 F0/energy 계열 가중치 감소

### M5. Calibrated selective scorer

- Calibration split에서 오류군별 Platt scaling
- 발화 ordinal score temperature scaling
- 90% split-conformal prediction interval
- 발행된 교정 precision 90% 이상을 만족하는 오류군별 threshold
- low confidence 시 abstain

다음 중 하나라도 해당하면 단정적 교정을 발행하지 않는다.

- 음질 또는 내용 일치 hard gate 실패
- 보정 확률이 threshold 미만
- explicit branch와 WavLM branch가 크게 불일치
- conformal interval이 교정 결정 경계를 가로지름

## 7. 실험 매트릭스

| ID | 구성 | 검증 가설 | 다음 단계 조건 |
|---|---|---|---|
| H0 | 1,000개 feature extraction benchmark | 6GB에서 WavLM-base 운용 가능 | OOM 0, peak <= 4,000MiB |
| E0 | M0 oracle loop | schema와 코칭 계약 정상 | 100개 결과 생성 성공 |
| E1 | M1 explicit | 해석 가능한 기준선 | phone F1 >= 0.50 |
| E2 | M2 WavLM simple | SSL 특징의 추가 가치 | E1 대비 phone F1 +0.03 또는 U1 QWK +0.03 |
| E3 | M3 GTX-HiPAMA-lite | 계층 구조의 추가 가치 | E2 대비 primary metric +0.02 |
| E4 | M4 fusion | 정확도·강건성 개선 | E3 대비 primary metric +0.02, 다른 주요 지표 하락 <0.02 |
| E5 | M5 calibration | 안전한 abstention | ECE <= 0.10, correction precision >= 0.90 |
| E6 | metamorphic suite | 불변성·국소성 확보 | 기대 결과 통과율 >= 95% |
| E7 | coaching evaluation | 근거 제한 피드백 | unsupported claim < 2% |
| E8 | offline reference bank | 단일 GPU 폐쇄루프 | 20문장 x 4후보 생성·선택 |

E2가 E1을 이기지 못하면 계층 모델로 넘어가지 않고 SSL layer, chunk, pooling을 점검한다. E3가 E2를 이기지 못하면 simple head를 MVP 모델로 채택한다. 복잡한 모델을 기본값으로 강제하지 않는다.

Fusion은 Train 예측을 재사용하지 않는다. M1과 M3를 고정한 뒤 Stack split에서 branch logits, alignment confidence, SNR, content-match만으로 gate를 학습한다. Calibration split은 fusion 학습에 사용하지 않는다.

## 8. 학습 설정

### 8.1 Cached-head trainer

- optimizer: AdamW
- learning rate: 3e-4에서 시작
- max phones: 128
- batch size: 16에서 시작, VRAM 통과 시 32
- gradient accumulation: 필요할 때만 2
- max epochs: 30
- early stopping patience: 5
- gradient clipping: 1.0
- class-weight 또는 focal loss는 dev AUPRC로 선택
- final 비교: seed 42, 43, 44

Smoke run은 seed 42 하나만 사용한다. 세 seed 평가는 E2-E5 후보가 두 개 이하로 줄어든 뒤 수행한다.

### 8.2 통계 비교

- 발화가 아니라 화자를 재표집하는 cluster bootstrap 5,000회로 차이의 95% CI를 계산한다.
- 사전 지정 비교는 `E2-E1`, `E3-E2`, `E4-max(E1,E3)`로 제한한다.
- 세 비교의 p-value에는 Holm 보정을 적용한다.
- 복잡한 모델은 효과 크기 기준과 CI 하한 조건을 모두 만족할 때만 채택한다.
- Dev에서 선택한 뒤 Locked-test는 단 한 번 평가한다.

### 8.3 Loss

```text
L =
  lambda_phone * L_phone_error
  + lambda_type * L_error_type
  + lambda_pron * L_pronunciation
  + lambda_fluency * L_fluency
  + lambda_comp * L_comprehensibility
```

초기 lambda는 각 task gradient 규모가 비슷하도록 normalization하고, 가중치 탐색은 3개 조합 이하로 제한한다.

### 8.4 체크포인트

- trainable head만 저장
- backbone checkpoint 복제 금지
- experiment ID, data hash, split hash, git commit, seed 기록
- best metric과 best calibrated metric을 각각 저장

## 9. 하드웨어 benchmark H0

1,000개 대표 발화를 구성한다. 별도의 짧은 benchmark에서는 2/4/8/12초 입력을 각각 warm-up 2회, 측정 10회 실행하고, 12초는 운영 입력이 아닌 스트레스 테스트로만 사용한다.

- 0-3초: 200
- 3-5초: 200
- 5-8초: 400
- 8초 초과 원본에서 생성한 chunk: 200

기록:

- peak allocated/reserved VRAM
- wall time
- audio seconds / processing seconds
- decoder failure
- NaN/Inf
- cache bytes / audio second

Go:

- OOM 0
- peak allocated <= 4,000MiB
- 실행 중 시스템 free VRAM >= 600MiB
- 실패율 < 1%
- WavLM p95 RTF <= 1.0
- pilot 20,000개 예상 feature 추출 시간 <= 48시간
- 예상 cache <= 20GB

No-Go 대응 순서:

1. max chunk 8초 -> 6초
2. FP32 -> autocast FP16 benchmark
3. output hidden states 비활성화, 마지막 layer만 사용
4. GPU 대신 CPU extraction
5. pilot subset 20,000 -> 10,000

## 10. Metamorphic test

| 변형 | 기대 결과 |
|---|---|
| 앞·뒤 0.5초 silence | phone prediction 거의 불변 |
| gain +/-6dB | phone prediction 거의 불변 |
| 약한 background noise | confidence 감소, 근거 없는 오류 증가 금지 |
| 특정 음절 duration 1.25배 | 해당 duration score만 주로 악화 |
| 전체 F0 scaling | 정규화 contour 점수 변화 제한 |
| phone 구간 마스킹 | 해당 phone confidence 감소 |

변화 허용치는 dev 분포의 95% 구간으로 먼저 정하고 test 전에 고정한다.

## 11. 코칭 실험

### 11.1 생성 방식

MVP 기본은 template + advice catalog다. LLM은 선택 사항이며 문장을 자연스럽게 표현하는 역할만 한다.

입력:

- top issue 1개
- 위치
- target/observed phone
- severity
- confidence
- evidence IDs
- learner level

출력:

- 잘된 점
- 핵심 오류 하나
- 검수된 조음 지시 하나
- minimal pair 또는 다음 문장

### 11.2 평가 표본

- oracle 50건
- model-correct 50건
- model-error 또는 low-confidence 50건
- 총 150건
- 전문가 2인 검토

통과 기준:

- evidence 없는 주장 < 2%
- 오류 위치·목표 음소 일치 >= 90%
- confidence threshold 이하 재녹음 요청 >= 95%
- 조음 지시 적합 >= 90%

### 11.3 후속 feasibility pilot

기술 게이트를 통과한 뒤 최소 30명, 1인당 12문장의 무작위 교차 실험을 진행한다.

- C0: 점수 + 일반 연습 안내
- C1: calibrated top-1 오류 코칭 + minimal pair
- 두 조건은 동일한 인간 참조음 또는 동일한 고정 TTS를 사용
- 최초 발화와 세 번째 재발화를 비교
- 평가자는 조건을 모른 채 목표 오류 해결 여부와 이해가능성을 평가
- participant와 prompt를 random effect로 둔 mixed model 사용

이 단계는 확증적 RCT가 아니라 본 연구 진입 여부를 판단하는 feasibility pilot이다. C1-C0 표준화 효과가 0.3 이상인 방향성을 보이지 않으면 다중 TTS나 GRPO로 확장하지 않고 코칭 정책을 먼저 수정한다.

## 12. 참조음/TTS 실험

GTX 1660에서는 TTS를 scorer와 동시에 실행하지 않는다.

1. 선정한 20개 연습 문장의 참조음을 별도 프로세스에서 사전 생성
2. 문장당 후보 4개
3. 가능한 경우 300M급 또는 0.5B급 사전학습 TTS를 batch 1로 사용
4. reference text를 직접 제공해 추가 ASR 모델 로딩 방지
5. 생성 후 TTS 프로세스 종료
6. scorer로 hard constraint와 다양성 평가
7. 최종 2-3개 reference만 앱에 탑재

TTS inference가 4.0GB VRAM cap을 넘으면 CPU 또는 외부 GPU에서 한 번만 생성하고, 로컬 MVP는 생성된 WAV만 사용한다. TTS 모델 학습은 실험 범위에 포함하지 않는다.

TTS hardware gate는 20-30음절 문장, 후보 1개, 10회로 측정한다. GPU process peak 4.0GB 이하, 실행 중 free VRAM 600MiB 이상, p95 RTF 3.0 이하일 때만 제한적 온라인 생성을 허용한다. 하나라도 실패하면 모든 참조음은 offline pre-generation 또는 인간 참조음 bank로 대체한다.

## 13. 전체 평가 게이트

### 정확도

- M1 phone macro F1 >= 0.50
- 엔지니어링 MVP phone macro F1 >= 0.60
- 자동 교정 활성화 phone macro F1 >= 0.75
- 최종 phone AUPRC가 class prior 대비 명확히 높음
- utterance QWK >= 0.55
- fusion이 explicit 또는 SSL 단독 대비 primary metric +0.02 이상
- 발행된 교정 precision >= 0.90
- 자동 교정 coverage >= 0.70

### Calibration

- ECE <= 0.10
- 80% coverage에서 full coverage 대비 error rate 20% 이상 감소
- low-confidence severe error의 자동 코칭 금지

### 공정성

- 중국어/일본어 worst-group phone F1 차이 <= 0.10
- F/M worst-group phone F1 차이 <= 0.10
- 기준 초과 시 전체 배포 금지, subgroup threshold 또는 sampling 재설계

### 실행성

- feature extraction OOM 0
- cached-head 학습 peak <= 4,000MiB
- 5초 발화 전체 분석 p95 <= 10초
- 단일 발화 진단 결과 생성 가능
- 동일 seed와 split에서 metric 재현 오차 <= 0.005

## 14. 실패 기준과 중단 규칙

- G0 데이터 게이트 실패: 모델 학습 금지
- E1 phone F1 < 0.50: taxonomy·pairing·label quality 재검토
- E2가 E1보다 나쁘고 세 번의 제한된 수정 후에도 개선 없음: WavLM branch 보류
- E3 개선 < 0.02: HiPAMA-lite 제외
- ECE > 0.15: 자동 코칭 금지, 점수 표시만 허용
- unsupported coaching claim >= 2%: LLM 제거, template-only 유지
- GPU OOM이 동일 구성에서 두 번 발생: 즉시 더 작은 chunk/config로 변경
- pilot feature extraction 예상 48시간 초과: subset 축소

## 15. 6주 실행 일정

| 주차 | 작업 | 산출물 |
|---:|---|---|
| 1 | 발음/Validation WAV 해제, pairing audit, split | QC report, manifest |
| 2 | explicit features, oracle loop, E1 | baseline report |
| 3 | H0, WavLM cache, E2 | hardware profile, cache |
| 4 | E3-E5, calibration | scorer checkpoint, metrics |
| 5 | metamorphic test, coaching 150건 | safety report |
| 6 | 참조음 20문장, end-to-end demo | MVP experiment report |

## 16. MVP 최종 산출물

- 고정 speaker-disjoint manifest와 hash
- 데이터 QC·분포 보고서
- GTX 1660 hardware benchmark
- explicit baseline
- frozen WavLM simple head
- GTX-HiPAMA-lite 또는 탈락 근거
- confidence-aware fusion 및 calibration
- metamorphic test 결과
- 근거 제한 coaching 평가
- 20개 문장 다중 참조 bank
- 한 발화 end-to-end 재현 스크립트
- 실험별 config, seed, metric, 실패 사례

## 17. MVP 이후 승격 조건

다음 조건을 모두 충족할 때만 더 큰 GPU 실험을 요청한다.

- final phone macro F1 >= 0.60
- utterance QWK >= 0.55
- ECE <= 0.10
- metamorphic pass >= 95%
- coaching unsupported claim < 2%
- frozen WavLM이 explicit baseline보다 일관되게 우수

승격 실험은 WavLM-large 전체 학습부터 시작하지 않는다. 먼저 동일한 고정 manifest에서 WavLM-large frozen feature가 base보다 유의미하게 좋은지 확인하고, 그 다음 adapter/partial fine-tuning의 비용 대비 효과를 평가한다.

## 18. 주장 범위

AI Hub 라벨만으로 얻은 성능은 "기존 라벨을 재현하는 능력"을 측정한다. 실제 한국어 학습자의 교육적 개선이나 새로운 녹음 환경에서의 진단 타당성을 바로 증명하지 않는다. 전문가 800건 독립 정답셋과 feasibility pilot 이전에는 다음 표현을 사용하지 않는다.

- 전문가 수준 진단
- 실제 발음 향상 입증
- 모든 한국어 학습자에게 일반화
- TTS가 학습 효과를 향상

MVP 보고서는 자동 지표, calibration, failure slice, 하드웨어 제약, 미검증 가설을 분리해 기록한다.

## 19. 공식 구현 참고

- WavLM Base Plus: https://huggingface.co/microsoft/wavlm-base-plus
- CosyVoice: https://github.com/QwenAudio/CosyVoice
- F5-TTS: https://github.com/SWivid/F5-TTS
