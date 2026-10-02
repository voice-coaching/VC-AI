# 2026-10-02 분석 실행 실패와 FE UI 복원

## 1차 원인 조사 범위

- 요청: RunPod 실패 원인 조사, 기존 FE 화면을 보존한 v4 계약 전환 및 우리 UI 변경 철회.
- Pod: `s8hb5b0l5k4qlx`, RUNNING 확인 후 기존 SSH로 읽기 전용 조사.
- 최종 코드/로그 관측: 2026-10-02 06:25:53 UTC (15:25:53 KST).
- 새 추론, GPT 요청, 자동 QA, 서버 수정·재시작·재배포는 하지 않았다.
- 이 실행은 15:04 KST이다. 앞선 사용자 제보의 13:38±20분 요청과 동일하다고 확정하지 않는다.

## 관측한 실패

| 식별자 | 값 |
| --- | --- |
| analysisId / recordingId | 48 / 58 |
| requestId | 0cae64ae-1f7e-4490-9de1-ce8c00e4ae40 |
| executionId | 8d92797d-1eb2-42be-be4c-8df2ff82e288 |
| AWS 조회 흐름의 sessionId | 94 |
| executor.log 수정 시각 | 06:04:52.814021 UTC |

RunPod `/run/intelligentai/logs/analysis-http.log`:
`canonical_execution_failed error=CanonicalIntegrationError`.
해당 execution의 `executor.log`: `canonical_executor_unavailable`.
내부 core 실행의 `producer.log`는 0 bytes였다.

AWS nginx에서 같은 analysis/execution의 journal reserve=200, start=204를 확인했다.
request/lease/admitted-case는 생성됐지만 core release-output/완료 결과가 없었다.
즉 접수·원본 준비·producer 시작 기록 이후, core 결과 생성 전에 실패했다.
GPT 표현 및 v4 정상 결과 callback까지 도달한 증거는 없다.

## 원인: guard Python과 core Python을 혼용

실제 배포 `canonical_producer.py:116`은 `run_guarded(python=CORE_PYTHON, ...)`를 호출한다.
`canonical_process.py:53`은 이 Python으로 `canonical_process_guard.py`를 실행한다.
guard의 71~73행은 아래 세 기능 중 하나라도 없으면 자식 core를 시작하기 **전에 125를 반환**한다.

| 런타임 | os.pidfd_open | os.P_PIDFD | signal.pidfd_send_signal |
| --- | --- | --- | --- |
| core mac_prod311 / Python 3.11.15 conda-forge | 없음 | 없음 | 없음 |
| /opt/intelligentai/envs/canonical / Python 3.12.3 | 있음 | 있음 | 있음 |

대상 Python의 `-I -B -c`로 버전과 attribute 존재 여부만 읽었다.
실제 guard를 새로 실행하거나 추론을 재현한 것은 아니다.
배포 소스와 지원 기능을 대조하면 **현재 core guard 호출은 필수 기능 검사에서 종료되는 구조**다.
실제 로그는 exit code를 기록하지 않으므로 125는 소스에 근거한 진단이며 로그에 직접 남은 값이 아니다.
Python 3.11 일반의 결함으로 단정하지 않는다. 해당 설치 빌드의 노출 기능 문제다.

실패가 뭉뚱그려진 이유도 확인됐다:
process wrapper는 비정상 종료를 CANONICAL_PROCESS_FAILED로 바꾸지만,
executor의 catch-all은 canonical_executor_unavailable만 출력한다.
따라서 이 로그만으로 GPT/API 키/B2/사용자 음질 실패를 주장할 수 없다.
health/readiness 200 및 executorConfigured=true는 실제 core 실행 성공의 증거가 아니었다.

### 실제 배포 파일 SHA-256

모두 `/opt/intelligentai/app/src/voice_coach/backend_analysis/` 아래 파일이다.

| 파일 | SHA-256 |
| --- | --- |
| canonical_producer.py | c7fdbe67d8cd666b9d85cd765eb5850ca007844a352f9ed098ff2dad43a5ef07 |
| canonical_process.py | 2aa11284541fc7f309f10d694668e90e025da62e995e4e00da57584876e51652 |
| canonical_process_guard.py | 8356ed06c5081932b0d000db2df87ede960c45a0145717ac6236b21b3c731d7a |
| canonical_executor.py | 9cc8350e55c00438ed970a3bdc617cc3dda526a3352be6f253f8f4f7b3246bf8 |

