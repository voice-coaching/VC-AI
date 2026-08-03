# WP8 E00 폐루프 데모 production STATIC readiness

기준일: 2026-07-29 KST  
판정: **정적 구현 준비 완료 / 실제 E00 0%**

## 1. 결론

Attempt 1부터 actual S108 진단, E02 gate, C02 top-1 catalog coaching,
licensed R03/D08 reference, attempt 2와 same-scorer comparison까지의
fail-closed 정적 계약을 구현했다.

실제 input, audio, model, GPU, API, coaching, reference, rerecord, metric,
result, output은 모두 0이다. 이 package는 session을 실행하거나 E00 완료,
교육 효과, 임상 효과 또는 production deployment를 주장하지 않는다.
정본 tracker, 기존 G04 UI/state와 locked locator는 수정하지 않았다.

## 2. 구현 artifact

| artifact | SHA-256 |
|---|---|
| `configs/experiments/e00_closed_loop_demo_v1.yaml` | `1bbf349a1229aff8a8b58335cc3118bd3d27850ef20b6e6374ea79e35a14318b` |
| `configs/demo/e00_closed_loop_completion_v1.schema.json` | `2341fde03ad5d0746d1acd5bf289cf838d2ea024229f835c18732fcc641f18f0` |
| `configs/demo/e00_closed_loop_preregistration_v1.template.json` | `5cd175a577ba792359968ca25433263747a66ea56d14d567c1c99ebd7a2786ce` |
| `configs/demo/e00_closed_loop_session_v1.schema.json` | `842d54b5bf1b881a18497f3de902b1ecb3101042aa7fe34da36a0dc50ec327ac` |
| `configs/demo/e00_closed_loop_trace_v1.schema.json` | `265ddd81d8a11a8d9b033f705fe4d3ce95b2ef24fe27414f7548d695799f1196` |
| `configs/demo/e00_execution_authorization_v1.schema.json` | `958039509bf0a0e1fe76ea77910d93f528046509a7d2dc4e3330b30d96ff99bd` |
| `configs/demo/e00_execution_authorization_v1.template.json` | `816b82489d45badcd5208ce6f829acde9f1f02da494b87b66d8bfbcb2b724d98` |
| `configs/demo/e00_d08_r03_lineage_receipt_v1.schema.json` | `4746cf2b9b0da14d235f91bf347f4c02757f1fde9ef3e70d9a6a1f76c5f068ad` |
| `configs/demo/e00_reference_handoff_v1.schema.json` | `675a8a149f99f31e48bd6eef1cae92437c00c6a3a017f64f66e4d15ba991761e` |
| `configs/demo/e00_s108_diagnostic_handoff_v1.schema.json` | `b33cfd7aabb459fb1f2b7784e5de235c3d935619982084a16a44f856297151ca` |
| `src/voice_coach/evaluation/e00_closed_loop_contract.py` | `9f846ae86f1a7a14f3f400224dee08f9b4bfeda5db27377f7dab157b5cd4a75f` |
| `scripts/prepare_e00_closed_loop_demo.py` | `8ff23c5b8c8cce54ea1229ccef722d88f3aad71e5999bb5f4b389222d14981f2` |
| `tests/unit/test_e00_closed_loop_contract.py` | `8dbf1d67f9d2ad03a51ea403b1ee4a3a3599387f9f531c97de75db95263c3fef` |
| `tests/integration/test_prepare_e00_closed_loop_demo.py` | `0153d572572ff1d8d3940fb9ca1e71e59c2c4bb78f3f838ef7a5e95b6a5a53c2` |
| `docs/evaluation/E00_CLOSED_LOOP_DEMO_DATA_MODEL_PRIVACY_SAFETY_STATISTICS_CARD_KO.md` | `3017cfd732703e9699dc0549dfdccdc7bf17b84c2780b32ff36f4f2bde5cc3fd` |

Readiness/card 문서는 canonical config source pin에 넣지 않아 config↔문서
hash 순환 의존을 만들지 않았다.

## 3. 고정 선행 gate

Full preflight 순서는 다음과 같다.

1. E00 static source/schema/template와 zero-runtime 정책
2. actual S108 final production model completion
3. E01 completed pass
4. C02 actual deterministic bundle와 frozen E02 guard
5. C03 expert audit pass와 production-release permission
6. R03 private reference bank
7. D08 aggregate completion·consent·recording·QC·rights
8. frozen E00 preregistration
9. human H00+E00 scope+individual consent 또는 nonhuman learner fixture
10. exact D08 metadata-row↔R03 candidate lineage receipt
11. licensed reference handoff
12. 별도 restricted executor 후보

앞선 gate가 unresolved이면 뒤 path를 resolve하지 않는다. Canonical
full은 첫 blocker인 S108 final에서 종료된다.

## 4. 폐루프와 safety branch

Eligible branch는 정확히 다음 순서다.

```text
attempt1
  -> actual S108 production handoff
  -> E02 guard
  -> C02 catalog top-1
  -> licensed D08-origin R03 reference
  -> attempt2
  -> same scorer comparison
```

- Locked test result copy/replay는 실제 S108 handoff를 대체할 수 없다.
- C02는 catalog item 1개이며 free generation과 backfill이 없다.
- Attempt 1/2는 같은 model, checkpoint, calibrator, threshold policy다.
- OOD, low confidence, unsupported, abstain-all은 모든 전달과 attempt 2를
  차단하고 `rerecord`/`expert_review` code만 허용한다.
