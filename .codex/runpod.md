# 실행 환경 운영 안내 — DLPC / 과거 RunPod

파일명은 기존 링크 호환을 위해 유지한다. 최근 기록은 [2026-10-16 J3990 → J406 이전](../docs/canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md)이다. 문서 작업을 위해 GPU나 원격 서비스를 시작하지 않는다.

## 대상과 근거

| 범위 | 정본·진입점 |
|---|---|
| host별 사용자·UID/GID·SSH·터널·복구 profile | [host_identity.py](../deploy/dlpc/host_identity.py). J3990과 J406 설정을 섞지 않는다. 알 수 없는 host는 거부한다. |
| DLPC 복구·시작 | [배포 안내](../deploy/dlpc/README.md), [recover_on_start.py](../deploy/dlpc/recover_on_start.py), [start_production.py](../deploy/dlpc/start_production.py), [startup hook](../deploy/dlpc/install_startup_hook.py) |
| 배포 controller·설치 identity | [github_deploy.py](../deploy/dlpc/github_deploy.py), [deployed_release.py](../deploy/dlpc/deployed_release.py), [실제 workflow](../.github/workflows/deploy-dlpc.yml) |
| 장애 이력 | [10/15 cold 복구](../docs/canonical-integration/incident-20261015-dlpc-cold-recovery.md), [10/16 이전](../docs/canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md) |
| 과거 RunPod 복구 | [RunPod 안내](../deploy/runpod/README.md). [Clova 안내](../deploy/runpod/README.legacy-clova.md)는 폐기 경로의 이력이다. |

10/16 기록은 J406으로의 전환과 당시 readiness를 보고한다. 10/7 기록의 RunPod `EXITED`는 당시 중지 관측이며 현재 Pod 상태·볼륨 과금은 이번 조사에서 확인하지 않았다. 과거 주소·PID·GPU·가격을 운영 기본값으로 복사하지 않는다.

## 운영 요청의 순서

1. 현재 host/Pod, 접속 경로, 코드·실행 bundle·복구 profile, admission/hold와 진행 작업을 확인한다. 로컬 기록이 없거나 조회가 실패하면 미확인으로 남긴다.
2. 시작·중지·삭제·이전을 구분한다. 이전은 source/target·디스크·모델·CUDA·커널·TTS fingerprint·터널·복구 자산을 대조한다. 관리 응답이 유실되면 기존 작업 상태부터 조회해 중복 생성/이전을 피한다.
3. supervisor·resident·부모 프로세스와 자동 복구 경로를 확인한다. admission을 닫고 drain을 확인한 뒤 해당 배포 절차를 따른다. 원본 서비스와 대상이 동시에 신규 작업을 받지 않게 한다.
4. release/manifest·실제 hash·schema digest·Backend 지원을 대조한다. 옛 완료 marker, 단순 프로세스 생존, 소스 checkout만으로 설치 완료를 판단하지 않는다.
5. 결과 재전송·미완료 journal·보관 상태를 조정한다. 자동 producer 재실행이나 원본 삭제로 복구하지 않는다. 롤백은 Backend verifier·계약·core/H5·TTS·upstream을 함께 검토한다.

`.github/workflows/deploy-dlpc.yml`은 main push·수동 실행·5분 catch-up을 정의하며 enrollment와 self-hosted runner를 요구한다. 과거 기록의 30분 설명보다 현재 YAML이 설정 근거다. 문서 수정·로컬 main 변경만으로 원격 배포가 완료되지는 않는다. push는 CI·배포를 유발할 수 있으므로 단순 문서 검증에 포함하지 않는다.

## 상태 보고

API liveness, 전체 readiness, 모델 접근, 실제 녹음/GPT 생성, 피드백 품질, 독립 TTS 합성을 따로 보고한다. readiness에는 캐시와 시점 한계가 있다. `/health/services`, `/health/deployment`, `/health/handoff`, `/health/audiovisual` 및 TTS health의 인증·필드는 해당 runtime 소스로 확인한다. 익명 direct readiness는 내부 인증 readiness와 별도다.

자원 진단은 cgroup 한도와 실제 프로세스 사용량을 구분한다. 로그는 대상·시각·파일·회전본·검색 범위를 적고 사용자 원문·키를 제거한다. 원격 조회를 수행하지 않은 문서 작업은 현재 정상/장애 판정으로 인계하지 않는다.
