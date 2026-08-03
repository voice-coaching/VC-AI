# 디렉토리 문서 - intelligentAI

이 파일은 현재 프로젝트 구조를 설명하는 기준 문서다.

## 필수 작성 항목
- 디렉토리 경로
- 주요 파일 경로
- 책임
- 담당 기능 및 계층
- 중요한 의존성 메모

기능/도메인 폴더를 바깥 경계로 두고, 각 기능 하위의 계층 폴더를 문서화한다.

## 현재 구조
| Path | Type | Responsibility | Owner | Notes |
| --- | --- | --- | --- | --- |
| `src/voice_coach/domain` | directory | DiagnosticProfile과 enum 계약 | core | 외부 I/O 의존 금지 |
| `src/voice_coach/application` | directory | 진단·코칭 use case | core | HTTP 객체 의존 금지 |
| `src/voice_coach/coaching` | directory | 우선순위와 advice allowlist | coaching | evidence 없는 조언 금지 |
| `src/voice_coach/api` | directory | FastAPI route와 계약 연결 | api | 비즈니스 판단 금지 |
| `tests/unit` | directory | 순수 계약·정책 테스트 | core | 빠른 단위 테스트 |
| `tests/integration` | directory | HTTP 계약 테스트 | api | 현재 DB 없음 |
| `scripts` | directory | 데이터 준비와 benchmark 진입점 | data/ml | import 부수효과 금지 |
| `configs/data` | directory | 데이터 join·split·privacy 정책 | data | restricted 경로 사용 |
| `configs/experiments` | directory | 고성능 GPU 실험 gate | ml | GB10 기준 |
