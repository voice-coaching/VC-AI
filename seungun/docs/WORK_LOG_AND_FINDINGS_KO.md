# XLS-R / Pronunciation Error Detector 작업 기록

최종 갱신: 2026-08-22 (Asia/Seoul)

> 2026-08-02까지의 데이터 감사, TextGrid target 분석, manifest 준비, processor 구축, 수동 alignment audit 과정은 `archive/WORK_LOG_AND_FINDINGS_KO_2026-08-02.md`를 참고한다.

---

## 1. 데이터와 split

최종 CTC split:

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
```

train/dev/test speaker overlap은 모두 0이다.

Locked test는 현재까지 사용하지 않았다.

---

## 2. Manual alignment audit 상태

240건 독립 수동 감사 결과:

```text
sample_count       = 240
correct_count      = 178
alignment_accuracy = 0.7417
status             = failed
```

기존 0.95 gate는 통과하지 못했다.

따라서 이후 수행한 XLS-R 학습은 "manual audit gate를 통과한 production training"이라고 기록하면 안 된다.

Stage-1/Stage-2는 내부 연구·진단 목적의 명시적 internal training 경로에서 수행된 실험이다.

---

## 3. XLS-R Stage-1

모델:

```text
facebook/wav2vec2-xls-r-300m
```

Stage-1:

```text
epochs = 3
learning rate = 5e-5
feature encoder frozen
BF16
gradient checkpointing
```

전체 dev 결과:

```text
utterances        = 34,424
reference phones  = 363,709

PER               = 0.0644939
substitution rate = 0.0414672
deletion rate     = 0.0119134
insertion rate    = 0.0111133
```

Stage-1 best model은 pronunciation-error baseline 평가에 사용한다.

---

## 4. XLS-R Stage-2

Stage-1 weight에서 추가 fine-tuning:

```text
epochs = 2
learning rate = 1e-5
```

전체 dev 결과:

```text
PER               = 0.0620689
substitution rate = 0.0403867
deletion rate     = 0.0110858
insertion rate    = 0.0105964
```

Stage-2는 Stage-1보다 일반 CTC PER이 개선됐다.

그러나 이 사실만으로 Error Detector backbone을 Stage-2로 선택하지 않았다.

---

## 5. Deterministic dev500 pronunciation-error protocol 복원

과거 Stage-1 pronunciation-error 결과를 동일하게 재현하기 위해 deterministic dev500 selection rule을 역검증했다.

확정된 selection:

```text
seed = xlsr-phone-error-f1-dev500-v1

score =
SHA256(
    seed + "\0" + utterance_id
)

score 오름차순
→ 앞 500 utterances
```

이 방법만 과거 gold signature를 정확히 재현했다.

```text
target phones    = 5,300
gold errors      = 437
gold insertions  = 17
```

Stage-1 재평가 결과:

```text
TN = 4769
FP =   94
FN =  300
TP =  137

Precision = 0.593074
Recall    = 0.313501
Error-F1  = 0.410180
Macro-F1  = 0.685255
```

기존 결과와 정확히 일치했다.

---

## 6. Stage-1 vs Stage-2 pronunciation-error 평가

동일 dev500으로 Stage-2를 평가했다.

Stage-2:

```text
TN = 4769
FP =   94
FN =  306
TP =  131

Precision = 0.582222
Recall    = 0.299771
Error-F1  = 0.395770
Macro-F1  = 0.677760
```

결론:

```text
CTC PER:
Stage-2 우수

