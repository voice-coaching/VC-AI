# DLPC 복구 중단과 direct API CORS 표시 — 2026-10-10

## 원인과 최초 관측

사용자는 `https://vc-fe.vercel.app`에서 direct 분석 조회가 CORS 오류로 차단되는 문제의
서버 점검과 복구를 요청했다. 공개 API는 nginx `502 Bad Gateway`를 반환했고,
오류 응답에는 `Access-Control-Allow-Origin`이 없었다.

DLPC `J3990.2020067.notebook.dlpc`에는 `/opt/intelligentai`, `/workspace/intelligentai`,
분석 8080·TTS 8083 listener가 없었다. 자동 복구 기록은
`failed_operator_reconciliation_required`였다. B2 base archive 다운로드는
16,062,581,191바이트 중 949,420,991바이트에서 중단됐고,
`DOWNLOAD_HASH_MISMATCH`로 완료 처리가 거절됐다. 이 코드는 당시 길이 부족도
같은 오류로 분류한다. 저장된 CORS 허용 origin은 이미 `https://vc-fe.vercel.app`이었다.

## 복구와 소스 변경

- 기존 실패 기록과 부분 파일을 보존하고 동일 file ID의 남은 바이트를 이어받았다.
- `b2_restore.py`에 64MiB HTTP Range 다운로드, 범위/길이 검증, 제한된 재시도,
  명시적 `--resume`, 동시 다운로드 방지 lock과 완료 객체 재검증을 추가했다.
- 전체 archive SHA-256은 `b30e4d258ce42f4d40188d4a295784748fcba94c31dad78e5cc51f24334c7eed`,
  manifest SHA-256은 `f806086d4196c1df6ff2f63e0ea3b8565197a0a390139aca0beacc8a20920bda`와 일치했다.
- 기존 복원 도구가 141,602개 archive member를 원본 inventory와 대조한 뒤 압축 해제를 시작했다.
- 운영 복구 대상은 10월 8일 승인 배포 source `791ca5496e04a10b6948c69dce97361ee4335f4c`,
  bundle `d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`이다.
  최신 checkout을 임의 운영 배포하거나 frozen core/H5를 새로 생성하지 않는다.
- 자산 설치 후 base backup의 프롬프트 symlink 3개와 현 운영 Git pointer 파일의
  형식 차이로 overlay 적용이 중단됐다. 기존 링크를 별도 보관하고 pinned bundle의
  동일 상대 파일명 바이트로 교체한 뒤 운영 파일 861개를 적용했다.
  복원 도구에도 이 세 경로만 허용하는 동일성 확인과 이전 링크 기록을 추가했다.
- GPU 라이브러리 복원은 성공했지만 `umask 077` 때문에 상위 `runtime-libs`가
  `0700`으로 생성돼 서비스 UID 10001의 접근이 거부됐다. 의도된 `0755`를 명시 적용하고
  `restore_cuda_libraries.py`에도 반영했다. 실제 서비스 계정의 사전·라이브러리 읽기와
  `core_environment` 검증이 통과한 후 분석 서비스를 다시 기동했다.
- 다운로드·링크·GPU 라이브러리 권한 수정본을 영속 홈의 실제 복구 도구에도 적용했다.
  이전 도구와 운영 설정은 root 전용 `/opt/intelligentai/ops/` 하위에 보존했다.

## 확인 범위

다운로드 재개와 전체 해시, archive inventory 검증을 실제 수행했다.
수정 Python 구문과 Git diff 정적 확인을 수행했다. 자동 회귀 테스트·브라우저 QA·
실제 녹음 분석·GPT 생성·TTS 합성·품질 QA는 수행하지 않았다.

## 1차 복구 관측 — 2026-10-10 09:58 UTC / 18:58 KST

음성 모델 파일의 첫 디스크 읽기가 오래 걸려 최초 resident 준비가 제한시간을 넘겼다.
1,262,083,185바이트 체크포인트를 순차적으로 읽었으며 SHA-256은
`b2ec663558606d651a718fa1eaa20c6d504df173725ecbc66df74c22267e2d68`이었다.
기존 supervisor의 다음 준비에서 두 resident가 각각 약 57초와 58초 만에
모델·G2P·MFA 준비를 완료했다. 모델, frozen 코드, 준비 제한시간을 변경하지 않았다.

진행 작업 0개와 readiness를 확인하고 이번 복구에서 만든 direct admission hold를 제거했다.

