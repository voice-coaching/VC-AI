# GPT API 지연 개선 계획

상태: **Fast mode 제외. P0/P1 운영 반영 완료, P2/P3 비활성 후보 생성 완료. 성능·품질 관측 대기.**
작성 기준: 2026-10-24, 사용자의 GPT API 시간 50% 이상 단축 목표.

## 목표와 범위

문장연습 direct 경로의 OpenAI 호출 완료 시간을 기존 단일 관측 **7.566초에서
3.783초 이하**로 줄이는 것을 1차 목표로 한다. 이는 보장값이 아니라 채택 목표다.
JSON·rubric·원본 근거 결속과 피드백 품질을 유지하면서 단계별로 한 가지 변경의
효과를 구분한다. 생성 결과 대신 조기 오류나 fallback을 반환한 요청은 개선 성공으로
세지 않는다.

범위는 GPT 요청·응답 표현, private 관측값, 해당 불변 H5와 배포·복구 절차다.
Native/MFA/GOP, 채점 정책, 공개 direct 계약, Backend 내부 handoff·v6 전환,
TTS, 화면은 별도 작업이다. Fast/priority 처리·프로젝트 tier 전환은 사용자 결정으로 제외한다. 새 요청은
`service_tier=default`를 고정한다. GPT 시간만 절반으로 줄이면 다른 시간이 같을 때
전체 24.698초는 약 **20.915초**가 된다. 전체 분석 시간의 절반 단축을 약속하지 않는다.

사용자의 모든 종류 테스트 금지와 화면 검증 금지를 적용한다. 테스트·fixture·mock·
벤치마크 실행기나 관련 설정을 만들거나 실행하지 않으며 삭제된 파일을 복원하지 않는다.
계획 작성 후 사용자가 Fast mode를 제외한 구현·운영 반영을 지시했다. 추가 분석 호출은
실행하지 않는다. 향후 관측은 정상 사용 요청의 계측을 이용하고, 추가 녹음 재실행은 사용자가
명시적으로 요청할 때만 그 범위에서 다룬다.

## 확인한 기준과 미확인 항목

아래는 앞선 작업의 **2026-10-24 12:05 UTC 관측**이며 이번에 서버 상태를
다시 조회한 결과가 아니다. 각 모델 한 번의 실행으로 평균이나 p95를 판단할 수 없다.

| 항목 | 관측 |
|---|---:|
| Astra low의 GPT 포함 응답 생성 전체 | 7.492초 |
| Luna low의 GPT 포함 응답 생성 전체 | 7.796초 |
| Luna OpenAI transport 전체 | 7.566164초 |
| transport 중 요청 검사·객체 구성 | 0.000601초 |
| HTTP 시작부터 응답 헤더 수신 | 7.565204초 |
| 헤더부터 전체 body 수신 | 0.000359초 |
| transport 밖 prepare·파싱·검증·결과 구성 | 0.230259초 |
| 최종 원본 preflight (응답 생성 전체 밖) | 0.249353초 |
| Luna 전체 완료 인지 | 24.698초 |

Luna job은 `1fb3b257-6c17-496b-94da-30225e22471f`, Astra job은
`48e208fd-6433-453d-b67c-4d7166a0aaa1`이다. 같은 PCM
`71dd0b1bd6929f0d921a630725c14f6f047cc27dcb978ac4c38b6d0a08770a1d`와
문장 "쌀과 콩을 깨끗한 그릇에 담아요"를 사용했다. Luna는 ACCEPT·97.2점,
dispatch 1회와 구조 검증 성공이었다. 당시 B2 보관과 AWS 이력 동기화도 완료됐다.

7.565초는 네트워크·OpenAI 대기·추론·생성이 합쳐진 값이다. reasoning token,
실제 출력 token, cached input token, 응답의 실제 service tier를 수집하지 않아
주원인을 아직 분해할 수 없다. body 수신 시간은 모델 출력 생성 시간이 아니다.
3000은 completion token **상한**이며 실제 사용량이 아니다. Astra에는 transport
계측이 없으므로 Luna transport와 Astra 응답 생성 전체를 직접 비교하지 않는다.

