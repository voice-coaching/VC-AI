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
   DLPC 터미널의 최초 실행과 공개 host key 반환을 기다리는 상태다.
2. 관리 접속 후 DLPC의 자원·기존 hook·소유권을 관측하고 전용 비특권 Actions runner를 설치한다.
   추론 계정과 분리하며 일반 root 셸 권한이나 비밀 설정 읽기 권한을 runner에 부여하지 않는다.
   runner 계정·고정 sudo controller 권한의 설치 코드도 자동 승인 검토가 거절하여 별도로 승인을 요청했다.
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
AWS SSH 관리 키·loopback 포트 허용은 적용했다. DLPC hook·runner·sudo 규칙은 아직 설치하지 않았다.
기존 AWS 복구 exporter에 `admin-bootstrap` 조회를 추가하는 제안은 자동 승인 검토가 별도 권한 확장으로 거절했다.
exporter는 변경하지 않았으며, 대신 승인된 설치 코드를 DLPC 터미널에서 직접 실행하도록 전달했다.
실제 배포·자동 회귀 테스트·GPU 추론·GPT 생성·재부팅은 실행하지 않았다.
원격 설치, runner online, 첫 workflow의 receipt 및 DLPC live revision을 확인한 뒤 운영 완료로 기록한다.
2026-10-07 14:47:45 UTC AWS 경유 재확인에서 분석·direct·TTS·Backend handoff는 HTTP 200이며,
live AI revision은 여전히 `df90eb10be7165dd29b77454c368da76733eac6b`다.
이후에 관리 SSH 포트를 열었다는 사실을 새 피드백 배포로 보고하지 않는다.

등록 방식은 [GitHub의 self-hosted runner 설치 안내](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)를 따른다.
조회한 repository용 Linux x64 패키지는 `2.337.0`, SHA-256
`70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613`이다.
