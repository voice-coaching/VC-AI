# DLPC CPU/GPU 확인 및 MFA·GOP 최적화 계획

2026-10-07. 작업 유형: 운영 자원 확인·아키텍처/성능 설계. 상태: **사용자 제공 11:54 KST 자원 관측 반영 및 계획 보완 완료. 구현·추가 추론·배포 미수행.**

적용 규칙: `.codex/ai_rule_developer/GLOBAL_RULES.md`, `ARCHITECTURE_RULES.md`, `.codex/runpod.md`, 루트 `AGENTS.md`. 모델·음소·시간 근거와 현재 GOP 점수/전문가 피드백 계약을 보존한다. API 경계에 자원 스케줄링 로직을 추가하지 않는다.

## 1. 확인된 사실과 미확인 상태

| 항목 | 근거와 상태 |
| --- | --- |
| CPU | 사용자 터미널 관측 11:54:36 KST: Intel Xeon Silver 4210R @ 2.40GHz, 조회 프로세스 affinity `[28,34,36,38]`로 논리 CPU 4개. 물리 코어 수·각 서비스 affinity·보장 CPU quota는 별도 확인 필요 |
| cgroup | `0::/`, `cpuset.cpus.effective=0-39`. 조회 프로세스 affinity가 더 좁으므로 40개 CPU를 사용할 수 있다고 해석하지 않음. `cpu.max`/`memory.max`/`memory.current` 출력 없음. 표시된 cpu.stat의 nr_periods/nr_throttled/throttled_usec=0은 해당 계층의 관측이며 상위 제한·분석 당시 throttling 부재를 증명하지 않음 |
| GPU | 같은 사용자 출력 묶음: NVIDIA RTX A5000, 총 24,564 MiB, 사용 4,076 MiB, 표시값 차이 20,488 MiB(20.008 GiB). GPU/메모리 utilization 모두 0%. 독점 할당 및 부하 중 peak는 미확인 |
| RAM | MemTotal 11,534,336 KiB=11 GiB, MemAvailable 5,386,852 KiB=5.137 GiB, swap 0. 실제 컨테이너 hard limit는 파일 미노출로 미확인 |
| 현재 배포 | 11:50:48 KST AWS 경유 API 직접 조회: source `df90eb10be7165dd29b77454c368da76733eac6b`, bundle `b7170dda1a4ec5a520f30dfa88a0f57f649f9c118060975957e7f68b27b94e78` |
| 현재 분석 상태 | 같은 시각 ready, executor/handoff ready, activeJobs/directJobs 각각 0. CPU/GPU 사용률을 뜻하지 않음 |
| 실제 모델 장치 | 11:35 testaudio1 CORE 근거의 Native device `cuda:0`, float32 |
| MFA 실제 설정 | 해당 실행 receipt: NUM_JOBS=1, BLAS_NUM_THREADS=1, SINGLE_SPEAKER=true, USE_MP/USE_THREADING=true |
| 배포 CPU 설정 | launcher의 OMP/MKL/OpenBLAS/NumExpr 각 1; Torch intra/inter-op 각 1. direct 분석 활성 1건 |
| GPU 실행 조정 | 분석·TTS 공용 GPU lock 1개. VRAM이 비어 있어도 동시 실행 허용 근거가 되지 않음 |

하드웨어 최신 근거는 사용자가 제공한 DLPC 터미널 출력이다. CPU 명령의 UTC 시각 `2026-10-07T02:54:36.284835+00:00`은 11:54:36 KST이며, 뒤이어 실행한 GPU/ps 명령에는 별도 시각이 없어 같은 묶음으로만 기록한다. 과거 비교는 [이전 기록](dlpc-migration-execution-20261005.md), 실행 시간은 [testaudio1 관측](testaudio1-announcer-timing-20261007.md)을 참조한다.

