# 시각 보조 피드백과 seungun 표현 계층의 전달 경계

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `implemented_serialization_without_host_integration`
- 구현 상태: `partial_implementation`; visual presentation integration은 `not_ready`
- 파일명 상태: `legacy_compatibility_name`

이 문서는 HyperCLOVA X의 설치·운영 문서가 아니다. 현재
[`docs/architecture`](README_KO.md)가 소유하는 범위는 입술 모양 시각 sidecar가
`seungun` 또는 host의 표현 계층에 보조 근거를 넘기는 경계뿐이다.

범위 수정 전 오디오 표현 계층 문서는
[`historical_snapshot`](../history/legacy_voice_coach_architecture_2026-08-26/HYPERCLOVAX_GROUNDED_FEEDBACK_KO.md)에
보존되어 있으며 현재 시각 아키텍처 정본이 아니다. 현재 오디오 구현과 확인된
smoke는
[`seungun` 통합 상태](../../seungun/docs/HYPERCLOVAX_INTEGRATION_STATUS_KO.md)가
자체적으로 기록한다.

## 1. 소유권

시각 sidecar는 다음을 소유하지 않는다.

- HyperCLOVA X client, model runtime, prompt 또는 출력 parser
- 오디오 issue 선택, 전체 coaching policy 또는 최종 사용자 문장
- LLM timeout·retry·fallback 정책
- allowlist 밖의 입술 동작 지식이나 행동 지시 생성

시각 sidecar는 HyperCLOVA X를 직접 호출하지 않는다. `seungun` 또는 host의
표현 계층이 어떤 renderer나 LLM을 사용할지는 upstream 책임이다. 표현 계층은
시각 근거를 새로 판정하거나 `seungun`의 audio decision을 변경할 수 없다.

## 2. 전달 계약

시각 sidecar는
[`SeungunPronunciationEvidencePort`](component.md)를 통해 이미 생성되고 변경할 수
없는 upstream 분석 envelope를 소비한다. 반환값은
`VisualFeedbackSupplement` 하나뿐이다.

```text
seungun immutable analysis envelope
        |
        v
visual lip sidecar
        |
        | VisualFeedbackSupplement
        v
seungun 또는 host의 decision/presentation 경계
        |
        +-- 보조 claim 생략
        +-- 검증·허용된 visual claim candidate를 결정론적으로 렌더링
        `-- upstream 소유 표현 계층에 제한된 plan 선택을 요청
```

`VisualFeedbackSupplement`는 다음 의미를 보존한다. exact 직렬화 schema는
`voice_coach.visual_feedback_supplement.v1`로 구현됐고 unknown field/enum을
fail-closed한다.

- immutable upstream analysis reference와 호환성 receipt
- [`VisualFeedbackStatus`](state.md)
- [`VisualEvidenceRelation`](state.md)
- upstream phone/time span을 그대로 가리키는 evidence pointer
- 직접 관찰된 외부 lip cue와 품질·provenance
- 검증·허용된 visual claim catalog 범위의 claim candidate ID 또는 `null`
- 금지된 해부학·신체 원인 claim과 disclosure 요구

raw video/frame, audio, media locator, 얼굴 image·embedding·mesh, speaker ID와
자유서술 prompt는 이 전달 객체나 표현 모델 입력에 포함하지 않는다. host용
supplement에는 integrity/provenance용 SHA-256 reference가 포함되지만, 이는 사용자
문장이나 LLM prompt로 전달할 내용이 아니므로 presentation adapter가 제거해야 한다.

## 3. 표현 허용 조건

- `visual_feedback_ready`일 때만 검증·허용된 visual claim candidate를 upstream에
  제안할 수 있다. 이 상태도 최종 렌더링을 강제하지 않는다.
- `supports_upstream`은 보조 근거일 뿐 `seungun` 판정의 재판정이 아니다.
- `conflicts_with_upstream`은 평균하거나 문장으로 완화하지 않는다. sidecar는
  `visual_uncertain`을 반환하고 upstream이 보류·생략 정책을 결정한다.
- `no_supported_lip_cue`, `visual_rerecord_required`, `visual_uncertain`,
  `visual_unsupported`, `dependency_unavailable`, `visual_failed_closed`에는
  렌더링 가능한 visual claim을 넣지 않는다.
- renderer 또는 LLM이 실패해도 sidecar가 새 사실을 생성해 빈 영역을 채우지
  않는다. upstream은 allowlist의 결정론적 문구를 사용하거나 visual supplement를
  생략해야 한다.

## 4. 현재 확인된 것과 확인되지 않은 것

현재 `seungun` 오디오 issue를 제한형 HyperCLOVA X 표현 계층에 연결한 구현과
단일 smoke 기록은 존재한다. 이는 오디오 경로의 구현 근거다.

현재 visual 쪽에서 구현·검증된 항목:

- canonical `VisualFeedbackSupplement.to_json()/from_json()` round-trip
- exact enum/field, candidate cardinality, observed cue와 cleanup 불변식
- raw path/frame/landmark가 없는 synthetic output test
- catalog ID만 전달하고 자유 문장을 생성하지 않는 contract
- detector 비의존 handoff와 registered binding digest exact-match contract
- 필수 audio derivation digest를 self-digest에 포함하는 same-attempt binding v2
- 합성 H.264/AAC 권장 `--video-fd` CLI의 공식 landmark model+CUDA Seungun audio 완료,
  visual unavailable, QC·content-sync candidate not calibrated, cleanup 완료

다음은 구현·검증되지 않았다.

- local composition/internal CLI를 persistent source 재로드 및 제품 entrypoint로 검증하고
  실제 사용자 영상 한 attempt로 성공 실행한 production 근거
- 사용자용 visual claim catalog의 P8 locked replay와 자동 claim-policy conformance
- visual supplement를 seungun/host 표현 계층에 전달하는 통합 경로
- provenance hash를 LLM/presentation view에서 제거하는 host adapter
- visual claim fidelity, privacy, timeout/fallback 통합 테스트
- 실제 학습자 대상 시각 피드백 정확성 또는 교육 효과

따라서 현재 오디오 HyperCLOVA X smoke를 시각 통합 완료나 시각 피드백 검증으로
승격하지 않는다.
