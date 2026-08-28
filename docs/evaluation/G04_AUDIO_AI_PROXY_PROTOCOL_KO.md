# G04 로컬 음성 AI 프록시 프로토콜

## 목적과 지위

이 프로토콜은 고정된 AI Hub 71490 G04 표본 240건의 음성과 목표·실현
phone 주석을 로컬 모델로 재검토해 전문가 재청취의 우선순위를 정한다.
결과는 **공학적 프록시**이며 전문가 검증이나 G04 완료 증명을 대체하지
않는다. 따라서 프록시 결과만으로 D07 이후 단계를 승격할 수 없다.

## 고정 입력과 모델

- 입력: 기존 G04 worksheet/details로 고정된 240건
- 음성·원주석 출처: 로컬 제한구역의 AI Hub 71490
- 청취 모델: 로컬 캐시 `Qwen2-Audio-7B-Instruct`
- 모델 revision: `0a095220c30b7b31434169c3086508ef3ea5bf0a`
- 실행 장치·dtype: CPU, bfloat16
- G04 전용 fine-tuning: 없음
- 네트워크 전송: 없음

## 주석 역할 분리

- 목표 phone은 녹음자가 발음했어야 할 prompt의 의도 발음을 나타낸다.
- 실현 phone은 녹음에서 실제로 발음된 내용을 나타낸다.
- 학습자의 실제 발음 오류 때문에 목표 phone과 음성이 다르다는 사실만으로
  목표 주석 오류라고 판정하지 않는다.

AI Hub 원문 phone과 로컬 g2pK prompt phone을 목표 주석과 각각 비교하고,
둘 중 낮은 유사도를 목표 주석 근거로 사용한다. Qwen2-Audio 전사문은 같은
g2pK frontend를 거쳐 목표·실현 phone과 별도로 비교한다.

## 사전 고정 임계값

- 최소 모델 토큰 평균 확률: `0.45`
- 최소 목표-원문 phone 유사도: `0.80`
- 최소 실현-음성 phone 유사도: `0.65`
- 목표·실현 중 어느 것과도 음성이 맞지 않을 때의 기권 경계: `0.45`

모델 확률이 낮거나 음성이 두 주석 모두와 기권 경계 미만이면
`uncertain`으로 판정한다. 가능한 판정은 `consistent`,
`target_annotation_mismatch`, `realized_annotation_mismatch`,
`both_annotations_mismatch`, `uncertain` 다섯 가지다.

## 정렬 재계산

목표·실현 시간구간은 `align_intervals()`의 현재 고정 인자
`tolerance_seconds=0.03`, `min_overlap_ratio=0.50`,
`preserve_one_to_many=false`로 재계산한다. 이 결과는 음성 기반 fidelity
판정과 분리해 저장한다. 현재 정렬 후보 우선순위가 과거 고정 표본 생성 뒤
변경됐으므로 재계산 차이를 곧바로 사람 주석 오류로 해석하지 않는다.

## 개인정보와 산출물

- R1 제한 상태: 항목별 전사문, phone 근거, 원시 입력 해시
- A1 집계: 판정 개수, 정렬 정확도, 설정·입력·제한 상태 해시
- 외부 전송: 금지
- 공개 집계에 원문, 음성 경로, 원시 utterance/speaker ID 저장: 금지
- 기존 canonical review state 변경: 금지

실행기는 항목마다 제한 상태를 원자적으로 저장하며 중단 뒤 같은 입력·모델·
임계값에서만 재개한다.

## 통과 해석

240건을 모두 처리해도 결과 상태는 `completed_proxy`일 뿐이다.
`substitutes_expert_review=false` 및
`canonical_g04_completion_claim_allowed=false`를 유지한다. 기존 G04 계약의
전문가·채점·attestation 조건 또는 승인된 계약 변경이 없으면 다음 정식
게이트는 열리지 않는다.
