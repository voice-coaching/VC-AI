# RunPod 실제 배포 파이프라인 — 2026-09-28 관측

> 이 문서는 9월 28일 이력이다. 현재 구성은 [9월 30일 최신 전체 조사](runpod-current-pipeline.md)를 우선한다. 이후 8082 gateway·AWS 직접 연결·독립 TTS 복원과 입술–GPT P0/P1 배포가 반영됐다.

## 근거와 적용 범위

RunPod 관리 API와 SSH를 통한 읽기 전용 조사다. 2026-09-28 12:34:47 UTC(21:34:47 KST)에 배포 metadata와 supervisor 상태를 읽고, 12:37:48 UTC부터 분석 health/readiness를 조회했다. 이어 같은 세션에서 TTS health와 전문가 사례 로드 로그를 확인했다. 아래는 **그 시점의 실제 배포 구성**이며 문서 수정만으로 코드·배포가 바뀌지 않는다.

| 항목 | 확인 결과 |
|---|---|
| 대상 Pod | `s8hb5b0l5k4qlx`, `intelligentai-a40-int8-120gb-20260915` |
| 관리 API | `RUNNING`, NVIDIA A40, 공개 설정 `22/tcp`, `8080/http` |
| Pod 배포 sourceRevision | `a97b86eba700e8ed6d197f929ebc9ec1258ecf52` |
| 배포 overlaySha256 metadata | `72ac65eb035ad0951dd662a10f0df3a0ef0e859779838aa785cd5c0346199406` |
| 로컬 조사 시작 HEAD | `44dc1300050b294f76c150a06e965dc6dea0388b`, `main`, 기존 `origin/main`보다 26 commits 뒤 |
| 실제 분석 서비스 | supervisor의 `api`만 ready, listener `0.0.0.0:8080`; `:8000` listener 없음 |
| 실제 provider/model | `/health/services`: `OPENAI` / `gpt-6-astra` |
| 분석 상태 | `/health` 200 alive, `/readyz` 200 ready, 인증 `/health/services` 200 ready, executorReady=true |
| runtime env 파일 | `runpod_http`, coaching=true, contract=v3, provider=OPENAI, model=gpt-6-astra, Seungun device=cuda; OpenAI 키 존재만 확인 |
| 독립 TTS | `127.0.0.1:8082`, 인증 health/live·health/ready 모두 200, revision=`melo-ko-practice-r1` |

`/proc/<api-pid>/environ`은 접근 거부되어 읽지 못했다. 설정값은 생성된 runtime env 파일, 배포 코드, 실제 서비스 응답을 교차 확인했다. sourceRevision만으로 모든 파일이 해당 Git commit과 동일하다고 주장하지 않는다. 아래 주요 파일의 `/opt` 실행 사본과 영구 overlay 바이트 일치는 별도로 확인했다. 전체 overlay manifest를 재생성하거나 모든 자산을 다시 해시한 것은 아니다.

## 전체 흐름

