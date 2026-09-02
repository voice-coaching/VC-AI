# 아키텍처 문서 - intelligentAI

## 요약
프로젝트 목적, 사용자, 핵심 제품 가치를 여기에 작성한다.

## 기본 정보
- 스택: FastAPI
- 데이터베이스: mysql
- 인증 사용: 미사용
- 외부 API 연동: 사용

## 계층 방향
- 이 프로젝트가 실제로 사용하는 의존성 방향을 기록한다.
- 각 계층이 import할 수 있는 대상과 금지 대상을 설명한다.
- 기능/도메인 폴더를 소유권 경계로 두고 각 기능 하위에 계층 폴더를 둔다.

## 프로필별 아키텍처 메모

### FastAPI
- 기능 경계는 `app/features/<feature>` 아래에 둔다. 예: login, post, talk.
- HTTP 엔드포인트는 각 기능의 controller/router 계층에 두고 비즈니스 판단은 같은 기능의 service 계층으로 위임한다.
- service 계층은 유스케이스 단위 메서드를 제공하고 같은 기능의 repository 또는 외부 연동 client를 조합한다.
- repository 계층은 DB 접근만 담당하며 HTTP 요청/응답 객체를 알지 못해야 한다.
- Pydantic schema는 요청/응답 계약이고 ORM entity는 저장 모델이다. 두 모델은 같은 기능 폴더 안에서도 혼용하지 않는다.
- 공통 설정, DB session, 인증 dependency처럼 여러 기능이 공유하는 요소만 core/shared에 둔다.
- 권장 흐름: Controller -> Service -> Repository -> DB, 단 각 계층은 같은 기능 폴더 안에 둔다.

## 흐름 메모
- 데이터 흐름:
- 인증 흐름:
- API 흐름:
- 저장 흐름:
- 외부 연동 흐름:

## 아키텍처 결정
| Date | Decision | Reason | Impact |
| --- | --- | --- | --- |
