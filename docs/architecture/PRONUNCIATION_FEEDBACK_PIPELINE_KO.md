# seungun → 입술 모양 피드백 sidecar 연동 계약

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `implemented_contract_with_internal_smoke`
- 구현 상태: `partial_implementation`; handoff facade는 구현, production host 연동은 `not_ready`
- 호환성 역할: 기존 파일 경로를 유지하는 연동 계약
- 상세 정본: [seungun 입술 모양 피드백 sidecar 상세 설계](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)

## 1. 이 문서가 설명하는 것

이 파일은 과거의 전체 발음 피드백 파이프라인 문서가 아니다. 현재 역할은
`seungun`이 이미 생성한 오디오 발음 분석을 입술 모양 피드백 sidecar가 읽기 위한
단방향 연동 계약을 정의하는 것이다.

```text
seungun audio core
  -> immutable PronunciationAnalysis v1 reference
  -> host-invoked SeungunAttemptBindingFacade + caller-owned reliability receipt
  -> SeungunPronunciationEvidencePort
  -> lip-feedback sidecar
  -> VisualFeedbackSupplement
  -> host/seungun-owned final decision and message
```

이 문서의 이름은 기존 링크 호환성을 위해 남겨 둔다. 범위 수정 전 전체 음성 코칭
설계는
[역사 문서](../history/legacy_voice_coach_architecture_2026-08-26/PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)에
보존되어 있으며 현재 정본이 아니다.

## 2. 소유권과 의존성 방향

의존성은 반드시 입술 sidecar에서 `seungun`으로 흐른다.

```text
Composition Root
  -> VisualFeedbackCoordinator
       -> visual domain
       -> SeungunPronunciationEvidencePort
            <- FrozenSeungunPronunciationEvidenceAdapter
                 -> injected immutable analysis source
```

규칙은 다음과 같다.

1. `seungun`은 visual/lip package를 import하지 않는다.
2. visual domain과 application service는 concrete detector나 `seungun` 파일 경로를
   import하지 않는다.
3. concrete adapter만 `korean_phone_ctc.pronunciation_analysis.v1` JSON schema를
   안다. `FrozenPronunciationDetector` class는 import하거나 호출하지 않는다.
4. visual 요청을 처리하기 위해 detector를 다시 실행하지 않는다. 이미 생성되어
   불변으로 보관된 analysis reference를 소비한다.
5. adapter는 upstream 값을 내부 DTO로 명시적으로 변환하지만 phone, 위치, score,
   threshold 또는 `is_error`를 다시 계산하지 않는다.
6. 최종 issue 선택, 전체 coaching 상태와 사용자 메시지는 host/`seungun` 소유다.

`docs/architecture` 자체는 문서 디렉터리다. 실행 구현은
`src/voice_coach/visual_feedback`에 있으며 위 단방향 의존성을 따른다.

## 3. 현재 확인된 seungun 계약

현재 코드에서 관찰되는 upstream 호출은 다음과 같다.

```python
detector = FrozenPronunciationDetector(
    repository_root=repository_root,
    device=device,
)
analysis = detector.analyze(
    audio_path=audio_path,
    expected_phones=expected_phones,
)
```

근거:

- [frozen detector 구현](../../seungun/src/korean_phone_ctc/frozen_detector.py)
- [현재 seungun audio pipeline](../../seungun/docs/CURRENT_PIPELINE_KO.md.md)
- [온라인 inference 보충 상태](../../seungun/docs/HYPERCLOVAX_INTEGRATION_STATUS_KO.md)
- [audio reliability 개선 제안](../../seungun/docs/PIPELINE_RELIABILITY_IMPROVEMENT_PROPOSAL_KO.md)

`PronunciationAnalysis` v1은 다음 종류의 값을 제공한다.

- `schema_version`
- detector/config/backbone provenance
- `expected_phones`
- target-free `ctc_hypothesis`
- 위치가 충분하지 않은 `insertions`
- expected phone별 `PhoneEvidence`
- score 의미와 사용 제한을 설명하는 `notices`

phone evidence에는 expected index/token, CTC candidate, alignment operation, nullable
`start_ms/end_ms`, `error_score`, operating threshold, `is_error`, score semantics와
soft feature가 있다.

다음 의미를 변경해서는 안 된다.

- `error_score`는
  `detector_ranking_score_not_calibrated_correctness_confidence`이다.
- `observed_candidate`는 CTC alignment 후보이며 정답 전사가 아니다.
- deletion은 유효한 span이 없을 수 있다.
- insertion은 expected phone index와 시간 anchor가 충분하지 않다.
- CTC span은 gold lip-event boundary가 아니다.

## 4. 필요한 upstream handoff

sidecar는 detector 객체나 임의 JSON path 대신 opaque reference를 받는다.

