# RunPod A6000 INT8 배포 준비

이 디렉터리는 2026-09-09 이전에서 만든 `/workspace/intelligentai`를 사용한다. 원본 release는 보존하고 `deployment/int8-overlay/overlay-manifest.json`에 기록된 파일만 `/opt/intelligentai/app`에 덧씌운다. 모델 ID/revision은 유지하되 실행 형식은 bitsandbytes 0.50.2 LLM.int8이다.

## 준비

컨테이너에는 Python 3.12, Java 21, ffmpeg, libseccomp2, libsndfile1, libegl1, libgles2, rsync, UID/GID 10001이 필요하다. MediaPipe는 CPU 추론에서도 EGL/GLES 공유 라이브러리를 적재한다. 원본에서 옮긴 `envs/{api,g2pk,scorer,hyperclova}`와 B2에서 검증·복원한 `runtime-assets`를 사용한다. 추가 의존성은 영구 환경에 설치한다.

```bash
uv pip install --python /workspace/intelligentai/envs/hyperclova/bin/python \
  --no-cache --no-deps bitsandbytes==0.50.2
python3 /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/prepare_runtime.py
python3 /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/start_hyperclova.py
```

준비 스크립트는 환경·코드·작은 runtime 자산을 컨테이너 디스크에 복사하고 root:10001 권한을 설정한다. RunPod 공유 볼륨은 chown을 거부할 수 있으므로 실행용 artifact 권한은 `/opt`에서 적용한다. 66.6GB Clova 원본 가중치는 영구 볼륨에 그대로 두고 별도 설정의 절대 경로로 참조한다. `/opt`는 Pod 컨테이너가 재생성되면 다시 구성해야 한다.

Clova 실행 설정은 GPU 배치 예산 40GiB, CPU 예산 8GiB, INT8 threshold 6.0, CPU offload 비활성화, 입력 최대 4,096토큰, 출력 최대 512토큰, 단일 직렬 generate다. `lm_head`, `vision_model`, `mm_projector`는 양자화 제외 대상으로 지정한다. 실제 INT8 module이 없거나 CPU/disk 배치가 발생하면 startup을 실패시킨다. GPU 배치 예산은 전체 GPU 사용량의 hard limit가 아니다.

서비스는 `127.0.0.1:8000`에만 바인딩한다. `/health`의 `quantization=bnb-int8`과 `maximum_input_tokens=4096`으로 설정을 확인할 수 있다. 모델 적재와 health는 한국어 설명 품질 또는 Seungun·영상 동시 실행 검증을 대신하지 않는다.

이전 무결성 확인 후 Pod 시작 명령을 `bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh`로 설정한다. `boot.sh`는 이미지의 SSH 서비스를 먼저 시작하고 시스템 의존성·`/opt`를 재구성한 뒤 Clova를 시작한다. `deployment/int8-migration-verified.json`이 없으면 모델을 시작하지 않는다. 모델 시작 실패 시 자동 재시도하지 않고 SSH를 유지한다.

RunPod 시작 스크립트는 호스트 CPU 수와 Pod CPU quota의 차이로 인한 과도한 스레드 생성을 막기 위해 `OMP_NUM_THREADS=4`, `MKL_NUM_THREADS=4`, `OPENBLAS_NUM_THREADS=1`을 지정한다.

INT8의 BF16→FP16 형 변환 경고는 같은 프로세스에서 최초 한 번만 기록한다. bitsandbytes 0.50.2가 토큰마다 각 선형 레이어에서 반복하는 경고를 영구 볼륨에 동기적으로 추가하면 생성 지연이 커진다. 다른 경고는 유지하며 BF16 연산 형식과 INT8 threshold 6.0은 바꾸지 않는다.

완료 요청은 GPU에서 한 번에 하나만 처리한다. 이미 처리 중이면 HTTP 503을 반환하며 대기열에 넣지 않는다. 상세 피드백을 위해 서버는 토큰 생성 단계마다 150초 제한을 확인해 초과 시 HTTP 504를 반환하며, 클라이언트 기본 제한은 180초다. 출력 토큰 상한까지 종료 토큰을 생성하지 못한 응답도 정상 완료로 반환하지 않는다. `/health.last_completion`에서 입력·출력 토큰 수, 생성 시간, 종료 사유를 확인할 수 있으며 원문은 기록하지 않는다.

상세 피드백 정책은 `grounded-detail-v1-20260909`다. 입력 v4의 `requiredFragments`는 관찰값과 연습 단계가 실제 메시지에 남도록 검증한다. 기존 v3 결과는 v4로 읽을 수 있지만, 없는 분석 근거를 자동으로 보충하지는 않는다. `/health`는 `feedback_policy_revision`, `grounded_input_schema`, `maximum_new_tokens`도 제공한다. 출력 한도 확대는 응답 시간을 늘릴 수 있으며, 이전 127토큰 답변의 약 33초 기록을 새 상세 답변의 지연으로 사용하지 않는다. [상세 수정과 분석 보완 계획](../../docs/clova_detailed_feedback_20260909.md)을 따른다.

## Docker 배포 대안

Docker를 지원하는 별도 호스트에서는 다음 override를 사용한다.

```bash
docker compose -f deploy/hyperclova/compose.yaml \
  -f deploy/hyperclova/compose.int8.yaml up -d --build
```

## Redis worker 경계

위 스크립트는 Redis 분석 worker를 시작하지 않는다. 기존 production 계약은 별도의 Redis TLS/인증, S3 media 접근 설정, authorization key 및 deployment attestation을 요구한다. 이 값을 임의 생성하거나 우회하지 않는다. 이관된 자산과 실행 환경을 준비한 뒤 실제 운영 설정을 연결해야 한다. 발음 판정 모델은 기존 Seungun을 유지한다.

실제 이전 상태와 원본 보존 여부는 [실행 기록](../../docs/runpod_b2_storage_migration_execution.md)을 따른다. 전체 원본 BF16 파일을 사용하는 실행 시 양자화이므로 디스크가 8bit 모델 크기로 자동 축소되는 것은 아니다.

## 고정 VLM의 attention 선택 보정

고정 모델의 `modeling_vlm.py`는 A6000 이름을 감지하면 영상 인코더를 FlashAttention2로 강제 전환한다. 기존 환경에는 flash-attn이 없어 최초 적재가 실패했다. INT8 adapter는 모델 생성 중에만 이 자동 전환을 억제하여 명시적으로 요청한 SDPA를 사용하고, 생성 후 원래 선택 함수를 복구한다. 원본 snapshot 파일·가중치를 변경하지 않는다. 이 서비스는 Clova에 텍스트 claim만 전달하며 입술 landmark는 MediaPipe가 담당한다.
