# 입술 모양 피드백 Sidecar 컴포넌트

- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 media/audio 경로는 smoke 검증,
  visual feedback runtime은 `not_ready`
- 상세 설계: [`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)

## 1. 분리 원칙

visual sidecar는 하나의 기능 경계로 유지한다. domain/application은 파일 경로,
GPU, video SDK와 concrete `seungun` class를 알지 않고 typed port만 호출한다.
모든 adapter와 policy는 constructor 또는 composition root에서 명시적으로 주입한다.

```text
Entrypoint/Composition
  -> VisualFeedbackCoordinator
       -> visual domain/services
       -> ports
            <- adapters -> seungun/filesystem/model/ledger
```

## 2. 핵심 domain 객체

| 객체 | 책임 | 포함하지 않는 것 |
|---|---|---|
| `SeungunEvidenceSnapshot` | adapter가 검증·변환한 upstream target, 위치, binding, reliability와 provenance | raw audio, 수정된 score/threshold, visual 판정 |
| `VisualFeedbackRequest` | opaque video handle, upstream evidence reference, consent reference | local path, raw bytes, caller가 임의로 만든 expected phone |
| `VideoProbe` / `VideoQualityObservation` | 사전 계산된 QC, 정면 pose, lip ROI coverage와 timestamp 관측 | raw frame, 발음 오류, 음소 정답 |
| `LipCueMeasurement` / `VisualCueEvidence` | 특정 upstream target에 join된 visible lip cue와 calibration receipt | landmark 배열, 내부 조음 원인, audio confidence |
| `VisualFeedbackSupplement` | visual 상태, relation, allowlist action/claim candidate ID와 privacy-safe evidence summary | 전체 attempt 상태, audio 결과 변경, 전역 coaching 문장 |
| `AdapterReceipt` | backend/revision/config/license/timeout/egress/failure provenance | 비밀정보, 경로, 원시 생체정보 |
| `RestrictedVideoAsset` / `RestrictedPcmAudio` | local restricted copy와 container/PCM lineage를 opaque digest로 표현 | source path, 원본 삭제 권한, visual 신뢰성 판정 |
| `MediaProbe` / `Rgb24Sample` / `AudioDerivationReceipt` | strict A/V stream metadata, 전 구간 균등 in-process frame sample, 16 kHz mono PCM 파생 provenance | calibrated sync reliability, pose/QC 통과, 사용자 claim |
| `MediaPipeLandmarkBatch` | frame별 얼굴 수와 single-face 최소 lip geometry, backend/model/network-isolation provenance | raw frame, full mesh, identity, P6/P8 reliability 판정 |
| `VideoQcCandidateEvidence` | decoded RGB+landmark의 얼굴 coverage, luma/exposure, edge sharpness proxy, lip ROI, track continuity와 exact lineage | pose/occlusion 판정, calibrated pass 또는 reliability |
| `AvSyncCandidateEvidence` | lip aperture와 PCM energy의 교차상관 offset·anchor 후보, exact media/landmark/derivation lineage receipt | P6 threshold, calibrated reliability 또는 사용자 claim |
| `RuntimeVisualDetectionEvidence v2` | binding/sample/model/landmark와 video-QC/A-V-sync receipt를 한 detection digest에 결합 | QC/sync candidate를 supported로 승격 |
| `PolicyValidatedAvSyncOffset` | passed sync decision과 attempt/binding/audio/video/landmark/policy lineage가 결합된 measured audio→video offset | raw `candidate_unvalidated`, 기본값 0 ms 또는 caller offset |
| `PhoneWindowDecodeRequest` / `PhoneWindowDecodeReceipt` | already-bound selected anchor에서 host가 만든 bounded request와 exact FFmpeg frame index/PTS/RGB lineage | caller 선택 span, visual promotion 또는 cue 판정 |
| `PhoneWindowTrackingReceipt` / `RuntimePhoneWindowReceipt` | dense sample의 landmark/model lineage와 global QC/sync/context를 decode receipt digest에 결합 | P6/P8 reliability 또는 사용자 claim 승인 |
| `P6EvidenceValidationReceipt` | AI Hub 68 aggregate summary의 rights/admission assertion, disjoint split, pipeline digest, 24-point metric, calibration/reload digest 검증 결과 | dataset 접근·metric 생성 또는 visual reliability 승인 |
| `P8ClaimConformanceReceipt` | AI Hub 538 frozen replay의 6개 상태 scenario, exact lineage/catalog/policy/금지 fixture 검증 결과 | read-zero seal 생성·인증 또는 사용자 visual claim 승인 |

도메인 enum은 [`state.md`](state.md)에 정의된 exact value를 사용한다. 임의 문자열,
boolean 하나 또는 `None`만으로 missing/OOD/unsupported 상태를 축약하지 않는다.

## 3. application과 service 책임

| 컴포넌트 | 책임 | 금지 책임 |
|---|---|---|
| `SeungunAttemptBindingFacade` | 완료된 analysis를 한 번 canonicalize하고 caller 제공 attempt/prompt/media/consent/reliability로 request와 source record를 원자 구성 | detector import·재실행, reliability 합성, phone 선택·재판정 |
| `SeungunVisualHandoff` | exact binding으로 연결된 `VisualFeedbackRequest`와 `SeungunAnalysisSourceRecord` bundle | production persistence 또는 media decode |
| `VisualFeedbackCoordinator` | dependency → consent → QC → sync/join → tracking → calibration/catalog → cleanup 순서와 상태 우선순위 적용 | detector 직접 import, audio 재분석, 최종 global policy |
| `VideoAttemptFacade` | consent → 제한 저장 → probe/PCM → Seungun 1회 → optional visual → 삭제를 조율하고 cleanup 실패 시 audio·visual 반환값을 모두 억제 | audio 판정 변경, P6/P8 reliability 합성 |
| `_map_phone_span` 내부 pure function | 검증된 upstream phone span을 affine video timebase에 연결 | 위치 추정, 없는 span 생성, expected index 변경 |
| `MediaPipeVisualDetectorBridge.plan_phone_window` | already-bound handoff의 exact selected anchor와 policy-valid measured offset만으로 host decode request 생성 | caller window, raw sync candidate 사용, anchor 재선택 |
| `MediaPipeVisualDetectorBridge.detect_phone_window` | bounded dense RGB sample을 동일 model로 tracking하고 decode/tracking/runtime receipt DAG를 detection evidence에 추가 | global QC/sync sample 대체, 자체 visual publication |
| `VisualCalibrationArtifact.calibrate` | exact phone/cue/context rule의 support와 deviation 적용 | visual-only phone/error 판정 |
| `VisualClaimCatalog.lookup` | frozen action/claim ID를 exact key로 조회 | catalog 밖 문구나 행동 생성 |
| `VisualFeedbackSupplement` serializer | exact field/enum JSON과 ready 불변식 검증 | raw media/path/landmark 직렬화 |
| P6/P8 evidence validator | aggregate/frozen JSON의 exact schema·digest·불변식을 fail-closed 검사 | 원본 dataset 읽기, assertion 인증, gate promotion |

service는 HTTP response, CLI args 또는 concrete SDK object를 반환하지 않는다.

## 4. port와 adapter

| Port | Adapter 책임 | 실패 시 원칙 |
|---|---|---|
| `SeungunPronunciationEvidencePort` | `seungun` schema, prompt/media/time binding과 reliability를 visual DTO로 변환 | 누락·revision 불일치는 `dependency_unavailable`; audio 결과는 유지 |
| `ConsentRightsPort` | facial-video 목적, 보존, 삭제와 egress 권리 확인 | decode 전에 `visual_failed_closed` |
| `RestrictedVideoPort` | locator를 opaque handle로 바꾸고 요청 종료 삭제 receipt 관리 | path를 domain/public output에 노출하지 않음; TTL/orphan 회수는 미구현 |
| `PhoneWindowDecoderPort` | host-issued request의 `[start,end)` frame만 bounded RGB24로 decode하고 exact PTS/byte receipt 발급 | caller span 수용, unbounded decode 또는 codec gate 우회 |
| `VideoQualityPort` | codec/timestamp, 얼굴 수, pose, blur, 가림, lip coverage 관찰 | score 0으로 대체하지 않음 |
| `LipTrackingPort` | frame timestamp별 lip contour와 tracking 품질 반환 | phone 또는 내부 조음 원인을 생성하지 않음 |
| `AvSyncPort` | upstream audio clock과 video clock의 offset/drift 및 불확실성 반환 | 실패를 0 ms로 가정하지 않음 |
| `VisualCalibrationPort` | capture contract용 frozen calibration artifact 조회 | synthetic 값을 실제 보정값으로 승격하지 않음 |
| `VisualClaimCatalogPort` | frozen revision에서 visible cue용 action/claim ID와 적용 조건 조회 | catalog 밖 문구 생성 금지 |

`FrozenSeungunPronunciationEvidenceAdapter`만 공개 analysis schema를 해석한다.
이 adapter도 `korean_phone_ctc.frozen_detector`를 import하거나 호출하지 않으며,
host가 주입한 JSON-compatible analysis만 읽는다. visual domain 객체는 upstream
dataclass를 직접 보유하지 않는다. source record에 facade 발급 binding digest를
등록하고 adapter가 presented receipt와 exact match를 검사하므로, metadata가 같아도
등록되지 않은 재발급 receipt는 거부한다. `SameAttemptBindingReceipt v2`는
`audio_derivation_receipt_sha256`를 필수 필드와 self-digest 입력으로 직접 attest하며,
source record와의 exact match도 강제한다.

## 5. relation과 발행 규칙

`VisualEvidenceRelation`은 다음 세 값만 사용한다.

- `supports_upstream`: 동일한 upstream target에서 지원되는 visible cue가 관측됨
- `conflicts_with_upstream`: usable visual evidence가 upstream target과 충돌함
- `not_comparable`: span, observability, calibration 또는 cue capability가 없어 비교 불가

`visual_feedback_ready`는 `supports_upstream`일 때만 가능하다. conflict는 평균이나
가중합으로 지우지 않고 `visual_uncertain`으로 보존한다. `not_comparable`은
`no_supported_lip_cue`, `visual_uncertain`, `visual_unsupported` 또는
`dependency_unavailable` 중 실제 원인에 맞는 상태가 된다.

## 6. 재사용과 소유 경계

- 재사용 가능한 것은 typed domain model, pure join/validation/policy와 port 계약이다.
- SDK, decoder, filesystem, model weight, consent/reference/ledger 접근은 adapter 전용이다.
- visual advice catalog는 외부에서 보이는 입술 행동만 소유한다.
- audio advice, prosody advice와 최종 한 가지 행동 선택은 `seungun`이 소유한다.
- visual adapter 실패는 sidecar 결과만 종료하며 `seungun` 상태 전이를 만들지 않는다.

## 7. 구현 상태 해석

위에 적은 visual domain, port, coordinator, JSON artifact adapter와 deterministic
precomputed adapter는 `src/voice_coach/visual_feedback`에 존재한다. 별도
`src/voice_coach/pronunciation_attempts`에는 local file consent/admission·제한 저장,
strict FFmpeg/ffprobe probe·decode·audio derivation, pinned MediaPipe worker/adapter와
host-owned `VideoAttemptFacade` 계약이 있다.

제한 저장과 FFmpeg 경로는 H.264/HEVC 합성 media test와 HEVC Main/AAC,
1080x1920 portrait, 30 fps 사용자 영상 두 건으로 실행됐다. commit된 container·PCM과
Seungun 입력 WAV는 0400이며 FFmpeg global RGB sample은 전 구간에서 8 frame을
균등하게 고른다. local composition은 최대 width/height 각각 1920, global sample 64 MiB,
dense phone-window 12 frame/80 MiB의 압축 해제 byte gate를 유지한다.
`VideoQcCandidateAdapter`는 decoded RGB+MediaPipe landmark에서 얼굴 coverage,
luma/exposure bin, edge sharpness proxy, lip ROI와 track continuity를 측정하고 runtime
detection v3에 raw candidate receipt를 묶는다. calibrated decision wrapper는 exact
candidate와 전체 관측값, measurement/decision policy·code SHA를 결합하며 sync decision이
통과한 뒤에만 validated offset을 발급한다. TS174/TL174 actual-media 정책 evidence가
없으면 production에서는 `candidate_unvalidated`/`unavailable`이며 calibrated pass가 아니다.
`LipAudioAvSyncAdapter`는 합성 lip-aperture/PCM-energy 신호와 실제 사용자 두 영상의
runtime 경로에서 미보정 candidate/unavailable을 계산하지만 P6 전에는 reliability가
아니다. dense phone-window는 policy-valid offset이 있을 때만 global QC/sync 뒤에
추가 실행되며 cleanup 소유권을 새로 만들지 않는다. internal CLI의 권장 `--video-fd`는
pre-opened read-only `fd >= 3`으로 participant path를 argv에 노출하지 않는다. 이를 사용한
두 HEVC portrait 사용자 영상은 공식 landmark model과 CUDA Seungun에서 audio
`coaching_ready`·visual unavailable·cleanup 완료를 반환했다. content sync는 각각
`not_observed`와 `candidate_unvalidated`였고 실제 phone-window 세 receipt는
`not_issued`다. camera/upload API/UI, 실제 사용자 영상의 calibrated pose·blur·가림 QC,
P6/P8로 검증된 sync와
reference/calibration, TTL/orphan scavenger, production cleanup/audit ledger 및 사용자 UI는
준비되지 않았다. 공개 후보와 역할은
[`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)에
기록했지만 data admission과 artifact 생성은 시작하지 않았다. 따라서 현재 검증은
contract와 두 사용자 파일의 media/audio infrastructure smoke이며 calibrated visual
성능 또는 사용자 visual feedback 검증이 아니다.
