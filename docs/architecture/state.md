# 입술 모양 피드백 Sidecar 상태

- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 runtime은 `not_ready`
- 상세 설계: [`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)

이 문서의 enum 값이 visual sidecar 상태 계약의 정본이다. Python `StrEnum`과 strict
serializer가 아래 exact string을 구현하며 동의어 또는 독자 상태를 허용하지 않는다.

## 1. 상태 종류

| State | Owner | Persisted | 의미 |
|---|---|---:|---|
| `VisualFeedbackStatus` | visual application | no | 한 번의 sidecar 호출 terminal 결과 |
| `VisualCueState` | visual domain | restricted evidence에만 선택적 | 개별 입술 cue의 관측 가능성과 품질 |
| `VisualEvidenceRelation` | visual policy | restricted evidence에만 선택적 | visual cue와 immutable upstream target의 관계 |
| upstream audio state | `seungun` | visual 소유 아님 | visual은 읽기 전용으로 소비하고 변경하지 않음 |
| visual audit/deletion receipt | adapter | restricted storage | provenance, privacy와 cleanup 확인용 |

sidecar는 서버 session/store를 소유하지 않는다. 같은 사용자의 재촬영도 별도의
호출이며 상위 시스템이 필요한 reference만 전달한다.

## 2. `VisualFeedbackStatus`

| Value | 의미 | visual advice | terminal |
|---|---|---:|---:|
| `visual_feedback_ready` | upstream target을 지원하는 관측·보정된 입술 cue와 검증·허용 행동이 있음 | yes, 한 개 | yes |
| `no_supported_lip_cue` | 처리는 유효하지만 발행 가능한 입술 cue가 없음 | no | yes |
| `visual_rerecord_required` | video capture 문제를 재촬영으로 회복할 수 있음 | no | yes |
| `visual_uncertain` | low confidence, sync 불확실 또는 audio/visual conflict | no | yes |
| `visual_unsupported` | pose, cue, device/cohort 또는 입력이 지원 범위 밖 | no | yes |
| `dependency_unavailable` | upstream schema, binding, location/timebase 또는 reliability가 불충분 | no | yes |
| `visual_failed_closed` | 권리, 무결성, contract, required adapter, ledger/deletion 불변식 실패 | no | yes |

`visual_feedback_ready`는 전체 발음이나 audio diagnosis를 재판정했다는 뜻이 아니다.
오직 이미 정해진 upstream target에 visual 보조 피드백을 붙일 수 있다는 뜻이다.

## 3. `VisualCueState`

| Value | 의미 | 수치 허용 |
|---|---|---:|
| `observed` | 지원 범위에서 cue가 실제로 관측되고 품질 검사를 통과 | 검증된 measurement만 허용 |
| `not_observable` | 정면 영상으로 해당 cue를 원리상 관측할 수 없음 | no |
| `missing` | 필요한 frame/span/landmark가 없음 | no |
| `low_confidence` | 관측 후보는 있으나 동결된 품질 기준 미달 | public cue score 금지 |
| `out_of_distribution` | pose/device/context/cohort가 보정 지원 범위 밖 | no |
| `backend_unavailable` | cue 생성에 필요한 visual backend를 사용할 수 없음 | no |

`observed`가 아니면 measurement, deviation, probability와 feedback claim을 비운다.
`missing`, `not_observable`과 `out_of_distribution`을 score `0`으로 바꾸지 않는다.

## 4. `VisualEvidenceRelation`

| Value | 의미 | 허용 결과 |
|---|---|---|
| `supports_upstream` | 같은 target/span에서 calibrated visible cue가 upstream target을 보조 | 모든 gate 통과 시 `visual_feedback_ready` |
| `conflicts_with_upstream` | 양쪽 usable evidence가 일치하지 않음 | `visual_uncertain` |
| `not_comparable` | capability, span, sync, reference 또는 calibration이 없어 비교 불가 | 원인에 따라 ready 이외의 상태 |

relation은 audio와 visual score의 평균이 아니다. visual은 upstream verdict를
재판정하거나 새로운 오류를 만드는 표를 갖지 않는다.

## 5. 상태 결정 우선순위

동시에 여러 조건이 관측되면 다음 순서로 결정한다.

1. upstream schema/binding/reliability 누락: `dependency_unavailable`
2. 동의·권리·digest·contract·삭제 불변식 실패: `visual_failed_closed`
3. 재촬영 가능한 video QC 실패: `visual_rerecord_required`
4. 안정적으로 지원되지 않는 pose/cue/context: `visual_unsupported`
5. low confidence, sync 불확실 또는 conflict: `visual_uncertain`
6. 유효하지만 지원 cue 없음: `no_supported_lip_cue`
7. 모든 visual gate와 catalog 통과: `visual_feedback_ready`

upstream dependency 누락을 visual cue로 보완하지 않고, rights 실패를 재촬영 문제로
표현하지 않는다.

## 6. 상태 전이

한 번의 호출에서 application은 아래와 같이 terminal 결과 하나만 반환한다.

| 내부 단계 | 조건 | Terminal status | Side effect |
|---|---|---|---|
| dependency validation | upstream 계약 누락·불일치 | `dependency_unavailable` | visual model 미호출 |
| media admission | 권리·binding 무결성 실패 | `visual_failed_closed` | publication 금지, cleanup 시도 |
| video QC | 회복 가능한 capture 실패 | `visual_rerecord_required` | reason receipt |
| video QC/capability | 지원 범위 밖 | `visual_unsupported` | visual analysis 중단 |
| tracking/sync/cue | 저신뢰 또는 conflict | `visual_uncertain` | visual advice 미발행 |
| cue eligibility | 지원 cue 없음 | `no_supported_lip_cue` | audio 결과 유지 |
| cue eligibility | observed/supports_upstream/catalog 통과 | `visual_feedback_ready` | supplement와 audit receipt |

중간 `running`, `analyzing`, `approved` 같은 public 상태를 추가하지 않는다.

## 7. 불변식

- 모든 결과는 upstream analysis reference를 보존하지만 raw audio나 내부 경로를
  포함하지 않는다.
- visual 결과는 upstream `error_score`, operating threshold, `is_error`, phone과
  location을 변경하지 않는다.
- upstream binding/schema/reliability가 없으면 visual evidence가 좋아 보여도
  `visual_feedback_ready`가 될 수 없다.
- `visual_feedback_ready`에는 하나 이상의 observed cue, `supports_upstream`,
  frozen action/claim candidate ID 정확히 한 개가 필요하다.
- 나머지 여섯 `VisualFeedbackStatus`에는 advice/claim을 포함하지 않는다.
- `conflicts_with_upstream`을 score averaging으로 `supports_upstream`으로 바꾸지 않는다.
- sidecar 상태는 `seungun`의 audio attempt/global coaching 상태로 역전파되지 않는다.
- required audit/deletion append가 실패하면 host attempt는 `failed_closed`이며 이미 계산된
  audio·visual 반환값을 모두 억제한다. `seungun` analysis 자체를 수정하거나 재실행하지는
  않는다.

## 8. 실행 scope와 현재 검증

상태 enum과 별도로 `ExecutionScope`는 `internal_contract_test`와 `user_runtime`을
구분한다. calibration/catalog scope가 `synthetic_contract_only`이면 내부 smoke에서만
ready 불변식을 검사할 수 있다. 같은 artifact를 `user_runtime`에 넣으면
`synthetic_artifact_not_user_eligible` reason의 `visual_uncertain`이며 candidate는 없다.

현재 unit test는 일곱 terminal status 중 ready, rerecord, uncertain,
dependency-unavailable, failed-closed와 conflict relation을 synthetic record로
검사했다. registered binding digest와 presented receipt exact-match, detector 비의존
handoff도 unit test로 검사했다. local 제한 저장의 commit 후 0400과 cleanup 실패 시
audio 포함 전체 반환값 억제, 합성 비개인 MP4의 실제 FFmpeg probe·전 구간 균등 RGB
decode·PCM derivation, MediaPipe worker network/model-integrity 경계도 별도 검사했다.
RGB/landmark video-QC와 lip/PCM A/V-sync candidate는 runtime detection v2에 receipt로
결합되지만 P6 전에는 `candidate_unvalidated`/`unavailable`이다. 합성 H.264/AAC CLI는
공개 test portrait를 변환한 static H.264/AAC를 pre-opened read-only `--video-fd`, 공식
landmark model과 CUDA Seungun에서 실행해 audio 완료·visual unavailable, cleanup 완료와
`video_qc_candidate_not_calibrated`·`content_av_sync_candidate_not_calibrated`를 반환했다.
따라서 실제 얼굴 landmark/QC·sync candidate 경로는 관측됐다. pose·occlusion과 calibrated QC pass,
실제 사용자 영상의 `visual_unsupported` 분포, user-ready precision, content-sync 실측
성능, 실제 데이터 calibration, P8 locked evaluation과 자동 claim-policy conformance는
아직 검증되지 않았다. `--video`는 path가 argv/shell history에 남을 수 있는 내부 편의
기능이다. TTL/orphan scavenger/persistent cleanup ledger와 `RLIMIT_NPROC`/`RLIMIT_FSIZE`도
없으며 launcher 강화가 전체 sandbox 완료를 뜻하지 않는다.

Seungun upstream 상태는 고정 detector config/state/normalization의 pin 검증과 완료된
analysis schema를 sidecar anchor로 사용할 수 있다는 제한된 의미에서 `supported`다.
정책 revision은 `seungun_final_pronunciation_detector_v1_locked_test_v1`이며, 이 상태는
ranking score를 보정된 confidence로 바꾸거나 개인별 진단 정확도·교육 효과를 승인하지
않는다. visual publication에는 별도의 사용자 runtime P6/P8 evidence가 계속 필요하다.

P6/P8 evidence validator와 CLI는 구현됐다. P6 성공 receipt는 항상
`evidence_validated_not_promoted`, `visual_reliability_authorized=false`이고 P8 성공
receipt는 항상 `zero_violations_not_promoted`, `user_visual_claims_authorized=false`다.
unit test는 synthetic privacy-safe bundle만 사용한다. AI Hub 68/538 실제 download,
admission, 24-point metric/calibration 산출과 locked P8 replay는 여전히 `not_started`다.
rights/reload와 read-zero seal은 supplied digest/assertion evidence이며 독립 생성·인증된
사실로 해석하지 않는다.
