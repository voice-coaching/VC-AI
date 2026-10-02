# Canonical 실행기와 운영 저장소 gate

최신: [2026-10-02 운영 기록](rollout-execution-20261002.md). 아래 adapter·subreaper는
전체 HTTP 실행기/부팅에 연결해 RunPod에 배포했고 제한 writer 키를 주입했다.
Backend의 검증/journal/apply 활성화 및 개발자 실행 수용 전이라 v4 접수는 off다.
아래 미연결·미배포 표기는 10/1 관측 이력이며 FE 테스트는 서버 준비와 별도다.

2026-10-01 후속 구현·읽기 전용 운영 관측. **실행기 전체 연결·배포·v4 활성화 완료 문서가 아니다.**

## 이번 구현

- `canonical_producer.py`: admitted request 한 건을 frozen R의 `scripts/run_release.py --cases … --output …`에 연결하는 adapter. launcher 고정 Python 3.11 경로를 유지한다. request ID를 case ID로 쓰고 입력 음원 크기/SHA·대본 SHA, R/S7 digest, 출력 schema/동일 request/audio identity, 실제 prepared WAV를 검증한다. core 원문 bytes를 보존한다. 이미 존재하는 execution 작업 디렉터리로 재실행하지 않는다.
- `canonical_process_guard.py`: 독립 Linux subreaper가 stdin lifetime pipe·monotonic deadline·TERM을 관찰한다. 직접 자식임을 `waitid(P_PIDFD)`로 확인한 pidfd만 종료하고, 입양된 자손까지 반복 회수한다. 별도 session을 만든 MFA도 process-group 하나에 의존하지 않는다. 종료 완료 전에는 guard가 끝나지 않는다. kernel I/O에 갇힌 프로세스는 회수 완료로 간주하지 않는다.
- `canonical_process.py`: 생성 중 취소에도 guard handle을 회수하고 lifetime pipe를 닫은 뒤 종료를 기다린다. 외부 강제 종료 등 음수 exit code는 `CANONICAL_PROCESS_CLEANUP_UNCONFIRMED`이며 상위 scheduler가 접수를 차단하고 운영자 확인을 요구해야 한다. **그 scheduler 연결은 아직 미구현**이다.
- core/MFA subprocess에는 명시한 PATH·locale·offline/수치 라이브러리 설정만 전달한다. B2·Backend·GPT·관리 키를 상속하지 않는다. 새 작업 파일/로그는 private 경로에만 생성하고 실패 시 보존한다. 소스·출력 데이터·인자를 서비스 로그로 전달하지 않는다.
- `canonical_filesystem.py`: SQLite journal과 실행 원본 workspace 초기화 전에 실제 `/proc/self/mountinfo`의 가장 깊은 mount를 검사한다. 전용 read-write ext4/xfs/btrfs/zfs가 아니면 거부한다. `/run`·`/tmp` 제외만으로 컨테이너 overlay를 영구 저장소로 오인하던 빈틈을 막는다. filesystem 유형 검사도 volume 수명·백업/복원 보장의 대체는 아니다.

위 코드는 HTTP factory/boot에 아직 연결하지 않았다. S7/GPT 실행·B2/receipt·callback orchestration, 실제 FE entry point 연결, 운영 키 공급·배포와 v4 접수는 여전히 남아 있다. 기존 공개 API·legacy 분석·TTS·frozen R/S7는 변경하지 않았다.

## 운영에서 새로 확인한 차단 조건

대상 `s8hb5b0l5k4qlx`. 현재 API metadata로 RUNNING 및 SSH mapping을 확인한 다음 접속했다. GPU 1개, 조회 요금 `$0.49/h`; GPU 종류 필드는 이번 응답에서 확인하지 못했다. Pod 시작·재시작·설정 변경은 하지 않았다.

2026-10-01 **08:22:27 UTC**:

- `/opt/intelligentai/deployment.json`의 sourceRevision은 `32d98e39767b56697bc7e58fb50e3e9d61432349`.
- overlay SHA는 `4721ac82c685e006951911bc7c756d91e55fb3285a5b861bc2481a1dde5c35ab`.
- supervisor 기록에서 api ready, restartCount=0. 인증 API 전체 readiness·GPT 생성·TTS를 새로 확인한 것은 아니다.

