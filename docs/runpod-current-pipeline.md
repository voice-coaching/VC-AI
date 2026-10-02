# 현재 RunPod 배포 파이프라인

[문서 목차](README.md) · [운영 절차](../deploy/runpod/README.md) · [운영 API](api/specification.md) · [입술 분석](video-lip-analysis/README.md)

**10/2 17:55 KST 후속:** [testvideo1 실제 API 검증](canonical-integration/testvideo1-verification-20261002.md).
G2P 환경·MFA zombie 회수 수정 `0adcdef`를 배포했다. 최신 overlay는
`41f30bdb15f02c2f2141845fddfb7b95b9004c7a4fde5b406943ed1f4e518d1d`다.
분석 51 core `ACCEPT / ok` 확인 후 Backend journal JSON wire 오류를 발견했다.
Backend PR #95 준비, 운영 교체 승인 대기이며 전체 callback E2E는 미완료다.
아래 overlay·미수행 표기는 각 관측 시점의 이력이다.

**10/2 11:39~11:41 KST 후속:** [실행기·부팅·키 주입 및 제한 v4 활성](canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화)을
운영 확인했다. Backend reader·검증·journal/apply 및 양쪽 admission ON이다.
명시 opt-in 단독 음성 발음 학습만 해당하며 기존 분석/TTS와 공개 API는 유지한다.
현재 overlay는 `b3fb15bc9b4027faeabfd089f7a0f3f2750391f766392412bb72634e08c0f6f5`다.
아래 9/30 source revision만으로 추가 코드를 식별하지 않는다. 실제 추론·callback E2E는
미수행이며 FE 테스트는 FE 개발자 책임이다. 이하 9/30 관측은 당시 이력이다.

**관측: 2026-09-30 01:38~01:44 KST / 2026-09-29 16:38~16:44 UTC.** 실행 중인 Pod와 AWS를 읽기 전용으로 조사했다. 이 문서를 현재 상태의 진입점으로 사용하고, 날짜별 배포 기록은 당시 이력으로 읽는다. 이후 상태 변화까지 보장하는 실시간 문서는 아니다.

후속 2026-09-30 21:02:44 KST RunPod 인증 분석 readiness에서도 같은 `32d98e3`가 확인됐다. 새 canonical core와 frozen LLM은 별도 artifact이며 아직 운영 경로가 아니다. [원본 위치·계약 차이·통합 진행](canonical-integration/README.md)을 참고한다. 이 후속 확인으로 아래 AWS·TTS 관측 시각을 갱신한 것은 아니다.

## 결론과 조사 범위

현재 운영은 **AWS Backend → AWS nginx → RunPod 8082 gateway → 분석 API 또는 독립 한국어 TTS**다. 분석은 Seungun CUDA 음성 근거와 선택적인 동일 시도 입술 관찰을 외부 GPT-6 API(`OPENAI / gpt-6-astra`)에 연결한다. Clova 추론·채점은 운영 조립 경로에서 폐기됐다. 입술–GPT P0/P1은 이미 배포됐으며 P2 개인별 영상 교정은 비활성이다.

관리 API의 Pod 목록, 실행 프로세스·listener·supervisor, 지정된 비밀 아닌 설정, 실행 소스, overlay·자산 hash, AWS 서비스·nginx·JAR 식별자, 비생성 health와 제한된 로그 이벤트를 확인했다. 비밀값·사용자 미디어·원문 로그는 이 문서에 복사하지 않는다. 조사 중 원격 설정·코드·프로세스를 변경하지 않았고 신규 분석·GPT 생성·TTS 합성·자동 회귀 테스트·품질 QA를 수행하지 않았다.

## 배포 식별과 기동

