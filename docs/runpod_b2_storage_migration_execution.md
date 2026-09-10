# RunPod 스토리지 이전 실행 기록

실행일: 2026-09-09. **새 120GB Pod로 필수 자산 이전 및 Clova INT8 기동 완료. 이후 사용자가 기존 300GB Pod를 삭제했으며 API 목록에서 부재를 확인했다.** 전체 서비스 운영 전환이나 양자화 품질 검증을 완료했다는 뜻은 아니다.

## 현재 자원

| 항목 | 상태 |
|---|---|
| 새 Pod | `odqpp9eqly0v0x`, `intelligentai-a6000-int8-120gb`, RUNNING, EU-SE-1 |
| 새 GPU / RAM | RTX A6000 1개, API VRAM 48GB / RAM 50GB |
| 실제 제한 | VRAM 46,068MiB, cgroup RAM 49,999,998,976 bytes |
| 새 스토리지 / 실행 요금 | Pod volume 120GB, container disk 40GB, GPU $0.53/h |
| 현재 SSH | `root@194.68.245.197`, port `22134`, 현재 PC의 기존 SSH 키 사용 |
| 기존 Pod | `urgf7ov260m6to`, 사용자 삭제 완료, API 목록에서 부재 확인 |
| B2 | 기존 비공개 `speakai2026` 재사용, 새 버킷 생성 없음 |

이전 중 중복 GPU 실행비가 발생했다. 기존 Pod는 새 Clova readiness 확인 후 중지했다. 기존 Pod의 API `costPerHr`는 요율이며 EXITED 상태에서 GPU가 실행 중이라는 뜻이 아니다.

## 이전 무결성

- 원본 `releases`, `envs`, `deployment`, `current`를 복사했다. rsync 종료 코드 0.
- 일반 파일 **55,766개 / 77,602,381,858 bytes**. 파일·디렉터리·symlink 기록 61,918개를 SHA-256, 크기, 유형, 기록된 mode/uid/gid 및 link target으로 대조하여 불일치 0건을 확인했다. 별도로 최상위 세 디렉터리의 mode/uid/gid도 일치했다.
- source manifest SHA-256: `7aa68067a272b63d7f860fa9278714f97fc67f1b1c189001155e56a45d7c7424`.
- B2 runtime archive 1,271,255,040 bytes와 내부 **30개 자산**을 SHA-256 대조 후 `/workspace/intelligentai/runtime-assets`에 복원했다. 실행용 복사본은 `/opt/intelligentai/app`에 배치하고 root:10001, 디렉터리 0750·일반 파일 0640 권한을 적용했다.
- `uv-cache`, `hf-download-cache`, `incoming`은 새 볼륨에 복사하지 않았다. 부재 확인 완료.
- 사용자 지정 불완전 `incoming/runtime-assets.tar` 505,789,440 bytes는 이전 요청에서 삭제했고 원본 Pod 중지 전 부재를 재확인했다.
- 임시 이전 private key와 source helper 디렉터리를 삭제했다. 새 Pod의 PUBLIC_KEY 설정과 재생성된 authorized_keys에서도 임시 키를 제거했다. 기존 공개키는 유지했다.

복원한 원본 B2 객체는 `intelligentai/models/runtime/20260908/runtime-assets.tar`이며 archive SHA-256은 `3b500966358540968bd83b350f802fe88eda0e95fdf174ac7a27a1d40d831cf9`이다.

## INT8 실행 상태

- bitsandbytes **0.50.2**, torch **2.12.1+cu130**, transformers **4.52.4**, accelerate **1.14.0**. 의존성 검사 통과.
- 원본 release와 BF16 모델 파일은 보존했다. 배포 변경 9개 파일은 `deployment/int8-overlay/overlay-manifest.json`으로 별도 추적하고 `/opt`에 적용했다.
- BF16 snapshot을 읽을 때 LLM.int8 변환을 적용한다. **별도 양자화 checkpoint를 저장하지 않으므로 원본 약 66.6GB 모델 파일도 영구 볼륨에 유지한다.**
- threshold 6.0, CPU offload 비활성화, GPU 가중치 배치 예산 40GiB, CPU 예산 8GiB. 입력 최대 4,096토큰, 출력 최대 256토큰, 직렬 generate.
- 최초 시작은 고정 VLM 코드가 A6000에서 FlashAttention2를 강제로 선택해 실패했다. INT8 adapter가 모델 생성 중에만 해당 자동 전환을 억제하고 명시적인 SDPA를 사용하도록 보정했다. 원본 모델 코드·가중치 파일은 변경하지 않았다.
- 보정 후 **14개 shard 적재 완료**, `/health`가 `status=ready`, `quantization=bnb-int8`, `quantized_linear_modules=504`, `maximum_input_tokens=4096`을 반환했다.
- 준비 상태에서 `nvidia-smi` GPU 사용량 **34,485MiB ≈ 33.68GiB**, 전체 46,068MiB. 이는 모델 적재 후의 값이며 음성·영상·LLM 동시 추론 peak가 아니다.
- 서비스 주소는 **127.0.0.1:8000**. 외부 HTTP 포트를 공개하지 않았다.

시작 명령은 `bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh`로 적용했다. API 설정 변경으로 실제 컨테이너 재생성이 발생했고 영구 볼륨에서 시스템 의존성·`/opt` 재구성이 수행되었다. 최초 자동 모델 시작의 FlashAttention 오류를 보정한 뒤 같은 시작 스크립트를 수동 재실행하여 ready를 확인했다. 보정 후 전체 Pod 재시작을 추가 반복하지는 않았다. 이후 시작에도 보정된 영구 overlay를 사용한다.

