# RunPod AI API lease 갱신·종료 진단 수정 계획

작성일: 2026-10-03. 상태: **로컬 구현·구문 확인 완료, 운영 반영은 아래 실행 기록 참조**.
대상: canonical v4의 HTTP 부모 → 격리 executor → core 감시기 연결.
근거: [분석 57 장애 조사](incident-20261003-journal-wire.md).
전역·서비스 계층·외부 연동·문서화 규칙을 적용한다.

## 1. 문제와 목표

확인된 사실: AWS main `8985c4d` 배포로 기존 journal JSON 결함은 해소됐다.
후속 분석 57은 claim/start/heartbeat 성공 후 core 결과 생성 전에 종료됐다.
마지막 lease 갱신 약 0.23초 후 오류가 발생했고 당시 lease는 약 86초 남아 있었다.
executor 로그는 내부 예외를 감춰 직접 원인을 확정할 수 없다.

가설: `publish_lease`의 atomic replace와 불변 결과물용 `read_private_result`의
ctime 검사가 경합한다. `remaining()`이 판독 예외를 0으로 바꾸므로 정상 갱신이
실행 권한 상실로 오인될 수 있다. 이 요청의 실제 예외였다는 증거는 아직 없다.

목표는 정상 파일 교체로 인한 조기 종료 경로를 제거하고, 다른 원인의 실패도 단계와
고정 코드로 식별하는 것이다. 모든 음성 입력의 분석 성공이나 품질을 보장하는 계획은 아니다.

## 2. 변경 범위와 보존할 계약

- 공개 HTTP endpoint, request/result schema, Backend lease 발급 권한은 유지한다.
- MFA/core 모델·동결 R/S7·GPT 정책·B2 보관·Backend DB·FE·독립 TTS는 수정 대상이 아니다.
- HTTP route에 파일 감시나 비즈니스 판단을 추가하지 않는다.
- 기존 불변 산출물 reader의 무결성 검사를 완화하지 않는다.
- 현재 작업 트리의 journal 진단 변경은 미커밋·미배포다. 포함할 변경을 별도로 대조한다.

## 3. 구현 순서와 파일별 책임

| 순서 | 파일 | 예정 변경 |
|---|---|---|
| 1 | `src/voice_coach/backend_analysis/canonical_lease.py` (신규 예정) | lease 전용 snapshot 모델·판독·검증·발행 경계를 구현 |
| 2 | `canonical_lane.py` | Backend가 승인한 lease만 전용 publisher로 전달; 기존 atomic replace 유지 |
| 3 | `canonical_executor.py` | 불변 결과물 reader 사용과 예외→0 변환 제거; lease 상태·실행 단계 진단 연결 |
| 4 | `canonical_process.py` | 만료·deadline·상위 비활성화를 구분하고 기존 자손 회수 완료까지 대기 |
| 5 | `http_runtime.py` | 기존 종료 사유를 보존하고 고정 진단 코드·숫자 guard 종료 상태 기록 |
| 6 | `canonical_backend_journal.py` | 앞선 로컬 journal 응답 오류 분류를 함께 검토해 배포 범위 확정 |
| 7 | `deploy/runpod/build_canonical_overlay.py`, `deploy_canonical_runtime.py`, 필요 시 `prepare_canonical.py` | 신규 모듈 포함·변경 전 hash·복원·readiness 확인 경로 검토 및 필요한 부분 수정 |
| 8 | 배포 README·장애 기록·문서 목차 | 제안/구현/운영 관측을 분리하고 실제 revision·hash·확인 범위 기록 |

경로가 생기기 전에는 신규 모듈을 구현 사실로 문서화하지 않는다.

## 4. lease 판독 설계

### 4.1 신뢰 경계

lease 파일은 같은 Pod의 HTTP 부모가 쓰는 private IPC snapshot이다.
권한의 원천은 인증된 Backend heartbeat 응답이며 executor는 기간을 연장하지 않는다.
기존 `executionId`·`until` 두 필드와 monotonic clock 기준을 유지한다.
재부팅 전 snapshot이나 기존 workspace로 실행을 재개하지 않는다.

### 4.2 파일 교체와 내용 변조 구분

1. private workspace 디렉터리를 검증하고 디렉터리 FD 기준으로 `lease.json`을 연다.
   최종 파일에 `O_NOFOLLOW`를 적용하고 symlink를 따라가지 않는다.
2. 열린 FD의 regular file, 실행 UID 소유, 0600, 1~1024 bytes를 확인한다.
   이 FD에서 bounded read 후 파일 속성을 다시 확인한다.
3. 읽는 동안 속성이 변하지 않았으면 완전한 snapshot으로 검증한다.
4. 속성이 바뀌었다면 현재 경로의 device/inode와 열린 FD를 대조한다.
   경로가 다른 정상 파일을 가리키는 atomic 교체가 확인된 경우에만 현재 파일을 다시 연다.
   동일 inode의 내용/권한 변경은 정상 갱신으로 취급하지 않는다.
