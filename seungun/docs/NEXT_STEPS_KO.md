# 다음 단계 실행 계획

최종 갱신: 2026-08-02 (Asia/Seoul)

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

240건 1차 독립 수동 정렬 감사는 **실제로 완료됐지만 gate를 통과하지
못했다.** 결과: `sample_count=240`, `correct_count=178`,
`alignment_accuracy=0.7417`, `status=failed`(기준 0.95). 실패 62건
분석과 후속 조치(phone-level accuracy 지표 도입, duration 5초 이상
30건 재감사 표본 생성)는 저장소 루트
`docs/AIHUB71490_EXPERT_AUDIT_WORKSHEET_GUIDE_KO.md` 6절과
`docs/RESEARCH_COMPLETION_TRACKER_KO.md`(G04, 즉시 실행 큐 91번
항목)를 따른다. 현재 상태는 여전히
`MANUAL_ALIGNMENT_AUDIT_REQUIRED`(gate 통과분 없음)이며, smoke와 전체
학습은 시작하지 않았다.

## 1. 진행 중: 5초 이상 30건 재감사와 gate 재정의

1차 240건 실패 원인은 phone(음소) 개수가 많은 발화일수록 "발화 전체가
완벽할 확률"이 이항분포적으로 낮아지는 통계적 효과였다(phone 단위
오류율은 발화 길이와 무관하게 5~8%로 일정함을 확인). 알고리즘
파라미터 문제가 아니었다. 이에 따라 `incorrect_interval_count` 기반
phone-level accuracy를 발화 단위 accuracy와 병행 도입했고, duration
5초 이상 utterance 30건을 현재(버그 수정 반영) 알고리즘으로
재계산해 독립 표본으로 만들었다.

```text
data/processed/aihub71490/manifests/
  aihub71490_manual_alignment_audit_long30_v2.tsv
  aihub71490_manual_alignment_audit_long30_v2_details.jsonl
docs/audit/aihub71490_manual_alignment_audit_long30_v2_public.tsv
```

이 30건 재감사 결과와, 어느 지표(발화 단위 vs phone 단위)를 최종 G04
gate로 쓸지는 연구책임자 결정 사항이며 아직 확정되지 않았다.

## 2. 240건 1차 worksheet 참고 (완료됨)

240건 worksheet 자체는 이미 완료됐다. 재현·재검증이 필요하면 다음
파일을 사용한다.

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

worksheet TSV를 직접 열어 수정하지 않는다. 저장소 루트에 이미 구현된
token 보호 로컬 검수 도구가 이 4개 필드를 원자적으로 저장하고 pseudonym
변환·경계값 검증을 대신한다.

```text
scripts/review_alignment_audit.py       # serve/status/export 서버·CLI
scripts/review_alignment_audit_gui.py   # Windows 검수자용 tkinter 클라이언트
```

검수자는 `audio_path`, `textgrid_path`, `alignment_detail_line`을 이용해
실제 음성과 TextGrid phone tier, 자동 alignment를 함께 확인한다. 상세
판정 절차는 저장소 루트의
`docs/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`를, 검수 도구 실행법과
보안·개인정보 경계는
`docs/LOCAL_ALIGNMENT_REVIEW_UI_KO.md`를, worksheet 열 구성과 SSH 터널
경유 Windows 실행 순서는
`docs/AIHUB71490_EXPERT_AUDIT_WORKSHEET_GUIDE_KO.md`를 따른다. 검수 결과가
저장소 데이터 구역 정책(`docs/DATA_PRIVACY_BOUNDARY_KO.md`)의 R1 운영
제한구역을 벗어나지 않도록 export는 반드시 위 도구의 `export` 명령으로만
수행한다.

검수 완료 후 로컬 UI의 `export` 명령으로 privacy-safe scorer TSV를 만든
뒤, 저장소 루트에서 채점한다.

