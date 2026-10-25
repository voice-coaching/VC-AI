# 문장연습 GPT-6 Luna 적용과 실제 API 시간

## 적용 상태

2026-10-24 12:04 UTC 문장연습 direct 운영 경로를 `gpt-6-astra`에서
`gpt-6-luna`로 전환했다. 양쪽 모두 `reasoning_effort=low`다. 사용자가 말한
"Astra light"는 실제 설정에서 별도 모델 ID가 아니라 Astra의 low 설정이었다.
[OpenAI 공식 Luna 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)의
Chat Completions·low 지원과 운영 키의 모델 조회 HTTP 200을 확인했다.

분석 코어·프롬프트·rubric·출력 schema·endpoint·3000 completion token 상한·
JSON object·store=false·120초 timeout·재시도 없음은 동일하다. 기존 H5를
수정하지 않고 요청 설정의 model만 바꾼 새 불변 H5와 lock/manifest를 생성했다.
transport는 서버가 등록한 harness 모델과 직렬화된 요청 모델의 일치를 검사하며,
동결 adapter의 실제 provider 응답 모델 검사도 유지한다.

`AI_DIRECT_LLM_ROOT`를 direct 서비스가 자기 자식 환경의 canonical root로
선택한다. Backend 내부 handoff는 기존 승인 tuple·schema digest에 묶여 있어
`AI_CANONICAL_LLM_ROOT`와 Astra 설정을 유지했다. 따라서 내부 `/health/services`의
coachingModel은 문장연습 모델 표시가 아니다. 내부 handoff admission은 전환 전에도
false였으며 이번 작업에서 열지 않았다. 전체 v6 배포도 수행하지 않았다.

분석 서비스를 중지하기 전에 자신이 소유한 admission hold를 만들고 활성 작업 0을
두 차례 확인했다. 분석만 재시작했으며 TTS는 재시작하지 않았다. 워밍 후 hold를
해제했고 마지막 관측에서 공개 `/direct/readyz`는 200/ready=true,
활성 작업과 direct 작업은 각각 0이었다. 모델 패치는 deployment.json에 별도로
기록했으며 기존 배포 revision을 새 소스 revision으로 위장하지 않았다.

지속 recovery 경로에 고정 SHA의 소스 payload·H5와 복구 hook을 설치했다.
복구 helper는 이미 적용된 파일 또는 지정된 이전 SHA만 허용하고 파일 변경 시
분석 중지를 요구한다. 전체 cold 복구는 실행하지 않았다. 기존 Backend readiness
gate가 남아 있으므로 이 기록을 전체 자동 복구 성공으로 해석하지 않는다.

## 실제 요청과 비교 조건

공개 `POST https://ai.voice-coaching.site/direct/api/v1/analyses`로 실제 분석을
한 번 수행했다. 과거의 일반 테스트 금지보다 이번 사용자의 명시적인 API 재측정
요청을 이 실행 범위에 적용했다. 단위·회귀·화면 테스트는 생성하거나 실행하지 않았다.

- 입력: `tests/voice/testaudio.m4a`에서 직접 변환한 16 kHz mono PCM s16le WAV.
- 원문: **쌀과 콩을 깨끗한 그릇에 담아요**.
- 원본 SHA-256: `c1c2249e067b093f6728a71ddd71fb797cdb8243deeb3673b09583ea212536f1`.
- PCM SHA-256: `71dd0b1bd6929f0d921a630725c14f6f047cc27dcb978ac4c38b6d0a08770a1d`.
- PCM 136,612 bytes, 4.2666875초. 이전 Astra 측정과 바이트가 같다.
- 원문 SHA-256: `b9b4cab47fd1c9dafdd34167a478290a93e22b3fb2669e82907327aa45d9c3af`.
- 제출 202, 상태 조회 13회, 약 2초 간격. CORS origin 응답도 요청 origin과 일치했다.
- 결과 `RESULT_READY`, `ACCEPT`, **97.2점**, `RUBRIC_COMPUTED`.
- GPT dispatch 1회, `SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID`, failure=null.
- B2 archive와 AWS historySync는 모두 `COMPLETE`. Backend DB는 직접 조회하지 않았다.