Pronunciation-error detection:
Stage-1 우수
```

따라서 Error Detector phonetic backbone은 Stage-1으로 결정했다.

---

## 7. Hard mismatch의 문제

Stage-1 CTC의 top-1 predicted phone과 expected phone이 다르면 error로 간주하는 hard detector는 실제 error에 대해 지나치게 보수적이었다.

dev500:

```text
Error Recall = 31.35%
```

즉 실제 오류의 약 69%를 놓쳤다.

가설:

CTC top-1 label만 사용하는 과정에서 probability distribution 안에 있는 uncertainty 정보를 버리고 있다.

---

## 8. Soft detector spike

Stage-1을 freeze하고 다음 soft evidence를 사용했다.

```text
hard error
predicted span 존재 여부
expected-phone probability
top competitor probability
expected margin
predicted-phone probability
entropy
blank probability
span duration
```

train 2,000 utterance 규모 spike:

Hard:

```text
Precision = 0.5931
Recall    = 0.3135
Error-F1  = 0.4102
```

Soft Linear:

```text
Precision = 0.5037
Recall    = 0.4622
Error-F1  = 0.4821
```

결론:

CTC soft evidence에 hard top-1 mismatch가 버리고 있던 pronunciation-error signal이 존재한다.

---

## 9. Full soft feature extraction

전체 train/dev에서 Stage-1 soft feature를 추출했다.

Train:

```text
utterances       = 217,176
phone rows       = 2,301,676
gold error rows  = 143,111
gold error rate  = 0.0621769
```

Dev:

```text
utterances       = 34,424
phone rows       = 366,288
gold error rows  = 30,246
gold error rate  = 0.0825744
```

Train/Dev speaker overlap:

```text
0
```

zero-target train utterance:

```text
2
```

확인된 ID:

```text
00771-M-2-ZH-B-RS055-0171949
01519-M-95-ZH-A-RS032-0463948
```

해당 발화는 pronunciation gold alignment가 insertion-only여서 target-phone supervised row가 0개였다.

이를 임의로 manifest phone으로 대체하지 않고 0-row utterance로 보존했다.

---

## 10. Full-dev Hard baseline

전체 dev 34,424 / 366,288 phone rows:

```text
TN = 329643
FP =   6399
FN =  22191
TP =   8055

Precision = 0.557285
Recall    = 0.266316
Error-F1  = 0.360403
Macro-F1  = 0.659420
```

이 값이 앞으로 full-dev Error Detector의 기본 baseline이다.

dev500 F1 0.4102와 full-dev F1 0.3604를 혼동하지 않는다.

---

## 11. Soft Linear full-dev

train 내부 speaker-disjoint fit/calibration split을 사용했다.

Dev:

```text
Precision = 0.461676
Recall    = 0.436917
Error-F1  = 0.448955
Macro-F1  = 0.700402
```

Hard 대비:

```text
Recall:
0.2663 → 0.4369

Error-F1:
0.3604 → 0.4490
```

실제 오류를 더 많이 탐지하지만 false positive도 증가한다.

---

## 12. Soft MLP full-dev

동일 9개 soft feature를 작은 MLP에 입력했다.

Dev:

```text
Precision = 0.470469
Recall    = 0.429015
Error-F1  = 0.448787
Macro-F1  = 0.700774
Average Precision = 0.374545
```

Linear와 비교:

```text
Linear F1 = 0.448955
MLP F1    = 0.448787
```

classifier capacity를 Linear → MLP로 늘려도 Error-F1은 개선되지 않았다.

결론:

> 현재 병목은 classifier capacity보다 입력 representation의 정보량일 가능성이 높다.

Precision 60% operating point:

```text
Precision = 0.603234
Recall    = 0.128281
Error-F1  = 0.211571
```

Precision 70%는 calibration에서 유효 operating point를 확보하지 못했다.

따라서 단순 soft feature만으로 제품 수준 precision/recall trade-off를 확보하기 어렵다고 판단했다.

---

## 13. XLS-R hidden-state smoke

Stage-1에서 `output_hidden_states=True`를 사용해 hidden representation을 확인했다.

모델 구조:

```text
Transformer layers = 24
hidden size        = 1024
vocab size         = 43
blank id           = 0
```

hidden tuple:

```text
25 tensors
= encoder input representation + 24 transformer layers
```

후보 layer:

```text
6
12
18
24
```

모든 layer가 logits와 동일한 CTC time dimension을 갖는 것을 확인했다.

---

## 14. Hidden representation screening

Screen 규모:

```text
Train = 20,000 utterances
Dev   = 5,000 utterances
```

비교 모델:

```text
soft_only
soft_expected
soft + expected + layer 6
soft + expected + layer 12
soft + expected + layer 18
soft + expected + layer 24
```

결과:

| Model           | Precision | Recall |   Error-F1 |         AP | P60 Recall | P70 Recall |
| --------------- | --------: | -----: | ---------: | ---------: | ---------: | ---------: |
| soft_only       |    0.4283 | 0.4675 |     0.4470 |     0.3637 |     0.1683 |     0.0002 |
| soft_expected   |    0.4374 | 0.4609 |     0.4488 |     0.3756 |     0.2050 |     0.0546 |
| hidden layer 6  |    0.4637 | 0.4333 |     0.4480 |     0.4013 |     0.2285 |     0.1299 |
| hidden layer 12 |    0.4481 | 0.4665 |     0.4571 |     0.4165 |     0.2345 |     0.0825 |
| hidden layer 18 |    0.4430 | 0.4960 | **0.4680** | **0.4273** |     0.2243 | **0.1506** |
| hidden layer 24 |    0.4568 | 0.4633 |     0.4600 |     0.4268 | **0.2501** |     0.1455 |

F1 ranking:

```text
1. Layer 18
2. Layer 24
3. Layer 12
4. Soft + Expected
5. Layer 6
6. Soft Only
```

해석:

* expected-phone conditioning 자체는 작은 개선을 보였다.
* layer 6은 ranking AP는 좋아졌지만 F1 개선은 거의 없었다.
* 중후반 layer 12/18/24에서 유의미한 개선 신호가 나타났다.
* layer 18이 Error-F1/AP 기준 1위였다.
* layer 24는 layer 18과 AP가 매우 가깝고 Precision 60% operating point의 Recall이 가장 높았다.

따라서 full extraction 후보는 layer 18과 24 두 개로 결정했다.

---

## 15. 현재 진행 중

현재 실행:

```text
XLS-R Stage-1 frozen
        ↓