| 항목 | 관측 |
|---|---|
| 조회 가능한 Pod | 1개: `s8hb5b0l5k4qlx`, `intelligentai-a40-int8-120gb-20260915`, RUNNING |
| image / GPU | `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` / NVIDIA A40 |
| 실행 root | `/opt/intelligentai/app` |
| sourceRevision / workerRevision | `32d98e39767b56697bc7e58fb50e3e9d61432349` |
| baseRelease | `15534800bcc3f730d6fffeb9aa98d3ee91d3ab18563b0dc5f0fad4dff6eb2887` |
| overlaySha256 metadata | `4721ac82c685e006951911bc7c756d91e55fb3285a5b861bc2481a1dde5c35ab` |
| 영구 overlay 대조 | manifest의 82개 항목 모두 영구 사본·실행 사본과 일치; symlink는 링크 대상 문자열로 대조 |
| 자동 배포 상태 | `github-actions/state.json`의 git/runtime revision 일치, pending 없음 |
| Pod 시작 명령 | `bash /workspace/intelligentai/deployment/api-gateway/boot.sh` |
| 관리 API 노출 포트 | `22/tcp`, `8080/http`, `8082/http` |
| 분석 supervisor | API PID 2800, supervisor 2799, ready, restartCount=0, consecutiveHealthFailures=0 |
| gateway/TTS supervisor | supervisor 73, nginx 103, TTS 104, 모두 running·restarts=0 |
| 조사 checkout | main, `8098fd4004ad6ce1ffa9fe40b53be814d987a799`, 시작 시 clean |

PID는 관측값이지 다음 작업의 조작 대상 상수가 아니다. 전체 base release·대형 Seungun/TTS 가중치를 이번에 다시 해시한 것은 아니다. runtime revision은 적용한 배포의 식별자이며 **로컬 checkout 전체와 운영 파일이 같다는 뜻이 아니다**. 실제로 운영 전용 GPT-only `production.py`·`http_runtime.py` 등의 차이가 남아 있다. `int8` Pod 이름·overlay 경로는 과거 이름이며 GPT의 로컬 실행을 뜻하지 않는다.

wrapper boot는 gateway/TTS supervisor와 기존 `int8-overlay/deploy/runpod/boot.sh` 분석 기동 경로를 함께 시작한다. 분석은 base+overlay+runtime-assets 복원, HTTP attestation·설정 준비 후 manager → supervisor → API를 실행한다. 분석 서비스 목록에 `api` 하나만 있는 것은 **별도 supervisor의 TTS가 없다는 뜻이 아니다**. gateway/TTS 파일은 분석 overlay manifest와 별도 관리된다.

## 실제 통신·서비스 구성

| 계층 | 현재 주소/역할 | 인증·경계 |
|---|---|---|
| AWS Backend | `RUNPOD_ENDPOINT_URL`, `EXAMPLE_TTS_BASEURL` 모두 `https://ai.voice-coaching.site` | 분석·TTS 각 서버 토큰 |
| AWS nginx | 위 도메인을 `https://s8hb5b0l5k4qlx-8082.proxy.runpod.net`로 전달 | upstream TLS 검증 on; TTS read 35초, 기본 60초 |
| RunPod gateway | nginx `0.0.0.0:8082` | 허용 경로만 전달, 나머지 404 |
| 분석 API | `0.0.0.0:8080` | `/health`, `/readyz` 제외 Bearer |
| 독립 TTS | `127.0.0.1:8083` | health 포함 별도 Bearer |
| 결과 callback | `https://api.voice-coaching.site/api/internal/ai/analyses/{analysisId}/…` | 별도 callback 토큰, claim·heartbeat·result |
| 미디어 | 제한 S3-compatible 객체 저장소 → 요청 임시 workspace | key 범위·크기·SHA 검증; 연구자료 B2와 혼동 금지 |

`0.0.0.0`은 bind 주소다. AWS가 사용하는 것은 HTTPS 도메인이며 public TCP 8082 직접 접속이나 SSH 터널이 아니다. 기존 분석 8080의 HTTP 노출도 아직 유지된다. image 기본 nginx의 9091/3001/7861/8081/8001/7270 listener는 각각 내부 포트로 전달하는 기본 proxy다. 해당 모델/API가 실제 실행 중이거나 8082에서 공개된다는 증거가 아니다. 현재 8000 listener와 Clova/vLLM 실행 프로세스는 발견하지 않았다.

gateway의 `/gateway/health`는 자체 liveness, `/health/services`는 분석 readiness, `/health/ready`는 TTS readiness다. 분석 작업 본문 상한 64 KiB, TTS 32 KiB, gateway upstream read 30초·자동 재시도 없음이다. 분석 접수는 비동기이므로 이 30초가 전체 추론 제한시간은 아니다. 경로별 상세는 [API 명세](api/specification.md)를 따른다.

