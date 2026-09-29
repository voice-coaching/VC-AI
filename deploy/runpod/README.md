# RunPod A6000 INT8 배포 준비

`main` push 기반 자동 배포의 설정·대상 등록·복구 절차는 [GitHub Actions 배포 안내](GITHUB_ACTIONS.md)를 따른다. 최초 연결과 실제 배포 성공 여부는 해당 안내의 관측 범위와 Actions 실행 결과를 구분한다.

## HTTP v1.1 분석 서버

`AI_ANALYSIS_TRANSPORT=runpod_http`를 Pod 프로세스 환경에 설정하면 `prepare_runtime.py`가 `prepare_http.py`를 실행하고 manager가 `services.http.json`을 선택한다. `redis_stream`은 기존 구성, `disabled`는 Clova만 시작한다. 미설정 시 기존 Redis 배포 호환성을 유지한다. HTTP와 Redis 분석 worker를 함께 실행하지 않는다.

HTTP listener는 UID/GID 10001로 `0.0.0.0:8080`에서 실행된다. `/health`는 liveness, `/readyz`는 모델·자산·설정·저장소·Backend 계약 연결 상태다. 설정 누락 시 진단용 listener를 시작하고 업무 접수는 차단한다. 인증된 `/health/services`에서 준비 상태를 확인한다. 상세 API·배포 순서는 [보완계획](../../docs/runpod_http_implementation_plan_20260913.md), 필드·시간 계약은 [v1.1 계약](../../docs/runpod_http_contract_v1_20260913.md)을 따른다.

비밀 공급원은 `RUNPOD_ANALYSIS_HTTP_ENV_JSON` 또는 root 전용 `/root/.config/intelligentai/analysis-http.env`다. 준비 단계가 `/run/intelligentai/analysis-http.env`를 생성한다. reset 복원에는 Pod의 지속 비밀 공급원이 필요하다. JSON/env 값을 소스·일반 archive에 넣지 않는다. 필수 연결 항목은 다음과 같다.

- 서로 다른 `AI_ANALYSIS_API_TOKEN`, `AI_ANALYSIS_CALLBACK_TOKEN`과 `AI_ANALYSIS_CALLBACK_BASE_URL`.
- `AI_ANALYSIS_OBJECT_STORAGE_BUCKET`, `AI_ANALYSIS_OBJECT_STORAGE_REGION`, `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX`, 필요 시 HTTPS endpoint와 path-style. 별도 제한 AWS SDK credential 공급.
- `AI_ANALYSIS_READINESS_OBJECT_KEY`, `AI_ANALYSIS_READINESS_OBJECT_SHA256`, `AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES`. canonical prefix 내 작은 비사용자 객체이며 읽기 전용으로 사용한다.

HTTP 배포 attestation은 `voice-coaching.analysis-http-deployment.v1`이며 실제 release·device·자산 hash·그룹 권한을 검증한다. Redis의 HMAC key ID나 consent policy를 생성하지 않는다. HTTP 영상도 별도 execution binding을 사용하고 기존 consent 진입점과 분리한다. 공통 `process_admitted`가 기존 분석 결과 객체를 생성하며 HTTP serializer가 별도 결과 Schema로 투영한다.

추가 의존성은 `http-requirements.lock`의 wheel hash로 고정했다. `/workspace/intelligentai/deployment/http-dependencies-v1_1`의 wheel을 이용해 `/opt/intelligentai/http-python`에 오프라인 설치한다. 기존 scorer·Clova 패키지를 교체하지 않는다. 배포 archive에는 소스와 lock만 포함하고 복원 시 wheel 캐시도 별도로 준비한다.

8080 기동 확인 후 실제 Pod의 기존 ports를 보존하면서 `8080/http`를 추가한다. 변경 시 reset이 발생할 수 있으므로 코드·모드·비밀 복원을 먼저 준비한다. Proxy URL은 `https://<현재 Pod ID>-8080.proxy.runpod.net`이다. `/readyz` 200과 양방향 연결을 확인하기 전에는 AWS Nginx preparing 503을 유지한다. 코드 컴파일·import·health 확인은 개발자의 실제 처리·품질 QA를 대신하지 않는다.

이 디렉터리는 2026-09-09 이전에서 만든 `/workspace/intelligentai`를 사용한다. 원본 release는 보존하고 `deployment/int8-overlay/overlay-manifest.json`에 기록된 파일만 `/opt/intelligentai/app`에 덧씌운다. 모델 ID/revision은 유지하되 실행 형식은 bitsandbytes 0.50.2 LLM.int8이다.

## 적용 범위

이 문서는 기존 Redis 분석 worker와 Clova를 관리하는 배포 절차의 정본이다. 새 HTTP 전송은 [별도 수정 계획](../../docs/runpod_http_callback_revision_plan_20260910.md)의 구현 상태를 확인한다. 아래 Redis/HMAC 설정을 새 HTTP 계약의 필수값으로 추가하지 않는다.

