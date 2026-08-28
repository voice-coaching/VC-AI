# Track E 시각 sidecar engineering 검증 계약

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `active_gate_contract`
- 구현 상태: `VE1_automation_present_formal_gate_not_passed`
- 공개 데이터 후보 조사: `dataset_candidates_audited`; admission·실행은 `not_started`
- frozen visual artifact: production용 없음; `synthetic_contract_only` fixture만 존재
- 파일명 상태: `legacy_compatibility_name`

`Track E`라는 파일명은 기존 링크 호환을 위해 유지한다. 이 문서가 정의하는
Track E는 `seungun` 전체 음성 코칭 시스템이나 오디오 모델의 완료 계약이 아니다.
`seungun` 분석 결과에 의존하는 입술 모양 시각 sidecar의 **engineering 검증
순서**만 정의한다.

범위 수정 전 계약과 `configs/pronunciation_feedback/track_e_contract_v1.json`은
legacy 오디오/전체 파이프라인 문맥의 기록이다. 이를 새 visual Track E의 frozen
artifact, threshold, 완료 receipt로 재명명하거나 재사용하지 않는다. 기존 문서는
[`historical_snapshot`](../history/legacy_voice_coach_architecture_2026-08-26/TRACK_E_CONTRACT_KO.md)에
보존되어 있다.

## 1. 불변식

- gate 순서는 건너뛰거나 병렬 성공으로 대체하지 않는다.
- 모든 실제 사용자 영상 처리에는 consent·rights·privacy·retention과 데이터 역할을
  먼저 적용한다.
- synthetic 또는 static 검증을 실제 영상 QC·성능·잠금 평가로 승격하지 않는다.
- 공개 portal/card의 listing은 데이터 사용 적격성, calibration 실행, 잠금 평가 또는
  claim-policy conformance 완료를 뜻하지 않는다.
- calibration과 선택을 끝내기 전 locked/independent 평가 자료를 열지 않는다.
- locked 결과를 본 뒤 같은 protocol의 model, normalizer, reference, threshold를
  다시 고르지 않는다.
- 시각 branch는 `seungun` audio decision을 재학습·수리·대체하지 않는다.
- 2D 입술 baseline의 증분 가치가 확인되기 전 2.5D/3D를 완료 조건으로 넣지 않는다.
- 어느 gate도 통과하지 못한 값을 0이나 임의 fallback으로 채우지 않는다.

## 2. 순차 gate

| Gate | 목적 | 최소 산출물 | 실패·미완료 처리 |
|---|---|---|---|
| VE0 범위·동의·데이터 감사 | 외부에서 관찰 가능한 lip cue, 금지 claim, 수집·파생물·삭제·라이선스 범위 동결 | [dataset evidence ledger](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md), visual data card, consent/rights/retention 계약, cue ontology, split·stop rule | restricted 원본 접근 금지, `visual_failed_closed` |
| VE1 synthetic 계약 | port/DTO/state/privacy와 fail-closed 동작 검증 | synthetic fixture, schema test, no-path/no-ID 검사, fake adapter test | 다음 gate 금지, `visual_failed_closed` |
| VE2 소규모 capture/QC | consent된 소수 자료에서 decode, view, lip ROI, blur, 가림, AV sync 가능성 확인 | QC report, failure distribution, rerecord 동작, device/view provenance | `visual_rerecord_required` 또는 `visual_unsupported` |
| VE3 2D baseline smoke | 관찰 가능한 closure/aperture/rounding/timing만 작은 speaker/device-disjoint split에서 평가 | 2D baseline report, audio-only 대비 paired ablation, unsupported-cue test | 자동 visual claim 비활성 |
| VE4 calibration·reload | calibration split에서 confidence/threshold를 정하고 새 process에서 재현 | model/normalizer/reference/ontology/threshold hash, calibration report, reload receipt | calibration 미충족은 `visual_uncertain`, 필수 adapter/무결성 실패는 `visual_failed_closed` |
| VE5 독립 평가 | 동결된 경로를 선택에 쓰지 않은 holdout에서 한 번 평가 | independent report, selective risk, slice·failure·latency 결과 | 재튜닝 금지, 새 protocol version 필요 |
| VE6 claim-policy conformance | 동결된 P8 결과와 catalog를 결정론적으로 재생해 evidence/binding/calibration 참조, visible-cue allowlist, 금지 해부학·의학 claim, candidate cardinality를 검사 | versioned policy, catalog digest, locked replay report, violation 0 receipt | visual claim 발행 금지, 수정 후 새 protocol·catalog revision 필요 |

