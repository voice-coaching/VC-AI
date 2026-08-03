# 한국어 AI 보이스 코칭 폐쇄루프 TTS 구현 및 실현 계획

## 1. 문서 목적

이 문서는 `한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처`의 설계를 실제 연구용 PoC로 구현하기 위한 실행 계획이다. 논문의 Phase 0-7을 그대로 나열하지 않고, 현재 저장소와 확보된 AI Hub 71490 데이터, 고성능 GPU 실행 환경을 반영해 작업 순서와 완료 기준을 재구성한다.

> 상태 주의(2026-07-29): 이 문서의 2장은 최초 계획 당시의 스냅샷이라
> ZIP 미해제, 코드 미구현 등 일부 설명이 현재와 다르다. 실제 완료 상태,
> 충돌 해소 결정과 계속 갱신되는 하위 작업은
> [연구 완성 추적기](./RESEARCH_COMPLETION_TRACKER_KO.md)와
> [실행 로그](./EXECUTION_LOG_2026-07-28_KO.md)를 기준으로 한다.

> WP4 S103 이후 S104–S108의 정본 실행 순서와 split·label·artifact·gate는
> [WP4 S103 이후 실행 계약](./WP4_POST_S103_EXECUTION_CONTRACT_KO.md)을
> 따른다. 아래 초기 Phase 2 순서와 충돌할 때만 새 계약을 우선하며,
> 현재 상태·배점은 tracker, 완료 사실은 고정 artifact·registry를
> 계속 정본으로 사용한다.

GPU 모델·VRAM 실측, 모델 용량별 시작점, benchmark와 중단 기준은 [고성능 GPU 실행 계획](./HIGH_PERFORMANCE_GPU_EXECUTION_PLAN_KO.md)에 정의한다.

핵심 결정은 다음과 같다.

1. 첫 번째 제품 목표는 TTS 모델 학습이 아니라 신뢰 가능한 학습자 발음 진단과 재시도 루프다.
2. S1은 명시적 음향 기준선에서 시작해 WavLM 계층 모델로 확장한다.
3. S2와 TTS는 S1의 calibration과 오류 위치 정확도가 검증된 뒤 연결한다.
4. GRPO는 학습 효과와 보상 해킹 방지 기준을 모두 통과한 뒤 수행하는 선택 단계다.
5. 전체 시스템은 단일 종합 점수가 아니라 근거, 신뢰도, 오류 위치를 포함하는 다차원 결과를 계약으로 사용한다.

## 2. 현재 상태

### 2.1 저장소

- 현재 저장소에는 데이터 다운로드·해제 스크립트만 있고 학습·API·평가 코드는 없다.
- Python은 3.14이므로 음성 연구 라이브러리 호환성을 위해 별도 Python 3.10 또는 3.11 환경이 필요하다.
- 데이터 준비, 모델 학습, 평가와 TTS 생성은 고성능 Linux GPU 머신을 기준으로 수행한다.
- GPU 모델과 VRAM을 첫 실행 시 실측한 뒤 batch size, 입력 길이와 미세조정 범위를 확정한다.
- scorer, TTS와 데이터 준비 환경은 각각 분리하고 버전을 고정한다.

### 2.2 AI Hub 71490 데이터

확인된 원천·라벨 구성은 다음과 같다.

| 데이터 | 현재 상태 | 수량/용량 |
|---|---|---:|
| Training 일반 음성 WAV | 해제 완료 | 102,632개, 약 173GB |
| Training 일반 라벨 JSON | 해제 완료 | 102,632개 |
| Training 발음 라벨 JSON | 해제 완료 | 286,560개 |
| Training 발음 TextGrid | 해제 완료 | 286,560개 |
| Validation 일반 라벨 JSON | 해제 완료 | 12,829개 |
| Validation 발음 라벨 JSON | 해제 완료 | 35,820개 |
| Validation 발음 TextGrid | 해제 완료 | 35,820개 |
| Training 발음 음성 ZIP 2개 | 다운로드 완료, 미해제 | 약 65.2GB |
| Validation 일반 음성 ZIP | 다운로드 완료, 미해제 | 약 15.2GB |
| Validation 발음 음성 ZIP | 다운로드 완료, 미해제 | 약 8.1GB |

따라서 라벨은 모두 준비됐지만, 발음 평가 모델 학습에 핵심인 Training/Validation 발음 WAV와 Validation 일반 WAV는 아직 ZIP 상태다.

