# AI Hub 71490 수동 정렬 감사 절차

기준 시각: 2026-07-28 KST

## 목적과 현재 상태

전체 322,380건의 자동 G0 gate는 통과했다. 최종 G0 고정 전에 독립
전문가가 240개 발화를 청취하고 목표 JSON, 실현 TextGrid, 자동 phone
매핑의 시간 대응이 올바른지 판정한다. 최소 통과 기준은 228/240,
즉 95%다.

## 고정 입력

- dataset root: `data/restricted/aihub/dataset-71490`
- enriched Parquet SHA-256:
  `291097f4c1a62f8c1c86ce7cbedfcb04bc1e1b2f0a0b8958d8cdcd23ee0f25e5`
- worksheet:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv`
- mapping detail:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl`
- selection metadata:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_metadata.json`

표본은 seed `aihub71490-g0-alignment-audit-v1`로 정했다. L1, 성별,
숙련도, 오류 유무로 구성된 관측 50개 층에서 각각 최소 2건을 포함하고
나머지는 모집단에 비례한다. 화자 원시 ID는 worksheet에 포함하지 않는다.

## 검수 절차

직접 worksheet에 원본 ID와 경로를 노출하는 방식보다
[`../LOCAL_ALIGNMENT_REVIEW_UI_KO.md`](../LOCAL_ALIGNMENT_REVIEW_UI_KO.md)의
token 보호 로컬 UI를 기본 검수 경로로 사용한다. worksheet와 상세
JSONL은 제한 입력이며 브라우저·review state·최종 scorer TSV에는
namespaced item/reviewer SHA-256만 남긴다.

1. 로컬 UI의 hash item을 선택하고 그 항목에 한해 stream되는 WAV를
   전체 청취한다.
2. 화면의 target phone, realized phone, 자동 mapping과 시간 경계를
   함께 확인한다. 더 정밀한 확인이 필요하면 서버 머신 안에서만 Praat을
   사용하며 원본 locator를 외부로 복사하지 않는다.
3. 목표·실현 phone과 자동 mapping이 같은 시간 구간을 올바르게
   대응시키는지 발화 전체에서 확인한다.
4. 자동 매핑의 match/substitution/deletion/insertion이 시간상 올바른
   목표·실현 phone을 연결하면 `alignment_correct=true`, 아니면
   `alignment_correct=false`로 기록한다.
5. 사람이 판단한 가장 큰 경계 오차를 ms 단위로
   `maximum_boundary_error_ms`에 기록한다. 판단 불가 사유와 오류 유형은
   `notes`에 쓴다.
6. reviewer pseudonym은 서버 시작 시 namespaced SHA-256으로 바뀐다.
   자동 G0 QC flag는 UI에서 숨겨 검수자의 확인 편향을 줄였다.

긴 발화의 일부만 확인하고 발화 전체를 정상으로 판정하지 않는다. 자동
operation의 종류 자체가 오류의 근거는 아니며, 음성과 두 원천 annotation의
시간 관계를 기준으로 판정한다.

## 결과 계산

검수가 끝나면 로컬 UI의 `export` 명령으로 privacy-safe scorer TSV를
생성한 뒤 채점한다. 미완료 review는 export되지 않는다.

```bash
.venv-scorer/bin/python scripts/score_alignment_audit.py \
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_scoring.tsv \
  --output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json
```

빈 판정, 알 수 없는 판정 값, 중복 utterance, 240건 미만/초과는 실패다.
허용 판정 값은 true/false, yes/no, pass/fail, correct/incorrect 등이다.
결과 JSON의 `status`가 `passed`이고 정확도가 0.95 이상일 때만 G0를
최종 통과로 바꾼다.

## 재생성

manifest가 바뀐 경우 기존 worksheet를 재사용하지 않는다. 다음 명령으로
표본, 상세 매핑, 해시 메타데이터를 함께 다시 생성한다.

```bash
.venv-scorer/bin/python scripts/sample_alignment_audit.py \
  data/processed/aihub71490/manifests/aihub71490_enriched.parquet \
  --output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv \
  --details-output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl \
  --metadata-output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_metadata.json
```

제한 데이터에서 파생된 경로·annotation 상세와 검수 worksheet는 Git에
커밋하거나 외부에 배포하지 않는다. 문서에는 집계 수치와 checksum만
남긴다.
