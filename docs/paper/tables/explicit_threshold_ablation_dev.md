# Explicit threshold ablation — development result

생성 기준: 2026-07-29 KST  
결과 범위: **development-only, post-hoc/exploratory, oracle alignment**  
주 분석 모집단: L1 Chinese/Japanese, gender F/M

이 표는 사전 고정된 기준선의 실패 원인을 조사하는 사후 분석이다.
Locked-test 결과가 아니며, 기준선 report나 B06 실패 상태를 덮어쓰지
않는다. Smoke gate 통과만으로 모델을 승격하지 않으며 독립 전문가 gold가
완료되기 전에는 annotation 재현 engineering 지표로만 해석한다.

## 격리된 선택 절차

- 입력과 모델: 기준선과 같은 목표/앞/뒤 phone, oracle duration,
  segment RMS/ZCR 및 balanced logistic regression
- primary train: 1,776,049 phone, 860 speakers
- seed별 inner split: hash 기반 speaker-disjoint 688 train /
  172 validation speakers, overlap 0
- 선택 공간: `C∈{0.1,1,10}`, threshold `0.05–0.95`(0.01 간격)
- 선택 기준: inner-validation macro F1; dev는 선택에 사용하지 않음
- 최종 평가: 선택 C로 primary train 전체를 refit한 뒤 threshold를
  이전하고, dev를 seed별 한 번만 예측
- 금지 split 접근: stack 0, calibration 0, locked-test 0

## Seed별 결과

| 분석 | seed | selected C | threshold | inner-val macro F1 | primary-dev macro F1 | primary-dev AUPRC | all-dev audit macro F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 사전 기준선 | 42/43/44 | 10.0 | 0.50 | - | 0.490189 | 0.161323 | 0.490127 |
| 사후 탐색 | 42 | 10.0 | 0.68 | 0.558565 | 0.558797 | 0.161323 | 0.558737 |
| 사후 탐색 | 43 | 10.0 | 0.67 | 0.554503 | 0.562138 | 0.161323 | 0.562078 |
| 사후 탐색 | 44 | 10.0 | 0.68 | 0.553831 | 0.558797 | 0.161323 | 0.558737 |
| 사후 mean ± sample SD | - | - | - | - | 0.559911 ± 0.001929 | 0.161323 ± 0.000000 | 0.559851 ± 0.001929 |

- primary-dev macro F1 차이 대 사전 기준선: **+0.069722**
- primary-dev AUPRC 차이: **0.000000**
- 사후 smoke gate 0.50: **passed (exploratory only)**
- engineering gate 0.60: **failed**
- B06 상태: **failed_unchanged**
- promotion decision: **not allowed**

Threshold는 동일한 score의 순위를 바꾸지 않기 때문에 AUPRC는 세 seed
모두 기준선과 동일하다. F1 증가는 오류 발행 operating point를 옮긴
결과이지 특징 표현력이나 ranking 개선의 증거가 아니다.

## 기준선 실패 분석

기본 threshold 0.5의 primary-dev class별 결과는 다음과 같다.

| class | precision | recall | F1 |
|---|---:|---:|---:|
| 정상(0) | 0.940785 | 0.641388 | 0.762759 |
| 오류(1) | 0.133998 | 0.578858 | 0.217619 |

- all-dev 실제 오류 비율: 0.087463
- all-dev 오류 예측 비율: 0.377809
- all-dev L1 macro F1: Chinese 0.488789, Japanese 0.492464
- primary 범위 밖 gender `O` macro F1: 0.426486
- 숙련도 최저 macro F1: `Advance` 0.470977
- target-phone 최저 macro F1:
  `ㅠ` 0.134699, `spn` 0.157566, `ㅇ` 0.161832

Slice는 all-dev audit에서 최소 100건인 집계만 기록했다. 원본 경로,
발화 ID, 화자 ID를 읽거나 공개하지 않았다.

## 재현 식별자

- 실패 분석 JSON:
  `artifacts/analysis/aihub71490_explicit_oracle_boundary_v1_failure/failure_analysis.json`
- 실패 분석 JSON SHA-256:
  `bfdcff140326dfb73b09b0c564dbcaef75b1a4a028d865819cc6f9e53aece65b`
- 실패 분석 Markdown SHA-256:
  `aff130481880aac4324034bf9c77562d46733d64e6fa79f2c3c282a72f467eb4`
- ablation config:
  `configs/experiments/explicit_threshold_ablation_v1.yaml`
- config SHA-256:
  `62323455d80807ed9ad534bc53f6b21b41feb66836038699cefedc4b8fdedb10`
- 결과 report:
  `artifacts/experiments/aihub71490_explicit_threshold_ablation_v1/report.json`
- report SHA-256:
  `ef16c405ad01d1c5add4c05ce077072b218836ee683e626e12a0e24f4637a7e1`
- 완료 registry:
  `artifacts/experiment_registry/aihub71490-explicit-threshold-ablation-v1-20260729/metadata.json`
- registry SHA-256:
  `e9c6ba64729f0b4a8bab577f569444f0a1e5689795fde71fc28544d1bdb43c99`
- registry run ID:
  `aihub71490-explicit-threshold-ablation-v1-20260729`

Report와 registry에는 checkpoint 3개, prediction 3개, slice report 3개,
최종 report 1개의 크기와 SHA-256이 기록돼 있다.
