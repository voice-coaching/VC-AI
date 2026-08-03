# WP7 R05 GRPO go/no-go production 정적 준비도

상태: **production 정적 package 구현 완료 / actual 판정·GRPO 실행 0**

기준일: 2026-07-29 KST

## 1. 구현 범위

R05 전용 신규 파일만 작성했다.

| 파일 | 역할 |
|---|---|
| `configs/experiments/r05_grpo_go_no_go_v1.yaml` | fixed gate, ordered predecessor, TBD, privacy/runtime/output 계약 |
| `configs/reference/r05_grpo_decision_report_v1.schema.json` | strict A1 five-gate decision report schema |
| `configs/reference/r05_grpo_gate_preregistration_v1.template.json` | 미근거 operational choice를 모두 드러내는 독립 prereg template |
| `configs/reference/r05_grpo_human_authorization_v1.template.json` | model/license/reward/safety human authorization template |
| `src/voice_coach/references/r05_grpo_decision_contract.py` | static/deep/read-only decision·bundle validator |
| `scripts/prepare_r05_grpo_go_no_go.py` | config-only와 read-only full preflight CLI |
| `tests/unit/test_r05_grpo_decision_contract.py` | fixed gate/A1/decision/integrity bounded-fault tests |
| `tests/integration/test_prepare_r05_grpo_go_no_go.py` | canonical runtime0와 earliest-S108 failure |
| `docs/R05_GRPO_GO_NO_GO_DATA_MODEL_LICENSE_SAFETY_CARD_KO.md` | data/model/license/reward/safety 경계 |
| `docs/evaluation/R05_GRPO_GO_NO_GO_READINESS_KO.md` | 본 증거 문서 |

R04 파일, 정본 tracker, 원고와 evidence map은 수정하지 않았다. GRPO/F5/
StyleTTS executor, downloader, model loader, GPU/reward runner, effect
estimator, decision publisher는 만들지 않았다.

## 2. 고정 gate와 미정 항목

R05 gate는 다음 다섯 개뿐이다.

1. S1 phone F1 ≥0.80
2. S1 utterance ICC ≥0.70
3. S2 severe error recall ≥0.80
4. multi-reference blind evaluation superiority
5. actual pilot learning-effect signal

그 밖의 exact artifact/metric pointer, population, alignment, CI, decision
rule, blind estimand/comparator/margin, learning-effect estimand/effect-size/
signal rule은 `TBD_PREREGISTRATION`이다. Model identity, code/weight/data
license, reward implementation/directionality, reward-hacking, safety와
rollback도 미정이다.

R04 static package는 병행 작성 중이므로 R05의 R04 config/contract SHA,
completion schema/SHA는 의도적으로 `TBD_R04_*`다. Root가 R04 final hash를
전달하기 전 임의 pin을 만들지 않는다.

## 3. Fail-closed 실행 순서

Full preflight의 순서는 바꿀 수 없다.

1. R05 config/source/schema/template exact pin
2. S108 final completion deep validation
3. S203 actual expert-holdout completion deep validation
4. R04 actual blind-evaluation completion deep validation
5. H00 approval와 H04 actual statistical report deep validation
6. independently frozen R05 preregistration
7. independent human authorization
8. 다섯 A1 aggregate gate의 preregistered decision
9. 모든 model/download/GPU/reward/training 실행 전 정지

처음 누락·불일치에서 종료한다. 누락이나 실패를 pass로 추론하지 않는다.
`GO`는 다섯 gate와 model/license/reward/safety assurance가 모두 pass이고
authorization binding이 유효할 때만 가능하다. 그 외 결과는
preregistration에 고정된 `NO_GO` 또는 `BLOCKED`다.

## 4. Canonical 실행 증거

Config-only는 C1/static source pin 14개만 읽는다. Contract와 CLI 자체도
exact hash로 pin한다.

- predecessor/external/preregistration/authorization path·read: 0
- aggregate/row/protected/audio/rating/participant read: 0
- model path/weight/download/network/GPU: 0
- reward/GRPO/F5/StyleTTS/metric/effect/decision: 0
- output path/write/publication: 0

Canonical full preflight는 의도된 exit 1이다.

