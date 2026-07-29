# Korean phone CTC gated baseline

AI Hub 한국어 학습자 발음 데이터의 실제 관측 음소열을 검증하고
XLS-R-300M CTC smoke training으로 넘어가기 위한 독립 작업 디렉터리다.

현재 자동 준비는 완료됐으며 상태는
`MANUAL_ALIGNMENT_AUDIT_REQUIRED`다. 독립 수동 정렬 감사 전에는 모델
학습이 비활성화된다.

## 핵심 문서

- [작업 기록과 실측 결과](docs/WORK_LOG_AND_FINDINGS_KO.md)
- [다음 단계와 재개 gate](docs/NEXT_STEPS_KO.md)
- [TextGrid target 정책](configs/textgrid_target_policy.yaml)

## 확인된 사실

- 요청서의 dataset ID 71479와 실제 서버 dataset 71490이 불일치한다.
- JSON `phonemic.phones`는 기대 발음열이다.
- TextGrid phone tier가 실제 교체와 삭제를 기록한다.
- 공식 Validation 화자 1,812명 전원이 Training과 겹친다.
- 미해결 TextGrid label은 임의 교정하지 않고 해당 발화를 제외한다.
- 관측 target manifest는 316,896건이며 train/dev/test 화자 교집합은 0이다.
- vocabulary는 43개이고 `<pad>` ID 0을 CTC blank로 사용한다.
- XLS-R-300M revision은
  `1a640f32ac3e39899438a2931f9924c02f080a54`로 고정했다.
- 240건 독립 수동 정렬 감사 worksheet는 아직 전부 미검수 상태다.
- 전체 학습과 smoke training은 시작하지 않았다.

## 감사 명령

ZIP 기반 Validation 감사:

```bash
PYTHONPATH=src python scripts/audit_aihub_pronunciation.py \
  --dataset-root /path/to/dataset-71490 \
  --output-dir artifacts \
  --partition validation \
  --sample-error-count 100 \
  --verify-crc
```

추출 디렉터리 기반 TextGrid target 감사:

```bash
PYTHONPATH=src python scripts/audit_textgrid_targets.py \
  --dataset-root /path/to/dataset-71490 \
  --output-dir artifacts
```

경로는 코드에 하드코딩하지 않으며 CLI로 전달한다.

## 품질 검사

```bash
python -m pytest
ruff check .
ruff format --check .
mypy
```

## 자동 준비 산출물

제한 데이터에서 파생된 다음 로컬 디렉터리는 Git에서 제외한다.

```text
manifest/   # train/dev/test, speaker split, vocabulary, 준비 보고서
processor/  # 16 kHz feature extractor와 43-token tokenizer
```

전체 재현 명령과 수동 감사 이후의 smoke/full 실행 순서는
[다음 단계 문서](docs/NEXT_STEPS_KO.md)에 기록돼 있다.
