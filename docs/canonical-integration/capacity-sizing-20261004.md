# RunPod 사양 기반 canonical 동시 실행 3건 선택 — 2026-10-04

작업 유형: 운영 자원 평가·서비스 실행기 설정 변경. 사용자의 최신 요청에 따라 Pod 사양을 확인해 슬롯 수를 선택했다. ?? ?? ?? ?? `GLOBAL_RULES.md`, 아키텍처·서비스·외부 연동·API 규칙, `.codex/backend-http.md`, `.codex/runpod.md`를 적용했다.

## 코드 동작

- `HttpRuntime`은 실행별 `(requestId, executionId)`로 활성 작업을 보관한다. 기존 요청의 동일 payload 재전송은 슬롯을 다시 차지하지 않는다. claim 중인 요청도 슬롯에 포함하며, 한 이벤트 루프에서 검사와 예약 사이에 `await`가 없다.
- 기본 OFF인 resident 모드의 v5 canonical 요청은 최대 **3개**를 접수한다. 각 요청은 독립 workspace, lease/heartbeat, guard와 core/GPT/handoff 프로세스 경로를 가진다. 4번째 요청은 기존 계약의 `429 CAPACITY_EXCEEDED`를 받는다. `activeJobs`는 현재 점유한 작업의 실제 개수를 반환한다.
- legacy 실행 또는 opt-in 단일 resident core 모드는 기존 단일 실행으로 유지한다. legacy와 canonical은 동시에 접수하지 않는다. 여러 canonical 요청의 첫 준비에서만 공용 legacy 실행기를 정리한다. 종료 시 모든 활성 작업에 취소를 전파하고 각 guard의 회수를 기다린다. 어느 guard라도 정리 불확정이면 새 접수를 중단하고 나머지 작업도 취소한다.
- HTTP 경로, 요청·응답 schema, Backend lease와 결과 계약, FE 공개 API는 변경하지 않는다. 서비스 프로세스는 `scripts/serve_backend_analysis_http.py`의 단일 Uvicorn worker로 실행한다. 여러 HTTP worker를 켜면 프로세스마다 독립 3개가 생기므로 별도 용량 조정이 필요하다.
- 현재 Pod의 frozen `canonical_scored_core_20261003_v2/scripts/run_release.py`는 요청별 output과 무작위 이름의 MFA Unix socket을 생성한다. 이 소스 확인은 고정 socket 충돌이 없다는 근거이며, 3개 동시 추론 성공의 근거는 아니다.

## 운영 관측과 활성화 조건

2026-10-04 16:36 KST RunPod 관리 API에서 Pod `s8hb5b0l5k4qlx`는 RUNNING, 공개 IP `194.68.245.79`, SSH port `22132`, GPU 1개, vCPU 9개, RAM 50GB, 시간당 $0.49였다. SSH의 `nvidia-smi`에서 GPU는 A40, VRAM 46,068 MiB, 관측 시 사용량 1,203 MiB, 사용률 0%였다. 컨테이너 cgroup RAM 한도는 49,999,998,976 bytes, 현재 9,302,040,576 bytes, 누적 최고 18,654,371,840 bytes이고 CPU quota는 765000/100000(7.65 core)이다. 메모리 OOM·할당 실패 누계는 0이다. 실행 설정의 resident 모드는 OFF로 확인했다.

## 슬롯 수 판단

- 현재 RAM 9.30GB와 누적 최고 18.65GB의 차이 약 9.35GB를 작업당 추가 사용량으로 **가정**해 용량을 가늠하면, 3개는 약 37.36GB로 12.64GB 여유가 남고 4개는 약 46.71GB로 3.29GB만 남는다. 이 차이는 개별 분석의 실제 증분이 아니다. cgroup page cache, TTS 등 다른 프로세스와 측정 시점이 섞여 있으므로 부하 검증을 대신하지 않는다.
- frozen core의 프로세스 환경은 `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`를 각각 2로 제한한다. 3개 작업이 각각 2개 스레드를 적극 사용하면 6개 스레드로 7.65코어 quota 안에 기본 서비스와 후처리를 위한 여유가 있다. 4개는 8개 스레드만으로 quota를 넘는다. MFA·기타 subprocess의 실제 CPU 경쟁은 별도 관측이 필요하다.
- 따라서 현 Pod의 초기 상한은 **3개**로 선택한다. 10개는 위 RAM 추산과 CPU quota 모두에 맞지 않는다. GPU 메모리의 작업 중 최고 사용량은 확보하지 못했으므로 3개도 실제 동시 추론·deadline 성공을 보장하지 않는다.

2026-10-04 운영 재측정의 분석 71은 코어와 GPT를 마친 뒤 Backend handoff에서 약 11분 34초 동안 실패했고 서버 결과는 `ANALYSIS_EXECUTION_TIMEOUT`이었다. 3개 접수 슬롯만으로 이 실패가 해결되지는 않는다. Backend handoff 수신의 반복 DB 왕복·10초 트랜잭션 timeout을 먼저 고치고, DB/HTTP timeout 관계를 재측정해야 실제 완료 용량을 평가할 수 있다. 3개 동시 실행에서 RAM·CPU·GPU·MFA·DB pool·각 작업 deadline과 성공률을 개발자가 검증해야 한다.

이 문서는 로컬 코드 변경 기록이다. Pod 코드 배포, Backend 수정·배포, 3개 동시 실제 분석, 품질 QA는 아직 수행하지 않았다. [수정 배포 순서](../../deploy/runpod/README.md#수정-배포-순서)의 별도 배포 승인과 drain·source revision/hash 대조가 필요하다. 동시 완료 용량이 3개에 못 미치면 실행 자원을 재산정하고 상한을 낮춰야 한다.
