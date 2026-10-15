# DLPC 자동 복구 실패와 운영 복원 — 2026-10-15

## 장애 관측

2026-10-15 11:37–11:38 UTC, DLPC `J3990.2020067.notebook.dlpc`를 직접 조회했다.
공개 분석 `/health`, `/readyz`, `/direct/readyz`는 nginx 502였고, 로컬 분석
8080과 TTS 8083은 connection refused였다. `/opt/intelligentai`,
`/workspace/intelligentai`, `/workspace/voice-coaching-tts`가 없었고 실행 중인
분석·TTS·복구 프로세스도 없었다. FE는 200, Backend 무인증 health는 401이었다.

자동 복구는 01:35:41 UTC에 `failed_operator_reconciliation_required`로
중단됐다. 다운로드 상태는 `source-manifest.json`, 0 / 42,449,956 bytes,
`DOWNLOAD_SIZE_HEADER`였다. 실제 설치된 훅은 구버전이며 10월 12일 준비한
production recovery 후보가 설치되지 않았다.

11:43 UTC 동일 고정 manifest의 전체 범위 요청을 조회했다. B2 응답은 HTTP 200,
Content-Length 42,449,956, Content-Range 없음이었다. 구버전 downloader가
HTTP 206만 허용해 정상 전체 객체 응답을 거절하는 조건을 재현했다.

## 승인 범위와 복구 기준

사용자는 상태 조사와 계획 검토 후 “계획을 수행하라”로 실행을 승인했다.
운영 기준은 10월 8일 source `791ca5496e04a10b6948c69dce97361ee4335f4c`,
bundle `d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`와
10월 10일 resident 복구·프로세스 health 패치다. 최신 main으로 바꾸지 않는다.
기존 녹음 재실행, frozen core/H5 변경, Backend 신규 배포는 이 복원의 대상이 아니다.

기존 실패 JSON은 영속 홈 recovery의 `incident-20261015/`에 보존했다.
`install_startup_hook.py --update`로 기존 Jupyter 설정을 보존하면서 준비된
복구 helper를 설치했고, 11:43:39 UTC `recover_on_start.py --restore-only`를
시작했다. 다운로드는 고정 file ID, size, 전체 SHA-256을 검증한다.

## 기동 순서 보정

Backend 인증 handoff 조회는 11:45 UTC HTTP 200, supported=true,
admissionEnabled=false였으며 schema 9개가 checkout과 일치했다.
공개 Backend 소스와 직전 운영 기록의 revision `a35f9e90…`를 대조한 결과,
Backend admission은 AI `/health/handoff`의 executorConfigured와 schema에
의존한다. AI 기동 전에 Backend admission을 요구하던 후보 스크립트는
전체 중단 상태에서 순환 대기하게 된다.

`start_production.py`는 기동 전 Backend supported 및 정확한 schema를
요구하고, AI 기동 후에는 기존 full admission 조건을 다시 요구하도록 수정했다.
또한 이번 복구가 direct hold를 해제한 뒤 공개 readiness 확인에 실패하면
해당 hold를 복원한다. 동시에 생성된 다른 운영자 hold는 덮어쓰지 않는다.
수정은 Python 구문 및 diff 정적 검토 범위이며 자동 회귀 QA는 수행하지 않았다.

## 중간 관측 — 11:48 UTC

11:48:36 UTC archive 4,966,055,936 / 16,062,581,191 bytes 다운로드 중이다.
전체 자산 검증·추출, overlay/의존성/비밀 설정 복원, 수정 기동 helper 설치,
분석·TTS 기동, 양쪽 readiness 및 FE CORS 확인은 아직 완료되지 않았다.

GitHub CLI는 인증되지 않았다. 연결된 GitHub 도구에는 runner 등록 API가
없어 runner 재등록을 위한 호스트 인증을 사용자에게 요청했다. 이 의존성은
분석·TTS 복원과 분리한다. runner가 online이라고 보고하지 않는다.

실제 녹음·GPT 생성·TTS 합성·품질 QA와 컨테이너 재생성 시험은 수행하지 않았다.
운영 완료 관측은 아래에 별도 기록한다.

## 작업 디렉터리 누락 수정

기본 자산 141,602개 항목의 검증·추출·설치와 overlay, CUDA, 비밀 설정,
resident 패치 및 TTS fingerprint 검증을 완료했다. 첫 분석 기동은
`/workspace/intelligentai/direct-spool` 생성 권한 부족으로 종료됐다.
원래 설정의 spool 경로는 유지되지만 runtime 준비 도구가 해당 디렉터리를
재생성하지 않아 서비스 계정이 root 소유 0750 부모 아래에 만들 수 없었다.

`prepare_restored_runtime.py`가 두 설정값을 고정 배포 경로와 대조한 뒤
`/workspace/intelligentai/direct-spool`과 `/var/lib/intelligentai/work/direct`를
UID/GID 10001, 0700으로 준비하도록 수정했다. 부모 디렉터리 권한이나 기존
job journal을 바꾸지 않는다. 수정 도구로 필요한 디렉터리를 준비한 뒤
FATAL 상태였던 분석 서비스를 다시 시작했다. 분석 liveness 200 이후
모델/MFA 준비가 끝나고 TTS가 기동됐다.