5. 재판독은 **총 3회, 전체 50ms 이내**를 초기 상한으로 제안한다. busy wait나
   sleep 반복을 넣지 않으며 각 시도 전후 monotonic deadline을 확인한다.
   이 한도는 새 lease나 유예 시간이 아니다. 상한 소진 시 고정 코드로 종료한다.
6. 이전 snapshot이 이미 만료됐다면, 현재 경로가 교체됐는지 같은 한도 안에서 확인해
   새 snapshot을 읽는다. 새로 검증된 lease가 없으면 만료를 유지한다.

OS의 동기식 파일 I/O 자체가 50ms 내 반환한다고 보장할 수는 없다. 판독 루프의
재시도 한도와 별개로 기존 부모 감시기·pipe 종료·hard deadline 경계를 유지한다.
실제 대상 filesystem에서 교체 의미와 읽기 비용을 개발자가 확인한다.

### 4.3 내용 검증과 실패 처리

- 중복 JSON key, 잘못된 UTF-8, 초과 크기, 누락/추가 필드, 다른 executionId를 거부한다.
- until은 bool을 제외한 유한 숫자여야 한다. 유효 기간은 `min(until, 원래 deadline)`이다.
- 기존 검증에 실패한 데이터를 캐시나 임의 기본값으로 대체하지 않는다.
- 정상 교체로 식별된 경합 외의 파일 부재·권한·JSON·identity 오류는 즉시 종료한다.
- 판독 오류를 `remaining=0`으로 숨기지 않는다. 유효 snapshot의 실제 만료와
  읽기 실패를 서로 다른 도메인 오류로 전달한다.
- 파일 판독 재시도는 분석/GPT/Backend POST/B2 PUT 재실행을 의미하지 않는다.

## 5. 발행·취소·종료 경계

- publisher는 같은 private 디렉터리의 새 0600 임시 파일에 전체 JSON을 기록·flush하고
  기존대로 atomic replace한다. 실행 중인 `lease.json`을 truncate/in-place 수정하지 않는다.
- 성공하지 못한 쓰기는 기존 snapshot을 유지하고 발행 실패를 고정 코드로 보고한다.
  해당 호출이 만든 임시 파일만 정리하며 workspace나 증거를 삭제하지 않는다.
- heartbeat·취소 처리는 HTTP 부모가 계속 소유한다. executor가 Backend에 새 lease를
  요청하거나 자체적으로 만료 시각을 뒤로 미루지 않는다.
- 실제 lease 만료, 전체 deadline, 사용자 취소, 부모 종료 시 기존 pipe/subreaper/pidfd
  자손 회수 절차를 유지한다. 정리 완료 전 공유 슬롯을 재개방하지 않는다.
- 정리 불확실성은 기존 admission 폐쇄 동작을 유지한다. 이를 일반 실패로 낮추지 않는다.

## 6. 원인 진단

private 로그에 허용된 stage, code, request/execution ID, 숫자 guard exit status와
필요한 시간 차이만 기록한다. 원문·경로·argv·키·provider 응답·임의 예외 문자열은 제외한다.

제안 stage: RESERVE, MATERIALIZE, CORE, EXPRESSION, PREFLIGHT, RETAIN, RECEIPT,
CALLBACK, CLEANUP. phase 변경 시 기록하고 오류에는 실패 시점의 phase를 보존한다.
정리 오류가 원래 오류를 덮지 않도록 primaryFailure와 cleanup 결과를 분리한다.

제안 고정 code:

- `CANONICAL_LEASE_READ_FAILED` / `CANONICAL_LEASE_INVALID`
- `CANONICAL_LEASE_REPLACEMENT_RETRY_EXHAUSTED`
- `CANONICAL_LEASE_EXPIRED` / `CANONICAL_DEADLINE_EXCEEDED`
- 기존 process cleanup·guard 종료 및 `BACKEND_JOURNAL_RESPONSE_INVALID` 코드

예외를 무조건 출력하는 대신 명시적 allowlist를 사용한다. 알려지지 않은 오류는
고정 INTERNAL_ERROR와 stage로 남긴다. 재시도 성공은 건수 등 최소 진단만 남기고
100ms 판독 루프마다 로그를 쓰지 않는다. 공개 실패 응답의 계약은 유지한다.

## 7. 검증·개발자 수용 기준

에이전트 수행 범위는 소스/계약 정적 대조, 필요한 Python 컴파일, bundle 구성·hash,
요청된 운영 readiness 조회다. 자동 회귀 테스트·fixture·브라우저 자동화는 생성·실행하지 않는다.

다음은 **개발자가 직접 확인할 수용 항목**이며 이번 계획에서 실행하지 않는다.

