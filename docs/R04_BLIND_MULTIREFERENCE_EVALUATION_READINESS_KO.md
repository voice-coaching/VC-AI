# R04 블라인드 다중 참조 인간 평가 STATIC readiness

## 1. 결론

R04의 production STATIC 계약, 승인 템플릿, 블라인드 randomization/rating 스키마, read-only CLI, synthetic tests를 구현했다. 정적 설정은 유효하지만 실제 평가 준비 완료는 아니다.

- 정적 설정: 통과
- full preflight: R00 deep validation 후 R01 `TBD_R01_COMPLETION`에서 fail closed
- R02/R03 completion 접근: 0
- 인간승인/preregistration/pilot/SAP/listener attestation 접근: 0
- D1 audio/rating 경로 해석: 0
- output 해석/생성: 0
- 실제 listener/audio/model/GPU/rating/metric/statistics/result: 0
- 현재 완성도: `C=1, H=0, M=0`

## 2. 근거와 고정 규칙

검토 근거:

- `docs/EXTERNAL_EVALUATION_EXECUTION_PACK_KO.md` §5
- `docs/IMPLEMENTATION_PLAN_KO.md` Phase 5
- R00/R01/R02/R03 final static contract와 R03 final pin

고정한 규칙:

- human reference와 R02 hard-gated TTS를 함께 포함
- R02 실패 후보는 MOS로 복구 불가
- system명, filename/path, 합성 여부, source label 은폐
- 청취자별 문장 내 무작위화
- 피로 block과 의무 휴식
- MOS unit `listener×sentence×candidate`
- MOS 1–5, naturalness/clarity/target-phone accuracy/pedagogical suitability
- CMOS 같은 문장 pair, -3–3
- primary ordinal mixed-effects, listener/sentence random effects
- bootstrap secondary
- power 0.80, 양측 alpha 0.05
- raw D1, aggregate A1
- PII·자유서술·identity/path/source-label blind leakage 금지

## 3. 생성한 계약 파일

| 파일 | 역할 | SHA-256 |
|---|---|---|
| `configs/experiments/r04_blind_multireference_evaluation_v1.yaml` | canonical static policy와 순서 | `991748409824114d326516f6a61e8f2828c86069fc32869adeec4d01390dbf8f` |
| `configs/reference/r04_human_research_approval_v1.template.json` | 인간 연구 승인 | `8908faa1fca5b57967429e540367b863c322d9969d887c45df1f5415ef28e961` |
| `configs/reference/r04_blind_evaluation_preregistration_v1.template.json` | 평가 preregistration | `34e89b372a1bd1de662cebcefae635655dc47722d4813372f02db8e30bcdbc80` |
| `configs/reference/r04_pilot_nuisance_attestation_v1.template.json` | pilot 분산·상관 증거 | `6b0d8cea7732a49cc12ae233c89a8500f540cc3dec0a70aae2d620fbf73c84e8` |
| `configs/reference/r04_statistical_analysis_plan_v1.template.json` | frozen SAP | `0c937ed0798f90f9dc415b342a3f6d28e4eccf5917eba94c511a6f4ceddf96b1` |
| `configs/reference/r04_listener_attestation_v1.template.json` | listener consent/eligibility/환경 증거 | `991ff3626d89a19930f22bda38763df2f42148345c77ce797ab7e9214d46432a` |
| `configs/reference/r04_randomization_manifest_v1.schema.json` | strict D1 randomization schema | `1d76bfac83317eea307740f0d137f1e6fd1b926cb2e3d40187b30402caef1a32` |
| `configs/reference/r04_randomization_manifest_v1.template.json` | fail-closed randomization template | `6651e7fc3c65aaa89aa8a924053bdfa19456f72a03e7d05a6c47ac3703fa2eae` |
| `configs/reference/r04_rating_manifest_v1.schema.json` | strict D1 MOS/CMOS schema | `510f0063048a5cbb0da6312d06949c4c059f0c8d39415a5a7063213c61d3b09f` |
| `configs/reference/r04_rating_manifest_v1.template.json` | fail-closed rating template | `c7a2919ab1b5f091a0cec27b4895fc24df57c7c48066d639e9edfb6ba3fad008` |
| `src/voice_coach/references/r04_blind_evaluation_contract.py` | strict read-only contract | `8bcb3d405a72e0f69c37b21e28f6e2634754be15fb527f5e9046720db8f49b7c` |
| `scripts/prepare_r04_blind_multireference_evaluation.py` | config-only/full CLI | `6dd7c7b813889e5cf007825dac0afc69a2a49d19d4f1b55c8f12d2e90647a36e` |
| `tests/unit/test_r04_blind_evaluation_contract.py` | synthetic contract tests | `828428c732e23ed5e020ac4598dfc4e7a857522d8c9b05de6ca6e7191cba5d8a` |
| `tests/integration/test_prepare_r04_blind_multireference_evaluation.py` | CLI 경계 tests | `f23defe06dded6e048e759e8262fa3e7c798026758ea8c1904cd0a40e385ce68` |
| `docs/R04_BLIND_EVALUATION_DATA_MODEL_PRIVACY_STATISTICS_CARD_KO.md` | 데이터·모델·개인정보·통계 카드 | `e7e5be7bab7a4de18ccddcbe6c6af388b5356459670c396d169c0f4d0f1c1f03` |