라벨 기준 전체 발화는 Training 389,192개, Validation 48,649개로 총 437,841개다. 발음 JSON과 TextGrid의 stem은 Training 286,560개, Validation 35,820개에서 각각 완전 일치한다. 현재 해제된 Training 일반 WAV 102,632개도 일반 라벨 JSON과 stem 기준 1:1로 일치한다.

공식 Training과 Validation은 신규 화자 평가 split으로 사용할 수 없다. 실측상 Training 고유 화자는 1,832명, Validation 고유 화자는 1,816명이며 Validation 화자 1,816명이 모두 Training에도 존재한다. 공식 Validation은 데이터 출처 표시는 유지하되, 모델 평가용 split은 `UserID` 그룹 기반으로 새로 만들어야 한다.

대표 WAV는 mono PCM16 48kHz지만 일부 RIFF 헤더의 byte rate가 실제 PCM 길이·JSON 녹음 시간과 일치하지 않는다. byte rate 필드를 직접 신뢰하지 말고 표준 decoder로 읽은 실제 sample 수를 기준으로 길이를 계산하고, 파생본은 정상 WAV로 재인코딩해야 한다.

대표 라벨 구조는 다음과 같다.

- 일반 라벨 JSON
  - `UserID`
  - 국적, 모국어, 숙련도, 성별 등 `SpeakerMetadata`
  - prompt, 실제 발화 전사, 녹음 길이 등 `RecordingMetadata`
  - `DeliveryEval`, `LanguageUseEval`, `ContentEval`
- 발음 라벨 JSON
  - 일반 메타데이터
  - 단어별 시작·종료 시각
  - 음소별 시작·종료 시각
  - 오류 태그와 오류 구간
  - `PronunProfEval`, `FluencyEval`, `ComprehendEval`
- TextGrid
  - words, phones, error tags의 구간 tier

원본 JSON은 UTF-8이며, Windows 기본 인코딩으로 읽으면 한글이 깨질 수 있다. 모든 전처리 코드는 `encoding="utf-8"`을 명시해야 한다.

발음 데이터의 JSON phone tier는 목표 발음을, TextGrid phone tier는 실제 실현 발음을 담는 사례가 확인됐다. 시간 구간 overlap으로 두 tier를 결합하면 `target_phone -> realized_phone` 오류쌍을 만들 수 있다. JSON과 TextGrid의 공백 interval 수가 다를 수 있으므로 배열 인덱스로 결합하지 않고 시간 overlap과 허용 오차를 사용한다.

분포상 여성 발화가 약 86%로 크게 많고, 중국어·일본어 외 소수 언어 코드 이상치도 존재한다. 전체 평균뿐 아니라 성별, L1, 숙련도별 slice를 별도로 평가해야 한다.

## 3. PoC 범위와 비범위

### 3.1 1차 PoC 범위

사용자가 목표 문장을 보고 발화를 녹음하면 시스템이 다음 결과를 반환한다.

- 문장 내용 일치 여부
- 음소별 오류 위치와 유형
- 말속도, 휴지, 음절·단어 duration
- F0 범위와 억양 형태
- energy와 loudness
- 발음 정확도, 유창성, 이해가능성 점수
- 각 판단의 confidence와 evidence
- 가장 중요한 오류 1-3개에 대한 근거 제한 코칭
- 다음 연습 문장과 재녹음 결과 비교

### 3.2 1차 PoC 비범위

- 실시간 스트리밍 최적화
- 모바일 온디바이스 추론
- StyleTTS 2 적대학습
- F5R-TTS/GRPO 훈련
- 상업 배포
- 6개 조건 전체 RCT

이 항목들은 오프라인 채점 성능과 소규모 학습 효과가 확인된 뒤 확장한다.

## 4. 목표 시스템 구조

```text
AI Hub / 전문가 데이터
  -> 정규화 manifest
  -> 오디오 QC 및 feature cache
  -> S1 Branch A: WavLM + hierarchical heads
  -> S1 Branch B: CTC/GOP + duration/pause + F0 + energy + rhythm
  -> confidence-aware fusion
  -> S2 독립 감사
  -> DiagnosticProfile JSON
  -> 규칙 기반 우선순위
  -> 근거 제한 LLM 코칭
  -> 학습자 재녹음 및 learner history

인간/검증된 TTS 후보
  -> hard constraint
  -> S1/S2 평가
  -> Pareto + diversity 선택
  -> 다중 참조 bank
```