현재 AWS→DLPC 관리 경로는 분석/TTS API뿐이다. CPU/GPU telemetry API는 없다. 사용자 터미널에서 독립된 한 줄 조회 명령의 결과를 받았다. 최초 heredoc의 줄바꿈 유실 및 설명 제목까지 붙여넣은 명령은 실행되지 않았고, 이후 CPU/GPU/ps 명령은 성공했다. 기존 작업의 `worker-complete.json` 출력은 아직 받지 못했다. 명령행 전체·환경변수·비밀값은 수집하지 않는다. `ps %CPU`는 프로세스 수명 평균, nvidia-smi 출력은 순간 관측이므로 분석 중 최대 사용률로 해석하지 않는다. cgroup 파일 미노출은 무제한이라는 뜻이 아니다.

### 프로세스 점유 및 계획에 반영할 사항

| GPU PID | 관측된 실행 환경 | GPU 메모리 |
| --- | --- | ---: |
| 95181 | TTS venv Python | 1,040 MiB |
| 451091 | 분석 mac_prod311 Python | 1,410 MiB |
| 451090 | 분석 mac_prod311 Python | 1,606 MiB |

분석 실행 환경 프로세스 두 개가 합계 3,016 MiB를 점유한다. 이 출력만으로 모델 복제·누수·불필요한 프로세스로 단정하지 않는다. 소스에서는 `direct_analysis/service.py`와 `backend_analysis/canonical_lane.py`가 각각 별도 `CanonicalResidentCore`를 생성하며, direct는 별도 workspace를 사용한다. 두 경로가 각각 모델을 적재했을 가능성이 있지만 실제 PID→resident/socket→API 경로 대응은 미확인이다. 확인 없이 어느 하나도 종료하지 않는다.

TTS PID 95181의 RSS는 2,310,984 KiB(약 2.204 GiB)다. RSS는 공유 페이지를 포함하므로 프로세스 RSS 합계를 실제 RAM 사용량으로 간주하지 않는다. 제공된 ps 상위 행은 모두 %CPU=0.0이며, 이것은 CPU가 분석 중에도 유휴였다는 근거가 아니다. 분석 GPU PID 두 개의 CPU/RAM 값은 해당 상위 15개 출력에 없어 미확인이다.

## 2. 이번 실행의 병목

testaudio1 5.141초, job `6b33fdfb-9d05-4adc-9da8-a6c5248b6f70`. 한 번의 관측이며 p50/p95가 아니다.

| 구간 | 초 | 해석 |
| --- | ---: | --- |
| MFA 전 준비 전체 | 1.464 | Native GPU 추론 0.552초·decode 0.400초 포함 |
| MFA 호출 | 13.445 | 코어 내부의 71.7% |
| MFA 이후 GOP 전 처리 | 0.777 | 운율·정책/근거 조립 등 |
| GOP | 3.070 | 코어 내부의 16.4% |
| 코어 내부 합계 | 18.756 | resident 내부 시간, producer 전체와 구분 |
| 클라이언트 결과 수신 | 37.746 | GPT와 검증·파일 기록·전송 등을 포함 |

MFA worker 전체 13.326초 중 alignment workflow 10.547초(특징 추출 2.093초 포함), analyze/export/cleanup 0.743초, 나머지 준비·정리 2.036초다. workflow의 남은 8.453초를 순수 GMM decoding으로 단정하지 않는다. 세부 span이 없다.

**이미 적용된 최적화:** resident imports, `DirectAligner`의 단일 발화 corpus/graph/alignment 처리, queue 완료 알림 adapter. 이번 receipt의 `actual_direct_kalpy=true`로 direct 경로를 확인했다. 이 기능들을 새 개선으로 중복 제안하지 않는다. 다만 요청마다 새 DB·aligner·특징/graph 처리는 남아 있다.

GOP는 기존 Native logits를 사용하며 추가 음향 모델 추론은 없다. CPU float64 CTC로 각 음소의 대체·삭제 후보를 계산하고 batch=32로 반복한다. 현재 GOP 함수 입력은 `native`, 전체 기대 음소 metadata, vocabulary로 MFA 산출물이 아니다. 현재 실행 순서는 MFA/정책 처리 이후 GOP다.

## 3. 목표와 자원 배분