| identity | Luna 실행 |
|---|---|
| jobId | `1fb3b257-6c17-496b-94da-30225e22471f` |
| requestId | `317b005c-6f5e-42b9-9e79-11e6ed37d520` |
| executionId | `d603bb25-a16a-4026-aaf8-e15604a84d1d` |
| clientAttemptId | `6e61ebb8-3dd9-4138-bed4-c6286682888f` |
| POST 시작 | 2026-10-24T12:04:40.016225Z |
| 서버 결과 공개 | 2026-10-24T12:05:04.618228Z |
| 클라이언트 완료 인지 | 2026-10-24T12:05:04.714235Z |

## Astra와 Luna 시간 비교

Astra는 같은 날 11:00 UTC 실행한 job
`48e208fd-6433-453d-b67c-4d7166a0aaa1`의 보존된 타이머를 비교 기준으로 썼다.
이번에 Astra를 다시 호출하지 않았다. 모델별 각 1회이며 평균·p95·품질 우열을
판정할 수 없다. 점수도 94.4→97.2로 달라져 출력의 완전 동일성을 뜻하지 않는다.

| 구간 | Astra low (초) | Luna low (초) |
|---|---:|---:|
| POST → 클라이언트 완료 인지 | 29.000 | 24.698 |
| POST → 서버 결과 공개 | 28.207 | 24.602 |
| DLPC 접수 → 서버 결과 공개 | 27.907 | 24.507 |
| 접수 POST 왕복 | 0.317 | 0.107 |
| 바깥 코어 전체 | 18.679 | 14.988 |
| MFA 호출 | 15.606 | 11.959 |
| GPT 포함 응답 생성 전체 | 7.492 | 7.796 |
| 최종 원본 preflight | 0.308 | 0.249 |

전체는 **4.302초(14.8%) 감소**했다. 그러나 GPT 포함 응답 생성은
**0.305초(4.1%) 증가**했다. 코어는 3.690초, 그중 MFA가 3.646초 줄었다.
클라이언트 polling 감지 차이도 0.793→0.096초다. 전체 단축을 Luna의 생성 속도
개선으로 귀속할 수 없다. MFA는 GPT보다 앞선 별도 단계다.

## Luna 결과 공개까지의 중복 없는 구간

| 구간 | 초 |
|---|---:|
| POST 시작 → DLPC 영속 접수 | 0.094608 |
| 접수 → RUNNING(큐) | 0.097787 |
| RUNNING → worker 시작(import 포함) | 1.262291 |
| worker 준비 | 0.009799 |
| 코어 전체 | 14.988493 |
| GPT 포함 응답 생성 전체 | 7.796423 |
| 최종 preflight | 0.249353 |
| 원본 artifact 영속 기록 | 0.013763 |
| 공개 결과 영속 기록 | 0.002150 |
| 부모 완료 감지·RESULT_READY 공개 | 0.087343 |
| 서버 결과 → polling 인지 | 0.095999 |
| 클라이언트 monotonic 합계 | 24.697777 |

왕복·업로드에는 AWS nginx·SSH tunnel·DLPC 처리가 합쳐져 있다. AWS 내부 시간은
서버 SSH/SSM 접근이 없어 분리하지 못했다. Windows 개인키 경로와 공개키만으로는
이 host에서 AWS shell 인증을 할 수 없다. 강제 명령 전용 tunnel 키는 shell에
사용하지 않았다.

## GPT 포함 응답 생성의 세부 시간

새 transport 계측은 request config 검사부터 전체 응답 body 수신까지의
monotonic 시간만 private worker-complete.json에 기록한다. prompt·생성 원문·
키·인증 헤더는 이 계측에 기록하지 않는다.

| 응답 생성 내부 | 초 |
|---|---:|
| 요청 JSON·고정 설정 검사·Request 객체 구성 | 0.000601 |
| HTTP 시작 → 응답 헤더 수신 | 7.565204 |
| 헤더 수신 → 전체 body 수신 | 0.000359 |
| OpenAI transport 전체 | 7.566164 |
| transport 바깥의 준비·파싱·검증·결과 구성 합계 | 0.230259 |
| GPT 포함 응답 생성 전체 | 7.796423 |

7.565초에는 DNS·연결/TLS·업로드·OpenAI 대기·reasoning/생성·네트워크가 함께
들어 있다. 비스트리밍 Chat Completions 호출이므로 첫 토큰 지연이나 reasoning과
출력 생성 시간을 따로 측정한 값이 아니다. body 수신 0.000359초를 모델의 전체
출력 생성 시간으로 해석하지 않는다. 토큰 수·reasoning token·캐시 hit도 수집하지 않았다.

