# Direct API 502·CORS 표시와 AWS 터널 복구 — 2026-10-19

## 요청과 조사 범위

사용자가 공개 `/direct/api/v1/analyses`의 OPTIONS 502와 브라우저 CORS 오류를
보고했다. J406 로컬 분석 API, AWS 공개 ingress, 기존 터널 로그·supervisor 및
현재 launcher/CORS 소스를 조사하고 터널 복구·재접속 수정을 반영했다.
작업 브랜치는 `main`에서 생성한 `fix/direct-ingress-tunnel-recovery-20261019`다.
기존 작업 파일은 보존했으며 아래 파일 이외의 프로젝트 동작은 변경하지 않았다.

## 원인

공개 경로는 FE → AWS nginx → AWS loopback 28080 → 역방향 SSH →
J406 loopback 8080이다. 분석 서비스 자체는 준비됐지만 중간 SSH 터널이 끊겼다.

`/tmp/intelligentai-aws-tunnel-120067/supervisor.log`에는 서버 로컬 시각
2026-10-19 17:53:00 KST의 SSH exit 255, 이어진 네 번의 짧은 시작 실패,
17:53:09 KST의 `FATAL / too many start retries too quickly`가 기록됐다.
같은 디렉터리 `ssh.log`에는 `Broken pipe`와
`remote port forwarding failed for listen port 28080` 네 건이 남았다.
로그 조회 범위는 현재 회전본의 마지막 35줄이며 AWS sshd 로그는 조회하지 않았다.

기존 launcher는 SSH를 Supervisor가 직접 실행하며 `startsecs=15`,
`startretries=3`을 사용했다. 재연결 직후 포트 bind가 연속 실패하면 Supervisor가
`FATAL`로 전환해 이후 복구를 시도하지 않는다. 원격의 이전 SSH 연결이 포트를
잠시 보유했을 가능성이 있지만, 최초 연결 단절의 네트워크 원인과 포트 점유
프로세스는 확보한 로그만으로 확정할 수 없다.

공개 nginx의 502 응답에는 CORS 헤더가 없었고 로컬 API에는 정확한 허용 origin이
있었다. 터널 복구만으로 공개 응답과 CORS 헤더가 함께 복구됐다. 따라서 이번
조사에서 확인된 CORS 표시는 도달 불가능한 upstream의 502와 연결된다.
제보한 OPTIONS 요청의 응답 전문·서버 로그·브라우저 세션을 확보하지 않았으므로
개별 요청의 전체 흐름까지 확인한 것은 아니다. `strict-origin-when-cross-origin`은
브라우저 referrer 정책 표시이며 이 터널 장애의 원인 근거가 아니다.

## 실제 복구와 변경

1. 11:17:57 UTC 공개 `/direct/readyz`는 502였고, 동일 origin을 포함한 로컬
   `/direct/readyz`는 200 / `ready=true`였다. 터널은 `FATAL`이었다.
2. 기존 영속 launcher로 터널만 재기동했다. 11:18:35 UTC 공개 direct readiness가
   200 / `ready=true`와 정확한 FE CORS origin을 반환했다.
3. [launcher](../../deploy/dlpc/start_aws_tunnel.py)에 종료한 SSH만 재접속하는
   영속 자식 실행 루프를 추가했다. 지연은 5→10→20→40→60초이며 최대 60초다.
   SSH 프로세스가 90초 이상 유지되면 지연을 5초로 초기화한다. 인증·host key·대상
   포트는 기존 host identity와 동일하며 분석·GPT·사용자 요청을 재실행하지 않는다.
4. `--upgrade-client`는 정확히 일치하는 종전 생성 설정에만 적용한다. 설정은
   원자적으로 교체하고 터널 supervisor에만 `reread`/`update`를 수행한다.
   임의 설정 차이, 비정상 파일 권한·소유자는 거부한다. supervisor socket이
   존재하는데 상태가 불명확하면 중복 manager를 생성하지 않고 실패한다.
5. 이번 작업 전용 direct admission hold를 생성하고 `activeJobs=0`, `directJobs=0`을
   재확인했다. 영속 설치 파일이 Git baseline SHA-256과 같음을 확인하고 수정본을
   원자적으로 설치했다. 터널 설정을 갱신하고 공개 liveness 복귀 뒤 이번 hold만
   제거했다. 분석·TTS 프로세스는 재시작하지 않았다.

