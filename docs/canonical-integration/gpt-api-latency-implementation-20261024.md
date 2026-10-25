# GPT API 지연 개선 구현·운영 반영

관측: **2026-10-24 14:06 UTC**, DLPC J406.
브랜치: `feat/gpt-api-latency-20261024`, local main `ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 생성.

## 결과

문장연습 direct 운영 경로에 **GPT-6 Luna / reasoning_effort=none /
service_tier=default**와 private provider 관측을 적용했다. 추가 분석 요청은
제출하지 않았고, **속도 50% 개선과 피드백 품질 유지는 아직 확인되지 않았다.**
기존 Luna low 한 건의 transport 7.566초만 있어 통계적인 비교 기준도 부족하다.

Fast mode는 사용자의 결정으로 계획·builder·선택 가능한 요청 조합에서 제외했다.
default를 명시해 프로젝트의 auto tier를 따르지 않는다. 응답이 fast/priority를
보고하면 새 요청 경로는 닫힌 오류로 처리하며 추가 호출·다른 모델 전환을 하지 않는다.
필드 의미는 [OpenAI Chat Completions 공식 문서](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)를 따랐다.

| 단계 | 코드·자산 | 운영 선택 |
|---|---|---|
| P0 계측 | 모델/effort/tier·token·시간·상태와 읽기 전용 집계 구현 | 적용 |
| P1 none | 부모 H5에서 none/default 파생, prompt·rubric·코어 보존 | 적용 |
| P2 compact | 고정 9개 level·score·feedback과 원래 형식으로 확장하는 후보 생성 | 비활성, 실행 승인 tuple 미등록 |
| P3 cache | 공통 지침·rubric 뒤 명시적 cache 경계, 사용자 자료는 그 뒤에 배치한 후보 생성 | 비활성, 실행 승인 tuple 미등록 |

P2/P3를 바로 켜지 않은 이유는 [계획](gpt-api-latency-plan-20261024.md)의
단계별 관측·품질 조건이다. 먼저 P1 정상 사용 요청을 관측한다. 목표를 만족하면
후속 단계를 적용하지 않는다. 판단에 필요한 표본이나 품질 근거가 없으면 미완료로
남기며, 테스트·임의 반복 호출로 자료를 채우지 않는다.

## 구현 경계

### 요청과 계측

[canonical_transport.py](../../src/voice_coach/backend_analysis/canonical_transport.py)의
`for_harness()`가 서버가 등록한 identity에서 model·effort·token 상한·tier·cache
설정을 선택한다. 직렬화된 요청의 필드 집합과 값이 완전히 일치해야 한다.
기존 low identity·생성자 경로의 요청 형태는 보존했다. 임의 env 기반 모델/effort
전환이나 frozen 검사를 느슨하게 하는 경로를 추가하지 않았다.

[canonical_provider_observation.py](../../src/voice_coach/backend_analysis/canonical_provider_observation.py)는
엄격한 기존 JSON 파서로 응답의 허용된 metadata만 읽는다. prompt/completion,
reasoning/text, cached/cache-write token과 실제 모델·tier·finish reason을 기록하며
없는 값은 null이다. 추론 token 수를 초로 환산하지 않는다. 지원되지 않는 optional
metadata는 원래 completion 검증 결과를 바꾸지 않는다.

transport는 요청 검사·헤더 대기·body 수신·전체 시간, 요청·응답 byte 수,
HTTP 상태와 닫힌 provider 오류를 남긴다. status는 NOT_DISPATCHED,
DISPATCHING, RESPONSE_RECEIVED, FAILED로 구분한다. RESPONSE_RECEIVED는
schema·채점 검증 성공을 의미하지 않는다. 기존 frozen adapter와 최종 preflight가
실제 생성 결과를 검증한다.

[direct worker](../../src/voice_coach/direct_analysis/worker.py)는 private spool의
`provider-observation.json`에 관측을 보존하고, 완료 자료에 providerTiming과
generationStatus를 연결한다. 관측 파일 I/O 실패는 정상 생성을 실패로 바꾸지
않으며 완료 자료에 persistence 실패를 명시한다. preflight 이전에 보존하므로
나중 단계가 실패해도 이미 받은 provider 관측을 확인할 수 있다. 프로세스 강제 종료
시 관측 파일이 없는 경우까지 수집 성공으로 표현하지 않는다.

새 계측은 키·인증 헤더·prompt·음성·생성 원문을 기록하지 않는다.
공개 direct DTO에는 새 필드를 추가하지 않았다. callback/archive 재전송은 계속
추론/GPT 재실행과 분리된다.

### 비활성 후보

[build_gpt_latency_h5.py](../../deploy/dlpc/build_gpt_latency_h5.py)는 none→compact→cached를
서로 다른 불변 디렉터리에 생성한다. 각 부모의 manifest·lock·CORE hash를 고정하고
파일 전체 해시와 Python 구문을 확인한다. 기존 후보의 production 템플릿을 재사용하고
다른 작업 디렉터리의 원본과 테스트 파일은 수정하지 않았다.

compact는 GPT가 준 점수·등급을 유지하고 고정 criterion ID·rubric revision만
복원한다. 기존 UNSCORABLE/null 처리와 확장 후 전체 schema·scoring 검증을 유지하며
서버가 점수를 계산·보정하지 않는다. 공개 schema와 scoring rubric 원본은 동일하다.

cached는 같은 compact prompt와 rubric을 system의 고정 prefix에 배치하고
rubric 뒤에 `prompt_cache_breakpoint={mode:explicit}`를 둔다. 요청별 script와
전체 phonemeAssessment/native 근거는 user 메시지로 그대로 전달한다. 설정은
`prompt_cache_options={mode:explicit,ttl:30m}`다. 입력 text byte 한도를 유지한다.
응답 cache를 만들거나 다른 시도의 결과를 재사용하지 않는다. 실제 cache hit·비용·
효과는 미확인이다. [공식 cache 문서](https://developers.openai.com/api/docs/guides/prompt-caching)와
[Chat Completions의 content-part/cache 필드](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)를 확인했다.

## 운영 반영과 복구

1. 대상 host와 기존 Luna low root·manifest·activeJobs/directJobs=0,
   기존 hold 없음과 public direct 준비 상태를 확인했다.
2. 고정 SHA의 runtime 7개와 P1 H5를 persistent recovery payload에 준비했다.
   복구 lock 아래 기존 hook을 보존하면서 새 선택 분기를 설치했다.
3. 소유 marker로 direct admission을 닫고 활성 작업 0을 두 차례 확인했다.
4. 분석만 정지하고 runtime·private preflight schema·direct LLM root를 함께
   변경했다. 분석을 시작한 뒤 워밍을 기다렸다. TTS는 재시작하지 않았다.
5. local·공개 direct readiness 200/ready=true 확인 후 자신의 hold를 해제했다.
   마지막 관측의 activeJobs/directJobs는 0, hold는 없었다.

현재 선택은 `/workspace/intelligentai/validation/canonical_dlpc_gop_luna_none_20261024_v1_h5`다.
`AI_CANONICAL_LLM_ROOT`와 내부 Backend handoff admission은 보존했다.
내부 handoff admission=false는 전환 전부터 있던 상태이며 새로 열지 않았다.
전체 v6를 배포하지 않았다. direct 준비 상태를 내부 연동·실제 GPT 생성·품질
확인 완료로 해석하지 않는다.

기존 source revision `791ca5496e04a10b6948c69dce97361ee4335f4c`와 bundle
`d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`는 유지하고
`deployment.json`의 `operationalPatches.gpt-latency-20261024`에 별도 파일 hash와
활성 stage·모델·effort·tier를 기록했다. 이전 Luna 변경도 이번 브랜치에 통합했다.

[restore_gpt_latency.py](../../deploy/dlpc/restore_gpt_latency.py)는 알려진 main/Luna
기준 또는 이미 설치된 SHA만 허용하고 파일 변경 시 분석 정지를 요구한다.
root 전용 rollback snapshot은 소스와 이전 direct root만 담는다. 키가 포함된 env를
payload나 백업으로 복사하지 않고 실제 변경 시 기존 private parser로 현재 값을
읽어 비밀값을 보존한다. 기존 readonly H5는 수정하지 않는다.

persistent recovery의 `gpt-latency-20261024/selected`가 `none`일 때 새 helper를
적용한다. 명시적 `--rollback`은 분석이 정지한 상태에서 snapshot의 runtime과 이전
direct root를 복구하고 metadata의 이번 패치 항목을 제거하며 selector를 disabled로
바꾼다. 이후 recovery는 이전 Luna helper를 사용한다. env만 되돌려 다음 복구가
다시 none을 켜는 상태를 피한다. rollback 자체와 전체 cold 복구는 실행하지 않았다.

## 운영 관측 집계

[summarize_gpt_latency.py](../../deploy/dlpc/summarize_gpt_latency.py)는 기존 direct spool을
읽기만 한다. 요청 전송·job 생성·재실행·fixture 생성 기능은 없다.

```sh
sudo -n /home/2020067/.local/share/intelligentai/python/cpython-3.12.3/bin/python3.12 -B \
  /home/2020067/intelligentAI-gpt-speed/deploy/dlpc/summarize_gpt_latency.py \
  --since 1792839600
