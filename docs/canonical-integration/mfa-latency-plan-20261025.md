# MFA 시간 개선 계획

작성: 2026-10-25. 상태: **M0 상세 계측·direct 전용 core/H5 선택을 운영 반영. 실제 성능·품질 관측 대기**.
브랜치: `docs/mfa-latency-plan-20261025`, local main
`ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 생성.

## 목표와 실행 범위

4~6초 한국어 단일 발화군에서 MFA 호출 전체의 정상 요청 p50을 **8초 이하**로
줄이는 것을 1차 목표로 제안한다. 후속 목표는 **약 6초(최근 단일 값 대비 약 50%)**다.
아직 실현 가능성이나 단축 폭을 검증한 수치는 아니다. 긴 녹음은 길이·음소 수별로
나눠 판단하며 모든 입력을 같은 8초 목표에 맞추지 않는다.

MFA 음소·단어 경계, 원본 음성·대본 digest, 음소 index와 downstream GOP·운율·
코칭 근거를 보존한다. 실패·정렬 누락·조기 timeout으로 빨리 끝난 요청은 개선으로
인정하지 않는다. GPT의 Luna none/default, Native 모델, GOP 알고리즘·채점·공개
DTO는 이 최적화에서 함께 변경하지 않는다. Fast mode도 범위에 없다.

계획 작성 후 사용자가 수행을 지시해 M0 코드·선택·복구를 구현하고 운영 반영했다.
진행 결과는 [구현·운영 기록](mfa-latency-implementation-20261025.md)을 따른다. 추가 분석 호출은 하지 않는다. 모든 종류의 테스트·회귀·fixture·mock·벤치마크 실행기·화면 검증을
생성하거나 실행하지 않고 삭제된 파일도 복원하지 않는다. 이후 구현에서도 정적 확인과
승인된 운영 반영 후 정상 사용 요청의 관측을 구분한다. 추가 녹음 실행은 사용자가
명시적으로 요청할 때만 그 요청 범위에서 다룬다.

## 최근 실행에서 확인한 병목

기준은 2026-10-24 14:42 UTC의 실제 job
`d88a46e4-b461-496f-ba4e-80a5e129a8f6`이다. 기존 완료 자료만 읽었고 재실행하지 않았다.
입력은 `testaudio.m4a`에서 변환한 4.2666875초 PCM과
"쌀과 콩을 깨끗한 그릇에 담아요"다. PCM SHA-256은
`71dd0b1bd6929f0d921a630725c14f6f047cc27dcb978ac4c38b6d0a08770a1d`다.
이 값은 단일 관측으로 평균·p50/p95를 뜻하지 않는다.

| 구간 | 초 | 포함 관계 |
|---|---:|---|
| POST → 서버 결과 공개 | 21.813 | 전체 서버 경로 |
| POST → 클라이언트 완료 인지 | 22.670 | polling 지연 포함 |
| 바깥 코어 전체 | 14.899 | 아래 MFA 포함 |
| MFA 호출 전체 | **11.952673** | core의 mfa_done−mfa_start |
| MFA RPC 왕복 | 11.936299 | 호출 전체 안 |
| host 요청 전체 | 11.943181 | RPC와 관측점이 달라 중첩 |
| worker 왕복 | 11.749659 | RPC 안 |
| worker 요청 전체 | **11.672333** | 아래 workflow/분석/export/cleanup 포함 |
| alignment workflow | **8.949013** | setup·features·graph·정렬 등을 포함 |
| feature 추출 | **1.576248** | workflow 안, 다시 더하지 않음 |
| 정렬 분석 | 0.406636 | worker 요청 안 |
| TextGrid export | 0.035265 | worker 요청 안 |
| cleanup | 0.274189 | worker 요청 안 |
| worker 요청에서 위 4개 구간을 뺀 잔여 | **2.007230** | 아직 개별 단계 미분리 |
| queue wait / restart wait | 0.000351 / 0 | 포함 구간, 큰 병목 아님 |

MFA는 서버 공개 시간의 약 **55%**, 바깥 코어의 약 **80%**다.
호출 전체−worker 요청은 **0.280340초**이고, workflow−feature는 **7.372765초**다.
전자를 순수 IPC로, 후자를 순수 GMM decoding으로 단정하지 않는다.
2.007초도 전부 모델 로딩이라는 근거는 없다. 요청 준비·상태 snapshot·보고서 구성 등
workflow 타이머 밖에서 수행하는 작업을 추가 분리해야 한다.

같은 음성의 이전 Luna low 실행에서도 MFA는 11.959초였다. 11:00 UTC의 더 이른
Astra 실행은 15.606초였으나, 그때의 RPC−worker 왕복 약 3.33초를 최신 실행의
병목으로 재사용하지 않는다. 최신 RPC−worker 왕복 차이는 약 **0.187초**다.

다른 시간이 같다는 단순 가정에서 MFA를 8초로 줄이면 서버 공개 약 **17.86초**,
6초면 약 **15.86초**다. 이는 효과 계산이며 측정 결과·보장값이 아니다.

## 이미 적용된 것과 현재 소스 경계

10/24 사용 코어의 설치 자산을 10/25 읽기 전용으로 확인했다.
`/workspace/intelligentai/releases/canonical_dlpc_gop_cuda_20261007_v2`의 manifest는
`f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`이다.
오늘의 active env·readiness·CPU 부하는 조회하지 않았으므로 설치 자산 존재를
현재 서비스 정상이나 선택 상태의 새 증거로 쓰지 않는다.

| 소스/자료 | 구현 사실 |
|---|---|
| 코어 `runtime_bundle/resident_worker.py` | MFA 3.4.1 import·모델 경로 준비를 worker 기동에 수행. 매 요청 새 DB·aligner·joblib cache·config 구성, workflow 실행 뒤 cleanup·상태 복원 |
| `runtime_bundle/direct_adapter.py` | DirectAligner로 단일 발화 corpus import·TrainingGraphCompiler·GmmAligner 직접 호출. NUM_JOBS=1과 speaker adaptation 없음 등을 검사 |
| `runtime_bundle/mfa_sidecar.py` | resident worker 준비·호출, queue adapter, DirectAligner 선택. 추가 버전 확인용 전체 CLI 기동은 이미 제거됨 |
| `runtime_bundle/worker_transport.py` | selector 기반 pipe 수신. 고정 간격 polling 제거가 이미 포함됨 |
| `scripts/run_release.py` | scratch는 이미 `/tmp/canonical_ai_release_*`. 저장장치·mount 종류는 별도 미확인 |
| 최근 worker receipt | NUM_JOBS=1, SINGLE_SPEAKER=true, USE_MP/USE_THREADING=true, BLAS_NUM_THREADS=1, USE_POSTGRES=false, SEED=0 |
| 최근 cleanup 근거 | lifecycle gate PASS, DB close PASS, 요청 temp 제거 true |

최신 실행의 실제 요청 root도 `/tmp/canonical_ai_release_7312e904327b/tmp/requests/...`였다.
이미 resident·단일 발화 경로·`/tmp`를 사용하므로 이를 새 개선으로 중복 제안하지 않는다.
프로세스 상주가 aligner 객체·DB·음향 모델 내부 상태까지 요청 간 재사용한다는 뜻은 아니다.

저장소의 [mfa_timing.py](../../deploy/dlpc/performance/mfa_timing.py)는 요청별 inclusive
span wrapper이고, [build_performance_release.py](../../deploy/dlpc/build_performance_release.py)는
이를 넣은 **별도 후보**를 만들지만 GOP 중첩·thread/device 변경도 함께 포함한다.
최근 사용 코어에는 이 상세 wrapper가 없어 전체 성능 후보를 바로 활성화하지 않는다.
현재 Luna none H5는 과거 builder의 기본 부모와도 다르다.

main에는 다른 worktree의 미커밋 GPT 변경이 아직 없다. 구현 시 먼저 실제 배포 SHA와
`feat/gpt-api-latency-20261024`의 필요한 변경을 연결한다. main 전체를 운영에 덮어쓰거나
미배포 J406 성능 profile을 env만으로 켜지 않는다.

## 개선 순서

### M0 — 정렬 workflow와 반복 준비를 세분화

현재 코어·GPT 동작을 유지한 **MFA 계측만의 파생 릴리스**를 첫 구현 단위로 만든다.
기존 wrapper를 바탕으로 실제 호출되는 메서드에만 계측하고 없는 메서드는 미관측으로
남긴다. 이전 성능 후보의 GOP 중첩·GPU·batch·thread 변경을 함께 가져오지 않는다.

1. worker 진입, stat identity 확인, 상태 snapshot, CLI context/모델 callback,
   모델·사전·G2P 조회, aligner 생성, DB/config 구성, 보고서 직렬화·전달을 분리한다.
   초기 snapshot 앞의 비용처럼 기존 request 타이머 밖의 구간도 별도 표시한다.
2. workflow 내부의 setup/DB/corpus import/normalize/dictionary, feature/CMVN,
   graph compile, GMM alignment·출력 수집을 나눈다. DirectAligner의 실제 메서드와
   설치된 MFA/kalpy 코드에서 경계를 확인한다.
3. 모든 span에 parent·start/end·inclusive 의미를 부여한다. 중첩 구간을 합산하지
   않고 exclusive 잔여를 산출한다. 관측 불가·누락을 0으로 꾸미지 않는다.
4. wall/process CPU, 가능하면 자식 누적 CPU와 I/O를 구분한다. 실제 worker/자식
   affinity·cgroup quota/throttling·thread 수, RSS/PSS와 임시 경로의 저장장치를
   읽기 전용으로 확인한다. host 전체 코어 수를 사용 가능 CPU 수로 해석하지 않는다.
5. generation/ordinal(1·2·3), 준비된 worker의 첫 요청/후속 요청, restart/queue 대기,
   입력 길이·음소 수·core/model/profile을 함께 남긴다. 텍스트·음성·env·키는 새
   성능 로그에 기록하지 않고 정렬 근거 본문은 기존 보존 경로를 유지한다.

산출물은 private MFA 진단과 정상 요청의 읽기 전용 집계다. 품질 근거를 지우거나
CPU/I/O 숫자를 순수 정렬 연산 시간처럼 표현하지 않는다. 전체 workflow 8.949초 중
무엇이 큰지 확인되기 전에는 특정 계산이 병목이라고 확정하지 않는다.

### M1 — 요청마다 반복하는 불변 자산 준비 축소

M0의 약 2.007초 잔여와 workflow setup 중 실제 반복 비용이 확인되면 적용한다.
고정 모델 ID·revision·사전·G2P metadata와 CLI의 변하지 않는 옵션 해석을 worker
기동 시 검증·준비하고, read-only 자산만 worker 수명 안에서 재사용한다.
요청별 경로·script/audio binding·출력 위치는 매번 새로 구성한다.

반복 HF callback·오프라인 asset 탐색·모델/사전 파일 해석이 실제로 중복되는지부터
확인한다. 이미 cache된 부분을 다시 최적화했다고 보고하지 않는다. 파일 내용을 검증한
기동 identity와 요청별 identity 변경 감지를 유지하고 자산 변경 시 cache를 폐기한다.

PretrainedAligner·SQLite session·config.MEMORY·현재 workflow·speaker/utterance
행은 그대로 공유하지 않는다. 이들은 mutable 요청 상태이며 worker의 config 복원,
fresh DB, cleanup·생존 프로세스 확인은 유지한다. snapshot·hash 검사를 무조건
삭제하는 방식도 제외한다. 준비·직렬화에서 큰 비용이 확인된 경우에만 중복 작업을
줄이고 원본 보고서·문제 조사에 필요한 진단은 보존한다.

### M2 — feature·graph·정렬 내부의 큰 구간 개선

M0에서 확인된 가장 큰 한 구간부터 별도 릴리스로 적용한다.

| 구간 | 조건부 개선 | 보존할 것 |
|---|---|---|
| feature/CMVN | 같은 요청 안의 반복 decode·WAV 변환·feature 계산·archive 읽기 제거 | sample rate·frame shift·윈도·정규화·features 값과 범위 |
| dictionary/lexicon/FST | 모델·사전 revision에 결속된 불변 자료의 반복 로딩·컴파일 축소 | phone symbol table·G2P·OOV·정규화 규칙 |
| TrainingGraphCompiler | 비용이 큰 경우에만 bounded read-only transcript graph cache 검토 | 모델/사전/G2P digest·정규화 transcript·컴파일 설정을 key에 포함, 요청별 utterance ID·출력 binding 재구성 |
| GmmAligner | 반복 음향 모델 읽기/준비와 실제 decoder 계산을 분리한 뒤 불변 자료 재사용 검토 | alignment model·silence boost·beam/retry beam·scale·seed와 정렬 결과 |

다른 음성의 feature·alignment·TextGrid 결과를 재사용하지 않는다. 같은 문장이라도
녹음은 새로 정렬해야 한다. graph cache도 요청 DB·speaker 상태나 결과 cache와
구분하고, 공유 객체의 mutable 상태가 배제되지 않으면 도입하지 않는다. cache
개수/byte 상한·무효화·실패 시 원래 계산 경로를 명시한다. 재시도와 재추론을 뜻하지 않는다.

feature 1.576초를 전부 없애는 것만으로 6초에 도달할 수 없다. 7.373초의 workflow
잔여 안에서 setup·graph·decoder를 나눠야 후속 목표의 가능성을 판단할 수 있다.

### M3 — 근거가 있을 때 MFA 전용 CPU 예산 조정

CPU 연산이 지배적이고 실제 quota/affinity에 여유가 확인된 경우에만 BLAS/OMP 등의
1→2, 필요 시 4 thread 후보를 순차 검토한다. 각 후보는 MFA 자식 환경과 실제
`effective_config.BLAS_NUM_THREADS`를 함께 명시한다. 상위 env만 바꿔도 효과가
생긴다고 가정하지 않으며 Native/TTS/API의 thread·장치 선택은 유지한다.

NUM_JOBS는 우선 1로 유지한다. 현재 adapter가 이를 검사하고 발화도 한 개라
num_jobs 증가가 선형 속도 향상을 보장하지 않는다.
[MFA 공식 3.4 문서](https://montreal-forced-aligner.readthedocs.io/en/v3.4.0/user_guide/workflows/alignment.html)는
single_speaker가 발화 기준으로 나누고 speaker adaptation을 끈다고 설명한다.
따라서 adaptation 끄기 역시 새 개선이 아니다. 공식 일반 CLI 권장보다 설치된
3.4.1 DirectAligner 구현·적용 설정을 우선 대조한다.

beam을 줄이거나 retry 경로·fine tune·G2P/OOV 정책을 생략하고, 음향 모델·
align_one/HF 모델·정렬기를 교체하는 변경은 이 계획의 초기 범위에서 제외한다.
현재 이미 single-utterance direct 경로이므로 CLI를 align_one으로 바꾸는 것만으로
개선된다고 판단하지 않는다. 품질·의미 변경이 필요한 대안은 별도로 설계한다.

### M4 — 세대 교체 대기와 꼬리 지연 분리 관리

최근 요청은 generation 1/ordinal 1, restart wait=0이었다. 재기동·세대 교체는
이번 11.95초의 주원인이 아니므로 M1/M2보다 우선하지 않는다. 정상 사용에서
준비 지연이 확인되면 [resident 관리](../../src/voice_coach/backend_analysis/canonical_resident.py)와
[MFA health](../../src/voice_coach/backend_analysis/resident_health.py)의 세대별 준비 시간을 관측한다.

기존 3-request 수명·요청별 정리·메모리 guard는 유지한다. 예열은 실제 메모리·CPU
여유가 입증된 경우에만 고려하고 신규 접수와 중복 실행을 만들지 않는다. 빠른 응답을
위해 필수 cleanup 완료 전에 다음 작업을 받거나 generation을 무제한 연장하지 않는다.

## 채택·완료 기준

승인된 운영 반영 이후 자연 요청의 요약을 사용한다. warm/첫 요청/재시작,
4~6초 등 길이·음소 수, 부하·core·모델·thread profile별로 비교한다.
기준과 후속 각각 최소 30개 정상 요청을 초기 판단에 사용하되 p95의 작은 표본
한계를 표시하고, 부족하면 미확인으로 남긴다. 반복 호출로 표본을 채우지 않는다.

| 기준 | 완료 조건 |
|---|---|
| 성능 | 비교 가능한 4~6초 정상 요청군의 MFA 호출 p50 ≤8초. 6초는 후속 목표이며 별도 판단 |
| 꼬리 지연·실패 | 동등 조건에서 p95·timeout·정렬 누락·retry·fallback 악화 없음. 실패는 시간 통계에서 제외하되 전체 분모에 남김 |
| 품질·근거 | 개발자가 기존 보존 자료와 정상 결과의 phone/word label·순서·경계·coverage와 GOP/운율 근거 결속을 판단 |
| 수치 의미 | 수치 변경 없는 최적화는 기존 결과의 의미 동등성이 기준. 허용 경계 오차가 필요하면 실제 model frame 설정을 확인하고 채택 전에 명시. 임의 오차를 사후 도입하지 않음 |
| 상태 격리 | 새 요청 DB/config·출력 binding, lifecycle gate·DB close·temp cleanup·취소/기한 처리와 메모리 제한 유지 |
| 효과 보고 | MFA 호출·workflow·feature·exclusive 잔여·전체 결과 공개를 분리. GPT·polling 변동을 MFA 개선으로 귀속하지 않음 |

정렬 분석 약 0.407초·cleanup 약 0.274초·export 약 0.035초를 없애는 것은
초기 개선안이 아니다. 필요한 근거 생성과 정리를 유지한다. 문서·구문·readiness
확인을 정렬 품질·실제 성능 확인으로 표현하지 않는다.

## 구현 경로와 배포·복구

첫 구현 산출물은 **MFA-only 계측 파생 builder와 private 관측·집계**다.
기존 [단일 발화 runtime 파생](../../deploy/runpod/native_mfa_source.py)와
[성능 wrapper](../../deploy/dlpc/performance/mfa_timing.py)를 참고하되 과거 후보의
GOP 변경·옛 H5 선택을 섞지 않는다. 임의 in-place patch 대신 현재 코어에서
새 immutable core와 호환 H5를 파생한다.

MFA 코드가 바뀌면 core manifest가 바뀐다. 따라서 GPT 최적화 때의 H5 변경과
달리 **새 core/H5 쌍**이 필요하다. H5의 parent core·adapter binding·lock을
갱신하되 Luna none/default prompt·rubric·completion 정책을 유지한다.
[서버 identity](../../src/voice_coach/backend_analysis/canonical_harness.py),
[실행 허용](../../src/voice_coach/backend_analysis/native_execution.py), private preflight의
core/H5/lock/prompt tuple과 실제 digest도 함께 등록해야 한다.

첫 적용은 문장연습 direct로 한정한다. 최근 GPT 변경은 direct LLM root만 분리했으므로
공용 `AI_CANONICAL_CORE_ROOT`만 바꾸면 기존 내부 H5와 조합이 깨질 수 있다.
[direct service](../../src/voice_coach/direct_analysis/service.py)에 **direct 전용 core 선택과
LLM root를 같은 승인 쌍으로 검사하는 경계**를 구현해야 한다. 이 기능은 M0의 [release_selection.py](../../src/voice_coach/direct_analysis/release_selection.py)에 구현했다.
`AI_DIRECT_CORE_ROOT`와 H5를 서버 승인 쌍으로 확인하며,
내부 Backend/core/H5 선택을 보존하고 direct 자식 환경에만 적용한다.

적용 시 대상·현재 pins·자산/버전·진행 작업을 다시 확인하고, 소유 hold→drain→
분석 전환·워밍→local/공개 readiness→재개 순서를 따른다. TTS를 함께 재시작하지
않으며 B2 보관·AWS 이력 동기화와 실제 생성·정렬 품질을 별도로 관측한다.
공용 core 또는 내부 계약까지 바꿀 경우에는 Backend verifier·지원 tuple·schema
digest·배포 revision을 별도 조율하고 기존 내부 admission을 임의로 열지 않는다.

지속 recovery의 runtime 파일 SHA·core/H5 자산·선택도 새 쌍과 함께 갱신한다.
실패 시 admission을 닫고 진행 상태를 조정한 뒤 이전 runtime·core/H5·preflight·
복구 선택을 함께 되돌린다. env만 되돌리거나 자동 복구가 새 패치를 다시 켜게 두지
않는다. 취소·timeout·응답 유실·전송 실패를 자동 재정렬이나 GPT 재실행으로 복구하지 않는다.

## 이번 작업의 확인과 남은 일

읽은 자료는 최근 실제 CORE의 mfa_receipt, 설치된 immutable MFA 소스와 저장소의
builder·resident·기존 성능 후보·문서다. 계획 당시에는 읽기·문서화만 했다. 이후 M0 구현과 운영 상태 확인·반영을 수행했다.
실제 생성·모든 종류의 테스트·화면 확인·commit/push/PR은 수행하지 않았다.

변경 파일·정적 확인·운영 반영은 구현 기록에 구분했다. 임시 검증 파일은 만들지
않고 기존 변경·삭제·미추적 파일을 보존했다. 다음은 정상 사용의 M0 관측을
근거로 M1/M2 중 비용이 큰 부분부터 선택하는 일이다.
