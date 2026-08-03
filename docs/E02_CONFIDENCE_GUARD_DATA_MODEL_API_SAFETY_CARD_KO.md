# WP8 E02 confidence 밖 자동 교정 차단 데이터·모델·API·안전 카드

기준일: 2026-07-29 KST  
상태: **production 정적 준비 계약만 구현 / 실제 request·API·교정 실행 0**

## 1. 목적과 현재 비주장

E02는 구조화된 진단이 등록된 domain 또는 calibration confidence 밖에
있을 때 자동 코칭 문구, reference와 corrective action이 API 응답으로
나가지 않게 하는 마지막 fail-closed guard다.

현재 구현은 schema, predecessor 검증 계약, 순수 guard 함수와 read-only
preflight만 제공한다. 다음 작업은 전부 수행하지 않는다.

- 실제 request, issue row 또는 guard input 접근
- FastAPI endpoint 호출 또는 응답 전송
- coaching advice, reference, corrective action 생성·전달
- 모델·GPU·metric·network·LLM 실행
- 자유 문장 생성 또는 C01 문구 rewrite
- output·registry·commit 작성
- production release, deployment 또는 E02 완료 주장

따라서 정적 테스트의 `allowed_for_separate_renderer`는 실제 교정 발행이나
API 배포 승인이 아니다. 별도 executor가 모든 선행 증거와 승인 범위를
다시 검증해야 한다.

## 2. 선행 증거

### 2.1 S107 actual calibration completion

E02는 다음을 config 값으로 복사하지 않고 실제 S107 completed bundle에서
deep validation한다.

- S107 immutable commit와 completed registry
- `threshold_policy_report.json`
- selected issue threshold와 selection status
- calibration-only, split·raw audio·GPU·promotion 경계
- generation artifact hash chain

Config에는 numeric confidence threshold가 없다. Threshold source는
`deep_validated_actual_s107_threshold_policy_only`이며 commit/report
SHA-256만 고정한다. `abstain_all_no_provisional_candidate`는 threshold가
생긴 것으로 취급하지 않는다.

현재 S107 completion과 threshold report SHA는
`TBD_S107_COMPLETION`이다. S107의 현재 정책은 silver/oracle provisional
engineering이므로, 미래 actual completion이 생겨도 그것만으로 production
release를 허용하지 않는다.

### 2.2 C02 actual deterministic coaching bundle

E02가 요구하는 C02 predecessor는 static config가 아니라 한 실제
diagnostic input과 deterministic selection output을 묶은 immutable
bundle이다.

Bundle은 다음을 포함한다.

- effective C02 config
- S107 commit/threshold lineage
- approved C02 preregistration
- D1 diagnostic input과 C02 selection output
- completed registry
- no-backfill·catalog-only·free-generation/network/model/GPU/reference
  non-execution assurance

E02는 pinned C02 contract를 사용해 C01 ontology, S107, C02
preregistration, diagnostic input과 selection output을 다시 검증하고
output을 deterministic하게 재계산한다. Hash와 상태만 보는 얕은
attestation은 허용하지 않는다.

현재 C02 completion은 `TBD_C02_COMPLETION`이다.

## 3. 미정 정책과 사전등록

문서 근거가 없는 다음 항목은 추정하지 않는다.

- out-of-domain population·acoustic·device·language 정의
- OOD detector, rule 또는 threshold
- OOD evidence receipt 생성 계약
- unsupported 판정의 최종 운영 정의
- request-level abstain-all의 정확한 정의
- 여러 차단 사유가 동시에 발생할 때 precedence
- 혼합 사유의 최종 safe-next-action 결정

이 값은
`configs/coaching/e02_confidence_guard_preregistration_v1.template.json`에서
`TBD_PREREGISTRATION`으로 남는다. Actual S107/C02 증거와 결합한
`approved_and_frozen` preregistration이 guard input 또는 API 접근 전에
필요하다.

사전등록은 numeric S107 threshold를 복제할 수 없다. 실제 S107 threshold
report SHA와 score-free `below_s107_policy`/`at_or_above_s107_policy`
판정 계약만 결합한다.

## 4. 차단 상태

다음 네 사유 중 하나라도 있으면 모든 자동 교정 capability를 false로
둔다.

| 사유 코드 | 입력 상태 | 결과 |
|---|---|---|
| `out_of_domain` | `out_of_registered_domain` | advice/reference/action 차단 |
| `low_confidence` | `below_s107_policy` | advice/reference/action 차단 |
| `unsupported` | `unsupported` | advice/reference/action 차단 |
| `abstain_all` | C02 ranked window가 모두 blocked | advice/reference/action 차단 |