PC에서 운영 대상·접속을 확인하는 순서는 [RunPod 운영 안내](../../.codex/runpod.md)를 따른다. 이 문서에는 현재 Pod 주소·상태를 보관하지 않는다.

## 준비

컨테이너에는 Python 3.12, Java 21, ffmpeg, libseccomp2, libsndfile1, libegl1, libgles2, rsync, UID/GID 10001이 필요하다. MediaPipe는 CPU 추론에서도 EGL/GLES 공유 라이브러리를 적재한다. 원본에서 옮긴 `envs/{api,g2pk,scorer,hyperclova}`와 B2에서 검증·복원한 `runtime-assets`를 사용한다. 추가 의존성은 영구 환경에 설치한다.

아래 명령은 대상 Pod의 Linux root 셸에서, 기존 boot·manager·supervisor와 자식 서비스가 중지된 유지보수 상태에만 실행한다. 영구 환경과 실행 사본을 변경한다. 자동 boot가 준비·기동 중이면 중복 실행하지 않는다. 필요한 의존성 보충 뒤 준비 명령의 종료 코드를 확인하고, 실패 시 manager를 시작하지 않는다.

```bash
set -e
uv pip install --python /workspace/intelligentai/envs/hyperclova/bin/python \
  --no-cache --no-deps bitsandbytes==0.50.2
python3 /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/prepare_runtime.py
# 준비 성공 후의 수동 기동. foreground 셸을 유지한다.
bash /opt/intelligentai/app/deploy/runpod/run_services.sh
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
# Docker를 지원하는 별도 호스트의 저장소 루트에서 실행한다.
docker compose -f deploy/hyperclova/compose.yaml \
  -f deploy/hyperclova/compose.int8.yaml up -d --build
```

## 추론 API와 전체 피드백 워커

2026-09-10부터 [services.json](services.json)이 Clova 추론 API와 실제 Redis 분석 워커를 관리한다. Clova가 준비된 뒤에만 워커를 시작하고, 프로세스 종료·시작 시간 초과·연속 health 실패에는 10~300초의 대기 시간을 두고 재시도한다. 의존 서비스가 내려가면 워커를 정상 종료하고 의존성이 복원된 뒤 다시 시작한다. 종료 시 진행 중인 처리를 기다리며 제한시간 후 해당 프로세스 그룹을 종료한다.

API는 기존 `127.0.0.1:8000/v1/chat/completions`이고, 음성·영상 분석 요청은 VC-BE의 기존 Redis 계약으로 받는다. 연구용 `voice_coach.api.app`의 Oracle API는 이 배포에서 실행하지 않는다. 워커는 `scorer` 환경과 UID/GID 10001을 사용하고, Clova는 별도 `hyperclova` 환경을 사용한다. `AI_ANALYSIS_CLOVA_ENABLED=true`이면 같은 시도의 Seungun·입술 분석 근거로 Clova를 호출한다. 생성 실패 시 검증된 카탈로그 피드백을 유지한다.

Redis TLS/인증, 녹음용 S3 접근 설정, VC-BE와 같은 HMAC 키·동의 정책이 필요하다. 설정이 없으면 워커는 `blocked`로 대기하며 Clova는 계속 시작한다. `.env`의 B2 연구자료 자격증명은 녹음 저장소나 Redis 설정을 대신하지 않는다. 이번 배포에서 존재하지 않는 접속 값이나 새 HMAC 키를 생성하지 않는다.

### 기존 Redis worker 비밀 설정과 재부팅 복원

[`worker.env.example`](worker.env.example)을 기준으로 root 전용 `/root/.config/intelligentai/analysis-worker.env`를 준비한다. 따옴표·변수 치환·여러 줄 값 없이 `KEY=VALUE`를 사용하고 파일은 `0600`, 부모는 `0700`으로 둔다. 키 JSON도 한 줄 문자열이며 backend의 기존 키 ID·비밀값·정책과 같아야 한다.

컨테이너 재생성 후에도 자동 복원하려면 이 설정들을 **문자열 값의 JSON 객체**로 만들어 RunPod의 비공개 환경 설정 `RUNPOD_ANALYSIS_WORKER_ENV_JSON`에 주입한다. `.env` 파일만 `/root`에 만든 상태는 지속적인 비밀 공급원이 아니다. 부팅 시 `prepare_worker.py`가 제공된 값을 읽어 root 전용 사본을 만들며, 공유 `/workspace`와 복구 archive에는 넣지 않는다. JSON 공급원이 있으면 기존 source 파일보다 우선하므로 설정 변경 시 지속 공급원도 갱신한다. B2 키를 이 설정에 추가하지 않는다.

