# DLPC 4 CPU와 A5000을 활용하는 마이그레이션 계획

작성일: 2026-10-05. 갱신: 2026-10-06. 상태: **DLPC 설치·진단 기동 확인 완료, 운영 전환 미완료.** 실제 관측과 남은 조건은 [마이그레이션 실행 기록](dlpc-migration-execution-20261005.md)을 따른다.

아래 Seungun/Scored v2 사양은 최초 계획의 역사적 기준이다. 실행 시 확인한 실제 기준은 **Native-only v4**이며 폐기된 Seungun을 다시 기동하지 않는다. CUDA v2 후보에서 Native/MFA와 TTS 준비를 확인했으며 AWS SSH, NAS quota/복원, 개발자 품질 QA가 남아 있다.

사용자 결정은 기존 Seungun의 단일 CPU 전제를 유지하면서 DLPC의 CPU 4개와 VRAM 24GB를 효율적으로 사용하는 것이다. 목표는 모델을 GPU에 한 번 적재하고 CPU 단계를 제한된 프로세스로 재사용하는 구조다. 초기에는 분석 1건, 검증 후에는 요청 간 단계 중첩으로 최대 2건을 처리한다. CPU 개수만큼 전체 파이프라인과 모델을 복제하지 않는다. 실제 최적 동시성은 개발자의 메모리·지연·처리량 측정으로 확정한다.

## 확인된 사양과 현재 구현

2026-10-05 21:48 KST 이후 RunPod에서 `ssh dlpc-migration`으로 조회했다. 후속 소스·토폴로지 조회도 같은 날 수행했다.

| 항목 | 관측 및 해석 |
|---|---|
| DLPC CPU | Xeon Silver 4210R. affinity 10,16,26,28의 논리 CPU 4개. 네 CPU는 서로 다른 물리 코어이며 두 소켓에 걸쳐 있다. 호스트 전체 40개를 사용 가능 자원으로 계산하지 않음 |
| DLPC 메모리 | `/proc/meminfo` 총 11GiB, 당시 가용 약 9.3GiB. swap 없음. 별도 cgroup `memory.max`, `cpu.max`는 노출되지 않아 한도·회계 범위의 관리자 확인 필요 |
| DLPC GPU | RTX A5000 24GB, `nvidia-smi` 총 24,564MiB, 가용 24,164MiB, 사용 1MiB. 실행 중인 CUDA 계산 프로세스 없음 |
| 환경 | Ubuntu 24.04.4 컨테이너, Python 3.12.3, Conda 있음. `docker`는 Podman remote 실행 파일. 명령 존재가 새 컨테이너·GPU 할당 권한을 뜻하지 않음 |
| 저장소 | root overlay 50GiB이나 현재 사용자는 `/`, `/opt`에 쓰기 불가. 홈은 쓰기 가능한 NFS이며 개인 quota·보존 정책 미확인. `/tmp` 약 5GiB, `/dev/shm` 약 7GiB이며 모두 용량을 추가 RAM으로 합산하지 않음 |
| 기존 점유 | Jupyter, MLflow, MinIO, Ollama 등이 실행 중. 소유자 승인 없이 종료하지 않음. 관측한 8080/8082는 미점유이나 배포 직전 재확인 필요 |
| 현재 연결 | RunPod의 SSH 별칭은 `127.0.0.1:23990`에 연결. 이 경유 SSH는 이전 중에만 사용하고 완료 후 유지하지 않음. 실제 분석·TTS 요청은 AWS와 DLPC 사이에서 전달 |

RunPod에서 확인한 운영 worker revision은 `32d98e39767b56697bc7e58fb50e3e9d61432349`, core는 `canonical_scored_core_20261003_v2`, H5는 `canonical_scored_h5_20261003_v3`다. 실제 실행기·동결 release를 기준선으로 포장하며 로컬 checkout 전체를 운영 소스로 간주하지 않는다.

