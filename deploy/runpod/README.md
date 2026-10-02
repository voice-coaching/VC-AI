# RunPod GPT-6 HTTP 운영 배포

2026-10-02 16:13 KST: [canonical 감시기 Python 분리 수정·재배포](../../docs/canonical-integration/incident-20261002-executor-and-ui.md)를
완료했다. 부팅/lane 초기화에서 실제 감시기의 pidfd·subreaper 지원을 확인한다.
기존 frozen core Python과 계약은 유지하며 실제 추론 E2E는 미수행이다.

2026-10-02 11:39~11:41 KST: [서버 배포·제한 v4 활성](../../docs/canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화)을
확인했다. RunPod 실행기·부팅·writer와 Backend reader·검증·journal/apply가 연결됐고
양쪽 v4 admission은 ON이다. 기존 헤더 없는 API는 legacy를 유지한다. 실제 추론·저장·
callback E2E/품질 QA와 FE 테스트는 미수행이다. 아래 과거 미배포 기록과 구분한다.

현재 운영은 **HTTP 분석 API + Seungun CUDA + 영상 입술 관찰 + 외부 GPT-6 API**다. Clova 서버·INT8 모델 기동·Redis worker 설정은 현재 운영 절차가 아니다. [옛 Clova 절차](README.legacy-clova.md)는 이력으로만 보존한다.

2026-09-30 후속 배포로 **분석·한국어 TTS 공용 8082 HTTPS 게이트웨이**가 추가됐다. 현재 Pod 시작 명령은 `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`이며 아래 기존 분석 boot를 내부에서 호출한다. [공개 경로·실제 확인 결과](../../docs/runpod_api_gateway_20260930.md), [gateway 운영 구성](api-gateway/README.md)을 우선 확인한다. 기존 분석 8080은 유지한다.

상태·revision·파일 hash는 [현재 배포 파이프라인](../../docs/runpod-current-pipeline.md), 자동 배포는 [GitHub Actions 안내](GITHUB_ACTIONS.md), 대상 확인은 [운영 안내](../../.codex/runpod.md)를 따른다. 이 문서는 조사한 배포 코드 기준이며 로컬 checkout과 실행 파일이 같다고 가정하지 않는다.

## 실행 구조

1. Pod 시작 명령은 `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`다. 별도 gateway/TTS supervisor를 시작하고 기존 `int8-overlay/deploy/runpod/boot.sh`를 분석 기동 경로로 호출한다. 분석 boot는 SSH를 유지하고 migration marker·시스템 의존성을 확인한다.
2. `prepare_runtime.py`가 base release + 영구 overlay + 검증된 runtime-assets로 `/opt/intelligentai/app`을 구성한다.
3. Python 환경은 `api/g2pk/scorer`를 복사한다. HTTP wheel cache는 `/workspace/intelligentai/deployment/http-dependencies-v1_1`, 실행 의존성은 `/opt/intelligentai/http-python`이다.
4. 기본 모드는 `runpod_http`다. `prepare_http.py`가 실행 env·HTTP attestation을 구성한다.
   `prepare_canonical.py`에서 제한 writer 암호문 복원, 전용 Python/동결 파일/private ACL을
   확인한다. canonical 준비 실패는 legacy 기동 실패로 바꾸지 않는다. 실제 canonical
   접수는 별도 Backend readiness monitor와 계약 SHA 일치가 있어야 열린다.
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

<a id="canonical-callback-v4-근거-보관-설정-미활성"></a>

## Canonical callback v4 근거 보관 설정

최신 운영 변경은 [2026-10-02 RunPod 배포 기록](../../docs/canonical-integration/rollout-execution-20261002.md)을 따른다.
전체 실행기·기존 boot→prepare_http→prepare_canonical 연결과 제한 writer 키 runtime
주입을 배포했다. 전용 Python/의존성 lock·동결 파일·private ACL 준비가 성공했고
legacy 분석과 TTS readiness를 확인했다. 후속 Backend reader·검증·journal/apply 활성화와
계약 9개 SHA 대조를 거쳐 양쪽 v4 접수는 ON이다. `/health/canonical`은 분석 Bearer로
8082 gateway에서도 제공한다. 설정·접수 준비와 실제 E2E 성공을 구분한다.
FE 테스트는 FE 개발자가 수행한다. 아래 미주입/미연결 문장은 과거 관측 이력이다.

현재 writer 지속 사본은 `/workspace/intelligentai/deployment/private/canonical-writer-v1.enc.json`
암호문이고 실행 env는 `/run/intelligentai/analysis-http.env` root:0600이다. 기존 private
callback token으로 복호화하므로 token 교체/Pod 이전 시 재암호화가 필요하다. 제한 writer와
Backend reader 만료는 2026-12-30이다. Pod 재생성 복원 시험은 수행하지 않았다.
Backend는 root:0600 `/etc/alpha-canonical-secrets/backend-runtime.env`를 systemd로 읽는다.
원본 보존은 INDEFINITE, journal 예산은 256 MiB다. 용량 부족 시 신규 접수를 닫으며
자동 삭제로 해소하지 않는다. 새 admission 중단 시 기존 reader·apply·ACK는 유지한다.

2026-10-01 후속 사용자 승인으로 [PostgreSQL journal](../../docs/canonical-integration/backend-journal.md)을 운영 방향으로 변경했다. 현 Pod의 FUSE/overlay에 영구 SQLite를 두지 않는다. B2 원본 정본은 유지하며 RunPod는 private scratch와 내부 HTTP client를 사용한다. V35·journal/apply 설정·DB spool 용량과 전체 factory/boot 연결이 선행돼야 한다. 로컬 구현/컴파일 단계이며 이 문서 편집으로 운영 키를 주입하거나 배포·활성화하지 않았다.