| 확인 | 결과 |
| --- | --- |
| 공개 `/readyz` | HTTP 200, `status=ready` |
| 공개 `/direct/readyz`, FE Origin 포함 | HTTP 200, `ready=true`, `reasonCode=null` |
| 업로드 OPTIONS, `content-type,x-analysis-metadata` | HTTP 200, GET/POST 및 해당 헤더 허용 |
| CORS | `Access-Control-Allow-Origin: https://vc-fe.vercel.app`, `Vary: Origin` |
| 인증된 분석 services | HTTP 200, `executorReady=true`, `handoffReady=true` |
| 독립 TTS readiness | HTTP 200, `melo-ko-practice-dlpc-r1` |
| Backend handoff readiness | HTTP 200, `supported=true`, `admissionEnabled=true` |
| 진행 작업 | `activeJobs=0`, `directJobs=0` |

사용자가 제시한 기존 `clientAttemptId` 조회는 CORS 헤더를 포함한 HTTP 404
`NOT_FOUND`였다. 해당 조회 결과만으로 이전 분석의 완료 여부나 영구 이력 유실을
확정하지 않는다. 기존 시도를 자동 재실행하지 않았으며 새 분석 요청이 필요하다.

원래 허용 origin을 유지했다. nginx나 FE 설정을 바꾸거나 wildcard CORS를 추가하지 않았다.
복원 도구 3개와 README, 이 기록은 로컬 변경이며 commit/push/PR은 수행하지 않았다.
운영 서비스와 영속 홈 복구 도구에는 위 복구를 실제 적용했다. 새 녹음의 추론·GPT 생성·
피드백 품질은 이 readiness 확인의 범위가 아니다. 컨테이너 재생성 시 자동 운영 접수와
Actions runner 재등록은 이번 복구에서 추가하지 않았다.

## 후속 503 조사와 MFA 접근 복구 — 2026-10-10 10:27 UTC

사용자가 POST 503과 미접수 attempt 조회를 보고했다. 제시한 attempt는 조회 시
404였으며 실패한 POST의 응답 본문은 확보하지 못했다. 따라서 그 요청의 정확한
503 code는 미확인이다. 별도로 10:20:09 UTC 접수된 작업은 약 2초 뒤
`RESIDENT_RESPONSE_INVALID`로 실패했고 resident 로그에는 `CORE_UNAVAILABLE`이
남았다. 다음 generation은 10:20:44 UTC에 준비됐다. 해당 재준비 구간에는
새 요청이 `CORE_NOT_READY` 503으로 거절될 수 있다.

실패 작업은 `effective_config.json`까지만 생성됐다. 그 다음 단계의 MFA adapter
import를 추론 없이 서비스 UID/GID 10001로 확인해 `PermissionError`를 확인했다.
overlay가 새로 만든 `mfa_preflight_optimization_20260922T043055Z_0d7f6a/`가
root:root 0750이었고 하위 `candidate/`와 `mfa.py`만 서비스 그룹이었다.
기존 readiness는 이 요청 시점의 import를 수행하지 않아 문제를 감지하지 못했다.

고정 MFA 파일 SHA-256
`ebecb9600d0cfc90264042db5b1cd43d0f7ca5aa83275924a5e3fef8b7fd7aee`와
디렉터리 소유자·모드를 대조한 후 그 상위 디렉터리 그룹만 10001로 수정했다.
재시작·기존 요청 재실행 없이 운영 환경에 반영됐다. installer의 MFA 권한 적용
범위도 해당 디렉터리까지 확장하고 영속 홈의 실제 복구 도구에 설치했다.
이전 권한과 도구는 `/opt/intelligentai/ops/mfa-access-recovery-20261010/`에 보존했다.

수정 후 같은 서비스 계정의 adapter import가 성공했다. 공개 direct readiness는
10:27:21 UTC HTTP 200/ready=true 및 FE의 정확한 CORS origin을 반환했다.
인증된 executor/handoff와 TTS 및 Backend admission도 정상이고 진행 작업은 0개였다.
Git diff와 installer 구문 정적 확인을 수행했다. 실제 녹음·GPT 생성·품질 QA는
수행하지 않았으므로 새 분석의 종단간 성공은 아직 확인되지 않았다.

## 후속 인증 오류와 실제 분석 실패 조사 — 2026-10-10 11:01 UTC