준비 단계는 승인된 overlay·자산의 해시와 Git 배포 revision, 활성 HMAC 키 ID·동의 정책·실행 device를 묶은 attestation을 생성하고, 실행용 `/run/intelligentai/analysis-worker.env`만 supervisor에 전달한다. 필수 설정이나 검증이 미완료면 실행용 파일을 생성하지 않는다. 기존 private source 파일을 보충한 뒤, worker가 설정 대기 중일 때 아래 준비 명령을 실행하면 supervisor가 생성된 실행 env를 다시 확인한다. 대상 Pod의 Linux root 셸에서 실행하며 worker 설정과 attestation을 변경한다. 종료 코드와 생성 파일·blocked 사유를 확인한다. 이미 실행 중인 worker의 환경은 자동 교체되지 않으므로 manager와 부모의 재시작 관계를 확인하여 정상 종료·기동 절차로 적용한다.

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

과거 이전 근거는 [실행 기록](../../docs/runpod_b2_storage_migration_execution.md)을 따른다. 전체 원본 BF16 파일을 사용하는 실행 시 양자화이므로 디스크가 8bit 모델 크기로 자동 축소되는 것은 아니다.

## 고정 VLM의 attention 선택 보정

고정 모델의 `modeling_vlm.py`는 A6000 이름을 감지하면 영상 인코더를 FlashAttention2로 강제 전환한다. 기존 환경에는 flash-attn이 없어 최초 적재가 실패했다. INT8 adapter는 모델 생성 중에만 이 자동 전환을 억제하여 명시적으로 요청한 SDPA를 사용하고, 생성 후 원래 선택 함수를 복구한다. 원본 snapshot 파일·가중치를 변경하지 않는다. 이 서비스는 Clova에 텍스트 claim만 전달하며 입술 landmark는 MediaPipe가 담당한다.

## 파일 위치와 보존 범위

| 위치 | 역할과 작업 원칙 |
|---|---|
| `/workspace/intelligentai/releases/` | 이관한 원본 release. 원본 보존 |
| `/workspace/intelligentai/current` | 사용하는 release를 가리키는 symlink. 준비 스크립트는 `releases/` 내부인지 검사 |
| `/workspace/intelligentai/envs/{api,g2pk,scorer,hyperclova}` | 영구 Python 환경. 실행용 환경의 복원 원본 |
| `/workspace/intelligentai/runtime-assets` | 검증한 작은 scorer·MediaPipe 등 실행 자산과 manifest |
| `/workspace/intelligentai/deployment/int8-overlay/` | 수정 파일과 `overlay-manifest.json`. 다음 기동에 적용되는 배포 변경 |
| `/workspace/intelligentai/deployment/int8-migration-verified.json` | boot의 이전 검증 완료 표시 |
| `/workspace/intelligentai/deployment/hyperclova-int8.log` | 기존 수동 기동의 과거 로그. 신규 supervisor 로그는 `/run` 경로 사용 |
| `/opt/intelligentai/app` / `/opt/intelligentai/envs` | 원본·overlay·runtime 자산을 합쳐 만든 실행 사본. 컨테이너 재생성 시 재구성 |
| `/opt/intelligentai/hyperclova-runtime.json` | 영구 볼륨의 고정 모델 snapshot을 참조하는 실행 설정 |
| `/opt/intelligentai/deployment.json` | source Git revision·overlay SHA-256·원본 release |
| `/opt/intelligentai/worker-deployment/` | worker artifact attestation과 실행 시 검증할 `manifest.json` |
| `/root/.config/intelligentai/.env` | B2 수집 작업용 자격증명 사본. 디렉터리 `0700`, 파일 `0600` |
| `/root/.config/intelligentai/analysis-worker.env` | Redis·녹음 S3·HMAC 설정의 root 전용 원본. 디렉터리 `0700`, 파일 `0600` |
| `/run/intelligentai/analysis-worker.env` | 설정·artifact 검증 후 생성하는 root 전용 실행 환경. 파일 `0600` |
| `/run/intelligentai/services.json` | supervisor 상태·PID·재시작 횟수·고정 실패 코드. 비밀값 제외 |
| `/run/intelligentai/logs/{clova,analysis-worker}.log` | 서비스별 로그 최대 10MiB, 이전 로그 최대 3개 |
| `/run/intelligentai/worker/{work,tmp}` | UID/GID `10001`의 `0700` 작업·임시 폴더. 컨테이너 재생성 시 복원 |
| `/tmp/intelligentai-external-data/<job-id>/` | 외부 데이터 임시 캐시 계획. 전체 4GiB 제한은 아직 수집기에 구현되지 않음 |

