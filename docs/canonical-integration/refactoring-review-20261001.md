# Canonical 통합 작업 재검토·계약 보존 리팩토링

2026-10-01 로컬 소스 기준. 사용자 요청은 현재까지의 작업 재검토와 API 계약을 유지하는 리팩토링이다. 이번에는 원격 배포·키 주입·DB migration·v4 활성화를 수행하지 않았다. [이전 운영 관측](rollout-execution-20261001.md)을 새 관측으로 갱신하지 않는다.

## 검토 기준

- AI: `main`, HEAD `cfe89ebca8131d0be46cb78b2b7dfd505d65bf63`와 기존 미커밋 변경.
- Backend: 별도 worktree `canonical-v4-be-20260930`, `feat/canonical-callback-v4-20260930`, HEAD `b48db988e7485943097acaa4c71308ef306175a8`와 기존 미커밋 변경. 원래 sibling 저장소는 편집하지 않았다.
- Frontend: 별도 worktree HEAD `d54ceae5065880ccabd8f00ffe83500ab9077dfd`의 비활성 canonical client/parser와, 로컬에 확보된 main `81b27dee88ae1c09bb4f9c2581be3552ac283137` 및 [호출자별 계약 검토](frontend-contract-review.md). 이번에 원격 refs나 실제 FE 배포 revision을 다시 확인하지 않았다.

## 재검토 결과

| 경계 | 확인한 구현 | 이번 결정 |
|---|---|---|
| FE → Backend | `/api/backend`, 기존 API envelope·동의 body·상태 polling·결과/segments·TTS binary 계약 | 기존 client/types/proxy와 공개 controller/DTO를 변경하지 않음 |
| 선택적 v4 접수 | `TrainingAnalysisSchemaAdmissionService`는 header가 없으면 기존 경로로 진행하고 v4 요청은 계속 차단 | 무조건 활성화하거나 전역 opt-in header를 추가하지 않음 |
| Backend → RunPod | request v1/v2 parser는 있지만 실제 `RunPodOutboxAnalysisJobPublisher`는 기존 요청 조립 경로 사용 | parser 존재를 canonical publisher 연결 완료로 해석하지 않음 |
| RunPod → Backend | 기존 callback DTO·v1/v2/v3 reader와 별도 v4 document/inbox/검증 worker/committer가 로컬에 존재 | v4를 구 DTO에 매핑하지 않음. 로컬 저장 구현과 운영 활성화를 구분 |
| 근거 검증 | B2 exact-version reader, offline verifier, receipt queue 기반 코드 존재 | receipt 검증과 callback 저장·공개 조회 완료를 구분 |
| 신규 FE | 별도 canonical reader/parser는 비활성 기반 코드이며 기존 화면 entry point와 미연결 | 기존 FE 무수정으로 canonical 전체 의미를 표시한다고 주장하지 않음 |

기존 API 호환은 URL뿐 아니라 응답 필드·null·상태·오류·인증·완료 부작용의 의미까지 포함한다. FE 실제 배포 URL은 서버 리팩토링/배포의 선행 입력이 아니다. 새로운 canonical 결과를 사용자에게 활성화할 때의 소비자 수용은 별도다.

## 수행한 리팩토링

### 1. private 문서 codec 분리

[canonical_codec.py](../../src/voice_coach/backend_analysis/canonical_codec.py)에 기존 `canonical_result.py`의 JSON 읽기/직렬화/schema 검증을 이동했다. journal, retention, receipt client와 오프라인 검증기는 해당 codec에서 직접 가져온다. 결과 조립 모듈은 기존 private import 이름의 호환 alias를 남겼다.

보존한 동작:

- UTF-8·중복 키·비유한 수·단독 surrogate 거부 및 기존 오류 코드.
- `Decimal` 기반 MFA 수치와 원본 바이트 SHA 의미. legacy HTTP/JCS parser와 합치지 않음.
- 문서 크기 제한, schema 파일/정의 선택, frozen prompt/정책/모델의 불변성.
- receipt의 인증·현재 실행 검사, journal 기록 순서, 재시도와 취소 동작.

### 2. Backend 실패 분류의 의존성 분리

Backend `analysis/infrastructure/canonical/EvidenceFailure.java`로 기존 B2 reader 안의 중첩 예외를 이동했다. B2 reader·offline verifier·worker가 같은 내부 실패 분류를 사용하되, 검증기가 예외 타입을 사용하기 위해 B2 reader 클래스에 의존하지 않게 했다.