```text
[User: script + recording (+ optional video)]
                 |
                 v
[VC-BE: ownership / canonical media / job state]
       |                               |
       | analysis request              +--> [Private recording object store]
       v                                                |
[RunPod HTTPS proxy -> HTTP API :8080]                   | bounded GET + SHA
       |                                                |
       | claim / heartbeat / deadline / cancellation    |
       +<-------------------------------> [VC-BE]        |
       |                                                |
       v                                                v
[HTTP executor / restricted attempt workspace] <--------+
       |
       +-- script -> [offline g2pK expected phones] --+
       +-- audio  -> [ffmpeg: 16 kHz mono PCM/WAV] ---+
                                                    |
                                                    v
                         [Seungun: one warm CUDA inference]
                         [all phone evidence + original selected phone]
                                                    |
                                                    v
                         [Coaching evidence admission]
                         [ACTIONABLE / REVIEW_ONLY / OBSERVED]
                         [group by phone pattern; choose <= 3]
                                      |                 |
                                      |                 v
                                      |       [Same-attempt video branch]
                                      |       [video/PCM/index binding]
                                      |       [QC + A/V sync + windows]
                                      |       [MediaPipe / P6 lip geometry]
                                      |       [aggregate observations]
                                      |                 |
                                      +--------+--------+
                                               |
[Pinned expert examples: 2 complete cases] ----+
[System/coaching prompt + practice guidance] --+
                                               v
                         [Input packing + candidate budget]
                            |                  |
                     no candidate /           | admitted candidates
                     budget failure           v
                            |       [External OpenAI API]
                            |       [gpt-6-astra, low reasoning]
                            |       [Chat Completions JSON mode]
                            |                  |
                            |                  v
                            |       [IDs / coverage / text validation]
                            |                  |
                            v                  |
                  [Deterministic fallback] <---+ generation/validation failure
                            |                  |
                            +--------+---------+
                                     v
                         [Coaching result + provenance]
                         [overallScore=null; visual not validated]
                                     |
                                     v
                         [Temporary media cleanup]
                                     |
                                     v
                         [HTTP result callback -> VC-BE -> User]

Independent example-audio path:
[VC-BE TTS worker] -> [documented AWS nginx + SSH tunnel]
                  -> [RunPod TTS :8082 / independent manager]
                  -> [MeloTTS KO -> WAV -> ffmpeg MP3] -> [VC-BE]

Offline data/assets:
[External original/derived data -> Backblaze B2]
                  -> [reviewed runtime assets / deployment recovery]
                  -> [RunPod local artifacts]
```

Backend 영구 저장과 AWS nginx/SSH tunnel의 현재 상태는 이번에 직접 조사하지 않았다. 그림의 Backend 경계는 실행 중인 RunPod의 요청·callback 코드와 기존 운영 인계에 근거한다. TTS의 AWS 경로는 2026-09-19 인계 기록이며 이번 live 확인은 Pod 쪽이다.

## HTTP 요청과 작업 수명

운영 전송은 `voice-coaching.runpod-http.v1.1` HTTP다. Redis request-v5/authorization-v4를 HTTP 요청에 섞지 않는다. Backend에서 오는 분석 접수·조회·취소는 서버 Bearer 토큰으로 인증하며 반대 방향 claim·heartbeat·result에는 별도 callback 토큰을 쓴다. 사용자 로그인·녹음 소유 관계는 Backend 책임이다.

- 접수: `POST /v1/analysis-jobs`; 조회: `GET /v1/analysis-jobs/{requestId}?executionId=...`; 취소: `POST /v1/analysis-jobs/{requestId}/cancel`.
- Backend callback: `/api/internal/ai/analyses/{analysisId}/{claim,heartbeat,result}`. readiness는 `/api/internal/ai/worker-readiness`.
- 동일 request/execution과 payload digest를 대조한다. 한 번에 한 작업을 예약하며 새 작업이 겹치면 429 `CAPACITY_EXCEEDED`다. claim을 획득한 후 executor에 요청을 보낸다.
- heartbeat·lease·deadline·취소가 작업과 callback 수명을 제한한다. callback의 일시 오류는 유효 lease 내에서 재전송한다.
- 녹음과 선택 영상은 제한된 객체 저장소에서 읽고 metadata·크기·SHA를 검증하며 처리 후 임시 파일을 정리한다. 연구자료 B2를 사용자 녹음 저장소와 동일시하지 않는다.

코드에 남은 운영상 주의점: `http_runtime.py`의 취소 finally에는 Clova 시대 설명과 **180초 drain 대기**가 여전히 있다. Clova가 실행 중이라는 뜻은 아니며 GPT 전환 후 취소 슬롯 반환이 즉시라고 문서화해서는 안 된다. 이번 작업에서는 이를 수정하지 않았다.

## Seungun과 코칭 후보