AWS에서 `alpha-backend`·nginx는 active/running/enabled, 과거 `voice-coaching-tts-tunnel`은 inactive/dead/disabled였다. Backend PID 86851, nginx PID 1995이며 재시작하지 않았다. 실행 JAR 링크는 `/opt/alpha/releases/b48db988e7485943097acaa4c71308ef306175a8.jar`, 파일 SHA-256은 `0ad3f601271d088cf2bc3c0be54db9b24bd298827ab631585997dea2f14b404e`다. 파일명만으로 Git provenance를 확정하지 않는다. nginx 설정 hash는 `e8b979412a57a17d3c0c52ef664a56c1286b37ac4bbb5f6d0dcc8a4626eb0cac`이며 이전 임시 접수 보호 설정은 남아 있지 않다.

## 분석 요청에서 callback까지

1. HTTP v1.1 제어 계약으로 요청을 인증·검증한다. 요청별 grant·서명·동의 증빙은 받지 않는다. Backend 로그인·녹음 소유 관계와 양방향 서버 인증은 유지한다.
2. `requestId/executionId` 및 JCS payload digest로 중복·충돌을 판정한다. 준비되지 않았으면 503, 단일 실행 슬롯 점유 시 새 요청은 429다. Backend claim 성공 후 executor에 작업을 넘기고 lease heartbeat를 보낸다.
3. 제한 저장소의 음성을 SHA·크기 검증 후 materialize한다. ffmpeg mono 16 kHz PCM, offline G2PK(`word_boundary_policy=drop`), 격리된 Seungun CUDA 분석을 수행한다. 음성 분석은 한 번이며 STT·GPT 점수를 새로 만들지 않는다.
4. 전체 음소 근거에서 허용 후보를 최대 3개 선택한다. 기대/관측 음소·철자 역할별 묶음의 반복 수와 첫 등장 순이며 detector score 순위가 아니다. legacy selected phone·digest·index·시각을 원본 근거로 보존한다.
5. 영상이 있으면 동일 시도 PCM·SHA와 후보 대표 음소를 결속한다. 전체 8프레임 QC·A/V sync 후 후보별 dense window(앞뒤 100 ms, 최대 1000 ms, 2~12프레임)를 처리한다. MediaPipe 4점 및 별도 40→24점 형상을 관찰한다. 영상 실패는 유효 음성 결과를 보존한다. [세부 흐름](video-lip-analysis/pipeline.md)
6. 내부 v2 측정 영수증과 정책으로 허용된 관찰만 GPT 입력에 투영한다. 전문가 두 사례 전체와 현재 시도 근거를 넣어 GPT 생성 최대 1회를 수행한다. 실패 시 같은 적격 후보의 deterministic fallback과 사유를 사용한다.
7. 서버 검증·렌더링 후 기존 coaching v1/callback v3으로 전달한다. 새 내부 receipt·표본 수·statement ID는 공개 응답으로 추가하지 않는다. 결과의 `overallScore/scoringEvidence`는 null이다. 요청 임시 파일은 정리하며 RunPod를 외부 자료의 영구 저장소로 사용하지 않는다.

작업 상태 캐시는 프로세스 메모리이며 완료 후 15분 또는 1024항목 초과 시 정리한다. 영속 상태의 정본은 Backend다. **취소 시 옛 Clova용 180초 drain 대기가 현재 `http_runtime.py`에도 남아 있다.** Clova 실행의 증거는 아니지만 취소 직후 슬롯이 즉시 반환된다고 문서화하면 안 된다. 이번 조사에서는 수정하지 않았다.

## GPT·입술 P0/P1의 실제 구현

호출 구현은 실행 root의 `src/voice_coach/backend_analysis/coaching_generator.py`, 조립은 `production.py`, 근거는 `coaching_evidence.py`, 영상 정책은 `coaching_visual_policy.py`다. `OPENAI / gpt-6-astra`, Chat Completions, `store=false`, `reasoning_effort=low`, strict JSON Schema `coaching_generation_v2`를 사용한다. 별도 timeout/input/output 설정은 없고 loader 기본 120초/현재 시도 16000/출력 1200을 사용한다. 전문가 bank 예약분은 별도이며 정확한 provider token 계수와 같지 않다.

