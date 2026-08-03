# 아키텍처 문서 - intelligentAI

## 요약
한국어 학습자의 발음을 진단하고 근거 기반 코칭과 기준 음성을 제공하며 재녹음 전후를 비교하는 폐쇄루프 AI 보이스 코칭 연구 프로젝트

## 기본 정보
- 스택: Python, FastAPI
- 데이터베이스: 미지정
- 인증 사용: 미사용
- 외부 API 연동: 사용

## 계층 방향

현재 의존성 방향은 다음과 같다.

```text
api -> application -> coaching/domain
coaching -> domain
domain -> Python/Pydantic
```

- `domain`은 FastAPI, 파일 시스템, 모델 runtime을 import하지 않는다.
- `application`은 use case를 조합하고 HTTP 객체를 알지 못한다.
- `api`는 입력 검증과 응답 변환만 담당한다.
- 향후 음성·모델 I/O는 adapter 경계에 추가한다.

## 프로필별 아키텍처 메모

### Python
- 패키지는 기능 또는 도메인 책임 기준으로 나누고 각 기능 폴더 하위에 service, adapter, model 등을 둔다.
- 공통 package는 여러 기능이 공유하는 정책, 설정, utility에만 사용한다.
- CLI 또는 실행 진입점은 입력 파싱과 출력 변환만 담당하고 실제 판단은 service/application 계층에 위임한다.
- 핵심 도메인 함수와 클래스는 파일 시스템, 네트워크, 환경 변수, process args에 직접 의존하지 않게 한다.
- 파일, DB, 외부 API, subprocess 연동은 adapter 또는 infrastructure 모듈 뒤에 둔다.
- 권장 흐름: Entrypoint/CLI -> Application Service -> Domain/Adapter -> External System.

### FastAPI
- 기능 경계는 `app/features/<feature>` 아래에 둔다. 예: login, post, talk.
- HTTP 엔드포인트는 각 기능의 controller/router 계층에 두고 비즈니스 판단은 같은 기능의 service 계층으로 위임한다.
- service 계층은 유스케이스 단위 메서드를 제공하고 같은 기능의 repository 또는 외부 연동 client를 조합한다.
- repository 계층은 DB 접근만 담당하며 HTTP 요청/응답 객체를 알지 못해야 한다.
- Pydantic schema는 요청/응답 계약이고 ORM entity는 저장 모델이다. 두 모델은 같은 기능 폴더 안에서도 혼용하지 않는다.
- 공통 설정, DB session, 인증 dependency처럼 여러 기능이 공유하는 요소만 core/shared에 둔다.
- 권장 흐름: Controller -> Service -> Repository -> DB, 단 각 계층은 같은 기능 폴더 안에 둔다.

## 흐름 메모
- 데이터 흐름: AI Hub 원천 → manifest/QC → feature/model → DiagnosticProfile
- 인증 흐름: 현재 연구용 API 인증 없음
- API 흐름: FastAPI route → oracle application service → domain contract
- 저장 흐름: 현재 API 영속 저장 없음; 산출물은 Git 제외 `artifacts/`에 저장
- 외부 연동 흐름: AI Hub와 공개 코퍼스 동기화는 `scripts/`의 별도 프로세스

## 아키텍처 결정
| Date | Decision | Reason | Impact |
| --- | --- | --- | --- |
| 2026-07-28 | GB10 고성능 GPU와 CUDA 13 환경만 사용 | 저사양 GPU 운영 종료 | VRAM 대신 통합 메모리와 OOM을 benchmark |
| 2026-07-28 | Oracle endpoint를 실제 모델 endpoint와 분리 | 모델 오류와 계약 오류를 독립 검증 | `/v1/oracle-attempts`는 연구 전용 |
| 2026-07-28 | DB 도입 보류 | learner history 요구가 아직 미확정 | 현재 API는 무상태 |