대본을 offline g2pK로 기대 음소열로 바꾸고 음성은 ffmpeg로 16 kHz mono PCM/WAV로 만든다. warm isolated Seungun은 CUDA에서 한 요청당 한 번 추론한다. 영상 분기의 `_PrecomputedSeungun`은 같은 음성 PCM과 기대 음소열을 대조하고 기존 결과를 재사용한다.

원래의 selected phone과 검출 결과는 provenance로 보존된다. 현재 학습 항목은 `coaching_evidence.py`가 **전체 음소 근거**에서 정한다. 과거처럼 원래 선택 음소 하나만 설명하는 구조가 아니다.

1. 기대열·CTC 후보열·철자 역할의 최소 편집 정렬을 검토한다.
2. deletion 기본 특징, 복수 정렬 가능성, 미확인 철자 역할, 초성 ㅇ 규약, 유효 국소 음향 구간 부재 등은 `REVIEW_ONLY`로 보류한다.
3. 근거가 허용되고 검출기가 표시한 행을 `ACTIONABLE`로, 나머지 관측을 `OBSERVED`로 구분한다.
4. 기대 표지·관측 후보·철자 역할로 묶어 반복 수, 첫 등장 위치 순으로 정렬한다. 최대 3개 후보를 코칭에 사용하고 입력 예산에 따라 후보 묶음 단위로 줄인다.

GPT는 이 후보의 ID와 범위를 넘어 새 진단 대상을 만들 수 없다. CTC 표지는 실제 전사나 확정 조음 원인으로 취급하지 않는다. `detectorScore`는 보정된 정확도나 학습 점수가 아니다.

## 영상 기반 입술모양 분석

후속 관측: [2026-09-29 영상·입술 분석 문서](video-lip-analysis/README.md)에 현재 배포 코드, 활성 자산, 계산 방식과 제약을 별도로 정리했다. 아래 내용은 2026-09-28 관측으로 보존한다.

현재 GPT 경로는 `analyze_coaching_observations` → `run_http_observations`를 사용한다. 최대 3개 후보의 첫 근거 음소 index에 대응하는 시간 구간을 검사한다. 동일 영상 SHA, request/execution, 추출 PCM, 기대열을 결속한다. Seungun 추론은 재실행하지 않는다.

영상 admission·container probe·내장 음성 추출과 요청 내 decode cache를 재사용하되 후보별 handoff·QC·A/V sync·신뢰성 검사를 거친다. MediaPipe 랜드마크와 P6 geometry 경로에서 얻은 aggregate 값, 구간, 실패 reason, 실제 decode 시각을 관찰로 전달한다. 40점 후보/24점 매핑은 기존 geometry 자산의 구조이며 매 요청에서 모든 형상값이 항상 제공된다는 뜻은 아니다.

승인된 교정 action이 없어도 유효 관찰값을 전달할 수 있다. 공개 aggregate 계약에 유효 landmark 표본 수가 없으므로 `sampleCount=null`; decode 시각 개수를 landmark 유효 표본 수로 해석하지 않는다. `referenceStatus=NOT_VALIDATED`, `correctiveClaimsAllowed=false`를 유지한다. 영상 실패 시 유효한 음성 결과와 코칭 경로를 보존한다.

GPT 모드에서는 기존 최상위 `visualSupplement`는 null이며 영상 관찰은 `coaching.visual`에 들어간다. 실제 혀 위치·성문·기류·3D 돌출이나 개인별 입 벌림 오류를 이 관찰만으로 확정하지 않는다.

## GPT-6 입력·전문가 사례·출력

배포 `coaching_generator.py`는 `OPENAI`와 **`gpt-6-astra`** 및 `https://api.openai.com/v1/chat/completions`를 고정한다. `reasoning_effort=low`, `response_format={type:json_object}`, `store=false`, `max_completion_tokens`를 사용한다. 숨은 재시도나 다른 모델로 자동 전환은 없다. API 관리 토큰과 `OPENAI_API_KEY`는 별개다.