```python
@dataclass(frozen=True, slots=True)
class SeungunAnalysisReference:
    reference_id: str
    attempt_id_sha256: str
    analysis_sha256: str


class SeungunPronunciationEvidencePort(Protocol):
    def resolve(
        self,
        *,
        analysis_ref: SeungunAnalysisReference,
        binding_receipt: SameAttemptBindingReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> SeungunEvidenceSnapshot: ...
```

이 Protocol과 이름은 현재 구현돼 있다. detector 비의존
`SeungunAttemptBindingFacade.bind_completed_analysis()`는 완료된 analysis의 `Mapping`
또는 `to_json_object()` 계약만 받고 `SeungunVisualHandoff` 안에 request와 source
record를 함께 만든다. `InMemorySeungunAnalysisSource`는 internal test double이며 실제
저장 방식, reference 수명과 retry/idempotency는 production composition이 정해야 한다.

`pronunciation_attempts`에는 restricted PCM을 0600 WAV로 생성하고 fsync·0400 commit 뒤
격리된 Seungun worker에서 detector를 한 번 호출한 다음 삭제하는 runtime adapter와,
container/audio derivation lineage를 검증해 위 facade에 전달하는 handoff adapter도 있다.
internal CLI와 local composition root는 권장 pre-opened read-only `--video-fd`로 합성
H.264/AAC MP4, 공식 landmark
model과 CUDA Seungun에서 audio 완료·visual unavailable,
`video_qc_candidate_not_calibrated`·`content_av_sync_candidate_not_calibrated`, cleanup
완료까지 실행됐다. `--video`는 path가 argv/shell history에 남을 수 있는 내부 편의
기능이다. 다만 persistent source 재로드나 실제 사용자 영상 실행 완료를
뜻하지 않는다.

`SeungunEvidenceEnvelope`에는 최소한 다음이 필요하다.

- 변경하지 않은 `PronunciationAnalysis v1`
- canonical analysis digest와 schema version
- attempt ID 또는 같은 시도를 증명하는 opaque binding ID
- prompt ID/revision과 expected-phone source revision/digest
- detector-input audio digest 또는 동등한 source-binding receipt
- audio timebase와 trim/pad/resample transform receipt
- upstream audio reliability/support receipt
- facade-issued registered binding digest
- 필수 audio derivation provenance digest; `SameAttemptBindingReceipt v2`가 직접 attest
- facade/backend revision과 failure state

raw audio path, raw video path, speaker identifier, 인증 정보는 envelope의 public DTO나
로그에 포함하지 않는다.

## 5. 현재 차단 조건

현재 `PronunciationAnalysis v1` 자체에는 다음이 없다.

- attempt와 prompt binding
- input audio digest/source identity
- 명시적인 audio timebase receipt
- audio QC/content/OOD/calibration을 포함한 reliability receipt

따라서 현 schema만으로는 video가 같은 발화 attempt의 audio와 결합됐는지 증명할 수
없다. 구현된 facade는 caller가 제공한 attempt/prompt/audio/video/timebase/consent와
upstream reliability를 canonical analysis snapshot에 묶고, source record에 발급
binding digest를 등록한다. evidence adapter는 presented receipt digest와 이 등록값의
exact match를 요구하므로 필드가 같은 별도 재발급 receipt도 거부한다. facade는
reliability를 합성하지 않는다. 필수 `audio_derivation_receipt_sha256`는 source record에
보존될 뿐 아니라 `SameAttemptBindingReceipt v2`의 self-digest와 exact-match 검증에
직접 포함된다.

local media admission, FFmpeg derivation과 handoff lineage bridge는 구현·unit test됐다.
RGB/landmark QC와 lip/PCM sync candidate는 runtime visual detection v2 receipt에 묶인다.
두 candidate는 P6 전 `candidate_unvalidated`/`unavailable`이며 pose·occlusion이나
calibrated pass/reliability를 만들지 않는다. 남은 blocker는 persistent source 재로드,
실제 사용자 attempt 실행 검증, camera-upload API/UI, 실제 사용자 영상 QC/content-sync
성능과 P6/P8 reliability다. TTL/orphan scavenger/persistent cleanup ledger와
`RLIMIT_NPROC`/`RLIMIT_FSIZE`도 없다. launcher 경계는 전체 sandbox가 아니다. 현재
고정 Seungun v1 analysis는 sidecar anchor로 사용할 수 있다는 제한된 upstream
`supported` receipt를 가진다. 그러나 reliability bridge는 사용자 runtime P6 calibration
artifact와 P8 catalog가 없으면 visual을 차단하므로 실제 사용자 영상에서
`visual_feedback_ready`를 발행할 수 없다.

이 상태에서 금지되는 fallback은 다음과 같다.

