# WavLM Base+–Large frozen-head 비교 계약

최종 갱신: 2026-07-29 KST
상태: S103 비교기·합성 검증 완료, Base+ 완료·Large 완료 산출물 대기

## 목적

S103은 같은 full cache와 같은 head 학습 계약으로 만든 WavLM Base+와
WavLM Large의 oracle-alignment 개발 결과를 짝지어 비교한다. 비교 결과는
모델 승격을 위한 locked-test 근거가 아니다. Base+/Large 모두 세 seed
42/43/44 학습과 experiment registry 종료를 완료한 뒤에만 실행한다.

구현 파일은 다음과 같다.

- 설정: `configs/experiments/wavlm_frozen_head_comparison_v1.yaml`
- CLI: `scripts/compare_wavlm_frozen_heads.py`
- 비교 모듈: `src/voice_coach/evaluation/wavlm_comparison.py`
- 합성 검증:
  `tests/integration/test_compare_wavlm_frozen_heads.py`

## 실행 전 강제 조건

비교기는 두 source report와 registry에 대해 다음을 모두 검증한다.

1. 두 report가 `completed`, development-only, oracle-alignment다.
2. encoder variant/model ID는 각각 Base+와 Large의 고정 조합이다.
3. Base+ head 설정은
   `9d0b8de8e8f41c66028c4bf895cb653dc77e69b64cdce3b9f1f7d0c59e8e0291`,
   Large head 설정은
   `4d14af8626e2496af5b484fb92f29e51d27f88979ce8a50b6c3a3cda9a9b0e6e`
   로 고정한다. 각 report의 `sources.training_config_sha256`과 registry의
   유일한 `inputs.config` 경로·해시·크기 및 현재 파일 바이트가 모두
   일치해야 한다.
4. full cache row/split, utterance label, primary population, head
   hyperparameter 계약이 같다.
5. 세 seed가 정확히 42/43/44이고 dev 추론은 task별 seed당 한 번이며,
   dev가 checkpoint·threshold·early-stopping 선택에 쓰이지 않았다.
6. source registry가 `completed`이고 실패 기록이 없으며, report hash와
   trainer/head source의 유일한 경로·hash·크기·현재 바이트가 registry
   `inputs.model` 및 고정 config와 정확히 일치한다.
7. 모든 dev prediction Parquet의 hash와 row 수가 report와 일치하고,
   NumPy 변환 전에 trainer writer와 동일한 Arrow column 순서, 타입,
   nullability, 정확한 metadata 및 전체 column null count 0을 강제한다.
   float seed/index/label, 정수 policy flag, null과 추가 metadata도 거부한다.
8. Base+/Large 및 모든 seed에서 발화 ID, 화자 ID, target, label mask와
   prediction row 순서가 정확히 같다.
9. YAML/JSON duplicate key와 문서 어디에 있든 NaN/Infinity를 거부한다.

어느 하나라도 다르면 비교 report를 만들지 않는다.

## 읽기·출력 경계

허용 입력은 완료된 두 head report, 두 registry와 report가 직접 pin한
dev prediction Parquet뿐이다. feature cache, raw manifest/audio, `.env*`,
stack, calibration, locked-test는 읽지 않는다. `raw-`, `raw_`,
`restricted-`, `restricted_`, `stack-`, `stack_`, `calibration-`,
`calibration_`, `locked-`, `locked_` prefix가 report, registry,
prediction 또는 출력 경로 component에 있어도 실행을 거부한다.

출력 report에는 aggregate metric, 검정 결과, source artifact 경로/hash만
기록한다. 발화·화자 hash ID, target/prediction row와 직접 식별자는
기록하지 않는다. report는 config, CLI, 비교 모듈, source report/registry와
12개 prediction artifact의 경로·SHA-256·크기를 기록하고 publish 직전에
다시 확인한다. 비교 config/CLI/module, 두 head report/registry뿐 아니라
registry가 참조하는 config/model/data/checkpoint 전체와 26개 source
artifact도 쓰기 보호 집합에 넣는다. commit, generation report 또는
receipt가 이 중 하나와 충돌하거나 세 출력 경로가 서로 충돌하면 통계 계산
결과를 publish하지 않는다.

