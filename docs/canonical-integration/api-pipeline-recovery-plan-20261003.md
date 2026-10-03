# FE–AWS–RunPod 연결 복구·재발 방지 계획

작성: 2026-10-03. 상태: **계획, 미실행**. 근거는 [세션107 장애 조사](incident-20261003-session107-502.md)의 시각이 있는 관측과 해당 소스다. 계획 작성 중 원격 상태를 새로 조회하거나 배포하지 않았다. 실행 시작 시 실제 대상·revision·진행 작업을 다시 확인한다.

## 목표와 적용 순서

| 우선순위 | 목표 | 완료 조건 |
| --- | --- | --- |
| P1 | 새 채점 계약으로 접수 복구 | AWS verifier/JAR, RunPod 앱/core/H5, FE 계약 일치; 양쪽 실제 canonical 접수 가능 |
| P2 | 불완전한 자동 배포 방지 | 의존성 누락·drain 실패·readiness 불일치 시 배포 성공/접수 재개 불가 |
| P3 | 일시적 조회 오류에서 FE 회복 | 같은 분석을 제한 재조회하고 서버 FAILED와 연결 장애를 구분 |
| P4 | 업로드 Content-Length 경고 제거 | 브라우저가 금지 헤더를 설정하지 않으며 기존 업로드 무결성 검증 유지 |

P1에 필요한 최소 접수 차단·drain은 복구 때부터 적용하고, P2에서 이를 자동화한다. P3/P4 코드 준비는 독립적으로 가능하지만 운영 전환은 위 우선순위를 따른다. 현재 요청은 계획 수립이며 구현·배포 완료로 표현하지 않는다.

## 기준 상태와 보존 조건

- 마지막 관측: AWS JAR `1bdd87650192e21ec75e228d760b2008fed5ed61`, 새 점수 schema `e3fd7531e2465885ae27200f7534ed6e8857af7ca21e60d54eda834d389805f3`.
- AWS 활성 verifier는 이전 H5 묶음 `021e17aa…`; RunPod도 이전 H5 및 schema `11f7c609…`. 이 조합은 canonical readiness 조건을 충족하지 못했다.
- AI 구현 기준은 `2337cc292021974c812553d38b01ca13faf35ad5`. 새 core/manifest/lock 및 재현 명령은 [채점 구현 정본](scoring-implementation-20261003.md)에 고정돼 있다.
- Backend PR99와 FE PR40의 병합은 이전 조사에서 확인했다. FE의 dev 병합과 실제 Vercel 배포를 동일시하지 않는다.
- 세션107/분석61은 COMPLETED/ACCEPT다. 실패로 변경하거나 자동 재분석하지 않는다. 과거 결과, execution ID, receipt, callback 원본과 B2 자료를 유지한다.
- 채점 산술·하네스 정책은 이번 복구에서 다시 바꾸지 않는다. readiness 우회, 강제 성공 응답, 임의 점수, 중복 분석 POST를 추가하지 않는다.

## P1 — 호환 버전 묶음으로 복구

### 준비와 진행 작업 보호

1. AWS JAR·verifier 경로/해시, RunPod Pod ID·앱/overlay·core/H5 경로/해시, 공유 schema 9개, 실제 FE 배포 자산과 계약을 기록한다. env 값이나 인증 응답을 묶음에 넣지 않는다. 기존 Git 변경과 운영 파일을 보존한다.
2. 현재 배포 작업이 실행 중인지 확인하고 복구 동안 새 자동 배포가 끼어들지 않게 단일 운영 잠금을 확보한다. 실행 중 배포를 중간에 강제 취소하지 않는다. 잠금 획득 실패 시 활성화하지 않는다.
3. 재시작 없이 public 분석 생성 진입점만 임시 차단한다. 초기 복구는 AWS nginx의 검토된 maintenance include와 graceful reload를 사용한다. 확인된 POST 경로는 `/api/training-sessions/{id}/analyze`, `/api/training-sessions/{id}/analysis/retry`다. 다른 분석 생성 경로도 Controller/application 호출 관계로 조사해 같은 목록에 포함한다. 구체적인 nginx location 우선순위·직접 접근 경로를 사전 확인한다.
4. 차단 응답은 일시적 접수 불가임을 나타내는503과 Retry-After로 일관되게 만든다. 상태/결과 GET, heartbeat, claim, outbox의 이미 접수된 작업 전달, journal, evidence, callback은 계속 동작시킨다. Backend 환경변수 변경을 위한 재시작부터 하지 않는다. RunPod의 기존 접수까지 먼저 꺼서 대기 outbox를 고립시키지 않는다.
5. DB와 RunPod 양쪽에서 미완료 작업을 대조한다. 접수 트랜잭션·미전달 outbox, claim/실행 lease, 실행 프로세스, 진행 journal/upload/receipt, 검증·apply/effects·ack 대기를 포함한다. 완료된 이력 행이나 보관 파일의 존재는 미완료 건수에 포함하지 않는다. 각 테이블의 실제 상태 enum을 읽어 조회를 작성한다.
6. 제안 기본 drain 상한은20분이다. 5초 간격 관측에서 미완료가0인 상태를 연속2회 확인한다. 운영 부하를 고려한 작은 읽기 전용 집계만 사용한다. 상한 초과·소유권 불명확·상태 모순이면 전환을 중단하고 기존 프로세스를 유지한다. 강제 종료·강제 FAILED·lease 초기화로 통과시키지 않는다.