| 상황 | 기대 결과 |
|---|---|
| 판독 중 정상 atomic 교체 | 허용된 상한 내 새 snapshot을 검증하고 조기 종료하지 않음 |
| 새 snapshot 도착 전 실제 만료 | 유예 없이 중단·자손 회수 |
| 손상 JSON, 다른 executionId, symlink, 잘못된 권한 | 즉시 거부하고 구분된 고정 코드 기록 |
| 교체가 반복돼 한도 소진 | 무한 대기 없이 중단; 임의 lease 연장 없음 |
| 사용자 취소·부모 종료·전체 deadline | 정상 갱신과 관계없이 중단·자손 회수·슬롯 경계 유지 |
| 오디오/core/GPT/journal/보관 등 다른 단계 오류 | 실제 실패 stage와 원인 분류 보존 |
| 원래 JSON 오류가 있었던 journal GET | 배포 schema 검증 통과 유지 |
| 개발자의 실제 새 분석 | core → 보관 → VERIFIED receipt → callback/APPLIED → 공개 결과 확인 |

반복 실제 분석의 횟수·대표 입력은 개발자가 정하고 실행한다. readiness 200만으로
간헐 경합 해소나 전체 분석 성공을 선언하지 않는다. 한 사례 성공으로 모든 입력의
MFA 성공·피드백 품질·GPT 생성 성공을 일반화하지 않는다.

## 8. 배포와 원복

1. 구현 시 현재 Pod/주소, AWS JAR, RunPod base/overlay/개별 파일 hash를 다시 읽는다.
   과거 주소·PID를 현재 상태로 가정하지 않는다. 기존 사용자 변경을 보존한다.
2. 변경 파일과 신규 모듈이 실제 bundle에 포함됐는지 확인하고, 동결 core 및 비밀값이
   patch archive에 포함되지 않도록 범위를 제한한다.
3. 새 접수를 제어하고 진행 중 실행을 확인한다. 활성 실행을 끊는 무계획 재시작은 피하며
   정상 종료 또는 기존 취소·회수 경로 완료 후 유지보수에 들어간다.
4. 영구 overlay·manifest·runtime·metadata의 원복 사본과 해시를 확보한다.
   boot/manager/supervisor 재시작 관계를 제어하고 검토된 배포 helper로 적용한다.
5. 신규 모듈 포함 runtime/overlay hash, sourceGitRevision, import·bootstrap·readiness,
   AWS와 RunPod 양방향 API 및 기존 journal 응답을 확인한다.
6. 기동/정리/계약 확인 실패 시 새 프로세스를 정상 종료하고 전체 overlay/metadata를
   이전 버전으로 복원한다. 이전 버전에는 경합 위험이 남으므로 원복 시 canonical 신규
   접수 재개는 별도로 판단한다. pending/journal을 삭제해 실패를 숨기지 않는다.
7. 개발자의 실제 분석 수용 결과와 운영 관측을 구분해 기록한다. 실패한 분석 57을
   재사용하지 않고 정상 retry API로 새 실행을 만들며 DB 상태를 강제로 변경하지 않는다.

완료 기준: 구현·정적 확인·배포 파일 일치·운영 준비 확인·개발자 경합/종료 및 실제
분석 수용을 각각 충족했는지 보고한다. 최초 요청에서는 계획만 작성했고,
후속 사용자의 계획 수행 요청에 따라 아래 구현을 진행했다.

## 9. 구현 기록

- 신규 `canonical_lease.py`는 private directory FD와 O_NOFOLLOW로 snapshot을 읽고
  현재 경로가 다른 정상 inode로 교체됐을 때만 재판독한다. 동일 inode 변경·권한·JSON·
  identity 오류는 거부한다. 원래 불변 결과물 reader는 변경하지 않았다.
- 재판독은 최대 3회이며 50ms 이후 추가 open을 시작하지 않는다. OS scheduling/I/O
  지연으로 정상 첫 판독이 50ms를 넘었다고 별도 오류로 만들지는 않는다. 실제 lease와
  전체 deadline은 판독 후 다시 확인한다. 이는 계획의 시간 한도를 적용하는 구체적 방식이다.
- lease 발행은 부모가 계속 수행하며 executor는 기간을 연장하지 않는다. 임의 캐시나
  만료 유예는 없다. 정상 교체 재판독이 발생한 경우에만 건수를 로그에 남긴다.
- executor는 단계별 고정 event와 lease/journal 오류를 기록한다. 자원 정리 중 오류는
  primary failure를 덮지 않으며, 모든 등록된 정리 작업을 시도한다.
- process wrapper는 deadline과 비활성화를 구분하고 기존 pipe/subreaper 정리를 유지한다.
  HTTP 부모 로그에는 executionId·허용 코드·숫자 guard exit status를 남긴다.
- 배포 helper는 beforeSha256=null로 명시된 신규 파일에 대해 runtime/overlay 양쪽
  부재를 요구하고 백업·원복 시 신규 파일 제거를 지원한다. 기존 파일 hash 검사는 유지한다.
- 코드 7개(신규 모듈·배포 helper 포함)의 구문 컴파일과 diff 정적 확인을 수행했다.
  자동 회귀 테스트·실제 분석·품질 QA는 수행하지 않았다.