`retryable` 판정과 `EVIDENCE_STORAGE_UNAVAILABLE` / `EVIDENCE_INVALID` 코드는 그대로다. 외부 DTO나 오류 계약을 변경하지 않았으며 SDK 예외 원문·원본 근거를 새로 노출하지 않는다. DB claim/lease/transaction/terminal 처리도 변경하지 않았다.

### 3. 다음 검증기 배포의 파일 누락 방지

[stage_backend_verifier_bundle.py](../../deploy/runpod/stage_backend_verifier_bundle.py)에 검증기 wrapper·Python 모듈·schema 14개 필수 경로 확인을 추가했다. 분리된 codec 없이 결과 조립 파일만 포함한 불완전 bundle은 원격 디렉터리를 만들기 전에 거부한다.

이 검사는 배포 입력의 파일 포함 여부를 확인하는 것이며 추론/품질 QA가 아니다. 이전 AWS staged release는 수정하지 않았다. **이번 리팩토링을 배포할 때는 `canonical_codec.py`를 포함한 새 digest-addressed release를 만들어야 하며**, 과거 staged release의 READY를 새 코드의 설치 readiness로 재사용해서는 안 된다.

## 2차 재검토와 추가 리팩토링

이 절은 같은 날 callback inbox/저장 구현이 추가된 이후의 로컬 재검토다. 앞선 codec·실패 분류 분리는 유지했다. 이번 추가 변경은 Backend 별도 worktree의 내부 코드와 문서에 한정하며 AI·FE 실행 코드, 동결 core/LLM, schema, migration, 설정값은 변경하지 않았다.

### 4. 두 검증 worker의 원본 읽기 경계 통합

Backend `analysis/infrastructure/canonical/CanonicalArtifactLoader.java`로 manifest schema 검사와 순서대로 원본을 읽는 중복 루프를 이동했다. `CanonicalEvidenceWorker`와 `CanonicalCallbackVerifierWorker`가 이를 사용한다.

- schema 검사 → 각 artifact 읽기 직전 실행 fence → 기존 exact-version reader 순서를 보존한다. B2 ACL·MIME·크기·SHA·암호화 검사는 원래 reader에 남긴다.
- 두 worker의 전용 스레드·claim·최종 fence·오프라인 의미 검증·finish·재시도 분기는 합치지 않는다. 네트워크 작업을 DB 트랜잭션 안으로 옮기지 않는다.
- 원본 배열 순서와 바이트를 유지한다. loader는 캐시·영구 저장·수신 ACK·VERIFIED 승격을 하지 않는다.
- pre-core 실패는 계속 원본 manifest/B2 읽기 없이 별도 검증 경로를 사용한다.

### 5. 내부 HTTP body 읽기 중복 제거

Backend `analysis/controller/RunPodRequestBody.java`로 callback/control과 evidence 등록의 media type·content encoding·bounded read를 추출했다. controller의 인증은 그대로 먼저 실행하며, evidence에만 필요한 worker header 검증도 유지한다.

- 경로, HTTP method, 응답 DTO, 200/202/503 및 오류 코드는 그대로다.
- 기존 control 64 KiB/result 1 MiB 제한과 `limit + 1` 읽기를 보존한다. 길이를 알 수 없는 본문의 초과 검사는 기존 parser가 계속 수행한다.
- JSON 재직렬화·원본 바이트 변경·인증 통합·전역 filter 추가는 하지 않는다. 공개 FE API와 TTS는 변경하지 않는다.

### 활성화 전 남아 있는 호환성 위험

