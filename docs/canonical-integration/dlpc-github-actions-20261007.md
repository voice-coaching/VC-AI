# DLPC GitHub Actions 배포 — 2026-10-07

## 요청과 현장 확인

사용자는 AWS를 경유한 DLPC SSH 접속과 main 자동 배포 workflow 구축을 요청했다.
2026-10-07 14:24–14:32 UTC AWS에서 읽은 사실은 다음과 같다.

- AWS `13.209.184.226`의 기존 일반 SSH에 접속할 수 있다.
- DLPC 전용 listener의 `PermitListen`과 key option은 `127.0.0.1:18080`,
  `127.0.0.1:18083`만 허용한다. 현재 loopback SSH 관리 포트는 없다.
- AWS에 Actions runner는 없다. 메모리 여유 약 600MiB, root 디스크 여유 1.3GiB이므로
  이 AWS에 runner 설치를 진행하지 않았다.
- GitHub 저장소는 private이며 runner 수는 0이다. DLPC의 최초 연결 명령을 플랫폼
  터미널에서 실행할 수 있다는 사용자 답변을 받았다.

## 배포 경로

1. AWS root 전용 관리 키와 loopback `18022 → DLPC 22` 터널을 준비한다.
   최초 자동 승인 검토 거절 후 사용자가 관리 키·포트·시작 hook 변경을 명시 승인했다.
   AWS 키 생성과 포트 허용, 기존 SSH listener HUP reload를 적용했다.
   [AWS 준비 도구](../../deploy/dlpc/prepare_aws_admin_tunnel.py)와
   [DLPC 최초 설치 도구](../../deploy/dlpc/install_admin_tunnel.py)를 사용한다.
   DLPC 도구는 개인키 없이 AWS 공개키만 받아 localhost 접속만 허용하며 기존 hook에 관리 터널 재연결을 추가한다.
   2026-10-08 KST 사용자가 DLPC 최초 설치 완료와 공개 host key를 반환했다.
   AWS 전용 known_hosts에 전달받은 ed25519 host key를 고정했다.
2. 관리 접속 후 DLPC의 자원·기존 hook·소유권을 관측하고 전용 비특권 Actions runner를 설치한다.
   추론 계정과 분리하며 일반 root 셸 권한이나 비밀 설정 읽기 권한을 runner에 부여하지 않는다.
   runner 계정·고정 sudo controller 권한의 설치 코드도 자동 승인 검토가 거절하여 별도로 승인을 요청했다.
   2026-10-08 KST 사용자의 명시 승인을 받은 뒤 설치·등록했고 GitHub online을 확인했다.
3. [main workflow](../../.github/workflows/deploy-dlpc.yml)는 main push와 main의 수동 실행만 받는다.
   `intelligentai-dlpc-deploy` label을 사용하고, 동시에 두 배포를 실행하거나 실행 중 배포를 취소하지 않는다.
   등록 완료 전 `DLPC_DEPLOYMENT_ENABLED` 변수를 설정하지 않는다. preflight는 이 경우 명시적으로 실패하며
   무기한 self-hosted runner 대기나 배포 성공으로 표시하지 않는다.
4. [client](../../deploy/dlpc/github_deploy.py)가 현재 main과 실행 revision을 대조하고,
   [고정 controller](../../deploy/dlpc/deploy_source.py)가 로컬 배포 lock·소스 digest·준비 상태를 검증한다.

controller는 root 소유의 별도 설치 경로 `/opt/intelligentai/cicd/deploy_source.py`에서 실행하며,
runner에는 이 고정 명령만 sudo로 허용한다. workflow checkout의 controller 파일을 root로 직접 실행하지 않는다.
runner label과 workflow 환경변수는 권한 증빙으로 취급하지 않는다. root controller가 GitHub의 현재 main,
base의 조상 관계, 전체 변경 파일 목록과 각 파일의 Git blob digest를 다시 조회·대조한다.
일시적인 contents-read job token은 이 조회에만 사용하며 release digest와 보관 자료에서 제거한다.
등록 토큰·runner credential·SSH 개인키·분석 설정은 Git/로그/배포 receipt에 넣지 않는다.

