# Canonical v4 운영 제한 키·통합·배포 실행 계획

**2026-10-04 Native 전환 작업본:** [전체 음소 채점 구현 현황](native-full-phone-implementation-20261004.md). 새 소스는 구형 추론을 거절하며 Native 정책은 DRAFT다. 보정/개발자 QA/BE·FE 계약 반영 전에는 이 작업본을 운영에 활성화하지 않는다. 아래 기록은 과거 운영 이력이다.

**2026-10-03 추가:** 새 채점 identity·배포 순서·확인 범위는 [채점 구현 정본](scoring-implementation-20261003.md)을 따른다. 기존 운영 기록은 새 경로의 배포·추론 성공 증거가 아니다. AWS 검증 묶음/Backend/FE가 새 점수 계약을 지원한 뒤 RunPod 파생 core/H5를 활성화한다.

**최신 실행: 2026-10-02 11:39~11:41 KST [서버 배포·제한 v4 접수 활성](rollout-execution-20261002.md#제한-v4-접수-활성화).**
RunPod 실행기·부팅·writer 키와 Backend reader·검증·journal/apply를 운영 반영했다.
양쪽 readiness와 계약 9개 SHA가 일치하고 v4 접수는 ON이다. canonical migration은
기존 seed V32를 보존한 **V33~V36**이다. 명시적 v4 헤더의 단독 음성 발음 학습만 대상이며
헤더 없는 기존 API와 FE/TTS 계약은 유지한다. 실제 추론/GPT/B2 PUT/callback E2E는
미수행이며 FE 테스트는 FE 개발자 책임이다. 아래 OFF/미배포·옛 migration 번호는
각 작성 시점의 계획/이력이며 현재 상태는 위 실행 기록을 우선한다.

작성일: 2026-10-01. **계획이며 실행 완료 기록이 아니다.** 이번 문서 작성에서는 키 발급·주입, 원격 설정 변경, DB migration, 배포, v4 활성화를 하지 않았다. 계약의 정본은 [callback v4 전환 계획](callback-v4-plan.md), 소스·동결 근거는 [통합 조사](README.md)다. 이 문서는 남은 구현과 운영 전환의 실행 순서·중단 조건을 구체화한다.

FE 호환성 재점검: 2026-10-01 공개 VC-FE의 기본 main `81b27dee88ae1c09bb4f9c2581be3552ac283137`과 dev `d54ceae5065880ccabd8f00ffe83500ab9077dfd`를 직접 대조했다. **이전 dev-only 검토로 현재 FE 호환을 보장할 수 없다.** [실제 API 계약·호출자별 위험과 근거](frontend-contract-review.md)를 본 계획의 배포 gate로 추가한다. 소스 확인이며 운영 FE 배포 확인은 아니다.

후속 실행: 사용자의 계획 수행 요청으로 [2026-10-01 실행 기록](rollout-execution-20261001.md)에 운영 기준선·보관 journal 구현·역할별 B2 제한 키 발급 및 Native 인증을 기록했다. 최초 401은 새 키 쌍으로 해소됐다. runtime 주입과 D0~D6 배포 완료는 아니다.

최신 로컬 구현은 위 실행 기록의 마지막 절을 따른다. 요청별 canonical profile → request v2/outbox/registry 원자적 발행, private 현재 결과 reader와 학습 완료/시험 점수 불가 조건, 별도 canonical 공개 조회 allowlist/actions를 추가했다. 검증된 REJECT/INCONCLUSIVE 후 ANALYZING 세션의 재녹음 복구도 기존 업로드 URL 명령에 연결했다. 아래 P1/P3의 해당 구현 항목은 로컬 코드가 있으며 공개 admission은 여전히 off다. 실제 FE 소비·DB/HTTP 복구 흐름의 개발자 수용 없이 활성화하지 않는다.

배포 조건 정정: **FE 실제 URL은 Backend/RunPod 구현·키 준비·접수 off 상태의 서버 배포(D0~D2)의 필수 입력이 아니다.** 기존 공개 API 계약은 그대로 유지한다. FE 확인은 새로운 canonical 상태·결과를 사용자 화면에 활성화하는 D3~D6의 소비자 호환 조건이다. URL 자체 대신 대상 release/commit·배포 artifact와 개발자의 직접 수용 기록으로 확인할 수 있다. 내부 v4 callback 업그레이드와 FE의 새 기능 활성화를 하나의 차단 조건으로 묶지 않는다.

## 1. 유지할 결정과 현재 출발점

**10/1 후속 결정:** [실행기·저장소 조사](executor-storage-gate.md)의 FUSE/overlay 문제에 대해 사용자가 **기존 AWS PostgreSQL 내부 journal로의 변경을 승인**했다. [구현·추가 gate](backend-journal.md)에 V35, one-shot start·B2 업로드 checkpoint·callback apply/ACK, RunPod 내부 HTTP client를 반영했다. 새 운영 경로는 SQLite를 fallback으로 사용하지 않으며 private scratch는 임시 공간으로만 취급한다. 별도 POSIX volume 선택 대기는 종료됐다. DB capacity/백업·migration, 전체 runtime/boot·FE·키·배포·수용·활성화는 여전히 미완료다.

후속 [계약 보존 리팩토링 기록](refactoring-review-20261001.md)을 함께 따른다. 다음 오프라인 검증기 release에는 분리된 `canonical_codec.py`를 포함한다. 기존 staged release는 그대로이며 새 코드의 설치 readiness는 다음 배포에서 별도 확인한다. 공개 API/DTO와 접수 gate는 이번 리팩토링에서 변경하지 않았다.

- 전체 통합·RunPod 배포·v4 접수와 이번 작업의 AWS Backend/DB 직접 배포는 이미 승인됐다. 이후 계획 수행 요청에 따라 실행 중이며 동일 승인을 다시 요구하지 않는다. 승인 여부와 구현·운영 준비 완료 여부는 구분한다.
- canonical 원본 보존, 취소·회원탈퇴 후 물리 삭제 유예는 `INDEFINITE`다. orphan도 별도 정리 승인 전 자동 삭제하지 않는다. 취소·탈퇴의 실행 차단·공개 접근 회수는 즉시 적용한다. 기존 녹음·영상 저장/삭제 정책은 별개다.
- frozen core R, S7 prompt/validator/후보 정책, 전문가 원문을 수정하지 않는다. 영상은 canonical 경로에서 `NOT_CONNECTED` / `correctiveClaimsAllowed=false`, prosody는 승인된 교정 mapping 없음 상태를 유지한다. 기존 legacy 영상·TTS 경로는 보존한다.
- QA는 개발자가 직접 수행한다. 에이전트는 자동 회귀 테스트·fixture·브라우저 자동화·추론 품질 QA를 실행하거나 생성하지 않는다. 정적 확인·필요한 컴파일·요청된 운영 상태 확인과 개발자의 수용 판정을 구별한다.

| 경계 | 마지막으로 확인한 사실 | 배포 전 필요한 작업 |
|---|---|---|
| RunPod | 9/30 23:28:04 KST, `s8hb5b0l5k4qlx`, 운영 revision `32d98e39767b56697bc7e58fb50e3e9d61432349`, legacy 분석 ready. canonical 모듈 미배포 | 대상·주소·revision 재확인, 운영 소스 기준으로 신규 코드 통합 |
| 로컬 AI | main `cfe89ebca8131d0be46cb78b2b7dfd505d65bf63` + 미커밋 기반 구현. bridge/result/store/retention 있음, canonical 접수는 무조건 503 | 실제 executor·보관·receipt·durable delivery·factory·준비 코드 연결 |
| Backend | develop `b48db988e7485943097acaa4c71308ef306175a8` 기반 별도 worktree. dual request, disabled readiness, 실행 registry, V32 migration 작성 | 운영 SHA/DB 확인, migration 적용 전 검토, receipt/ingestion/public reader/업무 경로 구현 |
| Frontend | 공개 main `81b27dee88ae1c09bb4f9c2581be3552ac283137`과 이전 dev 기준이 다름. standalone canonical client/view는 로컬 신규 코드일 뿐 공개 배포 아님 | 실제 배포/대상 브랜치 확인, main의 업로드·영상 동의 유지, 결과·실패·재진입·이력·업무 호출자 연결 |
| B2 | 10/1 00:02:49 KST, Native 인증 성공, `speakai2026` private, `us-east-005`, lifecycle 빈 배열, SSE-B2/AES256, Object Lock 비활성 | 제한 키 발급·주입, 실제 S3 동작 및 Backend 독립 검증 수용 |

위 시각은 이전 관측이다. 이 문서 편집일이 새 운영 확인 시각은 아니다. 현재 부족한 것은 관리 키 제공/배포 허가가 아니라 **미연결 구현·제한 키·개발자 수용 근거**다. health 200은 canonical readiness나 실제 GPT 생성 성공이 아니다.

### 1.1 기존 FE 계약을 바꾸지 않는 경계

- FE는 `/api/backend` → Backend 공개 API를 사용한다. 이 base URL을 RunPod 8082로 바꾸지 않는다. RunPod callback v4/receipt/B2 key는 서버 내부 경계다.
- 기존 공개 URL·body·`{result,message,data,code?}` envelope·오류·JWT/cookie/refresh를 유지한다. 내부 HTTP의 동의/grant 제외를 공개 analyze/retry 동의 body 및 영상 등록 동의 삭제로 확대하지 않는다.
- 녹음 upload URL/requiredHeaders/PUT 및 TTS의 HTTP 200 `audio/mpeg` binary를 유지한다. B2 근거 저장소와 녹음·TTS 계약은 별개다.
- 기존 polling의 PENDING/PROCESSING/COMPLETED/FAILED와 legacy 결과 DTO를 canonical 상태/객체로 바꾸지 않는다. 새 view/header는 별도 client의 추가 계약이며 전역 `api`의 method/반환 타입을 교체하지 않는다.
- 기존 FE 무수정 상태에서 canonical 근거 전체 표시까지 보장하지 않는다. legacy 사용자는 기존 경로를 유지하고, canonical은 새 상태·실패·업무 처리가 연결된 화면에서만 opt-in한다. 구 화면의 v4 안전 요약은 의미 전체 보존이 아닌 축소 안내다.

## 2. 제한 키 발급 설계

### 2.1 범위·역할

대상은 기존 private `speakai2026`, prefix `canonical-evidence/v1/`, endpoint `https://s3.us-east-005.backblazeb2.com`으로 한다. 실행 시 계정에서 버킷 ID를 다시 조회해 이름·private 상태와 함께 고정한다. 이 prefix는 아직 업로드된 운영 경로가 아니라 분리 구성안이다. 새 버킷이나 기존 lifecycle 변경을 전제로 하지 않는다.

| 역할 | 키 이름 형식 | 부여할 capabilities | 배치 대상 |
|---|---|---|---|
| 업로드 및 동일 version readback | `canonical-prod-runpod-writer-YYYYMMDD-N` | `readBuckets`, `readFiles`, `writeFiles`, `listAllBucketNames` | RunPod의 canonical 저장 adapter만 |
| 독립 검증 | `canonical-prod-backend-reader-YYYYMMDD-N` | `readBuckets`, `readFiles`, `listAllBucketNames` | AWS Backend evidence verifier만 |

두 키 모두 `bucketIds=[확인한 단일 ID]`와 `namePrefix=canonical-evidence/v1/`로 제한한다. `readBuckets`는 현재 adapter의 ACL 확인, `readFiles`는 version 지정 읽기, `writeFiles`는 업로드에 대응한다. 공식 S3 SDK 호환 지침에 따라 `listAllBucketNames`를 포함하되 **계정의 버킷 이름 목록 노출을 허용하는 예외**임을 기록한다. 다른 버킷의 파일 접근 권한을 주는 것은 아니다. [공식 S3 키·권한 매핑](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)

`listFiles`, `listBuckets`, `deleteFiles`, 키 관리·버킷 변경·암호화 변경·retention 변경·governance 우회 권한은 주지 않는다. SDK가 자동 discovery/목록 호출을 요구하면 고정 endpoint/bucket 호출로 수정하고, 오류 해결을 위해 마스터 키로 대체하지 않는다. 응답 유실 객체의 운영자 reconciliation은 필요할 때 별도 권한으로 수행하고 서비스 키를 확대하지 않는다.

주의: `writeFiles`는 이름 기준 삭제의 hide marker 생성에도 쓰일 수 있다. 따라서 “삭제 권한 없음”을 모든 논리 삭제/변조 불가능 보장으로 표현하지 않는다. 저장 adapter의 삭제 API 금지, immutable `versionId` 참조, SHA 검증을 유지한다. Object Lock 없는 버킷에서 이 구성을 WORM이라고 부르지 않는다. [공식 S3 삭제 권한 설명](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)

### 2.2 안전한 발급·회전

1. 키를 받을 역할별 비밀 공급원·권한·복구 경로부터 준비한다. 관리 자격증명은 발급 프로세스에만 주입하며 채팅의 값이나 `.env` 전체를 명령 인자·로그·배포 archive에 복사하지 않는다.
2. Native 인증 응답의 API URL에서 `POST /b2api/v4/b2_create_key`를 사용한다. v4는 단수 `bucketId`가 아니라 `bucketIds`다. 위 범위/capabilities와 `keyName`, `accountId`를 명시한다. 응답의 실제 제한 범위를 대조한다.
3. 키 수명은 **운영 제안값 90일(7,776,000초)**, 만료 14일 전 교체 착수·7일 전 미완료 경보로 잡는다. 이는 자료 무기한 보존과 별개다. 만료 추적 담당·경보 공급원을 배포 전에 지정한다.
4. 반환되는 비밀은 그 응답에서만 얻을 수 있으므로 즉시 승인된 비밀 공급원에 저장한다. 공개 운영 기록에는 key ID, 역할, 범위, 만료일, secret version 식별자만 남긴다. 원문 응답을 기록하지 않는다.
5. 발급 timeout은 성공/실패 불명으로 취급한다. 이름은 고유하지 않으므로 무조건 재발급하거나 같은 이름의 키를 일괄 폐기하지 않는다. 목록을 끝까지 확인해 발급 시각·ID·범위를 대조한다. 비밀을 회수하지 못한 이번 발급 키만 식별 후 폐기·재발급한다.
6. 회전은 새 키 발급 → 새 secret version 주입 → 역할별 확인 및 재기동 복원 확인 → 이전 키 사용 종료 → 정확한 구 key ID 폐기 순서다. 사용 여부가 불명확한 다른 키는 건드리지 않는다.

발급 API의 필드·수명·비밀 반환 제약은 [공식 create-key 문서](https://www.backblaze.com/apidocs/b2-create-key)를 따른다. 위 90일/14일/7일은 이 서비스의 제안이며 B2 기본 정책이 아니다.

채팅으로 공유된 마스터 키는 의존 서비스 조사 후 교체 대상이다. 제한 키가 필요한 운영 경로를 대체하고 복원 수단이 확인되기 전에 임의 폐기하지 않는다. 마스터 키는 RunPod/Backend S3 runtime에 배포하지 않는다.

## 3. 주입과 재기동 복원

값 없는 [canonical-evidence.env.example](../../deploy/runpod/canonical-evidence.env.example)을 설정 이름의 기준으로 삼는다. 현재 이 파일은 운영 boot가 읽지 않는다. 기존 녹음용 AWS credential이나 `OPENAI_API_KEY`를 B2 credential로 재사용하지 않는다.

| 대상 | 구현할 공급 경로 | 완료 조건 |
|---|---|---|
| RunPod | 기존 보호된 `RUNPOD_ANALYSIS_HTTP_ENV_JSON` 공급원과 root source `/root/.config/intelligentai/analysis-http.env`의 우선순위를 단일화. `prepare_http.py`의 parser/allowlist에 전용 이름 추가 → `/run/intelligentai/analysis-http.env` → canonical factory | source 부모 0700/파일 0600, runtime은 실제 서비스 UID만 읽음. 서비스가 writer key ID와 올바른 비밀 version을 사용하고 container 재생성 후 복원됨 |
| AWS Backend | 실제 서비스 launcher·secret 공급원을 먼저 확인하고, reader 전용 설정을 그 공급원에 별도 namespace로 등록. 전용 evidence client에 명시 전달 | recording client와 격리, 값 누락 시 canonical만 NOT_READY. 새 배포·재기동 시 동일한 secret version 복원 |
| 관리 작업 | 보호된 운영자 비밀 보관소 → 짧은 수명의 발급 프로세스 | 마스터 값이 runtime, CI 출력, 일반 archive, Git에 남지 않음 |

RunPod JSON 공급원이 source를 덮어쓸 수 있으므로 source 파일만 바꾸고 완료 처리하지 않는다. `/root`와 `/run`은 영구 비밀 보관소가 아니다. 기존 외부 비밀 공급원의 보호/복원 능력이 없으면 **그 공급원을 마련할 때까지 배포 gate를 닫는다**. 아직 확인하지 않은 AWS Secrets Manager/SSM이나 Pod secret 기능이 이미 구성돼 있다고 문서에 쓰지 않는다.

구현 세부 사항:

- `AI_CANONICAL_EVIDENCE_B2_*`와 세 `INDEFINITE` 정책을 명시적으로 파싱한다. Backend는 동일 의미의 별도 configuration binding과 reader client를 작성한다. 설정 이름/오류 코드만 로그에 남긴다.
- 기존 서비스 UID/GID 10001 관측을 대상에서 재확인한다. runtime 파일은 서비스 소유 0400 또는 동등한 최소 읽기 권한으로 준비하고, root source와 구분한다. 원자적 파일 교체·준비 검증 후 필요한 서비스만 재시작한다.
- canonical launcher·MFA·ffmpeg에는 B2/Backend 관리 키를 상속하지 않는다. GPT 전송에 필요한 값도 해당 프로세스에만 전달한다. gateway/TTS 환경과 합치지 않는다.
- TLS 검증·고정 HTTPS endpoint·제한된 timeout을 유지한다. 비밀값을 URL/query/프로세스 인자에 넣지 않고 shell tracing·SDK body/debug 로그를 끈다.
- 배포 manifest/attestation은 비밀 제외 설정과 코드·schema digest만 포함한다. 실제 비밀은 overlay·공유 `/workspace`·이미지·복구 archive 밖에서 공급한다.

## 4. 남은 통합 구현 — 순서와 산출물

### P0. 운영 기준선과 계약 확정

서버 배포 기준선으로 운영 RunPod의 현재 소스·overlay/boot·R/S7 manifest/lock, AWS 서비스 SHA·DB schema history·DB 백업/복원 수단을 읽어 확인한다. FE release/commit 대조는 FE 변경·사용자 대상 opt-in 활성화 전에 별도로 수행하며 FE URL 부재로 서버 작업을 차단하지 않는다. 로컬 checkout의 과거 Clova factory를 포함한 전체 파일을 운영 위에 덮어쓰지 않는다. 운영 revision 기준 통합 브랜치에서 필요한 변경만 이식한다. 사용자 worktree는 보존한다.

Backend는 기존 별도 worktree에서 최신 develop과 migration 번호 충돌을 대조한다. V32는 아직 미적용이므로 번호 확정 후 적용한다. develop PR·commit SHA 기록은 직접 배포 승인과 별도로 남긴다. FE는 공개 main/dev와 **실제 운영/배포 대상 브랜치**를 확인한다. 이전 dev worktree를 통째로 배포하지 않고, main의 상대 업로드 인증·영상 동의·기존 화면 기능을 보존하는 최소 변경을 이식한다. 기존 v1 UTC regex 문제는 운영 실제 schema/parser와 비교한 뒤 별도 호환 변경으로 다루며 v4 작업에서 구 계약을 일괄 치환하지 않는다.

구현 전에 다음을 양쪽 source/schema에 함께 확정한다:

- evidence 등록 `POST /api/internal/ai/analyses/{analysisId}/evidence`, receipt 상태 조회의 URL/DTO/오류 enum, 인증·202/중복 응답·poll 종료 조건. 현재 manifest만 정의돼 있으며 receipt API가 구현돼 있는 것은 아니다.
- receipt의 execution/manifest SHA/검증한 exact version 집합 결속, terminal 실패/취소/만료 의미. 다른 worker가 보낸 임의 receipt를 신뢰하지 않는다.
- 현재 **disabled 응답만 허용하는** control v1.2 schema를 enabled 상태까지 명시 확장하고 Backend/Python 소비자를 동시에 구현한다. 기존 `BackendHttp.call`은 이 503 capability 문서를 그대로 처리하지 못하므로 전용 분기가 필요하다.
- 공개 canonical view의 outer envelope·actions·오류 계약. 현재 standalone FE 제안을 Backend 구현 계약과 일치시킨다. frozen schema 3개는 바꾸지 않으며 서비스 schema는 양쪽 동일 바이트로 package한다.

서버 완료 gate: 위 서버 계약, schema digest, migration 목록, 서버 변경 대상 revision 및 롤백 artifact가 확정돼야 한다. FE 배포 URL/화면 수용은 D0~D2의 gate가 아니다. FE 수정 및 사용자 대상 활성화 때는 별도 소비자 gate를 적용한다. 새 approval 대기가 아니라 실행 전 기술 대조다.

### P1. Backend 저장·검증·수신 경로

1. immutable execution registry를 실제 publisher transaction에 연결한다. outbox v1 원문은 그대로 두고 opt-in 실행만 v2/profile/result v4를 고정한다.
2. execution·manifest·artifact version reference·receipt·검증 작업·callback dedup 저장 구조를 additive migration으로 추가한다. 자료 접근용 현재 포인터/visibility는 immutable 원본과 분리한다. 녹음 삭제 cascade가 원본 근거를 없애거나 legacy 삭제를 막지 않게 한다.
3. 인증 evidence 등록은 실행·lease·소유 관계·크기·schema를 확인한 뒤 durable 검증 작업을 등록한다. 비동기 verifier가 reader 키로 exact version을 읽어 길이/암호화/MIME/SHA와 frozen/service 의미를 독립 검증한다. B2 네트워크 작업 중 analysis row lock을 보유하지 않는다.
4. 검증 완료 시 다시 현재 세대·취소·deadline을 확인하고 receipt를 확정한다. RunPod의 readback 성공을 Backend `VERIFIED`로 대체하지 않는다. 바이트 SHA와 요청 JCS digest를 혼동하지 않는다.
5. 기존 callback URL에서 schemaVersion별 typed ingestion을 추가한다. receipt·request/execution/worker·scope·후보 순서/guidance·MFA/G2P·상태를 확인한다. 동일 event+동일 body만 DUPLICATE, 동일 event+다른 body는 충돌로 처리한다.
6. 결과 저장·현재 포인터·APPLIED 판정을 같은 transaction에서 확정하고 후처리는 durable outbox로 한 번만 수행한다. 늦은 callback이 취소/탈퇴/재시도 이전 세대를 다시 공개하지 못하게 한다.

완료 gate: v4 reader/receipt worker/DB는 준비됐으나 **접수는 닫힌 상태**로 배포할 수 있어야 한다. legacy readiness·v1/v3 처리와 기존 공개 API는 유지한다.

### P2. RunPod executor·보관·전송

후속 로컬 구현: [callback 내구성·복구](callback-delivery.md)의 journal v3/outbox·exact-byte 전송·원래 worker lease 재확인·Backend ACK-only endpoint를 작성했다. 전송 loop/startup scheduler와 executor/factory 연결은 아직 남아 있다. 코드 존재를 P2 완료나 운영 재시작 수용으로 보고하지 않는다.

- R launcher와 S7을 별도 pinned 환경으로 연결한다. R의 Python 3.11 경로, S7 Python 3.12와 `jsonschema==4.25.1`을 재확인한다. 기존 공용 환경을 임의 downgrade하지 않는다. manifest/lock 불일치는 readiness 실패다.
- claim/heartbeat/lease/cancel/deadline을 core→S7→보관→receipt→callback 전체에 적용한다. raw와 prepared media의 digest·서버 실측 frame/sample rate 및 시간축 binding을 보존한다.
- Pod에서 cgroup v2 controllers/kill이 없었던 점을 전제로, **실행별 Linux subreaper supervisor + 소유 자손 추적**을 구현한다. R/MFA의 별도 session도 회수해야 하며 부모 PID 종료만으로 완료 처리하지 않는다. socket/tmpdir은 실행별 소유 경로만 정리한다. 격리·종료 보장을 구현/수용하지 못하면 활성화를 막는다.
- `canonical_bridge`/transport/result/store/retention을 factory와 runtime에 연결한다. frozen selection/request/validator를 우회하지 않는다. non-ACCEPT에는 GPT를 호출하지 않고, 전송 재시도로 AI/GPT를 다시 실행하지 않는다.
- 원본은 개별 16 MiB, manifest 64 KiB, callback 1 MiB 한도를 유지한다. 한도 초과 때 자르거나 후보를 줄이지 않는다. callback 과대는 계약대로 FAILED/RETAINED_ONLY로 처리하고 원본은 B2에 보존한다.
- 실행 journal과 결과 outbox를 구현한다. 원본 바이트/digest, 업로드 상태·version reference, manifest bytes, receipt, 고정 result event/body를 단계별 원자적으로 기록한다. 응답 유실·재시작에서도 저장된 같은 body/event만 재전송한다.
- `/run`만으로 durable을 주장하지 않는다. RunPod 실행 journal은 접근 제한된 지속 상태 경로로 두되 임시 spool이며 B2 원본/Backend durable 작업을 영구 정본으로 한다. 정확한 mount와 Pod 이전 복구 경로는 P0에서 고정한다. B2 저장 전 host 손실은 복원 성공으로 꾸미지 않고 Backend lease/timeout 실패로 종료한다.
- 업로드 응답 유실은 UNKNOWN으로 보류하고 자동 재업로드/삭제하지 않는다. 이미 얻은 version reference를 보존한다. callback ACK 유실은 동일 전송 재시도만 허용한다. deadline/lease 상실 후에는 새 GPT·원본 생성·결과 적용을 하지 않는다.

완료 gate: core/S7 의존성, 프로세스 회수, B2 저장, Backend receipt, durable delivery를 각각 확인할 수 있어야 한다. Python의 현재 무조건 503 분기를 단순 삭제하지 않고 이 조건을 계산하는 gate로 교체한다.

### P3. 공개 API·업무·Frontend

- header 없는 기존 요청은 legacy를 유지한다. 별도 canonical client가 `X-Analysis-Result-Schema`를 접수/재시도에만 명시한다. 공통 HTTP client에 전역 주입하지 않는다. 준비 안 됐으면 기존 계획의 503을 반환하고 legacy로 몰래 낮추지 않는다. 요청 body·성공 DTO는 기존과 같으며 proxy/401 refresh에서 header를 보존한다.
- 신규 `GET /api/v2/analyses/{analysisId}`에 pending/failed/canonical 상태를 제공한다. 소유한 legacy 결과에만 `404 CANONICAL_ANALYSIS_NOT_FOUND`; 타인·삭제·숨김·이전 세대는 근거를 노출하지 않는다. FE는 이 legacy 전용 404에서만 구 API로 fallback한다.
- 기존 `pollAnalysis`는 FAILED에서 예외를 던지므로 새 canonical 흐름은 요청/상태의 analysisId를 보존해 실패 상세도 조회한다. 기존 polling의 반환/예외 계약과 status enum은 변경하지 않는다. 기본 HTTP 20초·proxy 60초·poll 600초를 전체 추론 deadline과 혼동하지 않고 비동기 접수→상태 조회를 유지한다. 대기 timeout은 재조회이며 자동 재분석이 아니다.
- `ACCEPT`는 정상 발음 판정이 아니다. 완료 판정은 COMPLETED+ACCEPT+검증된 정상 전달이며 deterministic fallback/no candidate도 허용한다. REJECT/INCONCLUSIVE는 재녹음, FAILED만 최대 3회 새 execution 재시도다.
- main의 `practice-session` 신규 결과·재진입은 결과 조회 후 자동 complete/진도/시험 호출을 한다. canonical에서는 별도 결과 분기에서 서버 `actions`를 먼저 확인하고 불가한 mutation을 보내지 않는다. Backend의 409 방어만으로 FE 호환 완료를 주장하지 않는다. legacy 업무 판정은 그대로 보존한다.
- null 점수 시험 완료는 `409 ANALYSIS_SCORE_UNAVAILABLE`; v4 regenerate는 `409 FEEDBACK_EVIDENCE_UNAVAILABLE`/GPT 0회, legacy segments는 기존 빈 페이지 형태를 유지한다. v3 기능은 유지한다.
- canonical 전용 renderer를 실제 신규 결과·실패·재진입·이력 상세에 연결한다. main `AnalysisView`는 dev의 `CoachingView` 연결과 다르며 기존 coaching v1은 CTC/writtenRole이다. type cast·MFA 초의 CTC ms 필드 대체를 하지 않는다. prepared/source 시간 대응이 검증되기 전 replay는 비활성이다.
- 이력의 기존 localStorage bundle에 canonical을 섞지 않는다. canonical은 no-store/별도 메모리 상태로 조회하며 retry/선택/삭제/사용자 변경 시 이전 세대를 비운다. 권한·갱신 실패에 cached canonical을 재노출하지 않는다. 기존 결과·segments API의 shape는 유지하고 새 결과를 legacy `Promise.all`에 그대로 대입하지 않는다.
- 초기 v4 opt-in은 위 경로가 연결된 **독립 음성 연습**으로 제한한다. 입술 화면의 별도 analyze/complete와 시험·클래스는 기존 legacy 유지, 각 호출자의 수용 뒤 별도 확대한다. server gate도 세션/content/대상 범위를 검사한다. canonical visual NOT_CONNECTED를 이유로 기존 video capability/TTS를 비활성화하지 않는다.

P3 완료 gate: [FE 재점검 F1~F6](frontend-contract-review.md)의 실제 호출자와 main/dev 차이를 반영한다. 로컬 standalone component 작성이나 TypeScript 컴파일만으로 완료하지 않는다. 기존 API의 wire 호환과 canonical 화면·업무 의미의 수용은 별도 판정한다.

## 5. 배포 단계와 go/no-go

아래 순서는 구현 완료 후 수행할 계획이다. capability/접수/UI flag 이름은 구현에서 확정하며 현재 존재하는 env처럼 취급하지 않는다. DB와 reader 확장 없이 admission flag만 켜지 않는다.

| 단계 | 실행 | 다음 단계 진입 조건 |
|---|---|---|
| D0 기준선 | P0 대조, 비밀 공급원 준비, 제한 키 발급·비공개 보관, 배포/복구 artifact 고정 | 범위·만료·복원 공급원·현재 revision 확인, 새 접수 off |
| D1 Backend 확장 | DB 백업/복원 수단 확인 → additive migration → reader 키 주입 → receipt/ingestion/public reader 배포 | DB/schema digest와 verifier 준비, 기존 API 유지, v4 접수 off |
| D2 RunPod 확장 | writer 키 및 pinned 환경 준비 → 운영 기준 overlay/attestation 배포 → 분석 서비스만 재시작 | frozen hash·부팅 복원·B2·Backend 독립 readiness 일치, gateway/TTS 영향 별도 확인 |
| D3 FE reader | 운영 FE 기준 최소 변경으로 view·실패·재진입·이력·actions 분기 배포, 일반 v4 요청 flag off | main 업로드/영상 동의/TTS와 기존 API 유지, F1~F6 소비 경로 연결, 아직 일반 사용자 신규 v4 없음 |
| D4 개발자 수용 | 개발자가 승인된 제한 대상에서 실제 녹음·GPT·저장·callback·조회 및 아래 실패 조건을 직접 확인 | 개발자 수용 기록과 단계별 근거 확보. 기존 freeze/smoke 기록으로 대체 불가 |
| D5 제한 접수 | 모든 gate 충족 후 서버 대상 제한 + 독립 음성 연습의 요청별 FE opt-in. 입술·시험·클래스는 legacy 유지. 먼저 승인된 단일 시도로 운영 확인 | 원본 version/SHA→VERIFIED→APPLIED→공개 view 추적 가능, legacy 영향 없음, 일반 완료/재진입 경로의 무조건 mutation 없음 |
| D6 확대·인계 | 개발자 수용 범위로 점진 확대, key expiry/보관량/실패율 운영 경보 연결 | 동일 계약 유지, rollback 가능, 새 키로 재기동 복원 완료, 정확한 배포 SHA·범위 기록 |

D4는 비운영 환경을 우선한다. 사용할 수 없다면 운영의 일반 v4 admission은 닫고 명시적으로 제한된 개발자 대상 경로를 구현해야 한다. 제한 장치 없이 전체 접수를 켜서 검증하지 않는다. 기존 승인으로 배포할 수 있어도 개발자 수용 기록이 없으면 D5/D6를 완료 처리하지 않는다.

서버 통합·제한된 개발자 대상 확인은 실제 FE URL 없이 진행할 수 있다. 사용자 화면의 opt-in은 별도다. 공개 main 소스의 구 화면은 COMPLETED 뒤 complete를 호출하므로 canonical REJECT/INCONCLUSIVE를 안전하게 소비한다고 볼 수 없다. 이를 이유로 기존 header 없는 legacy 요청을 막거나 FE 변경을 서버 배포의 전제조건으로 만들지 않는다. FE 수용은 새 결과를 사용자에게 노출하는 범위에 적용하며 에이전트의 브라우저 자동화로 대체하지 않는다.

필수 수용 항목은 [기존 계획의 확인 항목](callback-v4-plan.md)에 따른다. 특히 네 canonical 상태/core 부재, scoped ID/후보 변조 차단, non-ACCEPT GPT 0회, fallback, B2 오류/receipt mismatch/ACK 유실, 취소·탈퇴·재시작·자손 종료, 중복 후처리, legacy 요청/digest, FE 실패 조회/캐시, 학습·시험·regenerate, TTS 분리 상태를 개발자가 확인한다. 에이전트가 테스트 데이터나 자동 QA를 만드는 단계가 아니다.

FE 수용에는 main 기준 구형 클라이언트·신형 클라이언트·동일 사용자 다른 기기, 일반 연습/입술/이력/재진입, 상대/외부 upload, TTS binary, JSON envelope·동의·401 refresh를 포함한다. non-ACCEPT의 complete 409를 오류 화면/분석 재시도로 오인하지 않고 재녹음 안내로 처리해야 한다. 새 reader 경로만 성공하고 기존 경로가 바뀌면 확대하지 않는다.

제한 키의 실제 업로드/readback·Backend 읽기와 prefix 밖 거부도 개발자 수용에 포함한다. 키 metadata 검토만으로 S3 권한 동작을 통과 처리하지 않는다. 확인에 쓰는 비사용자 자료의 경로·잔존/보관을 기록하고 무기한 정책 아래 무단 삭제하지 않는다.

전체 deadline·동시성·큐 용량은 기존 smoke 한 번으로 정하지 않는다. 개발자 측정으로 core/MFA/S7/GPT/B2/receipt/callback 각각의 시간과 실패 여유를 배정해, 기존 Backend deadline 및 lease와 맞춘다. budget이 모자라면 새 접수를 거절하며 frozen 후보/요청을 줄이거나 timeout을 무조건 늘리지 않는다.

## 6. 관측·장애·원복

로그에는 request/execution/event/receipt ID, revision, 단계·고정 reason code·소요시간·byteSize·digest만 허용한다. 사용자 대본·원본 JSON·GPT 전문·키·Authorization·signed URL은 남기지 않는다. 전체 관측을 API liveness, frozen 의존성 readiness, B2 접근, Backend verification, 실제 GPT 생성, 피드백 품질, 독립 TTS로 나눠 보고한다.

접수 즉시 중단 조건: 계약/frozen hash 불일치, 다른 execution 근거 결합, receipt 없이 결과 적용, 비공개 접근 실패/공개 노출, 취소 후 실행·노출, 고아 프로세스, 중복 GPT/후처리, legacy 또는 TTS 영향. 성능/오류율 확대 중단 기준은 D4 측정과 담당자 수용 기록에 고정한다.

1. v4 신규 admission과 FE opt-in을 먼저 끈다. 명시적 v4 요청은 503이며 silent downgrade하지 않는다. 기존 v3 신규 접수는 독립적으로 유지한다.
2. 안전하게 완료할 수 있는 v4 in-flight는 drain한다. 무결성·취소 문제가 있으면 실행을 fence하고 자손 종료 후 계약에 맞는 실패로 남긴다. 실행 중 profile 변경이나 같은 execution의 legacy 재실행은 금지한다.
3. 이미 저장된 v4의 **FE reader/실패·이력·재진입 분기와 Backend reader/DB/receipt**를 유지한다. v4를 모르는 구 FE/Backend 바이너리로 통째로 되돌리거나 migration/table/evidence를 삭제하지 않는다. 접수 flag만 끄고 reader를 유지하는 수정 배포가 기본 원복이다. 구 FE의 축소 summary를 전체 canonical UX 복구로 표현하지 않는다.
4. RunPod는 이전 검증된 legacy 분석 overlay로 복구할 수 있지만 신규 v4는 계속 차단한다. gateway/TTS·동결 R/S7 원본은 건드리지 않는다. 원본·journal·delivery 상태는 복구를 위해 보존한다.
5. 키 오류는 검증된 이전 secret version으로 전환하거나 canonical만 중지한다. 마스터 키로 권한을 확대하지 않는다. 침해가 의심되는 키는 영향/의존성을 확인해 정확한 ID를 폐기하고 별도 사고 절차를 따른다.

무기한 보존으로 누적되는 B2 용량/버전/orphan 비용을 운영자가 관측하되 자동 만료 규칙을 새로 만들지 않는다. 취소·탈퇴 후 사용자 접근은 차단하고 private evidence는 유지한다. docs에는 설정·정책·비밀 없는 배포 근거만 기록한다.

## 7. 인계 완료 기준

AI/BE/FE commit 및 배포 SHA, PR·직접 배포 여부, 적용 migration, schema/frozen digest, 역할별 key ID/범위/만료/secret version, 공급원 복원 확인, admission 대상/상태, 개발자 수용 결과, 원복 artifact, 미해결 항목을 한 기록으로 남긴다. 실제 운영 파일의 hash와 소스 revision을 함께 대조한다.

계획 작성 후 선행조건 조사·보관 journal 코드·제한 키 발급을 진행했으며, 최신 실제 수행 범위는 [실행 기록](rollout-execution-20261001.md)을 따른다. 제한 키 runtime 주입과 P0~P3 미연결 구현, D0~D6 완료는 남아 있다. 컴파일·schema 정적 대조나 B2 Native 인증 성공을 전체 통합·운영 활성화 완료로 표현하지 않는다.
