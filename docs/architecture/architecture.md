# 입술 모양 피드백 Sidecar 아키텍처

- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 runtime은 `not_ready`
- 상위 시스템: [`seungun`](../../seungun/)
- 상세 설계: [`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)
- 데이터 후보: [`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)

## 1. 목적과 권한

이 디렉터리는 `seungun`의 음성 발음 분석 결과에 입술 모양 관측을 덧붙이는
선택적 sidecar만 설명한다. sidecar는 정면 영상에서 직접 관측 가능한 입술
closure, aperture, rounding, spreading과 움직임 timing을 분석하고, 근거가 충분한
경우 검증·허용된 입술 행동 한 가지를 **보조 피드백**으로 반환한다.

`seungun`은 다음 항목의 유일한 권한자다.

- 프롬프트, expected phone과 음소 위치
- 오디오 QC, 정렬, 발음 오류와 운율 분석
- `error_score`, operating threshold와 `is_error`
- 전체 coaching policy, 최종 사용자 표현과 재시도 정책

visual sidecar는 이 값을 생성·재계산·변경하지 않는다. 일반 visual unavailable/failure는
이미 만들어진 `seungun` audio 결과를 유지한다. 단, admitted workspace cleanup이
실패하면 host facade는 privacy fail-closed 규칙에 따라 audio를 포함한 outward 결과를
모두 억제하며, 이는 audio analysis 자체를 수정하거나 재실행한다는 뜻이 아니다.

## 2. 논리적 의존성

```text
상위 composition root
  |
  +-> seungun audio pipeline ---------------------> authoritative audio result
  |
  +-> lip-shape feedback sidecar
        |
        +-> SeungunPronunciationEvidencePort
        |     ^
        |     |
        |   FrozenSeungunPronunciationEvidenceAdapter
        |     |
        |     +-> host가 주입한 immutable PronunciationAnalysis source
        |
        +-> video intake/QC -> lip tracking -> cue analysis
        +-> visual eligibility policy -> VisualFeedbackSupplement
```

의존성 규칙은 다음과 같다.

1. visual application/domain은 `SeungunPronunciationEvidencePort`에 의존한다.
2. concrete adapter만 frozen detector의 공개 schema를 알고, 이미 생성된 analysis를
   읽는다. detector class를 import하거나 재실행하지 않는다.
3. adapter는 upstream DTO를 visual 내부 DTO로 복사·검증하며 의미를 바꾸지 않는다.
4. `seungun`은 visual package를 import하지 않는다.
5. composition root가 먼저 `seungun` 결과를 얻고, 필요한 경우에만 sidecar를
   호출한다. 이 순서로 순환 의존성을 막는다.

## 3. 입력 계약

sidecar는 raw audio를 진단 입력으로 받지 않는다. 최소 입력은 다음과 같다.

- 검증된 `seungun` analysis schema와 revision
- prompt/attempt/media binding
- expected phone index와 upstream이 확정한 target/location
- audio clock 또는 검증된 phone time span
- upstream reliability와 지원 범위 receipt
- opaque video handle과 facial-video 처리 동의 receipt

schema, prompt/media binding, 시간축 또는 upstream reliability가 없거나 서로
일치하지 않으면 visual 결과는 `dependency_unavailable`이다. 이 상태는 visual
출력만 차단하며 `seungun` audio 진단을 실패로 바꾸지 않는다.

receipt, 검증 adapter와 detector 비의존 `SeungunAttemptBindingFacade`는 구현됐다.
facade는 완료된 analysis의 `Mapping` 또는 `to_json_object()`를 canonical snapshot으로
만들고 request/source record를 `SeungunVisualHandoff`로 함께 반환한다. source record에
등록된 facade 발급 binding digest와 presented receipt가 exact match해야 하므로 별도
재발급 receipt는 거부된다. caller 제공 reliability receipt는 합성하지 않는다.
`SameAttemptBindingReceipt v2`는 `audio_derivation_receipt_sha256`를 필수 필드와
self-digest 입력으로 직접 묶고 source record와의 exact match도 검사한다.
`pronunciation_attempts`의 제한 저장·FFmpeg audio derivation과 이를 handoff 입력으로
검사하는 bridge 계약은 구현됐다. internal CLI는 권장 pre-opened read-only `--video-fd`로
합성 H.264/AAC MP4, 공식 MediaPipe model과 CUDA Seungun을 실행해
`audio_completed_visual_unavailable`, `cleanup_status=completed`까지 반환했다.
다만 persistent source 재로드와 실제 사용자 영상 성공 근거는 아직 없으므로 이를 사용자
attempt 또는 visual feedback 준비 완료로 해석하지 않는다.