목표는 단일 요청 지연을 먼저 줄이고, 이후 처리량을 개선하는 것이다. CPU 100% 또는 GPU 사용률 자체를 성공 기준으로 삼지 않는다. 현재 점수 rubric, blank/token mapping, 전문가 프롬프트, 근거/시간 출처, 실패·취소 계약을 유지한다.

아래는 **현재 관측된 4 CPU affinity를 기준으로 하되, 서비스별 affinity와 상위 quota 확인을 전제로 한 후보 배분**이다. CPU 번호는 재부팅 후 다시 확인하며 과거 번호를 고정하지 않는다. direct와 Backend 연동 경로가 동시에 실행할 때도 합산 예산을 만족해야 한다.

| 작업 | 초기 후보 예산 |
| --- | --- |
| MFA | worker/job 1개, BLAS 1; CPU 1개 상당을 기준으로 계측 |
| GOP CPU | intra-op 2, inter-op 1 후보; 1·2·3·4와 비교하되 운영 기본값은 실측 후 결정 |
| API·I/O·TTS CPU 준비 | 나머지 1 CPU 상당의 여유. 고정 예약을 구현했다는 뜻은 아님 |
| Native/TTS GPU | 기존 상주 모델·공통 슬롯 1개 유지 |
| 동시 분석 | 성능 후보는 전역 코어 실행 1건을 기준으로 검증. 현행 direct의 활성 1건 제한만으로 Backend 연동 경로까지 전역 1건이라고 보장하지 않음. 두 경로의 공통 실행 예산을 우선 확인/설계 |

MFA 단계에서 사용하지 않는 CPU 예산을 GOP가 활용하도록 설계한다. 4개의 모델 프로세스를 만들지 않는다. 모든 프로세스에 전역 `OMP_NUM_THREADS=4`를 적용하지 않는다. Torch 스레드 값은 process 시작 시 설정하고 실행 중 다른 작업과 공유하는 전역 값을 요청마다 변경하지 않는다.

메모리 제안은 기존 운영 예산을 출발점으로 한다: 유효 RAM headroom은 확인된 cgroup 여유와 MemAvailable의 작은 값, 신규 작업 시작 시 최소 2 GiB 여유. 현재 MemAvailable 5.137 GiB에서 이를 빼면 **약 3.137 GiB가 추가 점유의 계산상 상한**이며, 기존 MFA/TTS의 이후 증가량도 여기서 사용하므로 GOP에 전부 배정할 수 없다. 초기 후보는 기존 resident 안의 제한된 GOP 작업을 우선하고 새 모델/PyTorch 프로세스 복제를 보류한다.

GPU 여유 20,488 MiB는 GOP GPU 후보를 검토할 메모리 여지는 보여주지만 실제 할당·peak·CTC 연산 지원이나 속도 개선을 증명하지 않는다. GPU는 **실제 할당된 장치/예산**에서 기존 모델·TTS peak와 여유를 뺀 만큼만 후보에 배정한다. 잠정 GPU 총점유 20 GiB 상한을 쓴다면 현 스냅샷 대비 약 16.02 GiB의 계산상 여유가 있으나, GOP에 이를 예약하거나 사용하라는 설정은 아니다. 공유 장치 여부를 확인하고 batch별 실측 peak로 더 작은 예산을 정한다. B2는 모델·릴리스 복구용이고 실행 중 MFA 임시 DB/특징 파일이나 GOP tensor의 swap으로 쓰지 않는다.

## 4. 단계별 실행안

### P0 — 자원 확정 및 세부 계측

