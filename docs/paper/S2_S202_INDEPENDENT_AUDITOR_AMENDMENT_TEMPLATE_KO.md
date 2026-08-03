# 논문 개정 템플릿 — S202 별도 표현 pairwise·ordinal 감사기

상태: **TEMPLATE_ONLY / 실제 S202 결과 없음 / 정본 원고에 미승격**

이 파일은 미래 S202 completed commit과 독립 validation receipt가 생긴
뒤 정본 `EXPERIMENTAL_MANUSCRIPT_KO.md`에 반영할 자리만 고정한다. 현재
`PENDING`을 해제하거나 값을 추정하지 않는다.

## Methods 삽입 후보

> [PENDING: S202_METHODS] S202는 S1과 backbone, alignment, training
> labels, feature와 holdout을 분리한 별도 음성 감사기로 구성했다.
> S201 content hard gate는 routing에만 사용했고 Whisper transcript/WER/
> CER/ASR score를 S202 feature 또는 target으로 사용하지 않았다. 별도
> S2 전문가 pairwise preference와 ordinal rating으로 seed 42/43/44를
> 학습했으며, train 내부 선택과 dev 평가는 사전등록 절차를 따랐다.

검증 뒤 채울 provenance:

| 항목 | 값 |
|---|---|
| S108 final commit SHA-256 | `[PENDING]` |
| S200 readiness lock SHA-256 | `[PENDING]` |
| S201 completed commit SHA-256 | `[PENDING]` |
| S202 config/preregistration SHA-256 | `[PENDING]` |
| model ID/revision/preprocessor | `[PENDING: TBD_PREREGISTRATION]` |
| local bundle attestation SHA-256 | `[PENDING]` |
| label/rater attestation SHA-256 | `[PENDING]` |
| split-disjoint attestation SHA-256 | `[PENDING]` |
| completed registry/commit/receipt SHA-256 | `[PENDING]` |

## 데이터·라벨 표 후보

| split | item | speaker | prompt | pair | ordinal rating | 역할 |
|---|---:|---:|---:|---:|---:|---|
| S2 train | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | 학습·inner selection |
| S2 validation | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | 사전 고정 dev 평가 |
| S2 expert holdout | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | S203 one-shot; S202 미열람 |

반영 전에 item·speaker·prompt 및 S1 split overlap이 모두 0인지 원장
기반으로 검증한다.

## Label·objective preregistration

| 항목 | 사전 고정 값 |
|---|---|
| pairwise preference enum/tie/abstain | `[PENDING: TBD_PREREGISTRATION]` |
| ordinal scale/category/missing | `[PENDING: TBD_PREREGISTRATION]` |
| rater 수·자격·blind·calibration | `[PENDING: TBD_PREREGISTRATION]` |
| adjudication trigger·절차 | `[PENDING: TBD_PREREGISTRATION]` |
| pairwise/ordinal loss와 weighting | `[PENDING: TBD_PREREGISTRATION]` |
| uncertainty와 3상태 decision rule | `[PENDING: TBD_PREREGISTRATION]` |

## Development 결과 표 후보

Metric·CI·gate는 승인된 preregistration이 생긴 뒤 열을 확정한다.

| model/seed | pairwise metric | ordinal metric | uncertainty metric | 95% CI | dev gate |
|---|---:|---:|---:|---|---|
| seed 42 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| seed 43 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| seed 44 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| frozen ensemble | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |

## 독립성 감사 표 후보

| 검사 | 관측값 | 허용값 |
|---|---:|---:|
| S1 prediction/score/feature read | `[PENDING]` | 0 |
| oracle/predicted alignment read | `[PENDING]` | 0 |
| AI Hub silver/phone proxy label read | `[PENDING]` | 0 |
| Whisper ASR score를 feature/target으로 사용 | `[PENDING]` | 0 |
| S1 split item/speaker overlap | `[PENDING]` | 0 |
| train/validation/holdout prompt overlap | `[PENDING]` | 0 |
| S202 expert-holdout access | `[PENDING]` | 0 |

## 결과 해석 문장 후보

> [PENDING: S202_RESULTS] 사전 고정한 development protocol에서 별도 표현
> pairwise·ordinal auditor는 `[PENDING]`의 결과를 보였다. 이는 독립
> development evidence이며 S203 expert holdout 성능, S1 오판 recall,
> false-high-score 감소 또는 deployment readiness를 의미하지 않는다.

다음 근거 없이는 원고로 승격하지 않는다.

- exact model bundle·license와 3-seed checkpoint hash
- 사람 승인 preregistration의 선행 시점
- 평가자·adjudication·split-disjoint attestation
- no-S1/no-alignment/no-silver/no-Whisper-score 전수 lineage 감사
- metric·CI 독립 재계산
- completed registry, immutable S202 commit, independent receipt

## 한계 삽입 후보

> [PENDING: S202_LIMITATIONS] 다른 SSL backbone을 썼다는 사실만으로
> 독립성이 자동 성립하지 않는다. 독립성은 label, item/speaker/prompt,
> alignment, feature, selection과 holdout 분리의 실제 lineage로
> 제한적으로 입증했다. S202 development 결과는 S203 one-shot expert
> holdout 결과와 구분해 보고했다.