## 4. visual sidecar 책임

sidecar가 소유하는 처리 단계는 다음뿐이다.

1. 영상 처리 동의·권리와 제한 저장소 admission 검사
2. video decode, 얼굴 수, 정면 pose, 입술 ROI, blur·가림·조명 QC
3. 입술 landmark tracking과 정면 입력용 정규화
4. upstream audio clock/phone span에 대한 A/V sync와 timeline join
5. 입술 cue 추출, visual reference 조회와 visual calibration 검사
6. visual evidence와 upstream target의 관계 판정
7. allowlist visual advice catalog를 이용한 보조 피드백 구성
8. 제한 media 삭제와 privacy-safe audit receipt 생성

현재 `pronunciation_attempts`에는 1의 local consent preflight·제한 저장·삭제 adapter,
2의 strict FFmpeg/ffprobe container probe·전 구간 균등 RGB24 frame sample·16 kHz mono PCM
파생 adapter, 3의 pinned MediaPipe face-landmarker worker와 최소 lip geometry adapter가
구현됐다. decoded RGB와 landmark에서 얼굴 coverage, luma/exposure, edge sharpness
proxy, lip ROI와 track continuity를 계산하는 `VideoQcCandidateAdapter`도 runtime visual
detection v2에 연결됐다. 단 P6 전에는 candidate/unavailable 측정과 lineage만 제공한다.

`visual_feedback`에는 2~4의 사전 계산 관측값 기반 QC/tracker/sync adapter와 5~8의
계약·synthetic 경로가 있다. `pronunciation_attempts`의 `LipAudioAvSyncAdapter`는 입술
aperture와 PCM energy로 계보가 묶인 미보정 anchor candidate·receipt를 실제 계산한다.
그러나 실제 사용자 영상의 calibrated QC pass, pose·가림 판정, head-pose normalization,
content-sync 성능과 P6/P8 artifact 및 사용자용 claim은 아직 검증되지 않았다. 따라서
candidate extractor의 존재를 reliability, 사용자 runtime 또는 VE2 이후 gate 완료로
승격하지 않는다.

혀, 성문, 성대, 기류와 같은 내부 조음 원인은 정면 RGB 영상으로 확정하지 않는다.
head pose와 턱 landmark는 QC·정규화 보조값일 뿐 별도 발음 진단 또는 사용자 행동
근거로 승격하지 않는다.

## 5. 출력 계약

출력은 `VisualFeedbackSupplement`이며 상태값은
[`state.md`](state.md)의 `VisualFeedbackStatus`를 따른다.

`visual_feedback_ready`일 때도 출력은 다음으로 제한된다.

- upstream analysis와 target을 가리키는 불변 reference
- 관측된 입술 cue와 품질·reference·calibration receipt
- `supports_upstream` 관계
- 동결된 visual action/claim candidate ID 한 개
- 필요한 경우 privacy가 적용된 overlay reference

출력에는 raw frame, face/lip crop, landmark array, embedding, local path, participant
identifier, audio 원본 또는 내부 detector state를 포함하지 않는다.

## 6. 외부 연동과 adapter 경계

filesystem, video decoder, landmark SDK, model runtime, reference storage, consent
ledger와 audit storage는 모두 port/adapter 뒤에 둔다. adapter는 backend ID,
revision, config/checksum, license, timeout, retry 가능 여부와 실패 상태를 typed
receipt로 반환한다.

얼굴·입술 영상 및 파생 생체정보의 remote egress는 기본 금지다. 명시적인 동의,
계약과 egress policy가 모두 허용하지 않으면 local/offline adapter만 사용한다.
현재 FFmpeg/ffprobe, MediaPipe와 Seungun worker는 pre-exec
seccomp·resource-limit launcher를 사용한다. launcher는 io_uring, fork/vfork, 일반 clone,
clone3, session/group escape를 차단하고 runtime thread용 `CLONE_THREAD`만 허용한다.
CUDA는 AF_UNIX socketpair와 연결되지 않은 `SOCK_SEQPACKET` listener만 허용하며
connect는 ENOENT, accept/message I/O는 차단된다. FFmpeg는 nonblocking pipe를 drain하고
모든 예외에서 process group을 kill한다. `PR_SET_PDEATHSIG`와 설정 전후 expected-parent
검증은 worker를 launcher 수명에 결합하고, 해당 prctl 해제와 cross-process
signal/pidfd/ptrace/process_vm escape를 차단하되 self/native-thread signal은 유지한다.
CLI는 participant path가 argv에 나타나지 않도록 사전에 read-only로 연 `fd >= 3`을 받는
`--video-fd`를 권장한다. `--video`는 argv/shell-history 노출 위험이 있는 내부 편의
기능이다. 합성 공식 model+CUDA `--video-fd` CLI 실행은 확인됐지만, 전체 sandbox는 아니며
TTL·orphan scavenger·persistent cleanup ledger는 없으므로 강제 종료 뒤 잔존 workspace
회수나 전체 제품 security 검증으로 확대하지 않는다. `RLIMIT_NPROC`와 `RLIMIT_FSIZE`도
아직 적용되지 않았고 실제 사용자 영상은 검증되지 않았다.

