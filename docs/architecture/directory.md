# 입술 모양 피드백 Sidecar 디렉터리

- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 runtime은 `not_ready`
- 공개 데이터 후보 조사: `dataset_candidates_audited`; admission·실행은 `not_started`

이 문서는 현재 존재하는 관련 경로만 “현재 구조”로 기록한다. 아래 내부 adapter와
artifact는 production video backend 또는 실제 데이터로 검증된 calibration을 뜻하지 않는다.

## 1. 현재 존재하는 경로

| Path | Type | 현재 책임 | visual sidecar와의 관계 |
|---|---|---|---|
| `docs/architecture/architecture.md` | file | sidecar 범위, 권한과 의존성 방향 | 정본 개요 |
| `docs/architecture/component.md` | file | target domain/service/port/adapter 책임 | 설계 문서이며 구현 파일 목록이 아님 |
| `docs/architecture/flow.md` | file | visual use case와 실패 흐름 | 설계 문서 |
| `docs/architecture/state.md` | file | visual enum과 상태 전이 | 정본 상태 계약 |
| `docs/architecture/VISUAL_DATASET_EVIDENCE_LEDGER_KO.md` | file | P6·P8 공개 데이터 후보, 역할, 권리·schema blocker와 실행 계약 | metadata audit만 완료; 원본 접근·artifact 생성 아님 |
| `docs/architecture/TRACK_E_CONTRACT_KO.md` | file | VE0~VE6 순차 gate와 현재 준비도 | 공개 후보 조사와 gate 통과를 분리 |
| `docs/architecture/SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md` | file | 상세 입술 모양 sidecar 설계와 구현/미구현 경계 | `partial_implementation` 정본 |
| `src/voice_coach/visual_feedback/models/` | package | enum, request, binding/consent/timebase receipt, evidence, calibration/catalog, strict supplement schema | pure domain; 구현됨 |
| `src/voice_coach/visual_feedback/ports/` | package | Seungun evidence, consent, restricted video, QC, tracking, sync, calibration/catalog Protocol | dependency inversion 계약; 구현됨 |
| `src/voice_coach/visual_feedback/adapters/seungun.py` | file | 실제 frozen analysis schema와 동일-attempt receipt를 읽기 전용 검증 | in-memory host source만 구현; detector 호출 안 함 |
| `src/voice_coach/visual_feedback/adapters/video.py` | file | precomputed record의 consent/QC/lip aggregate/timestamp sync/삭제 | synthetic 내부 adapter; 실영상 decode/model 아님 |
| `src/voice_coach/visual_feedback/adapters/artifacts.py` | file | strict in-memory/JSON calibration·claim catalog loader와 digest pin | 구현됨 |
| `src/voice_coach/visual_feedback/services/handoff.py` | file | detector 비의존 completed-analysis snapshot, registered binding digest, atomic request/source handoff | 구현되어 local media composition에 연결; persistent source는 아님 |
| `src/voice_coach/visual_feedback/services/coordinator.py` | file | 상태 우선순위, exact anchor join, catalog 선택, fail-closed cleanup | 구현됨 |
| `src/voice_coach/visual_feedback/testing.py` | file | 단일-use synthetic attempt composition | 내부 contract test 전용 |
| `src/voice_coach/pronunciation_attempts/models.py` | file | consent/admission policy, opaque video/PCM, strict probe/frame와 audio derivation receipt | path-free attempt media 계약; 구현됨 |
| `src/voice_coach/pronunciation_attempts/ports.py` | file | restricted media store와 A/V container Protocol | attempt-level dependency inversion; 구현됨 |
| `src/voice_coach/pronunciation_attempts/adapters/restricted_local_media.py` | file | consent-before-I/O, no-follow local file admission, 0700 workspace, 0600 생성·commit 후 0400 container/PCM, derived audio lifecycle와 삭제 receipt | 구현됨; 제품 upload/camera intake는 아님 |
| `src/voice_coach/pronunciation_attempts/adapters/ffmpeg_av_container.py` | file | strict MP4/H.264/AAC probe, monotonic frame timestamp, 전 구간 균등 RGB24 frame sample, 16 kHz mono PCM derivation | 구현됨; 합성 비개인 MP4로 local FFmpeg 실행 검증 |
| `src/voice_coach/pronunciation_attempts/adapters/network_isolated_exec.py` | file | no-new-privs/resource limit, PDEATHSIG parent binding, io_uring·cross-process signal/pidfd/ptrace/process_vm·process/session/group escape 차단, thread-only clone, 제한 CUDA AF_UNIX 정책 | FFmpeg/ffprobe·MediaPipe·Seungun worker에 연결; 전체 sandbox나 NPROC/FSIZE limit은 아님 |
| `src/voice_coach/pronunciation_attempts/adapters/bounded_isolated_process.py` | file | shell 없는 bounded stdin/stdout, timeout·출력·주소공간 제한과 process-group 종료 | Seungun worker 실행 경계; 구현됨 |
| `src/voice_coach/pronunciation_attempts/adapters/seungun_runtime_worker.py` | file | seccomp 확인 뒤 frozen Seungun detector를 import·1회 실행하고 strict JSON 반환 | 구현됨; 합성 H.264/AAC CUDA CLI smoke 통과 |
| `src/voice_coach/pronunciation_attempts/adapters/mediapipe_lip_landmarks.py` | file | pinned model digest와 bounded RGB sample을 worker에 전달하고 얼굴 수·최소 lip geometry만 parse | 공식 공개 test portrait에서 얼굴 landmark candidate 관측; 실제 사용자 영상은 미검증 |
| `src/voice_coach/pronunciation_attempts/adapters/mediapipe_landmark_worker.py` | file | seccomp network 차단 뒤 MediaPipe Face Landmarker 실행, 최소 geometry만 반환 | worker/security 경계 구현; 실제 사용자 영상 검증 아님 |
| `src/voice_coach/pronunciation_attempts/adapters/video_qc.py` | file | decoded RGB+landmark의 single/multiple-face coverage, luma/exposure, edge proxy, lip ROI, continuity와 lineage receipt | `candidate_unvalidated`/`unavailable`; P6 pass/reliability 아님 |
| `src/voice_coach/pronunciation_attempts/adapters/lip_audio_av_sync.py` | file | lip aperture와 restricted PCM energy에서 offset·anchor candidate 및 lineage receipt 계산 | `code_present/unvalidated`; P6 전 reliability 승격 금지 |
| `src/voice_coach/pronunciation_attempts/services/video_attempt_facade.py` | file | consent → admission → probe/audio → Seungun 1회 → optional visual → cleanup 순서; cleanup 실패 시 모든 결과 억제 | host-owned orchestration 계약·unit test; 실제 사용자 검증 전 |
| `src/voice_coach/pronunciation_attempts/adapters/runtime.py` | file | explicit consent, restricted PCM→격리 Seungun, handoff와 QC/sync receipt를 묶는 runtime visual detection v2 | exact lineage·mismatch unit test; P6/P8 reliability는 fail-closed |
| `src/voice_coach/pronunciation_attempts/composition.py` | file | local concrete adapter를 `VideoAttemptFacade`로 조립하고 visual reliability/publication을 잠금 | 합성 CLI E2E에 사용; 실제 사용자 영상 성공 실행은 미검증 |
| `src/voice_coach/visual_feedback/gate_evidence.py` | file | AI Hub 68 aggregate P6 bundle과 AI Hub 538 frozen P8 replay의 schema/digest/invariant 검증 | receipt는 항상 non-promotional; dataset·rights·seal을 생성하거나 인증하지 않음 |
| `scripts/fetch_mediapipe_face_landmarker.py` | file | 공식 versioned Face Landmarker model을 bounded download하고 SHA-256 pin 검증 뒤 설치 | runtime implicit download 없음; output은 `/models/` 등 Git 제외 경로 사용 |
| `scripts/run_pronunciation_video_attempt.py` | file | 권장 `--video-fd` read-only fd와 내부 편의 `--video`, prompt, explicit local consent, optional landmark model을 받는 내부 CLI | 공개 test portrait static H.264/AAC+공식 model+CUDA에서 QC·sync candidate 관측, audio 완료·visual unavailable·cleanup 완료; `--video` path는 argv/history 노출 가능 |
| `scripts/validate_visual_gate_evidence.py` | file | `p6`/`p8`의 `--bundle`/`--bundle-fd` privacy-safe JSON 검증 및 schema 출력 | locator-sensitive 입력에는 read-only `--bundle-fd`; 실패 시 receipt 없이 exit 2 |
| `configs/visual_feedback/internal_visual_calibration_v1.json` | file | aperture rule과 support 범위 | `synthetic_contract_only`; 사용자용 아님 |
| `configs/visual_feedback/internal_visual_claim_catalog_v1.json` | file | allowlisted action/claim ID | `synthetic_contract_only`; 사용자용 아님 |
| `scripts/run_visual_feedback_contract_smoke.py` | file | 6개 deterministic 상태/cleanup/serialization scenario 실행 | 내부 smoke entrypoint |
| `tests/unit/test_visual_feedback_sidecar.py` | file | binding, consent, adapter, calibration, state, serialization, cleanup test | synthetic unit/contract test |
| `tests/unit/test_restricted_local_media.py` | file | consent 선행, mode, symlink/FIFO/size, partial artifact cleanup | local 제한 저장 단위 테스트 |
| `tests/unit/test_ffmpeg_av_container.py` | file | 실제 local ffprobe/ffmpeg의 strict probe, 전 구간 균등 RGB decode, PCM derivation과 fail-closed | 합성 비개인 media infrastructure 테스트 |
| `tests/unit/test_network_isolated_exec.py` | file | IP/Unix socket, io_uring, process clone/thread clone, timeout/output 경계 | seccomp escape·bounded worker 단위 테스트 |
| `tests/unit/test_mediapipe_lip_landmarks.py` | file | seccomp socket 차단, model digest와 symlink fail-closed | successful landmark inference test는 없음 |
| `tests/unit/test_video_qc.py` | file | deterministic QC candidate, no-face unavailable, lineage/resource/tamper failure | 합성 frame/landmark unit test; P6 calibration 아님 |
| `tests/unit/test_lip_audio_av_sync.py` | file | lip aperture/PCM energy candidate, receipt·lineage와 unavailable 상태 | 합성 신호 unit test; P6 calibration 아님 |
| `tests/unit/test_video_attempt_facade.py` | file | 순서, Seungun 1회, optional visual, cleanup 완료와 cleanup 실패 시 audio 포함 전체 억제 | fake port orchestration test; 실제 사용자 E2E 아님 |
| `tests/unit/test_pronunciation_attempt_runtime_adapters.py` | file | consent/prompt/Seungun temp WAV/handoff media lineage와 P6/P8 차단 | concrete bridge unit test; CLI/composition 완료 근거 아님 |
| `tests/unit/test_visual_gate_evidence.py` | file | P6 split/label/reload와 P8 lineage/scenario/catalog/forbidden claim, CLI fail-closed 검증 | synthetic privacy-safe fixture만 사용; P6/P8 실실행 아님 |
| `seungun/src/korean_phone_ctc/frozen_detector.py` | file | 현재 `seungun` phone-level audio 분석 계약 | sidecar가 import·재실행하지 않는 상위 권한 구현 |
| `seungun/docs/CURRENT_PIPELINE_KO.md.md` | file | `seungun` audio pipeline 책임 문서 | audio 권한의 상위 문서 |
| `seungun/docs/PIPELINE_RELIABILITY_IMPROVEMENT_PROPOSAL_KO.md` | file | audio reliability 개선 제안 | visual이 대신 구현하거나 완료로 해석하지 않음 |

