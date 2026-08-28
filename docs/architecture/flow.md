# 입술 모양 피드백 Sidecar 흐름

- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; 사용자 실영상 runtime은 `not_ready`
- 상세 설계: [`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)
- 데이터 후보: [`VISUAL_DATASET_EVIDENCE_LEDGER_KO.md`](VISUAL_DATASET_EVIDENCE_LEDGER_KO.md)

## 1. Actor와 진입 조건

- Actor: `seungun` audio 결과를 이미 보유한 상위 composition root
- Entry point: in-process coordinator/facade와 internal CLI. 권장 `--video-fd`는 사전에
  read-only로 연 `fd >= 3`을 받아 participant path를 argv에서 제외한다. `--video`는
  argv/shell-history 노출 가능성이 있는 내부 편의 기능이다. 합성 H.264/AAC
  `--video-fd` CUDA+MediaPipe E2E는 성공, 실제 사용자 영상 성공 실행은 미검증
- Required input: 검증된 upstream evidence reference, opaque video handle, consent receipt
- Side effects: 내부 fixture record와 local restricted media 삭제 receipt; production audit
  ledger는 미구현
- HTTP/API/session: 이 sidecar 범위 밖

별도 내부 gate-evidence CLI는 `p6|p8 --bundle` 또는 pathless read-only
`p6|p8 --bundle-fd`로 aggregate/frozen JSON만 검증한다. 이 흐름은 media runtime과
분리되며 성공해도 runtime reliability나 claim publication을 활성화하지 않는다.

sidecar는 raw audio를 다시 분석하거나 `seungun` detector를 별도 실행하는 흐름이
아니다. 먼저 완료된 upstream 결과를 concrete adapter가 visual 내부 계약으로
변환한 뒤 visual 처리만 수행한다.

## 2. 기본 흐름

```text
seungun authoritative result
        |
        v
SeungunAttemptBindingFacade
  canonical snapshot + atomic request/source record
  registered binding digest + caller reliability
        |
        v
upstream schema/binding/reliability validation
        |
        +-- invalid/missing --> dependency_unavailable
        |
opaque video + consent
        |
        v
rights/admission -> video QC -> A/V sync -> lip tracking
        |                |             |
        |                |             +-- uncertain --> visual_uncertain
        |                +-- recapturable failure --> visual_rerecord_required
        |                +-- unsupported scope -----> visual_unsupported
        +-- denied/integrity failure ---------------> visual_failed_closed
        |
        v
upstream target/span과 lip timeline join
        |
        v
visible lip cue extraction + reference/calibration
        |
        +-- usable cue 없음 ----------> no_supported_lip_cue
        +-- low confidence/conflict ---> visual_uncertain
        +-- OOD/지원 범위 밖 ----------> visual_unsupported
        |
        v
supports_upstream + allowlisted advice
        |
        v
visual_feedback_ready (supplement only)
```

## 3. 단계별 처리

### F0. upstream dependency 검증

1. `SeungunAttemptBindingFacade`가 완료된 analysis를 재실행 없이 한 번
   canonicalize하고 request/source record를 함께 만든다.
2. source record에 facade 발급 binding digest를 등록하고 caller가 제공한 reliability
   receipt를 그대로 보존한다.
3. `SeungunPronunciationEvidencePort`가 schema/revision과 presented binding digest의
   registered value exact-match를 검사한다.
4. prompt, attempt, media binding, expected phone index와 location/timebase를 검증한다.
5. adapter는 `error_score`, threshold, `is_error`, phone과 location을 그대로 보존한다.

필수 binding 또는 reliability가 없으면 `dependency_unavailable`로 visual 처리를
종료한다. `seungun` audio 결과에는 상태 전이나 부수효과가 없다.

`audio_derivation_receipt_sha256`는 `SameAttemptBindingReceipt v2`의 필수 필드이며
self-digest, source record와 presented receipt의 exact-match 대상이다. local FFmpeg
`AudioDerivationReceipt`와 container/audio lineage를 handoff에 전달하는 bridge는 unit
test로 검사됐다. internal CLI/local composition은 권장 pre-opened read-only
`--video-fd`로 합성 H.264/AAC MP4, 공식 MediaPipe model과 CUDA Seungun에서 audio
완료·visual unavailable·cleanup 완료를 반환했지만 persistent source 재로드와
실제 사용자 영상 실행은 아직 검증되지 않았다.

### F1. 동의와 제한 media admission

facial-video 처리 목적, 보존, 삭제와 egress 권리를 decode 전에 검사한다. locator는
즉시 opaque handle로 바꾸고 이후 domain/service에 path를 전달하지 않는다.
권리 거부, binding digest 불일치 또는 cleanup 정책을 만족할 수 없는 경우
`visual_failed_closed`다.

`LocalRestrictedVideoAdmissionAdapter`는 consent를 source I/O 전에 검사하고 no-follow
regular local file만 크기 제한 안에서 0700 workspace에 0600으로 생성한 뒤 fsync와
0400 commit을 거쳐 보관한다. 파생 PCM도 동일하게 commit 뒤 0400이다. 원본은
수정·삭제하지 않으며, 파생 PCM을 포함한 restricted workspace는 명시적 delete와
path-free receipt로 정리한다. cleanup 실패 시 facade는 이미 계산된 audio·visual
반환값을 모두 억제하고 attempt를 `failed_closed`로 반환한다. camera/upload/API
admission은 아직 연결되지 않았다. 요청 종료 delete 외 TTL enforcement, orphan
scavenger와 persistent cleanup ledger는 구현되지 않았다.

### F2. video QC와 정면 pose

container/codec, timestamp, 얼굴 수, track continuity, 정면 pose, lip ROI coverage,
blur, 가림과 조명을 관찰한다.

- 같은 환경에서 재촬영하면 회복 가능한 품질 문제: `visual_rerecord_required`
- 지원 정면 범위나 입력 capability 밖: `visual_unsupported`
- backend 미가용: cue에는 `backend_unavailable`; 필수 backend면 visual 결과를 발행하지 않음

QC 실패를 score 0 또는 정상 cue로 변환하지 않는다.

`FfmpegAvContainerAdapter`는 single MP4/H.264 video + AAC audio, duration/geometry/FPS,
frame count와 monotonic timestamp를 strict policy로 probe하고, 첫·마지막 frame을 포함해
전 구간의 bounded RGB24 frame을 결정론적으로 균등 sample한다. FFmpeg/ffprobe는
pre-exec seccomp와 resource limit launcher 뒤에서 실행된다. launcher는 io_uring과
일반 process/session/group escape를 차단하고 thread clone만 허용한다. FFmpeg stdout/
stderr pipe는 nonblocking이며 어떤 예외에서도 process group을 종료한다. 이 경로는
합성 비개인 MP4와 local FFmpeg/ffprobe로 실행됐다.

`VideoQcCandidateAdapter`는 decoded RGB와 MediaPipe landmark를 exact timestamp/
container/model lineage로 묶고 single/multiple-face coverage, 전체·lip-ROI luma와
under/overexposure fraction, neighbor-luma edge sharpness proxy, lip ROI coverage,
single-face run과 lip-center track continuity를 계산한다. 결과는 runtime visual detection
v2 receipt에 포함된다. P6 전에는 `candidate_unvalidated` 또는 `unavailable`일 뿐이며
pose·occlusion, calibrated QC pass나 reliability를 만들지 않는다. 실제 사용자 영상의
QC 분포와 rerecord/unsupported threshold도 검증되지 않았다.

### F3. lip tracking과 A/V sync

upstream audio clock/phone span과 video clock의 offset, drift와
불확실성을 계산한다. sync 실패를 `offset=0`으로 대체하거나 positional heuristic으로
복구하지 않는다.

내부 adapter는 주입된 timestamp anchor로 affine sync를 먼저 적합한 뒤, 해당
video window 안의 최소 2D lip 좌표를 aperture/closure aggregate로 바꾼다.
`MediaPipeLipLandmarkAdapter`와 network-isolated worker는 bounded RGB frame에서 얼굴
수를 세고 single-face일 때 네 점의 최소 lip geometry를 반환하도록 구현됐다. worker는
pre-exec seccomp/resource-limit launcher 뒤에서 bounded streaming I/O로 실행된다.
`LipAudioAvSyncAdapter`는 이 geometry의 aperture ratio와 restricted PCM energy를
교차상관해 offset·anchor candidate와 exact lineage receipt를 계산한다. 합성 신호 unit
test에서 `candidate_unvalidated`를 확인했지만, P6 calibration 전에는 sync reliability나
visual claim 근거로 승격하지 않는다. MediaPipe 공식 model은 합성 CLI에서 실행됐지만
공개 test portrait의 실제 얼굴 landmark/QC·sync candidate는 관측됐지만 실제 사용자
영상의 head-pose normalization과 sync 성능은 검증되지 않았다.
runtime bridge v2는 sample/probe/binding, frame timestamp, landmark/model과 QC/A-V-sync
receipt를 하나의 typed detection receipt로 묶고, 다른 container sample을 detector
호출 전에 거부하는 unit test를 통과한다.

### F4. timeline join

upstream이 제공한 expected phone index와 location만 join anchor로 사용한다.
visual sidecar는 phone, index 또는 location을 새로 선택하지 않는다. span이 없거나
media binding이 일치하지 않으면 비교 가능한 visual cue를 만들지 않는다.

### F5. cue, reference와 relation

target 설계는 lip closure, aperture, rounding, spreading과 timing을 지원한다. 현재
내부 baseline은 aperture ratio와 closure fraction만 계산하며, synthetic artifact에는
`ㅏ` aperture rule 한 개만 있다.
각 cue는 `VisualCueState`와 reference/calibration receipt를 가진다.

relation은 다음 중 하나다.

- `supports_upstream`
- `conflicts_with_upstream`
- `not_comparable`

visual-only deviation으로 새 발음 오류를 만들지 않는다. conflict는 평균이나 가중합으로
지우지 않고 `visual_uncertain`으로 보존한다.

### F6. 보조 피드백 구성

`visual_feedback_ready`는 다음을 모두 만족해야 한다.

- upstream dependency와 media binding 검증 통과
- usable video와 sync
- `VisualCueState=observed`
- 지원되는 visual reference와 calibration
- `VisualEvidenceRelation=supports_upstream`
- 동결된 visual advice catalog에 정확히 한 개의 허용 action/claim candidate ID 존재

추가로 사용자 runtime은 calibration과 catalog가 `calibrated` scope여야 한다. 현재
저장소 artifact는 `synthetic_contract_only`이므로 내부 contract scope에서는 ready
경로를 검사할 수 있지만 `USER_RUNTIME`에서는 반드시 `visual_uncertain`이다.

composer는 허용 candidate ID를 보조 결과로 변환할 뿐 전체 coaching 문장이나 audio action을
선택하지 않는다. 최종 presentation은 `seungun` 또는 상위 시스템 책임이다.

## 4. empty/error/retry 동작

| 상태 | 의미 | 다음 행동 |
|---|---|---|
| `no_supported_lip_cue` | 유효한 처리였으나 발행 가능한 입술 cue가 없음 | audio 결과만 유지 |
| `visual_rerecord_required` | blur·가림·정면 pose·track 품질처럼 재촬영으로 회복 가능 | video만 재촬영; audio 재녹음 결정은 seungun 책임 |
| `visual_uncertain` | low confidence, sync 불확실 또는 audio/visual conflict | visual advice 미발행 |
| `visual_unsupported` | cue, pose, device/cohort가 지원 범위 밖 | visual 기능을 사용하지 않음 |
| `dependency_unavailable` | upstream schema/binding/reliability가 불충분 | visual 기능을 건너뛰고 audio 결과 유지 |
| `visual_failed_closed` | 권리·무결성·계약·삭제·필수 adapter 불변식 위반 | visual publication 금지; host cleanup 실패면 audio 반환값도 억제 |

자동 retry는 하지 않는다. 재촬영은 사용자 또는 상위 시스템이 명시적으로 새
video attempt를 제출할 때만 수행한다.

## 5. 관찰 가능한 출력과 금지 부수효과

출력에는 visual status, upstream reference, visible cue summary, relation,
advice ID와 public notice만 포함한다. raw media, landmark, embedding, local path,
participant ID와 secret은 포함하지 않는다.

visual 흐름은 다음 부수효과를 만들 수 없다.

- upstream `error_score`, threshold, `is_error`, phone/location 수정
- visual cue로 audio 판정 자체를 변경하거나 재분석
- 전체 attempt/session 상태 저장
- Oracle/S1/S2/TTS/G04/Track E 실행
- 최종 사용자 coaching 문장 또는 전역 행동 선택

## 6. 현재 검증된 내부 흐름

`scripts/run_visual_feedback_contract_smoke.py`는 실제 media와 모델 호출 없이 내부
ready, 사용자 synthetic 차단, consent 선행 차단, blur 재촬영, sync 불확실,
동일-attempt mismatch, 모든 terminal의 cleanup과 supplement JSON round-trip을 검사한다.

별도 unit test는 local 제한 저장의 commit 후 0400, 실제 FFmpeg/ffprobe 기반 합성 MP4
probe·전 구간 균등 RGB decode·PCM derivation, MediaPipe worker network 차단,
RGB/landmark QC candidate, lip/PCM sync candidate, io_uring·process escape 차단과
nonblocking FFmpeg pipe/process-group 종료를 검사한다. cleanup 실패 시 audio를 포함한
결과 전체가 억제되는 것도 검사한다. internal CLI는 합성 H.264/AAC MP4, 공식 landmark
model과 CUDA Seungun에서 `audio_completed_visual_unavailable`,
`cleanup_status=completed`, `video_qc_candidate_not_calibrated`와
`content_av_sync_candidate_not_calibrated`를 반환했다. 이는 실제 사용자 영상
QC, pose/occlusion, P6 calibration, P8 evaluation 또는 사용자 claim 검증이 아니다.