사용자가 제시한 job `7b125002-af93-4777-851e-9d0d23189232`의 spool 원본을
조회했다. RESULT_READY이며 archive/history 전송은 COMPLETE이지만, 실제 결과는
`SYSTEM_FAILURE / SYSTEM_ANALYSIS_FAILURE / alignment`, `coreStatus=partial`,
`failure=CANONICAL_ANALYSIS_FAILED`였다. GPT는 NOT_DISPATCHED/0회다.
RESULT_READY 및 history COMPLETE는 분석 성공이나 사용자 계정 연결 성공이 아니다.

원인 연결은 아래 실제 기록에 근거한다.

- 앞선 10:32 요청의 `/tmp/vc-core-xsyafd0p/mfa_runtime/reports/g1_r0_da8f2053e28b_worker.json`
  traceback: `direct_adapter.py:78`에서 `NoAlignmentsError`. 발화 1개에 성공한
  alignment가 없었으며 beam=10/retry_beam=40이었다. 음성과 원고 불일치인지,
  음질 또는 사전/모델 문제인지는 이 오류만으로 확정할 수 없다.
- resident_worker.py는 이 예외를 fatal 응답으로 보낸 뒤 return한다.
  MFA runtime 로그에도 첫 정렬 패스 오류와 종료 시 semaphore 정리 경고가 있다.
- 후속 10:55 요청의 CORE 원본 오류는 `BrokenPipeError`다. 종료된 MFA 하위
  프로세스에 대한 통신 실패와 부합한다. 바깥 resident가 계속 ready인 상태와
  하위 워커 사망을 구분하지 못하는 복구/readiness 문제가 남아 있다.
- `MFA 정렬 실행이 제한 시간 안에 완료되지 않았습니다`라는 표시 문구의 실제
  details는 BrokenPipeError이므로 실제 시간초과로 확정하지 않는다.

인증은 별도 경로다. 공개 FE `/api/backend/api/users/me`를 로그인 정보 없이
조회하면 HTTP 401 / AUTHENTICATION_REQUIRED 및 인증 필요·Access Token 만료
메시지가 반환된다. 사용자의 `/direct-analysis-history/{jobId}/link` 401은
계정 이력 연결의 인증 거절이다. 배포 FE 번들은 저장된 token을 Bearer로 전달하고
credentials=include 및 401 후 refresh/retry 로직을 갖고 있다. 사용자의 실제
Authorization/Cookie와 refresh 응답, AWS/Vercel 서버 로그는 확보하지 않았으므로
토큰 누락·만료·refresh 실패 중 어느 것인지는 미확인이다. 이용 가능한 AWS SSH
명령은 settings/B2 복구 export에 제한돼 있어 이 연결로 서버 로그를 읽을 수 없다.

과거 attempt `1c8e4625-b55f-4d0e-bb05-e6684800f4d2`는 현재도 404 NOT_FOUND다.
새 job의 attempt와 다르며 인증 오류가 아니다. 이 조사에서는 추론 재실행,
beam/모델 정책 변경, 인증 완화 또는 사용자 세션 조작을 수행하지 않았다.

## MFA 실패 후 세대 재사용 방지 배포 — 2026-10-10 12:20 UTC

`direct_analysis/service.py`가 worker 종료 코드뿐 아니라 완료 checkpoint의
`failed`도 확인하도록 수정했다. 시스템 실패 결과를 정상적으로 기록해 프로세스가
0으로 종료했더라도 해당 resident 세대를 정리한다. 완료 처리 전 recovering 상태로
접수를 닫고 정리 실패 시 닫힌 상태를 유지한다. 정상 정리 뒤에는 기존 refresh가
새 세대를 준비하며 준비 전까지 readiness=false다. 실패 결과와 기존 journal은
보존하고 이전 요청을 재실행하지 않는다. 취소·비정상 종료 역시 세대를 정리한다.

운영 원본 파일의 SHA-256 `213ea2a95d66a464bb6d5d3deb4c216a0cdbbd1988464d3b38c1d69466c78f9e`를
대조하고 접수 hold 이후 activeJobs/directJobs=0을 재확인했다. 분석만 중지하고
수정본 `47758c852a7d0e7133b135ef2d75220db71761c3385191c03846e47381595fa0`를
적용한 후 12:19:10 UTC 기동했다. TTS는 계속 실행했다. 두 새 generation의
READY/socket 확인 후 hold를 제거했고 12:20:14 UTC 공개 direct readiness는
200/true 및 정확한 FE origin을 반환했다. 내부 executor/handoff, TTS,
Backend admission도 정상이고 진행 작업은 0개였다.