## 7. 범위 밖 책임

| 범위 밖 항목 | 소유자 또는 처리 원칙 |
|---|---|
| Oracle/HTTP/session/API | `seungun` 또는 상위 서비스; visual sidecar는 endpoint를 정의하지 않음 |
| S1/S2, G04, Track E와 전체 음성 학습·평가 | `seungun` 및 별도 연구 계약 |
| TTS·기준 음성 생성 | 별도 audio/reference 경로 |
| audio reliability와 전체 오류 선택 | `seungun` |
| 최종 coaching 문장과 전역 단일 행동 선택 | `seungun` presentation/policy |
| 교육 효과와 사용자 연구 | 별도 연구이며 sidecar 구현 완료 조건이 아님 |

## 8. 아키텍처 결정

| Date | Decision | Reason |
|---|---|---|
| 2026-08-26 | `docs/architecture`의 범위를 입술 모양 visual sidecar로 한정 | 전체 음성 코칭 아키텍처와 보조 시각 분석의 책임을 분리하기 위함 |
| 2026-08-26 | visual은 `SeungunPronunciationEvidencePort`를 통해서만 upstream을 사용 | concrete detector 결합을 adapter 한 곳으로 제한하고 testability를 유지하기 위함 |
| 2026-08-26 | visual 출력은 supplemental이며 audio 결과를 변경하지 않음 | 미검증 영상 branch가 authoritative audio 판단을 덮어쓰지 않게 하기 위함 |
| 2026-08-26 | visual 구현 전 상태를 `design_only/not_implemented`로 유지 | 문서 계약을 실제 runtime capability로 오인하지 않게 하기 위함 |
| 2026-08-27 | port/domain/coordinator/strict serialization과 synthetic 내부 adapter를 부분 구현 | 동일-attempt·privacy·상태 우선순위를 실행 가능한 계약 test로 고정하기 위함 |
| 2026-08-27 | synthetic calibration/catalog는 사용자 runtime에서 ready를 발행하지 않음 | 내부 fixture 통과를 실영상 보정 또는 제품 준비도로 오인하지 않게 하기 위함 |
| 2026-08-27 | P6에 AI Hub 68, P8 자동평가에 AI Hub 538을 공개 metadata 기준 후보로 배정 | 데이터 후보 조사와 admission·calibration·잠금 평가를 분리하기 위함 |
| 2026-08-28 | detector 비의존 handoff와 registered binding digest exact-match 구현 | 완료된 Seungun 분석을 재실행·변경하지 않고 동일 attempt에 묶고 receipt 재발급 우회를 차단하기 위함 |
| 2026-08-28 | VE6를 자동 claim-policy conformance gate로 정의 | 공개 데이터 기반 잠금 결과와 동결 catalog를 결정론적으로 검사하고 지원하지 않는 claim을 fail-closed하기 위함 |
| 2026-08-28 | 제한 저장·FFmpeg 파생·MediaPipe worker를 attempt-level adapter로 분리 | path-free lineage와 자원·egress 경계를 구현하되 미완료된 사용자 QC·sync·P6/P8 claim과 구분하기 위함 |
| 2026-08-28 | same-attempt binding v2, commit 후 0400, 격리 Seungun과 미보정 content-sync candidate를 구현 | audio derivation 계보·불변 저장·실행 경계를 강화하고 candidate 계산과 P6 reliability 승격을 분리하기 위함 |
| 2026-08-28 | RGB/landmark QC candidate와 runtime detection v2를 연결하고 launcher escape 경계를 강화 | 측정·lineage 구현과 P6 보정 판정을 분리하고 decoder/ML subprocess의 우회 경로를 줄이기 위함 |