메시지는 system/coaching prompt, 전문가 참고 사례, 현재 시도의 구조화 근거 순서다. 현재 시도에는 대본, 기대 음소/CTC 후보, 허용된 후보와 위치·음향 근거, review 집계, 영상 aggregate, 일반 목표음 연습 guidance가 포함된다. 원본 녹음·영상 바이트와 landmark 배열을 GPT 분석 입력으로 보내는 경로는 아니다.

전문가 사례는 `configs/coaching/expert_examples_v1.json`가 지정한 **두 사례 전체**를 매 생성에 추가한다. semantic 검색이나 모델 가중치 학습이 아니다. 과거 분석 출력과 아나운서 피드백은 참고자료이고 현재 시도의 관측·보정 정답으로 복사하지 않는다.

| 사례 근거 | 값 |
|---|---|
| revision | `announcer-paired-examples-20260928-v1` |
| 정책 | `ALL_CASES_EVERY_GENERATION` |
| case IDs | `testvideo1`, `testvideo2` |
| artifact | `artifacts/expert_coaching/announcer-20260928/cases.json` (Pod 내부 경로) |
| SHA-256 | `eb68e02e75bd1b816414c411dd97953c20cd08361a2f7da6b58b94341717f3f8` — 실제 파일 일치 확인 |
| 실제 시작 로그 | `2026-09-28T04:37:10Z`, `coaching_expert_examples_loaded`, input_budget=410821 |

`load_coaching` 기본값은 timeout 120초, 현재 시도 입력 예산 16000, 출력 1200이다. 사례 전체에 필요한 예약분을 입력 예산에 추가한다. 예산 계산은 UTF-8 바이트 기반 보수적 상한으로 실제 tokenizer 수치와 다르다. 실제 usage는 provider 응답을 따른다.

허용 후보가 있을 때 생성 호출은 0~1회다. 응답의 정상 종료·모델 identity·JSON 중복 키·크기, candidate/guidance 참조, 모든 입력 후보의 포함, 텍스트 제한을 검증한다. JSON 검증은 자연어의 교육적 정확성 검증을 대신하지 않는다.

생성 항목은 `explanation`, `action`, `practice`, `selfCheck`를 갖고 `observation`·위치·근거 연결은 코드가 붙인다. 생성 실패/검증 거절/입력 예산 문제에는 동일 허용 근거의 deterministic 연습 문구를 사용하고 `generation.source=TEMPLATE`, `fallbackReason`을 명시한다. 별도의 template-only 운영 provider가 있는 것은 아니다. 후보가 없으면 GPT를 호출하지 않고 `NO_ACTIONABLE_CANDIDATES`를 기록한다.

## 결과와 점수

완료된 coaching 결과는 `voice-coaching.runpod-analysis-result.v3`, 내부 coaching은 `voice-coaching.coaching-result.v1`이다. `READY`, `LIMITED_EVIDENCE`, `NO_ACTIONABLE_ISSUE`로 근거 상태를 표현한다. callback은 `overallScore=null`, `scoringEvidence=null`; coaching의 score도 `NOT_CALIBRATED` 또는 `INSUFFICIENT_EVIDENCE` 및 null 점수다. 기존 Clova 채점을 실행하지 않는다. `strengths=[]`, `comparison=null`을 임의로 채우지 않는다.

coaching 생성 전에 발생한 FAILED 결과에는 coaching이 없으므로 serializer의 legacy 결과 경로가 남아 있다. 모든 응답이 무조건 v3라고 가정하지 않는다. Backend DB 저장·화면 표시·실제 녹음 완주 여부는 이번 관측에서 직접 재검증하지 않았다.

## 독립 TTS와 저장소