| 우선순위 | 현재 소스 근거 | 필요한 후속 작업 |
|---|---|---|
| 활성화 차단 | `RunPodOutboxAnalysisJobPublisher`는 legacy 요청만 발행하며 `TrainingAnalysisSchemaAdmissionService`는 v4를 차단 | canonical publisher/registry·실행·전송을 연결한 뒤 별도 활성화 |
| 활성화 차단 | FE의 `/api/v2/analyses` 전용 client는 존재하지만 Backend 공개 canonical controller와 FE 화면 entry point는 미연결 | 공개 allowlist projection과 실제 소비 경로 구현; 기존 API 축소 안내를 전체 UX 호환으로 간주하지 않음 |
| 활성화 차단 | `TrainingAnalysisReaderImpl.existsCompletedAnalysisForSelectedRecording`는 job `COMPLETED`만 검사 | canonical REJECT/INCONCLUSIVE를 학습 성공으로 처리하지 않도록 완료·진도·시험 조건을 함께 연결 |
| 배포 순서 | 현재 `AnalysisResultReaderImpl`의 cache/visibility 경로가 V32를 조회하고 entity에 V34 필드가 있음 | v4 접수가 off여도 새 JAR만 먼저 배포하면 안 됨. V32–V34의 백업·적용·원복 계획 필요 |
| 후처리 미완료 | `CanonicalCallbackCommitter`는 영상 또는 기존 segment가 있는 결과를 `NOT_READY`로 차단 | 보류된 정리 정책/승인과 segment 조정을 해결하기 전 전체 결과 수용 완료로 보고하지 않음 |
| 개발자 QA 인계 | 기존 `InternalRunPodAnalysisControllerTest` 31행은 이전 4인자 생성자를 사용하지만 현재 controller는 이전 v4 작업으로 5개 의존성을 가짐 | 개발자 테스트 유지보수 필요. 이번에는 자동 테스트·fixture 생성/수정/실행을 하지 않았고 테스트 컴파일 통과도 주장하지 않음 |

이 위험을 없애기 위해 기존 공개 API 의미를 임의 변경하거나 미완성 v4 접수를 켜지는 않았다. 안전한 로컬 리팩토링과 신규 기능 통합은 별도 완료 조건이다.

## 확인 범위와 남은 항목

앞선 1차 확인:

- AI/Backend `docs/contracts`의 파일 21개가 이번 작업 시작 시점과 SHA-256 동일. 이번 리팩토링으로 wire schema를 바꾸지 않았다.
- 이동한 codec 함수는 이름과 동등한 기본 제한 상수명 치환 후 기존 소스와 동일함을 정적으로 대조했다.
- canonical Python 모듈 및 변경 배포 스크립트 11개 구문 컴파일 성공. 애플리케이션 모듈을 실행하지 않았다.
- Backend `compileJava processResources --offline` 성공. SDK deprecated API 경고는 남아 있다.
- 자동 회귀 테스트·fixture·브라우저 자동화·GPT/추론·B2 객체 작업·실제 사용자 요청 QA는 수행하지 않았다. 컴파일과 schema 동일성은 실제 E2E 호환성 통과를 뜻하지 않는다.

2차 확인: Backend `compileJava processResources --offline --no-daemon` 성공(24초). 실행 시점에 자동 테스트·fixture·브라우저 자동화·SQL 적용·실제 HTTP/B2/추론 요청은 없었다. 상세 정적 동일성 확인은 아래에 기록한다.

- 작업 직전 소스·계약 922개 파일의 SHA-256을 기록하고 이후 대조했다. 918개 동일, 위 controller/worker 4개 변경, helper 2개 추가, 삭제 0개다. 이 대조에 포함된 AI 분석 소스, FE `src`, 양쪽 계약 파일, Backend DTO·migration·설정은 동일하다.
- 공유 신규 schema 6개는 AI 원본 / Backend 원본 / `processResources` 결과가 바이트 단위로 모두 동일하다.
- 원본 읽기의 schema 선택·artifact 순서·각 read 직전 fence와 worker의 마지막 fence를 소스에서 대조했다. controller의 인증 → media type/encoding → 길이 제한 → bounded read → 기존 parser 순서도 유지했다.
- Git diff 공백 확인과 수정 문서의 로컬 링크 확인을 수행했다. Git의 기존 LF/CRLF 경고는 남아 있으며 광범위한 개행 변환은 하지 않았다.

이는 정적 계약 보존 근거이며 실제 HTTP 응답·동시성·SQL·Spring 기동·FE 동작의 QA 통과를 뜻하지 않는다.

미완료 기능: canonical publisher/registry 연결, 공개 projection/업무 gate와 결과 후처리, RunPod executor·durable callback delivery·factory/boot, 필요한 서비스 키 주입과 운영 전환, 새 화면 소비 경로와 개발자 수용. **v4 typed inbox/검증/결과 저장 코드는 이미 로컬에 추가됐으나 미배포 상태**이며 운영 완료로 간주하지 않는다.

기존 사용자 변경 및 루트 AGENTS의 충돌 표시는 임의로 정리하지 않았다. FE 파일은 수정하지 않았고, commit/push/PR/운영 배포는 수행하지 않았다.