1. 11:54 터미널 출력으로 조회 프로세스의 4 CPU affinity, GPU/VRAM, RAM 현재값을 확인했다. 남은 확인은 서비스별 affinity/quota, 분석 GPU PID 두 개의 역할·PSS, 분석 중 CPU/GPU peak다. PID→resident/socket→direct/Backend 경로 대응을 확인하고 두 진입점의 동시 실행 합산 예산을 설계한다. 두 경로를 유지하며 스케줄러를 공유하는 방안을 우선 검토하고, 단순 프로세스 종료나 요청 기능 비활성화로 메모리를 줄이지 않는다.
2. 기존 worker timing을 회수해 producer·expression·검증·파일 기록 시간을 완성한다. 순수 GPT API 시간과 expression 전체를 구분한다.
3. 후속 후보 release에서 MFA의 DB/corpus import, lexicon/G2P 준비, features, graph compilation, GMM alignment, export, cleanup을 각각 wall/process CPU 시간으로 계측한다. 스레드 수·context switch·I/O wait·cgroup throttling·RSS/PSS·GPU peak와 동일 executionId로 연결한다.
4. timing은 근거의 부속 진단으로 보관하고 점수/프롬프트 입력에 섞지 않는다. 내부 span 변화가 frozen schema/hash에 미치는 영향을 먼저 확인한다. root endpoint나 SSH 권한을 확대하지 않는다.

### P1 — GOP CPU 연산 개선과 같은 요청 내 MFA 중첩

**P1a: CPU GOP 최적화.** 현재 float64·blank=42·어휘/계산식을 유지한 채 1/2/3/4 thread를 비교한다. 후보 batch 32/64/128을 비교하고 frames·target 길이에 따라 tensor/CTC workspace 예산으로 batch를 제한한다. 긴 입력에 고정 큰 batch를 강제하지 않는다.

음소 간 동일 후보 transcript의 likelihood를 한 요청 안에서 재사용하고, 음소를 가로질러 후보를 묶어 CTC 호출 수를 줄이는 방안을 비교한다. 각 음소별 distinct 후보 집합과 분모를 보존한다. 다른 음소의 분모를 합치거나 중복 확률을 더하지 않는다. reference 확률을 반복 계산하지 않는다. target 생성의 CPU 비용과 CTC 연산 비용을 분리해 실제 이득이 있는 변경만 선택한다.

**P1b: MFA/GOP 중첩.** Native logits와 full metadata가 준비되면 bounded task 하나로 GOP를 시작하고 MFA와 겹친다. 기대 음소 입력은 불변 snapshot으로 전달한다. MFA 결과 및 최종 입력 판정이 완료된 뒤 원래 공개 허용 규칙에 따라 결과를 결합하며 GPT는 모든 필수 근거가 준비된 후 한 번만 호출한다.

초기 후보는 한 resident 내부의 제한된 작업 스레드와 별도 MFA 프로세스다. 실제 CTC 구현의 병렬 실행·GIL 해제와 경쟁은 실측한다. 별도 GOP process가 필요하면 `spawn` 기반 상주 worker 1개를 후보로 만들되 11 GiB RAM에서 PyTorch 추가 적재·IPC 비용을 먼저 측정한다. CUDA 초기화 후 fork하지 않는다.

실패·취소·deadline 시 GOP를 기다리거나 소유한 resident 세대를 정리한 뒤 자원을 해제한다. 백그라운드 task가 남은 상태로 입력 파일 삭제·세대 재사용·결과 공개를 하지 않는다. 재기동 후 미완료 작업을 자동 재추론하지 않는다.

현재 단일 관측의 수치만 대입하면 GOP 3.070초가 완전히 겹칠 때 resident 18.756→약 15.686초가 계산상 가능하다. **예측 보장값이 아니다.** CPU 경합·task/IPC 비용·메모리 압박을 포함한 실제 결과가 기준이다.

### P2 — MFA 요청 준비와 남은 workflow 개선

1. P0에서 큰 span부터 개선한다. `DirectAligner`/queue 최적화는 이미 적용돼 있으므로 먼저 현행 구체적 호출을 계측한다.
2. 읽기 전용 acoustic model/dictionary/G2P 자산의 재사용 가능 경계를 찾는다. model/dictionary/config digest가 같을 때만 공유하고 mutable aligner, DB session, 발화별 특징·정렬 결과는 격리한다. 검증된 재사용 구조가 없으면 현행 요청별 객체를 유지한다.
3. 동일 transcript의 정규화/lexicon/graph 준비를 cache 후보로 평가한다. model·사전·G2P·정규화 설정·transcript로 키를 구성하고, utterance ID와 request-specific graph 상태가 분리되는지 확인한다. 파일명/문장 문자열만으로 정렬 결과를 재사용하지 않는다. cold miss와 warm hit를 분리해 보고한다.
4. 공식 `align_one_hf`는 현행 direct 경로와 비교하는 후보일 뿐, 적용하면 빨라진다고 가정하지 않는다. 같은 모델/G2P/feature/silence/beam/adaptation 설정 및 TextGrid 결과 동등성이 필요하다.
5. `NUM_JOBS=4`는 단일 발화의 기본 해법에서 제외한다. 현재 argv validation, resident worker, direct/queue adapter 모두 1-job 계약이다. 다중 utterance 경로를 설계할 때만 별도 revision으로 검토한다. 음성을 임의 분할해 기존 문맥·정렬 경계를 바꾸지 않는다.

