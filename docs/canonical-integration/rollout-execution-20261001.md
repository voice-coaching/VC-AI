# Canonical v4 배포 계획 실행 기록 — 2026-10-01

상태: **제한 키 발급·Native 인증 확인, 보관 복구 코드 추가, runtime 주입·운영 배포/활성화 미완료**. [현재 v6 계약](v6-unification.md)의 후속 실행이다. 기존 API·서비스를 재시작하거나 v4 gate를 열지 않았다.

## 운영 기준선 재확인

| 시각 UTC (KST는 +9시간) | 대상 | 실제 관측 |
|---|---|---|
| 02:48:04 | RunPod 관리 API | `s8hb5b0l5k4qlx` RUNNING, 현재 SSH mapping 대조, `/workspace` mount, `RUNPOD_ANALYSIS_HTTP_ENV_JSON` 존재. 비밀값은 출력하지 않음 |
| 02:48:41 | RunPod 분석 API | 인증 health ready, executorReady=true, OPENAI/gpt-6-astra, revision `32d98e39767b56697bc7e58fb50e3e9d61432349` |
| 02:48:41 | RunPod 동결/배포 경로 | R manifest/S7 manifest/lock SHA 모두 [기존 기록](README.md)과 일치. canonical 모듈·canonical env 없음. cgroup v2 controllers 없음 |
| 02:49:16 | AWS `alpha-backend.service` | user ec2-user, WorkingDirectory `/opt/alpha/app`, JAR `/opt/alpha/app.jar`, source env `/etc/alpha-backend.env` |
| 같은 실행의 후속 조회 | AWS 설정/DB | env root:root 0600. PostgreSQL read-only 세션에서 Flyway V0~V31 모두 success 확인. V32 미적용 |
| 같은 실행의 후속 조회 | Backend 공개 refs | develop `b48db988e7485943097acaa4c71308ef306175a8`, main `931f90ccdf6e1a982ecbeef4baaea5c333803869` |

AWS 실행 JAR SHA-256은 `0ad3f601271d088cf2bc3c0be54db9b24bd298827ab631585997dea2f14b404e`다. `git.properties`/build-info에서 commit을 확보하지 못했고 manifest는 `0.0.1-SNAPSHOT`, JDK 21 수준만 제공했다. 공개 develop SHA를 운영 JAR source SHA로 간주하지 않는다. `/opt/alpha/app/.git`은 없고 `/opt/alpha`의 이름상 backup/bak 항목은 발견되지 않았다. 이는 계정/DB 전체에 백업이 없다는 뜻이 아니며 **실제 복원 가능한 DB 백업 수단은 추가 확인 필요**다.

FE 운영 URL/배포 branch·SHA, 개발자 수용 담당/기록을 사용자에게 문의했으나, **URL은 서버 구현·배포의 필수 입력이 아니므로 전체 진행 차단 조건에서 제외**한다. 새 canonical 화면 활성화에는 소비자 release/commit 및 개발자 수용 근거가 필요하며 URL 자체를 강제하지 않는다. 이전 공개 main/dev source 대조를 실제 FE 배포 확인으로 바꾸지 않는다. 이번에는 TTS 합성·GPT 생성·사용자 녹음·품질 QA를 실행하지 않았다.

## 제한 키 발급 시도

[발급 도구](../../deploy/runpod/provision_canonical_b2_keys.py)를 추가했다. 승인된 AWS 호스트에서 root 전용 `/etc/alpha-canonical-secrets`(0700)를 만들고, Native B2에 현재 `.env`의 `BACKBALZE_MANAGEMENT_API_TOKEN`과 앞서 제공된 key ID로 요청했다. 비밀은 SSH stdin/프로세스 메모리에만 전달했고 코드·로그·명령 인자에 넣지 않았다.

결과는 **B2_HTTP_401**, 키 생성 intent 이전에 실패했다. 후속 원격 확인에서 해당 디렉터리는 비어 있었고 제한 키 파일·발급 intent는 모두 없었다. `b2_create_key`에는 도달하지 않았으며 운영 키 발급·주입 완료가 아니다. 버킷·lifecycle·기존 키·서비스 설정을 변경하지 않았다. 이전 Native 인증 성공은 과거 관측이며 이번 실패를 덮어쓰지 않는다.

당시 재개 조건은 유효한 `.env`의 `B2_APPLICATION_KEY_ID` / `B2_APPLICATION_KEY` 쌍이었다. 아래 후속 실행에서 해소됐다. 발급 도구는 응답의 일회성 비밀을 0600 파일에 보존하고, 같은 역할을 무조건 재발급하지 않는다. 이 보관 경로는 아직 runtime 공급/복원이 검증된 최종 설정이 아니다.

### 새 키 쌍으로 재개 — 03:20:25 UTC 관측

