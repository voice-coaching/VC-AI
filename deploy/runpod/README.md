# RunPod GPT-6 HTTP 운영 배포

현재 운영은 **HTTP 분석 API + Seungun CUDA + 영상 입술 관찰 + 외부 GPT-6 API**다. Clova 서버·INT8 모델 기동·Redis worker 설정은 현재 운영 절차가 아니다. [옛 Clova 절차](README.legacy-clova.md)는 이력으로만 보존한다.

2026-09-30 후속 배포로 **분석·한국어 TTS 공용 8082 HTTPS 게이트웨이**가 추가됐다. 현재 Pod 시작 명령은 `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`이며 아래 기존 분석 boot를 내부에서 호출한다. [공개 경로·실제 확인 결과](../../docs/runpod_api_gateway_20260930.md), [gateway 운영 구성](api-gateway/README.md)을 우선 확인한다. 기존 분석 8080은 유지한다.

상태·revision·파일 hash는 [현재 배포 파이프라인](../../docs/runpod-current-pipeline.md), 자동 배포는 [GitHub Actions 안내](GITHUB_ACTIONS.md), 대상 확인은 [운영 안내](../../.codex/runpod.md)를 따른다. 이 문서는 조사한 배포 코드 기준이며 로컬 checkout과 실행 파일이 같다고 가정하지 않는다.

## 실행 구조

1. Pod 시작 명령은 `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`다. 별도 gateway/TTS supervisor를 시작하고 기존 `int8-overlay/deploy/runpod/boot.sh`를 분석 기동 경로로 호출한다. 분석 boot는 SSH를 유지하고 migration marker·시스템 의존성을 확인한다.
2. `prepare_runtime.py`가 base release + 영구 overlay + 검증된 runtime-assets로 `/opt/intelligentai/app`을 구성한다.
3. Python 환경은 `api/g2pk/scorer`를 복사한다. HTTP wheel cache는 `/workspace/intelligentai/deployment/http-dependencies-v1_1`, 실행 의존성은 `/opt/intelligentai/http-python`이다.
4. 기본 모드는 `runpod_http`다. `prepare_http.py`가 실행 env·HTTP attestation을 구성한다.
5. `run_services.sh` → `supervise_services.py` → `services.http.json`의 api 하나가 실행된다. UID/GID 10001, scorer Python, dependsOn=[]이며 listener는 `0.0.0.0:8080`이다.

`int8-overlay`·migration marker는 과거 이름을 유지한 경로다. GPT는 외부 API이며 Clova 환경·가중치 적재는 현재 준비 과정에 없다. 남아 있는 redis_stream/disabled 분기를 현재 지원되는 운영 대안으로 해석하지 않는다.

## 설정과 비밀 복원

비밀 공급원은 `RUNPOD_ANALYSIS_HTTP_ENV_JSON` 또는 root 전용 `/root/.config/intelligentai/analysis-http.env`다. JSON이 있으면 source 파일을 덮어쓰므로 지속 공급원과 source의 일관성을 확인한다. 준비 단계가 `/run/intelligentai/analysis-http.env`를 생성한다.

| 역할 | 설정 |
|---|---|
| 전송·코칭 | AI_ANALYSIS_TRANSPORT=runpod_http, AI_ANALYSIS_COACHING_ENABLED=true, AI_ANALYSIS_COACHING_RESULT_CONTRACT=v3 |
| GPT | AI_ANALYSIS_COACHING_PROVIDER=OPENAI, AI_ANALYSIS_COACHING_OPENAI_MODEL=gpt-6-astra, OPENAI_API_KEY |
| 양방향 인증 | 서로 다른 AI_ANALYSIS_API_TOKEN / AI_ANALYSIS_CALLBACK_TOKEN, AI_ANALYSIS_CALLBACK_BASE_URL |
| 녹음 저장소 | AI_ANALYSIS_OBJECT_STORAGE_BUCKET / REGION / RECORDINGS_PREFIX, 필요한 endpoint·SDK credential |
| 읽기 준비 확인 | AI_ANALYSIS_READINESS_OBJECT_KEY / OBJECT_SHA256 / OBJECT_SIZE_BYTES; 작은 비사용자 객체 |
| 모델·영상 | AI_ANALYSIS_SEUNGUN_DEVICE=cuda, AI_ANALYSIS_VISUAL_RELEASE_ROOT, AI_ANALYSIS_VISUAL_GEOMETRY_ARTIFACT_PATH |

실제 parser와 배포 자산 manifest가 필수 조건의 정본이다. 위 표는 비밀값이나 모든 선택 설정을 담지 않는다. 현재 GPT 기본 한도는 timeout 120초·출력 1200·현재 시도 입력 예산 16000이며 전문가 bank 예약분은 별도다.

현재 준비 코드는 AI_ANALYSIS_CLOVA_*를 제거하고 OpenAI 설정을 필수 검증한다. AUTHORIZATION/CONSENT/REDIS 관련 공급원 키는 거부한다. 옛 Redis/HMAC/grant·Clova 설정 묶음을 HTTP 공급원에 복원하지 않는다. Backend 로그인·녹음 소유 관계, 양방향 서버 인증, 파일 무결성 검증은 유지한다.