키 발급·주입과 전체 서비스 전환 순서는 [2026-10-01 운영 배포 계획](../../docs/canonical-integration/production-rollout-plan.md)을 따른다. 역할별 제한 키, source/runtime 비밀 분리, 개발자 수용 및 단계별 gate를 정의한 계획이며 아직 운영에 적용하지 않았다.

후속 요청으로 전체 통합·배포와 v4 접수의 **사용자 승인은 확보**했다. 2026-09-30 23:28 KST 재확인에서 현재 배포는 여전히 `32d98e3`의 legacy 경로이며, 아래 B2/canonical 설정은 확인한 로컬·Pod 설정 경로에 없었다. 접수 승인을 기술 readiness로 대체하지 않으며 [재확인 및 배포 조건](../../docs/canonical-integration/callback-v4-plan.md#15-접수-승인과-배포-전-재확인)을 따른다.

이후 AWS Backend·DB 직접 배포까지 명시 승인됐다. 2026-10-01 00:02:49 KST에는 사용자가 제공한 키 쌍으로 B2 Native 인증 및 버킷 메타데이터 조회에 성공했다. 이전 key ID 부재로 인한 인증 차단은 해소됐다. 관리용 비밀값을 그대로 S3 실행 키로 사용하거나 docs에 기록하지 않는다.

2026-09-30 사용자 결정: **canonical 원본 근거 보존기간과 취소·회원탈퇴 후 물리 삭제 유예기간은 무기한**이다. 자동 삭제를 예약하지 않는다. 취소·탈퇴 즉시 공개 조회와 실행 권한을 차단하는 책임은 그대로이며, 비공개 보관이 계속된다고 접근 권한이 유지되는 것은 아니다. 이 결정은 기존 녹음·영상 삭제 정책을 바꾸지 않는다. 미확정 orphan 객체도 별도 정리 승인 전에는 자동 삭제하지 않는다.

아래는 신규 [보관 어댑터](../../src/voice_coach/backend_analysis/canonical_evidence_store.py)의 필수 설정이다. **현재 운영 env에 주입하거나 저장소에 업로드한 기록이 아니다.** docs에 설정을 적는 것만으로 원본 근거가 영구 보존되지 않으며, RunPod 디스크·docs 자체를 B2 대신 사용하지 않는다.

| 설정 (`AI_CANONICAL_EVIDENCE_` 접두사) | 값/상태 |
|---|---|
| `B2_ENDPOINT`, `B2_REGION` | 인증 응답에서 확인: `https://s3.us-east-005.backblazeb2.com`, `us-east-005` |
| `B2_BUCKET`, `B2_PREFIX` | 비공개 `speakai2026` 확인. canonical 전용 `canonical-evidence/v1/`로 분리하는 구성안이며 아직 객체 업로드·운영 주입은 하지 않음 |
| `B2_KEY_ID`, `B2_APPLICATION_KEY` | 제한된 전용 S3 application key를 비밀 공급원으로만 주입. docs·manifest·로그에 값 기록 금지 |
| `RETENTION_DAYS` | `INDEFINITE` — 원본 core/bridge/selection/binding/association 자동 만료 없음 |
| `DELETION_GRACE_HOURS` | `INDEFINITE` — 취소·회원탈퇴 후 자동 물리 삭제 없음 |
| `ORPHAN_GRACE_HOURS` | `INDEFINITE` — 미확정 업로드 자동 정리 비활성; 별도 정리 결정 필요 |

빈 값이나 `0`을 무기한의 별칭으로 쓰지 않는다. `INDEFINITE`를 명시하며 유한값 지원은 후속 정책 변경을 위한 파서 기능이지 현 운영 정책이 아니다. B2에 기존 lifecycle/version 정리 규칙이 있으면 실제 객체가 먼저 삭제될 수 있으므로 활성화 전에 별도로 대조해야 한다. 어댑터는 bucket ACL·정확한 versionId·크기·SHA-256·AES256을 확인하지만 bucket lifecycle/권한을 변경하지 않는다. [B2 Put Object](https://www.backblaze.com/apidocs/s3-put-object), [Get Object](https://www.backblaze.com/apidocs/s3-get-object)

위 B2 관측에서 `bucketType=allPrivate`, `lifecycleRules=[]`, 기본 암호화 `SSE-B2/AES256`을 확인했다. Object Lock은 비활성이므로 무기한 보존 결정이 서버 강제 WORM을 뜻하지 않는다. 기존 버킷 정책은 변경하지 않았다. 2026-10-01 03:19 UTC에 해당 bucket/prefix 제한 writer·Backend reader 키를 발급하고 03:20 UTC에 각각 Native 인증·범위·만료를 대조했다. AWS root 전용 공급 사본에 보관했으며 **runtime 주입·S3 동작 수용은 아직 미완료**다. [실행 기록](../../docs/canonical-integration/rollout-execution-20261001.md)에 비밀 제외 메타데이터를 남겼다. [비밀값 없는 설정 예시](canonical-evidence.env.example)는 준비용이고 운영 env나 readiness 증거가 아니다.

비공개 객체 원본 한도는 개별 16 MiB이며 callback은 1 MiB다. 업로드 후 같은 versionId로 다시 읽어 확인하더라도 Backend의 `VERIFIED` receipt를 대신하지 않는다. 제한시간·lease를 포함한 worker 연결, Backend 검증 worker/DB, 전체 계약 일치와 담당자 E2E가 준비되기 전에는 v4 접수를 열지 않는다. 현재 어댑터는 운영 factory와 연결되지 않았으며 기존 분석·TTS·8082에는 적용하지 않았다. 진행 범위는 [v4 구현 현황](../../docs/canonical-integration/callback-v4-plan.md#14-구현-진행-현황)을 따른다.

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
