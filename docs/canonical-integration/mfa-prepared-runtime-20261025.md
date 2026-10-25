# MFA 준비 비용 단축 계획·구현

브랜치 `feat/mfa-prepared-runtime-20261025`는 local main
`ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 생성했다.
기준은 M0 core `c9c7d893ebbf6e5a2fa7762c3fa2e72b33b4ada391c945ce564c59d4d4a5bf82`,
Luna none/default H5와 정상 job `3f61a865-c214-4d19-ac35-7c652a845558`이다.

## 완료 조건과 단계

| 단계 | 구체적 실행 | 관찰 가능한 완료 조건 |
|---|---|---|
| P0 운영 전제 | 감시 raw log 8MiB×2, 메모리 raw 표본 256개, 최대 8 phase의 peak만 유지. 요청마다 전체 로그를 다시 읽는 경로 제거 | source와 새 artifact에 상한·요청별 peak 경계가 있고 기존 메모리/cleanup gate 유지 |
| P1 기동 준비 | 기존 check_third_party를 worker READY 전 1회 실행. 요청은 executable·환경·library stat identity 확인 | READY receipt에 준비 증거, 요청 경로에 canary subprocess 호출 없음. 자산 변경 시 실패·세대 폐기 |
| P1 세부 관측 | initialize_database·phonological rules·disambiguation·lexicon 쓰기/컴파일·G2P 등 실제 메서드 span 추가 | 후속 정상 사용의 CORE에 하위 비용이 남음. 관측 없는 값은 0으로 대체하지 않음 |
| P2 하드웨어 후보 | 같은 고정 부모에서 MFA-only 1/2/4 thread core/H5를 각각 생성 | Native/GOP·TTS·NUM_JOBS=1·slot=1 유지. 운영은 1-thread 준비 재사용 조합만 선택 |
| P3 별도 구조 전환 | 사전 seed/불변 lexicon 또는 corpus DB 없는 단일 파일 adapter | MFCC/CMVN·정규화·G2P·음소 mapping/경계·오류 의미의 동등성 판단 후 별도 릴리스 |

이번 실행은 **P0/P1 운영 반영과 P2 비활성 후보 생성**까지다. P3는 새 알고리즘
경로와 요청 상태 모델에 대한 별도 개발자 판단이 필요한 후속 범위다. 여러 사용자
worker 풀도 현재 단일 실행 슬롯·취소·세대 정책 변경을 포함하므로 자동 활성화하지 않는다.

MFA 목표는 우선 단일 짧은 문장 8초 이하, 이후 약 6초다. 기준 11.335초 중
외부 도구 점검 1.292초를 없앤다는 산술값은 약 10.043초이며 목표 달성 보장이 아니다.
P1은 기존 정렬 workflow·모델·음성·DB·feature·silence·scale·출력 순서를 보존한다.

테스트·화면 검증·추가 녹음 API 호출은 이번 요청에서 수행하지 않는다. 이전에 허용된
각 1회 실측은 이미 사용했다. 필요한 Python/JSON/schema/link/hash 정적 확인과
운영 준비 상태 조회를 수행하며 이를 실제 정렬 품질·속도 확인으로 표현하지 않는다.

## 구현 경계

- `mfa_prepared.py`: worker별 subprocess 준비와 자산 identity. 경로/키/문장을
  공개 관측에 넣지 않고 숫자·binary digest·기동 시간만 receipt에 남긴다.
- `mfa_memory_watchdog.py`: 기존 snapshot/violation을 주입받아 동일 guard를 실행한다.
  회전돼 사라진 raw sample의 peak도 메모리에 보존하고 요청 종료 전에 반환한다.
- 파생 builder: 고정 M0 source 문맥과 hash를 확인하고 새 core/H5 디렉터리에 생성한다.
  원본 릴리스는 덮어쓰지 않는다. GPT request config/prompt/rubric은 유지한다.
- 복구: runtime·schema·direct core/H5·selector를 함께 고정하며 동일 단위로 롤백한다.

## 실행 결과

**2026-10-25 06:44:02 UTC** 준비 재사용 v2의 새 resident READY와 local·공개 direct
readiness 200/ready=true를 확인하고 소유 admission hold를 해제했다.
**06:46:42 UTC** 최종 확인에서도 activeJobs/directJobs=0, local deployment health
200, local·공개 direct readiness 200/ready=true였다.

| 완료한 실행 | 결과 |
|---|---|
| P0 표본·로그 상한 | worker별 raw sample deque 256개, phase 최대 8개, raw 로그 8MiB×현재/직전 2개 |
| P0 요청별 peak | transport가 세대 drain 전에 peak를 반환. supervisor가 receipt로 옮겨 전체 g1 로그 재독 제거 |
| P1 dependency 준비 | worker READY 전에 기존 check_third_party 1회, 이후 요청은 identity 확인 |
| 실제 기동 receipt | executable 11개, 관측 library/loader 파일 1,887개, 준비 1.372367초 |
| MFA worker 기동 전체 | 7.802514초. 추론 시간이나 변경 전 대비 개선량은 아님 |
| 준비 실패 worker 차단 | prepared worker의 감시 로그가 10초 내 갱신되지 않으면 ready=false. 회전 중 직전 로그도 확인 |
| 추가 세부 span | initialize_database, setup_acoustic_model, phonological rules, disambiguation, lexicon/compiler, MFCC/CMVN/final features 등 |
| P2 2/4 thread 후보 | 각각 새 core/H5 생성. 실행 allowlist·운영 selector에는 등록하지 않음 |
| 실제 녹음·GPT 생성·모든 테스트·화면 검증 | 이번 작업에서 실행하지 않음 |
| 새 정상 상세 관측 | 0건, 속도 개선·8초 목표·품질은 미확인 |

peak는 실제 샘플링된 RSS/PSS/USS 값의 최대이며 측정되지 않은 항목은 null이다.
회전으로 raw sample을 버리더라도 peak는 남긴다. CPU 간격·RSS 실패 임계값,
fresh aligner/DB·원래 정렬/분석/export·DB close·lifecycle gate·3-request budget을
유지했다. sample 간 미관측 순간의 실제 최대 사용량이나 OS hard cap을 보장하지 않는다.

dependency 준비는 실행 파일의 내용 digest, 실행 파일/관측 library의 stat identity,
library directory·loader 설정·PATH/LD_LIBRARY_PATH/LD_PRELOAD/CONDA_PREFIX를
결속한다. LD_PRELOAD는 기존 절대 경로를 그대로 사용하고 해당 파일을 관측한다.
자산·환경 변경은 원래 subprocess 검증을 몰래 생략하는 대신 worker 실패·세대
폐기로 처리한다. 실제 요청의 준비 확인 비용은 아직 측정하지 않았다.

## 활성 릴리스와 비활성 후보

| MFA threads | core SHA-256 | H5 manifest SHA-256 | 상태 |
|---|---|---|---|
| 1 | `755667629b5d603910ff2646daa807bf7757e2d3f3ca29845e265e2e4dc79984` | `11d920cd07d3ed583885d6ea79d1bc2ee0a65903a42c98448cca87a37b4a5e94` | 운영 direct 활성 |
| 2 | `0546936b24332cad0115cb82368cb37e91b144585402a425f869491738cff78e` | `78f8fd1c28cc10b0ea594383fa2a6a2d0bc7a068168b304d490b0edf8eab04c6` | 비활성 후보 |
| 4 | `6bd9e0385955a2a6faff8c7aead0edb5e5a971a1200a17de364bc4c9d9b814a9` | `1f010d3e02c45ea852e396a344c52970d71e8e098fbcfd0d88ce9b9411c20433` | 비활성 후보 |

디렉터리는 `/workspace/intelligentai/validation/canonical_dlpc_mfa_prepared_20261025_t{1,2,4}_v2`
및 각 `_h5`다. 활성 lock SHA는
`fb322f24a263d385accb6b48f9f841bbaa8dd866b68b7129c5189b854ede553b`다.
GPT prompt SHA는 기존
`ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d`를 유지한다.

1-thread core의 변경은 runtime 8개와 historical_runtime_identity 1개다.
DirectAligner·Native·GOP·모델·음향/정규화 설정과 수치 연산 소스는 byte가 같다.
2/4 후보는 추가로 demo_common의 **MFA 자식만** thread 환경을 변경하고 CLI
params의 blas_num_threads를 맞춘다. NUM_JOBS=1과 동시 실행 슬롯 1은 유지한다.
각 H5는 adapter의 parent binding과 lock/manifest만 다르고 request config·prompt·
rubric·입출력 schema는 부모와 byte가 같다. Luna none/default이며 Fast는 사용하지 않는다.

내부 Backend의 AI_CANONICAL_CORE_ROOT/LLM_ROOT는 기존 GOP CUDA·announcer 조합을
유지하고 AI_DIRECT_CORE_ROOT/LLM_ROOT만 위 1-thread v2 쌍을 선택했다.
resource profile은 dlpc-4cpu다. TTS supervisor는 RUNNING, PID 460835,
uptime 8일 19시간대로 확인했으며 이번 작업에서 재시작·합성하지 않았다.

## 배포 과정·복구와 제한

활성 작업 0을 두 차례 확인하고 소유 hold로 접수를 닫은 뒤 분석만 중지·적용·
기동했다. 최초 준비 코드가 기존 CUDA preload를 거부해 worker 기동에 실패했다.
또 초기 private schema 등록에서 H5 manifest/lock enum 누락을 준비 guard가 차단했다.
모두 사용자 분석 제출 전 발견했으며 실패 상태를 성공으로 게시하지 않았다.
M0 코드·schema·health·core/H5를 함께 롤백해 **06:39:17 UTC** local·공개 readiness
200으로 복구한 후, preload를 보존하는 v2로 다시 반영했다. root payload의 기준
경로 연결도 정합화했으며 최종 payload는 저장소 밖에만 둔다.

영속 복구 payload/selector는
`/home/2020067/.local/share/intelligentai/recovery/startup-20261006/mfa-prepared-20261025-v2`다.
selector 값은 prepared이며 복구 hook에서 M0/GPT보다 먼저 선택한다.
[restore_mfa_prepared.py](../../deploy/dlpc/restore_mfa_prepared.py)는 fixed digest의
runtime 9개·core/H5를 복구한다. `--rollback`은 분석이 중지된 상태에서 M0 runtime·
health·schema·direct 쌍과 M0 selector를 함께 복원하고 준비 재사용 selector를 끈다.
비밀 설정은 기존 root parser로 읽고 direct root 두 개만 변경하며 env 사본을 남기지 않는다.

원시 로그 상한은 **새 direct prepared worker**에 적용된다. 내부 Backend의 기존
core·기존 종료 generation 로그를 함께 수정하거나 삭제하지 않았다. `/tmp`는
최종 관측에서 393,396,224 bytes가 남았다. 종료 generation 정리와 내부 기존
감시 로그의 수명 관리는 후속 운영 과제이며 디스크 고갈 전체가 영구 해결됐다고
표현하지 않는다.

## 실제 확인과 변경 파일

| 확인 | 결과 |
|---|---|
| 변경 Python/JSON 구문 | Python 25개 compile, JSON 2개 parsing |
| v2 core 3개 | 각 Python 62개 compile·모든 packaged file SHA 확인 |
| H5 3개 | Python adapter compile·lock/file SHA 확인, GPT policy byte 보존 |
| private schema | 실제 의존성으로 구조 compile, 실제 서비스 UID에서 선택할 identity·closed tuple·dependency SHA preflight 통과 |
| 배포 runtime | 9개가 브랜치 및 복구 pin과 SHA 일치 |
| 영속 helpers | restore·hook·health restore·읽기 집계 4개가 브랜치와 일치·compile |
| 문서 정적 확인 | local 링크 47개 누락 없음, 충돌 표식·Git diff 확인 통과 |
| 새 core | vc-core-mfvfy225, READY, 실제 준비 receipt 확인 |
| 운영 | local·공개 readiness 200, hold 없음, active 0 |
| 임시 테스트 파일 | 생성하지 않음. 운영 payload와 불변 후보는 실행 자산으로 유지 |

이번 후속의 주요 변경은 다음 파일이다. 기존 GPT/M0 prerequisite도 새 main 기반
worktree에 연결했다. 이전 worktree와 사용자 변경은 보존했고 삭제된 테스트 자산은
복원하지 않았다.

- [build_mfa_prepared_release.py](../../deploy/dlpc/build_mfa_prepared_release.py)
- [mfa_prepared.py](../../deploy/dlpc/performance/mfa_prepared.py)
- [mfa_memory_watchdog.py](../../deploy/dlpc/performance/mfa_memory_watchdog.py)
- [mfa_trace.py](../../deploy/dlpc/performance/mfa_trace.py)
- [restore_mfa_prepared.py](../../deploy/dlpc/restore_mfa_prepared.py)
- [summarize_mfa_latency.py](../../deploy/dlpc/summarize_mfa_latency.py)
- [recover_on_start.py](../../deploy/dlpc/recover_on_start.py)
- [restore_resident_health.py](../../deploy/dlpc/restore_resident_health.py)
- [canonical_harness.py](../../src/voice_coach/backend_analysis/canonical_harness.py)
- [native_execution.py](../../src/voice_coach/backend_analysis/native_execution.py)
- [resident_health.py](../../src/voice_coach/backend_analysis/resident_health.py)
- [private v4 schema](../contracts/runpod_result_v4.schema.json)
- 이 문서와 [docs 목차](../README.md)

남은 작업은 정상 사용의 상세 trace에서 준비 확인 비용·실제 MFA 시간·lexicon 내부
비용을 읽는 것, P2의 실제 효과와 품질 판단, P3의 요청 상태/정렬 의미 설계다.
이 작업에서 추가 시간 측정 요청을 만들지 않았다. commit/push/PR은 수행하지 않았다.
