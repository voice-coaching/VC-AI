# 2026-10-03 최근 음성 분석 실패: Backend journal JSON 응답

## 범위와 관측

사용자 요청에 따라 Pod `s8hb5b0l5k4qlx`에 SSH로 접속했다. 사용자가 제공한
현재 Connect 주소와 컨테이너 hostname `590a2bc87e07`을 대조했다.
최초 원격 시각은 2026-10-02 15:54:58 UTC (10월 3일 00:54:58 KST)이며,
이후 같은 세션 조사에서 최근 분석 53·54의 파일과 Backend journal GET을 읽었다.
RunPod 관리 API는 제공된 키에 401을 반환했다. 키는 파일에 저장하지 않았다.

| 분석 | 녹음 | executionId |
|---|---|---|
| 53 | 63 | `89380932-e1a0-4707-ac50-6be38eed261d` |
| 54 | 64 | `a1270369-be4e-4132-9c18-22c4f2b5ef10` |

두 실행 모두 canonical/host exit code 0, batch report COMPLETED,
MFA transport close exit code 0·survivors `[]`를 확인했다.
최종 executor 로그는 두 실행 모두 `canonical_executor_unavailable`였다.
이는 core 실행 종료의 증거이며 개인 발음 판정이나 품질 QA 통과를 뜻하지 않는다.

## 확인된 API 연결 결함

RunPod의 기존 callback 인증과 각 실행의 worker ID로 Backend journal status를
읽었다. 두 응답 모두 HTTP 200이지만 association과 artifact metadata가 실제
계약 JSON 대신 `nodeType`, `object`, `array`, `containerNode` 등의 bean 속성을
포함했다. 근거 5개는 모두 PREPARED, receiptId는 null이었다.

배포된 journal client는 prepare/stage 후 status의 `journalSnapshot`을 검증한다.
따라서 잘못된 중첩 JSON은 schema에서 거부되어 후속 B2 보관·receipt·callback으로
진행하지 못한다. 로그의 포괄적 오류명만으로 GPT나 음성 모델 장애로 판단하면 안 된다.

