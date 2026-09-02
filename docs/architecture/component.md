# 컴포넌트 및 모듈 문서 - intelligentAI

프로젝트 단위를 어떻게 분리하고 재사용하는지 기록한다.

## 분리 기준
- 공통 단위:
- 기능 전용 단위:
- Service/Application 단위:
- Repository/Adapter 단위:
- UI 또는 Presentation 단위:

## 계약 규칙
- Props 또는 입력 모델 규칙:
- 출력/result 모델 규칙:
- 재사용 기준:
- 소유 경계:

## 프로필 메모

### FastAPI
- APIRouter는 라우팅과 의존성 연결만 담당한다.
- Service가 FastAPI Response, Depends, Request에 직접 의존하지 않게 한다.
- Repository는 SQLAlchemy, SQLModel 등 실제 저장소 API를 감싸는 경계로 둔다.
- 예외는 도메인/서비스 예외로 먼저 표현하고 controller에서 HTTP 상태로 변환한다.
- 배경 작업, 외부 호출, 긴 작업은 service 하위의 명시적 adapter/client로 분리한다.