## 자동화 범위와 계약 전환

호환되는 application 소스 변경은 direct 접수를 닫고 진행 작업이 없는지 두 번 확인한 뒤
분석 프로세스를 graceful stop한다. 변경 파일·설정·metadata는 private transaction에 보관한다.
소스를 갱신하고 분석을 시작한 뒤 readiness와 revision 및 기존 core/H5/lock을 대조한다.
준비 실패 시 이전 소스·metadata·설정으로 복원한다. 복원마저 실패하면 hold를 유지하고 실패 receipt를 남긴다.
TTS와 frozen core/H5를 교체하거나 미확정 분석 작업을 재실행하지 않는다.
내부 API 호출은 재시작 중 일시적인 unavailable 응답을 받을 수 있다.

`configs/coaching/`, `docs/contracts/`, frozen harness, 의존성 변경은 자동 소스 복사 대상이 아니다.
이 경우 workflow는 `COORDINATED_IMMUTABLE_RELEASE_REQUIRED` 또는
`DEPENDENCY_RELEASE_REQUIRED`로 실패한다. 이를 배포 성공이나 새 프롬프트 반영으로 보고하지 않는다.
특히 PR #36의 4문장 지침은 새 H5 tuple의 AI/Backend 등록·계약 전환을 거쳐야 적용된다.
계약을 우회해 기존 H5 디렉터리를 덮어쓰지 않는다.

이번 controller는 기존 B2 cold-recovery bundle을 갱신하지 않는다. 컨테이너 재생성 후
새 application revision/runner의 자동 복원까지 완료됐다고 해석하지 않는다.
AWS Backend 배포 도구가 사용하는 운영 runtime profile도 별도 조정 대상이다.
이 최초 구현은 해당 profile을 자동 수정하거나 Backend 계약 전환을 승인하지 않는다.

## 현재 구현·검증 범위

workflow·client·controller 소스를 작성했고 Python 소스 컴파일과 diff 정적 확인을 수행했다.
AWS SSH 관리 키·loopback 포트 허용은 적용했다. DLPC 관리 hook과 관리 SSH 접속은
2026-10-07 15:20 UTC 원격 확인했다. 이후 전용 runner·고정 controller·sudo 규칙도 설치했고,
아래 등록 기록에서 실제 권한과 GitHub online을 확인했다. workflow는 아직 main에 병합하지 않았다.
기존 AWS 복구 exporter에 `admin-bootstrap` 조회를 추가하는 제안은 자동 승인 검토가 별도 권한 확장으로 거절했다.
exporter는 변경하지 않았으며, 대신 승인된 설치 코드를 DLPC 터미널에서 직접 실행하도록 전달했다.
실제 배포·자동 회귀 테스트·GPU 추론·GPT 생성·재부팅은 실행하지 않았다.
원격 설치, runner online, 첫 workflow의 receipt 및 DLPC live revision을 확인한 뒤 운영 완료로 기록한다.
2026-10-07 14:47:45 UTC AWS 경유 재확인에서 분석·direct·TTS·Backend handoff는 HTTP 200이며,
live AI revision은 여전히 `df90eb10be7165dd29b77454c368da76733eac6b`다.
이후에 관리 SSH 포트를 열었다는 사실을 새 피드백 배포로 보고하지 않는다.

### 2026-10-07 15:08–15:10 UTC 관리 터널 진단

AWS `18022`는 아직 listen하지 않았고 기존 `18080`·`18083`은 listen 중이었다.
전용 listener 로그에서 `authorized_keys_intelligentai_dlpc: Permission denied`와
DLPC IP의 공개키 인증 실패를 확인했다. 준비 도구가 root 소유 공개키 파일을 `0600`으로
교체하여 로그인 계정 `ec2-user`가 읽지 못한 것이 원인이다. SSH listener는 active였다.

