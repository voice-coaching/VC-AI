# 2026-10-02 Backend·RunPod 운영 배포 — 제한 v4 접수 활성

**최신 관측: 11:39~11:41 KST, Backend와 RunPod의 v4 접수 모두 ON.**
실행기·부팅·역할별 키, Backend reader·검증·journal/apply를 운영 반영했다.
헤더 없는 기존 API는 legacy이며 신규 v4는 명시 opt-in 단독 음성 발음 학습에 한정한다.
실제 추론/GPT/근거 저장/callback E2E·FE 테스트·품질 QA 통과를 뜻하지 않는다.
최신 변경은 [제한 v4 접수 활성화](#제한-v4-접수-활성화)를 따른다.
이후 해당 절 전까지의 내용은 00:24 KST Backend D1 시점 기록이다.
시각은 별도 표시가 없으면 KST다. 배포 후 관측은 2026-10-02 00:24 KST
(2026-10-01 15:24 UTC)이며, 문서 편집 시각을 새 관측으로 간주하지 않는다.

## 배포 전 차이와 보존

- AWS `13.209.184.226`의 운영 JAR는 이전 조사 이후 변경됐다. 실제 파일은
  `releases/b410076071c0f0e93e6103872e54082805a5b7da.jar`와 SHA가 동일했다.
  공개 `origin/develop`도 이 SHA였으며 최신 시험 API와 예시문제 seed 변경을 포함한다.
- 운영 DB의 V32는 이미 `seed example question contents`로 적용됐다. 이전 canonical
  **미적용** V32~V35 초안을 새 작업본에서 V33~V36으로 옮겼다. 운영 V0~V32 및
  Flyway 이력/checksum을 수정하거나 repair하지 않았다.
- 기존 dirty 작업본 `canonical-v4-be-20260930`은 보존했다. 새 배포 작업본은
  `C:/Users/Public/Documents/ESTsoft/CreatorTemp/canonical-v4-be-rollout-20261001`,
  브랜치는 `feat/canonical-v4-rollout-20261001`, base는 위 `b410076…`이다.
  예전 작업본의 JAR를 그대로 재배포하면 최신 변경과 migration 번호가 충돌한다.
- RunPod 소스·overlay, frozen R/S7, 기존 TTS 설정과 역할별 키는 변경하지 않았다.

## 실제 배포 식별자

| 항목 | 실제 값 |
|---|---|
| 새 Backend JAR SHA-256 | `86c2aa795348c716ff0319b9d014089620ae11505db703b55ee2890c63ea9f65` |
| JAR 경로 | `/opt/alpha/releases/canonical-v4-86c2aa795348c716ff0319b9d014089620ae11505db703b55ee2890c63ea9f65.jar` |
| 기동 경로 | `/opt/alpha/app.jar`가 위 JAR를 가리킴, `alpha-backend.service` active |
| 기존 원복 JAR | `/opt/alpha/releases/b410076071c0f0e93e6103872e54082805a5b7da.jar` |
| 기존 JAR SHA-256 | `d47e0db8b71906f63a41fd62f422447f4f5f9c8dfa2aab10fdedb01d5824773b` |
| 배포 소스 archive | 새 JAR와 같은 경로 stem의 `.source.tar.gz` |
| 소스 archive SHA-256 | `05f9a7c7bedff213f5d7c4816501727bd50025717a1ba7a8ec3398a663323ec9` |
| 적용 migration | V33 registry / V34 receipt / V35 callback·결과·포인터 / V36 journal·ACK·apply queue |

실제 Java/resources를 최신 base에 통합하여 빌드했고 소스·빌드 설정 archive를 AWS에
함께 보관했다. 비밀파일·사용자 데이터·테스트·Git 디렉터리는 archive에 포함하지 않았다.
commit/push/PR은 하지 않았으므로 위 base SHA를 canonical 변경까지 포함한 commit이라고
표현하면 안 된다. 향후 CI/develop 배포에 이 변경을 인계하지 않으면 새 JAR가 교체될 수 있다.

## DB 백업과 복구 경계

DB 서버는 PostgreSQL `180006`, 기존 호스트 `pg_dump`/`pg_restore`는 15.18이었다.
기존 Docker에서 공식 `postgres:18-alpine`을 받아 다음 digest로 고정해 백업 클라이언트만
실행했다. 새 PostgreSQL 서버를 시작하지 않았다.

- image: `postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`
- backup: `/var/backups/alpha-canonical/20261001T151200Z/database.dump`
- SHA-256: `4d52ffd7f6afbc34283d72113369040a584ce811a9d2636462409fb2544fad88`
- 크기: 1,367,213 bytes. 부모 root:0700, 파일 root:0600. 옆 `receipt.json`에 비밀 없는 식별자를 기록했다.
- 단일 DB custom-format dump, `pg_restore --list` 및 SHA 확인. **실제 복원·PITR·클러스터 역할 백업을 검증한 것은 아니다.**

[백업 도구](../../deploy/runpod/backup_backend_database.py)는 기존 서비스 DB 설정을
해당 프로세스에만 주입한다. 첫 실행의 archive 작성/목록 확인 후 Python 3.9에 없는
`hashlib.file_digest` 때문에 receipt가 미작성됐고, 호환 해시 연산으로 수정한 다음
**동일 archive를 재검사해 receipt만 완성**했다. 중복 덤프·삭제·복원은 하지 않았다.
PostgreSQL [백업 도구 문서](https://www.postgresql.org/docs/18/app-pgdump.html)와
[공식 컨테이너 이미지](https://hub.docker.com/_/postgres)를 대조했다.

## 배포 실행과 실제 상태

[이번 D1 전용 배포 도구](../../deploy/runpod/deploy_backend_canonical_disabled.py)는
고정 기존/신규 JAR·백업 SHA, DB V32, 기존 readiness, 유효 worker lease 부재를 확인한 뒤
JAR를 교체하고 서비스를 재시작했다. 새 JAR의 Flyway가 V33~V36을 성공 적용했다.
기동 실패 시 기존 JAR만 원복하며 추가 DB/근거는 삭제하지 않도록 구성했다.
이번에는 health와 migration 결과가 일치하여 원복하지 않았다.

기존 env 파일에 중복 assignment가 있어 초기 사전 검사가 멈췄지만 서비스는 변경되지
않았다. 설정 파일을 고치지 않고 systemd 서비스의 실제 프로세스 환경을 읽어 대조하도록
수정했다. env 원문·토큰·DB 비밀번호는 출력하지 않았다. 서비스 설정 파일은 그대로다.

| 확인 | 관측 결과 | 보장하지 않는 것 |
|---|---|---|
| Backend 관리 health | UP | 업무 E2E/피드백 품질 |
| 인증 worker-readiness | 200 ready | canonical 준비 |
| 인증 worker-readiness/v2 | 503, `legacyReady=true`, `canonicalSupported=false`, `canonicalAdmissionEnabled=false` | v4 활성화 |
| DB migration | V33~V36 success | SQL 장애/경합/복원 QA |
| RunPod 인증 8082 `/health/services` | 200 ready, executorReady=true, OPENAI / gpt-6-astra | 실제 생성 성공 |
| RunPod revision | `32d98e39767b56697bc7e58fb50e3e9d61432349` | canonical 코드 배포 |
| RunPod overlay | `4721ac82c685e006951911bc7c756d91e55fb3285a5b861bc2481a1dde5c35ab` | 새 frozen 실행 경로 연결 |

RunPod `/opt/intelligentai/app`에 `canonical_backend_journal.py`는 아직 설치돼 있지 않다.
Backend의 journal/receipt/callback worker 스위치는 기존 기본값 off다. v4 접수는 단순히
미설정 env 하나의 문제가 아니라 `TrainingAnalysisSchemaAdmissionService`와 v2 readiness의
명시적 차단 및 RunPod 전체 실행 경로 미연결 상태다. 이 차단을 삭제하거나 200으로 꾸미지 않았다.

## 활성화 전에 남은 작업

1. 운영 revision 기준 RunPod의 core→S7→원본 보관→receipt→callback 실행기,
   heartbeat/cancel/자손 회수, factory/boot 및 재시작 복구 연결.
2. writer/reader 역할별 키의 실제 runtime 주입, 최신 독립 검증기·저장소 readiness,
   PostgreSQL spool 용량 및 proxy body/timeout 설정. 키 재발급은 하지 않았다.
3. 제한된 canonical 소비자/FE의 실패·재진입·이력/actions 연결. 기존 header 없는
   FE 요청·공개 DTO·TTS는 변경하지 않는다.
4. 개발자 직접 수용 후 제한 접수 활성화. 원본 영상/기존 segments의 v4 후처리 차단도
   해결해야 한다. 이번 작업에서 영상/녹음 삭제를 수행하지 않았다.

최신 base의 `compileJava processResources bootJar --offline --no-daemon` 성공(44초),
AI/Backend/JAR 공유 schema 7개 동일, 패키지 migration 37개(V0~V36) 번호 중복 없음,
운영 기동·DB 이력·readiness 확인 범위다. 자동 회귀 테스트·fixture·브라우저 자동화·
실제 녹음/GPT·품질 QA는 수행하지 않았다. **사용자의 전체 운영 배포·v4 활성화 요청은
아직 미완료이며, 이번 완료 범위는 접수 off 상태의 Backend/DB 확장이다.**

## RunPod 실행기·부팅·키 배포

사용자의 후속 요청은 RunPod 전체 실행기·부팅 연결 및 운영 키 주입이다.
FE 테스트는 FE 개발자 책임이며 이번 서버 배포의 선행조건으로 삼지 않았다.
2026-10-02 11:00:43 KST 최종 overlay 반영, 11:03~11:07 KST 운영 상태를 확인했다.
Pod 전체 재시작/재생성, GPU 추가 시작, Backend/FE 재배포는 하지 않았다.

### 구현·배포한 경로

- `http_runtime.py`: 기존 단일 슬롯에서 legacy와 canonical을 분기한다. canonical도
  동일 claim/digest·heartbeat·lease·deadline·cancel을 사용한다. 실행 전에 resident
  legacy 모델을 종료해 GPU를 넘기며, guard가 자손 회수를 마쳐야 슬롯을 반환한다.
  회수 확인 불가 시 서비스 접수를 차단한다. 기존 legacy callback v1~v3 직렬화는 유지한다.
- `canonical_lane.py`: private 요청 descriptor와 atomic lease 파일로 독립 Python
  프로세스를 실행한다. 부모 종료·취소·lease 만료 시 lifetime pipe/subreaper로
  전체 자손을 회수한다. 같은 execution 디렉터리는 재사용하지 않는다.
- `canonical_executor.py`: 녹음 무결성 확인 → Backend journal reserve/one-shot start →
  frozen R(Python 3.11) → frozen S7/GPT(Python 3.12, jsonschema 4.25.1) → preflight →
  PostgreSQL 원문 stage → B2 version 고정 저장/readback → Backend VERIFIED receipt →
  고정 callback bytes → Backend APPLIED/DUPLICATE 확인을 연결했다.
  core 없는 실패를 가짜 canonical 판단/legacy 결과로 바꾸지 않는다.
- `canonical_settings.py`: 역할별 환경 allowlist. legacy multiprocessing의 초기 환경과
  전달 환경에서 `AI_CANONICAL_*`를 제외한다. 실제 legacy helper/executor의 해당 환경
  이름이 모두 빈 목록임을 확인했다. core/MFA에는 Backend·GPT·B2 키를 전달하지 않는다.
- `prepare_http.py` → `prepare_canonical.py`: 기존 부팅 경로에서 전용 venv를 복원하고
  SHA-256 lock을 사용해 로컬 wheel cache에서만 설치한다. 서비스 UID 10001로 frozen
  파일·bridge import·설정·B2 private ACL을 확인한 뒤 bootstrap ready를 기록한다.
  canonical 준비 실패는 legacy listener 준비를 실패로 바꾸지 않는다.
- SQLite journal/callback stack은 운영 bundle에 넣지 않았다. retry helper도 해당
  구 stack에서 분리했다. Backend PostgreSQL과 B2가 내구성 경계이며 `/run`은 임시 공간이다.

GPT 모델·요청 형식·동결 system prompt·후보/validator 정책은 바꾸지 않았다.
공식 OpenAI 문서는 기존 Chat Completions/JSON 모드의 유지 여부 확인에만 사용했다.
기존 공개 FE API·TTS 요청/응답·입술 P0/P1은 그대로다. canonical 영상은 여전히
`NOT_CONNECTED`이며 기존 입술 근거를 MFA 후보로 임의 치환하지 않는다.

### 키 주입과 부팅 복원

- 이미 발급한 bucket/prefix 제한 writer를 AWS root 전용 공급원에서 SSH stdin으로
  전달했다. 재발급하지 않았고 Master B2 키나 Backend reader/DB 키를 Pod에 넣지 않았다.
- 대상은 비공개 `speakai2026` / `canonical-evidence/v1/`; 기존 writer 만료는
  2026-12-30이다. 원본·삭제 유예·orphan 보존 설정은 모두 `INDEFINITE`다.
- 지속 공급 사본은
  `/workspace/intelligentai/deployment/private/canonical-writer-v1.enc.json`의
  AES-GCM 암호문이다. 기존 RunPod private HTTP 공급원의 callback token으로 HKDF
  wrapping key를 유도한다. 이 wrapping 비밀을 공유 볼륨에 별도 평문 저장하지 않는다.
  callback token 교체 또는 Pod 이전 시 해당 envelope를 명시적으로 재발급/재암호화해야 한다.
- 부팅 시 root가 복호화해 `/run/intelligentai/analysis-http.env`에 합친다.
  파일은 root:root 0600, canonical scratch는 UID/GID 10001·0700이다.
  전용 venv는 `/opt/intelligentai/envs/canonical`, 비밀 없는 wheel cache는
  `/workspace/intelligentai/deployment/canonical-dependencies-v1`이다.
- 실제 boot 프로세스의 공급 환경으로 준비 스크립트를 실행하고, 복호화·런타임
  주입·서비스 재기동을 확인했다. **Pod 자체 재생성 후 복원 시험을 수행한 것은 아니다.**
  부팅 R/S7·wheel cache·암호문·RunPod HTTP 비밀 공급원을 함께 보존해야 한다.
- B2 관측은 비공개 ACL 조회까지다. 실제 근거 PUT/readback·Backend reader 수용은
  실행 QA를 수행하지 않았으므로 완료로 기록하지 않는다.

### 최종 식별자와 운영 관측

| 항목 | 값 |
|---|---|
| Pod | `s8hb5b0l5k4qlx`, RUNNING, 기존 GPU 1개 유지 |
| Git 기준 revision | `32d98e39767b56697bc7e58fb50e3e9d61432349` |
| 최종 overlay SHA-256 | `87aa04793415572cda6fe708e99302f7cfe7ee8f929acfa942b654c25e573873` |
| 최종 30파일 bundle SHA-256 | `dfc1b12760054308a9063b12fb26e5e4ae864523e8183abd0d38858844d89c99` |
| 원 배포 전 백업 | `/workspace/intelligentai/deployment/canonical-runtime-backup-20261002T015252Z` |
| 최종 보완 직전 백업 | `/workspace/intelligentai/deployment/canonical-runtime-backup-20261002T020039Z` |
| 분석 8082 `/health/services` | 200 ready, executorReady=true, OPENAI / gpt-6-astra |
| 내부 8080 `/health/canonical` | 200, executorConfigured=true, admissionEnabled=false |
| TTS 8082 `/health/ready` | 200 ready, melo-ko-practice-r1 |
| gateway/TTS supervisor | 기존 PID 73, gateway 103 / TTS 104, 둘 다 restarts=0 |
| 공유 계약 | 운영 RunPod의 7개 SHA가 기존 AI/Backend/JAR 계약 SHA와 동일 |
| Backend readiness v2 | 503, legacyReady=true, canonicalSupported=false, admission=false |

새 commit으로 가장하지 않는다. 위 Git 값은 **기준 revision**이고 실제 변경은
overlay/bundle digest로 식별한다. `deployment.json`과 overlay manifest에
`canonicalIntegration` 기록을 추가했고 자동 배포 state의 overlay SHA도 맞췄다.
설치된 30개 파일과 지속 overlay의 바이트를 대조했다.
분리된 작업 트리는
`C:/Users/Public/Documents/ESTsoft/CreatorTemp/canonical-runpod-runtime-20261002`,
브랜치는 `feat/canonical-runpod-runtime-20261002`다. 구현·배포 helper 변경은
기존 intelligentAI 작업본에도 반영했다. commit/push/PR은 하지 않았다.

AWS의 실제 서비스 URL/token으로 curl health를 조회해
`https://ai.voice-coaching.site/health/services`와 `/health/ready` 모두 200을 확인했다.
Python urllib로 같은 외부 경로를 조회하면 403이고, RunPod proxy 직접 조회의 본문은
`error code: 1010`이었다. 클라이언트별 관측을 구분하며 nginx·TLS·포트를 변경하거나
검증을 끄지 않았다. Java 업무 요청/실제 생성까지 확인한 것은 아니다.
신규 canonical 진단 endpoint는 내부 8080만 제공하며 8082 gateway에서는 404다.
기존 gateway allowlist를 확장하지 않았다.

### 남은 서버 활성화 조건

이번 변경은 **RunPod 실행 경로 구현·부팅 연결·writer 주입·접수 off 배포 완료**다.
실제 canonical 요청은 실행하지 않았다. 다음 항목을 FE 테스트와 구분한다.

1. Backend reader 키의 runtime 주입, 독립 verifier 및 journal/receipt/callback/apply
   worker 활성화·readiness 연결, spool/proxy 용량 확인.
2. Backend의 현재 unconditional admission 차단과 RunPod boot의 접수 off를 실제
   서버 readiness에 연결하는 제한 접수 구현. 환경변수 하나만 켜서 우회하지 않는다.
3. 개발자의 실제 core/GPT/B2/receipt/callback·취소/복구 수용. 원문 staging 전에
   Pod를 잃으면 재생성 성공으로 꾸미지 않고 Backend lease/timeout으로 실패 처리한다.
4. 영상/기존 segments가 있는 v4 반영 차단은 유지한다. 미승인 원본 삭제를 하지 않는다.

FE 테스트·UI 수용은 FE 개발자가 수행한다. 이를 서버 구현·키 주입의 차단 사유로
사용하지 않았다. 자동 회귀 테스트, fixture, 브라우저 자동화, 사용자 녹음 분석,
GPT 생성, TTS 합성·품질 QA는 수행하지 않았다. 확인 범위는 컴파일·정적 계약/파일
대조·부팅 준비·비밀 역할 분리·운영 health다.

## 제한 v4 접수 활성화

사용자의 운영 배포·v4 접수 승인에 따라 11:32~11:35 KST 추가 배포했다.
11:39~11:41 KST 인증 health를 재조회했다. 위 OFF 기록은 각 배포 단계의 이력이다.

### 실제 활성 경로와 호환성

- Backend `CanonicalBackendReadiness`가 reader private ACL, 고정 offline verifier 설치,
  V33~V36 테이블 접근, 256 MiB journal 용량과 80 MiB 여유, 네 worker의 최근 DB poll,
  기존 readiness를 확인한다. RunPod의 실제 설정 URL에서 인증 `/health/canonical`을
  조회해 executor 준비 및 공유 계약 9개의 SHA를 대조한다. 설정만으로 ready를 만들지 않는다.
- RunPod는 별도 monitor로 Backend v2 readiness와 같은 9개 SHA를 확인한다.
  최근 확인 유효시간은 45초이며 확인 실패·불일치 시 canonical 접수만 닫는다.
  Backend는 RunPod의 `executorConfigured`를 확인하므로 서로의 admission을 기다리는
  순환 대기는 만들지 않는다. legacy 단일 슬롯·claim·heartbeat 경계는 유지한다.
- Backend registration/verifier/callback/callback-verifier/journal/callback-apply와
  canonical admission 설정을 켰다. 실제 reader와 worker 준비가 확인돼야 접수가 열린다.
- 기존 analyze/retry body·envelope와 공개 legacy DTO는 유지한다.
  `X-Analysis-Result-Schema: voice-coaching.runpod-analysis-result.v4`를 명시한 요청만
  canonical profile을 선택한다. 헤더 없음은 legacy, 잘못되거나 중복된 헤더는 400이다.
  서버 미준비 시 503이다. 초기 범위는 품질 PASS인 **단독 음성 PRONUNCIATION**이며
  영상·course·title exam은 제외한다. 영상/기존 segments 후처리 차단도 유지한다.
- 8082 gateway에 정확한 `/health/canonical` 경로만 분석 8080으로 연결했다.
  분석 Bearer 인증은 유지하며 연구/OpenAPI 경로를 열지 않았다. nginx 문법 확인 후
  reload했다. TTS 프로세스·요청/응답, AWS nginx와 FE는 변경하지 않았다.
- 내부 control v1.2 readiness schema만 READY/NOT_READY 양쪽과
  `canonicalExecutorReadiness`를 기술하도록 조정했다. frozen R/S7, GPT prompt·모델,
  result v4·MFA/ID 계약은 변경하지 않았다. 기존 두 legacy schema의 Backend 사본은
  운영 LF와 맞춘 바이트 정규화뿐이며 JSON 의미 변경은 없다.

### 키·설정·복구

- Backend reader는 기존 bucket/prefix 제한 읽기 키를 사용한다. writer/master는 주입하지 않았다.
  `/etc/alpha-canonical-secrets/backend-runtime.env`는 root:0600이며
  `alpha-backend.service.d/40-canonical.conf`의 EnvironmentFile로 읽는다.
  기존 `/etc/alpha-backend.env`는 수정하지 않았다.
- RunPod writer는 위 암호문 공급원에서 복원하며 실행 env는 root:0600이다.
  두 제한 키의 만료는 2026-12-30이다. 만료 전 역할별 교체가 필요하며 callback token
  교체/Pod 이전 시 writer envelope도 재암호화해야 한다. Pod 재생성 복원 QA는 하지 않았다.
- journal staging 한도는 `268435456` bytes다. 원본·취소/탈퇴 유예·orphan 보존은
  `INDEFINITE`로 유지하며 자동 삭제하지 않는다. 용량 누적 시 신규 접수가 닫힐 수 있으므로
  운영자가 보관량·DB 여유를 관리해야 한다. 이번 작업에서 원본/영상/DB 레코드를 삭제하지 않았다.
- 중단 시 신규 admission부터 닫고 이미 접수한 작업의 reader·검증·apply·ACK 경로는
  유지한다. 이전 OFF JAR로 즉시 되돌리면 진행 중 v4 처리가 중단될 수 있다.
  배포 helper는 고정 이전 SHA를 검사하는 일회성 도구이므로 무조건 재실행하지 않는다.

### 최종 배포 식별자

| 항목 | 값 |
|---|---|
| Backend JAR SHA-256 | `7f9f4c988c81fe7e0c73ba0fce52db1db1482519f43fb39fcc0b08c50073ce12` |
| Backend JAR | `/opt/alpha/releases/canonical-active-7f9f4c988c81fe7e0c73ba0fce52db1db1482519f43fb39fcc0b08c50073ce12.jar` |
| Backend 변경 영수증 | `/var/backups/alpha-canonical/activation-20261002T023209Z/receipt.json` |
| immutable verifier release | `/opt/alpha-canonical/releases/8ad7e1ce5afaa5b3024f535acb822ce242c934b094370bbc057b2f7e1bdb653c` |
| RunPod overlay SHA-256 | `b3fb15bc9b4027faeabfd089f7a0f3f2750391f766392412bb72634e08c0f6f5` |
| RunPod 30파일 bundle SHA-256 | `485a317b634473c1d15c233d3d119920feb8af12fee5d4601f60f61a0d1eab30` |
| RunPod 직전 백업 | `/workspace/intelligentai/deployment/canonical-runtime-backup-20261002T023415Z` |
| gateway 설정 SHA-256 | `45084a18fd5427600328e6c5eca128b8bbf40259467488b94aa22ec0862ddc11` |
| gateway 직전 백업 | `/root/.config/intelligentai/gateway-before-canonical.conf` |
| control v1.2 SHA-256 | `7acd63e1a0ff9aa68bd2d86c2c5ec37827df10b85b186efac08c1d338287e7a4` |

Backend는 기존 `b410076…` 작업본, RunPod는 `32d98e3…` 기반 overlay다.
새 commit으로 표시하지 않는다. compileJava/processResources/bootJar는 성공했으며
DB migration은 V36 유지(이번 활성화에서 추가 migration 없음)다.

### 관측과 미수행 경계

| 운영 확인 | 결과 |
|---|---|
| AWS alpha-backend | active, 실제 JAR SHA 일치, 7개 canonical 활성 플래그 true |
| Backend 인증 readiness v2 | HTTP 200 ready, canonicalSupported=true, canonicalAdmissionEnabled=true, evidenceStoreStatus=READY |
| RunPod 8082 인증 canonical health | HTTP 200, executorConfigured=true, admissionEnabled=true, executionQaPerformed=false |
| AWS 실제 설정 URL을 통한 canonical health | HTTP 200, 동일 ON 상태, schemaDigests 9개 |
| RunPod 기존 분석 health | HTTP 200 ready, executorReady=true, OPENAI / gpt-6-astra |
| 공유 계약 SHA | Backend/RunPod 9개 일치 |

11:40~11:46 KST 후속 파일 대조에서도 설치본/영구 overlay 30개가 배포 bundle과 모두 일치했다.
DB는 migration 36/유효 worker lease 0, AWS 설정 URL의 독립 TTS readiness는 HTTP 200
`melo-ko-practice-r1`이었다. TTS 합성 요청은 보내지 않았다.

이는 **배포·부팅·키 주입·서버 접수 활성화 확인**이다. 새 사용자 요청, 실제 녹음/core/GPT,
B2 PUT/readback, callback 적용 E2E, 장애/취소/복구 QA는 수행하지 않았다.
FE 테스트·화면 활성화는 FE 개발자의 책임이다. commit/push/PR은 하지 않았으므로
담당자는 배포 작업본을 develop에 통합해 후속 CI가 운영 변경을 덮어쓰지 않도록 해야 한다.
