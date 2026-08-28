# Korean phone CTC gated baseline

AI Hub 한국어 학습자 발음 데이터의 실제 관측 음소열을 검증하고
XLS-R-300M CTC 진단 연구를 재현하기 위한 독립 작업 디렉터리다.

현재 신뢰 가능한 gate 상태는 `MANUAL_ALIGNMENT_AUDIT_FAILED`다. 독립 수동
정렬 감사 240건 중 178건이 기준에 맞아 정확도 `0.7417`이었고, 요구값
`0.95`를 통과하지 못했다. 현재 학습 CLI는 smoke와 full 모두 이 gate를
우회하지 못한다. 과거 수행된 ungated 내부 연구 실행은 production training이나
gate 통과 근거가 아니다.

## 핵심 문서

- [작업 기록과 실측 결과](docs/WORK_LOG_AND_FINDINGS_KO.md)
- [상태 충돌과 신뢰성 개선 제안](docs/PIPELINE_RELIABILITY_IMPROVEMENT_PROPOSAL_KO.md)
- [HyperCLOVA X 제한 표현 계층 상태](docs/HYPERCLOVAX_INTEGRATION_STATUS_KO.md)
- [과거 다음 단계 계획](docs/NEXT_STEPS_KO.md) — 현재 상태 정본으로 사용하지 않음
- [TextGrid target 정책](configs/textgrid_target_policy.yaml)

## 저장소 루트 공용 문서

삭제된 과거 문서를 복원하거나 추측하지 않는다. 현재 존재하는 다음 문서와
계약만 사용한다.

- [현재 발음 피드백 파이프라인](../docs/architecture/PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)
- [G04 음성 AI 프록시 결과](../docs/evaluation/G04_AUDIO_AI_PROXY_RESULT_2026-08-09_KO.md)
- [연구 데이터 저장 원칙](../data/README.md)
- [저장소 연구·데이터 거버넌스](../.codex/ai_rule_developer/GLOBAL_RULES.md)
- [공개 수동 감사 집계](../docs/audit/aihub71490_manual_alignment_audit_public.tsv)

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
- 240건 독립 수동 정렬 감사 결과는 `178/240`, `0.7417`, `failed`다.
- 이후 Stage-1/Stage-2 내부 연구 실행이 있었지만 manual gate를 통과한 학습은
  아니다.
- 일부 후속 문서는 frozen detector와 one-shot 로컬 artifact의 존재를 보고하지만,
  독립 재실행·정본 승격·실제 학습자 타당성은 확인되지 않았다.

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
[과거 다음 단계 문서](docs/NEXT_STEPS_KO.md)에 기록돼 있다. 해당 문서의 실행
상태 표현은 현재 작업 기록과 신뢰성 제안서보다 오래됐으므로 그대로 완료 근거로
사용하지 않는다.

## 학습 gate

`scripts/train_ctc.py`는 smoke와 full 모두 `status=passed` 및
`alignment_accuracy >= 0.95`인 독립 수동 감사 결과를 요구한다. 과거의
`--allow-ungated-diagnostic-smoke`와 `--allow-ungated-internal-full` CLI 옵션은
제거했다. Python API에 남아 있는 동명 legacy keyword를 `True`로 전달해도
명시적으로 실패한다.

## locked test 접근 경계

`scripts/train_ctc.py`의 smoke/full 경로는 `manifest/train.jsonl`과
`manifest/dev.jsonl`만 연다. dev 레코드는 `split=dev`,
`source_split=dev`인 경우만 허용하고, `locked_test` provenance나
train/dev/vocab 파일·manifest 디렉터리·상위 경로 symlink는 fail-closed로
거부한다. smoke의 반환 건수 제한과 관계없이 두 manifest 끝까지
split/source_split provenance를 검사한다.

`manifest/test.jsonl`은 일반 학습·smoke·checkpoint 선택·재로딩 검증에서
읽지 않는다. 후속 문서가 로컬 one-shot artifact 존재를 보고하더라도 현재 일반
runner의 접근 차단은 유지되며, 그 artifact를 재튜닝이나 production 승격 근거로
사용하지 않는다.