2026-10-01 **08:29:04 UTC**:

| 경로/환경 | 읽기 전용 관측 | 현재 설계의 판정 |
|---|---|---|
| `/workspace` | `fuse`, read-write, root:root 0777. 하위 intelligentai/deployment도 0777 | 전용 로컬 SQLite journal mount 조건 불충족 |
| `/opt/intelligentai` | 별도 mount 없이 `/`의 container `overlay`, root:10001 0750 | 컨테이너 재생성 이후 journal 보존 근거 없음 |
| 추가 ext4/xfs mounts | NVIDIA 실행 파일·라이브러리와 `/etc` 파일의 bind mount | journal용 read-write 디렉터리 volume 아님 |
| `/usr/bin/python3.12` | Python 3.12.3, jsonschema 4.25.1 | S7의 Python/jsonschema 버전 조건만 확인; 전체 runtime 준비 아님 |
| HTTP source/runtime env | `AI_CANONICAL_*` 이름 없음 | 운영 canonical 키/설정 미주입 |

읽기만 수행했으므로 FUSE의 chmod/SQLite 장애 동작을 실험한 결과는 아니다. 관측된 mount와 현재 adapter의 명시적 local-filesystem 계약이 맞지 않는다는 판단이다. `/workspace` 전체 chmod, journal의 `/opt` 우회, 비밀 공급원의 공유 볼륨 복사, 기존 서비스 종료는 하지 않았다.

B2 제한 키가 있다고 이 문제가 해결되지는 않는다. B2는 원본 artifact 보관소이며 현재 업로드 intent·응답 유실 checkpoint·고정 callback event/body·ACK 원문은 별도 SQLite journal이 소유한다. Pod 재생성으로 이를 잃으면 안전한 재전송/미확정 업로드 판정이 불가능하다.

## 필요한 저장 경계 결정

**후속 결정:** 사용자가 1번 AWS Backend PostgreSQL 방향을 승인했다. 선택 대기는 끝났으며 [Backend journal 구현](backend-journal.md)을 최신 정본으로 따른다. 아래는 결정 전 대안과 당시 관측이다. producer는 이제 원격 journal one-shot start를 필수로 하고 private ephemeral scratch를 사용하도록 수정했다. 기존 SQLite용 filesystem guard는 남아 있지만 새 운영 경로의 의존성은 아니다. 운영 전환 자체는 아직 미완료다.

1. **기존 AWS Backend PostgreSQL에 서버 내부 durable journal을 두는 방향(제안)**: 기존 실행/receipt/inbox DB 경계와 맞추되 업로드 intent·원본 확보·고정 event/callback/ACK의 새 내부 API 및 additive migration이 필요하다. RunPod에는 제한된 실행 scratch만 둔다. 기존 공개 FE/API 계약과 B2 원본 무기한 보존은 유지한다. DB에 장기 원본을 무조건 중복 보관하거나 DB 관리자 자격증명을 RunPod에 배포하는 안은 아니다.
2. 현재 SQLite 설계를 유지하려면 **Pod 재생성에도 유지되는 비공개 POSIX 로컬 volume**과 백업/복원 공급원이 필요하다. 현재 Pod에서는 확인되지 않았다. 새 자원·비용·이전 범위를 정하기 전 생성하지 않는다.

둘 다 기존 계획의 저장 구현 경계를 바꾸거나 새 인프라를 요구한다. 현재는 어느 안도 적용하지 않았다. 방향 확정 후 부팅 gate·journal 복구·executor 전체 연결을 완성하고 서버 접수 off 배포, 개발자 수용, 사용자 대상 활성화 순으로 진행한다. FE URL 자체가 이 저장소 문제의 해결 조건은 아니다.

확인 범위: Python 변경 5파일 구문 컴파일 및 `git diff --check`. 자동 회귀 테스트·fixture·프로세스 종료 시험·SQL/SQLite 동작·실제 음원/GPT·FE/브라우저 QA는 수행하지 않았다. 운영 키 주입·DB migration·서비스 배포·commit/push/PR도 미수행이다.
