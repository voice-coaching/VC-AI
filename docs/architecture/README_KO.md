# seungun 입술 모양 피드백 sidecar 아키텍처

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 media/audio 경로는 smoke 검증,
  visual feedback runtime은 `not_ready`
- 공개 데이터 후보 조사: `dataset_candidates_audited`; admission·실행은 `not_started`
- 상위 시스템: [`seungun`](../../seungun/README.md)

이 디렉터리는 `seungun` 오디오 발음 분석 결과에 의존하여 사용자의 입술 모양과
움직임에서 직접 관찰 가능한 보조 근거를 만들고, 보조 피드백 후보를 반환하는
시각 sidecar만 설명한다.

이 디렉터리는 전체 음성 코칭 시스템, 오디오 진단, prompt/G2P, prosody,
S1/S2, TTS, Oracle, HTTP/session, 전체 coaching policy, 학습 효과 평가를 소유하지
않는다. 해당 책임을 시각 sidecar로 가져오거나 `seungun` 판정을 덮어쓰는 설계는
이 아키텍처에 위배된다.

## 현재 구현 경계

현재 저장소에는 다음이 구현되어 있다.

- `SeungunPronunciationEvidencePort`와 실제 frozen analysis schema를 읽기 전용으로
  검증하는 adapter
- self-digest를 가진 동일-attempt binding, consent, upstream reliability와 media
  timebase receipt
- 완료된 analysis를 concrete detector import/재실행 없이 canonical snapshot으로 만들고
  request/source record를 함께 반환하는 `SeungunAttemptBindingFacade`
- source record에 등록된 facade 발급 binding digest와 presented receipt의 exact-match
  검증; 필드가 같은 별도 재발급 receipt도 거부
- caller 제공 reliability receipt 보존과 필수 audio derivation provenance digest;
  `SameAttemptBindingReceipt v2`가 derivation receipt digest를 self-digest 대상에 직접 포함
- consent를 source I/O 전에 검사하고, no-follow regular file만 0700 workspace의
  0600으로 생성한 뒤 commit 시 0400으로 고정하며 opaque handle·삭제 receipt를 발급하는
  local admission adapter; PCM 파생물도 commit 뒤 0400
- strict MP4/(H.264|HEVC)/AAC probe, frame timestamp 검증, 전 구간 균등 RGB24 frame sample과
  16 kHz mono PCM 파생물·`AudioDerivationReceipt`를 만드는 FFmpeg/ffprobe adapter
- aggregate-only P6 evidence와 frozen-summary P8 replay를 strict digest/schema로 검사하는
  fail-closed validator 및 `p6|p8 --bundle/--bundle-fd` CLI. synthetic privacy-safe fixture
  test만 통과했으며 성공 receipt도 reliability나 사용자 visual claim을 승인하지 않음
- pinned model digest, bounded RGB 입력과 seccomp network 차단을 요구하고 최소 입술
  geometry만 반환하는 MediaPipe adapter/worker
- decoded RGB와 MediaPipe landmark에서 single/multiple-face coverage, luma/exposure bin,
  edge sharpness proxy, lip ROI와 track continuity를 계산해 lineage receipt를 발급하는
  `VideoQcCandidateAdapter`; P6 전에는 `candidate_unvalidated`/`unavailable`만 허용
- 최소 입술 aperture와 restricted PCM energy를 교차상관해 계보가 묶인
  `candidate_unvalidated` A/V anchor·receipt를 계산하는 `LipAudioAvSyncAdapter`
- 기존 전 구간 8-frame sample을 전체 영상 QC/sync에 보존하면서, 이미 binding된
  `UpstreamPhoneAnchor`의 `start_ms/end_ms`와 policy-valid measured A/V offset만으로
  선택 음소의 bounded dense RGB24 window를 생성하는 host-controlled 경로
- exact frame index/PTS/RGB digest와 FFmpeg revision을 묶는
  `PhoneWindowDecodeReceipt`, 해당 frame의 landmark lineage를 묶는
  `PhoneWindowTrackingReceipt`, QC/sync/context와 두 receipt를 묶는
  `RuntimePhoneWindowReceipt`; raw sync candidate나 caller 제공 window는 허용하지 않음
- consent/QC/precomputed landmark/timestamp sync용 deterministic 내부 adapter
- synthetic-only calibration artifact와 visual claim catalog의 strict JSON loader
- 상태 우선순위와 cleanup을 적용하는 `VisualFeedbackCoordinator`
- Seungun 1회와 attempt lifecycle을 소유하고 cleanup 실패 시 audio·visual 반환값을
  모두 억제해 `failed_closed`로 닫는 host `VideoAttemptFacade`
- path·raw frame·landmark 배열을 내보내지 않는 `VisualFeedbackSupplement` strict
  JSON 직렬화
