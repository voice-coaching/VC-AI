# seungun 보조 입술 모양 피드백 sidecar 상세 설계

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 media/audio 경로는 smoke 검증,
  visual feedback runtime은 `not_ready`
- 상위 시스템: [`seungun`](../../seungun/README.md)
- public 결과: `VisualFeedbackSupplement`
- 핵심 제약: visual sidecar는 `seungun` 결과를 보조하며 변경하거나 대체하지 않는다.

## 1. 목적과 현재 결론

이 문서는 사용자의 정면 영상에서 **직접 보이는 입술 모양과 움직임**을 분석하고,
`seungun`이 이미 선택한 발음 anchor에 연결할 수 있는 보조 피드백 후보를 만드는
시각 sidecar의 상세 정본이다.

```text
seungun-owned audio analysis and decision context
                |
                | immutable analysis reference + binding receipt
                v
       lip-feedback visual sidecar
                |
                | VisualFeedbackSupplement
                v
       host/seungun final decision and message
```

sidecar에는 prompt/audio/video digest, timebase, consent와 reliability를 묶는
self-digest receipt, 검증 adapter와 detector 비의존 `SeungunAttemptBindingFacade`가
구현됐다. facade는 이미 완료된 `PronunciationAnalysis`의 `Mapping` 또는
`to_json_object()`만 받아 canonical snapshot을 한 번 만들고 request/source record를
`SeungunVisualHandoff`로 원자적으로 반환한다. source record에는 facade 발급 binding
digest가 등록되고 adapter가 제시된 digest와 exact match를 검사하므로, 같은 필드로
별도 재발급한 receipt도 거부한다. local 제한 저장·FFmpeg audio derivation과 이
lineage를 facade handoff에 전달하는 bridge는 구현·unit test됐다.
`SameAttemptBindingReceipt v2`는 audio derivation receipt digest를 필수로 self-digest에
묶는다. internal CLI와 local concrete composition은 권장 pre-opened read-only
`--video-fd`로 합성 H.264/AAC MP4뿐 아니라 HEVC Main/AAC, 1080x1920 portrait,
30 fps 사용자 영상 두 건을 공식 MediaPipe model과 CUDA Seungun에서 실행했다. 두
실제 영상 모두 audio `coaching_ready`, attempt `audio_completed_visual_unavailable`,
cleanup 완료였으며, P6/P8 reliability와 policy-valid measured A/V sync가 없어 visual
claim은 fail-closed했다.

이 결손을 visual 코드가 detector 재실행, 파일명 일치 또는 임의 time offset으로
보완해서는 안 된다.

## 2. 범위

### 2.1 sidecar가 소유하는 책임

- 영상 처리 목적에 대한 동의와 권리 receipt 확인
- 제한된 임시 영상 lifecycle과 삭제 receipt
- container/video metadata probe와 decode
- 얼굴 수, 정면 pose, blur, 가림, 조명, lip ROI coverage 품질 판정
- frame timestamp별 lip landmark tracking
- 검증된 audio/video clock 사이의 A/V sync 추정과 실패 판정
- upstream phone span에 대응하는 visual window 생성
- 입술에서 직접 관찰 가능한 2D cue 추출
- visual reference/support 범위 확인과 visual calibration 적용
- visual evidence가 upstream anchor와 어떤 관계인지 기록
- allowlist catalog에서 supplemental visual action candidate 최대 한 개 선택
- `VisualFeedbackSupplement` 반환

### 2.2 sidecar가 소유하지 않는 책임

- prompt catalog, text normalization, G2P 또는 expected-phone 정본 생성
- audio decode/QC, content match, OOD 또는 pronunciation calibration
- XLS-R/CTC inference, MFA, prosody, S1/S2 또는 audio diagnosis
- upstream phone, 위치, candidate, score, threshold 또는 `is_error` 변경
- audio/visual score 평균 또는 새 overall confidence 계산
- 전체 issue top-1, coaching terminal status 또는 retry policy 결정
- 최종 사용자 메시지 렌더링
- LLM/HyperCLOVA/Qwen 호출
- 혀, 성문, 기류나 구강 내부처럼 영상에서 직접 보이지 않는 원인 주장
- 사용자 발음 정확도, 일반화 성능, production readiness 또는 교육 효과 주장

영상에서 직접 관찰되지 않는 책임을 추가하면 별도 연구 계약이 필요하며 이 문서의
sidecar 범위에 포함되지 않는다.

## 3. 근거와 구현 상태

현재 확인 가능한 upstream 근거는 다음과 같다.

- [seungun audio pipeline 정본](../../seungun/docs/CURRENT_PIPELINE_KO.md.md)
- [frozen detector 구현](../../seungun/src/korean_phone_ctc/frozen_detector.py)
- [온라인 inference와 bridge 상태](../../seungun/docs/HYPERCLOVAX_INTEGRATION_STATUS_KO.md)
- [audio reliability 개선 제안](../../seungun/docs/PIPELINE_RELIABILITY_IMPROVEMENT_PROPOSAL_KO.md)
- [sidecar 연동 계약](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)
- [P6·P8 공개 데이터 후보 근거 ledger](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)

관찰된 upstream 사실:

- `FrozenPronunciationDetector.analyze(audio_path, expected_phones)`는
  `PronunciationAnalysis`를 반환한다.
- schema version은 `korean_phone_ctc.pronunciation_analysis.v1`이다.
- phone별 expected index/token, nullable CTC span, candidate, score, threshold와
  `is_error`가 있다.
- `error_score`는 correctness confidence가 아닌 detector ranking score다.
- `observed_candidate`는 ground truth가 아닌 CTC alignment 후보다.
- deletion은 span이 없고 insertion은 visual join에 충분한 index/time anchor가 없다.
- 기존 audio-only bridge는 visual sidecar 계약이 아니며 최종 통합 근거로 사용하지
  않는다.

현재 구현된 항목:

- `SeungunPronunciationEvidencePort`, 실제 `PronunciationAnalysis v1` strict mapper와
  in-memory immutable source adapter
- attempt/prompt/analysis/expected-phone/audio/video/timebase/reliability/consent를 묶는
  self-digest receipt와 registered binding digest exact-match 검증
- caller가 제공한 upstream reliability receipt를 그대로 요구하며 detector를 import하거나
  재실행하지 않는 `SeungunAttemptBindingFacade`와 atomic `SeungunVisualHandoff`
- `SameAttemptBindingReceipt v2`의 필수 audio derivation provenance digest와 source
  record/presented receipt exact-match
- consent-before-I/O, no-follow local file admission, 0700 workspace, 0600 생성·commit 후
  0400 container/PCM, opaque handle, derived PCM lifecycle와 삭제 receipt
- strict MP4/(H.264|HEVC)/AAC probe, monotonic frame timestamp, 전 구간 균등 RGB24 frame
  sample과 16 kHz
  mono PCM·`AudioDerivationReceipt`를 만드는 FFmpeg/ffprobe adapter
- pinned model digest, bounded RGB 입력과 seccomp network 차단 뒤 얼굴 수·single-face
  최소 lip geometry만 반환하는 MediaPipe adapter/worker
