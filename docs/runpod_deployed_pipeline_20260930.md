# RunPod 실제 배포 파이프라인 — 2026-09-30 KST

[문서 목차](README.md) · [운영 배포 안내](../deploy/runpod/README.md) · [입술 분석](video-lip-analysis/README.md)

> 아래 최초 관측은 00:20~00:31 KST의 스냅샷이다. 이후 TTS 실행 환경·한국어 전용 frontend를 복원하고 8082 통합 API를 외부에 공개했다. 현재 공개 경로와 자동 복원 확인은 [후속 배포 결과](runpod_api_gateway_20260930.md)를 따른다.

## 현재 상태와 범위

**현재 운영은 HTTP 분석 API + Seungun CUDA + 영상 입술 관찰 + 외부 GPT-6 API다. Clova 추론 서버·채점기는 운영 조립 경로에서 폐기됐다.**

관측은 **2026-09-30 00:20:12~00:21:33 KST** / **2026-09-29 15:20:12~15:21:33 UTC**다. 관리 API와 SSH로 실행 상태·지정 설정·배포 파일·자산 hash·비생성 health·배포 상태 파일·제한된 로그 이벤트를 읽었다. 원격 코드는 변경하지 않았다.

| 항목 | 관측 |
|---|---|
| Pod | `s8hb5b0l5k4qlx`, `intelligentai-a40-int8-120gb-20260915`, RUNNING |
| GPU | NVIDIA A40, 46,068 MiB 중 1,479 MiB 사용 스냅샷 |
| 실행 root | `/opt/intelligentai/app` |
| sourceRevision / workerRevision | `5ea4712e55f231450b2120c7121dfaa3ade1ed6f` |
| baseRelease | `15534800bcc3f730d6fffeb9aa98d3ee91d3ab18563b0dc5f0fad4dff6eb2887` |
| overlaySha256 metadata | `f3b40b726959b9df69762469b0d61a4c7682d997fd1f259886678f996e5e5ba4` |
| supervisor | `api`만 등록·ready, API PID 1911, supervisor PID 1910 |
| `/health`, `/readyz` | 모두 HTTP 200, 각각 alive/ready |
| 인증 `/health/services` | HTTP 200, executorReady=true, reasonCode=null, OPENAI / gpt-6-astra |
| 분석 listener | `0.0.0.0:8080`; 과거 모델 포트 `:8000` 없음 |
| 독립 TTS | `:8082`, `/opt/voice-coaching-tts/venv`, `/run/voice-coaching-tts` 없음 |
| 로컬 조사 HEAD | `c3a247bb79805ed847ac03b420ca03b42e31631e`, main, 시작 시 clean |

Pod 이름과 영구 `int8-overlay` 경로의 INT8은 과거 이름이다. GPT가 Pod에서 INT8로 실행된다는 뜻이 아니다. GPU 사용량도 특정 모델만의 메모리 점유량으로 해석하지 않는다.

## 기동과 복원

프로세스와 코드를 대조한 경로는 boot → manager → supervisor → HTTP 분석 및 Seungun 자식이다. command line 전체·프로세스 환경 원문은 기록하지 않았다.

1. `boot.sh`가 SSH를 유지하고 migration marker를 기다린 뒤 시스템 의존성과 UID/GID 10001을 준비한다.
2. `prepare_runtime.py`가 `/workspace/intelligentai/current`의 base release, 영구 overlay manifest, runtime-assets를 검증하여 `/opt/intelligentai/app`을 구성한다.
3. 복사하는 Python 환경은 api/g2pk/scorer다. HTTP 의존성은 고정 wheel cache와 lock으로 준비한다. 현재 준비 코드에는 Clova 환경 복사·모델 적재가 없다.
4. 기본 전송은 runpod_http다. `prepare_http.py`가 private source·자산을 검사해 `/run/intelligentai/analysis-http.env`와 HTTP attestation을 만든다.
5. manager가 `services.http.json`을 선택한다. api는 UID/GID 10001, scorer Python, dependsOn=[]로 실행된다.

일부 코드에 redis_stream/disabled 분기가 남아 있지만 현재 실행·지원이 확인된 대안으로 안내하지 않는다. 옛 파일이나 주석의 존재는 운영 증거가 아니다.

## 요청 처리의 전체 흐름