AWS의 해당 공개키 전용 파일을 root 소유 `0644`로 수정했고 `ec2-user` 읽기 가능과
`sshd -t` 성공을 확인했다. 개인키와 listener 설정 파일은 `0600`을 유지한다.
준비 도구에도 같은 수정과 root 소유권 보장을 반영했다. 기존 API/TTS SSH 세션은 재시작하지 않았다.
사용자가 전달한 DLPC host key는 AWS root 전용 known_hosts에 고정했다.

수정 직후에도 `18022`가 없어 DLPC 관리 supervisor 재시작 명령을 사용자에게 전달했다.
인증 실패로 재시도가 소진됐다는 판단은 AWS 로그와 설정의 `startretries=10`에 근거한 추정이며,
DLPC supervisor 상태는 SSH 연결 후 확인해야 한다. 아직 관리 SSH 성공이나 자동 배포 완료로 보고하지 않는다.

2026-10-07 15:10:38 UTC AWS 경유 상태 조회에서는 분석 liveness/readiness, direct readiness,
TTS readiness, Backend handoff가 모두 HTTP 200이고 인증 없는 보호 API 요청은 401이었다.
분석 activeJobs는 0, live revision은 여전히 `df90eb10be7165dd29b77454c368da76733eac6b`다.
이는 운영 상태 조회이며 실제 분석·GPT 생성·품질 QA를 수행한 결과가 아니다.

### 2026-10-07 15:20 UTC / 2026-10-08 00:20 KST 관리 SSH 확인

사용자가 DLPC `start.py`를 다시 실행한 뒤 AWS `127.0.0.1:18022` listen을 확인했다.
사용자가 반환한 host key를 고정한 `StrictHostKeyChecking=yes` 접속이 성공했고,
대상 hostname은 `J3990.2020067.notebook.dlpc`이며 기존 sudo 사용도 가능했다.

- 관리 supervisor: `admin-tunnel RUNNING`, Jupyter 관리 hook marker 1개.
- 시작 도구: uid `120067`, mode `0600`, SHA-256
  `97c4552e123f28e4ac8f8d571d924e2b1e7eaa0449f7817e3043a43c91f04bf3`.
- 기존 analysis·tts supervisor: 모두 RUNNING. 프로세스 재시작은 하지 않았다.
- CPU affinity: `[28, 34, 36, 38]`; RAM 총 11GiB, available 약 4.65GiB, swap 없음.
- root 및 `/opt/intelligentai`, `/var/lib`: 같은 50GiB filesystem, 여유 약 22.79GiB.
  home NAS의 filesystem 여유는 사용자 quota를 의미하지 않는다.
- GPU: RTX A5000, 총 24564MiB, 사용 4088MiB, 관측 시 utilization 0%.
- `intelligentai-actions` 계정과 `/opt/intelligentai/cicd/deploy_source.py`는 아직 없다.

15:20:37 UTC API 재확인에서는 분석·direct·TTS readiness 및 Backend handoff가 200,
인증 없는 보호 API는 401, activeJobs는 0이었다. live revision은 여전히
`df90eb10be7165dd29b77454c368da76733eac6b`다. 관리 연결 성공은 최신 main 재배포를 의미하지 않는다.
runner·고정 controller sudo 권한의 명시 승인 요청은 아직 대기 중이며 배포 활성화 flag는 설정하지 않았다.
새 분석·GPT 생성·자동 회귀 테스트·재부팅은 수행하지 않았다.

### 2026-10-08 KST 승인 후 runner 등록

사용자는 `전용 runner·고정 controller sudo 권한 승인`으로 승인했다.
[설치 도구](../../deploy/dlpc/install_actions_runner.py)는 다음 두 단계를 분리한다.

1. root 설치 요청 `operation=prepare`: controller 본문과 SHA-256을 stdin으로 받아
   고정 경로에 설치하고, 공식 runner archive의 고정 SHA-256을 검증한 후 추출한다.
   설치 전 디스크 17GiB·available RAM 1GiB 이상의 여유를 요구한다.