- `voice-coaching.coaching-input.v2`, `coaching-measurement-receipt.v2`, `coaching-visual-policy-v2`가 배포됐다. candidate/audio evidence/observation ID를 연결하고 예산 축소 시 관련 근거와 statement를 함께 줄인다.
- 내부에는 실제 유효 표본 수·사용 프레임·context 시각·집계 방식을 보존한다. 구간 밖 기본 중심 프레임은 관찰로 승격하지 않는다. 변화율 계산 시간축은 여전히 정수 ms다.
- 정책은 `OBSERVATION_ONLY`/`UNAVAILABLE`만 허용한다. `referenceStatus=NOT_VALIDATED`, `correctiveClaimsAllowed=false`; 음소별 입 벌림 정답이나 개인 교정은 검증하지 않았다.
- GPT 출력은 후보·guidance ID, 네 텍스트 필드, `usedVisualStatementIds`다. **네 텍스트 모두 후보별 `allowedText` 선택으로 제한**되어 자유로운 상세 맞춤 문장 생성은 제한된다. 허용 후보/statement coverage·중복·문장/길이 등을 추가 검증한다.
- 수치와 구간은 GPT가 쓰지 않고 서버가 검증된 측정값으로 `items[].observation`에 렌더링한다. 출력이 해당 statement를 참조해야 붙인다. 입력 제공·출력 참조·표시 이벤트는 모델 내부 추론을 증명하지 않는다.
- 공개 `sampleCount=null`은 v1 호환성 때문이다. 내부 유효 표본 수가 없다는 뜻이 아니다. 공개 prompt/policy v1 라벨도 호환 라벨이며 새 내부 정책과 합성 prompt hash를 별도로 식별한다.

전문가 bank는 `configs/coaching/expert_examples_v1.json`이 지정하며 revision `announcer-paired-examples-20260928-v1`, `testvideo1/testvideo2` 모두 매 생성에 전체 주입한다. top-k 검색이 아니다. 파일 393,116 bytes, SHA-256 `eb68e02e75bd1b816414c411dd97953c20cd08361a2f7da6b58b94341717f3f8`를 manifest와 대조했다. 과거 전문가 의견은 현재 사용자 오류의 증거가 아니다. [구현·배포 기록](video-lip-analysis/p0-p1-deployment.md), [사례 구성](expert_feedback_rag_20260928.md)

## 독립 한국어 TTS

`/workspace/voice-coaching-tts/service/server.py`와 별도 Python 3.10.18 venv를 사용한다. Melo 한국어 모델, `cuda:0`, 속도 0.92, revision `melo-ko-practice-r1`이다. HF/Transformers offline 설정과 한국어 frontend 복원이 적용되어 있고 frontend 4파일 hash가 runtime manifest와 일치했다. 분석 overlay revision을 TTS 모델 revision으로 사용하지 않는다.

`POST /v1/tts/synthesize`는 고정 한국어 voice의 MP3를 반환한다. 텍스트 UTF-8 4500 bytes 이하, 요청 32 KiB 이하, 엔진 단일 lock·대기 제한·합성 대기 22초가 있다. 같은 requestId/payload의 결과는 최대 8개·120초 메모리 캐시, 충돌은 409다. 이것은 영구 음성 저장소가 아니다. 실제 합성 호출과 이미 저장된 음성의 재생을 구분해야 하며, readiness 확인만으로 모든 재생이 RunPod 합성을 호출한다고 판단하지 않는다.

## 실제 확인 결과와 남은 한계