각 gate의 exact 표본 수, threshold, CI, non-inferiority margin과 stop rule은 결과를
보기 전에 별도 versioned 계약으로 동결해야 한다. 현재 이 값과 frozen visual
artifact는 존재하지 않는다.

### 현재 gate ledger

| Gate | 현재 상태 | 근거와 한계 |
|---|---|---|
| VE0 | `p6_completed_p8_pending` | AI Hub 68 권리 확인·admission과 고정 split/protocol은 완료; AI Hub 538 실제 admission/evaluation evidence는 아직 없음 |
| VE1 | `implemented_and_tested` | port/receipt/state/strict JSON과 fail-closed orchestration이 구현·회귀 테스트됨 |
| VE2 | `p6_completed_p8_pending` | AI Hub 68 실제 archive admission 및 aggregate-only source manifest 완료; 538 TS174/TL174 admission은 미실행 |
| VE3 | `passed_for_p6_population` | AI Hub 68 24-point geometry, 5 cameras, actor/attempt-disjoint metric 및 production tracker parity artifact 통과 |
| VE4 | `p6_passed_runtime_promotion_blocked` | `어`/`ㅓ` P6B reference/calibration/catalog/policy와 fresh reload 통과; TS174/TL174 decision policy evidence 전에는 runtime promotion 미발급 |
| VE5 | `not_started` | AI Hub 538이 provisional locked 후보지만 seal·교집합 감사·one-shot 평가 없음 |
| VE6 | `not_started` | frozen-summary P8 conformance validator는 구현됐지만 production policy/catalog와 실제 locked replay receipt 없음 |