| 순서 | 처리 | 경계 |
|---|---|---|
| 1 | Backend가 대본·음성 객체와 선택 영상으로 HTTP 접수 | 로그인·녹음 소유 관계는 Backend 책임 |
| 2 | Bearer 인증, schema·request/execution·payload digest 검사 | Redis grant·서명을 만들지 않음 |
| 3 | 단일 슬롯 예약, Backend claim 후 executor 전달 | 겹치는 새 작업은 429 CAPACITY_EXCEEDED |
| 4 | 제한 저장소에서 metadata·크기·SHA 검증 후 media materialize | 연구자료 B2와 사용자 녹음 저장소를 구분 |
| 5 | 대본 → offline g2pK, 음성 → ffmpeg 16 kHz mono PCM/WAV | warm isolated Seungun CUDA 1회 분석 |
| 6 | 전체 음소를 ACTIONABLE/REVIEW_ONLY/OBSERVED로 분류 | 원본 selected phone 보존; 반복 패턴·첫 등장으로 최대 3후보 |
| 7 | 같은 후보의 영상 구간 관찰 | PCM/SHA/index 결속, QC·sync·dense window·MediaPipe/P6 |
| 8 | prompt + 두 전문가 사례 전체 + currentAttempt | byte 기반 입력 예산에 따라 후보 축소 |
| 9 | 외부 gpt-6-astra 호출·로컬 응답 검증 | 최대 1회, 후보가 없으면 미호출 |
| 10 | 근거 연결 결과·짧은 총평 또는 deterministic fallback | 임시 미디어 정리, 유효 lease 내 result callback |

heartbeat·lease·deadline·취소가 작업 수명을 제한한다. callback 일시 오류는 유효 lease 내에서 재전송한다. **취소 후 180초 drain 대기는 현재 코드에도 남아 있다.** 과거 모델 관련 주석이 남은 동작이며 즉시 슬롯 반환이라고 문서화하지 않는다. endpoint는 [API 명세](api/specification.md)를 따른다.

## GPT API와 현재 설정

배포 `coaching_generator.py`는 provider OPENAI, model gpt-6-astra, endpoint `https://api.openai.com/v1/chat/completions`를 제한한다. GPT는 Pod 내부 모델이 아닌 외부 API다.

| 설정/옵션 | 관측값 |
|---|---|
| AI_ANALYSIS_COACHING_ENABLED | true |
| AI_ANALYSIS_COACHING_RESULT_CONTRACT | v3 |
| AI_ANALYSIS_COACHING_PROVIDER | OPENAI |
| AI_ANALYSIS_COACHING_OPENAI_MODEL | gpt-6-astra |
| OPENAI_API_KEY | 존재만 확인, 비공개 |
| AI_ANALYSIS_CLOVA_* | 생성된 실행 env에 해당 이름 없음 |
| 생성 요청 | reasoning_effort=low, response_format={type:json_object}, store=false |
| 기본 한도 | timeout 120초, max_completion_tokens=1200, 현재 시도 입력 예산 16000 |

한도 세 변수는 runtime env에서 미설정이므로 loader 기본값을 적용한다. 전문가 사례 예약분을 별도로 더한다. 입력 예산 계산은 UTF-8 기반 보수적 상한이며 실제 token 수가 아니다. 현재 **strict JSON Schema 출력은 아직 아니다**. [GPT 개선 문서](video-lip-analysis/gpt6-integration-design.md)는 미구현 제안이다.

서버는 응답 128 KiB 한도, JSON 중복 키·비유한 값, 정상 종료·refusal, 모델 identity, 후보/guidance ID·coverage, 텍스트 제약을 검사한다. 실패하면 같은 허용 후보의 일반 목표음 연습으로 fallback하고 사유를 남긴다. 다른 모델이나 로컬 LLM으로 자동 전환하지 않는다.

## prompt·전문가 사례·결과

활성 prompt는 `configs/pronunciation_feedback/prompts/system.md`와 `coaching/active.md`가 가리키는 `pronunciation-coaching-v1.md`다. `expert_examples.py`의 사례 사용 규칙을 덧붙인다.

원격 prompt와 coaching.py에 **총평 최대 5문장, 후보별 action 한 문장·UTF-16 140 이하, 일반 총평 1~3문장**이 적용돼 있다. 서버가 action만 줄바꿈으로 묶는다. observation/practice/selfCheck를 총평에 다시 붙이지 않는다. 이는 9월 29일 로컬 구현 기록보다 최신의 배포 파일 확인이며 실제 문장 품질 검증은 아니다.

