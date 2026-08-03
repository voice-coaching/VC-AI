# WavLM 결과 표 렌더링 계약

최종 갱신: 2026-07-29 KST
상태: 렌더러·synthetic 검증과 실제 Base+ 입력 완료, Large/S103 결과 대기

## 목적

`scripts/render_wavlm_results_table.py`는 실험을 실행하거나 metric을 새로
계산하는 프로그램이 아니다. 다음 세 결과가 모두 완료된 뒤 검증된 집계값만
논문용 Markdown 표와 aggregate JSON으로 옮긴다.

1. Base+ frozen-head 3-seed `report.json`과 완료 experiment registry
2. Large frozen-head 3-seed `report.json`과 완료 experiment registry
3. 두 모델의 동일 dev prediction을 짝지은 S103 comparison commit

세 실제 결과가 모두 존재하기 전에는 최종 비교 표나 JSON을 만들지 않는다.
이 문서에도 placeholder 또는 예시 성능값을 기록하지 않는다.

## 입력 gate

렌더러는 다음 조건 중 하나라도 어긋나면 출력 없이 실패한다.

- head report schema가
  `voice_coach.wavlm_frozen_head_report.v1`이고 상태가 `completed`
- Base+/Large 모두 seed `42/43/44`가 정확히 한 번씩 완료됨
- 결과가 `development_only=true`, `oracle_alignment=true`
- stack/calibration/locked-test 접근이 모두 false이며 unread 요약이 true
- dev는 선택에 사용되지 않았고 registry의 metric split도 dev뿐임
- 두 registry가 `completed`이고 seed, source input, metric, 13개 artifact가
  모두 등록됨
- report에 기록된 trainer/head/config SHA-256이 registry 및 실제 파일과
  일치함
- checkpoint, slice report, phone/utterance prediction 및 최종 head report
  SHA-256이 registry와 실제 파일에 모두 일치함
- S103 schema가 `voice_coach.wavlm_frozen_head_comparison.v1`
- S103 commit이 같은 불변 generation의 report와 execution receipt
  경로·SHA-256·크기를 정확히 고정함
- receipt binding이 report artifact hash, 두 head report·registry·모든
  prediction과 정확한 comparison config/script/module hash chain을 고정함
- S103 구현 config/script/module SHA-256이 실제 파일과 일치함
- seed별 macro F1/AUPRC와 모든 utterance metric이 원 head report와 일치함
- event F1이 집계 event TP/FP/FN으로 재계산한 값과 일치함
- seed별 값에서 다시 계산한 mean/sample standard deviation이 S103
  aggregate와 일치함
- paired speaker bootstrap이 5,000회, 95% CI, seed 42 계약을 따름
- S103 gate가 `Large − Base+ phone macro F1 ≥ 0.03`의 point estimate
  판정과 일치함
- CI lower bound의 0.03 이상 여부는 별도 보수적 지지 flag로 일치함
- utterance ICC(2,1) 세 outcome의 Holm 보정과 `adjusted p ≤ 0.05`
  판정이 일치함
- report의 CI/bootstrap/p-value 또는 receipt/source bytes가 실행 후
  변경되면 commit/receipt hash 검증 단계에서 거부됨

## 출력

Markdown에는 다음 표만 포함한다.

- Base+, Large, Large−Base+의 seed별 phone macro F1/AUPRC/event F1
- 세 seed의 mean ± sample standard deviation
- 세 utterance outcome의 seed별 QWK/Spearman/MAE/ICC(2,1)과 집계
- Large−Base+ phone macro F1 paired estimate, 95% CI, 0.03 gate

JSON은 같은 수치, 입력 보고서·S103 report/receipt/commit SHA-256, 정책
flag만 포함한다. run ID, 발화·화자·phone 식별자와 prediction row는
출력하지 않는다.