## 4. 실행 순서

계약은 다음 순서를 바꿀 수 없게 검증한다.

1. R04 static source/template/schema 검증
2. R00 valid completion deep validation
3. R01 completed candidate generation deep validation
4. R02 completed hard gate deep validation 및 failure rescue 금지
5. R03 completed reference bank와 R01/R02 eligible lineage deep validation
6. 인간 연구 승인
7. frozen R04 preregistration
8. 승인된 pilot variance·intra-listener correlation
9. frozen SAP
10. 모든 listener attestation
11. D1 audio/randomization/rating 경로 전에 정지

canonical full preflight는 2번을 실제로 재검증한 뒤, 3번의 completion SHA가 `TBD_R01_COMPLETION`이므로 path를 해석하거나 읽지 않고 정지한다.

## 5. CLI

정적 검증:

```bash
PYTHONPATH=src python scripts/prepare_r04_blind_multireference_evaluation.py --config-only
```

예상:

- exit 0
- `status=valid_config_only`
- source pin read 25
- predecessor/D1/output/runtime count 0

predecessor-first full preflight:

```bash
PYTHONPATH=src python scripts/prepare_r04_blind_multireference_evaluation.py
```

현재 예상:

- exit 1
- `status=failed_closed`
- `stage=r01_completion`
- `error=R01 completion commit is TBD_R01_COMPLETION`
- R00 deep validation 1
- R00 public/aggregate evidence read 10
- R01 completion path/read 0/0
- 모든 R02 이후·승인·D1·runtime·output count 0

## 6. 검증 evidence

실행한 검사:

```bash
.venv-scorer/bin/ruff check \
  src/voice_coach/references/r04_blind_evaluation_contract.py \
  scripts/prepare_r04_blind_multireference_evaluation.py \
  tests/unit/test_r04_blind_evaluation_contract.py \
  tests/integration/test_prepare_r04_blind_multireference_evaluation.py

.venv-scorer/bin/mypy \
  src/voice_coach/references/r04_blind_evaluation_contract.py \
  scripts/prepare_r04_blind_multireference_evaluation.py

.venv-scorer/bin/pytest -q \
  tests/unit/test_r04_blind_evaluation_contract.py \
  tests/integration/test_prepare_r04_blind_multireference_evaluation.py
```

현재 결과:

- Ruff: 통과
- Ruff format check: 통과
- mypy: 통과
- targeted pytest: `19 passed`
- R00–R04 unit/integration regression: `107 passed`

테스트 fixture는 전부 합성 ID·hash·rating 값이다. 실제 listener/audio/rating/result를 읽지 않는다.

## 7. 현재 blocker

순서상 blocker:

1. `TBD_R01_COMPLETION`
2. `TBD_R02_COMPLETION`
3. `TBD_R03_COMPLETION`
4. R03 completion/registry exact schema version 미정
5. 인간 연구 승인 `TBD_EXTERNAL`
6. R04 preregistration `TBD_PREREGISTRATION`
7. pilot variance·intra-listener correlation `TBD_EXTERNAL`
8. listener N, exact estimand/link/CI/bootstrap/multiplicity/superiority margin·comparator/rater/adjudication `TBD_PREREGISTRATION`
9. SAP `TBD_PREREGISTRATION`
10. listener attestations와 D1 audio/rating access attestation `TBD_EXTERNAL`

이 항목을 임의 수치나 추정치로 채울 수 없다.

## 8. 미래 executor 인계 조건

- R01/R02/R03 completion artifact가 생성되면 config와 predecessor static pin을 함께 갱신
- R03 bank의 문장별 3–5개, human+TTS, R02 eligible-only immutable lineage를 deep validation
- 승인 artifact는 SHA-256과 no-TBD 상태로 동결
- randomization 생성 전 preregistered block 크기와 같은 문장 최대 연속 수를 전달
- rating 수집 전 모든 listener ID를 attestation과 결합
- 미래 output은 `ensure_future_output_targets_absent`로 traversal/symlink/no-overwrite 확인
- 같은 디렉터리 임시 파일→fsync→원자 rename 방식으로만 publish
- raw D1과 aggregate A1을 물리적으로 분리
- 분석 후에도 별도 promotion 전 publication 불가

R04 실제 completion schema/API는 현재 만들지 않았다. 실제 분석계획과 superiority decision rule이 승인되지 않은 상태에서 completion 형식을 선고정하면 결과 선택을 유도할 수 있으므로 후속 단계는 `TBD_R04_COMPLETION`으로 유지해야 한다.

## 9. tracker 정책

R03 log 59가 다른 작업에서 마감 중이므로 이 작업에서는 `docs/RESEARCH_COMPLETION_TRACKER_KO.md`를 수정하지 않았다. R04 tracker 반영은 상위 작업자가 최종 해시와 회귀 evidence를 확인한 뒤 수행해야 한다.