- Native runner에 `torch.set_num_threads(8)`와 CPU FP32 모델/입력 처리가 명시돼 있다. 주변 OpenMP/MKL/OpenBLAS 환경의 2스레드 제한만으로 이 8스레드 설정이 없어지지는 않는다.
- Native 계약 검사에는 `device`가 `CPU float32`로 시작해야 한다는 조건이 있다. GPU 전환에는 코드뿐 아니라 새 파생 release와 장치·계산 근거 계약이 필요하다.
- Native/MFA `parallel=True`는 `PARALLEL_NATIVE_MFA_DISABLED_AFTER_PRESERVED_120S_SYSTEM_FAILURE`로 차단된다. 과거 장애를 해소했다는 검증 없이 이 flag를 켜지 않는다.
- 운영 MFA 설정은 `active_workers=1`, `pending_queue=1`, 요청 timeout 120초, worker당 요청 3회 후 교체다. 현 파이프라인은 요청별 `active/state`를 closure로 공유하므로 같은 객체를 여러 요청에서 동시에 호출하면 안 된다.
- 현재 Native·최종 발음 분석의 장치는 CPU이며 TTS는 CUDA다. TTS는 GPU를 사용하면서도 RAM RSS 약 2.04GiB를 사용한다. 보존된 분석 79의 MFA worker tree 최고 RSS는 약 784MiB였고 이는 전체 분석 메모리가 아니다.
- 로컬 조사 기록 `docs/canonical-integration/testaudio1-timing-20261005.md`의 전체 응답은 197.855초, 준비 두 구간은 134.451초, 실제 코어 분석은 30.300초였다. Native forward 1.080초만 가속해서 전체 준비 지연이 해결되지는 않는다. 로컬 조사 기록 `docs/canonical-integration/core-preparation-bottleneck-20261005.md`를 함께 적용한다.

## 목표 프로세스와 CPU 배분

다음 표는 **초기 실행 예산 제안**이다. 프로세스 개수를 정확히 4개로 제한한다는 뜻은 아니다. 서비스와 감시기의 가벼운 스레드를 포함해 동시에 CPU를 많이 사용하는 작업을 통제한다.

| 논리 역할 | 담당 | 계산 예산 |
|---|---|---|
| CPU A | 음성 decode·정규화, G2P, 요청별 후처리·근거 조립 | 무거운 CPU 작업 1개, 기본 계산 스레드 1 |
| CPU B | 단일 MFA worker와 그 순차 작업 | 기존 단일 CPU 전제 유지, 내부 job/thread 1, 정렬 1건 |
| CPU C | Native·Seungun 공용 GPU worker의 입력 준비·결과 회수 | CPU 계산 스레드 1, GPU 분석 실행 1건 |
| CPU D | API·lease/heartbeat·취소·handoff, TTS CPU 처리·인코딩 | API는 비동기 I/O, 무거운 TTS CPU 작업은 1개 |

CPU 번호는 시작 시 `sched_getaffinity()`에서 가져온다. 0~3이나 이번 관측 번호를 코드에 고정하지 않는다. 초기에는 OS 스케줄링과 작업 수 제한을 사용하고, affinity 고정은 GPU의 NUMA 위치와 실측 효과를 확인한 경우에만 적용한다. GPU 실행 대기 중의 CPU를 유휴로 묶지 않으며 백그라운드 notebook 서비스와의 경쟁도 관측한다.

모든 새 계산 worker는 라이브러리 import 전에 `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`을 적용하고 Torch 초기화 전에 intra-op/inter-op를 각각 1로 설정한다. Native의 명시적 8스레드 설정과 TTS의 기존 2스레드 설정도 대상별로 조정한다. ffmpeg, MFA/Kaldi, tokenizer 등 별도 thread/job 옵션은 실제 parser를 확인해 1개로 제한한다. 환경변수만으로 모든 라이브러리 제한이 적용됐다고 보고하지 않는다.