### 포장과 전환

7. 고정된 AI 소스에서 새 core/H5와 AWS verifier 묶음을 재현한다. source hash와 manifest/lock을 비교한다. AWS 묶음에는 기존 core/S7/H5와 새 scored-core/scored-H5 및 검증 앱 의존성이 모두 있어야 한다. Seungun 모델·reliability policy 경로와 런타임 의존성도 확인한다. 모델 추론 없이 확인한 범위는 구분해 남긴다.
8. AWS 새 verifier를 비활성 릴리스에 설치하고 실제 서비스 사용자·Python3.12로 `--check-installation`을 실행한다. 새 세 pin뿐 아니라 과거 pin도 모두 일치해야 한다. 전체 묶음 검증 전에 현재 경로를 교체하지 않는다.
9. AWS의 JAR·verifier 경로를 호환 쌍으로 설정하고 전환한다. 현재 JAR가 이미 대상과 같아도 verifier 경로를 읽는 프로세스의 재시작 필요 여부를 확인한다. RunPod가 아직 이전 schema라면 이 중간 단계의 전체 admission 미충족을 예상 상태로 기록하고 public 접수는 계속 막는다.
10. 새 점수·과거 null 점수를 함께 읽는 FE가 실제 Vercel에 배포됐는지 확인한다. 브랜치 병합 기록만으로 통과시키지 않는다.
11. RunPod 앱·공유 schema·파생 core/H5와 영구 overlay/부팅 설정을 같은 릴리스로 설치한다. 기존 실행이 없는 상태에서 운영 절차에 따라 supervisor를 전환한다. 런타임 파일만 교체해 다음 재기동에서 과거 버전으로 돌아가는 상황을 방지한다.

### 재개 조건과 복구

12. public 접수 차단을 유지한 채 내부 readiness를 검증한다. AWS는 RunPod의 executorConfigured와 schema를 확인하고, RunPod는 AWS readiness를 확인한다. 외부 maintenance 차단을 내부 지원 상태와 섞어 서로 접수가 켜지기를 영구 대기하는 순환 조건을 만들지 않는다.
13. 성공 조건: AWS verifier 모든 pin 일치, schema 9개 일치, AWS worker-readiness/v2 HTTP200 및 canonicalSupported/admission true, RunPod executorConfigured/admission true 및 reasonCode null. 15초 간격으로3회 확인한다. health/services나 HTTP200 하나만으로 대체하지 않는다. GPT 모델 접근·TTS 상태·실제 추론 결과는 별도 항목으로 보고한다.
14. 개발자 수동 QA 및 실제 FE 배포 확인을 인계한다. 정상 접수 재개와 실제 음성/GPT/callback/점수 QA 완료를 따로 기록한다. readiness 통과를 품질 검증 완료로 보고하지 않는다.
15. 실패 시 public 차단을 유지하고 마지막 호환 묶음의 JAR·verifier·RunPod 앱/overlay/core/H5 설정으로 함께 복구한다. 부분 롤백이나 JAR 하나만의 롤백을 금지한다. 이전 묶음은 원래 점수 null 계약이라는 한계를 명시한다. 새 채점 실행이 이미 발생했다면 그 결과를 읽을 수 있는 reader/verifier를 유지해야 하므로 구형 JAR로 무조건 되돌리지 않는다. 보관 데이터와 migration을 삭제·역적용하지 않는다. 복구 후 readiness가 검증된 경우에만 접수를 재개한다.

## P2 — 배포 파이프라인에 같은 절차를 고정

### 배포 단위

소스 commit과 배포된 artifact를 구분하는 release manifest를 추가한다(신규 설계). Backend SHA/JAR digest, AI SHA/app bundle digest, verifier bundle digest, core/H5 manifest·lock, schema 파일별 digest, FE 호환 revision 및 실제 배포 참조, 이전 호환 release를 기록한다. 비밀값은 포함하지 않는다. 동일한 묶음의 활성화는 중복 수행하지 않는다.

새 런타임/core 포장 기능을 Backend 셸 안에 복제하지 않는다. AI의 기존 builder/exporter/stager가 artifact를 만들고 배포 조정 단계가 그 digest와 설치 결과를 소비하도록 한다. RunPod 변경은 별도 운영 단계로 유지하되 같은 manifest와 완료 판정을 공유한다.

