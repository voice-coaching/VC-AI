# Korean phone CTC gated baseline

AI Hub 한국어 학습자 발음 데이터의 실제 관측 음소열을 검증하고
XLS-R-300M CTC smoke training으로 넘어가기 위한 독립 작업 디렉터리다.

현재 상태는 `review_required`이며 모델 학습은 비활성화돼 있다.

## 핵심 문서

- [작업 기록과 실측 결과](docs/WORK_LOG_AND_FINDINGS_KO.md)
- [다음 단계와 재개 gate](docs/NEXT_STEPS_KO.md)
- [TextGrid target 정책 초안](configs/textgrid_target_policy.yaml)

## 확인된 사실

- 요청서의 dataset ID 71479와 실제 서버 dataset 71490이 불일치한다.
- JSON `phonemic.phones`는 기대 발음열이다.
- TextGrid phone tier가 실제 교체와 삭제를 기록한다.
- 공식 Validation 화자 1,812명 전원이 Training과 겹친다.
- TextGrid에는 357종의 미해결 raw label이 남아 있다.
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

manifest, processor 또는 smoke training은
`configs/textgrid_target_policy.yaml`의 검토가 완료되기 전에는 만들거나
실행하지 않는다.