output hidden states
        ↓
Layer 18 + Layer 24
        ↓
Train 217,176
Dev    34,424
        ↓
phone-span mean pooling
        ↓
full hidden feature 저장
```

한 XLS-R forward에서 layer 18과 24를 동시에 저장한다.

이 작업이 끝난 후 다음 detector를 비교할 예정이다.

```text
Layer 18
Layer 24
Layer 18 + Layer 24
```

---

## 16. 현재까지의 핵심 연구 결과

### Finding 1

낮은 PER이 좋은 pronunciation-error detection을 보장하지 않는다.

Stage-2는 Stage-1보다 PER이 낮지만 pronunciation-error F1은 더 낮았다.

### Finding 2

hard top-1 mismatch는 error recall이 매우 낮다.

CTC probability distribution을 버리는 것이 주요 병목 중 하나다.

### Finding 3

soft evidence는 실제로 유효하다.

전체 dev에서 Recall과 Error-F1을 크게 개선했다.

### Finding 4

같은 9개 soft feature에서 Linear → MLP로 classifier capacity를 늘려도 F1 개선은 거의 없었다.

따라서 representation 자체의 한계가 존재한다.

### Finding 5

XLS-R hidden representation에는 soft CTC summary보다 추가적인 pronunciation-error 정보가 존재하는 신호가 있다.

특히 layer 18과 layer 24가 유망하다.

### Finding 6

현재 결과는 dev 기반 architecture selection 단계다.

아직 locked test 성능이 아니다.

---

## 17. 절대 잘못 해석하면 안 되는 수치

```text
PER 6.45%
```

를

```text
accuracy 93.55%
```

라고 부르지 않는다.

```text
Macro-F1 0.70
```

을

```text
오발음을 70% 탐지
```

라고 부르지 않는다.

pronunciation-error detection을 설명할 때는 최소한 다음을 별도로 보고한다.

```text
Precision
Recall
Error-F1
Macro-F1
```

---

## 18. Locked test 상태

```text
Locked test utterances = 65,296
Locked test speakers   = 370

현재 접근 여부 = NO
```

최종 detector architecture와 threshold가 freeze되기 전까지 사용하지 않는다.

---

## 19. 현재 결정 사항

```text
Pronunciation backbone:
XLS-R Stage-1

Soft evidence:
유지

Expected-phone conditioning:
유지

Hidden full extraction:
Layer 18 + Layer 24

Final detector:
미확정

Locked test:
미사용
```

다음 결정은 full train/dev 기준의 layer 18, layer 24, layer 18+24 비교 후 수행한다.