새 volume의 `du -x -B1 -s`는 **85,375,512,576 bytes ≈ 85.38GB**, `/opt/intelligentai`는 **12,460,077,056 bytes ≈ 12.46GB**였다. 공유 파일시스템 메타데이터가 포함된 수치다. 120GB 영구 볼륨에는 약 34.6GB 여유가 있으며 후속 로그·작업 파일에 따라 변한다.

## 복구용 B2 백업

기존 모델·데이터 archive는 재업로드하지 않았다. 새 배포 코드·overlay manifest·이전 검증 기록만 기존 버킷에 보관했다.

- 최종 객체: `intelligentai/recovery/deployment-int8-20260909-odqpp9eqly0v0x/deployment-int8-final.tar.gz`
- 크기: **2,824,438 bytes**.
- SHA-256: `e31f71184efc79d5faf98bb55db05dbe6af8801df96a38deba5c2cdc76744ecc`.
- 업로드 응답의 SHA-1과 다운로드 후 SHA-256을 모두 확인했다.
- 보정 전 중간 백업 `deployment-int8.tar.gz` 2,823,552 bytes도 같은 prefix에 남아 있다. 복구에는 `-final` 객체를 사용한다.
- API 자격 증명과 임시 private key를 archive·Pod 설정·프로젝트 파일에 저장하지 않았다.

후속 영상 실행에서 누락된 MediaPipe 시스템 의존성(`libegl1`, `libgles2`)을 보완하고 RunPod Clova 시작 스크립트의 CPU 스레드를 제한했다. 당시 복구 객체는 같은 prefix의 `deployment-int8-video-fixes.tar.gz`다. 크기 2,825,651 bytes, SHA-256 `91ae8d075289c5e80e6508d85370190f99a7b3519ec5abc37d5562f1b00c6204`이며 다운로드 후 일치를 확인했다. 테스트 원본 영상은 이 백업에 포함하지 않았다.

Clova 시간 초과 수정 당시 복구 객체는 `deployment-int8-clova-fixed.tar.gz`다. 같은 prefix에 Clova 반복 경고 억제, 요청 대기·생성 제한, 실제 4건 성공 기록을 추가했다. 크기 **2,834,641 bytes**, SHA-256 **`d6c3eecc61cc241f7a384aeb5c546e33422cf38d564acf881e0d1a74d60ef2a5`**다. 업로드 SHA-1 및 다운로드 SHA-256 일치 확인을 완료했다. 모델·원본 영상·자격 증명은 추가하지 않았다.

**최신 복구 객체는 `deployment-int8-detailed-feedback.tar.gz`다.** 같은 prefix의 이 객체에는 상세 음성·입술 claim, 입력 v4의 필수 구절 검증, 상세 Clova 프롬프트, 출력 512토큰·생성 150초·클라이언트 180초 설정과 변경 문서를 포함한다. 크기 **2,869,943 bytes**, SHA-256 **`61ca3d8a6977e1154f30b555986f92781c22def34ab63f42b22e364638b1d5ab`**. 업로드 SHA-1 및 다운로드 SHA-256을 대조했다. 17개 overlay 파일과 실행 사본 일치, PID 12252의 ready를 확인했으며 **새 상세 정책의 영상/LLM 재추론은 하지 않았다.** [상세 수정 및 분석 보완 계획](clova_detailed_feedback_20260909.md)을 따른다. 이전 객체는 보존했고 새 버킷을 만들지 않았다.

## 남은 범위와 비용

Redis production worker에는 실제 Redis TLS/인증, S3 media 접근 설정, authorization key 및 deployment attestation 연결이 남아 있다. 이를 임의 생성하거나 우회하지 않았으며 worker를 시작하지 않았다. 이후 사용자가 직접 요청한 두 테스트 영상에서 누락 의존성과 Clova 반복 로그·요청 처리를 보완했다. **기본 경로와 40점 옵션 총 4건 모두 실제 Clova 결합 검증을 통과했고 fallback은 없었다.** 기본 Clova 생성은 약 33초였다. 최신 결과는 [Clova 수정 실행 기록](runpod_clova_runtime_fix_20260909.md)을 따른다. 브라우저·전체 회귀 QA, 동시 사용자 부하 시험 및 BF16 대비 양자화 품질 평가는 하지 않았다.

사용자가 기존 Pod를 삭제했으며 새 Pod만 API 목록에 남아 있는 것을 확인했다. **300GB→120GB 할당 축소가 완료되었다.** 앞서 확인한 공시 단가와 동일 실행 상태를 기준으로 월 $18(실행 중) 또는 $36(중지 중) 스토리지 절감에 해당한다. 새 running volume은 월 $12이며 container disk 비용은 별도다. 실제 청구액은 아직 조회하지 않았다.

설정·복구 방법: [RunPod 배포 준비](../deploy/runpod/README.md). 코드 변경은 로컬 작업 트리와 새 Pod overlay에 반영했으며 Git commit/push는 하지 않았다.

## 앞선 취소 이력

사용자가 GPU 적합성 검토를 먼저 요청하여 처음 만든 `rm38txs2c1jz18` 120GB Pod를 DELETE 204로 삭제했고 목록에서 부재를 확인했다. 그때 만든 임시 복원본도 제거되었다. EU-SE-1 network volume 생성 요청은 지원 지역 제한으로 거부되어 생성되지 않았다. 이후 사용자의 A6000 + 8bit 선택으로 위 새 Pod에서 이전을 재개했다.
