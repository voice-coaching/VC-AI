# 통합 RunPod API 게이트웨이

RunPod HTTP proxy의 8082를 분석·TTS 공용 진입점으로 사용한다. 외부 주소는 `https://<Pod-ID>-8082.proxy.runpod.net`이며 `0.0.0.0`은 서버 bind 주소이지 클라이언트 접속 주소가 아니다.

2026-09-30 실제 포트 공개와 재기동 자동 복원을 완료했다. 대상·시각·AWS 직접 인증 확인은 [배포 결과](../../../docs/runpod_api_gateway_20260930.md)를 따른다.

| 경로 | 내부 서비스 | 인증 |
|---|---|---|
| /gateway/health | nginx 자체 생존 | public; 전체 readiness 아님 |
| /health, /readyz | 기존 분석 127.0.0.1:8080 | 기존 public 정책 |
| /health/services | 분석 8080 | 기존 분석 Bearer |
| /v1/analysis-jobs 및 하위 상태·취소 | 분석 8080 | 기존 분석 Bearer |
| /health/live, /health/ready | TTS 127.0.0.1:8083 | 기존 TTS Bearer |
| /v1/tts/synthesize | TTS 8083 | 기존 TTS Bearer |
| 그 외 | 전달하지 않음 | 404 |

입술·Seungun·GPT 코칭은 분석 작업 내부 단계다. 별도 무인증 모델 API를 새로 만들지 않는다. SSH·Jupyter·템플릿 관리 프록시와 폐기된 Clova는 공개하지 않는다. 기존 8080/http는 호환성을 위해 유지한다. TTS 8083은 loopback이며 외부 포트로 등록하지 않는다.

## 실행과 영속성

- `boot.sh`, `supervisor.py`, `prepare_tts.py`, `configure_korean.py`, `nginx.conf`를 `/workspace/intelligentai/deployment/api-gateway/`에 배치한다. 기존 분석 overlay·sourceRevision과 별도 배포 단위다. `switch_aws_upstream.py`는 AWS용 일회성 도구이며 Pod bootstrap 구성요소가 아니다.
- `prepare_tts.py`는 기존 TTS manifest의 service/requirements SHA를 검사하고 한국어 운영용 의존성을 영구 `/workspace/voice-coaching-tts/venv`에 설치한다. `configure_korean.py`가 hash로 확인한 Melo frontend를 KR 전용으로 구성하고 변경 전 소스는 복구용으로 보존한다. 모델 cache는 기존 영구 자산을 사용하며 editable install hook을 실행하지 않는다. 기존 Python 3.10.18과 nginx를 포함한 동일 Pod 이미지가 전제다.
- 한국어 g2pkk/python-mecab-ko/python-mecab-ko-dic, 기존 모델 가중치와 symbol ID·tone offset은 유지한다. 활성 소스 hash는 korean-runtime.json, 운영 의존성 hash는 gateway-environment.json에 기록한다. 과거 전체 환경의 바이트 동일 복원으로 표현하지 않는다.
- Pod 시작 명령은 `bash /workspace/intelligentai/deployment/api-gateway/boot.sh`다. 기존 분석 boot와 별도의 gateway/TTS supervisor를 관리한다. 기존 분석 준비·비밀 공급원은 그대로 상속된다.
- supervisor는 nginx와 TTS만 소유하며 분석 프로세스를 임의 종료하지 않는다. TTS API가 종료되면 해당 모델 자식을 정리하고 제한된 간격으로 재기동한다.
- TTS token은 기존 공급원에서 `/run/voice-coaching-tts/token`으로 복사해 0600으로 사용한다. 분석 토큰을 TTS에 대입하지 않는다.
- 상태는 `/run/intelligentai-gateway/state.json`, root 전용 프로세스 로그는 같은 디렉터리의 gateway.log/tts.log다. 로그는 10 MiB 초과 시 사본 1개를 유지하고 live 파일을 truncate한다. 상태의 running은 모델 ready를 뜻하지 않는다.
- 신규 생성 요청 없이 인증 health로 확인한다. 기존 TTS 서버는 시작 시 모델 적재와 내장 warmup을 수행한다. 이를 사용자 음성 품질 QA로 보고하지 않는다.

## 외부 공개와 변경 경계

Pod ports에 기존 설정을 보존하며 `8082/http`만 추가한다. TCP 8082를 별도로 열지 않는다. TLS는 RunPod HTTPS proxy가 담당한다. proxy의 100초 제한 안에서 TTS 응답을 반환하고 nginx는 30초 upstream timeout과 재전송 비활성 정책을 쓴다. 합성 POST를 게이트웨이가 자동 재시도하지 않는다. [RunPod 포트 문서](https://docs.runpod.io/pods/configuration/expose-ports)

포트·시작 명령 갱신은 컨테이너 reset을 유발할 수 있다. 필수 비밀의 지속 공급원·실행 파일·Python 환경을 먼저 준비하고 진행 중인 분석·lease를 확인한다. 현재 주소·SSH mapping은 API에서 다시 조회한다. 실제 배포 완료 여부는 [배포 조사 문서](../../../docs/runpod_deployed_pipeline_20260930.md)의 후속 운영 기록을 따른다.

AWS 설정은 Pod 배포와 별도다. 2026-09-30 01:12 KST 후속 작업에서 기존 `https://ai.voice-coaching.site` 호출 주소는 유지하고 AWS nginx의 분석·TTS upstream을 모두 RunPod 8082 HTTPS로 전환했다. 기존 SSH 터널은 중지·자동 시작 해제했다. 직접 호출 클라이언트는 기존 서비스별 토큰을 사용한다. [AWS 적용·최종 확인·원복 주의사항](../../../docs/runpod_api_gateway_20260930.md)을 따른다.

`switch_aws_upstream.py`는 관측한 원본 설정 SHA를 강제하는 일회성 도구다. 기본 실행은 변경 예상 hash만 출력하며, AWS에서 root로 `--apply`할 때 백업·구문 검사·reload를 수행한다. 이미 적용한 설정에는 재사용할 수 없고 새 변경은 현재 설정을 다시 조사해야 한다. Backend 환경·토큰·JAR은 수정하지 않는다.

원복은 기존 Pod 시작 명령 `bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh`와 기존 ports로 되돌리는 작업이다. 생성한 영구 파일·TTS 환경은 조사 없이 삭제하지 않는다. 원복에도 reset·진행 중 요청 영향을 검토한다.
