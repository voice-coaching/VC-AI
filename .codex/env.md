# 환경변수와 비밀 설정 안내

[AGENTS.md](../AGENTS.md)의 작업 범위 안에서 필요한 프로세스와 설정을 먼저 정한다. 변수 이름·존재·권한만 확인하고 값은 출력하지 않는다. 파일 생성과 실제 주입, 연결 확인, 서비스 준비는 별도 상태로 보고한다.

## 역할별 설정

| 작업 | 필요한 설정과 읽는 곳 |
|---|---|
| PC의 B2 Native 인증·버킷 조회 | 아래 예제의 필수 6개 B2 변수. 비밀파일은 프로젝트 루트 `.env` |
| 외부 수집·배치 | `B2_*_PREFIX`, `RUNPOD_EXTERNAL_CACHE_*`는 후속 수집기 연결용 제안. 인증 자체의 필수값은 아님 |
| RunPod 관리 API | 별도 관리 키를 현재 요청 프로세스에 주입. SSH/분석 서버/B2 키와 구분 |
| SSH | 해당 PC의 private key와 Pod에 등록한 public key. 경로와 현재 주소를 [접속 안내](runpod.md)로 확인 |
| 과거 Redis worker | [worker.env.example](../deploy/runpod/worker.env.example)과 [과거 배포 복원](../deploy/runpod/README.legacy-clova.md#기존-redis-worker-비밀-설정과-재부팅-복원). 현재 HTTP 운영 설정으로 적용하지 않음 |
| 사용자 녹음 저장소 | intelligentAI의 `AI_ANALYSIS_OBJECT_STORAGE_*`/AWS credential chain, VC-BE의 `OBJECT_STORAGE_*`. 같은 canonical 객체의 역할별 접근 확인 |
| HTTP 서버·callback | [현재 배포 안내](../deploy/runpod/README.md)와 실제 배포 parser를 대조. 별도 양방향 서버 토큰과 저장소 설정 |
| GPT 코칭 | `OPENAI_API_KEY`와 `AI_ANALYSIS_COACHING_*`. 실제 provider/model과 필수값은 [배포 파이프라인](../docs/runpod_deployed_pipeline_20260928.md) 참조. RunPod 관리 키와 구분 |

B2 Master Native Key는 S3/AWS credential로 사용하지 않는다. 자세한 모드 구분은 [B2 안내](backblaze.md)에 있다. [S3 adapter](../src/voice_coach/backend_analysis/adapters/s3_object_store.py)와 서비스가 실제 읽는 설정을 대조하고 키 파일 전체를 다른 역할에 공유하지 않는다.

아래 B2 전용 loader는 허용한 이름만 읽는다. HTTP 변수를 이 예제에 미리 섞지 않는다. 기존 worker env allowlist와 `RUNPOD_ANALYSIS_WORKER_ENV_JSON`이 새 HTTP 변수를 지원한다고 가정하지 않는다.

## 파일과 수명

| 위치 | 적용 범위 |
|---|---|
| PC 프로젝트 `.env` | 로컬 보관 사본. 기존 값을 template으로 덮어쓰지 않음 |
| [.env.example](../.env.example) | 공유용 값 없는 template |
| Pod `/root/.config/intelligentai/.env` | B2 수집용 사본 위치. 부모 0700·파일 0600, 현재 존재는 별도 확인 |
| Pod `/root/.config/intelligentai/analysis-worker.env` | 기존 worker의 비공개 source. 복원 공급원은 배포 안내 참조 |
| Pod `/run/intelligentai/analysis-worker.env` | 기존 준비 스크립트가 만드는 실행용 파일 |
| Pod `/root/.config/intelligentai/analysis-http.env` | 현재 HTTP/GPT의 root 전용 source. 비공개 JSON 주입이 있으면 준비 코드의 우선순위 확인 |
| Pod `/run/intelligentai/analysis-http.env` | 현재 HTTP 준비 단계가 검증 후 생성하는 실행용 파일. Clova 설정은 제거됨 |

PC 사본의 현재 ACL을 확인한다. 공유 `/workspace`의 권한 제한 이력은 [배치 기록](../docs/backblaze_external_data_plan_20260909.md)에 있으며 비밀파일은 공유 볼륨·overlay·일반 복구 archive에 넣지 않는다. `/root`·`/run`은 컨테이너 재생성 시 사라질 수 있다.

.env는 해당 프로세스가 명시적으로 읽어야 한다. 현재 B2 파일을 [boot](../deploy/runpod/boot.sh)와 [prepare_runtime](../deploy/runpod/prepare_runtime.py)이 자동으로 읽는 구성으로 설명하지 않는다.

## PC PowerShell의 B2 로딩 예제

프로젝트 루트의 전용 PowerShell 프로세스에서 실행한다. [B2 인증·조회](backblaze.md)는 **이 로딩과 같은 프로세스**에서 이어서 수행한다. 별도 `exec_command` 호출은 같은 셸 변수·환경을 유지한다고 가정하지 않는다. 도구로 실행할 때는 로딩과 필요한 B2 호출을 한 호출에 묶거나 명시적으로 유지한 셸을 사용한다.

이 예제는 단순 `KEY=VALUE`용이다. 따옴표·치환·여러 줄 값의 범용 dotenv parser가 아니다. 필수값 6개를 먼저 검사하고 허용된 나머지 설정은 있을 때만 프로세스에 전달한다. 실제 수집기나 캐시 제한기를 구현하지 않는다.

```powershell
$ErrorActionPreference = 'Stop'
$b2EnvPath = Join-Path (Get-Location).Path '.env'
$b2EnvNames = @(
    'B2_APPLICATION_KEY_ID', 'B2_APPLICATION_KEY', 'B2_API_MODE',
    'B2_AUTH_URL', 'B2_BUCKET_NAME', 'B2_BUCKET_ID',
    'B2_EXTERNAL_PREFIX', 'B2_DERIVED_PREFIX', 'B2_COACHING_RELEASE_PREFIX',
    'RUNPOD_EXTERNAL_CACHE_DIR', 'RUNPOD_EXTERNAL_CACHE_MAX_BYTES'
)
$b2Settings = @{}
$b2LineNumber = 0
foreach ($b2Line in Get-Content -LiteralPath $b2EnvPath -Encoding UTF8) {
    $b2LineNumber++
    if ([string]::IsNullOrWhiteSpace($b2Line) -or $b2Line.TrimStart().StartsWith('#')) {
        continue
    }
    if ($b2Line -cnotmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
        throw "Invalid env format at line $b2LineNumber"
    }
    $b2Name = $Matches[1]
    $b2Value = $Matches[2]
    if ($b2Name -notin $b2EnvNames -or $b2Settings.ContainsKey($b2Name)) {
        throw "Unexpected or duplicate env name at line $b2LineNumber"
    }
    if ($b2Value -ne $b2Value.Trim() -or
        $b2Value.StartsWith('"') -or $b2Value.StartsWith("'")) {
        throw "Use unquoted literal values at line $b2LineNumber"
    }
    $b2Settings[$b2Name] = $b2Value
}
$b2RequiredNames = @(
    'B2_APPLICATION_KEY_ID', 'B2_APPLICATION_KEY', 'B2_API_MODE',
    'B2_AUTH_URL', 'B2_BUCKET_NAME', 'B2_BUCKET_ID'
)
foreach ($b2Name in $b2RequiredNames) {
    if ([string]::IsNullOrWhiteSpace($b2Settings[$b2Name])) {
        throw "Missing value: $b2Name"
    }
}
if ($b2Settings['B2_API_MODE'] -ne 'native' -or
    $b2Settings['B2_AUTH_URL'] -ne 'https://api.backblazeb2.com/b2api/v4/b2_authorize_account') {
    throw 'Unexpected B2 API mode or auth URL'
}
foreach ($b2Name in $b2Settings.Keys) {
    [Environment]::SetEnvironmentVariable($b2Name, $b2Settings[$b2Name], 'Process')
}
$b2Settings.Clear()
Remove-Variable b2Value,b2Line -ErrorAction SilentlyContinue
'B2 environment loaded; values are not displayed.'
```

작업 종료 시 전용 프로세스를 닫는다. 이미 실행한 자식 프로세스의 환경은 그 프로세스가 종료할 때까지 남는다. `Invoke-Expression`, env 전체 출력, 인증 응답 전체 저장으로 값을 노출하지 않는다. 파일을 수정해도 실행 중인 서비스에 자동 반영되지 않는다.

Pod에서 수집 프로세스를 만드는 작업은 필요한 이름만 명시적 parser로 읽고 그 프로세스에 전달한다. 외부에서 받은 env를 셸 코드로 source하지 않는다. 기존 worker는 이미 있는 전용 준비 스크립트를 사용한다.

## B2 사본 전달이 필요한 경우

현재 Pod와 SSH 값이 검증된 PC PowerShell에서만 적용한다. 아래는 **원격 파일을 새로 만드는** 수동 예제이며, 기존 파일이 있으면 덮어쓰지 않고 종료한다. 기존 설정 교체가 요청된 작업은 의도한 변경과 복구 방법에 맞춰 별도로 적용하며 같은 승인을 다시 묻지 않는다.

```powershell
$ErrorActionPreference = 'Stop'
if (-not $RunPodId -or -not $RunPodHost -or
    $RunPodPort -lt 1 -or $RunPodPort -gt 65535 -or
    -not $RunPodKey -or -not (Test-Path -LiteralPath $RunPodKey -PathType Leaf)) {
    throw 'Set current verified Pod connection variables in this process first'
}
$b2EnvPath = Join-Path (Get-Location).Path '.env'
if (-not (Test-Path -LiteralPath $b2EnvPath -PathType Leaf)) { throw 'B2 env file is missing' }
ssh -o BatchMode=yes -o ConnectTimeout=15 -i $RunPodKey -p $RunPodPort "root@$RunPodHost" 'umask 077; mkdir -p /root/.config/intelligentai && chmod 700 /root/.config/intelligentai && test ! -e /root/.config/intelligentai/.env && (set -C; : > /root/.config/intelligentai/.env)'
if ($LASTEXITCODE -ne 0) { throw 'Remote file exists or preparation failed' }
scp -o BatchMode=yes -o ConnectTimeout=15 -i $RunPodKey -P $RunPodPort $b2EnvPath "root@${RunPodHost}:/root/.config/intelligentai/.env"
if ($LASTEXITCODE -ne 0) { throw 'Transfer failed; inspect the incomplete destination before retrying' }
ssh -o BatchMode=yes -o ConnectTimeout=15 -i $RunPodKey -p $RunPodPort "root@$RunPodHost" 'chmod 600 /root/.config/intelligentai/.env && stat -c %a:%U:%n /root/.config/intelligentai /root/.config/intelligentai/.env'
if ($LASTEXITCODE -ne 0) { throw 'Remote permission check failed' }
```

결과는 부모 700·파일 600과 root 소유를 확인한다. 중간 실패 시 부분 파일을 완성된 설정으로 사용하지 않는다. 사본을 전달한 것만으로 B2 연결 또는 worker 설정 복원이 끝난 것은 아니다.

## 결과와 장애 확인

- 값 누락: 해당 프로세스가 파일을 읽었는지, 다른 도구 호출에서 만든 변수에 의존했는지 확인한다.
- 인증 오류: 키의 용도·대상·권한과 URL을 확인한다. TLS 검증을 끄지 않는다. Windows Python의 CA 오류는 CA 환경 수정 또는 정상 TLS를 사용하는 PowerShell로 확인한다.
- 재생성 후 파일 없음: 역할별 지속 공급원이나 로컬 사본에서 다시 주입한다. Git clone이 비밀파일을 복구하지 않는다.
- HTTP 값 추가 후 worker 대기: 실제 parser·transport·기동 의존성의 변경 여부를 확인한다. 과거 grant를 가짜로 생성하여 해결하지 않는다.

적용 프로세스, 변수 이름·파일 권한, 필요한 재시작과 실제 연결 확인 결과만 인계한다.
