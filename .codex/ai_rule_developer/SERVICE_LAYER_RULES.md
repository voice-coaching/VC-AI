# 서비스·유스케이스 규칙 — intelligentAI

service/application은 유스케이스 검증, 근거 정책, 상태 전이, 외부 port 조합을 담당한다. 탐색 진입점은 `backend_analysis/http_runtime.py`, `direct_analysis/service.py`, `pronunciation_feedback/pipeline.py`, `visual_feedback/services`다. 기존 구현 전체가 이상적 분리를 달성했다는 뜻은 아니다.

- route는 요청 파싱·인증 경계·응답/예외 변환을 담당한다. 분석 판단·저장·모델 호출을 추가하지 않는다.
- 순수 domain/service가 FastAPI Request/Response/Depends에 직접 의존하지 않게 한다. 기존 runtime orchestration의 transport 경계는 먼저 확인한다.
- client/adapter/store는 constructor·factory·좁은 모듈 경계로 연결한다. 형식만 맞추는 ORM repository를 만들지 않는다.
- 함수는 하나의 use case나 단계를 표현한다. 줄 수보다 검증·판단·side effect 책임을 기준으로 helper를 나눈다.
- 입력 검증 → 현재 상태/세대 확인 → 근거 기반 판단 → 명시 side effect → 계약 결과 변환 흐름을 드러낸다.
- claim·deadline·heartbeat·취소·슬롯·멱등성·재시작 의미를 해당 경로의 계약대로 유지한다. 전송 재시도에 모델/GPT를 다시 실행하지 않는다.
- public 결과와 private 원본·내부 오류·저장소 key를 분리한다. 누락값을 성공·0점·문제 없음으로 대체하지 않는다.
- fallback은 해당 경로에 정의된 근거·상태·사유로만 제공한다. legacy deterministic fallback을 모든 canonical 실패에 일반화하지 않는다.

변경한 실패·취소·재전송·보관 영향을 문서에 남기고 개발자 QA와 정적 확인을 구분한다. 자동 회귀 테스트를 실행·생성하지 않는다.