```

manifest·요청 모델·effort·실제 tier·cache hit/miss/unknown·16 KiB 요청 크기 구간으로
cohort를 나눈다. 성공 요청의 transport p50/p95와 expression p50을 계산하고,
fallback·length·provider 실패는 별도 분모에 남긴다. token 수는 관측 개수와 합계를
같이 표시하며 누락을 0으로 채우지 않는다. 실제 요금은 이 usage와 해당 시점의
기본/cache 단가로 계산해야 하며 이 집계가 청구 금액을 확정하지 않는다.

반영 전 읽기 집계의 결과는 Astra 정상 1건(transport 미계측), Luna low 정상 1건
(transport 7.566초), pending 0건, unreadable 0건이었다. token·tier는 모두 미제공이다.
마지막 운영 확인 시 P1 실제 생성은 아직 없었다. 최소 30건 기준은 초기 판단 조건이며,
동시기 low 비교 자료가 없으면 절대 목표만 판단하고 상대 50% 개선을 확정하지 않는다.
집계는 `relativeImprovementConfirmed=false`를 유지해 자동으로 개선 완료를 선언하지 않는다.

## 실제 확인 결과와 변경 파일

| 확인 | 결과 |
|---|---|
| 변경 production Python 구문 | 14개, 메모리 내 compile 통과 |
| 변경 JSON 파싱 | 2개 통과 |
| private preflight JSON Schema 구조 | 배포된 HTTP 의존성 경로로 컴파일 통과 |
| H5 lock·파일 SHA·Python 구문 | low 기준과 none/compact/cached 3개 후보 모두 일치 |
| 코어·rubric·확장 후 출력 schema | 보존 확인 |
| 활성 runtime SHA와 브랜치 소스 | 7개 일치 |
| persistent helper 2개·recovery hook | helper 2개는 브랜치와 일치, hook은 구문 통과 |
| local·공개 direct readiness | 200, ready=true |
| 추가 GPT/분석 호출·모든 종류 테스트·화면 검증 | 실행·생성하지 않음 |

schema 구조 확인 시 scorer 경로만으로는 jsonschema import가 실패해 실제 서비스의
`/opt/intelligentai/http-python` 경로를 적용한 뒤 통과했다. 의존성을 설치하거나
설정을 추가하지 않았다. 준비 상태 조회·정적 컴파일을 실제 생성·품질 QA로 보고하지 않는다.

변경 파일은 다음과 같다. 원래 작업 디렉터리와 다른 브랜치의 변경은 보존했다.

- runtime: `canonical_harness.py`, `native_execution.py`, `canonical_transport.py`,
  `canonical_provider_observation.py`, `direct_analysis/service.py`, `direct_analysis/worker.py`.
- private schema: `docs/contracts/runpod_result_v4.schema.json`.
- 배포·관측: `deploy/dlpc/build_gpt_latency_h5.py`, `restore_gpt_latency.py`,
  `summarize_gpt_latency.py`, `recover_on_start.py`.
- 기존 Luna 배포 정합: `deploy/dlpc/build_luna_h5.py`, `restore_luna_model.py`,
  `restore_direct_resident.py`.
- compact production 템플릿: `deploy/runpod/harnesses/compact_gop_output.py`,
  `compact_gop_output.schema.json`, `compact_gop_output_prompt.txt`.
- 문서: `docs/README.md`, 계획, 이 구현 기록과 기존 Luna 측정 기록.

일회성 검증 스크립트·샘플 파일은 만들지 않았다. 운영 H5·spool·복구 payload는
정식 운영 자산으로 유지한다. commit/push/PR은 수행하지 않았다.

남은 작업은 자연 요청의 시간/token·실패·비용 관측, 개발자의 피드백 품질 판단,
그 결과에 따른 P2와 P3 채택 여부다. 후보를 운영에 채택할 때는 read-only 등록과
실행 허용을 구분하고 새 runtime·schema pins·복구 선택을 함께 갱신해야 한다.