- target text 또는 expected phones가 같다는 이유만으로 같은 attempt라고 가정
- 파일명, 배열 순서 또는 timestamp 추정으로 analysis와 video를 결합
- visual 요청 중 detector를 재실행해 binding 결손을 숨김
- `offset_ms=0` 또는 audio reliability `passed`를 기본값으로 주입
- hash/revision이 고정되지 않은 legacy bridge의 top-1 결과를 현재 upstream
  `supported` 범위로 확대 해석

## 6. adapter 책임

`FrozenSeungunPronunciationEvidenceAdapter`는 anti-corruption layer다.

허용 책임:

- opaque analysis reference를 host-managed facade/source에서 resolve
- schema version과 required receipt 확인
- upstream dataclass/JSON을 typed internal snapshot으로 변환
- upstream exception과 schema mismatch를 명시적 dependency failure로 mapping
- original analysis와 provenance를 변경 없이 감사용으로 보존
- valid phone span을 visual time-axis join의 읽기 전용 anchor로 제공

금지 책임:

- audio 파일 decode, audio QC, content match, OOD 또는 calibration 수행
- expected phones 생성·수정
- detector 재실행 또는 model artifact 선택
- phone alignment 수정 또는 span 보간
- score를 confidence/severity로 개명
- threshold나 `is_error` 재판정
- top-1 issue, 전체 coaching 결과 또는 사용자 메시지 선택

filesystem, model runtime 또는 future seungun service 접근이 필요하면 해당 I/O는 이
concrete adapter 뒤에 둔다. visual domain에는 framework, path, network response를
노출하지 않는다.

## 7. visual sidecar에 전달하는 내부 snapshot

내부 snapshot은 upstream 의미를 축소하거나 덮어쓰지 않는다.

```text
SeungunEvidenceSnapshot
  binding_receipt
  reliability_receipt
  analysis_schema_version
  analysis_digest
  detector_provenance
  expected_phone_digest
  phone_anchors[]

UpstreamPhoneAnchor
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

visual code는 이 snapshot을 audio truth로 해석하지 않는다. 유효한 span과 binding이
있는 phone에 대해 직접 보이는 lip cue를 연결하기 위한 anchor로만 사용한다.

## 8. 반환 계약

sidecar의 유일한 public 결과는 `VisualFeedbackSupplement`다. 이 결과는 최종
진단·응답 DTO가 아니다.

`VisualFeedbackSupplement`는 다음을 담을 수 있다.

- visual-only status와 reason codes
- 참조한 upstream analysis/binding receipt ID
- read-only upstream phone anchor ID/index
- 직접 관찰한 lip cue evidence와 provenance
- `VisualEvidenceRelation`
- visual 전용 calibration/support receipt
- 최대 한 개의 allowlist supplemental visual action candidate
- 임시 media 삭제 receipt

다음 필드는 반환하지 않는다.

- 새 audio phone 또는 변경된 phone
- 변경된 span, score, threshold 또는 `is_error`
- 전체 severity/confidence
- 최종 coaching issue/status/message
- LLM이 생성한 free prose
- 혀·성문·기류처럼 영상에서 직접 보이지 않는 신체 원인

## 9. 상태 호환성

이 연동 계층은 상세 설계의 enum을 그대로 사용한다.

```text
VisualFeedbackStatus
  visual_feedback_ready
  no_supported_lip_cue
  visual_rerecord_required
  visual_uncertain
  visual_unsupported
  dependency_unavailable
  visual_failed_closed

VisualCueState
  observed
  not_observable
  missing
  low_confidence
  out_of_distribution
  backend_unavailable

VisualEvidenceRelation
  supports_upstream
  conflicts_with_upstream
  not_comparable
```

visual status를 기존 audio/coaching terminal status로 변환하는 것은 이 sidecar의
책임이 아니다. host는 supplement를 무시하거나 보류할 수 있으며 upstream 결정을
항상 보존해야 한다.

## 10. 구현 및 남은 검증 조건

현재 internal adapter는 schema/binding mapper, self-digest tamper 차단, registered
binding digest exact-match, phone index/span 보존과 path-free supplement를 검사한다.
handoff facade는 concrete detector를 import·호출하지 않고 analysis를 한 번만
직렬화한다. 다음 production 조건은 남아 있다.

1. 합성 composition에서 확인한 local admission/FFmpeg/handoff/detection v2 lineage를
   실제 사용자 attempt와 제품 entrypoint에서 재검증
2. persistent source에서 analysis reference와 registered binding을 atomic 저장·재로드
3. retry/idempotency와 충돌 request 정책 검증
4. unknown schema·누락 receipt fail-closed test
5. raw path·identifier·digest의 public/LLM egress 거부 test
6. TTL/orphan scavenger와 persistent cleanup ledger 구현·복구 test
7. 미보정 video-QC/content A/V sync candidate를 실제 사용자 영상에서 검증하고 P6/P8
   reliability receipt에 연결

이 조건은 상세 visual 성능 gate보다 앞선 dependency gate다. facade unit test와
internal source smoke를 production host/media 연동 완료로 기록하지 않는다.