`/root`와 `/run` 사본만으로 컨테이너 재생성 후 복원이 보장되지 않는다. 공유 `/workspace`·소스·일반 복구 archive에 env·개인키를 넣지 않는다. B2 연구자료 키는 녹음 저장소 자격증명을 대신하지 않는다.

## 상태 확인

| 근거 | 의미 |
|---|---|
| GET /health | 프로세스 alive; GPT 생성·업무 준비 보장 아님 |
| GET /readyz | executor·Backend 계약·저장소·OpenAI 모델 접근을 포함한 준비 상태 |
| 인증 GET /health/services | status/executorReady/reasonCode/provider/model/workerRevision |
| /run/intelligentai/services.json | supervisor의 서비스 PID·ready·restartCount |
| /run/intelligentai/logs/analysis-http.log | 파일 10 MiB, 이전 회전본 최대 3개 |
| /opt/intelligentai/deployment.json | sourceRevision·overlaySha256·baseRelease |
| 영구 overlay와 실행 파일 SHA | 실제 조사 파일의 일치 여부 |

OpenAI 모델 접근 성공은 300초 캐시한다. readiness 200은 실제 생성·발음 품질 QA 통과가 아니다. supervisor의 alive 검사만으로 전체 readiness를 판단하지 않는다. 설정 실패 시 진단 listener가 살아 있어도 작업 접수는 차단된다.

요청 계약은 [HTTP API 명세](../../docs/api/specification.md), 결과 버전은 [계약 안내](../../docs/contracts/README.md)를 따른다. 단일 실행 슬롯이므로 겹치는 새 작업은 429다. 과거 Clova 추론 서버의 503/504 정책을 이 API에 그대로 적용하지 않는다.

## 수정 배포 순서

다음은 **별도 배포 승인이 있는 작업**의 절차다. 문서 조사에서는 실행하지 않는다.

1. 관리 API로 현재 Pod 상태·SSH mapping을 조회하고 신뢰된 host key로 접속한다. 현재 revision·실제 파일 차이·boot/manager/supervisor 부모 관계와 자동 배포 pending 상태를 확인한다.
2. 변경 범위와 복구할 overlay·manifest·파일을 확보한다. 비밀 설정은 별도 안전한 공급원으로 유지한다. 로컬 Git HEAD를 운영 기준 revision으로 임의 지정하지 않는다.
3. [자동 배포](GITHUB_ACTIONS.md)를 기본으로 검토한다. 운영 전용 수정과 변경 파일이 충돌하면 source drift를 해결한 후 진행한다. 부트스트랩·환경·lock 변경은 별도 절차가 필요하다.
4. 수동 적용은 업무 종료와 상위 재시작 루프의 제어를 포함한 유지보수 상태에서만 한다. 자식 API만 종료하면 supervisor가 다시 시작하므로 중지 완료가 아니다. 과거 PID를 재사용하지 않는다.
5. 영구 overlay 파일·각 SHA·40자리 sourceRevision·manifest를 함께 갱신하고 준비 단계를 실행한다. `prepare_runtime.py`는 개별 deletedFiles를 지원하지만 임의 파일 전체 청소 기능은 아니다.
6. 준비 성공 후 기존 boot/manager 구조로 기동한다. runtime/overlay hash, deployment metadata, 인증 health의 revision·provider·model과 전체 readiness를 대조한다.
7. 실패 시 이전 overlay·manifest·관련 실행 파일을 일관되게 복원한다. pending만 지워 실패를 숨기거나 원본 release·/opt만 수정하여 배포 완료로 판단하지 않는다.

Pod 기동 때 Git 최신 커밋을 자동으로 받는 것이 아니라 마지막 영구 overlay를 복원한다. 소스 commit/push, Actions 성공, 실제 runtime 반영은 각각 확인한다. QA는 개발자가 직접 수행한다.

## 현재 운영상 주의점

- 최초 조사에서 발견한 TTS 실행 환경 부재는 후속 배포에서 복원했다. 통합 8082에서 분석·TTS 인증 readiness 200이며, AWS nginx도 2026-09-30 01:12 KST에 8082 HTTPS로 전환했다. 기존 Backend 호출 주소 `https://ai.voice-coaching.site`는 유지했고 SSH 터널은 중지·자동 시작 해제했다. 터널 중지 후에도 기존 도메인의 양쪽 인증 readiness 200을 확인했다. 실제 업무 생성·품질 QA와 구분하며 [후속 배포 결과](../../docs/runpod_api_gateway_20260930.md)를 따른다.
- 취소 처리의 180초 drain 대기와 옛 Clova 주석은 배포 소스에도 남아 있다. 현재 즉시 슬롯 반환으로 안내하지 않는다.
- 입술 결과는 관찰 보조이며 개인 교정·점수 근거로 검증되지 않았다. [영상 개선 계획](../../docs/video-lip-analysis/implementation-plan.md)의 P0/P1은 `32d98e3`으로 구현·배포됐고 P2는 비활성이다. 실제 생성·품질 QA는 미실행이며 [확정 구현·제약](../../docs/video-lip-analysis/p0-p1-deployment.md)을 따른다.
- 로컬 checkout에는 과거 Clova 지원 코드가 남을 수 있다. 문서에서 폐기 처리했다고 런타임 코드 정리나 재배포까지 수행한 것은 아니다.