TTS는 `/workspace/voice-coaching-tts/service/{server.py,manager.py}`, `/opt/voice-coaching-tts/venv`로 실행된다. 독립 manager가 `127.0.0.1:8082`를 관리하며 `POST /v1/tts/synthesize`는 별도 Bearer 토큰을 쓴다. 배포 소스는 MeloTTS 한국어 CUDA 모델, 속도 0.92, WAV 생성 후 ffmpeg MP3 변환을 사용한다. GPT 코칭 출력에서 TTS로 자동 연결되는 호출은 확인하지 않았다.

AWS nginx exact 경로와 SSH tunnel, Backend MP3 저장은 기존 `origin/main`의 `docs/example-tts-operations-20260919.md` 및 VC-BE 인계 문서에 기록돼 있다. 이번에는 Pod의 TTS 소스·listener·health만 확인했다. TTS 컨테이너 재생성 복원과 AWS 현재 연결은 미확인이다.

외부 원본·파생 자료의 영구 저장 위치는 프로젝트 결정에 따라 Backblaze B2다. 운영에서는 고정 실행 자산·모델·전문가 사례를 로컬에서 읽으며 요청마다 B2 원자료를 검색하는 코드는 이번 경로에서 확인되지 않았다. 사용자 미디어는 별도의 제한 객체 저장소 설정으로 읽는다.

## 코드 근거와 재확인

아래 경로는 **Pod `/opt/intelligentai/app` 기준**이다. 같은 상대 경로의 영구 `/workspace/intelligentai/deployment/int8-overlay`와 바이트가 일치했다. 오래된 로컬 checkout에 파일이 없거나 내용이 달라도 이번 배포 관측을 로컬 구현 사실로 바꾸지 않는다.

| 경로 | 실행 파일 SHA-256 |
|---|---|
| `src/voice_coach/backend_analysis/production.py` | `37328367f51928b12af17a4085b021d2952f94f761e4951c17256449766e9278` |
| `src/voice_coach/backend_analysis/coaching_generator.py` | `160a75c0271a12eb9e2ab6ad15d8ff0db2b67b5308515c48d099ebda08f4a860` |
| `src/voice_coach/backend_analysis/coaching.py` | `6f47817de6d573fc3002b3e76bda80ebac4974b5603f797167cc1c73a0fb55c6` |
| `src/voice_coach/backend_analysis/coaching_evidence.py` | `413eeacba67b88fb37338502e0bf6b49f5b3ba25600c9b794963779269b38ab2` |
| `src/voice_coach/backend_analysis/expert_examples.py` | `dfa4b0bb0d665564e40025db1e9d26597563d9c4e8b541f8d2d483fedf528114` |
| `src/voice_coach/backend_analysis/adapters/visual.py` | `c71763d907cbd13c4c00524864765457aa6e73063456fb0faf4b878245578deb` |
| `src/voice_coach/backend_analysis/http_runtime.py` | `2f6add3c31628d3bc7f5a92ca0095999e33245d52b922ad2958b08a1a14ae61b` |
| `src/voice_coach/backend_analysis/http_models.py` | `5ef0b99bc8476a459f54d188691016eb3c4cb8cbac9c57f1dc0682f80d6586c9` |
| `deploy/runpod/services.http.json` | `c50d84178db21453fd9d3ee69afeb077b5d5c64dbfae19bc3e37bb872a554bf7` |

보조 근거는 Pod의 `docs/gpt_only_runtime_20260919.md`, `video_attempt_facade.py`, `prepare_http.py`, `run_services.sh`, `supervise_services.py`, TTS server/manager다. 구형 Clova 파일의 존재나 오래된 주석을 실제 실행 근거로 사용하지 않는다.

이번 확인은 API/SSH 읽기·소스/선택 파일 hash·비생성 health·문서 정적 확인이다. GPT 생성, 실제 녹음 처리, 음성/영상 품질 QA, 자동 회귀 시험, 서비스 재시작, 코드 동기화, commit/push/PR/배포는 수행하지 않았다.
