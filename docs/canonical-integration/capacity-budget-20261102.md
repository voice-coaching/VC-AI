# 요청당 자원 비용과 동시 처리 계획 — 2026-11-02

## 범위와 관측

사용자는 요청당 컴퓨팅 비용과 현재 자원으로 가능한 동시 처리 수를 추정하고 슬롯을
확대하도록 요청했다. 새 음성·추론·GPT 호출·부하 테스트 없이 기존 실행 기록과
운영 중 프로세스·장치 정보만 읽었다. 아래 수치는 실측, 계획 예산, 계산을 구분한다.

J406, 2026-11-02 01:03 UTC 이후 관측:

- CPU affinity 16개, 노출된 cgroup CPU quota는 unlimited. 보이지 않는 상위 제한은 미확인.
- 가용 RAM 약 171.93GiB. root filesystem 가용 17.73GiB, `/tmp` 가용 3.99GiB.
- A100 80GB 3개. 현재 frozen `dlpc-4cpu` 모델은 `cuda:0`을 사용한다.
  `gpu_budget.py`의 `/run/intelligentai/gpu.lock`이 Native GPU 추론을 직렬화한다.
  따라서 GPU 1/2의 160GiB를 현재 처리 용량에 더하지 않는다.
- 서비스 UID 10001의 최소 환경에서도 nvidia-smi inventory 읽기가 가능함을 확인했다.
- 유휴 resident 하위 프로세스 RSS 합은 각각 약 1.28~1.29GiB였다.
  Native 프로세스 VmHWM은 약 1.87GiB, MFA worker 약 0.39GiB이며 주변 프로세스를
  합하면 약 2.4GiB다. 이는 각 프로세스의 수명 최대치 합이며 동시 peak/PSS가 아니다.
  PSS는 접근할 수 없었다. 정확한 요청당 GPU peak는 측정하지 않았다.

## 단일 요청 비용

기존 최근 두 입력의 WAV 헤더 길이는 5.84초, 6.4506875초다.

| 항목 | 기존 기록에서 확인한 값 | 계획에 사용할 값 |
|---|---|---|
| warm CORE 경과시간 | 약 18.8~20.4초 | 약 20초 |
| EXPRESSION 경과시간 | 약 8.7~9.1초 | 약 9초, provider I/O 포함 |
| 전체 파이프라인 | 약 31~33초, 직렬화에서 실패 | 성공 latency/품질 결과로 해석하지 않음 |
| cold 준비 | resident 로그 약 22~26초 | 별도 추가 비용, 기존 3회 generation 수명 유지 |
| Native span | 약 0.33~0.39초 | GPU kernel 단독 시간으로 해석하지 않음 |
| CPU | 정확한 요청별 CPU-seconds 미계측 | 연산 단계당 4 logical CPU 예산 |
| RAM | RSS/HWM은 위 관측 | worker당 3GiB |
| VRAM | 요청별 peak/컨테이너 PID 매핑 미확인 | worker당 2GiB, 보수적 가정이지만 peak 보장 아님 |
| 작업 디스크 | 요청별 peak 미계측 | worker당 256MiB |
| `/tmp` | 최근 generation 약 수~수십 MiB | worker당 64MiB |

CPU 계획 비용은 warm CORE 약 20초 × 4 CPU = 약 80 CPU-seconds다.
할당 예산을 시간으로 환산한 값이며 실제 사용 CPU-seconds나 청구 비용이 아니다.
cold 준비 로그의 일부 CPU 시간만으로 MFA 자식까지 포함한 전체 비용을 만들지 않는다.
서버 시간당 요금·OpenAI 토큰 사용량/요금이 없어 원화·달러 비용은 산정하지 않았다.

## 현재 배치의 계산

short audio를 10초 이하로 한정하고 다음 여유분을 남긴다.

| 제약 | 계산 | 상한 |
|---|---|---:|
| RAM | floor((171.93−24) / 3GiB) | 49 |
| GPU 0 resident 메모리 | floor((80−8) / 2GiB) | 36 |
| 작업 디스크 | floor((17.73−8) / 0.25GiB) | 38 |
| `/tmp` | floor((3.99−1) / 0.0625GiB) | 47 |
| 무거운 연산 | floor((16−4) / 4 logical CPU) | 3 |

