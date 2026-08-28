# 다음 단계 실행 계획

최종 갱신: 2026-08-22 (Asia/Seoul)

## 현재 상태

### 완료

* AIHub 71490 CTC 데이터 준비
* speaker-disjoint train/dev/test 생성
* train 217,176 / dev 34,424 / locked test 65,296
* 43-token processor/vocabulary 구성
* XLS-R 300M Stage-1 학습
* XLS-R Stage-2 추가 fine-tuning
* Stage-1 / Stage-2 전체 dev PER 평가
* Stage-1 / Stage-2 동일 deterministic dev500 pronunciation-error 비교
* Error Detector backbone을 Stage-1으로 선택
* 기존 hard mismatch full-dev baseline 산출
* 전체 train/dev soft feature extraction
* Soft Linear detector full-dev 평가
* Soft MLP detector full-dev 평가
* XLS-R hidden layer 6/12/18/24 screening
* layer 18 / 24를 full extraction 후보로 결정

### 현재 진행 중

```text
XLS-R Stage-1
→ hidden layer 18 + 24
→ train 217,176 + dev 34,424
→ full hidden representation extraction
```

### Locked test

```text
65,296 utterances
현재까지 미사용
```

architecture와 operating threshold가 확정되기 전까지 열지 않는다.

---

## 1. Layer 18 + 24 full hidden extraction 완료

현재 실행 중인 extraction이 끝날 때까지 기존 프로세스를 중복 실행하지 않는다.

완료 후 반드시 확인:

* train shard 수
* dev shard 수
* 누락 shard
* 임시 `.tmp` shard
* NaN / Inf
* hidden tensor shape
* label row count
* speaker index row count
* zero-target utterance
* 최신 run traceback 여부
* completion marker
* locked-test 미접근

---

## 2. Full detector 비교

같은 full train과 full dev에서 최소 다음 세 모델을 동일 조건으로 비교한다.

```text
A. Soft + Expected + Layer 18
B. Soft + Expected + Layer 24
C. Soft + Expected + Layer 18 + Layer 24
```

필요한 경우 기존 baseline도 같이 보고한다.

```text
Hard mismatch
Soft Linear
Soft MLP
```

threshold는 dev에서 선택하지 않는다.

train 내부 speaker-disjoint calibration subset에서 architecture checkpoint와 threshold를 결정한다.

---

## 3. 필수 평가 지표

각 detector에서 다음을 보고한다.

```text
Precision
Recall
Error-F1
Correct-F1
Macro-F1
Average Precision
Gold error rate
Predicted error rate
TN / FP / FN / TP
```

추가 operating point:

```text
F1-optimal threshold
Precision >= 0.60에서 Recall
Precision >= 0.70에서 Recall
```

제품 관점에서는 F1 하나만으로 결정하지 않는다.

---

## 4. Final Error Detector 선택

현재 full-dev 기준선:

```text
Hard mismatch

Precision = 0.5573
Recall    = 0.2663
Error-F1  = 0.3604
Macro-F1  = 0.6594
```

```text
Soft Linear

Precision = 0.4617
Recall    = 0.4369
Error-F1  = 0.4490
Macro-F1  = 0.7004
```

```text
Soft MLP

Precision = 0.4705
Recall    = 0.4290
Error-F1  = 0.4488
Macro-F1  = 0.7008
AP        = 0.3745
```

Hidden detector는 최소한 soft detector 대비 실제로 의미 있는 개선이 있어야 최종 후보로 채택한다.

선정 시 고려:

* Error-F1
* AP
* Precision/Recall trade-off
* Precision 60/70% operating point
* inference latency
* 모델 크기
* local 실행 가능성

---

## 5. Architecture freeze

최종 detector를 선택하면 다음을 고정한다.

```text
XLS-R backbone
hidden layer
hidden pooling 방법
soft feature schema
expected-phone conditioning
detector architecture
normalization statistics
operating threshold
phone vocabulary
processor
```

각 항목에 version과 SHA-256을 기록한다.

architecture freeze 이후에는 dev 결과를 보고 구조를 계속 변경하지 않는다.

---

## 6. Locked test one-shot 평가

architecture와 threshold freeze 이후에만 수행한다.

대상:

```text
65,296 utterances
370 speakers
```

한 번 산출할 항목:

```text
Precision
Recall
Error-F1
Correct-F1
Macro-F1
Average Precision
TN / FP / FN / TP
error rate
```

locked test 결과를 본 뒤 threshold나 architecture를 다시 조정하면 해당 test는 더 이상 locked test가 아니다.

---

## 7. 최종 detector bundle 생성

DGX에서 최종적으로 로컬로 가져올 자산을 versioned bundle로 만든다.

예:

```text
xlsr_stage1_best_model/
error_detector.pt
detector_config.json
thresholds.json
normalization.json
feature_schema.json
phone_vocab.json
evaluation_dev.json
evaluation_test.json
manifest.json
```

각 파일에 SHA-256을 기록한다.

---

## 8. 로컬 파이프라인 연결

최종 구조:

```text
WAV + Script
      │
      ├── g2pK
      ├── MFA / Prosody
      │
      └── XLS-R Stage-1
               │
               ▼
          Error Detector
               │
               ▼
         Pronunciation Evidence
               │
       ┌───────┴────────┐
       │                │
   Prosody         Pronunciation
       │                │
       └───────┬────────┘
               ▼
      Deterministic Policy
               │
               ▼
             Qwen3
```

---

## 9. Integration verification

로컬 통합 후 다음을 검증한다.

* 같은 WAV에 대한 DGX/Local inference parity
* phone sequence parity
* Error Detector feature parity
* Error probability parity
* threshold 판정 parity
* prosody regression
* 기존 legacy metrics regression
* AIHub 71557 metrics regression
* result JSON schema
* API latency
* fallback/error handling
* Qwen3가 deterministic 결과를 변경하지 않는지 확인

---

## 10. Audio MVP 완료 조건

다음이 모두 충족되면 audio v1을 완료로 간주한다.

* pronunciation detector freeze
* locked test one-shot 완료
* prosody branch 연결
* AIHub 71557 reference 연결
* deterministic policy 연결
* Qwen3 연결
* local inference 성공
* end-to-end regression test 성공
* 모델/reference/config version 고정

그 이후에만 multimodal lip branch 개발을 시작한다.

---

## 지금 하지 않을 작업

* Stage-2를 다시 처음부터 학습
* AIHub 71557 reference 재생성
* legacy 327 reference 수정
* locked test 조기 접근
* test 결과를 이용한 threshold tuning
* XLS-R 전체 fine-tuning
* multimodal/lip branch 구현
* Qwen3에 acoustic decision 위임
