# G04 정렬 감사 실패 진단 (2026-08-08)

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

- 문서 상태: `engineering_provisional` / 기존 감사 결과의 재분석
- 대상: 240건 1차 표본, `alignment_accuracy = 0.7417`, 기준 0.95, `status=failed`
- 입력: `aihub71490_manual_alignment_audit_failure_analysis.json/.md`,
  `alignment_review_state.json` (전문가가 이미 완료한 240건 판정)
- **이 문서는 새 판정을 만들지 않는다.** 전문가가 이미 내린 판정을 층화해 읽은 것이다.

## 1. 결론 — 표본을 더 검수해도 0.95에 도달하지 않는다

두 수치가 그 이유를 말한다.

```
error_presence = false : 151건 중 실패 9건  → 정확도 0.9404
error_presence = true  :  89건 중 실패 53건 → 정확도 0.4045
────────────────────────────────────────────────────────────
전체                   : 240건 중 실패 62건 → 정확도 0.7417
```

**실패 62건 중 53건(85.5%)이 발음 오류가 있는 발화에 몰려 있다.** 오류 없는 발화에서는
0.9404로 기준 0.95에 근접한다.

## 2. 경계는 틀리지 않았다

`alignment_review_state.json`의 판정별 `maximum_boundary_error_ms`:

| 판정 | 0.0 ms | 50.0 ms |
|---|---:|---:|
| correct (178) | 178 | 0 |
| **incorrect (62)** | **61** | 1 |

**실패 62건 중 61건이 경계 오차 0 ms다.** 즉 정렬기는 시간 구간을 잘못 자르지 않았다.
틀린 것은 *무엇이 발음되었는가*이며, operation별 실패율이 이를 확인한다.

| operation | 실패율 |
|---|---:|
| insertion | 1.0000 |
| deletion | 0.8571 |
| substitution | 0.7281 |
| match | 0.5118 |

기존 분석도 같은 결론을 적었다 — "실패 62건 중 자동 대응이 전부 `match`였던 항목
(경계/시간 판단 오류로 추정): **3건**".

## 3. 무엇이 실제로 측정되고 있는가

검수자 메모가 결정적이다.

> #215: target이 "딱하나만"이라고 되어있는데 실제로 발음이 "따카나만"이 맞지 않은지
> #223: 사람이 → "사알미"라고 인식되어있음. **근데 음성 자체는 맞지 않나**
> (정답 처리된 항목의 메모) target_label이 "ᄃ"이지만 '따'로 들리므로
> realized_label이 "ᄄ"인 것은 문제되지 않음

검수자들은 **원본 코퍼스의 target 라벨이 실제 발화와 어긋나는 경우**를 incorrect로
표시하고 있다. 그리고 그런 어긋남은 정의상 **발음 오류가 있는 발화에서 발생**한다.
`error_presence=true`의 정확도가 0.40인 것이 이것이다.

따라서 현재 0.7417은 두 가지가 섞인 값이다.

| 섞인 것 | 규모 |
|---|---|
| 정렬기가 구간을 잘못 잡음 | 3건 (전부 `match`인 실패) |
| target 라벨과 실제 발화의 불일치 | 나머지 대부분 |

후자는 **AI Hub 71490 코퍼스의 라벨 속성**이지 이 프로젝트 정렬기의 결함이 아니다.
재검수로 바뀌지 않고, 정렬기를 개선해도 바뀌지 않는다.

## 4. 이것이 왜 중요한가

G04는 S108 locked one-shot의 선행 gate다.
현재 구성대로면 **정렬기를 아무리 고쳐도 gate가 열리지 않는다.** 학습자 발화 데이터의
상당수가 정의상 발음 오류를 담고 있고, 그 구간에서 target 라벨과 실제 발화는 어긋나기
때문이다. 이 프로젝트의 대상 코호트가 L2 학습자인 이상 이것은 제거되지 않는 성질이다.

즉 gate가 잡아낸 것은 정렬기의 품질이 아니라 **gate 정의와 데이터 성격의 불일치**일
가능성이 높다.

## 5. 결정이 필요한 지점 (연구책임자)

