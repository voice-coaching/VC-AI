# 한국어 낭독 코칭 파이프라인

최종 갱신: 2026-08-22 (Asia/Seoul)

## 1. 문서 목적

이 문서는 현재 확정된 한국어 낭독 코칭 시스템의 전체 구조와 각 컴포넌트의 책임을 정의한다.

실험별 성능 변화와 개발 과정은 `WORK_LOG_AND_FINDINGS_KO.md`, 앞으로 해야 할 작업은 `NEXT_STEPS_KO.md`에 기록한다.

이 문서에는 현재 시스템 설계의 정본만 기록한다.

---

## 2. 최종 목표

사용자가 주어진 한국어 문장을 낭독하면 음성을 분석하여 다음 두 종류의 정보를 생성한다.

* 발음 오류: 기대 음소를 실제로 적절하게 발음했는가
* 운율 특성: 속도, 쉼, F0, intensity 등이 참조 분포에서 어디에 위치하는가

실제 판단은 deterministic code와 학습된 detector가 수행한다.

Qwen3는 음향 분석이나 오류 판정을 새로 수행하지 않고 이미 결정된 evidence와 directive를 사용자에게 자연스러운 문장으로 표현하는 역할만 담당한다.

---

## 3. 전체 파이프라인

```text
입력
Script Text + User WAV
        │
        ├──────────────────────────────────┐
        │                                  │
        ▼                                  ▼
Text Normalization                   Audio Preprocessing
        │                              16 kHz mono
        ▼                                  │
      g2pK                                 │
        │                                  │
        ▼                                  │
 Expected Phones                           │
        │                                  │
        └────────────────┬─────────────────┘
                         │
                         ▼
                   MFA Alignment
              단어/음소 시간 구간
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       Prosody Branch       Pronunciation Branch
              │                     │
       MFA timing               XLS-R 300M
       Parselmouth                  │
              │                 CTC evidence
              │                     │
       - Speech rate           Error Detector
       - Articulation rate          │
       - Pause                      │
       - F0                         │
       - Intensity                  │
              │                     │
       AIHub 71557                  │
       Reference                    │
              │                     │
              └──────────┬──────────┘
                         ▼
                Deterministic Policy
                         │
                         ▼
                       Qwen3
                         │
                         ▼
                  사용자 최종 피드백
```

---

## 4. 컴포넌트별 책임

### Text Normalization

입력 원고를 후속 g2pK와 alignment에서 사용할 수 있는 형태로 정규화한다.

### g2pK

스크립트로부터 expected phone sequence를 생성한다.

역할:

> "사용자가 어떻게 발음해야 하는가?"

### MFA

스크립트와 음성을 forced alignment하여 단어와 음소의 시간 구간을 얻는다.

주요 용도:

* speech timing
* articulation timing
* pause 분석
* prosody 분석 구간 정의

MFA의 역할과 pronunciation-error detector의 역할을 혼동하지 않는다.

### Prosody Analyzer

현재 분석 대상:

* speech rate
* articulation rate
* pause count
* pause duration
* F0 range
* F0 coverage
* intensity range
* 기타 versioned robust metrics

AIHub 71557 기반 reference distribution과 비교하여 사용자의 위치를 계산한다.

### Prosody Reference

현재 대규모 reference:

* AIHub 71557 prosody reference v1

기존 legacy reference:

* 327개 아나운서/준비생 reference

두 reference는 metric 정의와 analysis window가 다르므로 같은 distribution으로 합치지 않는다.

### XLS-R CTC

현재 pronunciation branch의 phonetic backbone이다.

역할:

> "사용자의 음성이 실제로 어떤 phone처럼 들리는가?"

현재 Error Detector backbone:

```text
XLS-R 300M Stage-1 best_model
```

Stage-2가 전체 dev PER에서는 더 좋았지만 동일 pronunciation-error 평가에서는 Stage-1보다 낮은 성능을 보였으므로 Error Detector용 backbone은 Stage-1을 사용한다.

### Error Detector

expected phone과 XLS-R acoustic evidence를 이용해 다음 질문을 판단한다.

> "expected phone을 사용자가 제대로 발음했는가?"

단순히

```text
expected phone != CTC top-1 phone
```

만으로 최종 오류를 결정하지 않는다.

현재 연구 중인 evidence:

* hard CTC mismatch
* expected-phone probability
* competitor probability
* expected-vs-competitor margin
* entropy
* blank probability
* CTC span duration
* XLS-R hidden representation
* expected-phone conditioning

