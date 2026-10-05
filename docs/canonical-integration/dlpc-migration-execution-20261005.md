# DLPC migration execution — 2026-10-05

상태: **코드 구현·후보 패키지 생성 완료, 자산 복사 진행 중. 운영 전환 미완료.**

## 범위와 실제 기준선

- 요청: RunPod의 실행 서비스를 DLPC로 이전하고 CPU 4개와 A5000 자원을 활용한다.
- RunPod 경유 SSH는 이전 중에만 사용한다. 기존 `ai.voice-coaching.site`의 실제 분석/TTS upstream을 DLPC로 전환하며 RunPod를 영구 중계기로 유지하지 않는다.
- 원본 Pod는 `s8hb5b0l5k4qlx`, 확인된 SSH는 `194.68.245.79:22132`다. DLPC는 `J3990.2020067.notebook.dlpc`이며 기존 `ssh dlpc-migration`으로 연결했다.
- 이전 조사 이후 운영 배포가 `canonical_native_core_20261005_v4` / `canonical_native_h5_20261005_v4`로 변경됐다. 실제 v4를 기준으로 포장한다. 폐기된 Seungun 경로를 다시 활성화하지 않는다.
- 원본 core SHA-256: `f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841`.
- 원본 H5 SHA-256: `efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428`.
- 코드 브랜치는 `feat/dlpc-migration-20261005`, 검토 기준 main은 `bcfadc8`이다. 운영 v4의 승인/계약 코드는 main과 일부 다르므로 전체 main으로 운영 사본을 덮어쓰지 않는다. 수정하는 공통 5개 모듈은 운영 사본과 비교해 이번 budget 변경 외 차이가 없음을 정적으로 확인했다.

## 확인된 DLPC 조건

CPU affinity 4개, RAM 11GiB, RTX A5000 VRAM 24,564MiB다. 컨테이너 root overlay 50GiB 중 최초 약 49GiB가 비어 있었고 sudo 권한을 확인했다. 홈 NFS의 작은 파일 생성·fsync는 성공했지만 사용자 quota와 컨테이너 재생성 후 자동 시작은 아직 확정하지 않았다. 이전 컨테이너에 복사했던 `/opt/voice-coaching/intelligentAI/runpod-import`는 현재 존재하지 않는다.

기존 Jupyter/MLflow/MinIO/Ollama 프로세스는 유지한다. 실행 자산은 `/opt/dlpc-migration-20261005/rootfs`에 격리 복사한다. 비밀 설정은 일반 자산 묶음에서 제외한다. 원본 Pod의 서비스·운영 upstream·녹음·B2 객체는 변경하지 않았다.

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

동시 분석 2건, standby 세대, 품질 승인, 새 실행 identity의 운영 allowlist 등록은 구현 완료로 표시하지 않는다. 생성된 CUDA 패키지는 CUDA 장치를 근거 및 동결 verifier/schema에 기록하며 기존 CPU identity로 위장하지 않는다. GPU 수치·음소 판정·점수 호환성은 개발자 확인 대상이다.

후보 패키지는 DLPC `/opt/dlpc-migration-20261005/candidates`에 전달했다. 생성은 모델 추론을 수행하지 않는다.

| 후보 | Core SHA-256 | H5 SHA-256 |
|---|---|---|
| CPU 1 thread | `676dfa443cd17bceac6591b230709b0f1573f9c8e9a5ddafeaf6fb2b81aef0e4` | `ba3a1e1ae51ca0fd47f01de70f33c95fa30c33e9bf47f75ebd4e98bf7882ac89` |
| CUDA FP32 1 thread | `6846f7f814651b39e912d703fb3839e0fcb098829d51c8a645d6c976afe166a1` | `57f06e748d2f8ec9de893c5f330721b56befa56207f013de57555a609992a25d` |

TTS 후보 service SHA-256는 `cbe2f84c565f583b6c539caea7a90d932f047abe78e6c86919e9b665b1d50c45`다. 각 패키지의 `executionApproved`/`qualityVerified`는 false다.

## AWS 연결

PC의 기존 키로 `ec2-user@13.209.184.226` 접속에 성공했다. DLPC에서는 해당 서버의 HTTPS 443은 연결되지만 SSH 22는 타임아웃이다. 이는 키 인증 전에 발생했다. 보안 그룹 규칙 자체는 EC2 역할의 `ec2:DescribeSecurityGroups` 권한 부족으로 조회하지 못했다.

DLPC 외부 IP `203.246.112.21`, 인스턴스에 연결된 보안 그룹 `sg-010a1f52288abc31e`를 확인했다. 사용자에게 AWS CloudShell 명령을 제공했다.

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

지금까지 수행한 확인은 소스 비교, 부모 manifest/hash 검증, 후보 소스 컴파일, 실제 대상의 자원·접속 상태 확인이다. 자동 회귀 테스트, 브라우저 QA, 음성/점수/피드백 품질 검증은 수행하지 않았다.

변경 문서 4개의 상대 링크 검사와 변경 Python 소스 컴파일은 통과했다. 전체 `check_source_pins.py`는 Windows `core.autocrlf=true` checkout에서 673건을 보고했다. 대표 파일의 checkout CRLF를 LF로 바꾸면 Git blob과 정확히 일치함을 확인했으나, 전체 검사 통과로 보고하지 않는다. 이 작업과 관계없는 연구 설정의 pin은 변경하지 않았다. PR commit은 저장소의 자동 QA 금지 지침에 따라 `[skip ci]`로 생성한다.

남은 단계는 자산 복사 완료 및 무결성 대조 → private 영속 백업/복원 경로 확정 → DLPC 설치 및 모델 준비 상태 확인 → 개발자 추론/품질 검증과 필요한 Backend identity 승인 → AWS 연결·upstream 전환 → 기존 lease/journal drain → RunPod 중지다. RunPod 삭제는 별도 결정이다.

실행 명령과 설정 경계는 [DLPC 배포 안내](../../deploy/dlpc/README.md), 설계는 [마이그레이션 계획](dlpc-migration-plan-20261005.md)을 따른다. API liveness와 실제 분석/GPT/TTS 품질 통과를 구분한다.
