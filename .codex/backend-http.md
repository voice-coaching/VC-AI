# Backend–RunPod HTTP 작업 안내

작업 경계는 [AGENTS.md](../AGENTS.md), 상세 계약과 설계 제안은 HTTP 수정 계획 (`runpod_http_callback_revision_plan_20260910.md`, ?? checkout?? ??? ?? ??)을 따른다. 이 안내는 운영 API 구현 완료 기록이 아니다.

2026-09-28 실제 배포는 HTTP + GPT-only 코칭이다. [배포 관측](../docs/runpod_deployed_pipeline_20260928.md)의 제어 v1.1·완료 coaching 결과 v3·null 점수와 실제 Pod 코드를 우선 대조한다. 로컬 HEAD가 배포보다 오래될 수 있으며 아래 로컬 파일만으로 현재 provider를 판단하지 않는다.

## 시작할 때

1. 평가·계획·구현·배포 요청을 구분하고 두 저장소의 remote·대상 브랜치·HEAD·미커밋 변경을 확인한다. 원격 브랜치 검토가 필요하면 fetch한 SHA를 기록한다.
2. 계획의 검토 SHA와 현재 소스 차이를 읽는다. 로컬 sibling clone의 이름만으로 편집 대상을 결정하지 않는다. `.git` 아래 과거 검토 snapshot은 편집·PR 대상이 아니다.
3. 사용자 결정은 유지하고, 실행 세대·claim·heartbeat·슬롯·deadline 등 구체적인 방식은 계획의 제안 및 실제 구현 상태로 판단한다.
4. 평가에서는 불일치·영향·수정 위치를, 계획에서는 변경안과 미구현 항목을 작성한다. 구현에서는 양쪽 계약·공통 코어·시작 조건을 함께 반영한다.

## 소스 대조 순서

| 대상 | 진입점 | 확인 내용 |
|---|---|---|
| VC-BE | `AnalysisJobPublisher`, `DisabledAnalysisJobPublisher`, `AnalysisCapabilitiesReaderImpl` | 선택한 전송 방식과 admission |
| VC-BE | outbox dispatcher, timeout sweeper, cancellation dispatcher | 전송 설정에 작업 수명 기능이 묶이는지 |
| VC-BE | `AnalysisWorkerResult`, `AnalysisResultIngestionService`, `SecurityConfig` | 결과 의미·중복/늦은 실행 처리·저장·서버 인증 |
| intelligentAI | [domain.py](../src/voice_coach/backend_analysis/domain.py), [ports.py](../src/voice_coach/backend_analysis/ports.py), [service.py](../src/voice_coach/backend_analysis/service.py) | HTTP 입력과 공통 분석 입력의 분리 |
| intelligentAI | 배포된 `production.py`, `coaching_evidence.py`, `coaching.py`, `coaching_generator.py`, `expert_examples.py` | Seungun 한 번의 전체 결과 → 최대 3개 후보·입술 관찰 → GPT 코칭. 과거 `grounded_feedback.py`는 현재 조립 경로가 아님 |
| 입력 파일 | [s3_object_store.py](../src/voice_coach/backend_analysis/adapters/s3_object_store.py) | 역할별 저장소 설정·조건부 읽기·크기/MIME/SHA 검증·정리 |
| 배포 | 배포된 `prepare_http.py`, `deployment_attestation.py`, `services.http.json` | GPT 필수 설정·artifact attestation·api 단독 supervisor 구성. 로컬 파일의 revision 차이 확인 |

기존 Redis parser가 요구하는 grant를 가짜 값으로 채우지 않는다. HTTP 전용 입력과 공통 코어를 분리하면서 기존 모델·자산·배포 revision 검증을 유지한다. 과거 Redis/HMAC 복구 설명은 [기존 배포 안내](../deploy/runpod/README.md)의 해당 절에만 적용한다.

## 계약 변경과 인계

API 경로·DTO·토큰 이름·상태 전이·재전송·취소 규칙은 루트 계획과 실제 양쪽 소스에서 대조한다. 이 안내에 상세 계약을 복제하지 않는다. 제안 변수를 env에 추가하는 것만으로 전송 방식이나 worker가 바뀌지는 않는다.

입력·결과의 같은 시도와 선택 음소 연결, 문제 없음 결과, 미제공 값의 의미를 보존한다. 공개 사용자 API도 바뀌면 내부 전송 변경과 함께 명시한다. 사용자 녹음 저장소가 연구자료 B2와 연결됐다고 추정하지 않는다.

[env 안내](env.md)로 역할별 설정 주입을 확인한다. RunPod에 배포하는 작업은 [운영 안내](runpod.md)를 따른다. 코드 작업에 원격 기동이 필요하지 않으면 이를 추가하지 않는다.

변경한 양쪽 SHA와 계약, 필요한 DB migration·설정·시작 조건, 확인한 범위, 미완료 연결, Backend PR와 RunPod 배포 상태를 인계한다. 세션·삭제·재시도 의미와 기존 저장·캐시·정리 흐름까지 대조하고, 구현·health 확인을 전체 요청 통과로 보고하지 않는다.