## 1차 조사 시 수정 제안 — 후속 적용은 아래 참조

1. 감시기용 Python을 core 실행 Python과 분리한다. 감시기는 검증된 canonical 환경,
   실제 frozen core command는 기존 mac_prod311을 유지한다.
2. pidfd/subreaper/자손 회수 검사를 제거하거나 단순 process-group kill로 우회하지 않는다.
3. readiness에 **실제 감시기 Python**의 필수 기능 검사를 추가한다.
4. 비밀값·사용자 원문·argv 없이 안정된 오류 code와 guard 종료 상태를 기록한다.
5. 이후 별도 수정·배포와 개발자의 실제 실행 확인이 필요하다. 이 문서는 복구 완료 증빙이 아니다.

## FE 수정 방향

사용자는 기존 사용자 화면·컴포넌트를 유지하고 **계약만 v4로** 전환하도록 명시했다.
PR #33/#35의 별도 canonical 화면/선택기/새 카드 UI를 철회한다.
VC-FE dev `d2a880d`에서 `fix/restore-ui-v4-contract-20261002` 브랜치로 수정한다.
우리 변경 전 main `35b2c9c`를 피드백 화면 복원 기준으로 사용한다.

- 기존 녹음·피드백·점수·문장 영역, 이력 탭/삭제 UI 보존.
- analyze/retry는 기존 body와 v4 헤더, 결과는 public view v1만 소비.
- v3 결과 fallback·별도 v4 진입 선택 제거. 과거 v3 이력 이관은 미구현이며 오류로 표시.
- 기존 화면 모델에 public view 전체 및 scoped ID/MFA/상태를 보존하고 허용된 표현만 총평으로 연결.
- canRetry/canRerecord/canComplete 및 현재 선택 녹음 확인. non-ACCEPT를 완료 처리하지 않음.
- 클래스/시험의 v4 admission은 현재 미지원. 기존 화면은 유지하지만 v3로 우회하지 않음.
- 점수와 문장별 판정 계약이 없으므로 임의 수치·segment를 생성하지 않음.