```bash
.venv-scorer/bin/python scripts/score_alignment_audit.py \
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_scoring.tsv \
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

## 3. Smoke training

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
train/dev: 5,000 / 1,000
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
4. dev PER와 substitution/deletion/insertion이 산출된다.
5. 대표 예측에 정상/오류 발화가 모두 포함된다.
6. best checkpoint가 저장된다.
7. checkpoint 재로딩이 성공한다.
8. 재로딩 전후 동일 음성 greedy argmax가 일치한다.
9. OOM 또는 반복적인 dataloader 실패가 없다.

Smoke 결과는 다음 경로에 생성된다.

```text
seungun/checkpoints/smoke/training_report.json
seungun/checkpoints/smoke/dev_evaluation.json
seungun/checkpoints/smoke/dev_confusion_matrix.json
seungun/checkpoints/smoke/best_model/
```

Smoke와 일반 학습 경로는 `manifest/test.jsonl`을 열지 않는다. 최종 test
평가를 위해 이 명령을 변형하거나 test를 dev로 복사·symlink하지 않는다.
workspace 밖 manifest, manifest 경로 구성요소 symlink, train/dev 파일
및 vocab symlink도 거부한다. smoke는 앞 5,000/1,000건만 반환하지만
provenance 검증은 각 manifest 끝까지 수행한다.

Smoke 결과가 나쁘면 전체 학습을 시작하지 않는다. 특히 200 steps에서
loss가 전혀 감소하지 않거나 decode가 대부분 비어 있으면 learning rate,
batch 구성, target 길이와 CTC input/output length를 먼저 조사한다.

## 4. 전체 학습 전 최종 확인

Smoke 통과 후 다음 항목을 다시 확인한다.

- `manifest/preparation_report.json`의 화자 교집합이 모두 0
- train vocabulary에 `<unk>`로 변환되는 target이 0
- locked test가 학습·checkpoint 선택·일반 평가에서 읽히지 않음
- base model revision이
  `1a640f32ac3e39899438a2931f9924c02f080a54`로 고정됨
- Git diff와 실행 명령 기록
- 최소 500 GiB 여유 디스크
- GPU에서 다른 대규모 작업이 실행 중이지 않음
- smoke checkpoint와 평가 산출물 SHA-256 기록

## 5. 전체 학습

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
train/dev loaded by runner: 217,176 / 34,424
reserved locked test: 65,296, not opened by runner
epochs: 3
train/eval batch: 4 / 4
gradient accumulation: 4
learning rate: 5e-5
warmup ratio: 0.1
feature encoder: frozen
precision: BF16 autocast
gradient checkpointing: enabled
best model selection: dev PER minimum
locked test evaluation: 일반 학습 runner에서는 접근 금지
```

중단 후 재개할 때는 같은 output directory와
`--resume-from-checkpoint <checkpoint-path>`를 사용한다. 다른 revision,
vocab 또는 manifest로 기존 optimizer checkpoint를 재사용하지 않는다.

## 6. 전체 학습 완료 검증

전체 학습 완료 후 반드시 보고할 항목:

- 총 학습시간과 peak GPU memory
- 시작/최종 train loss와 best dev loss
- best checkpoint
- dev PER
- substitution/deletion/insertion 수와 비율
- 빈 decode 비율
- NaN/Inf 여부
- checkpoint 재로딩 결과
- 재로딩 전후 동일 음성 출력 일치
- 정상/오류 발화를 포함한 대표 예측 최소 10개
- confusion matrix
- manifest, vocab, processor, checkpoint, 평가 보고서 SHA-256
- pytest/Ruff/mypy 재검사

이 항목들은 일반 학습 완료 검증 범위다. 최종 locked test PER는
사전등록된 별도 one-shot 보호 실행기, 명시적 승인, 불변 checkpoint hash가
모두 준비된 뒤에만 한 번 산출한다. 현재 `seungun/`에는 그 실행기가
구현돼 있지 않으므로 test 결과를 임의로 만들지 않는다.

## 실행 순서 요약

```text
독립 전문가 240건 청취 감사 (완료: 74.17%, gate 미달)
  -> 실패 원인 분석 (완료: 파라미터 문제 아님, phone-level accuracy 도입)
  -> 5초 이상 30건 재감사 (진행 중)
  -> 감사 채점 및 95% gate 재확정
  -> smoke 200 steps
  -> smoke PER/decode/reload gate
  -> 전체 3 epoch 학습
  -> dev 산출물 checksum과 검증 보고서
  -> 별도 승인된 protected executor에서 locked test 1회 평가
```

현재 즉시 해야 할 일은 5초 이상 30건 재감사 완료와, 어느 정확도
지표(발화 단위 vs phone 단위)를 최종 gate로 쓸지 연구책임자가
확정하는 것이다. gate가 확정·통과되기 전까지
`configs/xlsr300m_full.yaml`의 `enabled: false`를 유지한다.