| 확인 | 관측 | 의미/제한 |
|---|---|---|
| Pod 로컬 8082, 01:39:36 KST | gateway health 200 alive; 인증 분석·TTS readiness 각각 200 | 세 프로세스 경로 확인 |
| 인증 없는 분석 상세 health | 401 UNAUTHENTICATED | 분석 Bearer 경계 확인 |
| gateway `/docs` | 404 | 미허용 경로 비공개 |
| AWS 기존 도메인, 01:40:07 KST | 인증 분석 200 ready, executorReady=true, `32d98e3`, `OPENAI/gpt-6-astra`; TTS 200 ready, `melo-ko-practice-r1` | 터널 없이 기존 호출 경로 확인 |
| AWS Backend 내부 health | UP | 실제 callback 저장 검증 아님 |
| 영상 release 자산 | 기존 release와 QC/sync/bundle·geometry·랜드마크 hash 일치 | 새 영상 추론 검증 아님 |

분석 readiness는 executor, Backend `worker-readiness` 계약, 제한 저장소 읽기, OpenAI 모델 접근을 점검한다. monitor 주기는 15초, 의존성 유효기간 45초, 모델 조회 성공 캐시는 300초다. 모델 GET 성공은 **새 strict schema 생성 요청의 성공을 검증하지 않는다**.

최근 2시간의 `analysis-http.log*`에서 ISO 시각이 있는 행만 제한적으로 집계했다. 해당 행 2개 중 정책 로드 이벤트 1개였고 선택한 생성/실패/callback 실패 이벤트는 발견되지 않았다. 시각 없는 행은 제외했으므로 “오류 없음”, “호출 없음” 또는 전체 서비스 로그 전수 분석으로 표현하지 않는다.

남은 개발자 확인: 실제 녹음·영상의 전 구간 완주, strict schema 생성 수용, statement 선택·fallback, callback 저장·FE 표현, 실제 TTS 합성, 품질·지연. sync 정책의 `minimum_correlation=-1.0`, 미검증 개인 교정, 제한된 GPT 문구, 취소 drain, 운영/checkout 차이는 별도 개선 판단 대상이다. 이번 작업은 문서 갱신이며 이를 수정·재배포한 것이 아니다.

## 재현 가능한 파일 식별자

아래는 이번에 읽은 **실행 파일 바이트 SHA-256**이다. 비밀 설정은 제외한다. 더 자세한 영상 자산·소스 근거는 [영상 배포 근거](video-lip-analysis/deployment-evidence.md)에 있다.

| 실행 root 상대 경로 또는 절대 경로 | SHA-256 |
|---|---|
| `src/voice_coach/backend_analysis/production.py` | `37328367f51928b12af17a4085b021d2952f94f761e4951c17256449766e9278` |
| `src/voice_coach/backend_analysis/http_runtime.py` | `2f6add3c31628d3bc7f5a92ca0095999e33245d52b922ad2958b08a1a14ae61b` |
| `src/voice_coach/backend_analysis/coaching_generator.py` | `1cd13b0de18549750b1ec0c1a7faa078e768da3f074168bb9f538dd5a733ccf2` |
| `src/voice_coach/backend_analysis/coaching_visual_policy.py` | `3e5f92a5147db0017ad649e520b68d90e7da1f9f13ac6c5db21a8f9ef2816f7d` |
| `/opt/intelligentai/http-deployment/manifest.json` | `9d6ccdbcc60193a11b477b59bb409cedbd9e25a1e4edf09ad91fa5658976a42d` |
| `/workspace/intelligentai/deployment/api-gateway/boot.sh` | `a93535df59c78a89fbb8642a8b001f2d4b1c27a7d9d7ee4b390caef7500a0a90` |
| `/workspace/intelligentai/deployment/api-gateway/nginx.conf` | `3cb5950f729aed96e2bd9b6bc891ffe9a5e2f65069f4025c68870cc7a2928dec` |
| `/workspace/intelligentai/deployment/api-gateway/supervisor.py` | `fe3bab7101867141a9155f5d71a30d08e388f4d13175ce00dcd3c269cc415d6e` |
| `/workspace/voice-coaching-tts/service/server.py` | `8b41fcf817af76a9c0aaabae0df7c3a8efcd03c754fe92bffe3963daa17b4282` |

HTTP attestation은 `voice-coaching.analysis-http-deployment.v1`, releaseId는 위 runtime revision, workerDevice=cuda다. detector·Seungun model/processor·NLTK·영상 자산 항목을 가진다. manifest 파일 hash 확인과 모든 대형 모델 파일의 재검증은 구분한다.
