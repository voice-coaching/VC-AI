# MFA M0 상세 계측 구현·운영 반영

초기 반영 관측: **2026-10-25 02:38 UTC**, DLPC J406.
브랜치: `feat/mfa-latency-20261025`, local main
`ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 생성.

## 결과와 남은 범위

[MFA 개선 계획](mfa-latency-plan-20261025.md)의 **M0 상세 계측과 direct 전용
core/H5 선택·복구 경계**를 구현하고 운영 반영했다. 새 core hash의 resident READY와
local·공개 direct readiness 200/ready=true를 확인하고 소유 hold를 해제했다.
내부 Backend core/H5, GPT Luna none/default, TTS, 정렬·GOP·thread 설정은 유지했다.

**실제 녹음·GPT 호출과 모든 종류의 테스트를 실행하지 않았다.** 새 릴리스의
정렬 관측은 0건이며 8초 목표·속도 개선·정렬 품질은 미확인이다. 준비 상태를
실제 추론·품질 확인으로 해석하지 않는다. 계획대로 M1/M2 최적화 지점은 상세
계측 자료가 쌓인 후 결정한다. 모델·사전 cache나 CPU 증가를 근거 없이 함께 켜지 않았다.

## 계측 구현

[mfa_trace.py](../../deploy/dlpc/performance/mfa_trace.py)는 worker의 실제 호출을
감싸며 원래 함수·인자·반환·예외·정리 순서를 유지한다. request ContextVar와
thread별 부모 stack으로 요청 상태를 분리하고, 기록은 **최대 256 span**으로 제한한다.

| 관측 영역 | 단계 |
|---|---|
| 요청 경계 | validation, module identity stat, 초기 snapshot, request state/DB/cache 구성 |
| 반복 준비 | CLI context 생성, config load, third-party 확인, parameter parse, dictionary/G2P 조회, aligner 생성 |
| workflow | setup/database/corpus import/normalize/dictionary/jobs/features/graph/alignment의 실제 호출 |
| 후처리 | alignment 분석, TextGrid export, cleanup, DB close, config/env reset, gc, snapshot, lifecycle gate·temp 제거, worker report 쓰기 |

없는 메서드는 unavailable로 남긴다. 각 span은 parent ID, 상대 시작/종료,
inclusive wall과 직접 자식 시간 구간의 합집합을 뺀 exclusive wall을 제공한다.
겹치는 자식·동시 작업·중첩 label을 전체에 합산하지 않는다. process CPU·회수된
자식 누적 CPU·context switch·block I/O도 기록하지만 이 카운터 역시 중첩된다.
RSS는 프로세스 수명 최대값이며 새 요청의 정확한 peak로 표현하지 않는다.

worker affinity, 숫자로 된 BLAS/OMP 환경값, namespace root의 노출된 cgroup
quota/stat/memory/cpuset를 허용 목록으로 수집한다. 상위 cgroup·스토리지 종류·
전체 자식의 실시간 CPU를 안다고 가정하지 않으며 PSS는 미관측(null)이다.
cgroup 수치는 TTS 등 다른 서비스 사용량도 포함할 수 있다.

성공한 worker 반환의 `mfaPerformance`는 기존 MFA receipt를 통해 retained CORE에
포함된다. 예외에서도 가능한 경우 generation의 private reports에
`g*_r*_*_performance.json`을 별도로 남긴다. 그 파일은 입력·경로·원문·키를
기록하지 않는 숫자/상태 위주의 진단이다. 기존 worker report의 내용은 유지한다.
별도 관측 저장 실패는 원래 결과를 실패로 바꾸지 않는다. validation 이전 오류,
강제 종료 또는 generation 정리로 실패 진단이 없는 경우는 누락으로 보고하며
모든 실패의 영구 수집을 보장하지 않는다.

Trace의 관측 상태는 반환·예외 뒤에 초기화한다. DB·aligner·MFA 전역 config와
모델의 요청 상태를 재사용하는 cache는 추가하지 않았다. 기존 3-request budget,
cleanup gate·취소·기한·메모리 제한과 IPC 응답 1 MiB 제한도 유지한다.

## 불변 core/H5 파생과 선택

[build_mfa_trace_release.py](../../deploy/dlpc/build_mfa_trace_release.py)는 현재 core와
Luna none H5 부모의 고정 manifest/lock을 검증하고 새 디렉터리에 생성한다.
부모 core에서 바뀐 파일은 정확히 다음 세 개다.

- `runtime_bundle/resident_worker.py`: 같은 실행 경로에 계측 wrapper 추가.
- `runtime_bundle/mfa_trace.py`: 새 bounded 계측 helper.
- `config/historical_runtime_identity.json`: 복사되는 worker/helper의 고정 hash 갱신.

나머지 Native/MFA adapter·정렬·GOP·runtime 기동·설정 자산은 byte가 동일하다.
H5는 새 parent core binding과 dependency lock/manifest만 갱신했다.
**request config·prompt·rubric·확장 출력 schema·canonical parent schema는 기존
Luna none 부모와 byte가 같다.** 과거 GOP 중첩·thread/device 변경 후보를 가져오지 않았다.

| 자산 | SHA-256 |
|---|---|
| parent core | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` |
| M0 core | `c9c7d893ebbf6e5a2fa7762c3fa2e72b33b4ada391c945ce564c59d4d4a5bf82` |
| M0 H5 | `1d227a6654060357c04fb1a592826bba2bfc464cc536837e2f142cc612573211` |
| M0 H5 lock | `06907765cad8d14300e41fa6cf8709e32e11edf89c9bf021b945e534348e9579` |
| 보존 prompt | `ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d` |