- stage: `s108_final`
- error: `S108 completion commit SHA is TBD_S108_COMPLETION`
- S108 path resolution/read attempt: 0/0
- S203/R04/H00/H04/preregistration/authorization: 0
- protected/locked/audio/rating/participant/model/network/GPU/reward: 0
- metric/effect/decision/output/publication: 0

선언한 R05 decision report, commit, registry, failure report는 생성하지
않는다.

## 5. Strict A1와 completed bundle

Decision report는 정확히 다섯 gate를 고정 순서로 가진다. Fixed numeric
threshold drift, 여섯 번째 gate, 누락 gate, unresolved passed record,
non-finite/reversed CI, GO의 missing/failed gate 또는 false assurance를
거부한다.

A1 recursive policy는 row ID, PII/pseudonymous ID, free text, transcript,
target text, audio/model path와 row container를 거부한다. Report에는
predecessor/preregistration/authorization SHA와 aggregate gate record만
남는다.

Future completed-bundle validator는 synthetic fixture 범위에서 다음을
검증한다.

- commit→registry→report exact path/hash/schema/status
- canonical binding SHA 재계산
- registry artifact exact set
- symlink/path traversal 차단
- no-overwrite
- hash tamper와 검증 중 변경(TOCTOU)
- 최종 source·commit·registry·report 재검사

이 validator는 read-only이며 bundle을 작성하지 않는다.

## 6. 검증 결과

R05 targeted 검증 결과:

- unit + integration: **38 passed**
- R00–R03/S108/S202/S203/R05 관련 회귀: **198 passed**
- Ruff check: **passed**
- Ruff format: **passed**
- mypy contract/CLI: **passed**

Bounded-fault 범위는 fixed gates, TBD 우회, predecessor order, source pin,
strict JSON/YAML, A1 privacy, GO/NO_GO/BLOCKED, assurance binding,
symlink/path/hash/TOCTOU/no-overwrite와 atomic completed bundle이다.
이 범위의 open implementation finding은 C/H/M **0/0/0**이다. 실제
외부 승인·participant·평가·효과·모델·reward 실행 위험이 해소됐다는
뜻은 아니다.

검증 시점 SHA-256:

| 파일 | SHA-256 |
|---|---|
| config | `24aab055a9c8bfd684a64fbc566c966384f00c60c6660a707c27719555b948ee` |
| decision schema | `22174e8c8e844fdb79b969e7e6800de741ce71e2f3349e3b4771cdf2bfad8c47` |
| preregistration template | `83b343a59050093c9698d278f52d2ece4c1a4dabc694410b7d3fbcf233faac4b` |
| authorization template | `2480c83359549298d9a1b4a17ffa3f5f6eb755e3bf55364c8bb58a38b50e8645` |
| contract | `3041ea274404fec56a46f36fd5c79a16b64a76294e26df2e2fa40132bfdf47be` |
| CLI | `917b72f6429a80ced5db1f080b0cebb5d7ab33e6e6fc75318bf4be76b37e807d` |
| unit test | `f9d754c5c66ee232a608a60242298fa2ff988cbcb0ad2eafa2dea2153e1da3c2` |
| integration test | `4f4ec492f2654cb707177bdf35d0f114d3c29bda650992aafef7a99249e58372` |
| data/model/license/safety card | `b8095707280864440be68601302e2b8b995282f1bf66931fd91828111ff9171c` |

Readiness 문서 자체 SHA는 자기참조를 피하기 위해 본문 표에 넣지 않는다.

## 7. 현재 blocker와 다음 실행

1. S108 final S1을 실제 complete commit으로 고정하고 deep validation한다.
2. S203 actual expert holdout과 severe-error recall aggregate를 완결한다.
3. R04 actual blind evaluation과 preregistered superiority 결과를 완결한다.
4. H00 승인 후 actual participant pilot을 수행하고 H04 report를 완결한다.
5. 다섯 gate의 exact pointer/estimand/CI/rule과 missing/fail 결과를 독립
   사전등록한다.
6. Exact model·code/weight/data license, reward directionality/hacking,
   safety/rollback을 독립 검토하고 인간 authorization을 받는다.
7. 별도 aggregate-only decision executor/publisher를 구현·독립 검증한다.

그 전에는 actual R05 score와 연구 완료율은 0이며 GRPO 진입은
**NO-GO가 아니라 아직 증거가 없는 BLOCKED 상태**다. Canonical CLI는
이를 GO나 완료로 출력하지 않는다.