- No-issues는 no-correction safe exit다.
- Oracle endpoint는 production flow에 사용하지 않는다.

## 5. D08↔R03 계보 보강

R03 후보가 `human_recording`이라는 이유만으로 D08 후보라고 간주하지
않는다. 별도 lineage receipt가 다음을 exact cross-match해야 한다.

- selected R03 candidate canonical row
- target, audio object와 audio content
- D08 completion과 metadata manifest
- exact restricted D08 metadata row hash와 recording audio
- aggregate D08 rights attestation
- exact per-recording license grant

Candidate/source/D08 audio와 rights가 모두 일치해야 한다. 독립 R01 human
candidate를 D08로 추정하는 flag는 반드시 false다. Speaker와 locator는
receipt/reference handoff에 들어가지 않는다.

Unit test는 D08 recording audio mismatch, per-recording rights mismatch와
독립 R01=D08 추정을 각각 fail-closed하는지 확인한다.

## 6. Fixture와 licensed human reference 범위

`nonhuman_test_fixture`의 `contains_human_voice=false`는
`learner_attempt_input_fixture_only` 범위다. Session 전체에 인간 음성이
없다는 뜻이 아니다.

Reference는 fixture authorization 밖의 별도 동의·허가된 D08 인간
음성이다. `licensed_d08_human_reference_outside_fixture_scope=true`와
별도 lineage/rights gate를 동시에 요구한다.

Human mode는 actual H00 hash만으로 통과하지 않는다. E00 scope/amendment,
individual consent, withdrawal/deletion과 human escalation을 별도로
결합한다.

## 7. Preregistration과 통계 경계

문서 근거가 없는 다음 값은 actual audio/session 접근 전에
`approved_and_frozen` preregistration으로 정한다.

- exact trace count와 target selection
- minimum support
- paired metric과 missing/failed attempt 처리
- exact effect estimand
- CI와 multiplicity
- pedagogical threshold
- clinical·deployment claim

E00은 noninferential demo다. Same-scorer paired comparison이 존재해도 교육
효과, 임상 효과 또는 일반화 성능을 주장할 수 없다. Completion schema는
effect와 CI를 받지 않으며 claim을 false로 강제한다.

## 8. Canonical config-only 결과

Config-only는 C1/static source 50개를 exact SHA-256으로 읽는다.

- source pin read: 50/50
- source TOCTOU verification: 0
- predecessor module/path/read/deep validation: 0
- preregistration/H00/authorization/lineage/reference/session path/read: 0
- input/raw audio/microphone/model/inference/GPU/network/API: 0
- coaching/reference/rerecord/metric/result: 0
- output path/write/registry/commit/publication: 0
- artifact created: false
- completion/deployment claim: false

## 9. Canonical full fail-closed 결과

Full preflight는 static source 50개를 읽고 같은 50개를 TOCTOU 재검증한 뒤
다음으로 종료된다.

```text
status = failed_closed
stage = s108_final
error = S108 completion commit SHA is TBD_S108_COMPLETION
```

이 시점 counter는 다음과 같다.

- source pin read/TOCTOU: 50/50
- S108 path resolution/read/deep validation: 0/0/0
- E01/C02/C03/E02/R03/D08 path/read/deep validation: 모두 0
- E00 preregistration/H00/authorization/lineage/reference/session: 모두 0
- input/audio/model/GPU/network/API/delivery/rerecord/metric/result: 모두 0
- output path/write/registry/commit/publication: 모두 0

즉 unresolved SHA를 실제 path 존재 여부 확인으로 우회하지 않는다.

## 10. 테스트와 정적 품질

E00 targeted 검증:

- unit: 17 passed
- integration: 3 passed
- 합계: 20 passed
- S108/E01/C02/C03/E02/R03/D08/H00 연동 회귀: 222 passed
- repository 전체: 1,128 tests collected, all passed
- Ruff lint: passed
- Ruff format check: passed
- mypy contract/CLI: no issues
- `git diff --check`: passed

Unit은 strict schema, 50 source pin, prereg TBD/비추론 경계, fixture/human
authorization, actual S108와 locked replay 차단, same-scorer, eligible 및
네 blocked branch, no-correction, exact D08↔R03 audio/rights lineage,
D08 A1 privacy와 completion count를 확인한다.

Integration은 config-only success, S108 최초 blocker와 help의
nonexecution/lineage/fixture-reference scope를 확인한다.

## 11. 다음 실제 실행 순서

1. S108 final actual candidate와 immutable completion을 발급한다.
2. E01 actual pass를 발급한다.
3. C02 actual deterministic bundle, frozen E02 policy와 C03 expert pass를
   완성한다.
4. R03 private bank와 D08 actual recording/QC/rights completion을 완성한다.
5. Exact D08 metadata row와 R03 candidate를 제한 영역에서 교차 확인하고
   lineage receipt를 발급한다.
6. Exact sample/metric/missingness/statistics를 E00 preregistration으로
   동결한다.
7. Human이면 H00 외에 E00 scope와 개인별 consent를, fixture면 learner
   attempt input 전용 nonhuman authorization을 발급한다.
8. 별도 restricted executor에서 eligible/blocked branch를 실행한다.
9. 독립 검증 후 score-free trace와 A1 completion만 승격한다.
10. 논문에는 preregistered 범위에서 실제 관측된 결과만 채운다.

그전에는 actual audio/model/API/delivery/rerecord/metric/output을 수행하거나
E00 완료를 주장할 수 없다.