자산은 `/workspace/intelligentai/validation/canonical_dlpc_mfa_trace_20261025_v1`과
그 `_h5` 디렉터리다. core 83개 파일 중 Python 60개와 H5 Python의 구문·lock/file
hash를 확인했다. 패키지에 tests/fixtures 경로는 없고 테스트 자산을 추가하지 않았다.
빌드 시점 manifest의 미검증 상태는 실제 성능·품질 통과로 바꾸지 않았다.

[release_selection.py](../../src/voice_coach/direct_analysis/release_selection.py)는
`AI_DIRECT_CORE_ROOT`가 있으면 `AI_DIRECT_LLM_ROOT`도 요구한다. 서버가 등록·실행
허용한 H5 identity와 선택된 core manifest SHA가 일치해야 direct 자식의
AI_CANONICAL_* 환경에 반영한다. 잘못된 쌍은 direct ready_error로 닫고 다른
core로 자동 fallback하지 않는다. 내부 환경과 기존 LLM-only override 동작은 보존한다.
전체 자산 검증은 기존 resident 준비 절차에서 계속 수행한다.

서버 harness·Native 실행 허용과 private v4 preflight의 core/H5/lock tuple에
M0 조합을 닫힌 값으로 등록했다. 공개 DTO·내부 v6 전환은 수행하지 않았다.

## 운영 반영·복구

1. J406의 현재 선택·hold 없음·활성 작업 0을 확인했다. 이전 direct 선택은
   공용 기존 core와 Luna none H5였고 resource profile은 dlpc-4cpu였다.
2. 고정 SHA의 runtime 8개와 새 core/H5를 persistent recovery payload에 준비했다.
   이전 GPT 변경도 브랜치에 통합하고 정상 복구에 필요한 code baseline을 포함했다.
3. 소유 marker `mfa-trace-20261025`로 admission을 닫고 활성 작업 0을 두 차례 확인했다.
4. 분석만 중지하고 적용했다. 실제 변경은 harness·native 허용·service·preflight·
   새 release_selection 5개다. GPT 관측 runtime 3개는 이미 같았으며 복구 payload에
   포함된 상태를 확인했다. 분석을 시작하고 새 core의 resident READY를 확인했다.
5. local·공개 direct readiness 200/ready=true 확인 후 자신의 hold를 해제했다.
   최종 관측의 activeJobs/directJobs는 0, hold는 없었다. TTS는 재시작하지 않았다.

내부 `AI_CANONICAL_CORE_ROOT`는 기존 release,
`AI_CANONICAL_LLM_ROOT`는 기존 announcer H5를 유지한다. 새 core/H5는 direct
전용 두 설정으로만 선택한다. `/health/deployment`의 기존 core 표시는 내부
설정의 값이므로 direct 선택을 대신하지 않는다. source revision
`791ca5496e04a10b6948c69dce97361ee4335f4c`와 기존 bundle을 위장해 바꾸지 않고
`operationalPatches.mfa-trace-20261025`에 별도 core/H5·파일 SHA를 기록했다.