- decoded RGB+MediaPipe landmark에서 single/multiple-face coverage, luma/exposure bin,
  edge sharpness proxy, lip ROI와 track continuity를 계산하고 lineage receipt를 발급하는
  `VideoQcCandidateAdapter`
- consent, precomputed video QC, 최소 lip landmark aggregate, affine timestamp sync와
  in-memory lifecycle adapter
- lip aperture와 restricted PCM energy를 교차상관해 미보정 offset·anchor candidate 및
  exact-lineage receipt를 계산하는 `LipAudioAvSyncAdapter`
- whole-clip QC/sync용 전 구간 8-frame sample을 보존한 뒤 exact selected
  `UpstreamPhoneAnchor`와 policy-valid measured A/V offset에서만 bounded dense RGB24
  window를 만들고 동일 landmark backend로 재-tracking하는 host-controlled 경로
- window request와 exact frame index/PTS/RGB/FFmpeg lineage의
  `PhoneWindowDecodeReceipt`, dense landmark lineage의 `PhoneWindowTrackingReceipt`,
  binding/QC/sync/context를 함께 묶는 `RuntimePhoneWindowReceipt`
- bounded pre-exec seccomp worker에서 frozen Seungun을 한 번 실행하는 runtime adapter와
  H.264/HEVC allowlist·공식 landmark model·CUDA CLI E2E
- hash-pinned `final_pronunciation_detector_v1` 완료 analysis를 현재 구현 범위의 sidecar
  anchor로 허용하는 제한된 upstream `supported` receipt; correctness confidence 주장은 없음
- strict JSON calibration/catalog loader와 `synthetic_contract_only` artifact
- `VisualFeedbackCoordinator`, 상태 우선순위, cleanup과
  `VisualFeedbackSupplement` canonical JSON round-trip
- synthetic smoke script와 unit test

현재 완료 또는 성공 실행이 확인되지 않은 항목:

- persistent source/reference 재로드, 수명과 retry/idempotency
- camera-upload API/UI와 제품 entrypoint
- 대표 population/device slice의 calibrated 실제 사용자 영상 tracker metric
- pose/occlusion 판정, calibrated video-QC pass/reliability와 content-sync candidate의
  실제 사용자 영상 성능·threshold
- 사용자 runtime P6/P8를 통과해 visual publication을 attest하는 reliability receipt
- TTL, 강제 종료·재시작 뒤 orphan workspace를 회수하는 scavenger와 persistent cleanup ledger
- 적격 실제 영상으로 실행·검증된 lip reference/calibration artifact
- P8 locked replay와 claim-policy conformance를 통과한 사용자용 visual action catalog
- `VisualFeedbackSupplement`의 host/presentation integration
- 두 실제 영상 smoke보다 넓은 대표 capture/device 실행, locked independent evaluation와
  claim-policy conformance

범위 수정 전 상세 multimodal 문서는
[역사 snapshot](../history/legacy_voice_coach_architecture_2026-08-26/SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)으로
보존되어 있으며 현재 정본이 아니다.

## 4. 의존성 구조

### 4.1 코드 의존성

```text
Entrypoint / Composition Root
  -> VisualFeedbackCoordinator
       -> visual domain models and policies
       -> SeungunPronunciationEvidencePort
       -> visual I/O ports

Concrete adapters
  -> implement visual I/O ports
  -> FrozenSeungunPronunciationEvidenceAdapter
       -> validates injected PronunciationAnalysis schema; detector 호출 없음
```

불변식:

1. `seungun` package는 visual package를 import하지 않는다.
2. visual domain은 `korean_phone_ctc`, filesystem, network, GPU, codec SDK 또는
   landmark SDK를 import하지 않는다.
3. concrete seungun adapter만 upstream JSON schema를 안다. detector type은 import하지 않는다.
4. visual application은 이미 생성된 immutable analysis reference를 요청한다.
5. visual 요청 처리 중 `FrozenPronunciationDetector.analyze()`를 호출하지 않는다.
6. adapter는 upstream evidence를 읽기 전용 snapshot으로 변환한다.
7. host가 supplement를 최종 결과에 사용할지 결정한다.

### 4.2 데이터 의존성

visual 처리는 다음 항목이 모두 같은 attempt를 가리킬 때만 진행한다.

- seungun analysis reference
- verified prompt/expected-phone binding
- detector-input audio source와 timebase receipt
- 필수 audio derivation provenance digest; binding v2 self-digest에 직접 포함
- submitted video source와 timebase receipt
- consent purpose와 retention policy
- host가 선택한 upstream phone anchor reference

하나라도 누락되거나 서로 다르면 positional heuristic으로 복구하지 않는다.

## 5. 핵심 domain 계약

아래 형식은 현재 구현된 Python 계약을 요약한다. 세부 exact field와 validation은
`src/voice_coach/visual_feedback/models`가 정본이다.

### 5.1 요청

```python
@dataclass(frozen=True, slots=True)
class VisualFeedbackRequest:
    analysis_ref: SeungunAnalysisReference
    upstream_phone_expected_index: int
    video_handle: RestrictedVideoHandle
    binding_receipt: SameAttemptBindingReceipt
    consent_receipt: ConsentReceipt
    capture_contract_id: str
    population_id_sha256: str
    context_id: str
```

호출자가 raw audio path, arbitrary expected-phone array 또는 audio detector 설정을
sidecar에 전달하지 않는다. 분석할 phone anchor는 host/`seungun`이 먼저 선택한다.
sidecar가 detector score를 정렬해 top-1을 다시 고르지 않는다.

### 5.2 upstream envelope

```text
SeungunEvidenceEnvelope
  immutable PronunciationAnalysis v1
  analysis schema/version/digest
  attempt binding ID
  prompt ID/revision
  expected-phone source revision/digest
  detector-input audio source binding
  audio derivation receipt digest
  audio timebase/transform receipt
  upstream reliability/support receipt
  facade-issued registered binding digest
  facade/backend revision
```

현재 `SeungunAnalysisSourceRecord`, `SeungunEvidenceSnapshot`,
`SeungunAttemptBindingFacade`와 in-memory source adapter가 이 envelope의 strict 내부
계약을 구현한다. facade가 만든 `SeungunVisualHandoff`는 request와 source record를
함께 반환하고 registered binding digest를 강제한다. local composition과 media
derivation adapter는 합성 CLI와 두 HEVC portrait 사용자 파일의 internal E2E까지
실행됐지만 persistent source 재로드는 아직 없다.
현재 configured reliability receipt는 P6/P8 미완료를 나타내므로 visual publication은
`dependency_unavailable`/unavailable로 닫힌다.

### 5.3 read-only phone anchor

```text
UpstreamPhoneAnchor
  anchor_ref
  expected_index
  expected_phone
  alignment_operation
  start_ms | null
  end_ms | null
  observed_candidate | null
  error_score
  operating_threshold
  is_error
  score_semantics
```

visual code는 이 필드를 복사해 감사 근거로 남길 수 있지만 새 값을 써서 원본을
가리거나 public audio diagnosis로 반환하지 않는다.