전문가 bank는 `announcer-paired-examples-20260928-v1`, testvideo1/testvideo2, ALL_CASES_EVERY_GENERATION이다. 파일 393,116 bytes와 manifest SHA 일치를 확인했다. 과거 사례를 현재 관측·교정 정답으로 복사하지 않는다. 로드 로그의 input_budget=410821 역시 실제 token 수가 아니다.

완료 코칭은 `voice-coaching.runpod-analysis-result.v3`, 내부는 `voice-coaching.coaching-result.v1`이다. overallScore/scoringEvidence는 null, strengths는 [], comparison은 null이다. 코칭 전 실패에는 legacy v1 직렬화 경로가 남는다. [계약 안내](contracts/README.md)에서 현재 writer와 과거 reader 정의를 구분한다.

## 영상과 설계 제안의 경계

영상 adapter, composition, facade, coaching_evidence의 hash는 9월 29일 조사와 같았다. 활성 production_visual_v2는 enabled이고 세 하위 자산의 파일 hash가 manifest와 일치했다.

- `analyze_coaching_observations` → `run_http_observations`; 최대 3후보의 대표 구간만 관찰한다.
- `_PrecomputedSeungun`은 같은 PCM 분석을 재사용한다. 기본 중심 프레임과 closed-beta 40점/24점 형상은 집계 의미가 다르다.
- aggregate는 coaching.visual, 최상위 visualSupplement는 null이다.
- sampleCount=null, referenceStatus=NOT_VALIDATED, correctiveClaimsAllowed=false다. 개인 입 벌림 교정이나 점수로 해석하지 않는다.
- 새 observation ID·유효 표본 수·statement allowlist·strict schema·검증된 개인 교정은 [계획](video-lip-analysis/implementation-plan.md)의 미구현 항목이다.

## readiness·로그·독립 TTS

readiness는 executor, Backend worker-readiness의 HTTP 계약, OpenAI 모델 접근, 저장소를 검사한다. 인증된 `GET /v1/models/gpt-6-astra` 성공은 300초 캐시한다. 이는 실제 생성·품질 검사가 아니다. supervisor 자체 health는 alive이므로 업무 준비는 별도 /readyz와 인증 /health/services로 확인한다.

analysis-http.log는 파일별 10 MiB와 이전 3개 회전본으로 구성된다. 현재 파일의 제한된 최근 줄 조사에는 4줄, 전문가 사례 로드·ready 이벤트만 있었다. 생성 완료 이벤트가 표본에 없다고 전체 기간의 호출·오류가 없었다고 결론내리지 않는다. 회전 로그·Backend 로그 전체 감사는 하지 않았다.

독립 TTS의 `/workspace/voice-coaching-tts/service/{server.py,manager.py}`는 존재하나 실행 venv·runtime 디렉터리·8082 listener가 없다. 이것은 TTS가 서비스에서 폐기됐다는 뜻이 아니다. 아래 추가 조사에서 **AWS 백엔드의 RunPod TTS 연결 설정은 유지되지만 현재 새 합성 연결은 불가**한 것으로 확인했다. 9월 28일 health 200을 현재 상태로 재사용하지 않는다.

### 사용자 지적 후 AWS 교차 확인 — 2026-09-30 00:29~00:31 KST

처음 조사는 Pod만 확인했으며 AWS 연결 여부를 판단할 근거가 부족했다. 이후 AWS와 RunPod를 읽기 전용으로 대조했다.

| 경계 | 현재 관측 |
|---|---|
| AWS backend/nginx | alpha-backend, nginx 모두 active |
| 실행 Backend 환경 | EXAMPLE_TTS_PROVIDER=runpod, EXAMPLE_TTS_WORKERENABLED=true, EXAMPLE_TTS_BASEURL=https://ai.voice-coaching.site, revision=melo-ko-practice-r1; 토큰 존재만 확인 |
| nginx | exact /v1/tts/synthesize → http://127.0.0.1:18082 |
| AWS SSH 터널 | voice-coaching-tts-tunnel: activating/auto-restart, 관측 NRestarts=4725; 최근 60줄에 Connection refused 10건 |
| 대상 불일치 | 터널은 RunPod 호스트의 SSH 22176 사용, 현재 관리 API는 같은 호스트의 SSH 22069 반환 |
| TCP 재확인 | AWS 127.0.0.1:18082와 RunPod 127.0.0.1:8082 모두 연결 거부 |
| RunPod 실행 자산 | 영구 server.py/manager.py는 존재, /opt/voice-coaching-tts/venv 및 /run/voice-coaching-tts 없음 |