[restore_mfa_trace.py](../../deploy/dlpc/restore_mfa_trace.py)는 known main/Luna/GPT
baseline 또는 이미 설치된 digest만 허용한다. 변경 시 분석 정지를 요구하고
코드와 이전 direct 선택의 rollback snapshot을 root 전용 경로에 보관한다.
현재 private 설정을 기존 parser로 읽어 두 선택값만 변경하며 env·키를 payload나
백업으로 복사하지 않는다.

recovery는 `mfa-trace-20261025/selected=trace`일 때 M0를 먼저 복원한다.
explicit `--rollback`은 분석이 정지한 상태에서 이전 파일·direct core/H5·metadata를
복구하고 selector를 disabled로 바꾼다. 이후 기존 GPT 복구 분기를 따른다.
resident recovery helper도 새 service hash를 인식하도록 갱신했다. 자동 복구가
옛 helper로 새 파일을 덮어쓰거나, env 원복만으로 다음 부팅에 M0가 다시 켜지지 않게 했다.
rollback 명령과 전체 cold 복구는 실행하지 않았다.

## 관측 집계와 실제 확인 결과

[summarize_mfa_latency.py](../../deploy/dlpc/summarize_mfa_latency.py)는 보존된
job·CORE·worker-complete만 읽는다. 요청 생성·재실행·녹음 전송 기능은 없다.
core/ordinal·PCM 파일 byte 구간·음소 수 구간·NUM_JOBS/BLAS·노출된 CPU quota·
재기동 여부로 나누고, 성공한 MFA의 호출/workflow/feature와 trace label별
inclusive/exclusive 통계를 표시한다. 실패·미수집 receipt·pending은 별도 분모에 남긴다.
파일 byte 구간을 정확한 오디오 길이로 가정하지 않는다.

기존 10/24 요청 자료를 읽은 집계는 정상 MFA **3건**, MFA 호출 중앙값 11.959초,
workflow 중앙값 9.007초, feature 중앙값 1.612초였다. 상세 trace는 0건이다.
이는 이전 수동 실행 세 건의 요약이며 SLA/p95나 충분한 운영 표본이 아니다.
M0 반영 후 02:38 UTC에는 새 완료·진행 요청 모두 **0건**이었다.

```sh
sudo -n /home/2020067/.local/share/intelligentai/python/cpython-3.12.3/bin/python3.12 -B \
  /home/2020067/intelligentAI-mfa-speed/deploy/dlpc/summarize_mfa_latency.py \
  --since 1792839600
```

| 실제 확인 | 결과 |
|---|---|
| 변경 production Python 구문 | 19개 메모리 내 compile 통과 |
| 변경 JSON | 2개 파싱 통과 |
| private preflight schema | 실제 서비스 의존성으로 구조 컴파일 통과 |
| core·H5 | 모든 file hash·Python 구문 확인, core 변경 범위 3개로 제한 |
| GPT 설정·prompt·rubric·schema | 부모와 byte 동일 |
| 적용 runtime과 브랜치 | 8개 SHA 일치 |
| persistent helpers와 hook | helper 3개 브랜치 일치, hook 구문 통과 |
| 새 core resident | READY, coreManifestSha256=c9c7…5bf82 |
| local·공개 direct readiness | 200/ready=true |
| 추가 분석·테스트·화면 검증 | 생성·실행하지 않음 |

변경은 기존 GPT prerequisite 통합을 포함한다. 이번 MFA 주요 파일은 builder,
mfa_trace helper, 읽기 집계, restore helper, release_selection 모듈이다.
기존 service/harness/native 허용/private schema와 recovery 두 파일을 수정했고
계획·이 기록·docs 목차를 갱신했다. 이전 worktree·사용자 변경과 테스트 파일은 보존했다.
임시 검증 스크립트·fixture는 만들지 않았다. core/H5와 복구 payload는 운영 자산으로 유지한다.
commit/push/PR은 수행하지 않았다.