Blocked 응답의 `safe_next_action`은 `rerecord` 또는 `expert_review`만
허용한다. 어떤 사유가 우선하고 둘 중 어느 action을 선택할지는 frozen
combination policy가 결정한다. 응답에서 새 설명 문구를 생성하지 않는다.

C02가 `no_issues`이면 `no_correction`이며 모든 capability는 false다.
모든 등록 gate를 통과하고 C02가 실제 `selected`인 경우에만
`allowed_for_separate_renderer`를 반환한다. 이 상태도 별도 renderer
eligibility일 뿐 E02 package가 advice/reference/action을 전달했다는 뜻은
아니다.

## 5. 입력 데이터 모델

Schema:
`configs/coaching/e02_confidence_guard_input_v1.schema.json`

입력은 `D1_pseudonymized_restricted_api_guard`이며 다음만 포함한다.

- opaque guard input ID와 request binding SHA-256
- S107/C02/E02 lineage SHA-256
- domain/confidence/support/C02 selection의 categorical 상태
- domain, confidence, support, C02 selection evidence receipt SHA-256
- C02 no-backfill과 free-generation/network/model non-execution receipt

숫자 model score, probability, logit, confidence 값, threshold, ECE/Brier,
target text, transcript, PII, audio/model/source path, filename, free text,
prompt/completion, advice/reference/action payload를 허용하지 않는다.

`c02_abstain_all`은 caller가 임의로 선언할 수 없다. C02 output의
`ranked_window_count > 0`, `selected_count = 0`,
`blocked_count = ranked_window_count`에서 재계산한다.

## 6. API guard 응답 모델

Schema:
`configs/coaching/e02_confidence_guard_api_response_v1.schema.json`

응답은 다음 score-free 정보만 허용한다.

- `status`, `blocked`, 등록된 `blocked_reasons`
- `rerecord`/`expert_review` safe action code 또는 `null`
- advice/reference/corrective-action capability boolean
- immutable input·S107·C02·E02 lineage hash
- domain/confidence/support/C02 selection provenance hash
- score/PII/path/free-text/payload 비포함 safety receipt

Blocked 응답에서는 세 capability가 모두 false이고 renderer도 호출할 수
없다. Advice 문구, reference locator 또는 action parameters를 넣을 필드가
없다. Response 전체는 input과 frozen policy에서 재계산하고 exact
comparison한다.

현재 `/v1/oracle-attempts`는 연구·테스트 전용이며 target text, score와
사용자 제공 `confidenceThreshold`를 받는 기존 계약이다. 이를 E02
production endpoint나 actual S107 threshold 근거로 사용하지 않는다.
이번 package는 FastAPI route를 추가·변경하지 않았다.

## 7. C02 안전 규칙 승계

- top-k window를 먼저 고정하고 blocked item을 바깥 후보로 backfill하지
  않는다.
- C01 catalog 밖 문구와 free-generation fallback을 금지한다.
- prompt/completion, network와 runtime model 사용을 금지한다.
- C02 output을 pinned contract로 재계산하고 lineage를 확인한다.
- C03 전 production release는 false다.

E02가 C02 차단 결과를 override하거나 reference/action으로 우회할 수
없다.

## 8. 개인정보·무결성

- JSON/YAML duplicate key와 NaN/Infinity 거부
- exact root/nested key set과 strict schema
- 25개 C1/static source SHA-256 pin
- workspace-relative path, traversal/symlink/non-regular-file 거부
- open 전·중 inode/size/mtime과 content hash 검사
- predecessor와 preregistration TOCTOU 재검증
- future output unique/no-overwrite/atomic publish 계약
- API payload 재귀 denylist로 score·PII·raw path·free text 차단

Hash는 익명화를 의미하지 않는다. Request binding과 individual guard
decision은 D1에 유지하며 A1 집계로 승격하려면 별도 disclosure review가
필요하다.

## 9. 현재 actual

- actual S107/C02 predecessor: 0
- approved E02 preregistration: 0
- request/row/guard input 접근: 0
- API/coaching/reference/corrective action: 0
- model/GPU/metric/network/LLM/free generation: 0
- output/registry/commit/publication: 0

따라서 E02 배점, 자동 교정 안전성, OOD 탐지 성능 또는 production API
적합성을 주장할 수 없다.