### 소스와 브랜치의 관계

계획 브랜치는 `main`의 `ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서
만든 `docs/gpt-api-latency-plan-20261024`다. 기존 작업은 별도 worktree에 보존했다.

- `feat/gpt6-luna-timing-20261024`의 미커밋 소스와 측정 문서를 읽었다. 경로는
  `/home/2020067/intelligentAI-luna`다. 앞선 작업에서 운영 파일과 소스 6개의
  SHA 일치가 확인됐지만 이 계획에서 재검증하지 않았다.
- 이 계획 브랜치의 `main` 기준 코드는 아직 Luna 변경을 포함하지 않는다.
  실행 브랜치 `feat/gpt-api-latency-20261024`에서 필요한 Luna 변경을 통합했다.
  main 파일 전체를 운영에 덮어쓰지 않았다.
- 기존 `feat/unify-analysis-v6-20261019`와 위 Luna 변경은 별도 범위다.
  GPT 최적화에 v6 전환을 섞지 않는다.

## 구현 순서와 단계별 산출물

| 순서 | 변경 | 완료 판단 |
|---|---|---|
| P0 | 관측 보강·현재 Luna 소스 정합 | 실제 token·tier·상태와 시간을 같은 요청에서 구분 가능 |
| P1 | Luna `low → none`만 변경 | 같은 프롬프트·응답 표현에서 속도와 품질 차이 확인 |
| P2 | 기존 compact 응답 후보를 Luna에 연결 | 실제 출력 token 감소, 확장 후 기존 검증 유지 |
| P3 | 고정 prefix·입력 정리와 cache 관측 | cached token 실적과 cache 유무별 지연 구분 |

P0와 P1을 첫 구현 단위로 한다. 목표와 품질 조건을 충족하면 이후 단계는 생략한다.
P2 이후도 이전 단계의 결과를 보고 순서대로 진행하며 여러 변경을 한 번에 켜지 않는다.
단계 완료를 코드 존재·구문 확인·readiness만으로 선언하지 않는다.

### P0 — 관측과 실행 설정을 정합화

[transport](../../src/voice_coach/backend_analysis/canonical_transport.py),
[direct worker](../../src/voice_coach/direct_analysis/worker.py),
[harness identity](../../src/voice_coach/backend_analysis/canonical_harness.py)가 대상이다.
Luna 작업 소스에서는 private `providerTiming`이 이미 있다. 이를 기준으로 다음을
추가하는 계획이며 새 공개 API 필드는 만들지 않는다.

- 요청 model·effort·요청한 tier와 provider가 반환한 model·실제 tier.
- 지원되는 usage의 prompt/completion/reasoning/cached token 수, 요청·응답 byte 수.
  누락된 값은 null/미제공으로 두고 0으로 꾸미지 않는다. reasoning token 수를
  reasoning 경과 시간으로 환산하지 않는다.
- 단조 시계 기반 transport 전체·헤더 대기·body 수신과 기존 expression/preflight 시간.
- 성공 여부, generationStatus, dispatch 횟수, 닫힌 오류 사유. 실패도 분모와
  상태 집계에 남기고 성공 요청에서만 시간 통계를 산출한다.

계측 파싱은 기존 strict JSON 파서와 필드 allowlist를 사용한다. 중복 키·비정상 수치를
허용하거나 선택적 usage 누락 때문에 정상 생성 결과를 실패시키지 않는다.
prompt·음성·생성 원문·인증 헤더·키는 새 계측 로그에 넣지 않는다.
기존 private worker 영속 자료와 정상 요청의 요약만 사용한다.

### P1 — Luna none을 단독 적용

모델은 `gpt-6-luna`, Chat Completions, 동일 prompt/rubric·3000 token 상한·
json_object·store=false·timeout 120초·추가 재시도 없음으로 유지하고 effort만
`none`으로 바꾼 새 불변 H5를 만든다. Luna의 none 지원은
[OpenAI 공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)에서 확인했다.
추론 감소가 실제 몇 초를 줄일지는 아직 확인하지 않았다.

Luna transport도 `reasoning_effort="low"`를 고정 검사하고 요청 필드 집합의
완전 일치를 요구한다. 환경변수만 바꾸거나 검사를 느슨하게 하지 않는다.
서버가 등록한 release identity가 model·effort·token 상한·tier를 결정하도록
요청 계약을 연결한다. 기존 identity의 low 동작은 보존하고 미등록 조합은 거부한다.
새 manifest·lock과 실행 tuple·private preflight 등록도 함께 갱신한다.

기존 Luna builder는 Astra 부모에서 model만 바꾼다. 새 effort builder는 검증된
Luna 부모에서 파생하고 기존 디렉터리를 수정하지 않는다. 실제 응답 모델 검사,
schema·채점 검증과 원본 association도 유지한다. none 도입 때문에 점수 정의나
fallback 기준을 바꾸지 않는다.

### P2 — 생성해야 할 출력 token을 줄임

다른 작업 디렉터리에 미추적 상태의 `deploy/dlpc/build_compact_h5.py`와
`deploy/runpod/harnesses/compact_gop_output*` 후보가 있었다. production 템플릿 3개를 실행 브랜치에 통합하고 원래 worktree는
보존했다. 기존 테스트 파일은 복사·재사용·복원하지 않았다.

후보는 GPT가 `levels`, `score`, `feedback`만 반환하고 서버가 rubric revision과
9개 criterion ID를 복원하는 구조다. GPT가 선택한 level과 score를 보존하며 서버가
새로 채점하지 않는다. UNSCORABLE/null, level 순서·개수·타입, 점수 유효 범위와
확장 후 원래 schema·의미 검증을 유지하는지 먼저 소스와 명세를 대조한다.

후보 부모는 기존 Astra manifest에 고정돼 있다. 그대로 사용하면 현재 Luna/none
선택이 보존되지 않아 [단계별 builder](../../deploy/dlpc/build_gpt_latency_h5.py)에서
P1 부모를 고정해 compact 후보를 생성했다. 코어와 입력
근거는 그대로 두고 내부 출력 표현과 이를 해석하는 adapter·prompt만 함께 버전 관리한다.
공개 direct 응답 DTO에는 compact 배열을 노출하지 않는다.

먼저 반복 JSON metadata를 줄이고 실제 출력 token 감소를 확인한다. 피드백 지도 내용을
임의 삭제하거나 상한을 과도하게 낮춰 length 종료를 유도하지 않는다.
[OpenAI 지연 가이드](https://developers.openai.com/api/docs/guides/latency-optimization)는
실제 출력 token 감소를 주요 수단으로 제시한다. 상한만 낮춰 절반 단축을 기대하지 않는다.

### P3 — 입력과 고정 prefix를 필요한 만큼 정리

P0에서 prompt 처리 비중과 cached token 실적을 보고 착수한다. system 지침·rubric
같은 공통 내용을 고정 prefix에 두고 script·CORE 근거 등 요청별 자료를 뒤에 둔다.
직렬화 순서·불필요한 공백·중복 지침 정리는 새 prompt 계약으로 다룬다. 원본 CORE,
음소 index·시간·digest·채점에 필요한 관측은 보존한다.

응답 재사용 cache를 만들지 않는다. provider prompt cache는 매번 같은 시도의 새
생성을 수행하면서 공통 prefix 계산을 재사용하는 기능으로 구분한다. cache hit은
사용량 필드로 확인하고 cold 요청과 warm 요청을 섞어 성능을 과장하지 않는다.
저트래픽에서는 cache hit이 없을 수 있다. 계측 없이 cache 옵션을 추가하지 않는다.
[OpenAI prompt caching 문서](https://developers.openai.com/api/docs/guides/prompt-caching)를
구현 시 다시 대조하고 보관·요금 조건을 확인한다.

## 채택 기준과 관측 방법

추가 실행을 만들지 않고 승인된 운영 변경 후 자연 발생 요청의 private 요약을
이용한다. 같은 음성 길이·script/출력 token 규모·부하·cache 상태의 구간을 비교하고
낮은 부하에서의 단일 결과를 p95나 보장값으로 보고하지 않는다.

| 기준 | 제안 완료 조건 |
|---|---|
| API 절대 목표 | 정상 생성 transport p50 ≤ 3.783초 |
| 50% 개선 확인 | 비교 가능한 동시기 기준 구간 대비 transport p50 ≤ 50% |
| 꼬리 지연 | 같은 조건의 p95가 기준 구간보다 악화하지 않음 |
| 표본 한계 | 기준·후속 각각 최소 30개 정상 요청을 초기 판단에 사용하고, 표본 수·기간·범위와 작은 표본의 p95 한계를 함께 표시 |
| 정상 동작 | schema/의미 검증 실패·길이 종료·fallback·재시도 증가를 속도 개선으로 인정하지 않음 |
| 품질 | 개발자가 정상 결과의 rubric 준수·근거 결속·한국어 지도 적절성을 판단. 97.2점 재현 자체를 합격 기준으로 쓰지 않음 |
| 비용 | 실제 입력/출력 token·tier별 비용을 함께 보고하고 기본 처리 요율과 cache write/read 증가분 명시 |
| 전체 지연 | expression·preflight·전체 결과 공개 시간을 별도 보고하고 MFA 차이를 GPT 개선으로 귀속하지 않음 |

현재 7.566초는 단일 참고값이다. 동시기 기준 표본이 없으면 절대 목표 충족 여부만
판단하고 통계적인 50% 개선 완료로 표현하지 않는다. 자연 요청이 충분하지 않으면
관측 미완료로 남긴다. 승인되지 않은 반복 호출로 표본을 채우지 않는다.

## 배포·복구와 인계

1. 구현 시작 시 실제 운영 identity·파일 SHA·진행 작업·기존 hold를 확인한다.
   미병합 Luna와 v6/compact 작업의 경계를 정리하고 필요한 소스만 통합한다.
2. 변경 단계마다 새 H5·lock·manifest와 닫힌 서버 실행/검증 tuple을 묶어 준비한다.
   direct의 선택·모델 readiness·private 진단은 선택된 identity에서 읽는다.
3. direct 요청만 잠시 닫고 활성 요청이 빠진 것을 확인한 뒤 분석만 전환한다.
   TTS·Backend 내부 admission과 기존 독립 운영 설정은 보존한다.
4. 적용 전후 SHA·설정 선택·복구 자산을 기록하고 지속 recovery helper도 새 pins에
   맞춘다. 프로세스와 공개 readiness, 정상 결과·보관·이력 동기화를 따로 관측한다.
5. 요청 불일치·실제 모델/tier 불일치·검증 실패·품질 악화 시 해당 단계를 채택하지 않는다.
   진행 작업을 drain한 뒤 마지막 채택 release의 **runtime 소스·preflight pins·LLM root·
   recovery 활성 선택을 함께** 되돌린다. env만 되돌려 자동 복구가 다시 켜는 상태를 피한다.
6. timeout·응답 유실에 GPT를 재실행하거나 다른 모델로 자동 전환하지 않는다.
   같은 execution 상태와 현재 실패 계약으로 처리한다.

연결 풀·Responses/WebSocket·스트리밍·다중 GPT 병렬 호출은 첫 구현에서 제외한다.
현재 direct worker는 요청마다 새 프로세스에서 GPT를 한 번 호출하므로 같은 worker의
keep-alive만으로 다음 job 연결을 재사용할 수 없다. 공유 client 수명 변경은 별도 설계다.
스트리밍은 부분 JSON을 최종 결과로 공개할 수 없어 완료 시간 절반 단축 수단으로
가정하지 않는다. prepare·검증 전체가 약 0.23초인 상황에서 이 검사를 없애는 작업도
우선하지 않는다.

계획 작성 시에는 문서만 수정했다. 이후 사용자가 Fast mode를 제외한 수행을 지시했다.
모든 종류의 테스트와 추가 분석 호출은 실행하지 않고 구문·무결성과 운영 상태를 확인한다.
진행 결과와 다음 단계의 채택 조건은 [구현·운영 기록](gpt-api-latency-implementation-20261024.md)을 따른다.
