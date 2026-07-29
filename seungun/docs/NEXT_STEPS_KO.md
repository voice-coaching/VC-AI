# 다음 단계 실행 계획

최종 갱신: 2026-07-29 (Asia/Seoul)

## 현재 상태

자동으로 수행할 수 있는 CTC 준비 작업은 완료됐다.

- TextGrid 관측 target 보수적 정규화 정책 확정
- 전체 322,380건 처리 및 부적합 발화 제외
- 화자 비중첩 train/dev/test 생성
- train 217,176 / dev 34,424 / test 65,296
- 43-token vocabulary와 processor 생성
- XLS-R-300M exact revision과 가중치 고정
- smoke/full 공용 학습·평가 코드 구현
- pytest/Ruff/mypy 통과

현재 상태는 `MANUAL_ALIGNMENT_AUDIT_REQUIRED`다. smoke와 전체 학습은
아직 시작하지 않았다.

## 1. 최우선 작업: 독립 수동 정렬 감사

한국어 음성학 또는 음성 정렬 검수 역량이 있는 독립 검수자가 다음
worksheet 240건을 완성해야 한다.

```text
data/processed/aihub71490/manifests/
  aihub71490_manual_alignment_audit.tsv
```

각 행에서 반드시 채울 필드:

```text
reviewer_id
alignment_correct
maximum_boundary_error_ms
notes (필요한 경우)
```

검수자는 `audio_path`, `textgrid_path`, `alignment_detail_line`을 이용해
실제 음성과 TextGrid phone tier, 자동 alignment를 함께 확인한다. 상세
절차는 저장소 루트의
`docs/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`를 따른다.

완료 후 저장소 루트에서 채점한다.

```bash
.venv-scorer/bin/python scripts/score_alignment_audit.py \
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv \
  --output \
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json
```

통과 조건:

```text
sample_count = 240
valid_decision_count = 240
missing reviewer = 0
status = passed
alignment_accuracy >= 0.95
```

95% 미만이면 학습하지 않고 실패 사례를 유형별로 분석해 TextGrid
정규화·제외 정책과 manifest를 다시 생성해야 한다.

## 2. Smoke training

수동 감사 통과 후에만 저장소 루트에서 실행한다.

```bash
HF_HOME=data/cache/huggingface \
PYTHONPATH=seungun/src \
.venv-scorer/bin/python seungun/scripts/train_ctc.py \
  --mode smoke \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --manifest-dir seungun/manifest \
  --processor-dir seungun/processor \
  --manual-audit-result \
    data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json \
  --output-dir seungun/checkpoints/smoke \
  --seed 42 \
  --num-workers 8
```

Smoke 설정:

```text
train/dev/test: 5,000 / 1,000 / 1,000
max steps: 200
train/eval batch: 4 / 4
gradient accumulation: 4
learning rate: 1e-4
warmup ratio: 0.1
feature encoder: frozen
precision: BF16 autocast
gradient checkpointing: enabled
seed: 42
```

Smoke 통과 조건:

1. loss가 finite이고 감소 추세를 보인다.
2. NaN/Inf가 없다.
3. 빈 decode 비율이 허용 가능한 수준이다.
4. dev/test PER와 substitution/deletion/insertion이 산출된다.
5. 대표 예측에 정상/오류 발화가 모두 포함된다.
6. best checkpoint가 저장된다.
7. checkpoint 재로딩이 성공한다.
8. 재로딩 전후 동일 음성 greedy argmax가 일치한다.
9. OOM 또는 반복적인 dataloader 실패가 없다.

Smoke 결과는 다음 경로에 생성된다.

```text
seungun/checkpoints/smoke/training_report.json
seungun/checkpoints/smoke/dev_evaluation.json
seungun/checkpoints/smoke/test_evaluation.json
seungun/checkpoints/smoke/confusion_matrix.json
seungun/checkpoints/smoke/best_model/
```

Smoke 결과가 나쁘면 전체 학습을 시작하지 않는다. 특히 200 steps에서
loss가 전혀 감소하지 않거나 decode가 대부분 비어 있으면 learning rate,
batch 구성, target 길이와 CTC input/output length를 먼저 조사한다.

## 3. 전체 학습 전 최종 확인

Smoke 통과 후 다음 항목을 다시 확인한다.

- `manifest/preparation_report.json`의 화자 교집합이 모두 0
- train vocabulary에 `<unk>`로 변환되는 target이 0
- locked test가 학습·checkpoint 선택에 사용되지 않음
- base model revision이
  `1a640f32ac3e39899438a2931f9924c02f080a54`로 고정됨
- Git diff와 실행 명령 기록
- 최소 500 GiB 여유 디스크
- GPU에서 다른 대규모 작업이 실행 중이지 않음
- smoke checkpoint와 평가 산출물 SHA-256 기록

## 4. 전체 학습

모든 gate 통과 후 저장소 루트에서 실행한다.

```bash
HF_HOME=data/cache/huggingface \
PYTHONPATH=seungun/src \
.venv-scorer/bin/python seungun/scripts/train_ctc.py \
  --mode full \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --manifest-dir seungun/manifest \
  --processor-dir seungun/processor \
  --manual-audit-result \
    data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json \
  --output-dir seungun/checkpoints/full \
  --seed 42 \
  --num-workers 8
```

현재 full 기본값:

```text
train/dev/test: 217,176 / 34,424 / 65,296
epochs: 3
train/eval batch: 4 / 4
gradient accumulation: 4
learning rate: 5e-5
warmup ratio: 0.1
feature encoder: frozen
precision: BF16 autocast
gradient checkpointing: enabled
best model selection: dev PER minimum
locked test evaluation: 학습 종료 후 1회
```

중단 후 재개할 때는 같은 output directory와
`--resume-from-checkpoint <checkpoint-path>`를 사용한다. 다른 revision,
vocab 또는 manifest로 기존 optimizer checkpoint를 재사용하지 않는다.

## 5. 전체 학습 완료 검증

전체 학습 완료 후 반드시 보고할 항목:

- 총 학습시간과 peak GPU memory
- 시작/최종 train loss와 best dev loss
- best checkpoint
- dev/test PER
- substitution/deletion/insertion 수와 비율
- 빈 decode 비율
- NaN/Inf 여부
- checkpoint 재로딩 결과
- 재로딩 전후 동일 음성 출력 일치
- 정상/오류 발화를 포함한 대표 예측 최소 10개
- confusion matrix
- manifest, vocab, processor, checkpoint, 평가 보고서 SHA-256
- pytest/Ruff/mypy 재검사

## 실행 순서 요약

```text
독립 전문가 240건 청취 감사
  -> 감사 채점 및 95% gate
  -> smoke 200 steps
  -> smoke PER/decode/reload gate
  -> 전체 3 epoch 학습
  -> locked test 1회 평가
  -> 최종 산출물 checksum과 검증 보고서
```

현재 즉시 해야 할 일은 첫 단계인 독립 수동 정렬 감사다. 그 결과가
통과되기 전까지 `configs/xlsr300m_full.yaml`의 `enabled: false`를
유지한다.