Loop C는 모델 학습과 별도의 배치 작업으로 운영한다.

```text
저신뢰·불일치 표본
  -> 전문가 이중 평가
  -> ICC/QWK 검사
  -> calibration/threshold 갱신
  -> metamorphic test
  -> 모델 승격 또는 롤백
```

## 5. 코드베이스 구조

권장 구조는 다음과 같다.

```text
configs/
  data/
  features/
  models/
  experiments/
src/voice_coach/
  data/
    aihub71490.py
    manifest.py
    splits.py
    audio_qc.py
  phonology/
    korean_g2p.py
    rules.py
    taxonomy.py
  features/
    alignment.py
    gop.py
    prosody.py
    rhythm.py
    loudness.py
  models/
    s1/
    s2/
    fusion/
  coaching/
    schema.py
    prioritizer.py
    advice_catalog.py
    prompt_builder.py
  references/
    generator.py
    hard_constraints.py
    pareto.py
    diversity.py
  api/
    app.py
    routes/
  evaluation/
    metrics.py
    calibration.py
    metamorphic.py
scripts/
  build_manifest.py
  validate_dataset.py
  extract_features.py
  train_s1.py
  evaluate_s1.py
  train_s2.py
  build_reference_bank.py
tests/
  unit/
  integration/
  metamorphic/
artifacts/
  manifests/
  features/
  reports/
```

원천 데이터는 계속 `data/restricted/` 아래에 두고 Git에 커밋하지 않는다. 모델 체크포인트, feature cache, 평가 결과도 원천 데이터와 분리한다.

## 6. 통합 데이터 계약

발화 단위 manifest는 Parquet을 기본 형식으로 사용한다.

| 필드 | 설명 |
|---|---|
| `utterance_id` | 파일 stem 기반 고유 ID |
| `audio_path` | 저장소 루트 기준 상대 경로 |
| `label_path` | 일반 또는 발음 JSON 경로 |
| `textgrid_path` | 발음 정렬 정보 경로 |
| `split` | train, validation, test |
| `speaker_id` | `UserID` |
| `l1` | Chinese, Japanese 등 |
| `proficiency` | 학습자 숙련도 |
| `task_type` | speech, pronunciation |
| `prompt_text` | 목표 문장 |
| `observed_text` | 실제 발화 전사 |
| `duration_sec` | 녹음 길이 |
| `utterance_scores` | 기존 평가 점수 |
| `word_intervals` | 단어 정렬 |
| `phone_intervals` | 음소 정렬 |
| `error_intervals` | 오류 유형과 구간 |
| `qc_flags` | 손상, clipping, silence, mismatch 등 |
| `license_id` | 데이터 이용 조건 식별자 |

분할 원칙은 다음과 같다.

- `speaker_id`가 서로 다른 split에 절대 중복되지 않게 한다.
- 가능하면 동일 prompt도 test에 과도하게 중복되지 않게 한다.
- 모국어, 숙련도, 성별, task type의 분포를 층화한다.
- AI Hub 원래 Validation은 출처를 보존하되 화자가 Training과 전부 겹치므로 그대로 최종 평가에 쓰지 않는다.
- 새 speaker-disjoint test와 별도 prompt-disjoint challenge set을 만든다.
- S2용 독립 holdout은 S1 train/validation과 전문가 라벨러까지 분리한다.

## 7. 단계별 구현 계획

### Phase 0. 데이터 준비와 평가 계약 - 2주

작업:

1. 미해제 발음·Validation 음성 ZIP을 안전하게 해제한다.
2. 모든 ZIP과 출력 파일 수를 비교해 완료 표식을 만든다.
3. WAV-JSON-TextGrid를 파일 stem으로 결합한다.
4. JSON 목표 phone과 TextGrid 실제 phone을 시간 overlap으로 결합한다.
5. 누락, 중복, 길이 불일치, 잘못된 시간 구간을 검사한다.
6. WAV 헤더와 실제 sample 수를 비교하고 파생 오디오는 정상 포맷으로 재인코딩한다.
7. 오류 태그를 정규 taxonomy로 매핑한다.
8. speaker-disjoint 및 prompt challenge split을 생성한다.
9. manifest와 데이터 카드를 생성한다.
10. coaching 결과 JSON Schema와 모델 출력 계약을 고정한다.