이 현상은 [분석 51의 JSON wire 결함](testvideo1-verification-20261002.md)과 같다.
기존 Backend 수정 `c036312678acaee84e617fbbdf64f2edb263f539`를 로컬에서 재확인했다.
`CanonicalJournalController` reserve/status와 `CanonicalEvidenceController`
register/status는 `RunPodContract.encode`로 검증한 UTF-8 bytes를 application/json으로
반환한다. [develop 대상 PR #95](https://github.com/voice-coaching/VC-BE/pull/95)가
기존 인계 위치다. 이번에는 원격 PR 상태나 운영 JAR SHA를 확인하지 않았지만,
현재 관측한 운영 응답에는 수정 효과가 반영되지 않았다.

## 이번 로컬 보완

- `canonical_backend_journal.py`: 응답 snapshot의 schema 불일치를
  `BACKEND_JOURNAL_RESPONSE_INVALID`로 구분한다. schema 로딩 오류와 identity 검증은
  기존대로 유지하며 잘못된 JSON을 수용하거나 재구성하지 않는다.
- `canonical_executor.py`: 위 고정 오류 코드와 journal HTTP 실패 코드·상태 숫자를
  private 진단 로그에 남긴다. 응답 본문·원문·토큰·임의 예외 문자열을 추가하지 않는다.

이 보완은 원인 식별을 위한 것이며 Backend 직렬화 수정의 운영 적용을 대신하지 않는다.
계약·인증·lease·deadline·재시도 정책은 변경하지 않았다.

## 검증 및 인계

이번 확인은 원격 파일·인증된 journal GET의 읽기 전용 조사와 로컬 Python 구문 컴파일,
diff 정적 확인이다. 실제 분석 재실행, GPT 호출, 자동 회귀 테스트, 브라우저 QA,
서비스 재시작, 배포, commit/push, PR 추가 생성은 하지 않았다.

Backend는 저장소 지침에 따라 develop PR로 인계하고 담당자가 검증·배포한다.
담당자는 PR #95의 선행 rollout 포함 여부와 대상 revision을 대조한 뒤 적용하고,
중첩 JSON이 채워진 journal/receipt 응답을 확인해야 한다. 이후 개발자가 정상 retry
API로 새 실행의 보관·callback 완료를 확인한다. 실패 실행의 journal·lease를 초기화하거나
DB 상태를 강제로 되살리지 않는다. RunPod 진단 보완은 아직 로컬이며 별도 배포가 필요하다.

## PR 병합 후 실패 제보 재조사 — 10월 3일 01:17 KST

사용자가 Backend PR 병합 후에도 실패했다고 제보하여 같은 Pod를 다시 읽었다.
원격 관측 시각 2026-10-02 16:17:07 UTC (10월 3일 01:17:07 KST),
RunPod의 실제 callback 대상은 `https://api.voice-coaching.site`였다.

| 분석 | 녹음 | executionId | executor 오류 파일 갱신 시각 (KST) |
|---|---|---|---|
| 55 | 65 | `37e90fa9-4486-4783-a896-073e93c85dbb` | 01:01:06 |
| 56 | 66 | `194237f3-247c-4b7d-97c9-35e147e90e40` | 01:04:37 |

두 실행 모두 core/host exit 0, batch COMPLETED, MFA close exit 0·survivors `[]`였다.
executor 로그는 `canonical_executor_unavailable`, callback.json/completed.json은 없었다.
현재 journal GET은 HTTP 200이지만 두 요청 모두 association/metadata에 여전히 bean
속성을 반환했다. artifact 5개는 PREPARED, manifestSha256/receiptId는 null,
callbackStatus는 ABSENT였다.

분석 56의 현재 GET 응답을 Pod에 배포된 journalSnapshot schema와 정적으로 대조했다.
association의 anyOf 및 artifacts[*].metadata의 additionalProperties/required/oneOf
위반을 확인했다. 오류 본문이나 사용자 원문은 출력·보관하지 않았다.

결론: 최신 실패도 같은 journal JSON wire 결함과 일치하며, 현재 호출 대상의 운영 응답에
수정 효과가 나타나지 않는다. PR 병합 시각·CI/CD 결과·실행 중 Backend JAR SHA는 이번
조사에서 확인하지 않았다. 미배포, 이전 프로세스 잔존, 다른 인스턴스/대상 배포 중 어느
경우인지 단정하지 않는다. 담당자는 병합 SHA에서 생성한 JAR, 실제 프로세스의 JAR,
`api.voice-coaching.site`가 연결하는 인스턴스를 대조해야 한다.

이번 재조사는 로그·상태 GET·응답 계약 대조와 이 기록 갱신만 수행했다.
소스 추가 수정, 분석 재실행, 자동 회귀/브라우저 QA, 서비스 재시작·배포는 하지 않았다.

## 사용자 직접 배포 요청에 따른 AWS main 배포 완료 — 01:27 KST

사용자가 AWS 접속과 Backend main의 직접 배포를 명시적으로 요청했다.
AWS `13.209.184.226`의 `alpha-backend.service`는 배포 전에도 기존
`canonical-active-7f9f4c...jar`를 실행 중이었고 프로세스 시작 시각은
2026-10-02 02:32:12 UTC였다. PR 병합이 실행 JAR 교체로 이어지지 않은 상태였다.
CI/CD가 배포하지 않은 구체적 이유는 이번에 조회하지 않았다.

원격 main을 fetch하여 `8985c4debddd0c098c6cf0bb18a1d86cd3acf4af`를 확정했다.
main은 PR #96, develop은 PR #95 병합을 포함하며 hotfix `c036312`와 Git tree가
`2c56d9ce9271497ad7d4ee4f38e91b8505c2af76`로 동일하다.
해당 main의 별도 archive에서 Java 21 `bootJar -x test --no-daemon`을 수행했다.

| 항목 | 확인값 |
|---|---|
| 대상 소스 | `8985c4debddd0c098c6cf0bb18a1d86cd3acf4af` |
| 새 JAR SHA-256 | `a0d0b40581b0d1a13fa4a1f7b5e6b848002e9a325d3a04ee4971cfb6b9777c87` |
| 실행 경로 | `/opt/alpha/app.jar` → `/opt/alpha/releases/8985c4debddd0c098c6cf0bb18a1d86cd3acf4af.jar` |
| 이전 원복 JAR | `/opt/alpha/releases/canonical-active-7f9f4c988c81fe7e0c73ba0fce52db1db1482519f43fb39fcc0b08c50073ce12.jar` |
| 새 프로세스 | PID 702162, systemd 시작 2026-10-02 16:25:49 UTC |
| 최종 AWS 관측 | 2026-10-02 16:27:33 UTC / 10월 3일 01:27:33 KST |

운영/새 JAR의 모든 entry를 비교했다. 실제 바이트 내용 변경은
CanonicalJournalController.class와 CanonicalEvidenceController.class뿐이다.
V33–V36 SQL은 줄바꿈만 다르며 라이브러리·나머지 클래스·리소스는 동일하다.
기동 시 Flyway 38개 migration 검증 성공을 확인했다.

main의 기존 `scripts/deploy.sh`로 이전 JAR을 보존하고 교체·재시작했다.
최초 시도는 CRLF로 `set -Eeuo pipefail`에서 서비스 변경 전에 종료됐다.
실행용 스크립트 사본만 LF로 정규화하고, 이전/대상 JAR 해시를 다시 확인한 뒤
재실행했다. internal health와 public API docs 확인 후 배포 스크립트가 성공 종료했다.
스크립트 실패 원복은 준비돼 있었으나 이번에는 원복하지 않았다.

RunPod 관측 16:27:07 UTC에 실제 `https://api.voice-coaching.site`의 분석 55·56
journal GET은 모두 HTTP 200 및 배포된 journalSnapshot schema 검증 통과였다.
기존 5개 artifact를 보존했으며 callbackStatus는 ABSENT 그대로다.
Backend worker-readiness/v2는 200, ready, canonicalSupported=true,
canonicalAdmissionEnabled=true, evidenceStoreStatus=READY였다.
AWS 최종 service active/running, internal actuator health UP 및 실행 JAR SHA를 확인했다.

이는 운영 JSON 응답 결함의 수정 반영 확인이다. 새 분석·GPT·B2 업로드·receipt 생성·
callback E2E, 품질 QA, 자동 회귀/브라우저 테스트는 수행하지 않았다. 실패 요청을
자동 재실행하거나 DB/journal을 변경하지 않았다. RunPod 진단 보완 코드는 여전히
로컬이며 이번 AWS 배포에 포함되지 않았다. 새로운 commit/push/PR는 생성하지 않았다.

## 재배포 이후 분석 57 조사 — lease 갱신 경합 의심

사용자의 후속 장애 조사 요청으로 2026-10-02 16:51 UTC부터 RunPod/AWS를 다시
읽었다. AWS는 main `8985c4d` JAR, PID 702162, active 상태를 유지했다.
이번 실행은 앞선 journal 중첩 JSON 결함과 다른 단계에서 중단됐다.

- analysisId 57 / recordingId 67
- requestId `97a9afe1-60cb-4cb8-86b9-c9f7c2356181`
- executionId `8443cf38-068e-4cd4-a769-8f2c3e47e740`
- 전체 deadline 2026-10-02 16:59:40.460 UTC

### 양쪽 로그·현재 상태

AWS nginx에서 claim(16:44:47)=200, journal reserve(16:44:50)=200,
start(16:44:55)=204 및 16:45:04~16:46:47의 heartbeat 7회=200을 확인했다.
journal prepare, artifact stage, evidence register, result callback은 해당 실행의
access 기록에 없다. journal GET은 200, producerStarted=true, association=null,
artifacts=[], receiptId=null, callbackStatus=ABSENT다.
AWS 서비스 로그에서 같은 시간대의 분석 오류는 확인되지 않았다. 별도 refresh token
경고는 있으나 이 서버 간 실행 실패의 원인으로 연결할 근거는 없다.

RunPod status는 FAILED / INTERNAL_ERROR, executor 로그는
`canonical_executor_unavailable`, 상위 서비스 로그는 CanonicalProcessFailed다.
host READY/ready_verified PASS까지 진행했고 canonical 프로세스가 시작됐지만,
canonical_exit/batch_report/core 결과/callback/completed 파일은 생성되지 않았다.
MFA sidecar는 마지막 기록상 STARTING이며 모델 작업 로그는 0 bytes다.
따라서 이번에는 core 분석 성공이나 사용자 발음 문제로 판단할 수 없다.
cgroup memory.oom_control은 oom_kill=0, under_oom=0이었다.

| 사건 | UTC |
|---|---|
| lease.json 새 사본 mtime | 16:46:47.451356 |
| lease.json 교체 ctime | 16:46:47.452356 |
| MFA worker 정상 종료 의도 기록 | 16:46:47.553510 |
| executor 오류 기록 | 16:46:47.680358 |
| 마지막 lease의 계산된 만료 | 약 16:48:13.307 |

lease 만료는 같은 Pod의 wall/monotonic clock offset으로 환산했다.
오류 시각에 약 86초의 lease와 약 13분의 전체 deadline이 남아 있었다.
worker 종료 gate는 PASS, 자식·zombie·미해결 PID가 모두 비어 있었다.

### 가장 유력한 원인과 확정 한계

실제 배포 코드의 lease 판독은 로컬과 동일했다.
`canonical_lane.publish_lease`는 heartbeat 후 새 파일을 `os.replace`로 교체한다.
`canonical_executor.remaining`은 이 갱신 파일에도 불변 결과물용
`read_private_result`를 사용한다. 이 reader는 읽기 전후 inode/size/mtime/**ctime**의
완전 일치를 요구한다. 기존 파일을 열어 읽는 동안 교체로 기존 inode의 link/ctime이
바뀌면 내용이 온전해도 CANONICAL_OUTPUT_CHANGED가 발생할 수 있다.
remaining은 모든 예외를 숨기고 0을 반환하며, process 감시 루프는 이를 권한 만료로
처리하여 core 자손을 정리한다. 한 번의 일시적 판독 실패도 종료로 이어지는 구조다.

heartbeat 성공·파일 교체 직후 약 0.23초 내 종료·유효 lease·취소가 아닌 FAILED 상태는
이 경합 가설과 부합한다. **그러나 당시 reader 예외와 내부 종료 코드는 로그에 없으므로
이 요청의 직접 원인을 확정하지 않는다.** 재현 실행은 저장소 QA 규칙상 하지 않았다.
`CanonicalProcessFailed`는 바깥 guard가 executor의 실패를 관측한 분류이며,
그 자체로 모델 프로세스 오류나 실제 lease 만료를 증명하지 않는다.

후속 수정은 갱신 가능한 lease용 snapshot reader를 분리하고 파일의 소유·권한·크기·
executionId·유한 deadline 검증을 유지해야 한다. 정상 atomic 교체와 실제 내용 변조를
구분하고, 판독 실패 이유를 비밀값 없는 고정 코드로 남겨야 한다. lease 연장 권한을
worker에 주거나 만료·취소를 무시하는 방식으로 우회하지 않는다.

이번에는 운영 조회와 문서 기록만 수행했다. 소스 추가 수정·재배포·분석 재실행·
GPT 요청·자동 회귀/브라우저 QA는 수행하지 않았다.

후속 사용자의 계획 수행 요청으로 [lease 판독·진단 수정과 RunPod 배포](runpod-lease-hardening-plan-20261003.md#10-운영-배포-기록)를
완료했다. 패치 소스 `5f582fc`, 양쪽 readiness·기존 journal JSON을 확인했다.
실제 경합 재현·새 분석 E2E는 미수행이므로 위 원인 가설을 확정으로 변경하지 않는다.
