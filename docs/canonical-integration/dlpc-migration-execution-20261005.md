# DLPC migration execution — 2026-10-05

상태: **DLPC 자산 설치, 진단 API·Native/MFA·독립 TTS 기동 확인 완료. 영속 복구·AWS 연결·개발자 QA가 남아 운영 전환 미완료.** 최종 기동 관측: 2026-10-06 00:11 KST.

AI `main` 대상 draft [PR #23](https://github.com/voice-coaching/intelligentAI/pull/23)에 통합한다. Backend/FE와 운영 nginx upstream은 변경하지 않았다.

## 범위와 실제 기준선

- 요청: RunPod의 실행 서비스를 DLPC로 이전하고 CPU 4개와 A5000 자원을 활용한다.
- RunPod 경유 SSH는 이전 중에만 사용한다. 기존 `ai.voice-coaching.site`의 실제 분석/TTS upstream을 DLPC로 전환하며 RunPod를 영구 중계기로 유지하지 않는다.
- 원본 Pod는 `s8hb5b0l5k4qlx`, 확인된 SSH는 `194.68.245.79:22132`다. DLPC는 `J3990.2020067.notebook.dlpc`이며 기존 `ssh dlpc-migration`으로 연결했다.
- 이전 조사 이후 운영 배포가 `canonical_native_core_20261005_v4` / `canonical_native_h5_20261005_v4`로 변경됐다. 실제 v4를 기준으로 포장한다. 폐기된 Seungun 경로를 다시 활성화하지 않는다.
- 원본 core SHA-256: `f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841`.
- 원본 H5 SHA-256: `efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428`.
- 코드 브랜치는 `feat/dlpc-migration-20261005`, 검토 기준 main은 `bcfadc8`이다. 운영 v4의 승인/계약 코드는 main과 일부 다르므로 전체 main으로 운영 사본을 덮어쓰지 않는다. 수정하는 공통 5개 모듈은 운영 사본과 비교해 이번 budget 변경 외 차이가 없음을 정적으로 확인했다.

## 확인된 DLPC 조건

CPU affinity 4개, RAM 11GiB, RTX A5000 VRAM 24,564MiB다. 컨테이너 root overlay 50GiB 중 최초 약 49GiB가 비어 있었고 sudo 권한을 확인했다. 설치 후 overlay 여유는 약 24GiB였다. 홈 NFS는 작은 파일 쓰기에 성공했으나 실제 백업에서 사용자 quota 초과가 발생했다. 컨테이너 재생성 후 자동 시작은 미확정이다. 이전 컨테이너의 `/opt/voice-coaching/intelligentAI/runpod-import`는 현재 존재하지 않는다.

기존 Jupyter/MLflow/MinIO/Ollama 프로세스는 유지한다. 38개 자산 경로를 staging에 복사하고 rsync 내용·링크 대조 후 `/workspace`, `/opt/intelligentai`, Python 3.10 경로에 설치했다. 추가로 공개 CA 10개를 원본 SHA-256과 대조해 보완했다. 원본 Pod의 서비스·운영 upstream·녹음·B2 객체는 변경하지 않았다.

분석 설정과 TTS 토큰은 별도 SSH 스트림으로 `/etc/intelligentai`에 root:root 0600으로 전달했다. TTS 실행 토큰은 `/run/voice-coaching-tts/token`에 UID/GID 10001, 0600으로 주입했다. 비밀값은 코드·일반 archive·NAS에 넣지 않았다.

## 코드 및 패키지

| 경로 | 변경 |
|---|---|
| `src/voice_coach/backend_analysis/resource_budget.py` | DLPC 프로필: 활성 분석 1건, 가용 RAM 2GiB 미만 또는 측정 불가 시 새 접수 거절 |
| `http_runtime.py` | 기존 중복 실행 조회 이후, claim 이전에 자원 예산 적용 |
| `canonical_settings.py`, `canonical_resident.py`, `canonical_producer.py`, `canonical_executor.py` | 프로필을 자식으로 명시적으로 전달하고 수치 라이브러리 스레드를 1로 제한 |
| `deploy/dlpc/build_release.py` | v4의 hash를 검증한 별도 CPU/CUDA core·H5 파생 release 생성. Native→MFA 순서로 cold 준비, 기존 3회 recycle 유지 |
| `deploy/dlpc/gpu_budget.py` | 분석/TTS 사이의 공용 GPU 실행 lock, 제한 시간 및 Torch 1 thread/FP32 설정 |
| `deploy/dlpc/build_tts.py` | 검증된 TTS 소스의 thread/메모리/GPU 예약 적용, 기존 seed/rate 보존 |
| `deploy/dlpc/copy_assets.py`, `verify_copy.py`, `assets-20261005.json` | 명시적인 자산 목록, 재개 가능한 복사, 5GiB 디스크 여유, 파일 내용·링크 비교 |
| `deploy/dlpc/install_staged.py`, `launch.py`, `supervisord.conf` | 전용 UID·private 경로·역할별 설정·loopback listener·재시작/로그 제한. 초기 분석 접수와 journal 복구는 닫음 |
| `deploy/dlpc/memory_accounting.py` | 실제 MemAvailable과 확인 가능한 cgroup v1/v2 중 작은 headroom 사용. 미확인 OOM·한도 값을 만들지 않음 |
| `deploy/dlpc/backup_staging.py`, `receive_settings.py` | 사용자 자신의 NAS quota로 비밀 제외 백업, 역할별 설정의 SSH stdin 수신·검증 |

동시 분석 2건, standby 세대, 품질 승인, 새 실행 identity의 운영 allowlist 등록은 구현 완료로 표시하지 않는다. 생성된 CUDA 패키지는 CUDA 장치를 근거 및 동결 verifier/schema에 기록하며 기존 CPU identity로 위장하지 않는다. GPU 수치·음소 판정·점수 호환성은 개발자 확인 대상이다.

후보 패키지는 DLPC `/opt/dlpc-migration-20261005/candidates`에 전달했다. 생성은 모델 추론을 수행하지 않는다.

| 후보 | Core SHA-256 | H5 SHA-256 |
|---|---|---|
| CPU 1 thread v2 | `6a6fd3c24b2bedd81db8a2b463dcc0a820e8176142efe9b3df47f79ae251a87e` | `fda90f2c9df39a548bf8bf181d6451864e3e247438f30716d5a44f2aadc011eb` |
| CUDA FP32 1 thread v2 | `4d29ddf6da45d43de6ceb0b07b70cecaf291ae5bc92b700ece6b320a8e6c423d` | `de8f66aa7688404f2f59fc17e67dd2c250cf746d58f9697bebd8fee87095f5f9` |

TTS service SHA-256는 `8f25077eab50503b914e530866225d8984899dac2dcbf2791c622d90c36ea83b`, revision은 `melo-ko-practice-dlpc-r1`이다. 전환 시 Backend revision/fingerprint도 일치시켜야 한다. core 후보의 `executionApproved`/`qualityVerified`와 TTS의 `qualityVerified`는 false다.

## 기동 중 발견·수정한 결함

1. 복사 필터의 `*.pem` 제외가 HTTPS용 공개 `certifi/cacert.pem`까지 누락했다. API가 `FileNotFoundError`로 종료됐다. 공개 CA만 포함하는 예외를 copy/verify에 추가하고 설치본에 원본 hash 검증 후 보완했다. TLS 검증을 끄지 않았다.
2. 보존된 MFA watchdog은 cgroup v1 경로를 강제해 DLPC에서 실패했다. 별도 v2 core에서 실제로 읽을 수 있는 메모리 근거를 사용하게 했다. 다른 서비스 reserve는 2GiB, 접수 reserve는 2GiB + 기존 MFA combined RSS 상한이다. 기존 worker/supervisor RSS 상한과 3회 recycle은 보존했다.
3. 숫자 로그인명 `2020067`은 실제 UID `120067`과 다르다. Supervisor SSH 프로그램은 UID 120067로 지정했다.
4. root 소유 자산은 서비스 그룹에 읽기만 허용하되 owner write bit를 유지했다. MFA의 `copy2`로 만들어지는 worker 소유 임시 설정이 쓰기 불가능해지는 문제를 방지한다.
5. 설치 직전 tar 포장 과정의 JSON 줄바꿈 변경으로 inventory hash가 달라져 설치가 거절됐다. 검증 영수증과 동일한 원본 바이트를 재전달한 뒤 설치했다. 검증 조건을 완화하지 않았다.

첫 v1 CUDA 후보는 Native GPU 로딩까지 성공했으나 MFA 메모리 계측에서 실패했다. 해당 후보를 덮어쓰지 않고 `canonical_dlpc_{cpu,cuda}_20261005_v2`를 별도로 생성·설치했다. v2 파일별 manifest와 H5 dependency lock hash를 모두 대조했다.

## 실제 기동 관측과 백업

2026-10-05 15:11:43 UTC(10-06 00:11:43 KST), DLPC에서 관측했다.

| 항목 | 실제 결과 |
|---|---|
| 분석 API `/health` | 200, alive |
| 분석 API `/readyz` | 503, not_ready. bootstrap/handoff 접수 비활성 상태 |
| CUDA Native + MFA v2 | READY, core hash 일치. 기동 준비 약 31.44초 |
| TTS 인증 `/health/ready` | 200, ready, `melo-ko-practice-dlpc-r1` |
| 두 모델 준비 상태의 RAM | 총 11GiB, MemAvailable 6,945,924KiB(약 6.62GiB) |
| 두 모델 준비 상태의 GPU | 2,437 / 24,564MiB, 당시 utilization 0% |
| AWS SSH | TCP 22 timeout. 전용 tunnel 미기동 |

이 값은 대기 상태이며 요청 처리 peak·처리량·품질 검증이 아니다. TTS의 기존 기동 warmup은 실행됐지만 사용자 녹음 분석/GPT 생성·오디오 청취 QA는 하지 않았다. 점검용 Native/MFA는 수명 신호를 닫고 guard 종료를 기다렸으며 host/worker PID가 사라진 것을 확인했다. 진단 API와 TTS는 supervisor 아래 실행 중이다.

NAS `~/.local/share/intelligentai/migration/20261005`의 비밀 제외 archive는 14,123,270,144바이트에서 `Disk quota exceeded`로 실패했다. 실패한 partial만 정리했고 `backup.json`에 실패 상태를 보존했다. 다른 UID로 quota를 우회하지 않았다. 원본 RunPod를 보존한 상태에서 진단 설치만 진행했으며 **영속 백업 완료로 보지 않는다**. PC의 현 B2 설정은 필수값 검사를 통과하지 못해 대체 B2 객체 조회를 수행하지 못했다.

컨테이너 재생성 시 자산 복원, private 설정 재주입 및 플랫폼 시작 hook이 필요하다. `/etc`, `/run`, overlay가 유지된다고 가정하지 않는다. 이전 전용 SSH 키의 홈 보존만으로 전체 서비스 복구가 보장되지 않는다.

## AWS 연결

PC의 기존 키로 `ec2-user@13.209.184.226` 접속에 성공했다. DLPC에서는 해당 서버의 HTTPS 443은 연결되지만 SSH 22는 타임아웃이다. 이는 키 인증 전에 발생했다. 보안 그룹 규칙 자체는 EC2 역할의 `ec2:DescribeSecurityGroups` 권한 부족으로 조회하지 못했다.

DLPC 외부 IP `203.246.112.21`, 인스턴스에 연결된 보안 그룹 `sg-010a1f52288abc31e`를 확인했다. 사용자에게 AWS CloudShell 명령을 제공했다.

사용자의 첫 실행은 Ubuntu 서버 셸에서 `aws: command not found`로 끝나 규칙이 적용되지 않았다. AWS 웹 콘솔 검색에서 CloudShell을 열면 사전 설치된 CLI와 콘솔 계정 권한으로 아래 명령을 실행할 수 있다. EC2 서버에 CLI를 설치하는 것만으로 IAM 권한이 생기지는 않는다. [AWS CloudShell 실행 안내](https://docs.aws.amazon.com/cloudshell/latest/userguide/getting-started.html).

```bash
aws ec2 authorize-security-group-ingress \
  --region ap-northeast-2 \
  --group-id sg-010a1f52288abc31e \
  --protocol tcp --port 22 \
  --cidr 203.246.112.21/32
```

자동 승인 검토가 새 SSH 키 등록을 최초 거절해 사용자에게 이유와 권한 범위를 제시했다. 이후 사용자가 **전용 키 생성·등록을 명시적으로 승인**했고 다음을 수행했다.

- DLPC에서 전용 키 생성. PC 개인키는 복사하지 않음.
- AWS 기존 authorized_keys를 보존하고 새 공개키 추가.
- `restrict`, `permitopen="none"`, `permitlisten="127.0.0.1:18080"`, `permitlisten="127.0.0.1:18083"`, 강제 `/bin/false` 적용. 두 역방향 포워딩 외 셸/직접 포워딩 제한.
- 인증된 PC→AWS 세션으로 호스트 공개키를 조회해 DLPC 전용 known_hosts에 고정.
- 등록 후에도 DLPC→AWS SSH TCP 타임아웃을 관측했다. 공개키 등록과 네트워크 허용은 별개다.

`prepare_aws_upstream.py`로 AWS에 별도 nginx 후보를 생성했다. 기존 인증·TLS·도메인은 보존하고 분석 18080/TTS 18083으로 라우팅하며 후보 안에 RunPod proxy 주소가 남지 않음을 확인한다. `nginx -t` 문법 검사는 성공했다. 운영 설정 교체나 reload는 수행하지 않았다.

## 검증 및 남은 완료 조건

지금까지 수행한 확인은 소스 비교, 부모 manifest/hash 검증, 후보 소스 컴파일, 자산 무결성 대조, 실제 대상의 자원·접속·기동 상태 확인이다. 자동 회귀 테스트, 브라우저 QA, 음성/점수/피드백 품질 검증은 수행하지 않았다.

변경 문서 4개의 상대 링크 검사와 변경 Python 소스 컴파일은 통과했다. 전체 `check_source_pins.py`는 Windows `core.autocrlf=true` checkout에서 673건을 보고했다. 대표 파일의 checkout CRLF를 LF로 바꾸면 Git blob과 정확히 일치함을 확인했으나, 전체 검사 통과로 보고하지 않는다. 이 작업과 관계없는 연구 설정의 pin은 변경하지 않았다. PR commit은 저장소의 자동 QA 금지 지침에 따라 `[skip ci]`로 생성한다.

남은 완료 조건은 다음과 같다.

1. NAS quota 증설 또는 별도 영속 저장소, 검증된 백업·복원과 private 설정 재주입/플랫폼 시작 경로 확정.
2. DLPC→AWS TCP 22 허용 후 전용 tunnel의 두 loopback endpoint 연결 확인.
3. AGENTS.md에 따라 개발자의 실제 추론·점수/피드백/TTS 품질 QA, 검토된 새 core/H5 identity의 AI·Backend 등록 및 TTS revision/fingerprint 전환.
4. 기존 작업·lease/journal drain과 전체 readiness 확인 후 AWS upstream 전환, 그 이후 RunPod 중지. RunPod 삭제는 별도 결정.

실행 명령과 설정 경계는 [DLPC 배포 안내](../../deploy/dlpc/README.md), 설계는 [마이그레이션 계획](dlpc-migration-plan-20261005.md)을 따른다. API liveness와 실제 분석/GPT/TTS 품질 통과를 구분한다.