[`LOCAL_ALIGNMENT_REVIEW_UI_KO.md`](../LOCAL_ALIGNMENT_REVIEW_UI_KO.md)는 이미
"어느 지표를 G04 최종 gate로 쓸지는 연구책임자가 결정한다"고 적어두었다. 위 분석은
그 결정에 필요한 재료이지 결정 자체가 아니다.

| 선택지 | 내용 | 비용 |
|---|---|---|
| A. gate를 `error_presence=false` 층에 한정 | 정렬기 품질을 측정하려는 원래 의도에 맞음. 현재 0.9404 | 오류 발화 정렬 품질은 별도 지표 필요 |
| B. phone-level 지표로 전환 | 발화 단위 이진 판정이 너무 거칠다. 30건 2차 표본이 이 목적으로 이미 생성됨 | 재검수 필요 |
| C. 판정 기준을 "정렬기 오류"와 "라벨 불일치"로 분리 | 가장 정확하나 240건 재판정 | 전문가 시간 |
| D. 0.95 기준 자체를 재검토 | 사전등록 변경 | 계약 개정 |

**A와 B는 배타적이지 않다.** 2차 표본 30건(5초 이상)이 phone-level 검수용으로 이미
만들어져 있으므로, B를 진행하면서 A의 층화를 함께 보고하는 것이 자연스럽다.

## 6. 지금 확인된 한계

- `phone_level_accuracy`는 `null`이다. 240건 전부 legacy 형식이라
  interval 단위 정보가 없다(`legacy_reviews_missing_interval_count: 240`).
  phone-level 판정은 2차 표본에서만 가능하다.
- 층화 표본 크기가 작다. `Zhuang; Chuang` 8건(실패율 0.50), `gender=O` 28건(0.50)은
  신뢰구간이 넓어 그 자체로 결론이 되지 않는다.
- 이 재분석은 전문가 판정을 재해석한 것이지 검증한 것이 아니다. 검수자가 "라벨
  불일치"를 incorrect로 표시한 것이 프로토콜 위반인지 아닌지는 프로토콜 원문과
  대조해야 하며, 그 대조는 하지 않았다.

## 7. 2026-08-09 승인 결정: T43 + I30

연구책임자는 B(phone-level 지표)와 축소된 C(알려진 문제 후보의 원인 분리)를
결합한 [G04 amendment](../constraints/G04_TARGETED_PHONE_AUDIT_AMENDMENT_KO.md)를 승인했다.

- G04-H240의 `178/240`은 역사적 진단으로 보존한다.
- T43은 현재 정렬 drift 또는 로컬 AI non-abstain 불일치인 항목만 원인
  adjudication한다.
- I30은 기존 240건과 독립인 장문 30건으로 phone-level 품질을 측정한다.
- 발화 단위 `228/240`과 새 fidelity 240건 전수 재청취 요구는 폐기한다.

식별자 없는 실측 집합은 다음과 같다.

| 항목 | 건수 |
|---|---:|
| 현재 정렬 재계산 drift | 19 |
| AI non-abstain 주석 불일치 | 26 |
| 두 집합의 교집합 | 2 |
| T43 합집합 | 43 |
| I30 독립 장문 표본 | 30 |

로컬 Qwen2-Audio proxy는 240/240을 처리했지만 `uncertain=161`이므로
reviewer 대체 증거가 아니다. 불일치 후보를 T43에 넣는 screen으로만 쓴다.

현재 실행 상태는 다음과 같다.

- T43 immutable manifest: 43건 동결, SHA-256 `d59db42a…dccf`
- T43 amended review: 0/43
- I30 legacy review: 1/30
- I30 amended review: 0/30
- D07/S108 consumer schema migration: 구현·관련 단위·통합 회귀 49개 통과

따라서 amendment 승인은 G04 통과가 아니다. T43 unresolved 0건, I30
30/30과 phone-level accuracy 0.95 이상, privacy-safe report·reviewer
attestation·consumer migration 전에는 G04와 D07을 계속 fail-closed한다.
AI가 legacy missing을 추정해 채우는 것도 계속 금지한다.