현재 workspace에는 visual 계약과 내부 smoke 외에도 local 제한 저장, 실제 FFmpeg 기반
probe/decode/audio derivation, MediaPipe worker/adapter와 lip/PCM content-sync candidate
extractor 코드가 있다. 공식 공개 test portrait를 static H.264/AAC로 변환한 CLI에서
Face Landmarker의 얼굴 landmark와 QC·sync candidate 경로, CUDA Seungun audio 완료,
visual unavailable 및 cleanup 완료를 관측했다. 실제 사용자 영상 QC, P6로 보정된 A/V
sync reliability와 적격 실제 데이터 reference/calibration threshold는 없다. 실제 사용자
영상 E2E와 camera/upload API/UI도 없다.
AI Hub/Hugging Face 후보 조사는 문서화됐지만 원본 dataset access/admission, P6/P8 실행과
production artifact는 없다. 내부 smoke 또는 합성 media 검증의
`visual_feedback_ready`를 사용자 runtime 준비 상태로 해석하지 않는다.

FFmpeg/ffprobe·MediaPipe·Seungun worker용 OS-level network/resource launcher가 구현됐다.
io_uring과 일반 process/session/group escape를 차단하고 CUDA AF_UNIX 예외도 connect·accept·
message I/O가 불가능하도록 제한한다. FFmpeg pipe는 nonblocking이며 실패 시 process
group을 무조건 종료한다. PDEATHSIG expected-parent binding과 cross-process
signal/pidfd/ptrace/process_vm 차단도 적용하되 self/native-thread signal은 유지한다.
decoded sample·probe·binding·model·landmark·QC/sync receipt를
묶는 detection v2와 mismatch 선행 차단은 unit test됐다. 고정 Seungun v1을 sidecar
anchor로 쓰는 제한된 upstream `supported` receipt는 local composition에 연결됐다.
사용자 runtime P6/P8 visual reliability, TTL, orphan scavenger, persistent cleanup ledger와
`RLIMIT_NPROC`/`RLIMIT_FSIZE`는 아직 없다.
이 항목은 제품 composition과 함께 해결해야 할 production blocker다.

