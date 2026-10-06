# Seungun 원본 → GPT 채점·피드백 전환

2026-10-06 사용자 결정: 완성된 Seungun 출력과 채점표를 GPT에 전달한다. 서버의 별도 Native 음소 판정·보정 정책과 사전 검산표를 GPT 호출 선행조건으로 사용하지 않는다. 이 결정이 과거 서버 수치 판정/`RUBRIC_RESULT` 강제 명세를 대체한다.

## 실행 구조

`FE → RunPod 접수 → resident Seungun → GPT 한 번의 채점·코칭 → 응답 형식 검사 → 결과 공개`

공개 이후 B2 보관과 AWS 학습 이력 동기화는 기존 비동기 lane에서 실행한다. AWS를 사용자 응답 경로에 다시 추가하지 않는다.

원본은 `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda`다. 기존 Native-only 추론, MFA resident와 준비 최적화는 보존한다. 새 실행 패키지는 `canonical_seungun_core_20261006_v1`, GPT 패키지는 `canonical_seungun_gpt_20261006_v1`이다. 새 코어에는 `native_scoring`, `native_core_adapter`, `nativeScoringEvidence`, `nativeScoringLogits`, 보정 정책을 포함하거나 실행하지 않는다. 과거 결과 읽기용 코드는 과거 식별자에만 연결하고 실행 허용 목록은 새 릴리스로 교체한다.

GPT 입력은 연습문, 전체 원본 `SEUNGUN_OUTPUT`, 사용자 `RUBRIC`, 기존 코칭 후보다. 원본 관측을 서버가 재채점하거나 별도 토큰 대응/프레임 판정으로 변환하지 않는다. GPT가 9항목 단계와 총점을 작성한다. 서버는 JSON 구조, 항목 식별자·순서, 0~4/null 단계, 유한한 0~100 총점, 응답 모델·완료 여부, 코칭 후보 연결을 확인한다. 항목별 판정을 재계산하거나 GPT 점수를 덮어쓰지 않는다.

원래 채점표의 9항목·가중치·단계 경계·합산식은 프롬프트에 보존한다. `phone-rubric-native-v1`은 공개 배점표 식별자로 유지하고 계산 주체 변경은 새 harness 식별자 및 `scoring_authority=GPT_RUBRIC`로 구분한다. 공개 `RUBRIC_COMPUTED`는 이 릴리스에서 GPT가 채점표를 적용한 결과를 뜻하며 서버 검산 통과를 의미하지 않는다.

정상 입력이면 교정 후보가 없더라도 GPT 채점을 호출한다. GPT가 실제 입력 근거 부족으로 UNSCORABLE을 반환해도 코칭을 차단하지 않는다. 모델/API/출력 오류 시 기존 근거 기반 코칭 fallback과 사유는 유지하고 임의 점수는 만들지 않는다. 비정상 오디오/분석 시스템 실패까지 정상 관측으로 취급하지 않는다.

## 연결 계약

GPT 모델은 `gpt-6-astra`, Chat Completions JSON mode, reasoning low, store=false, 120초 timeout, 재시도 없음이다. 채점과 코칭을 함께 반환하므로 출력 한도는 3000 tokens다. JSON mode 자체가 스키마 준수를 보장하지 않으므로 파싱 후 계약 검사는 유지한다. [OpenAI Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs).

FE/BE 공개 점수 필드와 기존 이력은 보존한다. Backend는 새 core/harness/lock/prompt 식별자를 추가 수용한다. Backend 검증기는 GPT 점수를 다시 계산하지 않는다. 구형 식별자 지원은 과거 보관 결과 읽기 호환성이며 구형 모델로의 실행 fallback이 아니다.

## 검증 범위

소스 컴파일, JSON 스키마 정적 확인, 패키지 digest/등록 확인, 운영 readiness 확인을 수행한다. 저장소 지침에 따라 자동 회귀 테스트와 브라우저 QA는 실행하지 않는다. 실제 음성·GPT 생성 및 피드백 품질은 별도 개발자 QA이며 정적 확인을 생성 성공으로 보고하지 않는다. 배포 관측은 실제 활성화 후 별도로 기록한다.
