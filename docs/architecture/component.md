# 컴포넌트 및 모듈 문서 - intelligentAI

프로젝트 단위를 어떻게 분리하고 재사용하는지 기록한다.

## 분리 기준
- 공통 단위: `voice_coach.domain`의 엄격한 JSON 계약
- 기능 전용 단위: `coaching`, 향후 `features`, `models`, `references`
- Service/Application 단위: `application.oracle` 같은 use case 조합
- Repository/Adapter 단위: 데이터·모델 I/O 구현 시 추가
- UI 또는 Presentation 단위: `voice_coach.api`

## 계약 규칙
- 입력 모델 규칙: Pydantic extra field 금지, 점수 0–1, UTF-8
- 출력/result 모델 규칙: camelCase JSON, `schemaVersion` 필수
- 재사용 기준: HTTP와 무관한 정책만 domain/application에서 재사용
- 소유 경계: coaching은 허용된 advice ID와 우선순위 정책을 소유

## 프로필 메모

### Python
- import 시점에 파일 생성, 네트워크 호출, 프로세스 실행 같은 부수효과가 발생하지 않게 한다.
- 실행 가능한 모듈은 `main()` 진입점을 제공하고 직접 실행 코드는 `if __name__ == "__main__"` 아래에 둔다.
- 표준 라이브러리로 충분한 문제에 불필요한 dependency를 추가하지 않는다.
- 공개 함수와 클래스에는 의미 있는 타입 힌트를 유지하고, 입력/출력 모델은 명확한 자료 구조로 표현한다.
- 순수 계산 로직과 I/O 로직을 분리해 테스트와 재사용이 가능하게 한다.

### FastAPI
- APIRouter는 라우팅과 의존성 연결만 담당한다.
- Service가 FastAPI Response, Depends, Request에 직접 의존하지 않게 한다.
- Repository는 SQLAlchemy, SQLModel 등 실제 저장소 API를 감싸는 경계로 둔다.
- 예외는 도메인/서비스 예외로 먼저 표현하고 controller에서 HTTP 상태로 변환한다.
- 배경 작업, 외부 호출, 긴 작업은 service 하위의 명시적 adapter/client로 분리한다.
