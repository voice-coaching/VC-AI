# RunPod 통합 API 8082 배포 — 2026-09-30

## 완료 상태

사용자 요청으로 분석·TTS 업무 API의 공용 진입점을 `0.0.0.0:8082`로 구성하고 외부 HTTPS를 공개했다. **AWS EC2에서 기존 서비스별 토큰으로 직접 접속해 두 서비스의 readiness 200을 확인했다.** SSH 터널을 통하지 않는 연결이다.

외부 base URL: `https://s8hb5b0l5k4qlx-8082.proxy.runpod.net`

`0.0.0.0`은 서버 bind 주소다. 외부 클라이언트는 위 HTTPS 주소를 사용한다. TCP 8082를 직접 공개한 구성이 아니므로 `공개 IP:8082` 접속으로 안내하지 않는다.

## 경로와 인증

| 경로 | 내부 대상 | 인증 |
|---|---|---|
| GET /gateway/health | gateway 자체 | public, liveness만 의미 |
| GET /health, /readyz | 분석 127.0.0.1:8080 | 기존 public 정책; 분석 상태만 의미 |
| GET /health/services | 분석 8080 | 기존 분석 API Bearer |
| POST /v1/analysis-jobs | 분석 8080 | 기존 분석 API Bearer |
| GET /v1/analysis-jobs/{requestId}?executionId=... | 분석 8080 | 기존 분석 API Bearer |
| POST /v1/analysis-jobs/{requestId}/cancel | 분석 8080 | 기존 분석 API Bearer |
| GET /health/live, /health/ready | TTS 127.0.0.1:8083 | 기존 TTS Bearer |
| POST /v1/tts/synthesize | TTS 8083 | 기존 TTS Bearer |

Seungun·영상 입술 관찰·GPT 코칭은 분석 API 내부 단계이며 별도 무인증 endpoint를 만들지 않았다. 관리용 SSH·Jupyter·이미지 내 UI 프록시는 공용 게이트웨이에 추가하지 않았다. 허용 경로 외에는 404다. 기존 8080/http도 호환성을 위해 유지했다.

## 실제 적용

- Pod: `s8hb5b0l5k4qlx`, 기존 GPU·디스크·비밀 설정 보존.
- ports: `22/tcp`, `8080/http`, **`8082/http`**.
- 시작 명령: `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`.
- 기존 분석 bootstrap은 변경하지 않고 새 wrapper가 호출한다. 분석 overlay와 sourceRevision `5ea4712e55f231450b2120c7121dfaa3ade1ed6f`는 유지했다.
- 별도 supervisor가 nginx와 한국어 TTS를 관리한다. 파일 정본은 [api-gateway 구성](../deploy/runpod/api-gateway/README.md)이다.
- TTS Python 환경·한국어 frontend·모델 cache는 영구 저장소에 있고, runtime token/state는 기동 시 `/run`에 다시 구성한다.
- 설정 PATCH로 컨테이너가 재설정됐으며, 실제 재기동 후 gateway·분석·TTS의 자동 복원을 확인했다. 적용 직전 진행 중 분석/유효 lease 집계는 0이었다.
- 재설정으로 SSH mapping과 host key가 바뀌었다. RunPod 인증 로그의 ED25519 지문과 수집한 공개키를 대조한 뒤 strict host-key 검증으로 재접속했다. 과거 SSH 포트를 재사용하지 않는다.

## 확인 근거

관측: **2026-09-30 01:04:53~01:06:40 KST** / **2026-09-29 16:04:53~16:06:40 UTC**.

| 확인 | 결과 |
|---|---|
| 재기동 후 Pod 내부 분석 /readyz | 200 ready |
| 재기동 후 Pod 내부 인증 TTS /health/ready | 200 ready |
| 외부 HTTPS /gateway/health, /readyz | 200 |
| 외부 HTTPS 무인증 /health/services, /health/ready | 401 |
| 외부 HTTPS /docs, /openapi.json | 404 |
| AWS → 새 HTTPS /health/services, 기존 분석 토큰 | 200 ready, executorReady=true, OPENAI/gpt-6-astra |
| AWS → 새 HTTPS /health/ready, 기존 TTS 토큰 | 200 ready, melo-ko-practice-r1 |
| AWS → 무인증 분석 POST·TTS POST | 401; 업무 생성 없음 |

AWS의 Python urllib 요청은 Cloudflare 계층에서 403을 받았으나 같은 서버의 curl HTTPS 호출은 위 결과를 반환했다. 클라이언트 차이에 따른 프록시 응답을 기록하며, 이 관측만으로 모든 HTTP 클라이언트의 동작을 보장하지 않는다. TLS 검증을 끄거나 서비스 인증을 제거하지 않았다.

Python/shell/nginx 구문·파일 hash·패키지 메타데이터와 운영 health를 확인했다. 실제 사용자 분석·GPT 생성·TTS 합성 POST·품질 QA·자동 회귀 테스트는 실행하지 않았다. TTS 내장 시작 warmup은 서비스 기동의 일부다.

## AWS 기존 호출 경로 전환 완료