남은 것은 정상 사용의 상세 span·CPU/I/O/메모리 관측, 개발자 정렬 품질 판단과
M1/M2의 우선 대상 선택이다. 8초 목표 달성과 실제 재측정은 미완료이며,
새 요청을 만들거나 기존 결과를 재실행해서 표본을 채우지 않는다.

## 후속 집계 보완과 현재 관측

후속 브랜치 `feat/mfa-latency-followup-20261025`는 local main
`ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 새 worktree로 만들었다.
이전 MFA/GPT 구현의 미커밋 파일을 연결했으며 기존 worktree와 그 테스트 파일은
수정·복원·실행하지 않았다. 이번 후속 변경은 집계 도구와 이 문서다.

[summarize_mfa_latency.py](../../deploy/dlpc/summarize_mfa_latency.py)는 성공한
완전 trace의 단계별 요청 합계에서 **exclusive 중앙값 순위**와 관측 건수,
exclusive 누락 건수를 제공한다. 같은 label의 호출 중 하나라도 exclusive가 없으면
그 요청의 해당 exclusive 표본을 제외한다. 누락을 0초로 바꾸지 않는다.
inclusive 표본과 exclusive 표본의 모집단이 다를 수 있으므로 각각 count를 남긴다.
상위·하위 단계의 중앙값이나 동시 실행 구간을 더해 전체 시간으로 해석하지 않는다.

`optimizationReview`의 M1/M2는 다음 소스 검토 대상이다. 요청 준비 단계는 M1,
workflow 단계는 M2, cleanup·snapshot·계측 자체 등은 M0_REVIEW로 표시한다.
순위는 최적화 적용 승인·인과관계·속도 개선 증명이 아니다. CPU thread 변경은
자동 선택하지 않는다. 완전 trace가 없으면 `WAITING_FOR_COMPLETE_TRACE`이며,
30개 미만이면 초기 표본 충분 조건도 충족하지 않는다.

설치된 MFA **3.4.1** 소스를 읽은 결과 `from_pretrained`의 HF resolver는 이미
offline cached resolver를 사용하고 worker 기동에서도 실행된다. 반면 요청별 모델
객체의 `meta`는 acoustic model의 `meta.json`을 읽고, `available_dialects`는 사전
디렉터리를 탐색한다. dictionary/G2P 조회는 새 객체를 만든다. 이 사실만으로
준비 비용이 병목이라고 판단하거나 mutable 모델·aligner를 공유하지 않았다.
M1 캐시 및 M2 계산 변경은 실제 상세 span이 확보된 후 선택한다.

**2026-10-25 03:28:22 UTC, DLPC J406 실제 관측:**

| 확인 | 결과 |
|---|---|
| M0 적용 준비 시각 이후 retained 요청 집계 | cohort 0개, 진행 0건, 읽기 실패 0건 |
| 상세 trace에 따른 개선 대상 | WAITING_FOR_COMPLETE_TRACE |
| local `/health/deployment` | HTTP 200, activeJobs=0, directJobs=0 |
| local `/direct/readyz` | HTTP 200, ready=true |
| direct admission hold | 없음 |
| 새 분석 요청·실제 GPT 생성·테스트·화면 검증 | 실행하지 않음 |

후속 집계 도구는 Python 구문을 메모리에서 컴파일한 뒤 persistent recovery 경로의
기존 helper SHA·root 소유권을 확인하고 원자적으로 교체했다. 반영 SHA-256은
`09f66cbd4296f591ce902554032c4c57e94376d90786e1cb4b224fd4e4bfeb9a`다.
실제 retained 운영 자료만 읽었으며 합성 입력·fixture·테스트 파일은 만들지 않았다.
core/H5·분석 서비스의 코드와 설정은 이번 후속 단계에서 변경하지 않았고
재시작하지 않았다. 이번 후속 확인은 공개 endpoint를 다시 조회한 결과가 아니다.

남은 작업은 상세 MFA 기록 확보, 가장 큰 구간에 대한 M1/M2 후보 구현과 그 후
실측이다. 1회 녹음 API 실측을 테스트 금지의 예외로 허용할지 확인 요청했으나
응답 전에는 호출하지 않는다. 정상 사용자 요청이 들어오면 추가 분석 없이 이
집계 도구로 읽을 수 있다. 후속 변경의 commit/push/PR은 수행하지 않았다.