## 운영 복구 완료 — 12:24:46 UTC / 21:24:46 KST

복구 state는 12:23:08 UTC `production_services_ready`, productionAdmission=true를
기록했다. 12:24:46 UTC 별도 외부 조회 결과는 다음과 같다.

| 확인 | 결과 |
| --- | --- |
| 공개 `/health`, `/readyz` | HTTP 200, alive / ready |
| 공개 `/direct/readyz` | HTTP 200, ready=true, reasonCode=null |
| 공개 인증 `/health/services` | HTTP 200, executorReady=true, handoffReady=true |
| 공개 인증 TTS `/health/ready` | HTTP 200, `melo-ko-practice-dlpc-r1` |
| Backend 인증 handoff | HTTP 200, supported=true, admissionEnabled=true, schema 9개 일치 |
| direct GET 및 업로드 OPTIONS | FE origin `https://vc-fe.vercel.app`, OPTIONS 200, GET/POST와 요청 헤더 허용 |
| 분석/TTS 보호 API의 무인증 조회 | 각각 HTTP 401 |
| 운영 접수 / 작업 수 | direct hold 없음, activeJobs=0, directJobs=0 |

공개 deployment는 source `791ca5496e04a10b6948c69dce97361ee4335f4c`,
bundle `d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`,
core `f6d56e1d…`, H5 `3e430635…`, lock `58c26086…`을 반환했다.
분석 health의 provider/model은 OPENAI / `gpt-6-astra`였다.
직접 녹음 추론이나 GPT 생성 성공을 의미하지 않는다.

마지막 helper 갱신은 서비스를 재시작하지 않았다. 실제 설치된
`start_production.py` SHA-256은
`84ec7fe632267f3fe232b0faef1c87b1e60ab19d785d52979adb46977172a67e`,
`prepare_restored_runtime.py`는
`3fd300c594b8e070d508c65b8422673f4585e8ec1bf95b51d2d3339d34d77bc9`이며
checkout과 일치한다. 다운로드/전체 해시 검증과 시작 훅 갱신도 실제 적용했다.
비밀 없는 최종 응답 요약은 영속 홈의 root 전용
`recovery/incident-20261015/final-readiness.json`에 보존했다.

## 자동 배포 복구와 남은 범위

고정 controller SHA-256 `69f657d33b2ebf55d1410a0a3f9530284cdd71677cd9047d3d5949ea1bc3373a`와
공식 runner 2.337.0 archive SHA-256
`70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613`를 대조해
전용 계정·controller·제한된 sudo 규칙·runner 프로그램 설치를 복원했다.
GitHub CLI 및 기존 Git credential helper에는 인증이 없어 GitHub 재등록과
runner 시작은 수행하지 않았다. 사용자에게 호스트 인증을 요청한 상태다.
대기 workflow를 실행하거나 새 main을 배포하지 않았다.

이번 실제 적용 범위는 운영 자산/설정 복원, 분석·TTS 기동, readiness·CORS·인증
확인 및 영속 복구 도구 갱신이다. Python 구문·Git diff 정적 확인을 수행했다.
실제 녹음/GPT 생성/TTS 합성/품질 QA와 후속 플랫폼 재생성 시험은 수행하지 않았다.
코드·문서는 로컬 변경 상태이며 commit/push/PR은 수행하지 않았다.

## Runner 재등록 — 14:12 UTC / 23:12 KST

사용자가 runner 재등록과 미커밋 변경 전체의 commit/push/main PR 및 병합을
요청하고 GitHub 인증을 제공했다. 인증값은 프로세스 메모리로만 전달했으며
저장소·Git remote·문서에 저장하지 않았다.

기존 runner ID 21은 offline 및 idle이고 대기 중인 workflow가 없음을 확인했다.
해당 등록을 교체하여 `intelligentai-dlpc`를 ID 22로 재등록했다. GitHub는
online/busy=false를 반환했고 전용 supervisor의 `actions-runner`는 RUNNING이었다.
분석·TTS 서비스는 이 작업으로 재시작하지 않았다. 코드 PR과 main 병합은
이 후속 작업에서 진행하며, 그 결과는 GitHub PR 및 workflow 기록으로 확인한다.

변경 Python 15개 파일의 구문 검사, staged diff 및 credential 패턴 검사는
통과했다. 저장소 전체 정적 검사는 기존 main의 문서 링크 2개, 폐기된 문서
경로 참조 2개, API 문서 source pin 4개에서 실패했다. 해당 문서·config·검사기
10개 파일이 origin/main과 바이트 단위로 동일함을 확인했다. 이번 변경으로
해당 실험의 검증 기준이나 과거 문서 내용을 임의 갱신하지 않는다.

루트 AGENTS.md의 자동 회귀 QA 금지 지침에 따라 이 작업의 커밋에는
`[skip ci]`를 사용한다. main의 필수 status check는 없는 상태였다. 현재
운영 source는 위 복구 revision과 이미 적용된 resident 패치 조합이다.
controller는 변경 전 Git blob과 실제 파일이 달라지면 `RUNTIME_SOURCE_DRIFT`로
중단하므로, Git 병합만으로 새 main의 자동 배포 성공을 주장하지 않는다.