원본은 `/opt/intelligentai/ops/direct-resident-recovery-20261010/`에 보존했다.
`deployment.json.operationalPatches`에 원본/수정 hash를 기록하여 기존 bundle
식별값과 운영 변경을 구분했다. 새 `restore_direct_resident.py`와 수정된
`recover_on_start.py`를 영속 복구 도구에도 설치해 bootstrap 전에 적용한다.
원본 hash가 다르거나 persistent repo의 수정 파일 hash가 달라지면 자동 적용을
거절한다. Native/MFA frozen release·beam·모델·프롬프트는 변경하지 않았다.

확인은 Python 컴파일, diff 정적 검토, 운영 파일 동일성·재기동·새 모델/MFA 준비와
HTTP readiness 범위다. AGENTS.md에 따라 자동 회귀/추론/품질 QA를 실행하지 않았다.
따라서 실제 실패 후 자동 세대 교체의 종단간 검증 및 새 녹음의 분석 성공은 미확인이다.
최초 NoAlignmentsError의 입력/모델 원인 및 사용자 로그인 401은 별도 미해결 항목이다.
이번 변경은 연쇄 BrokenPipeError를 유발한 죽은 MFA 세대 재사용을 차단한다.
로컬 코드·문서는 미커밋/미푸시이며 운영 배포와 영속 복구 도구 반영은 완료했다.

## 예방 조치 보강 — 2026-10-10 12:38 UTC

추가한 `backend_analysis/resident_health.py`와 `canonical_resident.py` 변경을
운영에 배포했다. 모델을 준비하기 전에 실제 서비스 계정/런타임으로 MFA adapter를
import한다. 원고·음성을 입력하거나 추론을 수행하지 않고 요청 시점의 접근 권한과
Python 의존성 문제를 준비 단계에서 감지한다.

readiness는 private `host_ready.json`에 기록된 host·sidecar·실제 MFA worker의
`/proc` 상태와 서비스 소유자를 확인한다. 시작 시각도 기억해 PID 재사용을 배제하고
종료/zombie 상태는 비정상으로 판단한다. 준비됐던 유휴 세대의 하위 워커가 죽으면
`resident_mfa_unavailable` 이벤트를 남기고 기존 guard로 정리한 뒤 새로 준비한다.
진행 중인 실행을 임의 재시도하거나 모델·beam 값을 바꾸지 않는다. 앞선 완료 결과
기반 재활용 변경도 유지되며, 운영 로그에서 실제 `direct_resident_recycled` 발생을
확인했다. 새 장애를 인위적으로 만들거나 사용자 녹음을 재실행하지 않았다.

진행 작업 0개를 재확인한 후 접수를 잠시 닫고 분석만 재기동했다. 두 새 세대 준비와
새 readiness 조건 통과 후 접수를 열었다. 12:38:01 UTC 외부 direct readiness는
200/true와 FE CORS origin을 반환했다. 내부 executor/handoff, TTS와 Backend
admission도 정상이며 activeJobs/directJobs는 0개다.

`restore_resident_health.py`는 기존/수정 파일 hash를 고정하고 분석 중지 상태에서만
적용한다. 원본은 `/opt/intelligentai/ops/resident-health-20261010/`에 보존하며
`deployment.json.operationalPatches.resident-health-20261010`에 식별값을 기록한다.
영속 홈 복구 도구와 `recover_on_start.py`에도 설치해 이후 bootstrap 전에 적용한다.
기존 다운로드 구간 재시도/재개·전체 해시 검증과 GPU/MFA 디렉터리 권한 복구도 유지한다.

Python 구문·diff 정적 확인과 실제 정상 프로세스의 readiness/운영 준비를 확인했다.
자동 장애 주입·자동 회귀·녹음 분석·GPT/품질 QA는 수행하지 않았다. 이 예방 조치는
이번에 확인한 복원/접근권한/죽은 워커 재사용 문제를 방지·감지하기 위한 것이며
개별 입력의 정렬 성공이나 외부 서비스 무중단을 보장하는 것은 아니다.
코드와 문서는 로컬 미커밋/미푸시 상태이며 운영 및 영속 복구 도구에는 적용됐다.
