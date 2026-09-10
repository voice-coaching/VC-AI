# RunPod 운영 안내

현재 요청의 범위는 [AGENTS.md](../AGENTS.md), 기동·복원 방법은 [배포 안내](../deploy/runpod/README.md)를 따른다. 아래 예제는 운영 요청에서만 실행한다.

## 대상과 동작 결정

1. 필요하면 로컬 `.codex/local/state.json`과 `history.md`에서 과거 대상과 근거를 읽는다. 기록이 없으면 미확인으로 두며, 접속·조작 전에 API/Connect로 현재 Pod ID·상태·IP·포트·GPU·요금을 확인한다. 조회 실패를 이전 성공으로 덮어쓰지 않는다.
2. 종료 요청은 중지와 삭제를 구분한다. 재시작 요청은 기존 Pod부터 시도하고, GPU 부족과 boot/모델 오류를 구분한다. GPU 확보·이전은 서비스 boot의 기능이 아니다.
3. 이전이 필요하면 source/target, 저장소, 시작 명령, 비용, 진행 중인 migration을 확인한다. 변경 응답을 잃었을 때는 기존 작업 상태를 먼저 조회하고 중복 생성하지 않는다.
4. 이번 migration의 terminal 완료와 새 환경·overlay/assets 무결성을 확인한다. 옛 완료 marker만으로 판단하지 않는다. source lock의 소유와 해제도 확인하고 원본 보존·삭제는 요청 범위를 따른다.
5. 실행 중인 환경을 복구할 때는 boot·manager·supervisor 부모 관계와 자동 재시작 경로를 먼저 정상 종료한다. [수정 배포 순서](../deploy/runpod/README.md#수정-배포-순서)를 적용하고 실제 파일 hash·revision을 대조한다.

Pod 관리 API 형식은 작업 시 공식 API와 현재 설정으로 확인한다. 키는 프로세스에서 읽고 응답에서는 대상·상태 등 필요한 필드만 꺼낸다. 이 저장소에는 일반 Pod 관리 CLI와 불확실한 변경 결과를 자동 복구하는 도구가 아직 없다.

## PC PowerShell에서 SSH 연결

현재 API/Connect로 확인한 값과 해당 PC의 SSH identity 경로를 같은 PowerShell 프로세스에 입력한다. 빈칸은 과거 주소로 자동 대체되지 않는다. 이 예제 자체가 API 대상 대조를 수행하는 것은 아니다.

```powershell
$RunPodId = ''
$RunPodHost = ''
$RunPodPort = 0
$RunPodKey = ''
if (-not $RunPodId -or -not $RunPodHost -or
    $RunPodPort -lt 1 -or $RunPodPort -gt 65535 -or
    -not $RunPodKey -or -not (Test-Path -LiteralPath $RunPodKey -PathType Leaf)) {
    throw 'Set the current Pod ID, SSH host/port and existing private-key path first'
}
ssh -o BatchMode=yes -o ConnectTimeout=15 -i $RunPodKey -p $RunPodPort "root@$RunPodHost"
if ($LASTEXITCODE -ne 0) { throw 'SSH connection failed' }
```

새 공개키를 등록할 때는 기존 키를 보존한다. RunPod 관리 키와 B2 키는 SSH 비밀번호가 아니다. 접속 실패 시 대상 상태·현재 연결 주소·공개키를 확인한다. 관리 키의 주입 방식은 [env 안내](env.md)를 따른다.

## Health 조회

SSH 연결 값이 있는 PC PowerShell 프로세스에서 다음 터널을 유지한다. 이 명령은 foreground이며 종료하면 터널도 끝난다. 별도 도구 호출을 같은 셸로 가정하지 말고, 유지 가능한 셸/프로세스에서 터널을 실행한다.

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=15 -i $RunPodKey -p $RunPodPort -N `
  -o ExitOnForwardFailure=yes `
  -L 127.0.0.1:18000:127.0.0.1:8000 "root@$RunPodHost"
```

터널이 열린 동안 다른 PC PowerShell 프로세스에서 조회한다. 다음 요청은 생성을 수행하지 않는다. 실패하면 HTTP 상태와 응답 본문의 서비스별 state/reason을 확인한다. 특히 503은 PowerShell에서 오류 응답으로 표시된다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:18000/health' -TimeoutSec 15
Invoke-RestMethod -Uri 'http://127.0.0.1:18000/health/services' -TimeoutSec 15
```

조회 후 터널은 실행한 셸에서 Ctrl+C로 종료한다. API의 배치·필드 의미와 생성 제한값은 [배포 안내](../deploy/runpod/README.md), [시작 코드](../deploy/runpod/start_hyperclova.py), [서버 코드](../scripts/serve_hyperclovax_grounded_advice.py)에서 대조한다. 과거 PID·응답을 현재 준비 상태로 쓰지 않는다.

## 진단과 복구 진입점

| 증상/작업 | 확인할 근거 |
|---|---|
| 호스트 RAM·CPU가 과도하게 표시됨 | Pod Linux의 cgroup `memory.max/current/events`, `cpu.max`. 호스트 전체 값과 Pod 한도를 구분 |
| 전체 서비스 503 | `/run/intelligentai/services.json`의 서비스 state/reason. worker가 설정 대기면 로그가 아직 없을 수 있음 |
| 모델 시작/생성 오류 | 배포 revision·서버 로그·`last_completion`. 상태 코드별 제한은 서버 소스와 대조 |
| 기존 Redis worker 설정 복구 | [기존 설정·재부팅 복원](../deploy/runpod/README.md#기존-redis-worker-비밀-설정과-재부팅-복원). HTTP 신규 입력의 준비 목록으로 사용하지 않음 |
| 원본·overlay·실행 사본 복구 | [파일 위치와 보존 범위](../deploy/runpod/README.md#파일-위치와-보존-범위), manifest |
| 새 HTTP 전송 | [HTTP 작업 안내](backend-http.md). 환경변수 추가만으로 서버나 전송 방식이 구현되지 않음 |

대상 Pod의 Linux 셸에서 필요한 파일만 읽는다. 로그와 프로세스 명령줄을 공유하기 전 비밀값·사용자 원문을 제거한다. 코드 revision, overlay/hash, Clova health와 전체 readiness를 각각 기록한다. 중지 후에는 API 상태와 runtime 종료 근거를 확인한다.

결과는 로컬 관측에 대상·원본 관측 시각·source·확인 범위를 갱신하고 과거 이력은 보존한다. 복구 객체는 [B2 안내](backblaze.md)로 file ID·크기·hash를 확인한다. overlay 백업을 모델·환경·비밀 설정의 완전한 복구본으로 가정하지 않는다.

과거 사례는 [GPU·RAM 조사](../docs/runpod_gpu_capacity_assessment.md), [이전 실행 기록](../docs/runpod_b2_storage_migration_execution.md), [영상 실행 기록](../docs/runpod_video_pipeline_validation_20260909.md), [상세 피드백 변경](../docs/clova_detailed_feedback_20260909.md)을 참고한다. 사례의 시각·정책 revision을 보존한다.