0.230초에는 frozen artifact/Bridge 준비, 입력·CORE JSON 확인, WAV 측정,
prepare(바깥 준비와 dispatch 안에서 총 2회), envelope·후보 JSON 파싱,
schema/채점 검증, association·표현 결과 구성이 포함된다. 이 내부 항목은
개별 타이머가 없어 각각의 시간을 추정하지 않는다. prepare 2회가 GPT 2회를
뜻하지 않는다. 최종 preflight 0.249초는 위 7.796초 밖의 별도 단계다.
기존 Astra는 transport 계측이 없어 7.566초에 대응하는 순수한 transport 비교값이 없다.

## 코어 내부 및 운영 identity

CORE 누적 타이머 차이: decode 0.494초, Native 0.419초,
Native 종료→기대 음소 준비 0.211초, MFA 11.959초, prosody 0.014초,
prosody 종료→발음 정책 완료 0.683초, GOP 0.678초다.
코어 자체 14.727초와 producer 포함 바깥 코어 14.988초를 구분한다.
Native는 기존 cuda:0/float32 경로를 유지했다.

| pin | SHA-256 |
|---|---|
| core (기존과 동일) | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` |
| prompt (기존과 동일) | `ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d` |
| Luna H5 manifest | `1c62cd5f42625d80c2ca772d631ec3a2f9b6286d7e2a4b48e1b0b4f5a290899e` |
| Luna H5 lock | `8da3410fba23a1f0c802b436edca181a7fa0c3a14f2b455f4a26b9f7a0330c73` |

릴리스 경로는 `/workspace/intelligentai/validation/canonical_dlpc_gop_luna_20261024_v1_h5`다.
기존 배포 revision `791ca5496e04a10b6948c69dce97361ee4335f4c`와 bundle
`d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`는 유지하고
`operationalPatches.direct-luna-model-20261024`에 변경 파일 전후 SHA를 기록했다.

## 변경 파일·확인·남은 범위

`main`에서 `feat/gpt6-luna-timing-20261024` 브랜치를 만들었다. 원래 작업 디렉터리의
미완료 v6/기타 변경과 테스트 파일에는 손대지 않았다.

- `src/voice_coach/backend_analysis/canonical_harness.py`: Luna 고정 identity와 모델.
- `src/voice_coach/backend_analysis/native_execution.py`: 요청 승인된 Luna 실행 tuple.
- `src/voice_coach/backend_analysis/canonical_transport.py`: 선택 모델 검사·private 시간 계측.
- `src/voice_coach/direct_analysis/service.py`: direct 전용 LLM root 선택.
- `src/voice_coach/direct_analysis/worker.py`: identity 모델 전달·transport 타이머 보존.
- `docs/contracts/runpod_result_v4.schema.json`: 기존 private preflight에 Luna의 닫힌 tuple 추가.
- `deploy/dlpc/build_luna_h5.py`: 프롬프트와 코어를 보존한 model-only 불변 릴리스 생성.
- `deploy/dlpc/restore_luna_model.py`: 고정 SHA·정지 조건이 있는 지속 복구.
- `deploy/dlpc/restore_direct_resident.py`: Luna 후속 패치의 resident fix 포함 상태 인식.
- `deploy/dlpc/recover_on_start.py`: 명시 설치된 payload의 Luna 복구 hook.
- `docs/canonical-integration/README.md`: 현재 적용·측정 진입점.
- 이 문서: 실제 실행 근거와 비교.

운영에 위 5개 runtime 소스와 private preflight schema를 반영하고 지속 recovery
디렉터리의 helper/hook을 갱신했다. H5 builder는 새 릴리스를 실제 생성하는 데 사용했다.
Python AST/컴파일 가능 구문, JSON 파싱, diff 공백 확인과 실제 API 결과를 확인했다.
일회성 PCM·클라이언트 관측 JSON은 저장소 밖에 2개만 만들고 작업 종료 전 삭제했다.
운영 spool·artifact는 정식 분석·보관 자료로 유지한다.

남은 범위는 Backend 내부 handoff의 Luna tuple 승인·별도 배포, AWS 서버 내부
타이머 분리, 개발자 피드백 품질 판단이다. 이 작업의 commit/push/PR은 수행하지 않았다.