- [`run_visual_feedback_contract_smoke.py`](../../scripts/run_visual_feedback_contract_smoke.py)와
  synthetic contract unit test

위 media adapter는 로컬 FFmpeg/ffprobe를 H.264와 HEVC 합성 비개인 MP4에 실행하는
단위 테스트를 통과했다. 공식 MediaPipe 공개 test portrait(`portrait.jpg`, upstream
SHA-256 `a6f11e...`)를 로컬 static H.264/AAC MP4로 변환한 실행에서는 실제 얼굴
landmark, video-QC candidate와 content A/V-sync candidate 경로를 관측했다. 추가로
HEVC Main/AAC, 1080x1920 portrait, 30 fps인 사용자 영상 두 건을 internal CLI로 실행해
container admission·전 구간 decode·MediaPipe/QC 후보·Seungun audio
`coaching_ready`·cleanup 완료까지 확인했다. 이는 두 파일의 infrastructure smoke이며
calibrated tracker/QC 성능이나 사용자 visual claim 검증은 아니다.
FFmpeg/ffprobe, MediaPipe와 Seungun worker에는 pre-exec seccomp/resource-limit launcher가
연결됐다. launcher는 io_uring, 일반 process clone/fork와 session/process-group escape를
차단하고 `CLONE_THREAD`만 허용한다. CUDA 경로는 AF_UNIX socketpair와 연결되지 않은
`SOCK_SEQPACKET` listener만 예외로 두되 `connect=ENOENT`, accept와 message I/O 차단을
강제한다. worker는 `PR_SET_PDEATHSIG`와 설정 전후 expected-parent 검증으로 launcher
수명에 결합되며, 해당 prctl 해제와 cross-process signal/pidfd/ptrace/process_vm escape는
차단한다. self/native-thread signal은 유지한다. FFmpeg는 nonblocking pipe와 오류 시
unconditional process-group kill을 쓴다. 이는 전체 sandbox 완료를 뜻하지 않는다.
runtime visual detection v3는 sample/probe/binding/model/landmark에 raw QC/sync
candidate v2, calibrated decision v2와 policy-validated offset v2 receipt를 함께 묶고
mismatch를 선행 차단한다. 현재 hash-pinned
`final_pronunciation_detector_v1`과 one-shot locked result의 로컬 digest가 receipt와
일치하므로, local composition은 이 고정 revision을 시각 sidecar의 upstream anchor로
사용할 수 있다는 `supported` receipt를 발급한다. 이 상태는 schema·artifact 무결성과
현재 구현 경로의 사용 가능성만 뜻하며 detector score를 correctness confidence로
보정하거나 개별 판정의 정확성을 보증하지 않는다. AI Hub 68 P6 geometry/P6B artifact는
실제 실행됐지만, TS174/TL174 actual-media decision-policy evidence와 P8
claim-conformance receipt는 여전히 없다.
TTL, orphan scavenger와 persistent cleanup ledger도 아직 구현되지 않았다.

따라서 두 실제 사용자 영상에서 audio coaching을 받은 사실은 확인됐지만, visual
보조 피드백까지 받은 runtime 완료를 뜻하지 않는다.
internal CLI의 권장 입력은 사전에 read-only로 연 `fd >= 3`을 넘기는 `--video-fd`로,
민감한 participant path를 process argv에 넣지 않는다. `--video`는 내부 편의 기능이며
경로가 argv와 shell history에 남을 수 있다. 합성 H.264/AAC smoke에 더해 위 두
HEVC/AAC portrait 사용자 영상은 `--video-fd`, 공식 pinned landmark model과 CUDA
Seungun으로 전체 실행돼 각각 audio `coaching_ready`, attempt
`audio_completed_visual_unavailable`, `cleanup_status=completed`를 반환했다. 첫 영상은
`content_av_sync_not_observed`, 둘째 영상은
`content_av_sync_candidate_not_calibrated`였고, 둘 다
`video_qc_candidate_not_calibrated`, `p6_user_runtime_calibration_not_available`,
`p8_user_runtime_claim_catalog_not_available`로
`visual_stage_status=blocked_reliability`에 fail-closed했다. policy-valid measured A/V
offset이 없었으므로 dense selected-phone
decode는 시작되지 않았으며 실제 `PhoneWindowDecodeReceipt`,
`PhoneWindowTrackingReceipt`, `RuntimePhoneWindowReceipt`는 모두 `not_issued`다.
camera/upload API/UI, TS174/TL174 actual-media QC/sync decision calibration, P8 locked
evaluation과 claim-policy conformance는 `not_started`/`not_ready`다. AI Hub 68에서는
actor/attempt-disjoint 24-point geometry와 고정 `어`/`ㅓ` reference, production tracker
85/85 parity, visible-only calibration/catalog/policy가 통과해 aggregate 0400 v2 artifact가
생성됐다. 그러나 실제 runtime promotion identity/receipt는 위 decision-policy evidence가
없어 발급되지 않았다. P8 read-zero seal은 실제 locked source 접근 전 계약이며 아직 실제
seal/evaluation 기록으로 발급되지 않았다.
content A/V sync candidate extractor는 합성 신호 unit test를 통과했고 두 실제 영상에서
각각 `not_observed`와 `candidate_unvalidated` 경로를 관측했지만, population-level P6
calibration으로 검증되지 않아 reliability로 승격하지 않는다.
AI Hub 68 admission/calibration은 완료됐고, AI Hub 538의 필요한 두 실행은 시작하지 않았다.
포함된 calibration/catalog artifact는 digest-pinned immutable fixture이지만 scope는
`synthetic_contract_only`이며
`ExecutionScope.USER_RUNTIME`에서는 `visual_feedback_ready`가 되지 않고
`visual_uncertain`으로 닫힌다.

