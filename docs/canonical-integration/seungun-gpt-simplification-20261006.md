# Seungun 원본 → GPT 채점·피드백 전환

## 운영 반영 관측 — 2026-10-06 17:13 KST

- RunPod `s8hb5b0l5k4qlx`: AI revision `ef789373aac654ce40ac504104478aa3b8f64c24`, pipeline `canonical-seungun-gpt-20261006-v1` 배포 완료. `/direct/readyz`는 `ready=true`, 직접 접수 중지 파일은 없음.
- AWS: Backend `f0e1f070500e865b6effde266b051a48d0cb2056` JAR와 새 Python verifier를 함께 활성화했다. 양쪽 handoff readiness·9개 schema digest·배포 identity 검사가 통과했고 maintenance를 해제했다. DB 마이그레이션은 없다.
- FE: [PR #62](https://github.com/voice-coaching/VC-FE/pull/62) 병합 revision `dc261398e98ffca930ee4c20ffe88242f525d06d`의 운영 자산을 확인했다. 실제 mono 16 kHz PCM 변환으로 RunPod 입력 계약에 맞춘다.
- AI [PR #26](https://github.com/voice-coaching/intelligentAI/pull/26), BE [PR #110](https://github.com/voice-coaching/VC-BE/pull/110)은 병합 전이며 위 revision을 운영에 직접 반영했다. 후속 문서 commit은 운영 코드 revision과 구분한다.
- 소스 컴파일, Java 21 `bootJar -x test`, FE TypeScript·포맷, 설치 digest·readiness를 확인했다. 실제 녹음 요청, GPT 생성·점수·피드백 품질, 브라우저 QA, 자동 회귀 테스트는 실행하지 않았다. readiness를 실제 생성 성공으로 해석하지 않는다.

운영 bundle: `8379b43819915268f209bdf17ac3ced47f4cbac6d37c57a759ad37bc573800e7`.
AWS JAR SHA-256: `9855c16fbd00d0dbbbba6ad51dd0f6953489d54cfef476e2f5349b4d84cb6093`.
Verifier bundle: `7943d4ccc49247949019fef2dd2599b8be478b638fe46e641bc044c99e03c763`.

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
