# AI Hub 71490 발음 데이터 카드

생성일: 2026-07-29 KST

이 문서는 제한 데이터의 원문·원시 화자 ID를 포함하지 않는 집계
data card다. 모델 성능 결과가 아니라 데이터 구성과 품질을 기록한다.

## 규모와 고정 분할

- 발화: 322,380건
- 가명 화자: 1,830명
- 총 길이: 344.208시간
- 고유 prompt: 229개
- 고유 PCM: 322,368개
- 동일 split 내부 중복 record: 12건

| split | 발화 수 |
|---|---:|
| calibration | 35,106 |
| dev | 35,074 |
| locked_test | 66,312 |
| stack | 35,211 |
| train | 150,677 |

모든 prompt가 다섯 split에 나타나므로 현 speaker split은
prompt-disjoint 평가가 아니다. 별도 예약 prompt challenge를 사용한다.

## 길이와 오디오

- median: 2.901초
- p95: 6.912초
- p99: 9.984초
- max: 69.973초
- 20초 초과: 3,028건
- 60초 초과: 415건
- sample rate: 8000, 16000, 24000, 44100, 48000, 96000, 192000 Hz

SSL 입력은 decoder 이후 16 kHz로 resample하며, 20초 초과 발화는
label-aware chunking 대상으로 처리한다.

## 감독 label coverage

| label | 유효 | 결측(0 sentinel 포함) |
|---|---:|---:|
| pronunciation_score | 322,337 | 43 |
| fluency_score | 81,780 | 240,600 |
| comprehensibility_score | 322,337 | 43 |

평정 척도는 1–5다. 원본 0은 실제 최저 점수로 학습하지 않고
미평정 sentinel로 mask한다.

## Phone과 오류 annotation

- target phone label: 38종
- realized raw label: 1,715종
- realized occurrence canonical coverage: 99.968206%
- `<unk>` occurrence: 1,400건
- 공식 error tag: 17종, 176,523구간
- ontology 미매핑: 0건

| operation | 수 |
|---|---:|
| deletion | 38,385 |
| insertion | 25,783 |
| match | 3,991,194 |
| substitution | 267,049 |

Gold TextGrid boundary를 사용하는 결과는 `oracle alignment`로
표기하고, 실제 운용용 predicted alignment 결과와 분리한다.

## 자동 G0와 제외 정책

- QC flag 없는 발화: 321,309건
- `interval_out_of_bounds`: 1,070건
- `missing_realized_phone_tier`: 1건
- 위 flag 발화는 primary 학습에서 제외하고 감사 slice에는 보존한다.
- split 간 화자·PCM 중복은 0이다.
- 최종 G0는 240건 전문가 정렬 감사 전까지 pending이다.

## 재현 식별자

- modeling manifest SHA-256: `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1`
- split assignment SHA-256: `d3f0324d61afb9cebe7c002de0f39428584124771f8469703e60fc62e121ed84`

modeling manifest는 음원 decode를 위한 원본 상대경로와 file stem을
보존하므로 접근 제한 운영용 artifact다. file stem prefix와 원시
`UserID`의 연결 가능성이 확인되어 공개 가능한 manifest로 취급하지
않는다. 학습 feature cache와 결과 report에는 namespaced SHA-256 발화
ID만 허용하고 원본 stem·경로를 내보내지 않는다.