두 번째 CPU를 같은 Seungun 호출에 주는 방안은 후속 비교 후보다. 먼저 단일 CPU worker를 유지하고 독립 단계의 중첩에서 이득을 얻는다. [PyTorch multiprocessing 지침](https://github.com/pytorch/pytorch/blob/main/docs/source/notes/multiprocessing.md)은 다중 프로세스의 과도한 CPU 스레드 생성과 CUDA 초기화 이후 fork의 위험을 설명한다. CUDA worker는 spawn 또는 독립 실행으로 시작하며 각 자식 내부에서 CUDA를 초기화한다.

## GPU 상주와 요청 처리

### 모델을 보유하는 프로세스

- 분석 GPU worker 하나가 Native와 Seungun 모델을 각 한 사본씩 보유한다. 두 모델의 패키지·버전 동거가 불가능하면 격리 프로세스 두 개를 사용하되 모델 복제 없이 공통 GPU 실행 슬롯을 공유한다. 같은 의존성 환경으로 억지로 합치지 않는다.
- TTS는 기존 별도 서비스와 모델 child를 유지한다. 상태·장애·오디오 계약을 분석과 분리한다.
- GPU 분석과 TTS의 동시 커널 실행은 초기에는 합계 1개로 제한한다. 프로세스마다 독립 lock을 두지 않고 공통 coordinator에 실행 슬롯을 요청한다. 두 모델의 동시 실행은 메모리와 TTS/분석 지연 검증 후 확대한다.
- 모델은 시작 시 순서대로 로딩해 CPU staging 메모리 최고치를 제한한다. CPU state dict·불필요한 사본은 실제 참조를 해제하고 RSS/PSS 감소를 확인한다. MFA 준비와 겹치는 로딩은 cold peak가 메모리 예산 안에 들어온 뒤 적용한다.
- 처음에는 FP32와 기존 전처리·모델·임계값을 유지한다. FP16/BF16/양자화·TF32 허용은 별도 품질 변경으로 다룬다. GPU FP32도 CPU와 비트 단위 동일성을 보장하지 않으므로 경계 음소 판정과 최종 점수를 비교해야 한다.
- 상주 모델과 G2P 엔진은 재사용하지만 요청별 waveform·대본·관측 상태·출력 경로는 분리한다. GPU tensor를 요청 간 공유 메모리에 보관하지 않는다. 짧은 RPC에는 실행 ID, 검증된 파일 참조·digest·deadline과 작은 결과를 사용한다.

### 초기 단계는 전체 분석 1건

기존 요청 안의 순서인 준비 → Native → G2P/MFA 및 후속 분석 → 근거 조립을 유지한다. 실제 의존성이 있는 Seungun·MFA 출력 결합을 앞당기지 않는다. 기존 API 접수 상한 3은 DLPC용 검증된 설정으로 1까지 낮출 수 있게 한다. 환경변수 이름만 추가하고 parser와 runtime이 여전히 3을 쓰는 상태를 남기지 않는다.

### 검증 후 전체 활성 요청 2건

다음 요청의 Native/GPU 처리를 앞 요청의 MFA/CPU 처리와 겹치게 한다. **각 요청 내부의 단계 순서는 유지**한다. 이를 위해 현재 공유 `active/state`를 요청 context로 분리하고 GPU adapter·MFA adapter를 통해 자원을 예약한다. 동결 batch 객체에 스레드 두 개를 붙이는 방식은 사용하지 않는다.

| 자원 | 목표 상한 |
|---|---:|
| 활성 분석 전체 | 2건, 검증 전에는 1건 |
| MFA 실제 실행 | 1건 |
| MFA 대기 | 최대 1건, 이미 접수한 두 분석 안에서만 존재 |
| 분석 GPU 실행 | 1건 |
| TTS 합성 실행 | 1건 |
| 분석과 TTS의 GPU 실행 합계 | 초기 1건, 검증 후에만 2건 검토 |

예를 들어 A의 MFA가 실행되는 동안 B의 Native를 처리하고, A가 외부 GPT 응답을 기다리는 동안 B의 코어를 처리한다. GPT는 외부 API이므로 CPU 또는 GPU 슬롯을 점유한 채 기다리지 않는다. GPU 결과를 CPU의 최소 근거·파일로 확정한 뒤 GPU 실행 슬롯을 반환한다. 최종 handoff가 완료될 때까지 전체 활성 분석 수에는 포함한다. 이 구조의 처리량은 가장 느린 단일 MFA 단계와 원격 대기에도 제한되며 4배 개선을 가정하지 않는다.

대기열은 bounded이며 기존 capacity 오류·claim·lease·deadline을 유지한다. 대기 시간도 deadline에 포함하고 남은 시간이 부족하거나 자원 부족이면 접수 전에 거절한다. TTS 요청이 분석에 무한히 밀리지 않도록 대기 시간과 deadline을 고려해 교차 배정한다. 만료·취소 요청은 모든 대기열에서 제거한다.

## 메모리와 worker 수명

아래 수치는 **DLPC 11GiB 환경의 초기 운영 예산 제안이며 실측 요구량이 아니다**.

- RAM: 기존 notebook 서비스까지 포함한 사용량 목표를 8.5GiB 이하로 두고 약 2GiB 이상 여유를 확보한다. 새 서비스들의 동시 최고 증가분은 기존 점유를 뺀 범위에서 결정한다. 시작·모델 로딩·최대 길이 음성 처리 모두 이 예산을 적용한다.
- `MemAvailable`이 2GiB 미만이면 신규 분석과 새 TTS 합성을 보류한다. 기존 요청은 deadline·개별 worker 메모리 감시 아래 종료·인계를 진행한다. 표시 meminfo가 실제 할당량과 일치하는지 관리자에게 확인하고, 확인된 cgroup 한도가 있으면 둘을 함께 사용한다.
- GPU: 전체 점유 목표를 20GiB 이하로 두고 드라이버·일시 workspace·다른 사용자 작업을 위한 여유를 둔다. 실제 모델 적재량과 최대 입력의 peak를 측정하기 전 남은 VRAM만 보고 동시 실행을 확대하지 않는다.
- `/dev/shm` 7GiB와 `/tmp` 5GiB를 추가 RAM으로 취급하지 않는다. 대용량 PCM/tensor 큐와 pinned memory를 제한한다. swap 증설을 처리 용량의 근거로 삼지 않는다.
- MFA의 3회 recycle과 기존 resident의 3회 제한을 초기에는 보존한다. worker 상태 격리·RSS 증가 추세가 확인된 뒤 길이를 늘리는 별도 revision을 만든다. 두 세대를 동시에 올리는 교체 방식은 11GiB의 cold peak 예산을 먼저 통과해야 한다.
- GPU worker 종료·CUDA 오류가 발생하면 해당 세대를 unhealthy로 표시하고 신규 접수를 닫는다. 실행 종료 확인 없이 슬롯을 반환하지 않는다. GPU 작업을 안전하게 취소할 수 없으면 완료 후 결과를 폐기하거나 세대를 정리하고, 다른 대기 요청에는 명시적 실패를 전달한다. 조용한 CPU fallback·자동 재채점은 하지 않는다.

## 자산과 배포 계약

1. 현재 실행 revision·모델 SHA·Python/패키지·MFA/G2P 사전·ffmpeg·core/H5/verifier manifest를 고정하고 의존 파일 closure를 목록화한다. 모델 환경뿐 아니라 hard-coded validation 경로와 사전·정책 파일도 포함한다.
2. GPU 장치 및 단일 thread 변경은 별도 불변 core/H5 파생 release로 만든다. CPU 장치 assertion을 단순 삭제하지 않고 CUDA 장치·dtype·런타임 identity를 기록한다. Backend의 승인 identity와 verifier 패키지도 새 release에 맞춰 준비한다. 기존 receipt를 새 SHA로 다시 쓰지 않는다.
3. 홈 NFS에는 검증된 배포 원본·복구 자료를 보존하고, 실행 환경·자주 읽는 모델은 관리자 승인된 쓰기 가능한 로컬 디스크에 staging하는 안을 우선 검토한다. root 50GiB의 writable bind mount 또는 별도 quota 확보가 필요하다. `/tmp` 5GiB에 전체 모델·환경을 설치하지 않는다. 로컬 staging이 불가하면 NFS cold 시작·fsync·rename·quota 조건을 측정해 대안을 판단한다.
4. 임시 WAV·MFA DB·Unix socket은 짧은 로컬 작업 경로에 둔다. handoff journal과 재전송 원본은 컨테이너 재시작 보존이 확인된 private 저장소에 유지한다. 현재 UID로 0700/0600을 적용하고 기존 `/root`, UID 10001, `/workspace`, `/opt` 가정을 실제 경로/권한으로 정리한다. 키는 모델 archive에 넣지 않는다.
5. 새 사용자 서비스 supervisor를 사용해 logout 이후 유지·재기동·로그 회전·정리를 제공한다. pidfd, subreaper, child 회수, `/proc` 접근은 실제 계정에서 확인한다. Python에 `pidfd_open` 심볼이 있다는 것만으로 전체 guard 지원을 판정하지 않는다.
6. UI·공개 분석 DTO·점수 산식·B2 보관 책임·외부 GPT 모델을 유지한다. GPU 전환으로 수치 판정이 달라지면 원인을 해결하거나 별도 정책 결정을 받는다. 마이그레이션에 미지원 코스/영상/시험 기능을 추가하지 않는다.

## 이전 접속과 실제 서비스 연결

사용자 결정에 따라 `ssh dlpc-migration` 경유 접속은 이전 중에만 사용한다. RunPod를 접속 중계기로 계속 유지하거나 별도의 상시 관리 접속을 구축하지 않는다. 다만 AWS에서 DLPC의 분석·TTS API를 호출하는 실제 서비스 연결은 이전 대상에 포함된다. 기존 `ai.voice-coaching.site` HTTPS 도메인을 유지하고 내부 upstream을 변경한다.

- DLPC에서 AWS로 연결하는 역방향 SSH 또는 조직이 허용하는 private VPN 중 접근 정책에 맞는 경로를 구축한다. 초기 제안은 AWS loopback에만 연결되는 별도 분석/TTS 터널이며 SSH 키에는 필요한 포워딩만 허용한다.
- 외부 HTTPS 도메인은 AWS에서 계속 종료하고 내부 upstream만 DLPC로 전환한다. RunPod proxy URL이 코드·환경에 직접 남아 있는 분석, TTS, 준비 상태 조회, FE 직접 접수 계획의 실제 활성 여부를 대조한다. DLPC로의 새 익명 공개 포트를 만들지 않는다.
- 분석·TTS 서버 인증, callback HTTPS, 녹음 읽기, GPT API 연결을 각각 유지한다. SSH 연결 성공만으로 전체 서비스 연결 성공이라고 판단하지 않는다.
- 세션별 실행 소유자를 고정하고 전환 중 기존 RunPod 요청은 RunPod에서 마친다. 같은 request/execution을 두 호스트에 동시에 보내지 않는다. deadline·callback·journal이 정리된 뒤 신규 접수를 DLPC로 옮긴다.

## 단계별 실행과 완료 조건

| 순서 | 작업 | 완료 조건 |
|---|---|---|
| 0 | DLPC 할당·서비스 수명·디스크 권한·NAS quota·AWS API 연결 확정 | AWS의 분석·TTS 요청이 DLPC에 도달하고 실행/영속 저장 공간과 자원 한도가 확인됨 |
| 1 | 자산·의존성·기준 요청/결과 보존, DLPC 파생 release 설계 | 모델/근거 hash·버전·원복 release 목록이 완전함 |
| 2 | 단일 CPU thread와 단일 활성 요청의 CPU 호환 경로 준비 | 기존 근거·점수·상태 처리 의미가 유지되고 guard/메모리 문제가 없음 |
| 3 | 상주 Native/Seungun GPU worker·TTS·MFA, 시작 로딩 예산 적용 | GPU 장치가 실제 사용되고 CPU 기준과 음소 판정/점수/출처 호환성이 개발자 검증됨 |
| 4 | 요청 context 분리 및 분석 2건 단계 중첩 | 1건 대비 완료 처리량 증가, p95 지연/실패율 악화 없음, RAM/VRAM 예산 유지 |
| 5 | DLPC 신규 요청으로 제한 전환 | 분석 저장·TTS 실제 생성·오디오 품질·이력·취소/재시도·재시작 복구가 각각 확인됨 |
| 6 | 확대와 RunPod 정리 | 진행 중 RunPod 작업·미인계 journal이 없고 사용자 승인 범위에서 중지. 삭제는 별도 결정 |

단계 2는 호환성 확인용 기준 경로이며 최종 CPU-only 배포 결정을 뜻하지 않는다. 2건 단계 중첩이 메모리나 지연 기준을 통과하지 못하면 상주 GPU 모델을 유지한 1건 구성으로 인계한다. 이 계획만으로 3~4건 동시 실행을 허용하지 않는다.

검증은 개발자가 수행한다. 비교는 동일 입력·최대 허용 길이·모델 warm/cold·TTS 병행·반복 요청에서 CPU 시간/사용률, 전체 RAM/PSS와 cold peak, GPU 사용량/peak, 대기·단계별 p50/p95, 완료 건수, 오류와 timeout을 기록한다. 짧은 요청 한 번을 throughput으로 일반화하지 않는다. score와 음소 index/시간 구간·원본 digest·request/execution 연결, 경계 점수 사례, 생성/저장 분리도 확인한다. 자동 회귀·브라우저 도구·새 테스트 산출물을 이번 계획 작업에서 실행하거나 생성하지 않는다.

서비스가 준비되었다는 판단은 API liveness, 모델·MFA·GPU 준비, Backend claim/callback, GPT 실제 생성, TTS 합성, 결과 품질로 나눠 인계한다. 시간 목표는 기존 197.855초 관측만으로 새 초 수를 약속하지 않고 준비 반복 제거와 요청 간 중첩 효과를 계측해 정한다.

## 변경 위치와 원복

- AI: `canonical_producer.py`의 thread 환경, `http_runtime.py`/`canonical_lane.py`의 검증된 접수 상한·자원 예약, `canonical_resident.py`와 실제 파생 release builder/host의 GPU 모델 소유·요청 상태 격리·재기동 정책. 현재 CPU 8스레드를 유지하는 옛 resident builder를 그대로 배포하지 않는다.
- AI frozen core: `source/scripts/run_canonical_batch.py`, MFA adapter/설정 및 장치·model manifest. 운영 release를 현장에서 덮어쓰지 않고 새 release를 생성한다.
- gateway/TTS: `deploy/runpod/api-gateway/supervisor.py`와 DLPC용 서비스 실행 구성에서 CPU budget·모델 장치·공통 GPU 예약·private 경로 반영.
- Backend: 새 producer identity/verifier 승인, 분석/TTS upstream과 readiness/capacity 설정을 확인하고 필요 변경만 `develop` PR로 인계한다. 변경한 AI 코드는 `main` PR 대상으로 인계한다. FE는 실제 활성 endpoint 계약 변경이 있는 경우에만 수정한다.
- 실패 시 DLPC 신규 접수를 닫고 해당 호스트의 진행 요청을 drain하거나 명시적으로 종료한다. 기존 RunPod release로 신규 요청을 돌리며 승인된 실행 소유·idempotency 규칙을 유지한다. 저장된 결과·녹음·B2 객체와 복구 journal을 삭제하지 않는다.

2026-10-05 실행 조사에서 운영 배포가 Native-only v4로 변경된 사실을 추가 확인했다. 위 Seungun/Scored v2 내용은 최초 계획 당시 관측이며, 실제 이전 기준은 현재 Native v4다. 중단된 옛 모델을 다시 활성화하지 않는다. 코드·복사·운영 전환의 현재 상태는 실행 기록으로 구분한다.