### P3 — GOP GPU 후보, CPU 개선 후 필요할 때

현재 A5000 Native GPU 모델과 GOP CPU 코드를 구분한다. GPU 모델을 쓴다고 GOP CTC도 GPU에서 실행되는 것은 아니다.

- 먼저 설치된 PyTorch/CUDA 조합에서 현재 float64 CTC·blank=42의 CUDA 지원 및 성능을 확인한다. 공식 문서의 CuDNN 경로 요건은 float32·blank=0·int32 및 길이 제한을 포함하므로 단순 `.cuda()`로 그 경로가 적용된다고 가정하지 않는다.
- float64 native CUDA 경로가 지원돼도 CPU 대비 빠른지, H2D/D2H·GPU lock 대기·TTS p95를 포함해 비교한다.
- float32/blank remapping/CuDNN은 수치·토큰 계약 변경 후보로 분리한다. 성능 개선을 위해 검증 없이 precision이나 분모 계산을 바꾸지 않는다. float64 CPU를 비교 기준으로 두고 승인 기준을 충족하지 않으면 GPU 후보를 폐기한다.
- GPU 사용 시 기존 분석/TTS 슬롯에 GOP를 포함한다. lock 범위에 실제 GPU 실행 완료가 포함되도록 동기화하고, 다른 작업을 기다리면서 GPU 슬롯을 점유하지 않는다.

### P4 — 여러 요청의 처리량 개선

P1/P2가 검증된 뒤에만 활성 분석 최대 2건을 별도 후보로 검토한다. A의 GPT 응답 대기 중 B의 코어를 실행하되 MFA 1개·GPU 슬롯 1개를 우선 유지한다. journal·request context·취소 소유권을 분리하고 inference/LLM 중복 실행을 방지한다. 단일 요청 지연 단축과 처리량 증가는 서로 다른 지표로 보고한다. 4 CPU라고 모델/분석을 4개 복제하지 않는다.

## 5. 개발자 검증 및 채택 기준

현재 요청은 계획이다. 이 문서 작성 중 새 추론/자동 회귀 테스트/브라우저 QA를 실행하지 않았다. 후속 실측은 개발자가 직접 수행한다.

- 같은 음원·문장·모델/prompt/seed 조건으로 현행과 후보를 교차 비교한다. 첫 요청, resident 후속 요청, 3회 recycle 이후, 반복/서로 다른 문장, 긴 입력, 무음/오류, TTS 병행을 구분한다. p50/p95 표본 수를 명시하고 단일 측정으로 p95를 만들지 않는다.
- CPU 사용률은 interval별 process-tree CPU seconds / wall seconds로 사용 코어 상당량을 구하고 실제 CPU 예산으로 정규화한다. host 전체 CPU 수나 thread 개수를 사용률로 대체하지 않는다. GPU는 이용률·메모리 사용·peak allocation·lock 대기를 함께 기록한다.
- 음소 배열/순서, mapping, 선택값, request/file identity, frame/sample 기준 정렬 경계, GOP 후보 집합·순위·coverage·finite 상태를 비교한다. float64 리팩토링은 exact 일치를 우선하며 수치 차이가 있으면 원인과 허용 오차를 검증 전에 명시한다. 변경된 GOP를 그대로 GPT에 보내고 답변이 좋아 보인다는 이유만으로 동등성을 인정하지 않는다.
- 구조/계산식 유지, 근거 누락·교차 요청 오염·점수 적격성 변화 없음이 필수다. LLM은 비결정적이므로 최종 자연어 문자열 일치나 단일 총점만으로 회귀 여부를 판정하지 않는다.
- **제안 채택 기준:** 동일 부하의 대상 단계 p50 20% 이상 감소, core p95 악화 없음, TTS p95 5% 넘는 악화 없음, 신규 실패/timeout/메모리 예산 위반 없음. 수치는 측정 전 개발자 검증 기준으로 확정할 제안이며 달성된 성능이 아니다. 격리·보존 계약을 통과하지 못하면 성능과 관계없이 채택하지 않는다.

