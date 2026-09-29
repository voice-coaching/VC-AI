# main 자동 배포

[워크플로](../../.github/workflows/deploy-runpod.yml)는 `main` push 또는 `main`에서 수동 실행할 때 기존 RunPod에 커밋된 소스 변경을 배포한다. 새 Pod 생성·시작·중지·삭제·reset, 모델 추론, 브라우저·회귀 QA는 실행하지 않는다. 동시 실행은 하나로 제한하며 진행 중 배포를 취소하지 않는다. 대기 중 push가 여러 개면 GitHub concurrency가 가장 최근 대기 실행으로 합칠 수 있으며 그 커밋까지의 모든 소스 변경을 배포한다.

## 최초 연결

GitHub 저장소 설정의 Actions secrets/variables에 다음을 등록한다. API 키만으로 Linux SSH 인증을 대신할 수 없다.

| 종류 | 이름 | 값 |
|---|---|---|
| Secret | RUNPOD_API_KEY | 기존 RunPod의 조회 권한을 가진 API 키 |
| Secret | RUNPOD_SSH_PRIVATE_KEY | 이 배포 전용 Ed25519 개인키; 개인 PC의 공용 사용 키를 업로드하지 않음 |
| Secret | RUNPOD_SSH_HOST_KEY | 신뢰된 기존 접속으로 확인한 Pod 호스트 공개키의 `ssh-ed25519 BASE64` 두 필드 |
| Variable | RUNPOD_POD_ID | 현재 대상 Pod ID |

GitHub 인증에는 저장소 Contents 및 Workflows 쓰기, Actions Secrets/Variables 관리 권한이 필요하다. 비밀값은 저장소 파일·로그·일반 배포 archive에 넣지 않는다. 최초 등록 시 개인키는 안전한 임시 파일에서 `gh secret set ... < file`로 전달하고 제거한다. 기존 Pod의 authorized_keys는 보존하고 배포 전용 공개키 한 줄만 추가한다. Pod 재생성 이후에도 공개키를 유지할 RunPod 공개키 설정은 별도로 등록한다. 호스트 키는 API에서 얻은 IP만 믿고 매번 ssh-keyscan으로 교체하지 않는다.

최초 원격 등록 전에 실제 운영 코드와 Git 기준 커밋의 차이, 프로세스 부모 관계, OpenAI 전용 운영 설정을 대조한다. Git 기준은 운영 파일들이 파생된 실제 커밋이어야 한다. 최신 main으로 임의 지정하면 그 사이 변경이 배포되지 않는다. 운영 전용 수정은 먼저 Git으로 이관하거나 명시적으로 유지할 차이로 확인한다. 같은 파일의 변경이 겹치면 배포 전 source drift 검사에서 멈추므로 Git과 운영 코드를 조정한 뒤 다시 시도한다.

현재 구현은 HTTP 모드와 `boot.sh → run_services.sh → supervise_services.py` 구조를 요구한다. 독립 실행된 API나 다른 프로세스 관리자는 추측하여 종료하지 않는다. 이 경우 실제 관리 구조를 먼저 정리하거나 이 스크립트의 수명 관리 코드를 해당 구조에 맞게 검토해야 한다.

검토한 원격 helper를 Pod에 전달한 뒤 root 셸에서 실행한다. 꺾쇠 표시는 실제 값으로 바꾼다.

```bash
python3 /path/to/github_deploy_remote.py --enroll <40자리-Git-기준-SHA> <현재-Pod-ID>
```

등록은 `/workspace/intelligentai/deployment/github-actions/state.json`에 Pod ID, Git 기준 SHA, runtime revision, overlay hash를 기록한다. 서비스가 ready일 때만 등록하고 이미 등록됐거나 미완료 작업이 있으면 거절한다. 비밀값은 기록하지 않는다. 기존 등록을 지워 무조건 재등록하면 배포 이력이 깨지므로 수행하지 않는다.

## 매 실행의 처리