사용자가 새 키 쌍 설정을 완료했다. 인증 이후 발급 도구가 v4 응답의 권한을 `storageApi.capabilities`로 잘못 읽는 오류가 발견돼 `storageApi.allowed.capabilities`로 수정했다. [B2 공식 v4 응답 계약](https://www.backblaze.com/apidocs/b2-authorize-account)에 따른 수정이다. 오류는 생성 intent 전에 발생했고 03:16:11 UTC에 공급 디렉터리가 비어 있음을 확인한 후 재개했다.

03:19:55 UTC 역할별 제한 키 2개를 발급했다. 03:20:25 UTC에 각 키로 Native 인증을 다시 수행해 실제 bucket/prefix/capabilities/만료가 발급 응답과 동일함을 대조했다.

| 역할 | key ID (비밀값 아님) | 권한 | 만료 UTC |
|---|---|---|---|
| RunPod writer | `0055c17e18c6bc70000000005` | readBuckets, readFiles, writeFiles, listAllBucketNames | 2026-12-30 03:19:55.209 |
| Backend reader | `0055c17e18c6bc70000000006` | readBuckets, readFiles, listAllBucketNames | 2026-12-30 03:19:55.878 |

두 키의 객체 접근 범위는 비공개 `speakai2026` (`e5cc71977ef1880ca60b0c17`), `canonical-evidence/v1/`다. `listAllBucketNames`는 버킷 이름 나열 권한이며 다른 버킷의 객체 접근 권한이 아니다. deleteFiles·writeKeys는 부여하지 않았다. 원본 무기한 보존과 키의 90일 수명은 별개다.

공급 사본은 AWS `/etc/alpha-canonical-secrets/{runpod-writer,backend-reader}.json`에 root:root 0600, 부모 0700으로 보관했다. 발급 intent도 남겨 중복 발급 방지에 사용한다. secret 값은 문서·Git·명령 인자에 넣지 않았다. **발급·비공개 보관·Native 인증만 완료**했으며 RunPod/Backend runtime 주입·S3 업로드/readback·prefix 밖 거부·회전 경보·호스트 손실 복원은 아직 확인하지 않았다.

## 이번 로컬 구현

- [canonical_journal.py](../../src/voice_coach/backend_analysis/canonical_journal.py): private Linux SQLite checkpoint. 실행 association·원본 바이트/sha·upload intent·version reference·readback 상태·manifest bytes를 transaction/FULL sync로 보존한다. journal root는 미리 준비된 소유자 전용 0700, DB는 0600을 요구한다. mount의 실제 지속성과 filesystem 지원은 배포 전 따로 확인해야 한다.
- [canonical_evidence_store.py](../../src/voice_coach/backend_analysis/canonical_evidence_store.py): `put`에 필수 `on_uploaded` callback을 추가해 version 응답을 readback 전에 기록한다. 기록 실패를 무시하고 업로드 성공으로 처리하지 않는다.
- [canonical_retention.py](../../src/voice_coach/backend_analysis/canonical_retention.py): journal을 필수 인자로 연결한다. 첫 업로드 전 원본 set을 저장하고, 성공 여부가 불명확한 UPLOADING은 자동 PUT하지 않는다. 이미 받은 version은 동일 version 읽기로만 재확인한다. 취소 후 도착한 version은 보관하되 현재 실행을 다시 살리지 않는다.

이 journal은 **Backend VERIFIED receipt나 callback durable outbox가 아니다**. 프로세스 재시작 후 preflight 원본 재구성·receipt 등록/status client·result event/body 재전송 loop, subreaper executor, production factory는 여전히 연결해야 한다. core 생성 전 실패는 별도 서비스 실패 계약이며 이 preflight journal로 가장하지 않는다.

## 확인·잔여 조건

이번 확인 범위는 신규/변경 Python 파일의 구문 컴파일, 호출 지점·문서 상대 링크·whitespace 정적 확인, 위 운영 메타데이터/health/DB migration 읽기와 제한 키 발급·Native 인증이다. journal DB 생성·실행/장애 복구·B2 업로드·실제 추론 테스트·자동 회귀 테스트는 실행하지 않았다. Native 인증은 S3 권한 동작 검증 통과가 아니다.

남은 주요 항목:

1. 발급된 제한 키의 공급원/runtime 주입/회전/복원 확인.
2. AWS 실행 source provenance·DB 백업/복원 수단 확인. FE 대상 release 확인은 FE 변경·사용자 대상 새 기능 활성화 단계에 적용하며 서버 작업을 막지 않는다.
3. Backend receipt/검증 worker/typed ingestion/public projection/업무 연결, RunPod executor/전송 outbox/factory, FE 실제 소비 경로 통합.
4. 개발자의 직접 수용 확인 후 제한 v4 접수 및 확대. 새 approval 자체가 아니라 기술·수용 조건이 남아 있다.

원격 변경은 **root 전용 비밀 공급 디렉터리 생성과 제한 키 2개 발급·비공개 사본/intent 보관**이다. 서비스 중지/재시작, env 주입, DB migration, 객체 업로드/삭제, 기존 키 폐기, FE/Backend/RunPod 배포, v4 활성화는 하지 않았다. commit/push/PR도 미수행이다.

## 전체 배포 목표 후속 구현 — 근거 등록 경로

전체 서비스 키 주입·배포·v4 활성화 목표는 계속 진행 중이다. 아래는 그 선행 서버 구현이며 목표를 키 발급/기반 코드 작성으로 축소한 완료 기록이 아니다.

현재 대상 재확인: RunPod 관리 API는 `s8hb5b0l5k4qlx` RUNNING과 SSH `194.68.245.79:22132`를 반환했다. AWS 03:32:34 UTC에서 `alpha-backend.service` active, 운영 JAR SHA는 위 `0ad3f601…`과 같았다. 제한 키 JSON/intent 4개가 있고 `/etc/alpha-backend.env`에는 `ANALYSIS_CANONICAL_*` 이름이 없었다. 이 재확인은 실제 GPT 생성/품질·TTS QA가 아니다.

추가 구현:

- 양쪽 control v1.2에 닫힌 `evidenceReceipt`/`evidenceError` schema를 추가했다. 기존 readiness는 계속 disabled이며 기존 제어 응답/공개 API를 변경하지 않았다. [등록·조회 계약](../contracts/runpod_http_control_v1_2.md)에 URL·header·200/202·중복·오류·원본 바이트 SHA 경계를 명시했다.
- Backend 별도 worktree에 `CanonicalEvidenceController`, `CanonicalEvidenceRegistrationService`, `CanonicalEvidenceExceptionHandler`를 추가했다. 인증·claimed worker·현재 소유 관계·lease/deadline·파생 key namespace를 검사하고, analysis→receipt 잠금 순서에서 manifest 원문/SHA와 PENDING 작업을 기록한다. 동일 execution의 다른 manifest는 충돌이며 DB 잠금 중 B2 호출은 없다.
- 미적용 `V33__create_canonical_evidence_receipts.sql`에 원본 manifest/receipt/검증 작업 상태를 추가했다. 원본/식별자와 terminal 상태의 변경, DELETE/TRUNCATE를 막는다. V32 immutable registry만 참조하며 사용자/녹음 삭제로 cascade하지 않는다. **V32/V33 모두 이 작업에서 운영 적용하지 않았다.**
- Backend `CanonicalEvidenceSettings`/`CanonicalEvidenceReader`는 별도 namespace·역할 키·고정 HTTPS endpoint를 사용한다. ACL·정확한 version·MIME·AES256·길이·SHA를 확인하는 bounded read를 구현했다. 기본 S3Client bean을 추가하지 않아 녹음/TTS 주입을 변경하지 않는다. 설정 예시는 Backend ???? ??? `canonical-evidence.env.example`이며 아직 자동 로딩/운영 주입하지 않았다.
- RunPod [canonical_receipt_client.py](../../src/voice_coach/backend_analysis/canonical_receipt_client.py)는 정확히 저장된 manifest를 등록하고 인증된 상태를 조회한다. 응답 유실은 같은 bytes만 재전송하며 AI/GPT/PUT은 반복하지 않는다. association·worker·manifest SHA와 receipt ID를 대조하고 live VERIFIED 응답에서만 trusted receipt를 만든다.
- journal을 version 2로 확장해 receipt identity/첫 인증 VERIFIED 응답을 보관한다. 재시작 복구 때 원래 worker ID와 Backend 재조회가 필요하며 로컬 VERIFIED 기록만으로 실행을 되살리지 않는다.

확인: Backend `compileJava processResources` 성공(테스트 미실행), 신규 Python receipt client·변경 journal 구문 컴파일 성공, 양쪽 source와 Backend package의 control schema 바이트 SHA 일치:
`1829c6a623d874e6ca8f6addd108dd40d522a40d57cdeca162ade8c3e58cd874`.
JSON 및 local `$ref`를 정적으로 대조했다. SQL 실행, 실제 API 등록, B2 객체 읽기/쓰기, journal 복구, 자동 회귀 테스트는 수행하지 않았다.

다음 연결: Backend의 frozen 원본 의미 검증/queue consumer(현재 VERIFIED 생성 코드 없음), publisher registry 호출, typed v4 ingestion/후처리/public reader/업무 gate, RunPod의 subreaper executor·전송 outbox·factory/boot, 그 뒤 역할 키 주입과 단계별 서버 배포. Backend가 prepared WAV 길이에 대한 MFA 경계를 독립 대조할 때 사용할 서버 실측 frame/sample-rate 근거의 전달도 확정해야 한다. 현재 보관 association에는 SHA만 있고 실측 길이 필드는 없으므로 원래 녹음 길이를 prepared 길이로 대체하지 않는다.

이번 후속 구현에서는 원격 파일·서비스·키를 추가 변경하지 않았다. v4 접수는 계속 off이며 개발자의 직접 수용 근거 없이 전체 활성화 완료를 선언하지 않는다.

## 후속 구현·AWS 검증기 staging — 04:03 UTC

이 절은 위 근거 등록 구현 이후의 추가 작업이다. **전체 목표는 계속 진행 중**이며 서비스 키 주입·Backend/RunPod 실행 전환·v4 활성화 완료가 아니다.

### 의미 검증과 prepared 시간 근거

- [canonical_media.py](../../src/voice_coach/backend_analysis/canonical_media.py)를 추가했다. finished prepared WAV의 같은 no-follow descriptor에서 SHA와 실제 PCM 바이트 수를 읽고, 프레임 수·샘플레이트를 보존한다. 측정 도중 파일 stat 변경은 실패 처리한다. metadata는 private association에만 추가되며 기존 source durationMs/CTC 시간을 대용하지 않는다.
- bridge/result wrapper와 서비스 v4 schema의 association에 nullable `preparedAudio`를 연결했다. 같은 SHA/metadata를 preflight와 Backend 검증에서 대조한다. prepared 음원 재생 mapping·발화속도·조음 지속시간을 주장하지 않는다. frozen parent/input/output schema·manifest·prompt에는 변경이 없다.
- [canonical_verify.py](../../deploy/runpod/canonical_verify.py)는 Backend 전용 오프라인 검증 실행 파일이다. 고정 Python 3.12/jsonschema 4.25.1/rfc8785 0.1.4에서 R/S7 파일 pin과 frozen prepare를 검증하고, 길이 제한이 있는 stdin 원문 frame만 받는다. stdout은 상태/digest뿐이다. 원문 파일 임시 저장·GPT dispatch·모델 실행·네트워크·자식 프로세스 생성을 하지 않으며 AS/CPU/FD 제한을 건다. Python audit hook은 커널 보안 경계 전체를 대신한다고 주장하지 않는다.
- Backend `CanonicalSemanticVerifier`는 서비스 환경을 상속하지 않는 자식으로 검증기를 호출한다. B2/GPT/DB 키는 전달하지 않고 요청 원문 및 exact-version 원본 바이트를 전달한다. 결과의 request/manifest/callback 원문 SHA를 대조하고 전체 대기를 제한한다.
- `CanonicalEvidenceJobs`/`CanonicalEvidenceWorker`를 추가했다. 전용 단일 lane에서 DB 짧은 claim → 외부 exact-version 읽기/의미 검증 → 현재 실행/소유/lease/deadline 재확인 → VERIFIED를 수행한다. analysis→receipt 잠금 순서를 유지한다. verifier claim 만료 시 원본 읽기/검증만 재개하며 AI/GPT는 실행하지 않는다. 일시 실패는 최대 8회, 취소·stale·만료는 결과를 되살리지 않는다. legacy outbox/timeout scheduler와 별도다.

### 실제 AWS staging 관측

1. 03:55~03:57 UTC: AWS에 `/usr/bin/python3.12` 3.12.13과 venv 지원이 있음을 확인했다. 기본 `python3`은 3.9이므로 사용하지 않았다.
2. 03:58 UTC: 최초 `/opt/alpha/canonical/venvs/py312-jsonschema4251-rfc014` staging 환경을 생성했다. 후속 권한 점검에서 `/opt/alpha`가 UID 1000 소유인 점을 확인해 **최종 경로로 채택하지 않았다**. 이 비활성 사본은 삭제/서비스 연결하지 않았다.
3. 04:01:32 UTC: root 소유 `/opt/alpha-canonical/venvs/py312-jsonschema4251-rfc014`에 독립 환경을 설치했다. 기존 Backend Python/Java/TTS 환경은 변경하지 않았다. 설치 프로세스에는 명시적 최소 환경과 `PIP_CONFIG_FILE=/dev/null`, 고정 PyPI HTTPS index, binary-only 패키지를 사용했다. [pip 설정 우선순위](https://pip.pypa.io/en/stable/topics/configuration/)에 따라 기존 pip 설정 파일을 로드하지 않았다.
4. 고정 RunPod manifest의 core 작은 파일 86개와 S7 lock 파일 11개, manifest/lock 자체 및 검증 wrapper/schema를 포함한 **113개·1,608,568 bytes**를 root 소유 신규 digest-addressed release에 준비했다. 모델·사용자 미디어·env·키는 포함하지 않았다. 원본 byte hash를 대조했으며 RunPod frozen 원본은 수정하지 않았다.
5. 04:03:00 UTC: 배포 bundle의 파일 SHA/권한을 대조하고 AWS `ec2-user`로 `--check-installation`을 수행했다. 결과는 `READY`, R/S7/lock digest 모두 기존 pin과 일치했다. stdin 작업 입력·추론·GPT·B2 객체를 사용하지 않았다. 이는 **검증기 설치 readiness**이지 실제 receipt 처리·품질 QA·전체 서비스 readiness가 아니다.

검증기 staged release:
`/opt/alpha-canonical/releases/fb32b7e2d28e9f574178d8f8717086810c4cfbcbf8c4530dc50d3f14042574a3`

현재 Python 배포 의존성 관측: Python 3.12.13, jsonschema 4.25.1, rfc8785 0.1.4, attrs 26.1.0, referencing 0.37.0, jsonschema-specifications 2025.9.1, rpds-py 2026.6.3, typing_extensions 4.16.0, pip 23.2.1. 설치 report와 `installation.json`은 전용 venv 경로에 보관했다. core/모델 실행용 환경이 아니다.

확인 범위: 변경 Java `compileJava processResources` 성공(테스트 제외), Python 구문 컴파일, 중첩 `$id` resource를 고려한 schema local-reference 정적 대조. 초기 단순 root-only 참조 점검은 frozen 내장 `$defs/ErrorCode`에서 실패했으며 resource scope를 반영한 정적 점검으로 수정했다. schema 자체 오류로 보고하지 않는다. 양쪽 source/Backend package의 result-v4 SHA:
`7a00a4662badbf98939be7dcdde8a442543ab702c1b8d2e3c75fb9e9dd541d79`.

남은 연결: publisher/registry, v4 typed ingestion·원본 callback 대조·후처리/public view/업무 gate, RunPod subreaper executor·전송 outbox·factory/boot, 역할 키 공급/주입 및 Backend·RunPod 배포. DB V32/V33는 여전히 미적용이고 receipt worker는 운영 JVM에 아직 설치하지 않았다. FE 소비 경로 및 개발자의 직접 수용도 별도 남아 있다. 이번 원격 변경은 독립 검증 환경·소스 release staging뿐이며 기존 서비스 재시작·DB 변경·새 키 발급·원본 업로드·v4 gate 변경은 하지 않았다.

## 후속 Backend callback v4 수신·검증·저장 구현 (로컬, 미배포)

전체 목표는 서비스 키 주입·통합 배포·v4 활성화이며 아직 완료되지 않았다. 계약 보존 리팩토링 이후 현재 worktree를 다시 확인하고 Backend `feat/canonical-callback-v4-20260930` / base `b48db988…`에 다음 경로를 추가했다. 이번 후속 작업에서는 원격 상태를 재조회하거나 서비스·키·DB를 변경하지 않았다.

- 기존 `POST /api/internal/ai/analyses/{analysisId}/result`에 schemaVersion v4 분기를 추가했다. v1/v2/v3 DTO·ACK·오류 형식은 유지한다. v4는 BigDecimal로 schema를 검사하고 embedded `$id`의 local reference 범위를 보존한다.
- `CanonicalCallbackDocument`는 identity/상태/실패/결정/원본 식별/receipt 메타데이터를 typed 값으로 읽고 전체 frozen 본문은 private 원문 bytes로 보존한다. 구 `AnalysisWorkerResult`로 변환하지 않는다. 이 클래스 생성만으로 원본 검증 완료가 되지 않는다.
- `CanonicalCallbackInbox`는 원문 1 MiB 한도, raw SHA·JCS SHA, event/실행/worker 결속을 기록한다. 최초 등록 transaction은 503을 반환하기 전에 commit하며, 네트워크 작업을 analysis lock 안에서 실행하지 않는다. 동일 event+다른 bytes, execution의 다른 최종 event, 다른 실행의 event 재사용은 거부한다.
- `CanonicalCallbackVerifierWorker`는 전용 lane에서 저장 요청의 JPA 복호화 accessor, VERIFIED receipt, B2 exact-version 원본 및 고정 오프라인 검증기로 callback을 독립 대조한다. 전후 lease/deadline/취소 검사를 수행하며 claim 복구·최대 8회 의존성 재시도를 지원한다. 기존 core manifest를 등록한 실행은 뒤늦게 core가 없는 실패로 위장할 수 없다. AI/GPT 호출은 하지 않는다.
- 검증 중은 503 `DEPENDENCY_UNAVAILABLE` / `Retry-After: 1`이며 원래 event/body만 재전송한다. 이후 같은 POST에서 검증 완료를 확인하면 `CanonicalCallbackCommitter`가 원문·typed 상태·현재 포인터·구 API 안전 요약·캐시 후처리 outbox를 같은 transaction에 저장하고 나서 APPLIED를 응답한다. APPLIED의 동일 원문은 취소/노출 상태를 재확인한 뒤 DUPLICATE로 응답하며 lease 만료만으로 재확인을 막지 않는다.
- 새 `V34__create_canonical_callback_inbox_and_results.sql`은 callback inbox, immutable 결과, 현재 analysis profile/예정 결과 버전/결과 포인터, 대상별 cache effect outbox를 추가한다. 사용자/녹음 삭제가 근거에 cascade하거나 기존 삭제를 막는 FK는 추가하지 않는다. JSONB 대신 JSON과 BYTEA를 사용한 근거는 [현재 v6 계약](v6-unification.md)에 기록했다. migration은 실행하지 않았다.
- `AnalysisResult`의 canonical 종결은 점수·STT·selected phone·legacy coaching을 생성하지 않는다. retry/assignExecution은 profile/포인터를 새 세대에 맞게 초기화하고, fail/cancel은 현재 포인터를 지우되 현재 canonical profile은 남겨 timeout/실패를 식별한다. immutable 원문은 보존한다.
- canonical 이력이 있는 분석 및 이후 재시도는 기존 DETAIL/SESSION_RESULT/SEGMENTS 캐시를 우회하고 현재 소유·선택·삭제·취소 상태를 확인한다. `CanonicalResultEffectsWorker`는 commit 후 해당 사용자·analysis·session의 정확한 캐시 키 두 개만 재시도 가능한 방식으로 무효화한다. segments의 페이지 키를 wildcard로 지우지 않으며 해당 이력의 조회를 캐시 밖으로 우회해 기존 TTL로 만료시킨다. canonical 완료의 기존 segments API는 빈 페이지이고 feedback regenerate는 기존 409 `FEEDBACK_EVIDENCE_UNAVAILABLE`로 차단한다.

### 안전 검토로 보류한 후처리

자동 검토가 v4 완료 시 기존 `visualObjectKey`의 삭제 outbox 예약 코드 추가를 별도 승인 대상으로 차단했다. 차단된 패치는 적용되지 않았고, 사용자에게 **기존 영상 정리 정책의 v4 연결 승인**을 요청했다. 실제 영상·B2 객체·DB 행을 삭제하지 않았다.

현재 committer는 영상 또는 기존 legacy segments가 남은 경우 임시로 503 NOT_READY를 반환한다. 이 임시 제한은 최종 요구사항을 축소한 완료 상태가 아니며, 기존 영상 정리/segments 정합성 후처리가 해결되기 전 전체 활성화할 수 없다. 공유 캐시 전체 clear도 자동 검토에서 차단돼 적용하지 않았으며, 다른 사용자에게 영향을 주지 않는 대상 키 eviction과 세대별 cache bypass로 대체했다.

추가 설정 `ANALYSIS_CANONICAL_EVIDENCE_CALLBACK_ENABLED` 및 `...CALLBACK_VERIFIER_ENABLED`는 예시 파일에서 false다. 실제 source env에 주입하지 않았다. 공개 admission과 readiness도 여전히 disabled이며 수신 코드 존재만으로 지원 준비가 완료됐다고 광고하지 않는다.

검증은 Java 컴파일·resources packaging과 소스/계약 정적 대조 범위다. SQL migration, 실제 callback/receipt API, B2 원본 조회, Python 검증 작업, 자동 회귀 테스트·fixture·브라우저·추론 품질 QA는 수행하지 않았다. 신규 저장/조회 코드 배포에는 V32~V34가 먼저 필요하며 구 운영 DB에 JAR만 교체하면 안 된다.

최종 로컬 확인: `compileJava processResources --offline --no-daemon` BUILD SUCCESSFUL (24초). 공유 schema 6개는 AI/Backend source/package의 바이트 SHA가 모두 일치했고 control v1.2 설명 문서도 양쪽 동일하다. 변경 문서 4개의 상대 링크, 결과 INSERT의 24개 컬럼/placeholder 대응, `git diff --check`를 정적으로 확인했다. 이것은 DB migration 적용·실제 API 수용·품질 검증 결과가 아니다.

다음 연결: 영상/segments 후처리, canonical publisher/registry와 admission 전달, `/api/v2/analyses` typed allowlist/public actions 및 complete/시험/진도 gate, RunPod executor·원문 callback durable delivery·production boot, 제한 키 주입과 원격 배포/개발자 수용. commit/push/PR/원격 배포는 이번에도 미수행이며 전체 목표는 계속 active다.

## 후속 요청 발행·현재 결과·학습 완료 연결 (로컬, 미배포)

계약 보존 리팩토링 이후 전체 배포 목표를 계속 진행했다. 직전 작업은 실제 소스 변경·컴파일 근거가 있는 진행으로 분류한다. 이번에도 현재 worktree를 확인했으며 아래는 새 **로컬 구현** 기록이다. 운영 환경을 조회하거나 키·서비스·DB를 변경한 기록이 아니다.

### 요청별 프로필과 원자적 발행

- 내부 `AnalysisExecutionProfile`을 추가하고 controller → request/retry application → publisher로 전달한다. 기존 공개 body/응답과 Redis `AnalysisWorkerRequest`에 필드를 추가하지 않는다. header가 없으면 legacy, 명시적 v4는 별도 프로필이며 non-HTTP publisher는 canonical을 legacy로 낮춰 보내지 않는다.
- `RunPodOutboxAnalysisJobPublisher`는 canonical에 request v2/profile/result v4를 고정한다. 새 execution과 analysis profile, 기존 outbox, V32 immutable registry를 같은 transaction에 저장한다. JPA session/profile/outbox를 flush한 뒤 JDBC registry fence를 확인하며, flush는 commit이 아니다. registry 오류 시 전체 접수가 rollback된다. 저장된 원문 재전송 dispatcher를 그대로 사용한다.
- 최초 배포 범위는 계획대로 독립 음성 연습이다. 서버의 recording/session 관계로 영상, 클래스 step/education revision, 연결된 title exam, 비발음 focus, 품질 미통과, 기존 실행 덮어쓰기를 거절한다. 기존 영상/클래스/시험의 header 없는 legacy 경로는 유지한다. 이 초기 범위는 추후 수용 범위를 확장하는 전체 목표의 대체가 아니다.
- `CanonicalExecutionRegistryImpl`은 현재 analysis profile/예정 v4를 추가 대조한다. canonical claim/heartbeat도 registry의 현재 사용자·녹음·세션·content visibility를 재확인한다. 기존 요청 JCS digest, lease/deadline, worker 소유 검사는 유지한다.
- **공개 admission은 아직 닫혀 있다.** `TrainingAnalysisSchemaAdmissionService.selectProfile`은 기존 `assertAvailable`을 먼저 호출하므로 현재 v4는 계속 503이다. 전달 경로 구현을 활성화 완료로 보고하지 않는다. readiness·실행·공개 소비자 완성 뒤 이 gate를 연결해야 한다.

### 현재 결과와 업무 조건

- `CanonicalCommittedResultReader`는 현재 analysis 포인터/요청/실행/worker·recording/content/소유 관계, APPLIED inbox의 검증 완료, core가 있을 때 VERIFIED receipt, raw SHA와 JCS SHA를 대조한다. 취소·삭제·탈퇴·선택 변경 또는 이전 세대의 원문으로 fallback하지 않는다. raw BYTEA를 파싱하고 private diagnostics를 SQL JSONB로 변환하거나 공개하지 않는다. 이 reader 자체는 공개 endpoint가 아니다.
- `CanonicalCompletionEligibility`는 현재 COMPLETED + INLINE + ACCEPT + feedbackDeliveryAllowed + 검증 receipt + 정상 전달 분기만 허용한다. READY의 정상 표현/deterministic fallback, NO_PERMITTED_COACHING_CONTENT의 무후보 결과는 학습 **절차** 완료가 가능하다. REJECT/INCONCLUSIVE/FAILED/RETAINED_ONLY는 불가이며 발음 정상·만점 판정으로 바꾸지 않는다.
- `TrainingAnalysisReaderImpl`에 위 조건을 연결했다. `TrainingSessionWriterImpl.complete`는 기존 session lock을 잡은 뒤 다시 확인한다. 소스상 다른 세대에 대한 선행 조회만으로 완료를 허용하지 않는다. 기존 URL/body/DTO와 `ANALYSIS_NOT_COMPLETED` 오류 경계를 유지한다.
- `TitlePersistence`는 canonical을 점수 조회에서 명시 제외하고, 소유·시험 세션·content·선택된 COMPLETED 결과의 점수 불가 분기에 포함한다. 기존 `ANALYSIS_SCORE_UNAVAILABLE`을 사용하며 시험 점수·passed를 만들지 않는다. 과거 legacy 점수 규칙은 그대로다.

### 새로 확인한 남은 복구·소비 경계

현재 `VoiceRecordingWriterImpl.findMutableSession`과 upload/session 흐름은 RECORDING/UPLOADING에서만 녹음 변경을 허용한다. canonical REJECT/INCONCLUSIVE 후에는 session이 ANALYZING이므로, 계획의 **같은 세션 재녹음 복구**가 아직 실제 코드로 구현된 것은 아니다. 단순히 public `canRerecord=true`를 표시하거나 FAILED retry로 바꿔 우회하면 안 된다. upload/등록/선택/새 analyze의 세션 전이를 일관되게 연결할 후속 작업으로 남긴다.

클래스 progress API는 현재 analysisId/결과 결속 없이 클라이언트가 보낸 progressPercent/step을 갱신한다. 이번에 이 기존 API를 임의로 개편하지 않았으며 초기 canonical 접수에서 클래스 세션을 제외했다. 클래스 확대 전에 결과 소비/진도 명령 결속을 별도로 해결해야 한다. FE 완료·이력·실패·actions 연결과 `/api/v2/analyses` 공개 allowlist도 아직 남아 있다.

확인: 최종 `compileJava processResources --offline --no-daemon` BUILD SUCCESSFUL (24초). 공유 schema 6개는 AI/Backend/package 바이트 SHA가 동일하고, legacy `RunPodAnalysisJobRequest.from` 메서드는 base HEAD와 소스가 동일하다. `git diff --check` 및 수정 문서 로컬 링크를 확인했다. 실제 SQL/JPQL 실행·Spring 기동·API 호출·추론·테스트/fixture/브라우저 QA는 수행하지 않았다. 새 migration은 추가/적용하지 않았으며 V32–V34 적용 선행 조건은 그대로다.

미완료: 공개 view/actions·재녹음 복구·FE 실제 소비, 영상/segments 후처리, RunPod executor/내구성 있는 callback 전송/boot, 키 runtime 주입·Backend/RunPod 배포·개발자 수용·접수 활성화. commit/push/PR/원격 배포는 이번에도 미수행이다. 전체 목표는 축소하거나 완료 처리하지 않는다.

## 후속 canonical 공개 조회·actions 구현 (로컬, 미배포)

직전 요청 발행/업무 조건 연결은 소스 변경과 컴파일 근거가 있는 진행이었다. 현재 별도 Backend worktree(base `b48db988…`)와 FE standalone parser/client를 다시 읽고, 기존 사용자 API를 교체하지 않는 추가 조회 경로를 구현했다. 이 절의 변경은 로컬이며 원격 상태·키·DB·서비스 설정을 변경하거나 재관측한 기록이 아니다.

- `CanonicalAnalysisController`: **`GET /api/v2/analyses/{analysisId}`**. 기존 user JWT와 `ApiResponse` envelope, `Cache-Control: no-store`를 사용한다. 공개/내부 보안 matcher를 완화하지 않았으며 기존 `/api/analyses`의 COMPLETED 전용 동작·업로드·TTS는 유지한다.
- `CanonicalAnalysisQueryService`: 현재 소유/선택/삭제/세션/사용자/content와 실행 registry를 대조한다. PENDING/PROCESSING은 canonicalAnalysis와 serviceFailure가 null이고, FAILED는 유효 core 유무에 따라 원래 실패/근거를 반환한다. callback 전에 발생한 Backend timeout/전달 실패도 현재 canonical profile로 식별하며 고정된 공개 실패 코드만 사용한다. 과거 failureReason/내부 예외를 그대로 노출하지 않는다.
- `CanonicalAnalysisView` / `CanonicalPublicProjection`: public record allowlist를 작성했다. decision의 원래 reason_code/stage, scoped ID·G2P role·MFA 초(BigDecimal)·후보/근거 배열 순서·generation/fallback을 보존한다. JSON 전체 relay, ID 축약, CTC ms로 치환, 원본 시간축 replay, 임의 점수나 시각 교정 생성은 없다. coreInputValidation·raw diagnostics·sourceIdentity·object key/receipt/manifest/version 정보는 응답에 없다.
- coaching의 READY/비호출 전역 상태/FAIL_CLOSED를 서로 다른 public record로 표현한다. 필요한 null은 명시하고, 원래 branch에 없는 필드를 null로 추가하지 않는다. 후보 등의 공개 안전 subtree 9개 필드 집합을 공유 callback schema와 정적으로 대조했다. 브라우저에 전달하는 숫자 범위도 검사한다.
- `CanonicalViewVisibility`: 캐시를 사용하지 않고 조회 전후 현재 세대를 확인한다. 조회 중 request/execution/status/pointer가 바뀌면 409 `CANONICAL_ANALYSIS_CHANGED`, 숨겨진 결과는 404 `RESOURCE_NOT_FOUND`다. visible한 실제 legacy profile에만 404 `CANONICAL_ANALYSIS_NOT_FOUND`를 반환하며, unsupported profile/불일치/의존성 오류는 503이다. FE는 앞의 전용 legacy 404에서만 fallback할 수 있다. 이미 내려받은 복사본의 회수나 응답 이후 변경까지 막는 보장은 아니다.
- `CanonicalActionPolicy`: 실제 complete 조건, 기존 사용자 retry 최대 3회, session 상태, source scope, canonical admission/설정, 동시 실행 수를 읽어 action과 사유 배열을 만든다. retry 한도와 초기 scope를 명령 측 코드와 공유하도록 분리했다. GET은 동의/접수/재시도/GPT/B2 작업을 수행하지 않는다. action true는 조회 시점 힌트이며 실제 명령에서 권한·상태를 다시 검사한다.
- **같은 세션 재녹음 복구는 아직 미구현**이다. ANALYZING의 non-ACCEPT에 canRerecord=true를 위조하지 않고 `CANONICAL_RERECORD_RECOVERY_NOT_READY`로 표시한다. canonical admission도 off이므로 canonical retry는 불가 사유를 반환한다. 이 임시 기능 제한을 최종 활성화 완료로 간주하지 않는다.

Backend에 `docs/api/canonical-analysis-view-v1.md`를 추가하고 endpoint 목록/specification 및 architecture 문서의 오래된 미구현 표기를 갱신했다. FE의 standalone `canonical-analysis.ts` / `api/canonical.ts`와 필드·분기·오류/fallback 의미를 소스로 대조했으나 **실제 화면 entry point는 연결하지 않았고 FE 파일도 수정하지 않았다**.

확인: 최종 `compileJava processResources --offline --no-daemon` 성공(24초), 공유 schema 6개 AI/Backend/package SHA 동일, public-safe record/schema 필드 집합 9개 동일, Git diff 공백 및 문서 로컬 링크 확인. SQL/JPQL·Spring/Jackson 응답 직렬화·HTTP 요청·FE 화면·실제 추론·자동 회귀 테스트·fixture·브라우저 QA는 수행하지 않았다. 구문 컴파일/정적 계약 확인을 E2E 통과로 보고하지 않는다. V32–V34는 여전히 미적용이고 새 migration은 없다.

다음 연결: 같은 세션 upload/등록/선택/new analyze 복구, 실제 FE reader/실패·이력/actions 소비, 영상/segments 후처리, RunPod 실행·내구성 있는 callback 전송·boot/readiness, 새 검증기 bundle과 서비스 키 주입·Backend/RunPod 배포, 개발자 수용 및 v4 활성화. 이번에도 commit/push/PR/원격 배포는 미수행이며 전체 목표는 active다.

## 후속 같은 세션 재녹음 복구 (로컬, 미배포)

직전 턴은 기존 변경의 재검증으로 새 배포 진척은 없었다. 이번에는 현재 Backend worktree(branch `feat/canonical-callback-v4-20260930`, base `b48db988…`)의 업로드·등록·선택·analyze/retry 소스를 읽고 실제로 남아 있던 복구 경로를 구현했다. 원격 관측 시각/배포 revision을 갱신한 기록은 아니다.

- `CanonicalRerecordEligibility`는 현재 committed reader의 APPLIED/VERIFIED 원문·identity 확인을 재사용한다. 초기 독립 음성 범위의 INLINE/COMPLETED + REJECT 또는 INCONCLUSIVE, 일치하는 global coaching/NOT_DISPATCHED, 보관 receipt, 서비스 실패 없음일 때만 ANALYZING 세션 복구를 허용한다. core/LLM 판단·후보·prompt는 변경하지 않는다.
- 기존 `recordings/upload-url` 명령의 session 잠금 이후 현재 선택 analysis도 잠근다. 적격 결과가 정확히 하나일 때만 ANALYZING→UPLOADING으로 이동하고 기존 upload intent를 발급한다. 같은 transaction이므로 발급 실패 시 전이도 rollback된다. 등록/선택을 우회하거나 외부 업로드가 이미 성공했다고 가정하지 않는다.
- 기존 `recordings` 등록은 새 recording/attempt를 만들고 기존 select로 새 PASS 녹음을 선택한다. 이전 선택은 이 명시적 선택 전까지 유지하며 이전 analysis/callback/receipt/B2 근거는 삭제·덮어쓰기하지 않는다. 새 선택 후 과거 canonical 결과는 기존 visibility fence로 숨긴다. 업로드 포기는 UPLOADING에서 다시 발급할 수 있고 자동 분석/완료/재시도를 수행하지 않는다.
- `TrainingAnalysisRequestService`의 analyze/retry는 기존 user admission 잠금 다음에 session 선택 잠금을 얻고 입력 녹음을 읽는다. 선택이 바뀐 뒤 예전 녹음으로 요청을 발행하는 경합을 방지한다. select writer도 session 잠금 안에서 대상 소유·미삭제·PASS를 다시 확인한다. 잠금 순서는 user→session→analysis이며 callback commit은 session mutation 잠금을 역순 획득하지 않는다.
- GET `canRerecord`도 같은 eligibility를 사용한다. PENDING/PROCESSING, ACCEPT, FAILED, legacy, 미검증 결과 및 완료/취소 세션을 이 복구로 열지 않는다. FAILED의 기존 최대 3회 retry와 REJECT/INCONCLUSIVE의 COMPLETED 상태는 유지한다. admission off에서도 업로드는 복구할 수 있지만 새 canonical analyze는 계속 503이며 silent legacy downgrade는 없다.

기존 media controller·DTO·업로드/등록 application 소스는 base 대비 변경하지 않았음을 정적으로 확인했다. 새 HTTP 필드·status enum·migration·삭제 hook·전역 캐시 clear는 추가하지 않았다. Backend 공개 조회 문서와 architecture 기록, 통합 README/계획의 최신 현황을 갱신했다.

확인: `compileJava processResources --offline --no-daemon` **BUILD SUCCESSFUL (26초)**. 기존 Redis consumer의 deprecated/unchecked 컴파일 경고는 남아 있다. 소스상 호출/잠금 순서와 공유 schema의 global/NOT_DISPATCHED 값을 대조했다. SQL/JPQL 실행·Spring/HTTP·FE·실제 음원/GPT·자동 테스트·fixture·브라우저 QA는 수행하지 않았다. 개발자는 업로드 실패/포기/중복, 재선택과 동시 analyze/retry/cancel, 구 결과 보존/비노출, 비적격 결과 거부 및 신규 녹음 분석을 직접 수용해야 한다. 생성자/내부 port 변경에 따른 기존 테스트 코드 정비도 수행하지 않았다.

남은 전체 배포 작업은 실제 FE 소비 연결·복구 수용, 영상/segments 후처리, RunPod canonical 실행/내구성 있는 결과 전송/boot/readiness, 새 검증기 bundle·서비스 키 주입·V32–V34/Backend/RunPod 배포와 개발자 수용 후 v4 활성화다. 이번에 원격 서비스·키·DB·접수 flag는 변경하지 않았고 commit/push/PR/배포도 하지 않았다. 전체 목표는 계속 진행 중이다.

## 후속 callback outbox·재시작 전달 복구 (로컬, 미배포)

직전 재녹음 복구 턴은 코드 추가와 컴파일 근거가 있는 진행이었다. 이번에는 현재 main 및 별도 Backend worktree를 읽고, 다음 실제 배포 필수 경계인 결과 전송을 구현했다. 자세한 호출 순서·상태·수용 경계는 [callback 내구성·복구](callback-delivery.md)를 따른다.

- `canonical_journal.py` format v3에 `result_deliveries`를 추가하고 `CanonicalResultOutbox`를 작성했다. request 원문/원문 SHA/요청 JCS SHA, 고정 worker/revision·result event, 최대 1 MiB callback 원문/원문 SHA/별도 JCS SHA, send claim·시도·다음 시각·인증 ACK 원문을 같은 private SQLite transaction 경계로 보관한다. version 0~2에서 additive 초기화하는 코드이며 실제 운영 journal에 적용하지 않았다.
- `reserve`→기존 serializer→`enqueue`→전송의 순서를 명시했다. 원래 core가 journal에 있으면 이를 없는 실패로 바꿀 수 없으며 association/manifest/artifacts/receipt checkpoint를 대조한다. 이 로컬 checkpoint는 새로운 Backend receipt를 발급하거나 preflight·독립 검증을 대신하지 않는다. PREPARING 상태를 추론 재실행으로 복구하지 않는다.
- `CanonicalCallbackClient`는 기존 callback URL에 **저장된 raw bytes 그대로** 전송한다. JCS digest를 계산해도 해당 bytes로 재직렬화하지 않는다. 기존 worker·active predicate·현재 lease/deadline budget, 고정 HTTPS Backend·Bearer, redirect/proxy 비활성, 10초 HTTP·64 KiB 응답 한도를 사용한다. Retry-After/backoff를 보관하고 상한 초과는 조기 재시도하지 않는다.
- 재시작 `recover_once`는 저장된 원래 worker/request/execution으로 기존 heartbeat를 호출하여 live lease를 인증 응답으로 재확인한다. 새 claim/worker 교체/AI/GPT/업로드는 없다. 살아 있는 lease만 같은 결과 전송을 재개한다. 만료/종결·권한 부재는 확인 전용으로 전환하며 로컬 저장 lease를 권한으로 취급하지 않는다.
- Backend `POST /api/internal/ai/analyses/{analysisId}/result/ack`를 추가했다. 동일 v4 bytes·raw/JCS SHA, 현재 visible execution/worker, 검증 APPLIED inbox, immutable 결과/현재 포인터가 일치할 때만 기존 internal ACK shape의 `200 DUPLICATE`를 반환한다. 미등록은 404, 미적용/미검증은 503이다. inbox/결과 등록·검증 작업·새 적용·lease 갱신은 하지 않는다. 새 접수 off여도 기존 결과 확인은 가능하도록 분리했다.
- 확인 전용 전환은 지속 상태이며 다시 normal 전송으로 되돌리지 않는다. 최대 60초 확인 window 뒤 미확인으로 중단한다. claim token으로 경쟁 전송의 checkpoint를 구분하고, ACK 저장과 취소 정리 경합 시 확정 ACK를 PENDING으로 되돌리지 않는다. 기록 삭제·근거 삭제·원본 재생성은 없다.

공유 control v1.2에 기존 shape의 `resultAck`/`leaseResponse` 정의를 추가했다. root readiness는 disabled-only이고 기존 v1.1 schema·callback v1~v3·public FE 계약은 그대로다. 새로운 control v1.2 SHA는 `dbf747857ff253e9dc4893747f36646775cbf7c9796ad73702c26cf403f2e780`이다. 양쪽 계약 문서도 동일 bytes다. Backend specification의 오래된 public envelope 형태 callback 응답 예시는 실제 internal ACK 필드 설명으로 정정했다.

확인: Python 변경 3파일 `py_compile` 성공, Backend 최종 `compileJava processResources --offline --no-daemon` **BUILD SUCCESSFUL (22초)**, 앞선 Java 재컴파일도 성공(26초). 6개 공유 schema의 AI/Backend source/package SHA가 모두 같고 Git diff 공백을 정적으로 확인했다. frozen schema/manifest/prompt는 변경하지 않았다. 자동 테스트·fixture·SQLite/DB 동작·Spring/HTTP·재시작/장애 주입·실제 inference/GPT·브라우저 QA는 수행하지 않았다.

**아직 factory/runtime/startup scheduler에는 연결하지 않았다.** 현 로컬 runtime이 운영보다 오래된 Clova 코드도 포함한다는 사실을 다시 확인했으며 이를 통째로 운영에 덮어쓰지 않는다. canonical 503 guard는 그대로이고 실제 executor/subreaper·전 수명 heartbeat/cancel·boot/키 공급·새 검증기 bundle·FE 소비·개발자 수용·DB/Backend/RunPod 배포와 활성화가 남아 있다. 이번 원격 조회·키 주입·서비스/DB 변경·commit/push/PR/배포는 없었다. 전체 배포 목표는 여전히 미완료다.

## 후속 frozen 실행기·운영 저장소 조사 (로컬 구현, 원격 읽기 전용)

최신 요청의 실행기/부팅·FE·운영 키·배포·활성화를 위해 현재 Pod 관리 API와 SSH를 다시 확인했다. 2026-10-01 08:22 UTC 운영 sourceRevision은 여전히 `32d98e39767b56697bc7e58fb50e3e9d61432349`이며 supervisor의 api는 ready였다. 실제 frozen launcher/batch/blackbox host와 운영 HTTP runtime/prepare 소스를 읽었다. 기존 프로세스·설정은 변경하지 않았다.

`canonical_producer.py`, `canonical_process.py`, `canonical_process_guard.py`를 추가했다. 동결 launcher를 요청 한 건에 연결하고 private 출력 원문과 prepared WAV를 대조한다. 별도 session을 만드는 MFA 자손을 Linux subreaper·직접 자식 pidfd·waitid로 회수한다. core subprocess는 서비스 키를 상속하지 않는다. 이는 호출 가능한 내부 adapter이며 HTTP factory/boot/S7/B2/receipt/callback 전체 orchestration에는 아직 연결하지 않았다.

08:29 UTC mountinfo 확인에서 `/workspace`는 FUSE, `/opt`는 container overlay였다. 추가 ext4/xfs는 개별 시스템 파일 bind mount이며, 기존 설계의 private local persistent SQLite journal을 둘 전용 volume은 확인되지 않았다. 새 `canonical_filesystem.py`를 journal/producer 초기화에 연결하여 overlay/FUSE를 영구 로컬 저장소로 잘못 쓰는 것을 차단했다. [관측·구현·저장 경계 선택](executor-storage-gate.md)에 자세히 기록했다.

기존 AWS Backend PostgreSQL 내부 durable journal로 변경하거나 적격 private POSIX volume을 마련하는 결정이 필요하다. 기존 계획과 다른 저장 경계를 임의 적용하지 않았다. 이 상태에서 키만 주입하고 v4 접수를 열면 재생성 시 업로드/결과/ACK 상태를 잃을 수 있으므로 운영 변경은 진행하지 않았다. FE 연결·부팅/실행기 전체 연결·서비스 키 주입·배포·활성화는 미완료다.

확인: 변경 Python 5파일 `py_compile` 성공, Git diff 공백 및 수정 문서 로컬 링크 정적 확인. 자동 회귀 테스트·fixture·실제 프로세스 회수/추론·SQL/SQLite·브라우저 QA는 수행하지 않았다. commit/push/PR, 원격 키 주입·DB migration·서비스 배포·접수 활성화는 하지 않았다.

## 후속 승인된 PostgreSQL journal 전환 (로컬, 미배포)

사용자가 기존 AWS Backend PostgreSQL로 복구 기록을 옮기는 방향을 승인했다. 별도 POSIX volume 선택/승인 대기는 끝났다. 현재 worktree와 별도 Backend 브랜치/base를 재확인했고 다른 TTS 관련 미추적 문서는 건드리지 않았다. 이번 작업은 새 원격 상태 관측이 아니다.

- V35에 고정 event/request/worker/revisions와 one-shot start, B2 업로드 원문 인계 spool·intent·exact version/readback, ACK 원문, callback apply queue를 추가했다. 기존 registry/receipt/inbox와 결속하며 원본 정본은 B2다. PostgreSQL 관리자 자격증명을 RunPod에 전달하지 않는다.
- Backend 내부 journal controller/service는 기존 Bearer와 original worker를 요구한다. 일반 쓰기는 현재 visibility/lease/deadline을 다시 확인하고, 늦은 upload checkpoint는 기존 immutable identity·사전 intent에 한정된 쓰기 전용 경로다. 취소/탈퇴 후 조회·재실행을 열지 않는다.
- callback 검증 성공 시 server apply queue를 함께 발행한다. 별도 worker가 같은 bytes를 현재 lease 안에서 적용하며 AI/GPT/PUT/claim/heartbeat를 대신 실행하지 않는다. APPLIED ACK를 결과 적용 transaction에서 저장하고 GET ACK는 현재 APPLIED 결과를 확인한 뒤 DUPLICATE만 반환한다.
- Python `BackendCanonicalJournal`·`BackendCanonicalDelivery`와 `CanonicalJournalPort`를 추가하고 producer/retention/receipt를 원격 journal 경계로 변경했다. 프로세스 시작 marker는 실제 launch 전에 commit한다. 원격 I/O는 heartbeat loop 밖에서 수행하며 원문 바이트를 유지한다. SQLite 구현은 보존했지만 새 운영 fallback이 아니다.
- 원문 spool은 최대 5×16 MiB/실행, 별도 명시적 전역 artifact byte budget으로 제한한다. DECLARED metadata도 미리 예산에 반영한다. 자동 원본/임시 복사본/orphan 삭제는 넣지 않았다. B2 인계 후 spool 정리 정책과 전체 DB/WAL/백업 용량은 별도 배포 작업이며 무한 DB 용량을 보장하지 않는다.

새 공유 journal schema SHA는 `798aa2eee85f9196d0519763119f266b14f065899c03faa75599fab5fed2e5f5`다. frozen 및 기존 result-v4/control-v1.2 schema는 그대로다. [상세 구현·남은 gate](backend-journal.md), 양쪽 동일 [내부 HTTP 계약](../contracts/runpod_canonical_journal_v1.md)을 따른다.

확인: 변경 Python 7파일 구문 컴파일 성공. Backend 최종 `compileJava processResources --offline --no-daemon` **BUILD SUCCESSFUL (25초)**, 앞선 컴파일도 성공(56초/25초). 7개 공유 schema는 AI/Backend source/package SHA가 모두 같다. schema 내부 ref, Git diff 공백과 문서 링크를 정적으로 확인했다. 기존 deprecated API 경고는 남아 있다. 자동 테스트·fixture·SQL/trigger/HTTP·Spring 실제 기동·원격 DB·B2·추론/GPT·브라우저 QA는 수행하지 않았다. 생성자 변경에 따른 기존 테스트 코드 정비도 개발자 수용 범위다.

전체 작업은 미완료다. V32–V35 운영 적용·백업/복원/번호 충돌 확인, staging 용량·proxy body/time 한도, 실제 factory/boot/runtime·S7 포함 전 단계 연결, 원래 worker recovery context·unknown upload reconciliation, FE 소비·운영 키 주입·배포·개발자 수용·v4 활성화가 남아 있다. 이번에는 commit/push/PR·키 주입·DB migration·서비스 배포·접수 변경을 하지 않았다.
