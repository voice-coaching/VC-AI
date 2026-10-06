# DLPC 재부팅·RunPod 의존성 감사 — 2026-10-07

## 현재 요청과 적용 상태

사용자는 RunPod 의존성을 제거하고 DLPC 재부팅 시 전체 배포 파이프라인이 다시 기동되도록 요청했다. 운영 경로 점검, GitHub RunPod 배포 제거와 재부팅 복구 코드 검토를 수행했다.

자동 승인 검토가 OS 패키지 설치·비밀 설정 재주입·서비스 기동·운영 접수 자동 재개를 포함한 영속 자동화 패치를 거절했다. 해당 부작용 범위를 사용자에게 명시해 승인을 요청했다. 거절된 자동화 코드는 작성·배포되지 않았으며, 기존 진단 전용 hook은 그대로다.

## 제거 완료한 외부 의존성

| 지점 | 실제 확인 또는 변경 |
| --- | --- |
| 분석·TTS upstream | AWS nginx에 `proxy.runpod.net` 없음. DLPC 역방향 SSH 경로 사용 |
| Backend 분석 주소 | `RUNPOD_ENDPOINT_URL=https://ai.voice-coaching.site` 확인 |
| AWS runtime profile | AI `e5c612f51850a0a3f51e901b53d4d806667eb3c3`, bundle `906df1334b8482a6c1fade401534dd8ea977edec8130da61acca9818b29f344c` |
| GitHub Actions | `Deploy main to RunPod`를 `disabled_manually`로 변경; 저장소 workflow YAML 삭제 |
| GitHub secrets | 해당 workflow 전용 `RUNPOD_API_KEY`, `RUNPOD_SSH_HOST_KEY`, `RUNPOD_SSH_PRIVATE_KEY` 삭제 |
| GitHub variables | `RUNPOD_POD_ID` 삭제 |
| RunPod 자산 | 이전 작업에서 Pod 중지; retained volume 삭제는 수행하지 않음 |

`RUNPOD_HTTP` enum, `runpod_*.schema.json`, verifier의 `deploy/runpod` 경로는 기존 계약·로컬 코드 식별자다. 이 이름들은 RunPod 서비스에 접속하지 않는다. 기존 원격 배포 스크립트와 과거 자료 복사 스크립트는 이력·복구 참고용이며 재부팅 경로에 넣지 않는다. 현재 `prepare_aws_upstream.py`의 옛 proxy 주소는 과거 구성 변환의 입력 패턴이다.

## 기존 재부팅 경로에서 확인한 결함

1. `recover_on_start.py`는 진단 모드만 시작한다. 현재 production `--admit` supervisor 구성은 `bootstrap.py`가 거절하므로 단순 재부팅 시 자동 운영 재개를 보장하지 않는다.
2. GitHub main push workflow가 RunPod 관리 키를 요구했다. 위 제거 작업으로 차단했다.
3. 기본 B2 archive와 GOP overlay만으로 최종 TTS 서비스·OS 미디어 도구의 동일성을 보장한다고 가정할 수 없다. 복구 후 실제 서비스 hash와 TTS fingerprint를 확인해야 한다.
4. 새 컨테이너에서 CUDA driver, platform supervisor, UID/GID, Python native library, ffmpeg/ffprobe와 약 46 GB 복원 공간이 필요하다. RAM/VRAM 정상 여부만으로 기동 가능성을 판단하지 않는다.
5. B2 backup은 로컬 journal을 실시간 영속화하지 않는다. 중단된 producer를 자동 재실행하거나 미완료 자료를 완료로 바꾸지 않아야 한다.
6. RunPod 중지 후 PC에서 사용하던 RunPod→DLPC 관리 경로도 종료됐다. 기존 AWS SSH 키는 고정 명령과 분석/TTS 포워딩만 허용한다. 신규 installer 적용에는 DLPC 플랫폼 터미널에서 한 번 실행하거나 별도 승인된 관리 경로가 필요하다.

## 승인 후 구현할 기동 순서

1. 영속 home hook에서 중복 실행 lock 획득; 이미 정상인 production 프로세스는 유지.
2. B2 고정 file ID/size/hash로 비어 있는 runtime 복원, GOP overlay와 private cuDNN 적용. 부분 설치·hash 불일치는 차단.
3. AWS 고정 SSH exporter로 역할별 비밀 재주입. 값은 출력·archive·Git에 남기지 않음.
4. OS 도구·TTS 서비스 fingerprint·AWS 배포 계약·journal 상태를 대조.
5. AWS 터널과 분석 시작, 분석 준비 완료 후 TTS 시작; 접수 차단을 유지한 채 상태 검증.
6. 두 서비스와 계약이 정상이면 운영 접수 자동 재개. 미확정 중단 작업·operator hold·변경된 release는 보존하고 명시적인 실패 상태 기록.

전체 컨테이너 재생성 및 자동 복구는 아직 실행 검증되지 않았다. 이 문서의 구현 순서를 완료된 기능으로 해석하지 않는다.