### 5.4 visual evidence

```text
VisualLipCueEvidence
  evidence_id
  cue_id
  cue_state
  video_start_ms
  video_end_ms
  raw_measurement_summary
  reference_support_state
  calibrated_deviation | null
  measurement_backend_id/revision
  reference_id/revision
  calibration_id/revision
  reason_codes[]
```

`calibrated_deviation`은 적합한 reference와 calibration receipt가 있을 때만 존재한다.
결측, 미지원 또는 저신뢰를 숫자 0으로 채우지 않는다.

### 5.5 supplemental action candidate

```text
SupplementalVisualActionCandidate
  action_id
  approved_claim_id
  catalog_revision
  upstream_phone_anchor_ref
  supporting_visual_evidence_ids[]
```

candidate는 host가 선택 가능한 allowlist 식별자다. 최종 문장, 전체 severity/confidence,
audio issue 변경을 포함하지 않는다. 한 sidecar 결과에는 최대 한 개만 허용한다.

## 6. 상태 enum

### 6.1 `VisualFeedbackStatus`

| 값 | 의미 | 허용 결과 |
|---|---|---|
| `visual_feedback_ready` | 직접 보이는 cue, binding, support와 calibration이 모두 유효하고 supplemental candidate가 있음 | candidate 1개와 근거 반환 |
| `no_supported_lip_cue` | 요청과 영상은 유효하지만 선택된 phone에 이 시스템이 주장할 직접 가시 cue가 없음 | candidate 없이 근거·reason 반환 |
| `visual_rerecord_required` | blur, 가림, 얼굴 이탈, sync 실패처럼 다시 촬영해 해결 가능한 품질 문제 | reason만 반환, candidate/advice 없음 |
| `visual_uncertain` | evidence 부족, 저신뢰, calibration 부족 또는 upstream과의 conflict로 자동 visual action을 고를 수 없음 | candidate 없음 |
| `visual_unsupported` | pose/camera/cue/population이 동결된 지원 범위 밖 | candidate 없음 |
| `dependency_unavailable` | seungun handoff/binding/reliability 계약 또는 필수 upstream dependency가 없음 | video 분석·candidate 발행 차단 |
| `visual_failed_closed` | 동의·권리·schema·artifact 무결성 또는 privacy gate 실패 | 처리와 공개 결과 발행 차단 |

이 상태는 audio/coaching terminal status가 아니다. host가 이를 audio 성공·실패로
변환해서는 안 된다.

### 6.2 `VisualCueState`

| 값 | 의미 |
|---|---|
| `observed` | 지원 범위 안에서 cue가 실제 측정됨 |
| `not_observable` | phone/cue가 정면 입술 영상에서 본질적으로 직접 관찰 불가 |
| `missing` | 관찰 가능해야 하지만 frame/track/window에 측정값이 없음 |
| `low_confidence` | 측정은 있으나 동결 품질 기준 미달 |
| `out_of_distribution` | 사전등록된 visual support population 밖 |
| `backend_unavailable` | tracker/reference/calibration backend를 사용할 수 없음 |

### 6.3 `VisualEvidenceRelation`

| 값 | 의미 |
|---|---|
| `supports_upstream` | 직접 보이는 visual cue가 선택된 upstream anchor에 대해 허용된 보조 설명과 양립함 |
| `conflicts_with_upstream` | 유효한 visual cue와 upstream anchor 해석이 양립하지 않음; 어느 쪽도 덮어쓰지 않음 |
| `not_comparable` | span, observability, cue 또는 calibration이 없어 비교 자체가 성립하지 않음 |

`supports_upstream`은 audio 진단을 독립 검증했다는 뜻이 아니다.
`conflicts_with_upstream`도 visual이 audio truth를 대체한다는 뜻이 아니다.

## 7. logical component와 객체 책임

### 7.1 application/service

| 객체 | 책임 | 금지 책임 |
|---|---|---|
| `SeungunAttemptBindingFacade` | completed analysis canonical snapshot, caller 제공 attempt/prompt/media/consent/reliability binding, atomic request/source bundle | detector import·재실행, reliability 합성, upstream phone 재선택 |
| `SeungunVisualHandoff` | registered binding으로 연결된 request와 source record 동시 반환 | production persistence, media decode 또는 최종 coaching 결정 |
| `VisualFeedbackCoordinator` | 요청 검증, port 순서, 상태 우선순위, cleanup, supplement 반환 | concrete SDK 호출, 최종 coaching 결정 |
| `_map_phone_span` | 검증된 affine transform으로 upstream span을 visual window에 연결 | deletion/insertion span 발명 |
| `VisualCalibrationArtifact.calibrate` | exact cue/phone/context의 support와 deviation 계산 | 혀·성문·기류 추정 또는 phone 오류 판정 |
| `VisualClaimCatalog.lookup` | allowlist에서 candidate 최대 1개를 exact key로 조회 | global issue top-1 또는 최종 메시지 생성 |

### 7.2 port

| port | 목적 |
|---|---|
| `SeungunPronunciationEvidencePort` | immutable upstream analysis/binding envelope resolve |
| `ConsentRightsPort` | purpose, data category, retention, deletion 권리 검증 |
| `RestrictedVideoPort` | raw locator를 opaque handle로 바꾸고 요청 종료 삭제 관리; TTL/orphan 회수는 미구현 |
| `VideoQualityPort` | codec/timestamp, 얼굴 수, pose, blur, 가림, 조명과 lip ROI coverage 관찰 |
| `LipTrackingPort` | frame별 lip landmark와 tracking quality 제공 |
| `AvSyncPort` | 검증된 source binding 안에서 offset/drift와 reliability 측정 |
| `VisualCalibrationPort` | visual measurement support와 보정 artifact 조회 |
| `VisualClaimCatalogPort` | allowlist supplemental action/claim ID 조회 |

모든 port는 typed result와 failure state를 반환한다. backend unavailable을 빈
landmark, 0점 또는 성공 result로 바꾸지 않는다.

### 7.3 adapter

- 구현됨 — handoff facade/seungun adapter: completed analysis canonical snapshot,
  registered binding digest exact-match와 immutable reference 검증; detector import/호출 없음
- 구현됨 — internal restricted record adapter: precomputed record lifecycle과 삭제 receipt
- 구현됨 — internal QC/tracker/sync adapter: 주입된 probe/최소 좌표/timestamp로 계약 계산
- 구현됨 — calibration/catalog adapter: versioned strict local JSON 조회
- 구현됨 — local media admission/storage: consent-before-I/O, opaque 0700 workspace,
  0600 생성·commit 후 0400 container/PCM과 exact lifecycle·삭제 receipt
- 구현됨 — FFmpeg/ffprobe adapter: strict MP4/(H.264|HEVC)/AAC probe, 전 구간 균등
  bounded RGB24 frame sample, 16 kHz mono PCM derivation과 tool/timebase/digest receipt;
  H.264/HEVC 합성 media unit test와 HEVC portrait 사용자 영상 두 건 실행 검증