따라서 이번 관측에서 **동시 접수/진행 요청 상한 추정은 36개**, 그 안의
**cold 준비·CORE 연산 동시 예산은 3개**다. 대기·GPT I/O를 포함한 36개 요청을
36개 동시 GPU kernel 실행이나 일정 응답시간 보장으로 표현하지 않는다.
현재 frozen Native GPU 연산의 전역 잠금은 유지돼 해당 구간은 여전히 1개씩 실행된다.

이는 36개 부하 검증 또는 계약상 최대 길이 입력에 대한 peak 증명이 아니다.
허용값이 32에서 36으로 증가하는 정도이며, 더 큰 트래픽 처리량을 입증한 결과가 아니다.
추가 GPU를 사용하려면 device/lock 배치를 포함한 versioned frozen release와 관련
identity·성능/품질 검증을 별도로 수행해야 한다. CUDA_VISIBLE_DEVICES로 identity를
감추거나 전역 GPU lock을 임의 제거하지 않는다.

## 구현

- `capacity_planning.py`에서 가용 RAM·디스크·tmp 및 해당 profile의 GPU inventory로
  상한을 산출한다. 128은 코드가 허용하는 절대 ceiling일 뿐 운영 기본 동시 수가 아니다.
  필요한 inventory가 없으면 확대를 가정하지 않고 capacity=0으로 admission을 닫는다.
- 실제 profile `dlpc-4cpu`는 GPU 0만 계산한다. 기존 승인된 `dlpc-j406` profile은
  GPU 0/1/2 중 가장 작은 예산을 사용하며 CPU/RAM 비용을 더 크게 설정한다.
- ResourceBudget와 resident pool은 시작 시 계산된 상한을 공유한다. 실행 중에도
  RAM·disk·tmp·GPU 여유와 inode를 확인한다. GPU 조회는 2초 cache이며 비밀 없는 환경으로 수행한다.
- short audio의 기존 14GiB disk floor(8GiB reserve+6GiB working)는
  8GiB reserve+256MiB 새 요청 예산으로 대체한다. video/10초 초과 audio는 확대 계산에서
  제외하고 독점 접수·기존 14GiB disk floor와 더 큰 RAM/VRAM headroom을 적용한다.
- CPU 연산 semaphore는 모델 준비와 CORE를 제한한다. executor는 CORE가 끝난 뒤
  private `core-finished.json`에 실행 identity를 남긴다. parent가 소유권·형식·identity를
  확인한 뒤 연산 permit을 반환하므로 GPT I/O 중 다음 요청의 CORE가 진행될 수 있다.
  resident 자체는 원래 요청에 계속 예약돼 socket/파일/증거가 섞이지 않는다.
- 연산 대기 중 heartbeat·lease·deadline·취소를 유지한다. child guard는 취소·예외 시
  반드시 drain하고 회수 미확인은 admission을 닫는다. marker는 외부 API 결과/증거가 아니다.
- `/health/deployment`의 인증된 진단에 `capacityPlan`을 포함해 산정값과 가정을 확인할 수 있다.

기존 schema·모델·frozen bundle·GPU 배치는 이번 변경에서 추가 수정하지 않는다.
PR #49와 Backend PR #164의 coordinated 계약 배포 요구는 여전히 유효하다.

## 확인과 인계

관측 자료의 수치 계산, 소스 AST 구문 및 diff 정적 확인만 수행했다.
모델/서비스 재시작·배포·자동 테스트·부하 테스트·새 음성 분석·GPT 생성을 실행하지 않았다.
단위 예산은 짧은 입력의 계획 가정이므로 실제 peak와 p95 처리량은 개발자가 확인해야 한다.
계획값이 불충분하면 비용 가정을 갱신하고 계산된 상한을 낮춰야 하며 검증 guard를 해제하지 않는다.
