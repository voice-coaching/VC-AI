# 아키텍처 문서 - intelligentAI

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

## 요약
한국어 L2 학습자(중국어·일본어 L1)의 발음을 진단하고 근거 기반 코칭과 기준 음성을 제공하며 재녹음 전후를 비교하는 폐쇄루프 AI 보이스 코칭 연구 프로젝트. 주 산출물은 AI pipeline이다. T43/I30 인간 검증에는 제한 음성을 외부로 내보내지 않는 별도 loopback 서버·GUI를 사용한다.

목표 기능의 계층·gate·API target은
[`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)에
별도 설계로 기록한다. 이 문서는 현재 구현을 완료로 승격하지 않으며, 실제
endpoint와 계약이 추가될 때만 현재 구조 문서와 API 문서를 갱신한다.

정면 고정 영상의 입술 cue를 `seungun` audio evidence와 결합하는 별도 sidecar
target은
[`SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md`](SEUNGUN_VISUAL_PRONUNCIATION_EVIDENCE_PIPELINE_KO.md)에
기록한다. 이 경로는 `design_only/not_implemented`이며 현재 audio detector나
bridge의 구현 완료 상태를 바꾸지 않는다.

## 기본 정보
- 스택: Python 연구용 AI pipeline. FastAPI는 Oracle 계약 검증과 G04 T43/I30 로컬 전문가 검수에 사용하며 학습자 발음 feedback 전달 형태는 아니다.
- 데이터베이스: 미지정
- 인증 사용: 일반 서비스 인증 없음. G04 로컬 검수 API만 실행별 임시 token 사용
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

발음 feedback 경로의 의존성 방향은 다음과 같다.

```text
CLI/연구 코드 -> pronunciation_feedback pipeline -> domain/ports -> adapters -> external(model/fs)
```

`prompt catalog`, `expected phones`, `audio intake`, `content/alignment`,
`S1/S2 scorer`, `threshold artifact`, `advice catalog`는 각각 port로 분리하고
pipeline이 port만 주입받는다. 상세 port 목록과 단계별 구현 상태는
[`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)
§2와 §13에 있다.

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
- API 흐름: FastAPI route → oracle application service → domain contract (Oracle 계약 검증 전용)
- G04 검수 흐름: browser presentation → versioned local API → amended review store → 원자 JSON state
- 발음 feedback 흐름: CLI → pipeline(Stage A0–H) → port → adapter. S1/S2 backend 미연결 구간은 fail-closed
- 저장 흐름: 현재 API 영속 저장 없음; 산출물은 Git 제외 `artifacts/`에 저장
- 외부 연동 흐름: AI Hub와 공개 코퍼스 동기화는 `scripts/`의 별도 프로세스

## 아키텍처 결정
| Date | Decision | Reason | Impact |
| --- | --- | --- | --- |
| 2026-07-28 | GB10 고성능 GPU와 CUDA 13 환경만 사용 | 저사양 GPU 운영 종료 | VRAM 대신 통합 메모리와 OOM을 benchmark |
| 2026-07-28 | Oracle endpoint를 실제 모델 endpoint와 분리 | 모델 오류와 계약 오류를 독립 검증 | `/v1/oracle-attempts`는 연구 전용 |
| 2026-07-28 | DB 도입 보류 | learner history 요구가 아직 미확정 | 현재 API는 무상태 |
| 2026-08-06 | 연습 프롬프트는 서버 카탈로그가 소유 | 사용자와 모델이 같은 문장을 보장해야 하고, 자유 입력 문장에는 threshold·기준 음성 근거가 없음 | `PromptCatalogPort`와 R00 sentence card를 프롬프트 출처로 고정, 자유 입력은 `uncalibrated` profile로 제한 |
| 2026-08-06 | 발음 feedback 경로는 무상태 in-process pipeline + CLI로 전달 | 본 프로젝트 산출물은 AI pipeline이며 백엔드·프론트엔드가 아님 | HTTP endpoint·session store·DB를 도입하지 않고, 재녹음은 `run_attempt` 재호출로 처리 |
| 2026-08-06 | 모델 weight는 각 scorer adapter가 프로세스 수명 동안 상주 | attempt마다 large 모델 재로딩은 연습 루프 지연에 부적합 | scorer adapter가 자기 수명을 소유, branch별 latency 실측을 S1 연결과 동시에 수행 |
| 2026-08-06 | 목표 사용자를 한국어 L2 학습자(중국어·일본어 L1)로 재정의 | 학습·calibration·오류 태그 근거가 모두 이 코호트이고, 아나운서 근거는 사람 발화·판정 없이 확보 불가 | 코호트 불일치 해소. 아나운서 규범 진단은 범위에서 제외하고 별도 WP로 분리 |
| 2026-08-06 | 결과를 Track E(`engineering_provisional`)와 Track R(`research_validated`) 두 등급으로 분리 | 사람 gate를 실행 경로에서 빼야 자율 end-to-end가 가능하고, 동시에 engineering 결과가 검증 결과로 오인되면 안 됨 | Track E는 자율 실행하되 배점 0·주장 상한 고정. Track R 승격은 사람 gate receipt 없이는 코드에서 거부 |
| 2026-08-10 | T43/I30 검수 화면을 package data 프론트엔드와 `/api/v1` 로컬 API로 분리 | profile별 43/30 판정을 오디오·interval 근거와 함께 수행하되 제한 식별자와 경로를 browser에 노출하지 않기 위함 | 기본 loopback/token/CSP, 원자 state, T43·I30 field 분리를 테스트로 고정 |