사용자의 후속 요청에 따라 **2026-09-30 01:12 KST**에 AWS EC2 `13.209.184.226`의 nginx를 전환했다. Backend의 실제 `RUNPOD_ENDPOINT_URL`과 `EXAMPLE_TTS_BASEURL`은 모두 `https://ai.voice-coaching.site`이며 이 주소를 유지했다. Backend 환경·JAR·DNS 변경 없이 기존 호출이 새 RunPod 8082 upstream을 사용한다.

- 대상: `/etc/nginx/conf.d/ai.voice-coaching.site.conf`.
- 분석 기본 location: 기존 RunPod 8080 HTTPS에서 `https://s8hb5b0l5k4qlx-8082.proxy.runpod.net`으로 전환.
- TTS exact `/v1/tts/synthesize`: 기존 `http://127.0.0.1:18082` SSH 터널에서 같은 8082 HTTPS로 전환.
- 두 location 모두 기존 Authorization을 전달하고 Host·TLS SNI를 upstream에 맞춘다. 인증서·시스템 CA 검증을 유지하며 토큰을 nginx 설정에 저장하지 않는다.
- AWS TTS read timeout은 35초로 설정했다. RunPod gateway의 30초보다 길며 기존 연결 2초·송신 5초·자동 upstream 재시도 금지 설정은 유지했다.
- 원본 백업: `/var/backups/voice-coaching-gateway/20260929T161204Z/ai.voice-coaching.site.conf` (root 전용 디렉터리, 파일 0600).
- 원본 SHA-256: `25020daf013fbdd533f56ea4f8bbeb9f7c13b0f0687eeb3f3c109e50b7565f18`.
- 적용 SHA-256: `e8b979412a57a17d3c0c52ef664a56c1286b37ac4bbb5f6d0dcc8a4626eb0cac`.
- `nginx -t` 성공 후 graceful reload했다. Backend는 재시작하지 않았고 alpha-backend PID 86851·nginx master PID 1995는 유지됐다.
- 기존 도메인의 인증 health 성공 후 `voice-coaching-tts-tunnel`을 중지하고 자동 시작을 해제했다. unit·SSH 키는 삭제하지 않았다. nginx의 기존 18082 참조는 남아 있지 않다.

전환 직후 01:12:26 KST 및 **터널 중지 후 01:14:38 KST**에 AWS에서 기존 도메인을 확인했다.

| 기존 AWS 주소를 통한 확인 | 결과 |
|---|---|
| 인증 GET https://ai.voice-coaching.site/health/services | 200 ready, executorReady=true, workerRevision=5ea4712e55f231450b2120c7121dfaa3ade1ed6f |
| 인증 GET https://ai.voice-coaching.site/health/ready | 200 ready, revision=melo-ko-practice-r1 |
| 인증 GET /v1/tts/synthesize (전환 직후) | 405; POST 전용 경로 도달, 합성 요청 없음 |
| 무인증 POST /v1/analysis-jobs 및 /v1/tts/synthesize (전환 직후) | 401; 신규 업무 생성 없음 |
| Backend 내부 관리 health | UP |
| alpha-backend / nginx | active/running, enabled |
| voice-coaching-tts-tunnel | inactive/dead, disabled |
| 최종 nginx -t 및 설정 hash | 성공, 적용 hash 일치 |

이 확인은 기존 도메인을 통한 운영 연결·인증·readiness 범위다. Java Backend의 실제 업무 실행, 사용자 분석·GPT 생성·TTS 합성·품질 QA는 수행하지 않았다. AWS 콘솔·IAM·인증서·Backend 소스는 변경하지 않았다.

[전환 도구](../deploy/runpod/api-gateway/switch_aws_upstream.py)는 위 원본 hash만 허용하는 일회성 마이그레이션이다. 적용 후 재실행하면 drift 검사로 중단된다. 원복 시 현재 설정과 백업을 대조하고 `nginx -t` 후 reload해야 한다. **이전 설정의 TTS 터널은 이미 고장 난 상태였으므로 백업 복원만으로 TTS가 복구되지 않는다.** 터널을 다시 쓸 경우 현재 Pod SSH mapping·host key·전용 인증과 내부 TTS 포트 8083을 별도로 검증해야 한다.

## 남은 연결 경계

SSH host key가 바뀌었으므로 자동 배포의 고정 `RUNPOD_SSH_HOST_KEY` 및 별도 운영 클라이언트의 known_hosts는 다음 사용 전에 대조해야 한다. GitHub secret은 이번에 변경하지 않았다. AWS 업무 HTTP 호출은 이제 SSH 터널을 사용하지 않는다. 새 키를 검증 없이 자동 수락하도록 바꾸지 않는다.

HTTP proxy의 TLS·100초 제한은 [RunPod 공식 포트 안내](https://docs.runpod.io/pods/configuration/expose-ports)를 따른다. gateway는 30초 upstream timeout, 분석 body 64 KiB/TTS 32 KiB, 자동 upstream 재시도 비활성이다. 기존 업무별 인증·동시 실행 제한을 유지한다.

Pod 변경·런타임 배포는 수행했지만 Git commit/push/PR은 하지 않았다. 이번 gateway는 기존 GitHub Actions 분석 overlay와 별도 영구 배포 단위다. 원복과 차후 이미지 변경 조건은 [구성 안내](../deploy/runpod/api-gateway/README.md)를 따른다.