완료 기준:

- WAV-라벨 결합률 99% 이상, 목표 99.5%
- JSON parse 성공률 100%
- 모든 phone/word 구간이 음성 길이 안에 존재
- split 간 화자 중복 0
- duration/time-bound 오류 1% 미만
- 표본 정렬 감사 통과율 95% 이상
- 오류 태그 미매핑 비율 1% 이하
- 데이터 카드에 L1·숙련도·평가 점수 분포 기록

### Phase 1. 해석 가능한 기준선 - 3주

작업:

1. 기존 TextGrid를 기준 정렬로 읽는 parser를 구현한다.
2. 신규 발화용 Korean MFA/CTC 정렬 경로를 별도로 구현한다.
3. 말속도, articulation rate, pause count/ratio를 계산한다.
4. F0 contour, range, slope, boundary movement를 계산한다.
5. RMS energy, loudness, clipping, SNR 근사치를 계산한다.
6. 음절·모음 기반 nPVI와 duration z-score를 계산한다.
7. CTC posterior 또는 acoustic posterior 기반 GOP 기준선을 구축한다.
8. 기존 오류 태그를 사용해 음소 오류 탐지 성능을 측정한다.
9. 독립 전문가 2인 이상이 1,000-2,000개 발화를 재평가해 최종 test의 정답 신뢰도를 측정한다.

완료 기준:

- 100개 수동 표본에서 시간 구간 오차 중앙값 50ms 이하
- silence/gain metamorphic test 통과율 95% 이상
- phone error macro F1 0.60 이상
- utterance-level 기존 점수와 Spearman 0.50 이상
- 한 발화 분석 결과가 완전한 `DiagnosticProfile` JSON으로 저장
- holistic ICC/QWK 0.70 이상
- 오류 taxonomy 평가자 합의 0.65 이상

### Phase 2. S1 WavLM 계층 채점기 - 4주

모델 도입 순서:

1. frozen WavLM-base 또는 base-plus + 단순 utterance head
2. phone/word/utterance multi-task head
3. layer-wise weighted sum
4. HiPAMA식 phone -> word -> utterance attention
5. 명시적 branch와 confidence-aware fusion
6. WavLM-large로 승격 실험

처음부터 WavLM-large 전체를 미세조정하지 않는다. base 계열과 frozen encoder로 데이터·손실·평가 코드를 먼저 검증한 뒤 large로 승격한다.

학습 목표:

- phone error type: multi-label classification
- word/utterance accuracy: ordinal regression
- fluency, pronunciation, comprehensibility: ordinal 또는 bounded regression
- uncertainty: temperature scaling 또는 evidential/confidence head
- calibration loss: validation ECE 기반

완료 기준:

- phone macro F1 0.75 이상, 목표 0.80
- utterance ICC 0.65 이상, 목표 0.70
- word PCC 0.65 이상, 목표 0.70
- ECE 0.10 이하
- L1별 성능 격차가 전체 F1 기준 0.10 이내
- 기준선 대비 세 주요 지표 중 두 개 이상 유의하게 개선

Go/no-go:

- ECE가 0.15를 넘거나 특정 L1에서 F1이 0.60 미만이면 S2/TTS로 넘어가지 않고 데이터·라벨·손실을 수정한다.
- WavLM-large+HiPAMA는 단순 head 대비 utterance ICC 0.03 이상 또는 phone macro F1 3%p 이상 개선할 때만 채택한다.
- large 모델에서 최악 subgroup가 단순 모델보다 0.05 이상 하락하면 단순 모델을 유지한다.

### Phase 3. S2 독립 감사와 Loop C - 2주

S2는 S1과 다음 항목을 공유하지 않는다.

- backbone
- 정렬 방식
- 전문가 holdout
- 목표 함수

권장 초기 구현:

- Whisper 기반 transcript consistency와 alignment-free 음성 표현
- HuBERT 또는 별도 운율 표현 기반 pairwise preference/ordinal head
- 출력은 승인, 불일치, 불확실성의 3상태

Whisper는 내용 일치와 WER hard gate에 우선 사용한다. 세밀한 음소·운율 감사 전체를 Whisper 하나에 맡기지 않는다.

metamorphic test:

- 앞·뒤 silence 추가
- 전체 gain 변경
- 제한된 배경 잡음 추가
- 한 음절 duration 변형
- F0 global scaling
- 특정 음소 교체 합성