2. `operation=register`: 한 시간 유효한 GitHub 등록 토큰을 SSH stdin으로 전달한다.
   개인 SSH 키·분석 설정·일반 GitHub PAT를 runner에 복사하지 않는다.
   토큰 요청 응답과 설치 subprocess 출력은 메모리에서 처리하고 비밀값을 출력하지 않는다.

최초 runner 홈 `/var/lib/intelligentai/actions-runner`에서는 .NET runner가 부모 디렉터리의
읽기 권한을 요구하여 등록이 실패했다. 분석 state 부모의 `0711` 보호 권한을 유지하고
runner 전용 홈을 `/var/lib/intelligentai-actions`로 옮겨 실행 버전 `2.337.0`과 등록 성공을 확인했다.
설치 도구도 이 전용 홈을 사용한다. 별도 시스템 라이브러리 설치는 필요하지 않았다.

- 계정: `intelligentai-actions`, uid `999`, 전용 gid `995`, 로그인 셸 `/usr/sbin/nologin`.
- 홈: `/var/lib/intelligentai-actions`, uid `999`, `0700`; runner credential `0600`.
- controller: `/opt/intelligentai/cicd/deploy_source.py`, root 소유 `0600`, SHA-256
  `8ba96c45605b237478c91240a85bcdbaad36011241e5817ff48d9381833a8d68`.
- sudoers: `/etc/sudoers.d/intelligentai-actions`, root 소유 `0440`, `visudo` 확인 성공.
  허용 명령은 `/usr/bin/python3 -I /opt/intelligentai/cicd/deploy_source.py` 하나다.
- runner 계정의 controller `operation=status` 호출은 성공했고 일반 `sudo true`는 거절됐다.
  `/etc/intelligentai/analysis.env`와 `/root/.ssh`는 읽을 수 없었다.
- 독립 supervisor: `actions-runner RUNNING`; root socket
  `/run/intelligentai-actions-supervisor.sock`, private bounded logs, 기존 분석·TTS와 별도 관리.
- Jupyter runner 시작 marker 1개를 추가했다. root 설치가 남아 있는 재시작에서는
  고정 `start_runner.py`를 통해 runner supervisor를 재기동한다. 재부팅 시험은 하지 않았다.
- GitHub repository runner id `21`, 이름 `intelligentai-dlpc`, label `intelligentai-dlpc-deploy`,
  `online`, `busy=false`를 확인했다.

현재 GitHub main은 `7a825885dc870ab83138ba2583eb139c248c7c3e`, 운영 base는
`df90eb10be7165dd29b77454c368da76733eac6b`다. 두 revision 사이에는
`configs/coaching/announcer_feedback_system_20261007.txt`와 요청·결과 contract schema 변경이 있다.
따라서 현 상태에서 호환 소스 자동 배포만 활성화하면 계약 전환 요구로 차단되는 것이 정상이다.
`DLPC_DEPLOYMENT_ENABLED`는 미설정이고 PR #37은 Draft다. 실제 소스 배포는 수행하지 않았다.

활성화 전 남은 순서는 새 frozen prompt/core/H5 identity의 개발자 QA 및 AI·Backend 등록,
기존 coordinated activation을 통한 drain·계약 전환, 운영 source/Backend runtime profile과
B2 cold-recovery bundle 정합성 확인, workflow main 병합과 활성화 변수 설정이다.
컨테이너 재생성으로 `/var/lib`·`/opt`가 사라지면 runner account/credential/controller도 사라진다.
이때 현재 Jupyter hook은 runner를 새로 등록하거나 credential을 복구하지 않는다.
새 runner의 완전한 cold recreation 복구까지 완료한 것으로 보고하지 않는다.

등록 방식은 [GitHub의 self-hosted runner 설치 안내](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)를 따른다.
조회한 repository용 Linux x64 패키지는 `2.337.0`, SHA-256
`70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613`이다.