### 상태와 자동화

배포 상태를 `준비 → 검증 → 신규접수차단 → 기존작업완료대기 → 전환 → 호환성/readiness확인 → 접수재개 → 완료`로 기록한다. 모든 전환에 release ID·시각·대상·결과를 남긴다. 실패는 기존 프로세스 유지 또는 호환 묶음 복구로 연결한다.

- `.github/workflows/deploy.yml`: develop push의 빌드 흐름은 유지하되, activation 전에 manifest 및 모든 대상 artifact의 존재·무결성을 요구한다. 의존성 누락이면 운영 파일을 건드리기 전에 실패한다.
- `scripts/deploy.sh`: JAR만 교체하는 로직을 위 상태 전환으로 수정하고 호스트 잠금도 사용한다. CI와 수동 배포가 같은 잠금 규칙을 따라야 한다. 대기 중 더 최신의 호환 배포가 있는 경우 오래된 미활성 작업은 생략하되 이미 전환 중인 작업을 취소하지 않는다.
- health 판정에 verifier/schema/양쪽 canonical 본문을 포함한다. maintenance 동안 의도된 public503은 장애로 오인하지 않되, 내부 readiness 불일치를 성공 처리하지 않는다. 실패 이유는 `검증 묶음 누락`, `schema 불일치`, `drain 미완료`처럼 구분한다.
- maintenance 경로 목록과 일시503 계약을 버전 관리하고 인증·상태조회·callback 경로가 차단되지 않는지 정적으로 확인한다. 향후 새 분석 생성 경로가 생기면 이 목록도 함께 갱신한다.
- 실행 중단 뒤 재실행은 현재 stage·활성 digest·미완료 작업부터 조회한다. 불명확한 원격 변경을 무조건 반복하지 않는다. 오래된 잠금을 자동 삭제하거나 실패 직후 무조건 접수를 열지 않는다.

이 단계는 분석을 보호하는 유지보수 배포다. 단일 Spring 프로세스 restart의 짧은 GET 공백까지 제거하는 무중단 배포를 완료했다고 표현하지 않는다. 별도 인스턴스 전환은 DB/worker 중복 실행·lease 경쟁을 설계해야 하므로 이번 최소 복구 범위에 추가하지 않는다. 상태 조회 공백은 P3에서 회복한다.

## P3 — FE가 일시적 조회 오류에서 회복

### 오류 정책(제안 기본값)

- 적용 범위: 기존 분석의 상태 GET. HTTP502/503/504, 명시적 NETWORK_ERROR, 요청 TIMEOUT만 재시도한다. 응답 schema 위반·소유권 오류·사용자 취소·인증 세대 변경을 네트워크 오류로 오분류하지 않도록 API client의 오류 매핑도 확인한다.
- 대기: 1→2→4→8초, 이후 최대8초, ±20% jitter. Retry-After가 있으면 남은 예산 내에서 반영한다. 연속 장애 허용 시간은90초, 전체 기존 폴링 제한은10분으로 유지한다. 각 요청 timeout도 남은 예산 이내로 제한한다. 정상 상태 응답으로 연속 장애 카운터만 초기화하며 전체 deadline은 늘리지 않는다.
- 401은 기존 토큰 갱신 규칙까지만 적용한다. 최종401/403, AUTH_SESSION_CHANGED, REQUEST_ABORTED, 요청/계약 오류는 자동 재시도하지 않는다. 서버의 FAILED는 실제 작업 실패로 처리한다.
- 같은 sessionId/analysisId와 마지막 확인 진행률을 유지한다. 화면에는 “연결 복구 중”을 표시한다. 90초 또는 전체 제한 초과 시 “분석 상태를 확인하지 못했습니다”와 기존 “상태 다시 확인” 동작을 제공한다. 실패 기록·진행률100·새 분석 POST를 만들지 않는다.
- 화면 이탈/로그아웃/대상 변경 시 진행 GET와 대기 타이머를 중단하고 늦게 온 결과를 무시한다. 기존 입력·완료 가능 여부 및 canonical actions 검증을 유지한다.

책임: `src/lib/analysis-polling.ts`가 재조회·예산을 담당하고 `src/lib/api/client.ts`는 HTTP/취소/계약 오류를 구분한다. `src/components/practice-session.tsx`는 전달받은 연결 상태를 표시한다. UI에 HTTP 재시도 로직을 넣지 않는다. 프록시는 upstream 상태를 보존하고 필요한 Retry-After를 전달한다. 프록시 계층에서 POST를 자동 재전송하지 않는다.

