# 서비스·애플리케이션 규칙 - intelligentAI

## 책임

application/service는 유스케이스의 순서와 판단을 소유한다. 이 저장소의 대표
orchestrator는 `ClosedLoopSession`, `PronunciationFeedbackPipeline`,
`VideoAttemptFacade`, `VisualFeedbackCoordinator`다.

서비스는 다음을 담당한다.

- 신뢰 경계별 입력 검증과 권한·동의 확인
- 단계 실행 순서와 상태 전이
- port를 통한 외부 효과 호출
- gate, fallback, retry 금지/허용, cleanup 정책
- 내부 결과를 privacy-safe service result로 조립

서비스는 FastAPI `Request`, `Response`, `Depends`, HTTP status code, argparse
namespace 또는 GUI widget을 알지 않는다.

## 의존성 주입

- 외부 효과는 `Protocol` port로 표현하고 constructor 또는 composition function에서
  주입한다.
- service 안에서 concrete model, HTTP client, subprocess, FFmpeg, MediaPipe,
  filesystem store를 즉석 생성하지 않는다.
- optional backend는 명시적 unavailable adapter 또는 `None` 정책으로 처리한다.
  backend 부재를 성공값이나 기본 점수로 대체하지 않는다.
- port 메서드는 비즈니스 의도를 드러내는 좁은 입력·출력을 사용한다. upstream
  SDK 객체나 process handle을 서비스 전반에 퍼뜨리지 않는다.

## 유스케이스 작성 규칙

- public method는 `run_attempt`, `prepare_prompt`, `create_visual_feedback`처럼 결과를
  드러내는 이름을 사용한다.
- orchestration method가 길어질 수는 있지만, 각 helper는 admission, analysis,
  gate, mapping, cleanup처럼 검토 가능한 한 단계만 담당한다.
- 여러 단계의 성공을 하나의 boolean으로 압축하지 않는다. 기존 enum, receipt,
  reason code를 보존한다.
- 외부 예외를 public 결과에 그대로 노출하지 않는다. adapter/service 전용 예외나
  안정된 reason code로 매핑한다.
- `except Exception`은 마지막 fail-closed 경계 또는 보안상 예외 원문을 숨겨야 하는
  경계에서만 사용하고, 주석과 테스트로 의도를 고정한다.
- 숨은 retry를 추가하지 않는다. retry가 계약에 있으면 횟수, timeout, idempotency,
  최종 실패 상태를 명시한다.

## 발음 피드백 파이프라인

- prompt catalog와 expected-phone sequence는 host가 신뢰된 입력에서 만든다.
- content match가 부족하면 발음 진단보다 재녹음 판단을 우선한다.
- S1과 독립 S2의 근거·독립성 조건을 유지한다.
- score가 없거나 신뢰할 수 없으면 `MetricState`를 유지하고 0으로 채우지 않는다.
- 승인된 advice catalog/claim 밖의 문구를 모델이나 service가 새로 만들지 않는다.
- LLM 출력은 최종 권위가 아니라 승인된 claim의 제한 표현 계획이다. 검증 실패 시
  정확한 catalog 문구로 fallback한다.

## 영상 시도 서비스

- caller는 phone index, expected-phone sequence, binding receipt, reliability receipt를
  제공할 수 없다. 모두 신뢰된 host 단계에서 발급한다.
- 입수한 MP4의 내장 audio를 Seungun이 정확히 한 번 분석한 뒤 같은 결과의 selected
  phone만 visual anchor로 사용한다.
- visual detector 결과의 존재와 reliability 승인을 구분한다.
- visual 실패는 audio-only 결과로 축소할 수 있지만, consent/media/audio/Seungun 또는
  cleanup 실패는 서비스 계약에 따라 전체 fail-closed 한다.
- 제한 media 삭제는 `finally` 성격의 서비스 불변식이다. 삭제 receipt가 없으면 완료로
  보고하지 않는다.

## 연구·학습 서비스

- 실행 전에 config, source pin, predecessor receipt, split, rights/human gate를
  검증한다.
- 준비, 학습, 평가, promotion을 한 메서드나 한 CLI 호출로 암묵적으로 연결하지
  않는다.
- smoke와 full 실행은 같은 gate를 따르며, smoke라는 이유로 human/locked-data
  경계를 우회하지 않는다.
- output은 schema version, status, source/model revision, digest와 실패 이유를
  포함하는 기존 계약을 유지한다.
- artifact publish는 no-overwrite 또는 원자 교체 등 해당 모듈의 기존 정책을 따른다.

## Presentation과의 경계

- API/CLI는 service command를 만들고 결과를 직렬화한다.
- 학습자용 view는 service가 승인한 내용만 표현하며 새 진단이나 조언을 만들지 않는다.
- HTTP 오류 매핑은 route/app factory에 두되, 원인은 service/domain 예외나 reason code로
  먼저 표현한다.
- 기존 route에 경계 위반이 있어도 별도 요청 없이 전체를 리팩터링하지 않는다. 변경
  대상에서 비즈니스 로직을 더 추가하지 않고 최소 추출을 검토한다.

## 검증

- 변경한 상태 분기마다 성공, unavailable, invalid, timeout, cleanup 실패 테스트를
  선택한다.
- 실제 model/data가 필요 없는 port fake로 orchestration을 먼저 검증한다.
- 실제 backend smoke가 필요하면 명시된 작은 표본만 사용하고, unit test 통과와 runtime
  준비 상태를 구분해 보고한다.
