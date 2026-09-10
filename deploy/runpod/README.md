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

Pod 시작 명령은 `bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh`다. `boot.sh`는 SSH를 먼저 시작하고 시스템 의존성·`/opt`를 재구성한 뒤 `run_services.sh`와 `supervise_services.py`를 시작한다. `deployment/int8-migration-verified.json`이 없으면 확인을 기다린다. 준비 실패는 최대 300초 간격으로 재시도하며 SSH를 유지한다. 중복 boot·manager·supervisor는 각각 잠금으로 막는다.

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

## 추론 API와 전체 피드백 워커

2026-09-10부터 [services.json](services.json)이 Clova 추론 API와 실제 Redis 분석 워커를 관리한다. Clova가 준비된 뒤에만 워커를 시작하고, 프로세스 종료·시작 시간 초과·연속 health 실패에는 10~300초의 대기 시간을 두고 재시도한다. 의존 서비스가 내려가면 워커를 정상 종료하고 의존성이 복원된 뒤 다시 시작한다. 종료 시 진행 중인 처리를 기다리며 제한시간 후 해당 프로세스 그룹을 종료한다.

API는 기존 `127.0.0.1:8000/v1/chat/completions`이고, 음성·영상 분석 요청은 VC-BE의 기존 Redis 계약으로 받는다. 연구용 `voice_coach.api.app`의 Oracle API는 이 배포에서 실행하지 않는다. 워커는 `scorer` 환경과 UID/GID 10001을 사용하고, Clova는 별도 `hyperclova` 환경을 사용한다. `AI_ANALYSIS_CLOVA_ENABLED=true`이면 같은 시도의 Seungun·입술 분석 근거로 Clova를 호출한다. 생성 실패 시 검증된 카탈로그 피드백을 유지한다.

Redis TLS/인증, 녹음용 S3 접근 설정, VC-BE와 같은 HMAC 키·동의 정책이 필요하다. 설정이 없으면 워커는 `blocked`로 대기하며 Clova는 계속 시작한다. `.env`의 B2 연구자료 자격증명은 녹음 저장소나 Redis 설정을 대신하지 않는다. 이번 배포에서 존재하지 않는 접속 값이나 새 HMAC 키를 생성하지 않는다.

### 비밀 설정과 재부팅 복원

[`worker.env.example`](worker.env.example)을 기준으로 root 전용 `/root/.config/intelligentai/analysis-worker.env`를 준비한다. 따옴표·변수 치환·여러 줄 값 없이 `KEY=VALUE`를 사용하고 파일은 `0600`, 부모는 `0700`으로 둔다. 키 JSON도 한 줄 문자열이며 backend의 기존 키 ID·비밀값·정책과 같아야 한다.

컨테이너 재생성 후에도 자동 복원하려면 이 설정들을 **문자열 값의 JSON 객체**로 만들어 RunPod의 비공개 환경 설정 `RUNPOD_ANALYSIS_WORKER_ENV_JSON`에 주입한다. `.env` 파일만 `/root`에 만든 상태는 지속적인 비밀 공급원이 아니다. 부팅 시 `prepare_worker.py`가 제공된 값을 읽어 root 전용 사본을 만들며, 공유 `/workspace`와 복구 archive에는 넣지 않는다. B2 키를 이 설정에 추가하지 않는다.

준비 단계는 승인된 overlay·자산의 해시와 Git 배포 revision, 활성 HMAC 키 ID·동의 정책·실행 device를 묶은 attestation을 생성하고, 실행용 `/run/intelligentai/analysis-worker.env`만 supervisor에 전달한다. 필수 설정이나 검증이 미완료면 실행용 파일을 생성하지 않는다. 기존 private source 파일을 갱신한 후 다음 명령으로 준비하면 supervisor가 설정을 다시 확인한다.

```bash
/opt/intelligentai/envs/scorer/bin/python /opt/intelligentai/app/deploy/runpod/prepare_worker.py
```

Custom Redis CA·별도 credential 파일을 선택하면 해당 파일도 재부팅 시 제공되고 UID 10001이 읽을 수 있어야 한다. 기본 template은 시스템 CA와 직접 주입된 자격증명을 사용한다. `services.json` 설정 변경은 manager를 정상 종료하고 `run_services.sh`를 다시 실행해 적용한다.

### 상태와 배포 버전

- `/health`: Clova 모델 상태, `process_id`, `deployment_revision`.
- `/health/services`: 전체 서비스의 준비 상태. 모두 준비되면 HTTP 200, 설정 대기·시작 중·장애면 HTTP 503. 오래된 상태 파일도 준비 완료로 응답하지 않는다.
- `/run/intelligentai/services.json`: 서비스별 PID·상태·재시작 횟수·고정 실패 코드. 설정값·자격증명은 포함하지 않는다.
- `/run/intelligentai/logs/{clova,analysis-worker}.log`: 서비스별 최대 10MiB, 이전 로그 최대 3개. 원문 traceback과 비밀값을 억제한다.
- `/opt/intelligentai/deployment.json`: source Git revision·overlay hash·원본 release. 부팅마다 Git 최신 코드를 받지 않고 마지막 배포본을 복원한다.

영구 overlay를 갱신할 때는 각 파일 SHA-256과 `overlay-manifest.json`의 `sourceRevision`을 함께 기록한다. `prepare_runtime.py`는 검증한 파일만 `/opt`에 적용하고 영상 release의 `0440` 권한 및 UID 10001의 임시 작업 디렉터리를 복원한다. 배포 후 프로세스·버전·health 확인은 추론 품질·실제 Redis/S3 요청 검증과 별도다.

실제 이전 상태와 원본 보존 여부는 [실행 기록](../../docs/runpod_b2_storage_migration_execution.md)을 따른다. 전체 원본 BF16 파일을 사용하는 실행 시 양자화이므로 디스크가 8bit 모델 크기로 자동 축소되는 것은 아니다.

## 고정 VLM의 attention 선택 보정

고정 모델의 `modeling_vlm.py`는 A6000 이름을 감지하면 영상 인코더를 FlashAttention2로 강제 전환한다. 기존 환경에는 flash-attn이 없어 최초 적재가 실패했다. INT8 adapter는 모델 생성 중에만 이 자동 전환을 억제하여 명시적으로 요청한 SDPA를 사용하고, 생성 후 원래 선택 함수를 복구한다. 원본 snapshot 파일·가중치를 변경하지 않는다. 이 서비스는 Clova에 텍스트 claim만 전달하며 입술 landmark는 MediaPipe가 담당한다.