- 구현됨 — FFmpeg/ffprobe·MediaPipe·Seungun worker pre-exec launcher: no-new-privs,
  CPU/address-space/FD/core limit, io_uring·process/session/group escape 차단과 thread-only
  clone; PDEATHSIG 설정 전후 expected-parent 검증, 해당 prctl 해제와 cross-process
  signal/pidfd/ptrace/process_vm 차단, self/native-thread signal 유지; CUDA는 제한 AF_UNIX
  socketpair/listener만 허용하고 connect/accept/message I/O 차단
- 구현됨 — FFmpeg nonblocking stdout/stderr drain과 예외 시 unconditional process-group kill
- 구현됨 — `VideoQcCandidateAdapter`: decoded RGB+landmark에서 얼굴 coverage,
  luma/exposure, edge proxy, lip ROI와 continuity 후보 및 exact-lineage receipt 계산
- 구현됨 — `LipAudioAvSyncAdapter`: lip aperture와 restricted PCM energy의 교차상관으로
  미보정 offset·anchor candidate와 exact-lineage receipt 계산; 합성 신호 unit test
- 구현됨 — dense selected-phone path: facade가 전 구간 QC/sync를 먼저 수행한 뒤
  already-bound Seungun anchor와 policy-valid offset으로만 phone window를 발급하고,
  FFmpeg bounded RGB24 decode와 MediaPipe re-tracking을 decode/tracking/runtime receipt
  digest DAG에 결합; raw candidate이면 실행하지 않음
- 부분 구현 — MediaPipe face-landmarker worker: pinned digest, bounded input, network 차단과
  최소 lip geometry 계약; 공개 portrait와 두 HEVC portrait 사용자 영상에서 inference/QC
  candidate 경로는 관측했지만 population-level tracker 정확도는 미검증
- 구현됨 — runtime visual detection v3: sample/probe/binding, frame timestamp,
  landmark/model, raw QC/sync candidate v2, calibrated decision v2와 validated-offset v2의
  exact lineage 및 mismatch 선행 차단 unit test
- 구현됨 — calibrated decision wrapper: 원 candidate receipt와 전체 관측값, measurement/
  decision policy 및 code SHA를 digest에 결합하고 synchronized decision 뒤에만 offset 발급
- 미검증 — pose/occlusion별 QC/sync threshold와 두 사용자 영상에서 관측한 candidate의
  population-level 성능; TS174/TL174 actual-media decision policy는 미생성
- 미구현 — append-only production audit adapter
- 구현됨 — aggregate/frozen summary P6/P8 validator와 `p6|p8 --bundle/--bundle-fd` CLI;
  성공 receipt도 reliability/claim을 승인하지 않는 non-promotional 상태로 고정
- 실행 완료 — AI Hub 68 24-point geometry와 고정 `어`/`ㅓ` P6B reference/calibration,
  5-camera·actor/attempt-disjoint metric, 85/85 production tracker parity, 0400 fresh reload
- 미실행 — AI Hub 538 TS174/TL174 decision calibration과 locked P8 replay; 따라서 실제
  runtime promotion identity/receipt는 미발급

외부 SDK, model hub, filesystem, subprocess 또는 network 접근은 adapter 안에 둔다.
얼굴·음성·landmark의 remote egress는 기본 거부한다. 권리·정책의 명시적 허용 전에는 local/offline
adapter만 허용한다.

FFmpeg/ffprobe, MediaPipe와 Seungun worker는 pre-exec launcher 뒤에서 실행된다.
io_uring과 일반 fork/clone3/session/group escape는 차단되고 `CLONE_THREAD`만 허용된다.
CUDA 예외는 AF_UNIX socketpair와 연결되지 않은 `SOCK_SEQPACKET` listener뿐이며 connect는
ENOENT, accept와 message I/O는 차단된다. FFmpeg는 nonblocking pipe와 unconditional
killpg를 사용한다. `PR_SET_PDEATHSIG` 설정 전후 expected-parent를 확인하고 해당 prctl의
해제와 cross-process signal/pidfd/ptrace/process_vm을 차단하되 self/native-thread signal은
유지한다. 이 경계는 전체 sandbox가 아니며 `RLIMIT_NPROC`/`RLIMIT_FSIZE`도 없다. runtime
detection v3는 candidate→decision→offset lineage와 mismatch를 unit test로 검사하며, 고정 Seungun v1 upstream
anchor만 제한적으로 `supported`다. 사용자 runtime P6/P8 visual reliability receipt는
없으므로 전체 production security 또는 same-attempt visual claim lineage 완료를
주장하지 않는다.

별도 `gate_evidence.py`는 P6에서 AI Hub 68 aggregate-only rights/admission assertion,
actor+attempt disjoint split, pipeline revision/digest, 24-point metric, calibrated artifact와
fresh-process reload summary를 검사한다. P8에서는 AI Hub 538 frozen summary의
ready/missing/low-confidence/OOD/conflict/unsupported 6개 scenario와 exact binding/anchor/
calibration/catalog/policy, forbidden fixture와 모든 catalog claim을 재생 검사한다.
legacy P6/P8 summary receipt는 non-promotional이다. 별도 automated P6 v4 계약은 실제
P6/P6B producer와 tracker parity에 더해 TS174/TL174 actual-media decision-policy artifact,
전수 lineage count와 fresh reload를 요구한다. 마지막 evidence가 없어 실제 승격은 보류다.
P8 read-zero seal v3 역시 measurement/decision/phone-window code·policy를 모두 pin하지만
실제 locked source seal/evaluation은 아직 없다.

## 8. 처리 흐름

### V0. upstream dependency gate

1. request schema와 enum을 검증한다.
2. `SeungunPronunciationEvidencePort`로 immutable reference를 resolve한다.
3. analysis schema, prompt/expected-phone/audio/timebase/reliability receipt와 registered
   binding digest exact-match를 확인한다.
4. host가 선택한 phone anchor가 envelope 안의 동일 anchor인지 확인한다.
5. receipt가 없거나 불일치하면 video inference를 시작하지 않고
   `dependency_unavailable`로 종료한다.

facade와 internal source를 사용한 자동 dependency 검증, local media
admission/derivation과 handoff lineage bridge의 unit test는 실행 가능하다. internal
CLI/local composition은 사전에 read-only로 연 `fd >= 3`을 받는 권장 `--video-fd`로
H.264 또는 HEVC video 한 개와 AAC audio 한 개를 가진 MP4를 처리한다. 합성
H.264/AAC smoke와 HEVC Main/AAC, 1080x1920 portrait, 30 fps 사용자 영상 두 건을
공식 MediaPipe model과 CUDA Seungun에서 실행했고, 실제 두 영상 모두 audio
`coaching_ready`, `audio_completed_visual_unavailable`, cleanup 완료를 반환했다.
participant-sensitive path는 argv에 포함되지 않는다. `--video`는 path가 argv와 shell
history에 노출될 수 있는 내부 편의 기능이다.
현재 reliability bridge도 P6 calibration과 P8 catalog가 없으면 visual을 차단하므로
사용자 claim 단계로 진행할 수 없다.

### V1. 요청·동의 gate