report와 execution receipt는 report SHA-256을 generation ID로 사용하는
불변 generation directory에 기록한다. receipt는 report artifact hash,
정확한 전체 source hash chain과 metric/bootstrap/multiplicity payload
hash를 하나의 binding hash로 묶는다. 두 파일을 모두 fsync한 뒤에만
단일 `commit.json` pointer를 원자적으로 교체한다. report/receipt의 파일
또는 directory fsync, commit 임시 파일 fsync, commit rename 전 단계가
실패하면 새 generation을 정리하며 기존 commit은 바뀌지 않는다. commit
rename 성공 뒤 directory fsync가 실패한 경우는 durability가 불확실하지만
이미 선형화된 pointer를 되돌리지 않고 참조 report/receipt를 보존한다.
따라서 이 오류 뒤에도 commit의 경로·SHA-256·크기는 모두 유효하며 재실행
또는 downstream validator로 확인할 수 있다. 다운스트림은 고정 이름의
report가 아니라 이 commit이 가리키고 hash가 일치하는 generation만
신뢰한다.

## metric과 결정 규칙

seed별로 다음 값을 Base+, Large, `Large − Base+` 순서로 계산한다.

- phone: macro F1, AUPRC, event F1
- utterance 세 outcome: QWK, Spearman, MAE, ICC(2,1)
- 세 seed 평균, sample standard deviation, 유효 seed 수

event는 한 발화 안에서 binary positive phone index가 연속된 최대 구간이다.
예측 event와 정답 event가 phone index 하나 이상 겹치면 후보 match로 두고,
결정적 maximum one-to-one matching으로 TP/FP/FN을 계산한다. 정답과 예측
event가 모두 0개인 경우는 정의되지 않은 값이므로 report 생성을 거부한다.

primary 효과는 세 seed의 `Large − Base+ phone macro F1` 평균이다. 화자를
cluster 단위로 짝지어 5,000회 percentile bootstrap하고 95% CI 및
zero-effect 양측 p-value를 기록한다. Large 채택 gate는 point estimate가
절대 `0.03` 이상일 때만 `passed`다. CI 하한이 `0.03` 이상인지 여부는
별도 필드이며 gate status를 바꾸지 않는다.

utterance multiplicity family는 세 outcome의 ICC(2,1) Large−Base+
비교다. 같은 화자 bootstrap 계약으로 raw p-value를 만들고 Holm 방식으로
family-wise error를 보정한다.

## 실제 실행

두 source experiment가 완전히 끝난 뒤 다음 명령을 한 번 실행한다.

```bash
env PYTHONPATH=src PYTHONUNBUFFERED=1 \
  ./.venv-scorer/bin/python scripts/compare_wavlm_frozen_heads.py \
  --config configs/experiments/wavlm_frozen_head_comparison_v1.yaml
```

원자적 commit pointer는 다음 위치다.

```text
artifacts/experiments/aihub71490_wavlm_frozen_head_comparison_v1/commit.json
```

실제 report와 receipt 경로는
`.../generations/<report_sha256>/report.json` 및
`.../generations/<report_sha256>/execution_receipt.json`이며 CLI stdout이
commit과 두 경로를 출력한다. 렌더러는 `commit.json`만 입력받아 receipt,
report와 전체 source hash chain을 다시 검증한 후 논문용 표를 생성한다.

## 완료된 합성 검증

합성 Base+/Large report·registry·prediction을 사용해 다음을 확인했다.

- 정상 비교 report와 최소 hash chain 생성
- Base+/Large ID 불일치 거부
- source leakage flag 거부
- prediction 파일 hash 변조 거부
- Arrow 타입/nullability/null/metadata 변조 거부
- head 설정 pin과 report source hash 불일치 거부
- registry config input 누락·중복·추가·metadata/현재 바이트 변조·경로 이탈
  거부
- registry model input 누락·중복·추가·경로/hash/크기/현재 바이트 변조 거부
- YAML/JSON duplicate key와 non-finite number 거부
- casefold 뒤 `.env`로 시작하거나 다른 금지 prefix가 있는
  config/report/registry/prediction/출력 경로 거부
- config/trainer/head/cache/data/registry input·artifact와
  commit/report/receipt의 충돌을 쓰기 전에 거부
- commit과 계산된 generation report/receipt의 자기충돌을 쓰기 전에 거부
- report/receipt/commit의 파일·directory fsync 전 경계에서 실패를
  주입해 pre-commit 미완료 generation 정리, 기존 commit 보존,
  post-commit 참조 파일·hash 유효성 확인
- 중첩 event의 one-to-one tie 처리
- 같은 bootstrap seed에서 estimate와 CI의 완전 결정성

실제 Base+ 산출물은 완료됐고 Large 산출물은 아직 대기 중이므로 이 문서
작성 시점에는 실제 S103 비교나 GPU 작업을 실행하지 않았다.
