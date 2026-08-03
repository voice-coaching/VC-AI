# WP8 E01 metamorphic robustness production 정적 준비도

상태: **production 정적 package 구현 완료 / 실제 E01 실행 0**

기준일: 2026-07-29 KST

## 1. 구현 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/e01_metamorphic_robustness_v1.yaml` | S108-first, five-family TBD, fixed 0.95, privacy/runtime/output 계약 |
| `configs/reference/e01_metamorphic_input_manifest_v1.schema.json` | strict D1 non-locked input manifest schema |
| `configs/reference/e01_metamorphic_report_v1.schema.json` | strict A1 five-family aggregate report schema |
| `configs/reference/e01_metamorphic_preregistration_v1.template.json` | magnitude/relation/calculation TBD fail-closed template |
| `src/voice_coach/evaluation/e01_metamorphic_contract.py` | static/deep/input/report/bundle read-only validator |
| `scripts/prepare_e01_metamorphic_robustness.py` | config-only와 read-only full preflight CLI |
| `tests/unit/test_e01_metamorphic_contract.py` | policy/schema/privacy/integrity synthetic tests |
| `tests/integration/test_prepare_e01_metamorphic_robustness.py` | canonical runtime0와 earliest-S108 failure |
| `docs/E01_METAMORPHIC_DATA_MODEL_SAFETY_CARD_KO.md` | data/model/license/safety 경계 |
| `docs/E01_METAMORPHIC_READINESS_KO.md` | 본 증거 문서 |

Tracker, S108/S2 기존 파일과 원고는 수정하지 않았다. Actual perturbation,
audio/model loader, inference/metric executor와 publisher는 만들지 않았다.

## 2. 근거 감사

Canonical 실행계획과 기존 S203 non-holdout 계약이
`metamorphic_expectation_pass_rate >= 0.95`를 명시하므로 threshold만
고정했다.

Silence/gain/duration/F0/phone operation family는 문서에 있지만 exact
magnitude와 expected invariance/monotonicity는 없다. Numerator,
denominator, aggregation, weighting, support, invalid/missing, gate
statistic, CI와 multiplicity도 없다. 이 값은 config/template에서 모두
`TBD_PREREGISTRATION`이다.

## 3. 실행 순서와 current failure

Full preflight 순서는 다음과 같다.

1. E01 C1 config/source/input-output schema/template exact pin
2. actual S108 final selected-model completion deep validation
3. fully resolved and frozen E01 preregistration validation
4. input manifest/audio/model/output path resolve 전 정지

Canonical S108 completion/model/checkpoint/calibrator/threshold hash는
`TBD_S108_COMPLETION`이다. 따라서 full preflight는 source validation 후
`stage=s108_final`에서 commit path를 resolve하지 않고 종료한다.

Config-only는 source pin 14개만 읽는다.

- predecessor/preregistration path·read: 0
- input manifest/case/raw audio: 0
- model path/weight/inference/network/GPU: 0
- metric/decision: 0
- locked test/S203 expert holdout: 0
- output path/write/publication: 0

## 4. Schema와 fail-closed 계약

D1 input validator는 정확히 다섯 family coverage, opaque hash, unique case,
duration/phone target-region requirement와 non-locked marker를 강제한다.
Audio/model path, raw audio와 protected split 표시는 거부한다.

A1 report validator는 다음을 강제한다.

- five-family fixed order와 허용 relation type
- family/overall case accounting과 count-derived pass rate
- overall family 합계 일치
- fixed threshold 0.95의 inclusive pass/fail
- blocked일 때 missing gate statistic
- non-finite/reversed CI 거부
- row/case/item/speaker/audio ID·path·free text 거부
- locked/S203 access count 0와 publication false

Future bundle validator는 report→registry→commit path/hash/schema/status/
binding, registry exact artifact, source 재검사, no-overwrite, normalized
path, symlink/hash/TOCTOU를 synthetic fixture로 검증한다.

## 5. 검증 결과

- E01 unit + integration: **31 passed**
- S108/S200–S203/E01 관련 회귀: **139 passed**
- Ruff check/format: **passed**
- mypy contract/CLI: **passed**

검증 시점 SHA-256:

| 파일 | SHA-256 |
|---|---|
| config | `faf828027ba12ceb1c0c78244900eeec2e8198e22588e77fbe20a4fd8ecbca9a` |
| input schema | `4b3459751aacb225a2637f80a90562809c8de840ab4e520dc07180b5c070d73d` |
| report schema | `7e7e4e7be86f06b800de108c0744156b5b7db469efb99b19430ef4b4b77ee577` |
| preregistration template | `1e364ac47061e27d7bd82e76df9302e26ff85ca15258f2d017d492cef28aa2f7` |
| contract | `0b4e6decf6b6da63baa6939a44ae2b39e0d7a18d8d6c79c7e037ef7ac9236e43` |
| CLI | `591153fe7812ca4e32a027e2da277483dcc4938b21cb77ed2a1fa9f22a437863` |
| unit test | `4436e06327e49c3d5e66062126b976383fe74b7602e072eeb1e40ca503dd58ec` |
| integration test | `8f919c728770336ac74b5c5a11890ee012b6e04f0a563d0a541d093d49dd6ed4` |
| data/model/safety card | `bef7b09317ceb82e9eb5c0de0c20990cb111f9aa5bcede2c822a3fd267cb0eae` |

Readiness 문서 자체 SHA는 자기참조를 피한다.

2026-07-29 source-currency 재감사에서 canonical manuscript와 claim-gap
문서가 갱신됐지만 config pin이 구 snapshot을 가리켜 unit/integration
6건이 static 단계에서 실패하는 것을 재현했다. 두 source의 현재 SHA를
각각 `dea994728ae8c73be098a29e9a70af2ab4730a651063e64687b40c07433b17eb`,
`2168233b459bbe0b2aac411de94d57720408be2479c96e589b14388ff3d5d7a1`로
갱신한 뒤 전용 31건과 config-only가 통과했고, full preflight는 의도대로
`stage=s108_final`의 `TBD_S108_COMPLETION`에서 input/output 접근 전에
fail-closed했다. 이 currency repair는 실제 실행을 허용하지 않는다.

## 6. 다음 실행

1. S104–S108 actual chain을 완료하고 final commit/model artifact hash를 고정한다.
2. Non-locked E01 population과 sampling을 독립 사전등록한다.
3. 다섯 family별 exact magnitude, relation, output/tolerance와 pass-rate
   계산법을 결과 접근 전에 동결한다.
4. D1 input manifest와 restricted audio-object sidecar를 독립 검증한다.
5. 별도 perturbation/model executor를 구현하고 locked/S203 접근 0을
   attestation한다.
6. A1 report/bundle을 독립 검증한 뒤 별도 model-promotion review를 한다.

그 전에는 E01 연구 완료율과 획득 점수는 0이다.