Error Detector의 파이프라인 내 위치는 확정됐지만 최종 detector architecture와 operating threshold는 아직 freeze되지 않았다.

### Deterministic Policy

Prosody evidence와 pronunciation evidence를 받아 실제로 사용자에게 어떤 피드백을 줄지 결정한다.

판단 우선순위와 threshold는 코드에서 관리한다.

LLM이 이 판단을 덮어쓰면 안 된다.

### Qwen3

Qwen3는 표현 계층이다.

허용:

* 이미 결정된 결과를 자연스럽게 설명
* 여러 evidence를 읽기 쉬운 피드백으로 구성

금지:

* 음성 자체를 보고 새로운 판정 생성
* threshold 변경
* 오류 여부 임의 수정
* 없는 수치 생성
* 정량 결과에 과도한 의미 부여

---

## 5. 현재 pronunciation backbone 결정

### Stage-1

전체 dev:

```text
PER = 0.0644939
```

동일 deterministic dev500 pronunciation-error 평가:

```text
Precision = 0.5931
Recall    = 0.3135
Error-F1  = 0.4102
Macro-F1  = 0.6853
```

### Stage-2

전체 dev:

```text
PER = 0.0620689
```

동일 deterministic dev500 pronunciation-error 평가:

```text
Precision = 0.5822
Recall    = 0.2998
Error-F1  = 0.3958
Macro-F1  = 0.6778
```

### 결정

Stage-2는 일반 phone recognition PER은 개선됐지만 pronunciation-error detection은 악화됐다.

따라서 현재 Error Detector backbone은 Stage-1이다.

---

## 6. Error Detector 현재 상태

현재 확정:

```text
Backbone:
XLS-R Stage-1

Detector 위치:
Pronunciation Branch 내부

Expected-phone conditioning:
사용

Soft acoustic evidence:
사용

최종 detector architecture:
아직 미확정

Locked test:
미사용
```

현재 진행 순서:

```text
Hard mismatch
      ↓
Soft Linear
      ↓
Soft MLP
      ↓
XLS-R hidden representation screening
      ↓
Layer 18 / Layer 24 full extraction
      ↓
Full-dev detector 비교
      ↓
Final detector freeze
```

---

## 7. 반드시 유지할 설계 규칙

1. PER을 pronunciation-error accuracy라고 부르지 않는다.
2. Macro-F1을 error recall이라고 부르지 않는다.
3. CTC recognition과 pronunciation-error detection을 별도 task로 평가한다.
4. Qwen3는 acoustic decision을 하지 않는다.
5. AIHub 71557 reference와 legacy 327 reference를 혼합하지 않는다.
6. pronunciation gold alignment와 CTC training phone source가 항상 동일하다고 가정하지 않는다.
7. train/dev는 speaker-disjoint 상태를 유지한다.
8. architecture와 threshold가 확정되기 전 locked test를 사용하지 않는다.
9. test 결과를 보고 architecture 또는 threshold를 다시 선택하지 않는다.
10. multimodal/lip analysis는 현재 audio v1이 확정된 이후 별도 branch로 추가한다.

---

## 8. 현재 데이터 분할

```text
Train
217,176 utterances
1,263 speakers

Dev
34,424 utterances
197 speakers

Locked Test
65,296 utterances
370 speakers

Train / Dev / Test speaker overlap
0
```

Locked test는 최종 architecture와 threshold를 확정한 후 one-shot 평가에만 사용한다.

---

## 9. 실행 환경 역할 분리

### DGX

주요 역할:

* XLS-R 학습
* 대규모 XLS-R forward
* soft feature extraction
* hidden representation extraction
* Error Detector 학습/실험
* 최종 locked-test one-shot 평가

### Local

최종 목표:

* XLS-R inference
* Error Detector inference
* MFA
* Prosody Analyzer
* reference comparison
* deterministic policy
* Qwen3
* API/backend integration
* 최종 end-to-end regression test

서비스 시 DGX가 필수 dependency가 되어서는 안 된다.

---

## 10. 현재 v1 범위 밖

현재 Audio MVP가 완료될 때까지 다음은 포함하지 않는다.

* lip shape
* mouth movement
* facial expression
* gaze
* posture
* gesture

이 기능들은 audio pronunciation/prosody pipeline 완료 후 별도 multimodal branch로 추가한다.