영속 설치 위치는
`/home/2020067/.local/share/intelligentai/aws-tunnel/start_aws_tunnel.py`다.
기존 SHA-256은 `f39f241743feac4f4cf820ed109fd6fe2f486b88c32cca3dc9fe2fc8f7e7a5cb`,
수정본은 `dd36dbf89f89fbe4b4febe3858b68274dc64115a41e03dfd85b5c6d54a853d71`이다.
nginx·FE·CORS 정책·frozen core/H5·wire schema는 수정하지 않았다.

## 관측과 확인 한계

| 관측 | 결과 |
|---|---|
| 11:27:20 UTC 공개 `/health` | HTTP 200, `status=alive` |
| 11:27:29 UTC 공개 `/direct/readyz` | HTTP 200, `ready=true`, `reasonCode=null` |
| 같은 응답의 CORS | `Access-Control-Allow-Origin: https://vc-fe.vercel.app`, `Vary: Origin` |
| 11:27:29 UTC 터널 supervisor | `RUNNING`, 새 재접속 manager PID 1871342 |
| 11:29:43 UTC 최종 운영 조회 | 공개 direct 200 / ready=true / 같은 CORS origin, 터널 RUNNING 2분 33초, 접수 hold 없음 |
| 수정 파일 | Python 내장 `compile()` 구문 확인 및 `git diff --check` 통과 |

테스트 파일·fixture·mock을 만들거나 어떤 테스트도 실행하지 않았다. OPTIONS
재현 호출, 브라우저·스크린샷 검증, 실제 분석 제출·재실행, GPT 생성·TTS 합성,
장애 주입과 장기 재접속 동작 검증도 수행하지 않았다. 공개 readiness·헤더 조회는
운영 상태 관측이며 추론·품질·전체 업무 흐름 성공을 뜻하지 않는다. Supervisor
`RUNNING`은 재접속 manager의 생존 상태이며 SSH 연결·API 준비를 보장하지 않는다.

## 별도 발견과 남은 작업

내부 `/health/services`는 `executorReady=true`, `handoffReady=false`,
`reasonCode=handoff_backend_not_ready`로 HTTP 503이었다. 11:27:47 UTC Backend
`/api/internal/ai/worker-readiness/handoff`는 HTTP 200 / `supported=true` /
`admissionEnabled=false`를 반환했다. 후속 digest 대조에서 아홉 개 계약 중
`runpod_result_v5.schema.json` 하나가 현재 운영 AI 파일과 달랐으며 추가·누락된
schema 이름은 없었다. 내부 모니터는 digest map 전체 일치를 요구하므로 이 차이는
내부 handoff readiness가 닫히는 근거다. 공개 익명 direct readiness와는 별도다.

운영 AI identity는 source `791ca5496e04a10b6948c69dce97361ee4335f4c`,
bundle `d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`였다.
Backend 쪽 실제 배포 revision·schema bytes는 이번 checkout에서 확보하지 않았다.
따라서 일치 조건을 완화하거나 어느 한쪽 schema를 추정해서 교체하지 않았다.
내부 Backend 인계를 사용하려면 해당 경로의 양쪽 운영 revision과 결과 계약을
정합화해야 한다. 이는 전체 시스템을 v5로 통일하거나 v6를 v5로 되돌리라는 뜻이
아니다. 현재 checkout은 음성 전용 request v3 → result v5 / handoff v1과,
음성·영상 request v4 → result v6 / handoff v2를 구분한다. v6 readiness도 공통
handoff readiness에 의존하므로 공통 gate의 v5 digest 불일치는 별도로 판단해야 한다.
v6를 운영 목표로 삼을 경우 v6 실행 identity·자산·설정·Backend 지원을 함께 확인해야
하며, 이번 조사에서는 v6 운영 활성화 여부를 확인하지 않았다. 공개 direct 응답의
`voice-coaching.direct-analysis.v1`도 내부 result v5/v6와 별개의 버전이다.

이번 ingress 복구와 예방 수정은 J406 영속 실행기에 실제 반영했다. 저장소 변경은
미커밋·미푸시이며 PR 생성과 전체 애플리케이션 배포는 수행하지 않았다.
플랫폼 재생성·장기 네트워크 단절 후의 복구는 관측하지 않았다.