AWS 실행 프로세스의 JAR 인자는 `/opt/alpha/app.jar`, 조사 당시 symlink 대상은 `/opt/alpha/releases/b48db988e7485943097acaa4c71308ef306175a8.jar`였다. 이는 경로 관측이며 파일명만으로 실행 Git provenance를 확정하지 않는다. 그 JAR의 bytecode에서 `ExampleAudioService.audio`의 runpod 분기는 `ExampleTtsStore.playable` 결과를 반환하고 없으면 오류를 낸다. 재생 요청마다 RunPod 합성을 직접 실행하는 분기가 아니다. `ExampleTtsWorker`는 작업을 claim하고 저장된 생성 캐시가 없을 때 generator를 호출한 뒤 stage/store/complete한다.

따라서 **TTS 연결·사용 설계**, **저장된 음성 재생**, **신규 합성 가능 여부**를 구분해야 한다. 기존 음성 재생이 가능하더라도 신규 합성 경로가 정상이라는 증거는 아니다. 이번에는 실제 사용자 재생·DB 캐시 내용·S3 접근·새 합성을 시험하지 않았다. nginx access 최근 3000줄에는 합성 POST가 없었지만 전체 호출 이력 부재를 뜻하지 않는다.

관측된 문제는 오래된 SSH 포트 설정과 Pod TTS 실행 환경 부재다. 포트만 수정해 전체 복구가 끝난다고 단정할 수 없다. host key·전용 SSH 인증·TTS 런타임·양쪽 인증 health를 함께 확인할 별도 복구 작업이 필요하다. 이번 조사에서는 터널·설정·프로세스를 변경하지 않았다.

## 자동 배포 상태와 기록 불일치

Pod `/workspace/intelligentai/deployment/github-actions/state.json`의 gitRevision/runtimeRevision은 모두 위 5ea4712e…이며 overlay hash도 deployment metadata와 일치했다. pending.json은 없었다.

[복원 이력](runpod_private_settings_recovery_20260929.md)은 a734f626…의 성공을 기록하지만 이번 실제 상태와 다르다. GitHub 실행 이력·변경 원인은 이번에 검증하지 않았으므로 rollback·배포 실패를 추정하지 않는다. 로컬 main HEAD도 Pod와 다르다. 로컬 push나 문서 변경은 배포 완료가 아니다.

현재 checkout workflow에는 main push/수동 실행이 정의되어 있지만 check_only 입력은 없다. 이전 기록의 모드가 모든 revision에서 존재한다고 안내하지 않는다. [Actions 안내](../deploy/runpod/GITHUB_ACTIONS.md)를 참고한다.

## 파일 근거와 미확인 범위

아래 `/opt/intelligentai/app` 상대 파일은 영구 overlay와 바이트 일치를 확인했다. sourceRevision만으로 전체 운영 디스크와 Git의 동일성을 주장하지 않는다.

| 파일 | SHA-256 |
|---|---|
| src/voice_coach/backend_analysis/production.py | `37328367f51928b12af17a4085b021d2952f94f761e4951c17256449766e9278` |
| src/voice_coach/backend_analysis/service.py | `7d9e0e5bd5623844e80ab6643e1f25bf1ad781a80c08f76c12751eda1d8111df` |
| src/voice_coach/backend_analysis/coaching_generator.py | `160a75c0271a12eb9e2ab6ad15d8ff0db2b67b5308515c48d099ebda08f4a860` |
| src/voice_coach/backend_analysis/coaching.py | `23210dde720b01fe53f26b339a0f98306b519a766bd5892056247c8f16875979` |
| src/voice_coach/backend_analysis/http_runtime.py | `2f6add3c31628d3bc7f5a92ca0095999e33245d52b922ad2958b08a1a14ae61b` |
| deploy/runpod/prepare_runtime.py | `ccca477a9d3155083acd20544600b80b9d6b0247007f758c9963dc27ad5c9026` |
| deploy/runpod/prepare_http.py | `5497ca9be103546439fdb19a1cefe396b51f2c18bfa2551b99b3228d4e42cd7d` |
| deploy/runpod/services.http.json | `c50d84178db21453fd9d3ee69afeb077b5d5c64dbfae19bc3e37bb872a554bf7` |
| configs/pronunciation_feedback/prompts/coaching/pronunciation-coaching-v1.md | `d1cec994c388f724b7b7c92ae5a8f5a357feb059e8ca951719eed9caa40dcfba` |
| docs/contracts/coaching_result_v1.schema.json | `fab5d88c611924bef156fbbe17169402f233e9c5ab3745ad0a8a2c749708d918` |
| docs/contracts/runpod_result_v1.schema.json | `00a7cd25bee85c34d93fe0903939b3b288e48679ed8ba1cad46b5d8701801ca0` |