완료 조건: 개발자 수동 확인에서 약40초의 상태조회503 뒤 동일 분석의 COMPLETED를 받아 결과를 표시한다. 서버 FAILED, 로그인 변경, 화면 이탈,90초 초과도 각각 다른 경로로 처리한다. 중복 analyze/retry POST는0회여야 한다. 실제 서비스 재시작으로 검증하기 전에 개발자 환경에서 수행한다.

## P4 — 업로드 헤더 계약 정리

1. Backend `S3PresignedUrlProvider.requiredHeaders`와 기본 `PresignedUrlProvider`에서 브라우저가 설정할 Content-Length를 제외한다. Content-Type 및 허용된 필수 서명 헤더는 유지한다.
2. FE upload client는 대소문자 구분 없이 Content-Length 등 브라우저 금지 헤더를 거른다. 허용된 `x-amz-*`를 일괄 제거하지 않는다. 오래된 Backend 응답에도 경고가 발생하지 않도록 방어한다.
3. presigned URL의 실제 signed headers와 Blob 본문을 대조한다. 현재 S3 서명 요청의 contentLength나 Backend 파일 크기·MIME·소유권·digest 검증을 이번 경고 해결을 이유로 제거하지 않는다. 서명 계약 변경이 필요하면 관측 근거와 함께 별도로 명시한다.
4. 완료 조건: 개발자 브라우저 수동 업로드에서 unsafe-header 경고가 없고 업로드·등록·선택이 성공한다. 잘못된 크기/MIME·만료 서명은 기존 정책대로 거절돼야 한다.

## 파일별 수정·인계 범위

아래는 기존 파일의 예정 수정 위치이며 아직 구현되지 않았다. 신규 manifest/배포 보조 파일은 구현 단계에 명시적으로 추가하고 directory 문서를 갱신한다.

| 저장소 | 기존 파일/위치 | 예정 변경 |
| --- | --- | --- |
| AI | `deploy/runpod/build_canonical_scored.py`, `export_canonical_verifier_frozen.py`, `stage_backend_verifier_bundle.py` | 재현 가능한 포장·배포 묶음 검증에 사용; 복구에 필요한 결함만 수정 |
| AI | `deploy/runpod/prepare_canonical.py`, `canonical_verify.py` | 대상 경로·pin·설치 검사·영구 배포 설정 확인 |
| AI | `src/voice_coach/backend_analysis/http_runtime.py`, `canonical_settings.py` | 양쪽 schema/admission 확인과 장애 사유 분리 검토; 필수 검증 완화 금지 |
| Backend | `.github/workflows/deploy.yml`, `scripts/deploy.sh` | 호환 artifact 사전 검증, 접수 차단/drain, stage 기록·잠금, 성공/복구 조건 |
| Backend | `CanonicalBackendReadiness.java`, `CanonicalSemanticVerifier.java` | readiness 실패 원인의 안전한 구분·운영 진단; 정책 우회 금지 |
| Backend | `TrainingAnalysisSchemaAdmissionService.java` 및 생성 use case | 접수 차단 범위·새 작업 진입점 누락 검토 |
| Backend | `PresignedUrlProvider.java`, `S3PresignedUrlProvider.java` | 브라우저 requiredHeaders 수정 |
| FE | `src/lib/analysis-polling.ts`, `src/lib/api/client.ts` | 상태 GET의 제한 재시도·오류 분류·취소·헤더 필터 |
| FE | `src/components/practice-session.tsx`, API 프록시 route | 연결 복구 상태 및 조회 재개; 필요 시 Retry-After 전달 |
| 공통 | 운영 배포 문서·API 명세·공유 schema | 실제 바뀐 응답/설정만 동시 갱신; reason enum 확장 시 양쪽 함께 반영 |

Backend는 최신 develop, FE는 최신 dev 기준 새 브랜치/PR로 인계한다. 이미 병합된 PR을 수정 완료의 대체로 사용하지 않는다. AI 구현 commit, 운영 artifact digest, 실제 FE deployment를 별도로 기록한다. 런타임 수정·JAR 배포·FE 배포·RunPod 배포 완료 여부를 각각 보고한다.

## 확인 범위

에이전트: 관련 Python 구문·Java compileJava·TypeScript noEmit, 공유 schema 메타/byte/hash 검증, shell/YAML 및 문서 정적 검토, 요청된 운영 상태 조회. 저장소 지침에 따라 자동 테스트·회귀 fixture·브라우저 자동화는 실행하거나 생성하지 않는다.

개발자: 실제 음성/GPT/Seungun/callback/점수, 과거 결과 조회, 접속 복구 UI, 업로드와 재시도 수동 QA. 코드 컴파일, API liveness, canonical readiness, 실제 분석·점수·품질 QA를 각각 분리해 기록한다.

이번 계획 작성의 파일 변경은 이 문서와 문서 목차뿐이다. 실행 코드·설정·서버·DB는 변경하지 않았고 commit/push/PR·배포도 수행하지 않았다.