공개 후보와 역할의 근거, 확인된 결손 및 P6/P8 실행 계약은
[`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)를
따른다. 현재 readiness를 한 줄로 합치지 않는다.

| 구분 | 현재 상태 |
|---|---|
| 공개 후보 조사 | `dataset_candidates_audited` |
| 실제 데이터 admission/access | `AI Hub 68 completed`; `AI Hub 538 pending` |
| P6 calibration 실행 | `passed`; runtime promotion은 decision-policy evidence 대기 |
| P8 자동 독립평가 | `not_started` |
| VE6 claim-policy conformance | `not_started` |
| gate 승격 | P6/P6B evidence passed; user runtime `none` |

내부 `visual_feedback_ready` scenario는 candidate cardinality와 serializer를 검사하는
synthetic case일 뿐 gate를 건너뛰어 사용자 claim을 활성화하지 않는다.

legacy summary validator의 non-promotional receipt와 별도로, automated P6 v4 builder는
실제 P6/P6B producer evidence와 production tracker parity, actual-media decision-policy
evidence를 모두 요구한다. 마지막 evidence는 아직 없으므로 실제 pipeline identity와
promotion receipt를 발급하지 않았다. P8는 read-zero seal/one-shot evaluator 계약의
synthetic test만 완료됐으며 locked partition은 열지 않았다.

local 제한 저장의 commit 후 0400, 실제 FFmpeg/ffprobe 기반 H.264/HEVC MP4 probe·전 구간
균등 RGB decode·PCM derivation, MediaPipe worker의 network/model-integrity 경계와
RGB/landmark video-QC candidate, lip-aperture/PCM-energy A/V anchor candidate는 구현·unit
test됐다. QC candidate는 얼굴 coverage, luma/exposure, edge proxy, lip ROI와 continuity를
측정하지만 pose·occlusion 또는 calibrated pass를 발급하지 않는다. 합성 H.264/AAC
smoke 외에 HEVC Main/AAC, 1080x1920 portrait, 30 fps 사용자 영상 두 건을 권장
`--video-fd`, 공식 landmark model과 CUDA Seungun으로 실행했다. 둘 다 audio
`coaching_ready`, `audio_completed_visual_unavailable`, cleanup 완료였고
`video_qc_candidate_not_calibrated`였다. content sync는 각각 `not_observed`와
`candidate_unvalidated`였다. 이는 공개 후보 dataset admission, production tracker
정확도 또는 P6 calibration이 아니므로 VE0~VE3 formal ledger와 P6/P8 상태는 위 표
그대로 유지한다.

host-controlled dense phone-window 경로도 unit/integration test됐다. whole-clip 8-frame
QC/sync sample을 먼저 처리한 뒤 already-bound Seungun selected anchor와 policy-valid
measured A/V offset만으로 bounded RGB24 window를 decode·re-track하고,
decode/tracking/runtime receipt SHA를 결합한다. 실제 두 영상에는 policy-valid offset이
없어 세 receipt가 모두 `not_issued`였으며 visual gate를 승격하지 않았다.

## 3. upstream dependency gate

VE1 이후 모든 실제 결합 검증은
[`SeungunPronunciationEvidencePort`](component.md)의 immutable analysis
reference/envelope와 호환성 receipt를 요구한다. detector 비의존
`SeungunAttemptBindingFacade`는 완료된 analysis, attempt, prompt, audio/video
timebase, consent와 caller 제공 reliability를 `SeungunVisualHandoff`로 원자적으로
묶는다. source record에 facade가 발급한 binding digest를 등록하고 evidence adapter가
제시된 digest와 exact match를 검사하므로, 필드가 같더라도 별도로 재발급한 receipt는
거부된다. binding v2는 audio derivation receipt digest도 필수로 self-digest에 묶는다.
local media derivation과 container/audio lineage를 handoff로 전달하는 bridge는 unit
test됐고 internal CLI/local composition은 합성 H.264 및 두 HEVC portrait 사용자 파일의
audio-completed E2E까지 실행됐다. 그러나 persistent source 재로드와 visual claim 성공
근거는 아직 없다. 또한
local composition은 hash-pinned `final_pronunciation_detector_v1`의 완료 analysis를
sidecar anchor로 사용하는 범위에서 upstream reliability를 `supported`로 발급한다.
이는 artifact/schema 호환성이지 correctness confidence 또는 production 정확도 승인이
아니다. reliability bridge는 사용자 runtime P6 calibration artifact와 P8 catalog가
없으면 visual을 계속 fail-closed한다.

시각 자료가 upstream 발화와 정확히 결합됐다는 증거가 없으면 visual cue를 다른
audio span에 붙이거나 휴리스틱 시간 이동으로 복구하지 않는다.

## 4. 공유 상태 사용

상태의 정본 enum과 전이 우선순위는 [`state.md`](state.md)를 따른다.

- 지원 가능한 cue가 없으면 `no_supported_lip_cue`
- 재촬영으로 해결할 capture 실패는 `visual_rerecord_required`
- low confidence, calibration 부재 또는 A/V 충돌은 `visual_uncertain`
- calibration support OOD 또는 지원하지 않는 cue/view는 `visual_unsupported`
- upstream envelope/receipt 또는 필수 adapter 부재는 `dependency_unavailable`
- consent·privacy·schema·무결성 위반은 `visual_failed_closed`
- VE0--VE6의 해당 선행조건과 검증·허용된 claim을 모두 만족한 보조 후보만
  `visual_feedback_ready`

`VisualCueState`의 `missing`, `low_confidence`, `out_of_distribution`,
`backend_unavailable`도 0점이나 `observed`로 바꾸지 않는다.

## 5. 완료 주장 한계

VE0--VE6의 요구가 실제 evidence와 함께 충족돼도 이 계약이 말할 수 있는 것은
정해진 범위의 시각 sidecar engineering 검증을 수행했다는 사실뿐이다. 다음
주장은 별도 연구·검증·증거 없이는 허용하지 않는다.

- `research_validated`, `production_ready` 또는 교육 효과 완료
- 시각 피드백이 audio-only보다 일반적으로 우수함
- 한국어 L2 전체, 다른 L1·연령·기기·환경으로 일반화됨
- 내부 혀·연구개·성문·성대·airflow 또는 의학적 원인을 진단함

`engineering` label을 `research_validated`로 바꾸거나 legacy artifact 이름을 새
완료 receipt로 재사용해서 위 gate를 통과한 것처럼 만들 수 없다.