추가로 coaching_evidence/expert_examples/visual adapter/composition/video facade/http_models 및 prompt·사례 manifest의 hash와 overlay 일치를 확인했다. 모델 자산 전체 재해시, Backend/FE/AWS측 전면 감사, 신규 생성·음성/영상 품질 QA, 재시작·설정 변경·commit/push/PR/배포는 수행하지 않았다.

## 후속 운영 변경 — 한국어 TTS와 통합 진입점 준비

사용자의 별도 구현 요청에 따라 `/workspace/intelligentai/deployment/api-gateway`에 독립 gateway/TTS 구성을 배치했다. [구성·운영 안내](../deploy/runpod/api-gateway/README.md)를 따른다. 최초 읽기 전용 조사와 달리 이 후속 작업에서는 파일 배치·환경 설치·gateway/TTS 재기동을 수행했다.

- 분석은 기존 8080을 유지하고 gateway는 0.0.0.0:8082, TTS는 127.0.0.1:8083으로 구성했다.
- TTS 환경은 영구 `/workspace/voice-coaching-tts/venv`에 복원했다. KR 전용 frontend와 기존 한국어 g2pkk/python-mecab-ko/python-mecab-ko-dic을 사용한다.
- 모델 가중치·전체 symbol ID·tone offset·공개 ko-KR 요청 계약은 유지했다. 변경 전 frontend는 `/workspace/voice-coaching-tts/source-backups/korean-runtime-v1`에 복구용으로 보존했다.
- 실제 변경 소스 hash는 `/workspace/voice-coaching-tts/korean-runtime.json`, 설치 의존성 hash는 `gateway-environment.json`에 기록했다. 기존 분석 sourceRevision은 변경하지 않았다.
- 00:56 KST 단계에서는 외부 8082/http와 Pod 시작 명령이 미반영이었다. 이후 사용자 요청을 이어서 적용하여 8082/http를 추가하고 통합 wrapper로 시작 명령을 전환했다. 실제 재설정 후 자동 복원까지 완료했으며 [01:04~01:06 KST 확인](runpod_api_gateway_20260930.md)을 따른다.
- 이 단계에서는 AWS nginx·터널·Backend 설정을 변경하지 않았다. 이후 01:12 KST에 별도 사용자 요청으로 AWS nginx도 전환했으며 아래 후속 기록을 따른다.
- 2026-09-30 00:56:33 KST, 통합 포트의 인증된 `/health/ready`는 200 ready, revision=melo-ko-practice-r1이었다. 같은 기동에서 분석 `/readyz` 200도 확인했다. 이 health는 외부 HTTP 공개 완료나 실제 합성 API 품질 검증을 뜻하지 않는다.

확인은 패키지 설치 메타데이터·소스 hash·구문 확인과 서비스 health 범위다. 새 사용자 녹음·GPT 생성·TTS 합성 API·품질 QA·자동 회귀 테스트·commit/push는 수행하지 않았다. TTS 자체의 시작 warmup은 서비스 기동의 일부다.

## 후속 운영 변경 — AWS 기존 호출 경로 전환

2026-09-30 01:12 KST에 AWS nginx의 분석·TTS upstream을 모두 `https://s8hb5b0l5k4qlx-8082.proxy.runpod.net`으로 변경했다. Backend의 분석·TTS base URL `https://ai.voice-coaching.site`는 유지했으며 Backend 재시작·환경/JAR 변경 없이 nginx만 graceful reload했다. 기존 서비스별 인증과 upstream TLS 검증을 유지했다.

기존 도메인의 양쪽 인증 health 200 확인 후 `voice-coaching-tts-tunnel`을 중지·자동 시작 해제했다. 01:14:38 KST 최종 확인에서도 분석·TTS ready 200, Backend UP, nginx/Backend active, 터널 inactive/disabled였다. 실제 분석·합성 생성과 품질 QA는 하지 않았다. 설정 경로·백업·hash·확인 범위·원복 주의사항은 [AWS 전환 기록](runpod_api_gateway_20260930.md)을 따른다. 위 최초 장애 관측은 과거 시점 기록이며 현재 연결 상태와 구분한다.