## 내부 실행

runtime은 model을 암묵적으로 내려받지 않는다. 저장소 root에서 공식 pin을 Git 제외
경로에 한 번 설치한다.

```bash
mkdir -p models/mediapipe
.venv/bin/python scripts/fetch_mediapipe_face_landmarker.py \
  --output "$(pwd)/models/mediapipe/face_landmarker.task"
```

participant 영상 경로를 argv나 shell history에 직접 쓰지 않으려면 prompt 입력으로 받은
경로를 read-only descriptor로 연 뒤 실행한다.

```bash
read -r -p "Video path: " VOICE_COACH_VIDEO_PATH
exec 3<"$VOICE_COACH_VIDEO_PATH"
unset VOICE_COACH_VIDEO_PATH
.venv/bin/python scripts/run_pronunciation_video_attempt.py \
  --video-fd 3 \
  --prompt-id R00-01 \
  --consent-local-processing \
  --landmark-model "$(pwd)/models/mediapipe/face_landmarker.task" \
  --device cuda
exec 3<&-
```

exit `0`이어도 현재 허용된 완료 상태는 Seungun audio 결과와
`audio_completed_visual_unavailable`이다. Seungun upstream anchor는 현재 고정 revision에
한해 supported지만, 사용자 runtime P6/P8 gate 전에는 visual claim이 반환되지 않는 것이
정상이다. 입력은 allowlist의 H.264 또는 HEVC video 한 개와 AAC audio 한 개를 가진
MP4여야 하며 현재 local composition의 최대 width/height는 각각 1920이다. codec-neutral
capture contract ID는 `local_allowlisted_video_aac_mp4_v2`다.

## 읽는 순서

1. [`architecture.md`](architecture.md) — 범위, 의존성 방향, 불변식
2. [`component.md`](component.md) — domain/service/port/adapter 책임
3. [`flow.md`](flow.md) — 시각 분석과 보조 피드백 흐름
4. [`state.md`](state.md) — 시각 sidecar 전용 상태와 전이
5. [`directory.md`](directory.md) — 현재 존재하는 경로와 향후 배치 규칙
6. [`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md) — P6·P8 공개 데이터 후보, 역할과 blocker
7. [`TRACK_E_CONTRACT_KO.md`](TRACK_E_CONTRACT_KO.md) — VE0~VE6 순차 검증 계약과 현재 ledger
8. [`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md) — 상세 설계·검증 gate
9. [`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md) — 기존 경로명을 유지한 `seungun` 연동 계약

나머지 문서는 G04, Track E, 표현 모델과 시각 sidecar 사이의 소유권 경계를
설명하는 호환성 문서다. 범위 수정 전 내용은
[`docs/history/legacy_voice_coach_architecture_2026-08-26`](../history/legacy_voice_coach_architecture_2026-08-26/README_KO.md)에
`historical_snapshot`으로 보존되어 있으며 현재 정본이 아니다.

## E00/E02 source scope 경계

`configs/experiments/e00_closed_loop_demo_v1.yaml`,
`configs/experiments/e02_confidence_guard_v1.yaml`과 대응 evaluator는 source pin마다
의미 scope를 강제한다. 오디오 정본은 `seungun_audio_canonical`, 데이터 반출 경계는
`data_privacy_boundary`, API는 `api_contract`, E00 평가 문서는
`evaluation_boundary`이며, 이 디렉터리 문서는 오직 `visual_sidecar_only`다.
validator는 path·SHA-256뿐 아니라 path에 지정된 exact scope도 검사하므로 visual
문서를 오디오 정본으로 바꿔 적는 것을 거부한다.

이 분리는 과거 source/hash의 소유권 오류를 고친 것이지 E00/E02 실제 실행이나 visual
준비도를 승격한 것이 아니다. source-pin 검증 성공은 static contract 일관성만 뜻하며,
P6 calibration·P8 locked evaluation·claim-policy conformance가 없으면 visual claim은
계속 fail-closed한다.