FE 커밋 `6b19593`을 푸시하고 [dev 대상 PR #37](https://github.com/voice-coaching/VC-FE/pull/37)을 생성했다.
TypeScript `tsc --noEmit --incremental false`와 diff 정적 확인이 통과했다.
analysis-view/coaching-view/coaching.ts는 복원 기준 35b2c9c와 diff가 없음을 확인했다.
FE 병합/배포와 수동 QA는 미수행이다. 최초 조사에서는 문서 변경을 로컬 작업 트리에 남겼으며,
아래 후속 배포 기록과 함께 저장소 문서로 인계한다.

## 후속: 사용자 승인에 따른 수정·재배포 완료

사용자의 “파악한 원인을 바탕으로 수정하고 재배포하라” 요청으로 다음을 적용했다.
2026-10-02 07:06 UTC에 운영 overlay를 갱신했고 07:07~07:13 UTC
(16:07~16:13 KST)에 운영 readiness를 확인했다.

### 수정

- `run_guarded` 인자를 `guard_python`으로 명확히 분리했다.
- 외부 lane 감시기는 설정된 canonical Python, 내부 core 감시기는 canonical executor의
  `sys.executable`을 사용한다. 실제 frozen core command는 기존 mac_prod311 Python 3.11 그대로다.
- guard의 `--check`는 실제 Python pidfd binding, 자기 프로세스 pidfd 열기,
  subreaper 설정/조회 가능 여부를 확인한다. 모델·분석 자식 작업을 시작하지 않는다.
- 기존 prepare_canonical → executor --check 및 HTTP lane 초기화에서 이 검사를 수행한다.
  실패하면 canonical 준비/접수를 열지 않는다.
- pidfd·자손 회수·lease/deadline·취소 경계를 유지했다.
- private executor 로그에 실패 code와 숫자 guardExitStatus를 보존한다.
  traceback·사용자 원문·경로·argv·토큰은 추가하지 않았다.
- 배포 helper에 변경 전 파일 hash 확인, canonical bootstrap/readiness 확인,
  새 서비스 기동 후 실패 시에도 정상 종료 후 복원하는 절차를 추가했다.

### 배포 식별자와 백업

| 항목 | 값 |
| --- | --- |
| 패치 소스 커밋 | `ab8bb6348c4992516319412fd3ecea989d79b00e` |
| 유지한 base sourceRevision | `32d98e39767b56697bc7e58fb50e3e9d61432349` |
| 새 overlay SHA-256 | `01cd48d48e31332c28517fd83a3b81b595c0238130a0c1bd1abaca1026c1e58f` |
| 패치 bundle SHA-256 | `f05d08ce145dc5bdf7712e91753eb63b93434e95601be965b9cdfc1267c2d93b` |
| 백업 | `/workspace/intelligentai/deployment/canonical-runtime-backup-20261002T070600Z` |
| 이번 실행 로그 | `/run/intelligentai/deploy-guard-20261002T0710/operation.log` |

전체 로컬 HEAD를 운영 sourceRevision으로 위장하지 않았다. 실제 5개 파일의 패치 소스 커밋은
manifest/deployment.json의 canonicalIntegration.sourceGitRevision에 별도로 기록했다.
runtime/영구 overlay/각 파일 SHA가 모두 일치했다. 분석 boot 루프를 일시 정지하고
manager/supervisor를 정상 종료한 뒤 prepare_http 및 재기동을 수행했다.
gateway·TTS·frozen R/S7·키·HTTP/callback schema·Backend JAR은 변경하지 않았다.
백업은 소스와 manifest/metadata용이며 비밀 설정이나 모든 모델을 포함한 전체 복구본은 아니다.

| 변경 파일 | SHA-256 |
| --- | --- |
| canonical_lane.py | 178724adaa2966a1964850bc333971f9cf2e2cdddf4d3fc2dc8858e40c984014 |
| canonical_executor.py | f73165db7e3bd8943c1fb428ef026bda1e745727b44e91af5655d46736e220a9 |
| canonical_producer.py | 144fc83b6b394363d18e6c38c211a336381c92309548c7a44a739f87f68bfeea |
| canonical_process.py | da0c0e7d11afb32e966d43dfe1d9e16c42b25ce513cd0fa26f6c2cd1ff61881c |
| canonical_process_guard.py | 2a54f7d5a9ec68bce734a86d95c3ca99aae957b7483ff246fdbdac00580cc6e3 |

### 운영 확인과 한계

- 소스 6개(배포 helper 포함) 컴파일 및 diff 정적 확인 통과. 운영 적용은 런타임 5개.
- RunPod bootstrap true, 분석 health/services=200/ready/executorReady.
- 공용 8082 canonical health=200, executorConfigured=true, admissionEnabled=true.
- Backend worker-readiness/v2=200/ready, canonicalSupported=true,
  canonicalAdmissionEnabled=true, evidenceStoreStatus=READY.
- 독립 TTS readiness=200. AWS 실제 설정 주소에서도 분석·canonical·TTS 모두 200.
- 처음 Python urllib 기본 User-Agent health 요청은 403 및 `error code: 1010`이었다.
  `intelligentAI-deployment-readiness/1.0`을 명시한 동일 인증 GET은 200이었다.
  관측상 클라이언트 서명 필터와 일치하며, nginx/TLS/인증 정책을 해제하거나 변경하지 않았다.
  [Cloudflare 1010 공식 설명](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)
- 실패 요청 48을 자동 재실행하거나 journal/lease를 초기화하지 않았다.
- 실제 사용자 녹음/core/GPT/B2 업로드/callback E2E와 품질 QA, 자동 회귀,
  브라우저 자동화는 미수행이다. `executionQaPerformed=false`를 그대로 유지한다.

이는 원인이 확인된 실행 경로의 **수정·재배포와 운영 준비 상태 확인**이며
실제 분석 E2E 성공 또는 이후 단계 장애 부재를 보증하지 않는다.
FE PR #37의 병합·배포는 이번 RunPod 재배포 범위에 포함하지 않았다.