Markdown과 JSON은 content hash generation ID 아래 불변 파일로 먼저
기록한다. 두 파일의 경로·SHA-256·크기와 입력 S103 commit hash를 고정한
단일 output commit manifest를 마지막에 원자적으로 교체한다. 두 번째
출력의 파일/directory fsync 또는 commit rename 전 단계가 실패하면 새
generation을 정리하고 이전 commit을 유지한다. commit rename 성공 뒤
directory fsync가 실패한 경우는 durability가 불확실하므로 이미 선형화된
pointer와 그 pointer가 고정한 Markdown/JSON을 보존한다. 이 경우에도 두
참조 파일의 경로·SHA-256·크기는 유효하며 재실행 또는 commit loader로
검증할 수 있다. 다운스트림과 논문 반영 절차는 base 출력 이름을 직접 읽지
않고 output commit이 가리키는 두 hash-matching 파일만 읽는다. 검증
실패 시 실패 placeholder를 만들지 않는다.

렌더러는 raw/restricted/stack/calibration/locked-test 또는 casefold 뒤
`.env`로 시작하는 모든 component와 각 보호 이름의 `-`/`_` prefix를
입력, prediction, 출력 어디서든 거부한다. 양쪽 report/registry와 26개
artifact, registry의 config/model/data/checkpoint 전체, S103
commit/report/receipt 및 comparison config/script/module을 하나의 전이
쓰기 보호 집합으로 만들고 Markdown/JSON/commit과 실제 generation
경로가 하나라도 충돌하거나 commit이 계산된 generation 출력과 충돌하면
쓰기 전에 거부한다. feature cache와 prediction
내용은 읽지 않고, 허용된 commit, receipt, report, registry, source와
artifact 바이트를 SHA-256 검증에만 사용한다. 모든 JSON은 duplicate
key와 NaN/Infinity를 허용하지 않는다.

합성 fault-injection은 Markdown, JSON, output commit 각각의 파일 fsync와
rename 뒤 directory fsync를 모두 중단시켜 pre-commit orphan 부재와 기존
commit 보존, post-commit pointer·참조 파일·hash 일치를 확인한다. 또한
checkpoint, training config, trainer/head source, registry data,
comparison receipt/config/module에 output을 겹치게 한 모든 경우를
publication 전에 거부하는지 확인한다.

## 실행 준비

실제 Base+/Large head와 S103 comparison commit이 모두 생성된 뒤에만 각
입력의 canonical 경로를 CLI 인자로 전달한다. 현재 인터페이스는 다음
명령으로 확인한다.

```bash
.venv-scorer/bin/python scripts/render_wavlm_results_table.py --help
```

실제 실행 명령은 다음과 같다.

```bash
env PYTHONPATH=src PYTHONUNBUFFERED=1 \
  ./.venv-scorer/bin/python scripts/render_wavlm_results_table.py \
  --base-report artifacts/experiments/aihub71490_wavlm_base_plus_frozen_head_v1/report.json \
  --base-registry artifacts/experiment_registry/aihub71490-wavlm-base-plus-frozen-head-v1-dev/metadata.json \
  --large-report artifacts/experiments/aihub71490_wavlm_large_frozen_head_v1/report.json \
  --large-registry artifacts/experiment_registry/aihub71490-wavlm-large-frozen-head-v1-dev/metadata.json \
  --comparison-commit artifacts/experiments/aihub71490_wavlm_frozen_head_comparison_v1/commit.json \
  --output-markdown docs/paper/generated/wavlm_frozen_head_results_table.md \
  --output-json artifacts/experiments/aihub71490_wavlm_frozen_head_comparison_v1/results_table.json \
  --output-commit artifacts/experiments/aihub71490_wavlm_frozen_head_comparison_v1/results_table.commit.json
```

고정 pointer는 `results_table.commit.json` 하나다. 실제 Markdown/JSON은
각 base 출력 경로의 상위 directory 아래
`generations/<content_sha256>/...`에 기록되며 CLI stdout이 확정 경로를
출력한다.

렌더링 성공 뒤에는 output commit, aggregate JSON과 comparison
report/receipt/commit SHA-256을 evidence map에 등록하고, commit 검증
후에만 논문 본문의 해당 PENDING 표를 교체한다. 현재 Base+는 완료됐지만
Large와 S103은 대기 중이므로 실제 렌더러는 아직 실행하지 않는다.