Clova 원본 snapshot은 `current/data/models/hyperclovax-seed-think-32b/a6cdfd3464d1b767259cad23e164eaf39d3e3960`에 있다. 실행 시 INT8로 변환하므로 원본 약 66.6GB 가중치가 디스크에서 자동으로 줄어들지는 않는다. 모델 ID는 `naver-hyperclovax/HyperCLOVAX-SEED-Think-32B`다.

외부 데이터 원본과 파생 자료의 영구 저장소는 [Backblaze B2](../../.codex/backblaze.md)다. 필요한 작은 승인 실행 자산과 작업별 임시 캐시만 RunPod에 둔다. 기존 runtime에 필요한 모델을 외부 자료 정리 대상으로 일괄 삭제하지 않는다.

`/workspace` 공유 볼륨에서는 실제로 `chmod 0600`이 `0666`으로 나타났으며 소유권 변경도 제한되었다. 따라서 비밀 설정 파일은 이 공유 볼륨이나 일반 B2 복구 archive에 넣지 않는다. `/root`와 `/run` 사본은 컨테이너 재생성 시 사라질 수 있다. B2 수집용 `.env`는 [env 안내](../../.codex/env.md)에 따라 다시 전달하며, `prepare_runtime.py`는 이 B2 파일을 읽지 않는다. worker 설정은 위의 기존 Redis worker 복원 절차를 사용한다.


## 수정 배포 순서

대상 Pod의 Linux root 셸과 영구 overlay를 변경하는 절차다. 먼저 로컬 변경과 실제 배포 revision을 확인한다.

1. 변경 범위와 현재 작업 트리를 확인하고 원본 release·기존 overlay·manifest·현재 설정의 복구 자료를 확보한다. `.env`와 개인키는 일반 archive에서 제외한다.
2. 로컬에서 수정한 파일을 영구 `int8-overlay`의 같은 상대경로에 반영하고 `overlay-manifest.json`의 경로·SHA-256·40자리 `sourceRevision`을 함께 갱신한다. manifest에 없는 파일은 자동 적용되지 않는다. 부팅 때 Git 최신 커밋을 자동으로 받는 방식이 아니라 마지막 배포한 overlay를 복원하는 방식이다.
3. 기본 적용 경로는 갱신한 overlay로 다음 Pod 기동 때 boot가 재구성하게 하는 것이다. 실행 중인 Pod에서 수동 적용할 때는 실제 boot·manager·supervisor 부모 관계를 확인하고, 재시작을 관리하는 상위 프로세스까지 고려하여 요청 처리를 마친 뒤 서비스를 정상 종료한다. 자식 모델만 종료하면 supervisor가 다시 시작하므로 재구성 전의 중지 절차가 되지 않는다. 과거 기록의 PID를 재사용하지 않는다.
4. 기존 서비스와 자동 재시작 경로가 중지된 유지보수 상태에서만 SSH root 셸의 다음 준비 명령을 실행한다. 이 명령은 실행 파일·환경·attestation을 바꾼다. boot가 관리 중인 정상 기동에서는 boot가 같은 준비를 담당한다.

   ```bash
   python3 /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/prepare_runtime.py
   ```

5. 준비 성공 후 manager가 아직 실행되지 않는 수동 전환 상황에서는 아래 명령으로 Clova와 worker를 함께 관리한다. foreground에서 동작하므로 셸을 유지해야 하며, 상시 기동은 위 Pod 시작 명령의 boot 흐름을 사용한다. `start_hyperclova.py`만 별도로 실행하면 supervisor의 수명 주기·worker 의존성 관리를 적용하지 못한다.

   ```bash
   bash /opt/intelligentai/app/deploy/runpod/run_services.sh
   ```

6. 실제 overlay·실행 사본 hash, `deployment.json`, `/health.deployment_revision`을 대조한다. `/health/services`의 전체 상태와 worker 실패 코드를 별도로 확인한다. 배포 revision·파일 hash·확인한 상태와 아직 대기 중인 설정을 구분하여 기록한 뒤 B2에 비밀값을 제외한 복구 archive와 manifest를 보관한다.

원복할 때도 이전 overlay·manifest를 함께 복원하고 같은 준비·기동 절차를 적용한다. 원본 release를 직접 덮어쓰거나 `/opt`만 수정해 영구 배포가 끝났다고 판단하지 않는다. 기능·브라우저·회귀 QA는 개발자가 수행한다.


`prepare_runtime.py`는 `rsync --delete`를 사용하지 않는다. `/opt`에 임의 추가한 파일을 모두 청소하는 기능으로 해석하지 않는다. 영구 환경을 바꾸지 않고 실행 사본만 수정하면 재구성 시 변경이 사라질 수 있다. `start_hyperclova.py`만 별도 실행하면 manager의 worker 의존성 관리를 적용하지 못하며 기존 수동 프로세스를 supervisor가 자동 인계하지 않는다.