1. visual processing purpose, data category, retention과 deletion receipt를 확인한다.
2. 동의·권리·privacy 계약이 없으면 media를 decode하지 않고
   `visual_failed_closed`로 종료한다.

### V2. 제한된 영상 intake

1. raw locator를 opaque restricted handle로 변환한다.
2. codec, duration, FPS, frame timestamp와 audio-track metadata를 관찰한다.
3. 요청 종료 cleanup 책임을 등록한다. TTL·orphan scavenger·persistent cleanup ledger는
   없으므로 장기 잔존 회수 완료를 주장하지 않는다.
4. path, digest와 raw metadata를 public supplement에 넣지 않는다.

현재 `LocalRestrictedVideoAdmissionAdapter`가 1과 3의 local file 경계를 구현하고,
commit된 container/PCM을 0400으로 고정한다. `FfmpegAvContainerAdapter`가 2의 strict
MP4/(H.264|HEVC)/AAC probe·timestamp 검증, 전 구간 균등 RGB24 frame sample과
embedded audio derivation을 구현한다. local composition은 최대 width/height 각각 1920,
전 구간 8-frame sample 64 MiB, dense phone-window 최대 12-frame/80 MiB의 압축 해제
RGB byte gate를 유지하며 codec-neutral capture contract ID는
`local_allowlisted_video_aac_mp4_v2`다. 합성 media test 외에 위 두 HEVC portrait 사용자 영상의
admission/decode가 통과했다. 두 영상 모두 `video_qc_candidate_not_calibrated`였고
content-sync는 각각 `not_observed`와 `candidate_unvalidated`여서 visual은 차단됐다.
camera/upload API/UI와 formal capture/QC report는 없다.

### V3. video QC와 정면 pose

관찰 항목:

- 한 명의 얼굴만 지속적으로 추적되는지
- yaw/pitch/roll이 동결된 정면 capture contract 안인지
- blur, 조명, 입술 가림과 ROI 크기가 기준을 만족하는지
- frame drop과 timestamp monotonicity가 허용되는지

다시 촬영으로 해결 가능한 실패는 `visual_rerecord_required`, 구조적으로 지원하지
않는 pose/camera는 `visual_unsupported`다. threshold는 calibration 없이 문서에서
임의로 숫자를 만들지 않는다.

현재 `VideoQcCandidateAdapter`는 decoded RGB와 exact-lineage MediaPipe landmark에서
single/multiple-face coverage, 전체·lip-ROI luma/노출 bin, neighbor-luma edge sharpness
proxy, lip ROI coverage, single-face run과 lip-center continuity를 계산한다. runtime
detection v3가 이 candidate receipt를 binding/landmark와 함께 묶는다. 별도 calibrated
decision wrapper는 exact candidate의 모든 관측값을 복사하고 frozen policy로 pass/fail을
결정하며, A/V offset은 synchronized decision에서만 생성한다. 실제 TS174/TL174 정책
evidence가 없으면 production composition은 wrapper를 연결하지 않고
`candidate_unvalidated`/`unavailable`에서 닫힌다.

여기서 video-QC calibration은 영상 한 건을 단순히 “정상”으로 표시하는 작업이 아니다.
고정된 capture contract와 적용 population에서 actor/attempt-disjoint calibration split을
만들고, 얼굴 coverage·다중 얼굴·전체/입술 ROI 노출·edge proxy·track continuity 등의
측정값이 실제 landmark/QC 사용 가능성과 어떤 관계인지 slice별로 추정하는 절차다.
그 결과로 metric report, support/OOD 범위, rerecord/unsupported/selective-pass 기준,
pipeline/model/data hash와 fresh-process reload parity receipt를 생성해야 한다. 현재 두
사용자 영상에서 얻은 candidate metric은 이 population-level 기준을 선택하거나 검증할
표본·정답·독립 split이 아니므로 calibration artifact로 승격하지 않는다.

### V4. lip tracking

frame timestamp마다 lip contour와 landmark visibility를 기록한다. face landmark는
정면 pose와 lip ROI 정규화에만 사용한다. 얼굴 표정, gaze, 신원, 감정 또는 건강
상태를 추론하지 않는다.

track이 끊기거나 occlusion이 심하면 `missing` 또는 `low_confidence`로 보존한다.
보간은 동결된 짧은 gap 정책 안에서만 가능하며 원본 관측과 보간값을 구분한다.

현재 MediaPipe worker는 RGB frame별 얼굴 수와 single-face 네 점의 최소 입술
geometry를 반환하도록 구현됐다. 공식 model의 공개 test portrait뿐 아니라 두 HEVC
portrait 사용자 영상에서 landmark/QC candidate 경로를 실행했다. 다만 두 건의 smoke는
actor/device slice별 tracker 정확도나 pose/occlusion metric을 검증하는 평가가 아니다.
따라서 이 구현만으로 V4를 완료하지 않는다.

### V5. A/V sync

sync는 seungun-owned audio timebase receipt와 submitted video clock 사이에서만
계산한다. 영상의 audio track은 sync measurement에 사용할 수 있지만 발음 진단에는
사용하지 않는다.

검증 대상:

- source binding identity
- start timestamp와 trim/pad transform
- offset, drift와 measurement reliability
- audio/video duration compatibility

불확실한 sync를 `offset_ms=0`으로 바꾸지 않는다. 다시 촬영 가능한 실패는
`visual_rerecord_required`, evidence 충돌은 `visual_uncertain`이다.

FFmpeg probe는 stream start/timebase와 video frame timestamp를 보존한다.
`LipAudioAvSyncAdapter`는 동일 container의 MediaPipe lip-aperture sequence와 16 kHz PCM
energy를 offset 범위에서 교차상관하고, 상위 강도 event를 anchor candidate로 골라
derivation/landmark/container/policy lineage가 묶인 receipt를 발급한다. backend 코드는
`code_present/unvalidated`다. 합성 신호 test에 더해 실제 두 영상에서는 각각
`not_observed`와 `candidate_unvalidated`를 관측했다. P6 calibration 전에는 이를
reliability 또는 `visual_feedback_ready` 근거로 승격하지 않는다.

### V6. upstream phone window join

1. host가 선택한 `expected_index`와 expected token을 exact match한다.
2. valid forward `start_ms/end_ms`가 있는지 확인한다.
3. A/V transform으로 video time window를 계산한다.
4. boundary uncertainty와 span source를 별도 receipt에 남긴다.
5. capture window 안에 필요한 frame coverage가 있는지 확인한다.

deletion의 null span과 insertion tuple에 positional span을 발명하지 않는다. 이 경우
relation은 `not_comparable`이며 지원 cue가 없으면 `no_supported_lip_cue` 또는
`visual_uncertain`으로 종료한다.

이 경로는 현재 구현돼 있다. caller는 window 시작/끝을 제공할 수 없고,
`MediaPipeVisualDetectorBridge`가 already-bound `SeungunVisualHandoff`의 exact selected
`UpstreamPhoneAnchor`를 다시 parse·교차검증한다. policy-valid
`PolicyValidatedAvSyncOffset`의 attempt/binding/audio/video/landmark/policy lineage가 모두
일치할 때만 audio span을 video clock으로 옮기고 고정 padding과 clip bounds를 적용한다.
그 후 `FfmpegAvContainerAdapter`가 `[start,end)`의 exact frame PTS를 bounded RGB24로
decode하고, MediaPipe가 dense sample을 다시 tracking한다. decode/tracking/runtime
receipt는 서로의 SHA-256을 연결하며 visual promotion을 자체 승인하지 않는다.