완료 기준:

- 심각한 S1 오판 탐지 recall 0.80 이상
- S1 false-high-score 30% 이상 감소
- 정상 표본 referral 비율 15% 이하
- S1-S2 불일치 표본의 전문가 오류 적중률 60% 이상
- metamorphic expectation 통과율 95% 이상
- calibration report와 모델 승격 체크리스트 자동 생성

### Phase 4. 코칭 MVP - 2주

LLM은 음성 자체를 판정하지 않고 구조화된 진단 결과만 설명한다.

처리 순서:

1. 오류 후보 생성
2. `Severity x Confidence x ComprehensibilityImpact x Trainability` 계산
3. 상위 1-3개 선택
4. 허용된 advice catalog에서 조음·운율 지침 선택
5. LLM으로 학습자 수준에 맞게 표현
6. 재녹음 과제와 minimal pair 생성

필수 안전 규칙:

- confidence가 기준보다 낮으면 재녹음을 요청한다.
- evidence 없는 오류는 언급하지 않는다.
- 잘된 점 하나와 핵심 오류 하나를 기본 형식으로 한다.
- 시간 구간과 목표 음소를 함께 표시한다.
- 조음 지시는 검수된 ontology ID만 사용한다.

완료 기준:

- 전문가 200건 검토에서 근거 없는 피드백 2% 이하
- 오류 위치·유형 표현 정확도 90% 이상
- 저신뢰 발화 재녹음 유도 recall 95% 이상
- 동일 입력에 대한 핵심 오류 선택이 재현 가능

### Phase 5. 다중 참조음과 Loop A - 2주

초기에는 모델을 학습하지 않고 inference-only 후보 생성으로 시작한다.

1. 인간 전문가 녹음과 CosyVoice 계열을 참조 후보로 사용한다.
2. 문장당 8-16개 후보를 생성한다.
3. ASR 내용 일치, artifact, 휴지, 목표 음소를 hard constraint로 검사한다.
4. 자연스러움, 운율 적합성, 교육적 명료성, 다양성의 Pareto frontier를 구한다.
5. 화자·속도·억양 cluster에서 3-5개 exemplar를 선택한다.
6. 학습자 발화와 가까운 참조를 aspect별로 선택한다.

단순 `min distance`는 품질이 낮은 outlier 참조 하나가 점수를 과도하게 좋게 만들 수 있다. hard gate를 통과한 reference bank에서 k-nearest median 또는 trimmed soft-min을 기본 비교 함수로 사용하고, 논문의 순수 minimum은 ablation으로 둔다.

현재 공식 CosyVoice 저장소에는 CosyVoice 2 이후 모델과 GRPO 지원도 공개돼 있으므로, 논문의 CosyVoice 2를 재현 기준선으로 유지하되 최신 계열은 별도 후보로 benchmark한다. 모델 변경이 논문의 비교 가능성을 훼손하지 않도록 실험 ID를 분리한다.

완료 기준:

- hard constraint 통과 후보의 transcript error 2% 이하
- 인간 blind 평가에서 단일 무선별 TTS보다 CMOS 우위
- multi-reference가 단일 reference보다 정상 발화 오탐을 20% 이상 감소
- exemplar 다양성이 speaker/speed/pitch 세 축에서 보존

### Phase 6. 파일럿 학습 효과 검증 - 3주 + 유지 평가

처음부터 C0-C5 6개 군 RCT를 수행하지 않는다. 인과효과를 두 단계로 분리한다.

1. C0 대 C3: 구조화 피드백의 추가 효과 검증
2. C3 대 C5: 다중 참조와 이중 루프의 추가 효과 검증

권장 파일럿:

- 파일럿 조건당 최소 15-20명
- 2주 훈련
- 사전, 즉시 사후, 4주 유지 평가
- 평가자는 조건을 모르는 2인 이상
- primary: comprehensibility
- secondary: phone accuracy, fluency, retention, 만족도

본 시험 표본 수는 파일럿 분산과 효과 크기로 power analysis 후 확정한다.

Go/no-go:

- C5가 C3보다 학습 효과를 보이지 않으면 TTS 최적화를 중단하고 코칭·커리큘럼을 개선한다.
- 자동 점수만 좋아지고 blind 전문가 comprehensibility가 개선되지 않으면 Loop A reward를 수정한다.

