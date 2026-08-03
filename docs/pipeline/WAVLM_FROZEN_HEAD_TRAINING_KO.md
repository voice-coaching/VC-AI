# Frozen WavLM 단순 head 학습 계약

최종 갱신: 2026-07-29 KST  
상태: Base+ 3-seed 학습·dev 평가와 완료 registry 검증 완료, Large 실행 대기

## 목적

`scripts/train_wavlm_frozen_head.py`는 이미 고정·동결된 WavLM 표현 위에
작은 phone/utterance head만 학습한다. 현재 실험은
`microsoft/wavlm-base-plus` revision
`4c66d4806a428f2e922ccfa1a962776e232d487b`를 사용한다. head 코드는
encoder model ID와 hidden dimension을 config에서 받아 이후 Large
실험에서도 같은 구조와 학습 절차를 재사용한다. report와 checkpoint의
encoder variant는 하드코딩하지 않고 config의 `encoder.variant`를 사용하며,
variant와 model ID의 조합(`base_plus`/`wavlm-base-plus`,
`large`/`wavlm-large`)이 다르면 실행을 거부한다.

이 결과는 gold target-phone 경계를 사용한 oracle-alignment 개발 결과다.
실제 정렬기가 포함된 배포 성능으로 해석하지 않으며, 수동 G0와 사전 승인된
locked-test 1회 평가 전에는 모델 승격 근거로 사용하지 않는다.

## 입력과 label 역할

- phone 입력: `embedding_mean`, `target_label`만 허용
- phone label: `binary_error_label`은 loss/평가에만 사용
- utterance 입력: `embedding_mean_std`만 허용
- utterance label: privacy-safe explicit utterance cache의
  `pronunciation_score`, `fluency_score`, `comprehensibility_score`만
  SHA-256 발화 ID로 join
- L1·성별·숙련도는 primary-scope filter와 slice에만 사용
- raw manifest, audio/path, raw stem, realized phone, alignment operation,
  error tag, `.env*`는 읽거나 registry에 등록하지 않음

primary population은 Chinese/Japanese L1과 F/M 성별 교집합이다. `train`
화자는 SHA-256 앞 64 bit의 고정 modulo partition으로 train-fit과
inner-validation에 나눈다. early stopping과 phone threshold는 이
train 내부 speaker-disjoint validation에서만 선택한다. `dev`는 seed별
최종 checkpoint에 대해 task별 한 번만 추론하며 어떤 선택에도 사용하지
않는다.

## 실행 전 gate

runner는 학습 시작 전에 다음 조건을 모두 확인한다.

1. WavLM cache report가 `completed`, `full_run=true`, `limit=null`,
   `source_scan_complete=true`다.
2. source 322,380건과 utterance/phone/word
   169,837/2,233,039/408,790건이 사전 고정 수와 일치한다.
3. 세 cache 종류의 모든 shard set, SHA-256, byte/row/row-group 수,
   schema, fixed-size float16 dimension과 metadata를 검증한다.
4. 실제 row split은 train/dev뿐이며 train/dev speaker 교집합이 0이다.
5. privacy flags, restricted access, non-shareable, prohibited column,
   pinned model revision/config/checkpoint hash가 일치한다.
6. explicit utterance label cache hash와 metadata를 검증하고 WavLM
   utterance 169,837건과 hash join이 정확히 1:1임을 확인한다.

100건 smoke나 일부 source scan은 명시적으로 거부한다.

## 학습·메모리 계약

세 seed는 42/43/44이며 같은 head와 AdamW hyperparameter를 사용한다.
phone head는 WavLM embedding MLP와 train-fit에서만 만든 target-phone
embedding을 결합한다. utterance head는 세 점수를 동시에 출력하는
bounded MLP다. 각 seed 안에서도 phone 학습이 소비한 dropout RNG 수나
early-stopping epoch가 utterance 초기화에 영향을 주지 않도록 task가
바뀔 때 같은 seed로 RNG를 다시 초기화한다.

Parquet embedding을 전체 float32 행렬로 만들지 않는다. shard를
8,192행 이하 batch로 순차 읽고 해당 mini-batch만 float32 CUDA tensor로
변환한다. Base+ config에서 embedding에 대한 정적 상한은 다음과 같다.

- 한 Parquet float16 embedding batch: 최대 25,165,824 bytes
- 한 float32 학습 embedding tensor: 최대 25,165,824 bytes
- explicit label hash map: 169,837개 non-embedding record
- dev buffer: prediction/label/hash metadata만 보관하고 embedding은 보관하지 않음

report에는 실제 process peak RSS와 CUDA peak allocated/reserved bytes도
기록한다.

결정적 CUDA 실행을 위해 runner는 `torch` import와 CUDA runtime 초기화
전에 `CUBLAS_WORKSPACE_CONFIG=:4096:8`을 설정한다. 이미 다른 값이
설정돼 있거나 production process가 이 계약보다 먼저 CUDA를 초기화했다면
실행을 거부한다. report에는 이 고정값과 초기화 순서 검증 결과만 기록하며
다른 환경 변수나 `.env*` 내용은 읽거나 기록하지 않는다. 실제 full 학습
전에는 fresh subprocess에서 CUDA forward/backward smoke를 별도로 통과해야
한다.

## 실행

full cache가 독립 검증된 뒤 다음 명령을 사용한다.

```bash
.venv-scorer/bin/python scripts/train_wavlm_frozen_head.py \
  --config configs/experiments/wavlm_base_plus_frozen_head_v1.yaml \
  --run-id aihub71490-wavlm-base-plus-frozen-head-v1-dev
```

기본 산출물은
`artifacts/experiments/aihub71490_wavlm_base_plus_frozen_head_v1/`에
원자적으로 publish된다.

- `report.json`: cache/model/config hash, split 계약, seed별/집계 metric
- `seed_*/checkpoint.pt`: head weights, train-only vocab/threshold
- `seed_*/phone_dev_predictions.parquet`
- `seed_*/utterance_dev_predictions.parquet`
- `seed_*/slice_metrics.json`
- experiment registry: input hash, runtime/GPU/git, metric과 모든 artifact hash

`report.json`의 `sources`에는 실행한 trainer와 head source의 SHA-256을
직접 기록한다. 두 source 파일은 registry의 model input으로도 각각
fingerprint하며, 학습 도중 어느 하나라도 변경되면 publish하지 않는다.
따라서 dirty worktree에서도 실제 실행 코드를 commit hash만으로
추정하지 않는다.

중간 실패 시 `.part-*` experiment directory는 제거한다. registry를 시작한
뒤 학습 artifact가 먼저 publish됐더라도 metric/artifact 등록이나 registry
종료가 실패하면 canonical output directory를 원자적으로 숨긴 뒤 제거한다.
registry에는 exception message나 환경 값을 저장하지 않고 실패 type만
남긴다.
