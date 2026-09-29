# RunPod 운영 배포 안내

현재 운영 기준은 **HTTP 분석 API + Seungun + 후보별 영상 관찰 + 외부 GPT-6 API**다. Clova는 운영 추론·채점·readiness에서 제외됐다. 실제 모델 ID는 `gpt-6-astra`다. [2026-09-28 배포 파이프라인과 관측 근거](../../docs/runpod_deployed_pipeline_20260928.md)를 먼저 읽는다.

이 문서는 Pod의 `/opt/intelligentai/app` 및 영구 `deployment/int8-overlay`를 직접 대조해 갱신했다. 조사 시작 시 로컬 `main` HEAD는 `44dc1300050b294f76c150a06e965dc6dea0388b`, Pod 배포 metadata는 `a97b86eba700e8ed6d197f929ebc9ec1258ecf52`였다. **현재 로컬 checkout의 Clova 중심 스크립트를 그대로 재배포하면 안 된다.** 배포 metadata와 실제 파일 hash를 함께 확인한다. 이번 변경은 문서 갱신이며 소스 동기화·재배포가 아니다.

## 현재 기동과 실행 경계

```text
Pod start command
  -> /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh
  -> prepare_runtime.py: base release + verified overlay + runtime assets
  -> /opt/intelligentai/app
  -> prepare_http.py: private settings + artifact attestation
  -> /run/intelligentai/analysis-http.env
  -> run_services.sh -> supervise_services.py -> api (:8080)
  -> HTTP executor -> Seungun / visual / external GPT API

Independent TTS manager -> MeloTTS (:8082, loopback)
```

배포된 코드의 기본 transport는 `runpod_http`이고 `services.http.json`에는 `api`만 있으며 서비스 의존성 목록은 비어 있다. supervisor의 허용 서비스 이름은 `api`, `analysis-worker`다. 과거 Clova service manifest는 이 supervisor에서 유효하지 않다. `redis_stream`/`disabled` 분기나 구형 파일이 남아 있다는 이유로 해당 모드가 현재 운영 가능한 것으로 간주하지 않는다.

## 설정 공급

- `RUNPOD_ANALYSIS_HTTP_ENV_JSON` 또는 `/root/.config/intelligentai/analysis-http.env`에서 준비하고 `/run/intelligentai/analysis-http.env`로 전달한다. JSON 공급원이 있으면 private source를 갱신하므로 공급원 간 우선순위를 확인한다.
- `AI_ANALYSIS_COACHING_ENABLED=true`, `AI_ANALYSIS_COACHING_RESULT_CONTRACT=v3`, `AI_ANALYSIS_COACHING_PROVIDER=OPENAI`, `AI_ANALYSIS_COACHING_OPENAI_MODEL=gpt-6-astra`, `OPENAI_API_KEY`가 필수다. 준비 과정은 `AI_ANALYSIS_CLOVA_*`를 제거한다.
- Backend 양방향의 서로 다른 서버 토큰, callback URL, 사용자 녹음 저장소와 읽기 자격증명, readiness 객체, Seungun·영상 자산은 별도로 필요하다. HTTP에 Redis HMAC·consent grant 설정을 추가하지 않는다.
- private env는 root 전용 `0600`으로 보관한다. 키는 소스·문서·overlay·일반 archive에 넣지 않는다. 컨테이너 재생성 후 비밀 공급원 복원은 별도로 확인한다.

현재 기본 호출 제한은 120초·출력 1200 completion tokens다. 현재 시도 입력의 16000 예산에 전문가 사례 전체를 위한 예약분을 더하며, 관측된 시작 로그의 입력 예산은 410821이다. 이 수치는 UTF-8 바이트 기반 보수적 추정 한도이고 실제 API token usage와 다르다. 자세한 입력 계약과 해시는 배포 파이프라인 문서를 따른다.

## 상태와 로그

| 확인 대상 | 의미 |
|---|---|
| `:8080/health` | API liveness |
| `:8080/readyz` | executor·Backend callback·OpenAI 모델 접근·저장소 준비 |
| 인증된 `:8080/health/services` | provider/model, workerRevision, executorReady, reasonCode |
| `/run/intelligentai/services.json` | 분석 supervisor의 실제 서비스 상태 |
| `/run/intelligentai/logs/analysis-http.log` 및 회전본 | 분석·GPT·fallback·callback 로그 |
| `/opt/intelligentai/deployment.json` | 배포 revision과 overlay hash metadata |
| TTS `:8082/health/live`, `/health/ready` | 별도 TTS 토큰으로 확인하는 독립 서비스 상태 |

OpenAI readiness는 인증된 모델 조회를 최대 300초 캐시한다. readiness 성공은 실제 GPT 생성·녹음 처리·피드백 품질 통과를 뜻하지 않는다. 로그 조회는 RunPod container/system 스트림과 Pod 내부 서비스 파일을 함께 구분한다. container 스트림에 최근 출력이 없다는 것만으로 내부 서비스 오류가 없다고 결론 내리지 않는다.

## 수정 배포 순서

1. [운영 안내](../../.codex/runpod.md)로 현재 Pod와 상태·접속 주소를 확인하고 로컬 Git 상태·실제 배포 파일을 대조한다.
2. Backend의 결과 v3 수용 여부와 진행 중 작업을 확인한다. 신규 접수·진행 작업 처리 및 복구 계획을 정한 유지보수 범위에서 배포한다.
3. 기존 base release·overlay·manifest와 private settings의 복구 위치를 보존한다. 비밀은 root 전용 별도 복구 위치에 둔다.
4. 코드 변경은 영구 overlay의 파일과 manifest, 실행 사본, artifact attestation을 일치시킨다. `/opt`만 수정하면 다음 복원에서 사라질 수 있다. 배포용 `sourceRevision`과 실제 파일 해시를 함께 기록한다.
5. 분석 boot·manager·supervisor의 부모 관계에 맞춰 정상 종료·재기동한다. 독립 TTS manager는 별도 관리한다.
6. 기동 뒤 provider/model/revision·readiness·파일 hash를 재확인한다. 개발자의 실제 처리·품질 QA는 별도로 인계한다.

현재 조사에서는 어떤 서비스도 재시작하거나 배포하지 않았다. 이전 Clova INT8·모델 복원 자료는 [과거 안내](README.legacy-clova.md)에 보존한다. 구형 모델 파일의 존재는 현재 사용의 근거가 아니다.