### Phase 7. StyleTTS/F5R-TTS/GRPO - 선택 4주 이상

진입 조건:

- S1 phone F1 0.80 이상
- S1 utterance ICC 0.70 이상
- S2 심각 오류 recall 0.80 이상
- multi-reference blind 평가 우위 확인
- 파일럿에서 실제 학습 효과 신호 확인

다목적 reward는 가중합 하나로 축약하지 않는다.

1. 내용 정확성 hard gate
2. 핵심 음소 정확성 hard gate
3. artifact hard gate
4. 운율 적합성
5. 자연스러움
6. 교육적 모방 가능성

GRPO 전에는 best-of-N/rejection sampling으로 reward의 방향성을 먼저 검증한다. F5-TTS 코드와 사전학습 가중치의 라이선스가 다를 수 있으므로 실험 산출물에 코드·가중치·데이터 라이선스를 별도 기록한다.

## 8. 일정

기본 일정은 연구자 1명, 음성학 전문가 파트타임 1명, 필요 시 ML 엔지니어 지원을 가정한다.

| 주차 | 목표 | 주요 산출물 |
|---:|---|---|
| 1-2 | Phase 0 | manifest, QC report, split, taxonomy |
| 3-5 | Phase 1 | 명시적 feature baseline, DiagnosticProfile |
| 6-9 | Phase 2 | S1 base/large 비교, calibration report |
| 10-11 | Phase 3 | S2, metamorphic suite, audit report |
| 12-13 | Phase 4 | coaching API, advice catalog |
| 14-15 | Phase 5 | reference bank, Pareto selector |
| 16-18 | Phase 6 | 1차 2군 파일럿 및 분석 |
| 19-22+ | Phase 7 | 조건부 GRPO/적대학습 실험 |

실제 연구의 최단 엔지니어링 MVP는 Phase 0-4까지 13주다. 검증된 S1/S2와 전문가 정답셋 구축에는 보통 추가 4-6개월이 필요하며, TTS 폐쇄루프와 학습 효과 RCT까지 포함한 전체 연구는 6-12개월 이상으로 잡는 것이 안전하다. GRPO는 별도 장기 연구 트랙으로 관리한다.

## 9. 인프라 계획

### 12-24GB GPU 작업

- frozen/base WavLM feature 추출
- Base+ adapter 또는 부분 미세조정
- WavLM Large frozen 비교
- CosyVoice 소규모 후보 생성
- 소규모 S2 실험

### 32-80GB 이상 GPU 작업

- WavLM-large 전체 또는 부분 미세조정
- 큰 batch의 다중 태스크 학습
- 대규모 TTS 후보 생성
- GRPO와 adversarial 실험

권장 운영 방식:

- 데이터 준비와 모델 학습은 고성능 Linux GPU 머신의 분리된 환경에서 수행
- 데이터는 manifest 기반 shard로 전송
- MLflow 또는 W&B로 실험 ID, commit, 데이터 버전, metric을 기록
- 모델 승격은 자동 지표와 전문가 audit를 모두 통과해야 한다

## 10. API 계약

핵심 엔드포인트:

```text
POST /v1/attempts
GET  /v1/attempts/{attempt_id}
GET  /v1/learners/{learner_id}/profile
POST /v1/reference-candidates
GET  /v1/reference-sets/{text_id}
POST /v1/audits
```

핵심 분석 응답:

```json
{
  "attemptId": "uuid",
  "targetText": "오늘은 날씨가 좋습니다.",
  "contentMatch": {
    "score": 0.96,
    "confidence": 0.93
  },
  "aspects": {
    "segmental": 0.72,
    "fluency": 0.68,
    "prosody": 0.61,
    "comprehensibility": 0.76
  },
  "issues": [
    {
      "type": "final_consonant_deletion",
      "startMs": 1320,
      "endMs": 1540,
      "target": "ㄹ",
      "observed": null,
      "severity": 0.78,
      "confidence": 0.87,
      "evidence": ["ctc_competing_phone", "gop_low", "duration_short"]
    }
  ],
  "audit": {
    "status": "approved",
    "s2Confidence": 0.83
  },
  "coaching": {
    "adviceId": "ARTICULATION_RULE_017",
    "nextTextId": "minimal_pair_0042"
  }
}
```

## 11. 평가와 모델 승격

