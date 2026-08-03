# Explicit oracle-boundary baseline — development result

생성 기준: 2026-07-29 KST  
결과 범위: **development-only, provisional, oracle alignment**  
주 분석 모집단: L1 Chinese/Japanese, gender F/M

이 표는 locked-test 결과가 아니다. AI Hub 목표 annotation과 TextGrid 실현
annotation의 일대일 overlap에서 파생한 silver phone label을 사용한다.
수동 G0와 독립 전문가 gold가 완료되기 전에는 모델 승격 근거로 쓰지 않는다.

## Phone binary error

| seed | selected C | macro F1 | AUPRC |
|---:|---:|---:|---:|
| 42 | 10.0 | 0.490189 | 0.161323 |
| 43 | 10.0 | 0.490189 | 0.161323 |
| 44 | 10.0 | 0.490189 | 0.161323 |
| mean ± sample SD | - | 0.490189 ± 0.000000 | 0.161323 ± 0.000000 |

- primary dev phone: 423,820
- all-dev audit phone: 424,315
- all-dev audit macro F1/AUPRC: 0.490127/0.161182
- smoke gate 0.50: **failed**
- engineering gate 0.60: **failed**

## Utterance ordinal scores

모든 outcome에서 cumulative ordinal logistic이 선택됐다. 아래 값은 seed
42/43/44에서 동일한 결정적 pipeline의 mean이다.

| outcome | QWK | Spearman | MAE | ICC(2,1) |
|---|---:|---:|---:|---:|
| pronunciation | 0.123321 | 0.165532 | 0.862386 | 0.143535 |
| fluency | 0.448306 | 0.477956 | 0.803590 | 0.469829 |
| comprehensibility | 0.115326 | 0.163301 | 0.906048 | 0.129859 |

- primary dev utterance: 32,063
- all-dev audit utterance: 32,078
- 목표 Spearman 0.50: 세 outcome 모두 **failed**

seed별 값이 같은 것은 stochastic fine-tuning이 아니라 동일 데이터의
결정적 scikit-learn solver를 반복했기 때문이다. 따라서 0인 seed 표준편차를
불확실성이 0이라는 뜻으로 해석하지 않는다. 화자 bootstrap CI와 Holm
보정은 G0 뒤 사전 허가된 단일 locked-test 평가에 남겨 둔다.

## 재현 식별자

- 결과 report:
  `artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/report.json`
- report SHA-256:
  `a5c3ade0738777409bb57956b088e025081cb63886808c6d6fc87724d0875519`
- 완료 registry:
  `artifacts/experiment_registry/aihub71490-explicit-v1-dev-primary-20260729/metadata.json`
- registry SHA-256:
  `0445d69cb18fd9105a531a4e5d342d60c7cad94050c6a031c0d7ee3ef6e2141e`
- 보존한 최초 scope 오류 중단 run:
  `artifacts/experiment_registry/aihub71490-explicit-v1-dev-20260729/metadata.json`
- 실행 환경: Python 3.12.3, NumPy 2.5.1, SciPy 1.18.0,
  scikit-learn 1.9.0, aarch64, NVIDIA GB10/CUDA 13.0
- 완료 run 시간: 2026-07-29 03:06:47–03:08:19 KST

