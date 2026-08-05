# 논문 수치 증거 검증

최종 갱신: 2026-08-04 KST
대상: `docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md`

## 목적

논문에 적힌 실측 수치를 사람이 복사한 값이 아니라 해시가 고정된 실제
집계 artifact와 자동 대조한다. 검증기는 원본 음성·전사·화자 metadata와
`.env*`를 읽지 않는다. `data/raw`, `data/restricted`, `.env*`, 절대경로,
workspace 밖 경로는 설정 단계에서 거부한다.

현재 증거 맵은 다음을 연결한다.

- AI Hub 발화·화자·시간·split·label coverage와 자동 G0 QC
- prompt challenge와 explicit train/dev filtering
- 원어민 4-source object 수와 총 시간
- explicit utterance/phone cache 행 수와 Parquet binary-label 집계
- explicit dev metric, privacy-safe 실패 분석, 사후 threshold ablation
- WavLM Base+ full cache의 행·vector·shard·scalar 전수검증과
  resource/privacy 계약
- WavLM Base+ frozen head의 3-seed phone·발화 집계 metric, 완료
  report/registry, config·trainer·model source hash와 13개 artifact 계약
- WavLM Large frozen head의 3-seed phone·발화 집계 metric과 완료
  report/registry, S103 paired speaker-cluster bootstrap, 사전 등록된
  `+0.03` gate 판정
- S103 comparison report·execution receipt·원자적 commit과 논문용
  Markdown/JSON results-table generation의 전체 hash chain
- R00의 CC0-1.0 고정 문장 20개, coverage·중복·PII·금칙 검사와
  `valid` 독립 validation report
- 격리 clean environment recreation report의 고정 SHA-256
- 논문용 AI Hub 데이터·split·QC 표, cohort flow source·검증 SVG의 고정
  hash
- 자료 라이선스 원문·역할 ledger의 고정 hash와 미해결 권리 경계 보존
- report/config/registry 실제 SHA-256과 registry에 기록된 report SHA-256
- PENDING marker 보존과 explicit/threshold/Base+ head run의
  stack/calibration/locked-test 미접근

## 실행

저장소 루트에서 scorer 환경으로 실행한다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_manuscript_evidence.py \
  --evidence-map docs/paper/evidence-map.yaml \
  --output artifacts/reproducibility/manuscript_evidence_validation.json
```

종료 코드는 통과 `0`, 검증 실패 `1`, 잘못된 evidence-map 설정 `2`다.
실패하더라도 가능한 경우 privacy-safe JSON report를 원자적으로 남긴다.

2026-07-29 S103 표·R00 고정 문장과 검증된 cohort SVG·자료 license
ledger를 canonical 원고/evidence-map에 반영한 뒤의 실제 결과는 다음과
같다.

- artifact: 35/35
- manuscript claim: 475/475
- unresolved PENDING marker: 18개, 고정 marker multiset hash 일치
- locked-test access: explicit dev `false`, threshold ablation `false`,
  WavLM Base+ cache metric evaluation `false`, Base+ head
  stack/calibration/locked-test `false/false/false`, Large head와 S103
  comparison/results도 각각 `false/false/false`
- registry metric split: `dev`, `inner_val`, `dev_exploratory`만 존재
- 전체 상태: `passed`
- report SHA-256:
  `af4a8566050f83c1c5e4cfc5de21ad2e84fceaee0f9a5870d5d10f77c400e92d`
- canonical 원고/evidence-map SHA-256:
  `c21e6d86570eb74069df104295aafe7cf78af02ce51398335c50133a5d1304f2`,
  `edf548f9b439b0c423a2ff15b1d2dc8b22fb6590ef679958a81af306ebb7c1fb`

## Evidence-map 계약

각 artifact는 workspace 상대경로와 SHA-256을 가진다. 단일 파일은 파일
SHA-256, phone Parquet shard 디렉터리는 정렬한
`relative_path + NUL + file_sha256 + LF` 레코드의 SHA-256으로 고정한다.

각 claim에는 다음 정보가 있다.

- 안정적인 claim ID
- artifact alias
- JSON Pointer, Parquet aggregate 또는 artifact SHA-256 source
- 원고의 named capture `(?P<value>...)` 정규식
- 허용 소수 자릿수 또는 exact 비교 규칙

수치 비교는 artifact 값에 명시한 변환을 적용한 뒤 decimal
`ROUND_HALF_UP`으로 반올림한다. NaN/Infinity, artifact 누락, 해시 변경,
정규식 누락·중복 match와 허용 반올림 밖 값은 모두 실패한다. Parquet는
row count, file count, 특정 label count/ratio 집계만 읽으며 행 식별자를
보고서에 내보내지 않는다.

PENDING 계약은 현재 marker 전체의 개수와 정렬된 marker multiset
SHA-256을 함께 고정한다. 실제 artifact가 없는 항목의 marker를 지우거나
임의 숫자로 바꾸면 검증이 실패한다. locked-test guard는 report의 접근
flag, registry split, 원고의 금지 metric 정규식을 함께 검사한다.

## 새 결과를 논문에 추가할 때

1. privacy-safe aggregate report와 registry를 먼저 완성한다.
2. artifact 파일의 SHA-256을 확인해 evidence map에 새 alias를 추가한다.
3. JSON Pointer 또는 Parquet aggregate와 원고 정규식, 반올림 자릿수를
   가진 claim을 추가한다.
4. 실제 artifact가 없는 결과는 숫자를 쓰지 않고 PENDING marker를
   유지한다. marker를 바꿨다면 count와 marker hash도 검토 후 갱신한다.
5. CLI를 재실행해 모든 artifact/claim/PENDING/locked-test guard가
   통과하는지 확인한다.
6. 최종 PDF 생성 전 별도로 인용·표·그림 렌더링과 독립 감사를 수행한다.

해시가 바뀌었다는 이유만으로 evidence map의 값을 자동 교체하지 않는다.
해당 artifact가 의도한 재실행 결과인지 먼저 검토하고, 논문 수치와 registry
lineage를 함께 갱신한다.