모든 모델 결과는 전체 평균뿐 아니라 다음 slice로 보고한다.

- L1
- 숙련도
- 성별
- 발화 길이
- 녹음 환경
- 오류 유형
- prompt 중복 여부

모델 승격 조건:

1. 사전 정의한 primary metric 통과
2. calibration 통과
3. metamorphic test 통과
4. 기존 모델보다 주요 slice 성능 악화 없음
5. 전문가 audit 통과
6. 재현 가능한 학습 run과 데이터 버전 기록

## 12. 주요 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 파일명은 맞지만 WAV-라벨 의미 불일치 | 잘못된 지도학습 | stem, duration, transcript를 함께 검증 |
| Windows 인코딩으로 한글 손상 | 전사·오류 태그 오염 | UTF-8 강제, round-trip 테스트 |
| 공식 Training/Validation 화자 전원 중복 | 신규 학습자 성능 과대평가 | UserID 그룹 재분할, prompt challenge set |
| WAV 헤더 byte rate 이상 | 길이·feature 오류 | decoder sample 수 기준 검증·재인코딩 |
| 여성·중국어 화자 편중 | subgroup 성능 저하 | 층화 sampling, slice metric, worst-group gate |
| AI Hub 기존 평정의 편향·불균형 | calibration 실패 | 전문가 독립 holdout과 slice 평가 |
| 동일 화자/문장 누수 | 과대평가 | speaker-disjoint split, prompt audit |
| S1과 S2의 실질적 독립성 부족 | 공동 실패 | backbone·정렬·라벨러·split 분리 |
| WavLM-large 과도한 자원 요구 | 일정 지연 | base/frozen 검증 후 단계적 승격 |
| TTS가 채점기 약점 공략 | reward hacking | hard gate, S2, blind human audit |
| `min distance`가 outlier 참조를 선택 | 비정상적으로 높은 점수 | hard gate + k-nearest median/trimmed soft-min |
| UTMOS가 L2 억양을 과도하게 벌점 | 참조·학습자 편향 | artifact gate로 제한, 인간 평가 병행 |
| 자연스러운 TTS가 학습에는 부적절 | 학습 효과 부재 | 교육적 모방 가능성을 별도 평가 |
| LLM의 근거 없는 조언 | 사용자 피해 | ontology allowlist, evidence contract |
| F5 계열 가중치 비상업 조건 | 결과 재사용 제한 | 코드·가중치·데이터 라이선스 분리 기록 |

## 13. 첫 10영업일 실행 백로그

### 1-2일

- 미해제 음성 ZIP 목록과 예상 파일 수 기록
- 발음·Validation 음성 해제
- ZIP 항목 수와 출력 수 비교

### 3-4일

- 일반/발음 JSON schema parser
- TextGrid parser
- UTF-8 및 시간 구간 validation

### 5-6일

- WAV-JSON-TextGrid manifest 생성
- 결합 실패 리포트
- speaker-disjoint split 생성

### 7일

- 데이터 카드
- 오류 taxonomy 정규화표
- label distribution report

### 8-9일

- 속도, pause, duration, F0, energy 기준선
- 100개 샘플 feature cache

### 10일

- `POST /v1/attempts`용 분석 결과 schema
- 샘플 20건 end-to-end 결과
- Phase 1 진입 리뷰

첫 스프린트의 Definition of Done은 "모델이 학습됐다"가 아니라 "재현 가능한 manifest에서 한 발화를 읽고, 정렬·오류 라벨·음향 특징을 하나의 DiagnosticProfile로 생성할 수 있다"이다.

## 14. 최종 성공 기준

기술적 성공:

- phone F1 0.80 이상
- word PCC 0.70 이상
- utterance ICC 0.70 이상
- ECE 0.10 이하
- 심각 오류에 대한 S2 audit recall 0.80 이상

교육적 성공:

- 전문가가 코칭 근거와 오류 위치에 동의
- 재시도에서 목표 오류가 감소
- 파일럿에서 C3 또는 C5가 C0보다 comprehensibility를 개선
- 4주 뒤 유지 효과 확인

연구적 성공:

- TTS 품질 향상과 학습 효과를 별도 결과로 보고
- 모든 ablation, 데이터 split, 모델 버전을 재현 가능하게 기록
- S1/S2 불일치와 실패 사례를 정량적으로 분석
- GRPO 도입 여부가 사전 정의된 go/no-go 기준으로 결정
