# AI Hub 71490 데이터·split·QC

상태: 자동 집계 완료, 외부 전문가 정렬 G0는 `PENDING`

> 모든 값은 아래 SHA-256 고정 aggregate artifact에서 가져왔다. 이 표는
> 원본 음성·전사·경로·발화/화자 식별자를 포함하지 않는다.

## 전체 cohort와 고정 split

| 항목 | 발화 수 | 비고 |
|---|---:|---|
| 전체 source | 322,380 | 1,830화자, 344.208시간, 229개 고유 prompt |
| train | 150,677 | 고정 source split |
| dev | 35,074 | 고정 source split |
| stack | 35,211 | 평가 전용, cache 생성 제외 |
| calibration | 35,106 | 평가 전용, cache 생성 제외 |
| locked-test | 66,312 | 미열람 metric 보호 대상, cache 생성 제외 |

다섯 split의 발화 수 합은 전체 source 322,380건과 일치한다. Split 간
화자 및 동일 PCM overlap example은 각각 0건이다.

## Train/dev cache cohort flow

제외 수는 pipeline 적용 순서에 따른 상호배타적 disposition이다.

| 순서 | 처리 | 제외 | 잔여 |
|---:|---|---:|---:|
| 0 | 전체 source metadata scan | - | 322,380 |
| 1 | stack/calibration/locked-test 제외 | 136,629 | 185,751 |
| 2 | QC flag가 있는 행 제외 | 642 | 185,109 |
| 3 | 예약 prompt 행 제외 | 15,272 | 169,837 |
| 4a | 최종 train cache | - | 137,759 |
| 4b | 최종 dev cache | - | 32,078 |

최종 train/dev의 합은 169,837건이다. 예약된 prompt는 20개지만,
cache의 15,272건은 split과 QC gate를 먼저 통과한 train/dev 행에만
적용한 제외 수다. Prompt-challenge artifact의 21,240건은 locked-test
challenge 이외 split에서 동일 prompt를 가진 전체
training/tuning-exclusion disposition이므로 두 수를 혼용하지 않는다.

## 자동 G0와 미완료 외부 G0

| 검사 | 자동 집계 결과 | 판정 범위 |
|---|---:|---|
| WAV–JSON–TextGrid pairing | 99.9996898% | 자동 modality pairing |
| decoder failure | 0% | 자동 decode audit |
| time-bound error | 0.3319064% | 자동 interval-bound audit |
| split 간 화자 overlap example | 0 | 자동 hash audit |
| split 간 PCM duplicate example | 0 | 자동 PCM hash audit |
| error-tag ontology 미매핑 | 0 | 자동 taxonomy audit |
| 외부 전문가 정렬 정확도 | `PENDING` | 이 표의 자동 G0 결과에 포함하지 않음 |

`interval_out_of_bounds` 자동 flag는 전체 source에서 1,070건,
realized-phone tier 누락은 1건이다. Cache의 QC 제외 642건은 evaluation
split을 먼저 제외한 뒤 train/dev에서 하나 이상의 QC flag가 있는 행을
세는 pipeline disposition이므로 전체-source flag occurrence와 같지 않다.
자동 G0 통과는 외부 전문가 정렬 G0 통과를 의미하지 않는다.

## 근거 artifact

| 역할 | 경로 | SHA-256 |
|---|---|---|
| 데이터·split 집계 | `data/processed/aihub71490/manifests/aihub71490_data_card.json` | `4ab53c6837b1a28fbf153dd3a351a195f6931a43694467114cea6c21b8a57b13` |
| 자동 G0 | `data/processed/aihub71490/manifests/aihub71490_g0_audit.json` | `1a41814f04062c1f1242287e05e34f46673e0e6e2152075c0dd1cf019bce81fd` |
| prompt 예약 | `data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json` | `3ea7f12ea76be07a0796d7e62f0c3727ca9858c0278df7c85ac1e08d9b653fa9` |
| train/dev filter | `data/processed/aihub71490/features/explicit_utterance_train_dev_v1_report.json` | `2f7b540b5fb6d2aaad3e2efa29da5f5b23c967645da0eb98d9a6d68ffc5b762e` |

WavLM Base+ cache report도 같은 filter 결과
322,380 → 169,837(137,759/32,078)과 제외 수
136,629/642/15,272를 독립적으로 반복 기록한다. 해당 report SHA-256은
`34da4c3ec48088b88e1e44763868cd15a21b7baaa06a2cb253338866262ef41d`다.