1. [runner 스크립트](github_deploy.py)가 공식 RunPod REST API로 지정 Pod의 RUNNING 상태·현재 public IP·22/tcp 매핑을 확인한다. 고정 IP를 재사용하지 않는다. SSH 호스트 키는 고정 공개키와 대조한다.
2. 이전 배포 Git 커밋이 새 커밋의 조상인지 확인한다. 작업 트리 대신 Git blob에서 소스 delta를 구성한다. 따라서 저장하지 않거나 commit하지 않은 파일은 배포하지 않는다.
3. 대상은 `src/`, `seungun/src/`, `configs/`, `scripts/`, `deploy/runpod/`, `docs/contracts/`다. 모델·녹음·전문가 사례의 비공개 원본·env는 업로드하지 않는다. 기존 비공개 자산과 환경을 유지한다. requirements/pyproject/lock 변경은 별도 환경 배포를 요구하며 자동으로 pip 설치하지 않는다. boot/manager 스크립트 변경도 별도 bootstrap 배포를 요구한다.
4. [원격 스크립트](github_deploy_remote.py)는 실제 변경 대상 파일이 기준 Git blob 또는 새 blob과 일치하는지 확인한다. 운영 전용 덮어쓰기를 발견하면 중단한다. 변경되지 않은 운영 전용 overlay는 보존하므로 Git SHA는 마지막으로 반영한 Git 변경 범위를 나타내며 전체 운영 디스크와 Git의 동일성을 뜻하지 않는다.
5. overlay hash와 프로세스 관계를 확인하고 변경 파일·기존 manifest를 root 전용 작업 폴더에 백업한다. boot 재시작 루프를 일시 정지한 뒤 manager를 정상 종료하고 supervisor/서비스 종료를 기다린다. 준비 단계 전에 기존 프로세스가 남아 있으면 중단한다.
6. 영구 overlay와 manifest를 갱신하고 기존 `prepare_runtime.py`로 runtime·attestation을 구성한다. 삭제된 소스는 `deletedFiles`로 기록하여 재기동에도 복원되지 않게 한다. 개별 파일 삭제만 허용한다.
7. 파일 hash를 확인하고 boot 루프를 재개한다. 인증된 `/health/services`의 `status=ready`, `executorReady=true`, `workerRevision=배포 SHA`를 확인한다. 이는 실제 녹음 처리·발음 품질 QA 통과를 의미하지 않는다.
8. 실패하면 동일한 정상 종료 절차로 이전 overlay와 runtime 변경 파일을 복구하고 기존 revision의 readiness를 확인한다. 복구 확인까지 실패하면 pending marker와 백업을 남기고 후속 배포를 막는다.

작업은 SSH 연결과 분리되어 실행된다. runner 취소·네트워크 단절 시 중복 제출하지 않고 기존 작업의 result만 조회한다. 강제 프로세스 종료·호스트 장애까지 무중단 복구를 보증하지 않는다. 단일 Pod 서비스 재시작 동안 요청 접수가 일시 중단될 수 있다.

## 상태와 복구

- `/workspace/intelligentai/deployment/github-actions/state.json`: 마지막 성공 배포.
- 같은 디렉터리의 `pending.json`: 미완료 작업 ID. 존재하면 후속 변경 차단.
- `jobs/<commit>-<operation>/result.json`: 성공·실패 결과.
- 각 작업의 `backup.json`: 변경 전 소스 사본과 overlay manifest, 비밀 환경 제외.
- 각 작업의 `operation.log`: root 전용 준비 기록. workflow에서 통째로 공개하지 않는다.

runner 시간 초과만 보고 작업을 다시 시작하지 않는다. result/pending 및 실제 프로세스 상태를 먼저 조회한다. 원복 실패 또는 강제 중단이면 백업과 현재 파일을 대조하고 [수정 배포 순서](README.md#수정-배포-순서)로 복구한 뒤 상태 파일을 일치시킨다. pending만 삭제하여 문제를 덮지 않는다. 작업 기록은 자동 삭제하지 않으므로 운영자가 보관 기간을 정해야 한다.

기존 Base clone CI는 이 배포의 선행 조건이 아니다. 최초 배포에서 자동 회귀 QA를 실행하지 않으려면 push할 준비 커밋에 `[skip ci]`를 사용하고 배포 워크플로만 `gh workflow run deploy-runpod.yml --ref main`으로 실행한다. 이후 기존 CI 실행 정책은 별도 관리 대상이다.

## 이번 설정의 관측 범위

2026-09-29 설정 중 공식 API에서 Pod `s8hb5b0l5k4qlx`가 RUNNING임을 확인했다. 현재 GitHub CLI의 저장소 조회/push 접근은 403/조회 실패, 연결 GitHub 앱은 404를 반환했다. Pod SSH는 인증 전에 `Exceeded MaxStartups`로 닫혔다. 따라서 Secret 등록, 원격 main 반영, 대상 등록, 실제 workflow 실행과 배포·원복 확인은 아직 수행하지 못했다. 코드는 운영 구조를 추정해 우회하지 않고 검사에 실패하면 중단하도록 작성했다.