현재 실제 두 영상에서는 A/V sync가 policy-valid `synchronized`로 승격되지 않았으므로
window planner가 `None`으로 닫혔다. 따라서 실제 `PhoneWindowDecodeReceipt`,
`PhoneWindowTrackingReceipt`, `RuntimePhoneWindowReceipt`는 모두 `not_issued`이며, 이는
잘못된 window를 추정하는 대신 dense decode 전 차단한 정상 동작이다. whole-clip QC/sync
sample과 attempt cleanup은 그대로 유지되고 cleanup은 한 번만 수행된다.

### V7. 직접 가시 2D lip cue

첫 baseline은 landmark에서 직접 계산 가능한 cue로 제한한다.

- 입술 세로 aperture와 시간 변화
- 입술 가로 width와 width/aperture ratio
- lip closure/opening event와 지속 시간
- lip contour motion magnitude/velocity
- lip corner/contour visibility와 좌우 measurement consistency

위 값은 영상 관측이다. 곧바로 특정 phoneme, 혀 위치, 성문 상태, 기류 또는 발음
오류의 원인으로 바꾸지 않는다. 3D 복원, appearance encoder와 video-to-phone
classifier는 이 baseline gate를 모두 통과하기 전에는 구현·학습하지 않는다.

### V8. reference와 calibration

reference 조회에는 최소한 cue ID, expected phone/context, capture contract,
normalizer revision과 지원 population이 포함되어야 한다. 단일 화자의 얼굴을 정답
템플릿으로 사용하지 않는다.

다음 경우 score/deviation을 만들지 않는다.

- reference rights/checksum/revision 불명
- capture condition 또는 cue가 지원 범위 밖
- calibration cohort와 적용 population 역할 불일치
- speaker leakage 또는 locked evaluation 접근 흔적
- reload parity 실패

값이 없으면 `missing`, `out_of_distribution` 또는 `backend_unavailable`로 보존한다.

### V9. upstream relation

relation policy는 visual evidence를 upstream anchor와 나란히 기록한다.

- 허용된 직접 가시 cue가 upstream anchor와 양립: `supports_upstream`
- 둘 다 유효하지만 양립하지 않음: `conflicts_with_upstream`
- span/cue/support/calibration 부족: `not_comparable`

conflict가 생겨도 audio score나 visual measurement를 덮어쓰지 않는다. sidecar는
`visual_uncertain`을 반환하고 host가 후속 정책을 결정하게 한다.

### V10. supplemental action candidate

candidate를 반환하려면 다음을 모두 만족해야 한다.

- status 후보가 `visual_feedback_ready`
- cue state가 `observed`
- binding, support, calibration과 artifact provenance가 유효
- relation이 `supports_upstream`
- visual action catalog에 expected context와 cue에 대한 검증·허용된 claim ID가 있음
- supporting evidence ID가 하나 이상 있음

candidate는 최대 하나이며 고정 ID와 evidence link만 포함한다. LLM으로 빈 catalog를
채우거나 신체 조언을 생성하지 않는다.

### V11. 결과·cleanup

1. `VisualFeedbackSupplement`를 구성한다.
2. 최종 issue/status/message를 만들지 않는다.
3. audit event에는 status, reason, revisions와 opaque receipt ID만 기록한다.
4. 성공·실패와 관계없이 임시 media 삭제를 시도하고 deletion receipt를 남긴다.
5. cleanup 실패는 숨기지 않는다. host `VideoAttemptFacade`는 이미 계산된 audio와
   visual 반환값을 모두 제거하고 attempt를 `failed_closed`, visual stage를
   `cleanup_failed`로 반환하며 별도 incident 절차에 전달할 수 있다.

## 9. public 출력 계약

```text
VisualFeedbackSupplement
  schema_version
  status: VisualFeedbackStatus
  reason_codes[]
  upstream_binding_ref
  upstream_phone_anchor_ref
  cue_evidence[]
  evidence_relation: VisualEvidenceRelation
  supplemental_action_candidate | null
  visual_backend_receipts[]
  reference_calibration_receipts[]
  media_deletion_receipt | null
  notices[]
```

public DTO에는 raw video/audio, filesystem path, speaker ID, prompt 원문, full
landmark sequence, biometric embedding, secret 또는 자유 형식 전문가 메모를 넣지
않는다.

다음 불변식을 검증한다.

- `visual_feedback_ready`일 때만 action candidate가 존재한다.
- 다른 상태에서는 candidate가 항상 `null`이다.
- candidate는 정확히 하나의 upstream anchor와 하나 이상의 visual evidence ID를
  참조한다.
- supplement에는 변경된 upstream phone/span/score/threshold가 없다.
- supplement에는 final message가 없다.
- unknown enum/schema는 fail-closed다.

## 10. 상태 판정 우선순위

한 요청에 여러 문제가 있으면 다음 순서를 사용한다.

1. seungun handoff/binding/reliability dependency 누락
   -> `dependency_unavailable`
2. consent/rights/privacy/schema/artifact integrity 실패
   -> `visual_failed_closed`
3. 다시 촬영으로 해결 가능한 video/sync 품질 실패
   -> `visual_rerecord_required`
4. 고정 capability 밖의 입력/cue/population
   -> `visual_unsupported`
5. low-confidence, calibration 부족 또는 conflict
   -> `visual_uncertain`
6. 유효 입력이지만 직접 가시 cue가 없음
   -> `no_supported_lip_cue`
7. 모든 visual 발행 조건과 allowlist 통과
   -> `visual_feedback_ready`

blocked 상태에서 다른 cue, 다른 phone 또는 audio-only candidate로 backfill하지
않는다. audio-only 처리는 host/`seungun`의 별도 책임이다.

## 11. privacy와 media lifecycle

얼굴 영상, 음성 track, landmark와 derived motion은 민감한 연구 데이터로 취급한다.

필수 원칙:

- visual 목적에 대한 명시적 consent 없이 decode하지 않음
- 수집 목적, 보존 기간, 삭제 방법과 접근 역할 분리
- raw media와 상세 landmark는 public log/Git/LLM payload에 포함하지 않음
- 기본 local/offline 처리와 remote egress `denied`
- 임시 workspace는 요청 단위 격리와 최소 권한 적용; TTL/orphan scavenger는 미구현
- 정상·예외·취소 경로 모두 cleanup 수행
- 삭제 성공/실패를 receipt로 기록
- reference/training publication 전 재식별·동의·license 검토

visual reference와 calibration artifact도 source consent와 허용 목적이 확인되지 않으면
adapter가 `backend_unavailable` 또는 fail-closed를 반환한다.

## 12. 데이터와 artifact 역할

역할을 섞지 않는다.