## 2. 현재 배치와 향후 확장 규칙

하나의 visual-feedback 기능 경계 안에서 다음 책임을 분리한다.

- `domain`: visual state와 immutable evidence/supplement model
- `services`: `SeungunAttemptBindingFacade`, `VisualFeedbackCoordinator`, timeline join,
  eligibility와 supplement composition
- `ports`: `SeungunPronunciationEvidencePort`와 video/reference/ledger interface
- `adapters`: seungun DTO mapping, internal precomputed adapter, attempt-level local
  storage/FFmpeg/MediaPipe/Seungun worker/content-sync candidate와 향후 reference adapter
- `entrypoint`: synthetic smoke와 합성 container E2E를 통과한 internal CLI가 있으며
  participant-sensitive path에는 pre-opened read-only `--video-fd`를 권장한다. `--video`는
  argv/shell-history 노출 caveat가 있는 편의 기능이다. 실제 사용자·제품 composition
  검증은 준비 중; 향후 제품 entrypoint는 인자 파싱,
  dependency 조립과 결과 직렬화만 담당

실제 backend를 추가할 때도 domain/port 계약을 유지하고, internal adapter를
production adapter로 이름만 바꾸거나 범위를 승격하지 않는다.

## 3. 배치 불변식

- `seungun` 아래 기존 audio detector 파일을 visual 책임으로 이동하거나 수정하지 않는다.
- `seungun`은 visual package를 import하지 않는다.
- `src/voice_coach/visual_feedback`은 `korean_phone_ctc.frozen_detector`를 import·재실행하지
  않는다. host attempt의 concrete detector import는 격리된 Seungun worker에만 있다.
- visual domain/service에서 filesystem, environment, subprocess, HTTP, GPU 또는 SDK를
  직접 호출하지 않는다.
- Oracle, S1/S2, TTS, G04, Track E와 전체 audio coaching 파일을
  `docs/architecture`의 현재 visual 구조로 다시 추가하지 않는다.

## 4. 경로 갱신 조건

다음 변경이 발생하면 이 문서를 함께 갱신한다.

- visual feature package 또는 entrypoint가 실제로 추가됨
- port/adapter 경계가 이동하거나 공개 계약이 변경됨
- seungun upstream adapter가 지원하는 schema/revision이 변경됨
- visual model/reference/threshold artifact의 실제 저장 위치가 확정됨
- 외부 SDK, model runtime 또는 restricted storage adapter의 계약·검증 상태가 변경됨

존재하지 않는 파일을 구현 완료 경로처럼 기록하거나, 계획 경로를 현재 구조 표에
추가하지 않는다.