## 6. 변경 경계와 배포 순서

| 위치 | 후속 변경 책임 |
| --- | --- |
| `deploy/dlpc/launch.py`, `gpu_budget.py` | 역할별 thread 예산·GPU 슬롯 설정, 실제 관측된 할당 반영 |
| frozen `source/scripts/canonical_native/seungun_gop.py` | CTC batch/중복 likelihood 재사용·선택된 backend |
| frozen `source/scripts/run_canonical_batch.py` | Native/metadata 준비 후 bounded GOP task와 MFA 결합 |
| frozen `runtime_bundle/resident_worker.py`, `direct_adapter.py` | MFA span·검증된 read-only 자산 재사용·요청 상태 격리 |
| `direct_analysis/service.py`, resident orchestration | P4에서만 여러 요청 조율·취소·소유권 |
| release builder/manifest/AI·Backend registry/verifier | 새 불변 identity 생성 및 허용 계약 동시 갱신 |

기존 frozen 파일을 운영에서 덮어쓰지 않는다. 단계별 새 core/H5/lock/manifest를 생성하고 현재 GOP 채점·복원 prompt를 유지한다. 검증한 후보만 AI main PR·Backend develop PR과 배포 bundle에 반영한다. 접수 hold → drain → 양쪽 계약 전환 → readiness/identity 검증 → resume → AWS 복구 identity와 B2 pin 갱신 순서다. 이번 프롬프트 전환에서 확인한 것처럼 Backend hold 중 admission=false는 정상이며, 대기 조건을 admission=true에 걸어 교착시키지 않는다.

실패 시 접수 차단을 유지한 상태에서 AI/Backend/profile/verifier/복구 pin을 이전 호환 묶음으로 조율해 복원한다. JAR 또는 prompt 하나만 원복하지 않는다. B2 archive, journal, 진행 중 사용자 작업은 성능 개선을 위해 삭제하지 않는다.

## 참고

- [MFA 공식 alignment 문서](https://montreal-forced-aligner.readthedocs.io/en/stable/user_guide/workflows/alignment.html): single-file 경로 및 utterance 기반 병렬화 설명. 실제 운영 MFA 3.4.1 구현·adapter를 최종 기준으로 비교한다.
- [PyTorch CTCLoss 문서](https://docs.pytorch.org/docs/stable/generated/torch.nn.CTCLoss.html): CuDNN 입력 조건 및 비결정성 주의. 현재 설치 버전의 실제 지원 여부는 별도로 확인한다.
- [기존 direct 최적화 기록](https://github.com/voice-coaching/intelligentAI/blob/df90eb10be7165dd29b77454c368da76733eac6b/docs/canonical-integration/direct-native-latency-20261006.md): DirectAligner·queue 최적화가 이미 포함된 경위. 배포 source revision의 동명 문서를 참조한다.

## 2026-10-07 ?? ??

[?? ?? ??](dlpc-mfa-gop-implementation-20261007.md)? ?? ????? core/H5/lock hash?B2 file ID??? ??? ????. GOP CPU batch? reference ???, MFA/GOP ??, ?? ?? ?? ??, MFA inclusive span? ????. ?? Python 63? ? runtime identity ?? 17?? ?? ??? B2 readback? ????. ?? ?? ??/Backend/?? pin? ?? ???? ???. P0 ?? ??? ??? ????? QA? ????, P2~P4? ? ?? ??? ?? ??? ?? ????.