```text
development
  cue/algorithm 개발

calibration
  support 범위, visual threshold와 selective policy 선택

independent evaluation
  동결 artifact와 policy 평가

claim-policy conformance
  동결 결과·catalog의 evidence binding, visible-cue allowlist와 금지 claim 자동 검사
```

공개 후보의 현재 역할과 blocker는
[`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)에
기록한다. 공개 metadata audit, 실제 admission/access, calibration 실행, 잠금 독립평가와
claim-policy conformance를 한 상태로 합치지 않는다.

필수 provenance:

- data/source/capture-contract revision
- consent/license status
- speaker/attempt split과 교집합 검사
- tracker/normalizer/cue extractor hash
- reference/calibration hash
- action catalog revision
- seed와 실행 환경
- 완료·실패·partial 상태

calibration이나 independent evaluation 자료를 detector 학습 또는 threshold 재선택에
재사용하지 않는다. `seungun` audio split/locked 결과를 visual gate의 정답으로
간주하지 않는다.

## 13. 단계별 구현·검증 gate

현재 자동화 상태는 Gate 1에 필요한 주요 synthetic case와 detector 비의존 handoff,
local 제한 저장·H.264/HEVC FFmpeg media 경로, dense phone-window receipt DAG의
infrastructure test가 구현·실행된 수준이다.
Gate 0의 공개 데이터 후보 감사는 수행했지만 실제 dataset admission·rights receipt와
제품 composition/entrypoint의 성공 실행 근거가 없으므로 formal Gate 0 또는 Gate 1
완료 receipt는 발급하지 않았다. 두 HEVC portrait 사용자 영상에서 MediaPipe/QC
candidate와 audio coaching까지 관측했지만 formal capture/QC report나 calibration이
아니므로 Gate 2 이후는 `not_started`다.

### Gate 0 — data·consent·ontology audit

시작 조건:

- visual 목적 consent와 deletion 계약
- versioned dataset evidence ledger와 candidate/admission 상태 분리
- 직접 가시 cue ontology와 금지 claim 목록
- capture contract와 지원 범위 초안
- speaker/attempt-disjoint split 계획
- seungun handoff facade와 binding receipt 계약

완료 조건:

- 미확인 권리·동의·label 역할 0
- audio/visual responsibility matrix freeze
- 자동 A/V binding dependency 사용 가능

실패 시 video model, reference와 user feedback 구현을 시작하지 않는다.

### Gate 1 — synthetic contract test

합성 dataclass/fixture로 다음을 검증한다.

- 모든 enum과 unknown value fail-closed
- immutable upstream mapper와 no-mutation
- registered binding digest와 presented receipt exact-match; 별도 재발급 receipt 거부
- completed analysis 직렬화 1회와 detector 호출 0
- caller 제공 reliability receipt exact preservation; 합성 fallback 0
- missing binding -> `dependency_unavailable`
- consent 실패 시 decoder 호출 0
- deletion/insertion -> `not_comparable`
- blocked 상태에서 candidate backfill 0
- 모든 terminal path에서 cleanup 호출
- privacy-unsafe public field 거부

이 단계는 실행 계약 확인일 뿐 실제 영상 성능 근거가 아니다.

현재 `tests/unit/test_visual_feedback_sidecar.py`와
`scripts/run_visual_feedback_contract_smoke.py`가 binding mismatch, consent 선행 차단,
QC/sync 상태, conflict, strict serialization, artifact tamper와 cleanup을 검사한다.
내부 ready는 synthetic artifact로 serializer/cardinality만 검증하며 사용자 ready
근거가 아니다.

### Gate 2 — 작은 video QC smoke

admission을 통과한 소수의 실제 정면 영상으로 decode, timestamp, pose, lip ROI,
tracker와 cleanup을 확인한다. 표본 수와 합격 기준은 실행 전 preregistration에 기록한다.

보고 항목:

- 성공/실패/재촬영 상태 분포
- decoder/tracker failure reason
- frame/time coverage
- pose·blur·occlusion slice
- latency와 peak memory
- raw media 잔존 여부와 deletion receipt

이 gate 전후 모두 full video model 학습은 금지한다.

### Gate 3 — 직접 가시 2D cue baseline

동결된 tracker와 normalizer로 cue extraction을 평가한다.

- cue repeatability와 reload parity
- reference annotation과의 agreement 및 annotation provenance
- phone/context별 observability
- camera/device/pose slice
- missing/low-confidence/OOD 비율
- 단순 baseline 대비 증분 가치

지표가 나쁘면 feature/model 규모를 키우지 않고 cue 정의, capture contract와 품질
gate를 먼저 수정한다.

### Gate 4 — calibration·reload

speaker/attempt-disjoint calibration 자료에서만 support와 visual selective policy를
결정한다.

- reference/calibration hash 동결
- artifact reload 후 cue와 status parity
- calibration metric과 coverage-risk
- measurement eligibility의 selective precision; 사용자 claim은 Gate 6 전 비활성
- worst-group와 bootstrap uncertainty
- measurement-only catalog의 cue/action schema·금지 claim 범위 freeze

공개 schema에 없는 발음 오류·action truth를 만들어내지 않는다. Gate 6의 자동
claim-policy conformance 전에는 사용자용 action claim을 비활성화하고 QC·geometry
측정만 보고한다.

기준 미달이면 `visual_feedback_ready`를 비활성화하고 `visual_uncertain`을 유지한다.

### Gate 5 — independent evaluation

동결 artifact·policy·catalog를 별도 independent cohort에서 평가한다.

- development/calibration과 speaker/attempt 교집합 0
- preregistered capture/device/context slice
- status별 precision/error/coverage; 이용 가능한 정답 label 범위만 보고
- upstream relation fidelity
- privacy/deletion audit
- latency/throughput과 failure rate

평가 결과를 본 뒤 동일 evaluation을 사용해 threshold나 cue를 재조정하면 그 결과는
더 이상 independent evaluation이 아니다.

### Gate 6 — claim-policy conformance

Gate 5에서 봉인한 결과와 production catalog를 수정 없이 결정론적으로 재생해 다음을
자동 검사한다.

- 모든 candidate의 registered binding, upstream phone/time anchor,
  observed cue와 calibration/support receipt exact reference
- 직접 보이는 lip cue allowlist와 동결 renderer key의 exact mapping
- 지원하지 않는 혀·연구개·성문·기류·의학 원인 target/claim 0
- missing, low-confidence, OOD, conflict와 unsupported slice의 candidate 0
- `visual_feedback_ready`의 candidate cardinality 1, 나머지 상태의 cardinality 0
- versioned policy/catalog digest, locked replay checksum과 violation 0 receipt

transcript, bbox 또는 keypoint는 공개 schema가 제공하는 역할로만 사용한다. 이 gate가
발음 오류·action truth를 새로 만들지는 않는다. conformance receipt가 없거나 violation이
하나라도 있으면 사용자용 visual claim을 발행하지 않으며, 수정하려면 새
protocol/catalog revision과 새 잠금 평가가 필요하다.

### Gate 이후

Gate 0~6이 통과하기 전에는 full video encoder, video-to-phone model, 3D face
reconstruction 또는 대규모 학습을 시작하지 않는다. Gate 통과도 교육 효과나
production readiness를 의미하지 않는다. 교육 효과 평가는 sidecar 구현 범위 밖의
별도 연구 계약이 필요하다.

## 14. 테스트 요구사항

### 14.1 단위 테스트 계약

- `SeungunPronunciationEvidencePort` schema/receipt validation
- concrete adapter의 detector non-invocation
- `SeungunAttemptBindingFacade`의 Mapping/`to_json_object()` 입력과 직렬화 1회
- facade 발급 binding digest 등록과 미등록 재발급 receipt 거부
- caller reliability exact preservation과 analysis snapshot mutation 격리
- upstream field exact preservation
- audio/video binding mismatch
- phone index/token/span exact match
- null/deletion/insertion span 처리
- non-monotonic video timestamp
- pose/blur/occlusion state mapping
- cue missing/low-confidence/OOD/backend-unavailable 보존
- relation 세 값과 conflict no-overwrite
- status precedence
- candidate cardinality 0..1
- unknown enum/schema fail-closed
- public supplement privacy validation

위 목록 전체가 완료됐다는 뜻은 아니다. 현재는 schema/receipt, field preservation,
binding mismatch와 미등록 재발급 차단, detector 비의존 handoff, caller reliability,
blur/sync, conflict, candidate cardinality, unknown schema/enum, path-free supplement와
cleanup failure와 audio 포함 전체 반환값 억제를 확인했다. 추가로 local admission의
consent-before-I/O·permission·symlink/FIFO/size/partial cleanup·commit 후 0400, 실제
FFmpeg/ffprobe의 strict probe·monotonic timestamp·전 구간 균등 bounded RGB decode·PCM
derivation, MediaPipe worker network 차단·model integrity failure, RGB/landmark QC와
lip/PCM sync candidate, exact bound anchor에서만 파생되는 dense phone-window
decode/tracking/runtime receipt DAG, io_uring·process escape 차단을 검사했다. 두 실제
HEVC portrait 영상에서 landmark/QC candidate도 관측했다. deletion/insertion,
pose/occlusion 성능 평가와 cancel path는 미검증이다.

### 14.2 통합 테스트 계약

- consent fail -> video processing access 0; lifecycle cleanup은 1회 시도
- dependency unavailable -> video inference 호출 0
- valid receipt -> QC -> sync -> tracking -> cue -> supplement
- sync failure -> `visual_uncertain`, candidate 없음
- supported conflict -> `visual_uncertain`, upstream 변경 없음
- no observable cue -> `no_supported_lip_cue`
- reference/calibration unavailable -> no score fabrication
- all success -> `visual_feedback_ready`와 candidate 정확히 1개
- normal/error/cancel path temporary media cleanup
- host renderer 입력에 supplement만 전달되고 LLM 호출 0

현재 첫 여덟 항목 중 synthetic adapter로 consent/dependency 차단, valid 내부 흐름,
sync uncertain, conflict, calibration tamper, ready cardinality와 cleanup을 확인했다.
attempt facade fake-port test는 Seungun 1회 호출, global QC/sync 뒤 dense selected-phone
처리 순서, 정상 cleanup 1회와 cleanup 실패 시 audio·visual 전체 억제를 검사했다.
local FFmpeg test는 H.264/HEVC allowlist, exact window PTS/RGB byte lineage와 비허용 codec
차단을 검사한다. internal CLI의 두 HEVC Main/AAC, 1080x1920, 30 fps 사용자 영상은
공식 landmark model과 CUDA Seungun에서 audio `coaching_ready`,
`audio_completed_visual_unavailable`, `cleanup_status=completed`를 반환했다. 둘 다
`video_qc_candidate_not_calibrated`였고 content sync는 각각 `not_observed`와
`candidate_unvalidated`였다. policy-valid sync가 없어 실제 phone-window 세 receipt는
`not_issued`다. P6-calibrated QC/content sync, cancel과 host renderer/LLM 통합은
확인하지 않았다.

### 14.3 평가 보고서 필수 구분

- static contract validation
- synthetic smoke
- actual small-video smoke
- calibration
- independent evaluation
- claim-policy conformance

한 단계의 성공을 다음 단계 완료로 승격하지 않는다.

## 15. 주요 위험과 대응

| 위험 | 대응 |
|---|---|
| 같은 prompt의 다른 audio/video를 잘못 결합 | immutable attempt/audio/video/timebase binding 없으면 `dependency_unavailable` |
| CTC span을 정확한 lip boundary로 오해 | span source/uncertainty 보존, deletion/insertion fail-closed |
| 입 모양이 비슷한 phone을 hard label로 확정 | cue observation과 phone diagnosis 분리, `not_comparable` 허용 |
| visual score가 audio score를 덮어씀 | read-only upstream snapshot, relation만 반환 |
| low-quality video가 정상 score처럼 처리됨 | explicit cue state와 rerecord/unsupported 분리 |
| calibration 없는 deviation 발행 | `calibrated_deviation=null`, `visual_uncertain` |
| LLM이 새 조음 사실을 생성 | sidecar에서 LLM 호출 금지, approved claim ID만 반환 |
| 얼굴/landmark의 외부 유출 | local/offline 기본, egress denied, 요청 종료 cleanup; TTL/orphan 회수는 미구현 blocker |
| 작은 smoke를 교육 효과로 과장 | 단계별 상태와 별도 교육 효과 연구 계약 유지 |

## 16. 미결정 사항

다음은 문서에서 임의로 확정하지 않는다.

- production source 저장 방식, reference 수명과 retry/idempotency
- 정면 pose·blur·occlusion·sync 지원 threshold
- 지원할 expected phone/context와 직접 가시 cue allowlist
- visual reference cohort와 rights basis
- calibration metric, selective-risk 허용값과 worst-group 기준
- approved supplemental visual action catalog 내용
- landmark retention 기간과 삭제 검증 방식
- host가 supplement를 최종 UI에 표시하는 정책

이 항목은 구현 전 각각 owner, evidence, 결정 일자와 revision을 가진 계약으로
동결해야 한다. 현재는 모두 설계 또는 미확인 상태다.

## 17. 최종 불변식

1. visual sidecar는 `seungun`에 의존하고 `seungun`은 visual에 의존하지 않는다.
2. sidecar는 already-produced immutable analysis를 소비하며 detector를 다시 돌리지
   않는다.
3. binding/reliability receipt가 없으면 상태는 `dependency_unavailable`이다.
4. upstream phone/span/score/threshold/`is_error`는 변경 불가다.
5. 직접 보이는 입술 cue만 visual evidence로 사용한다.
6. 결측·미지원·저신뢰·OOD를 0으로 채우지 않는다.
7. conflict는 보존하고 어느 evidence도 덮어쓰지 않는다.
8. output은 `VisualFeedbackSupplement`뿐이다.
9. 최종 issue와 메시지는 host/`seungun` 소유다.
10. 데이터 감사부터 independent evaluation과 claim-policy conformance까지 gate를
    통과하기 전 full video model을 시작하지 않는다.
