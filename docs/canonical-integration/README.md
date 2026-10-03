# Canonical AI와 동결 LLM 서비스 통합

**2026-10-03 H5 선택 후속:** [H5 파생 하네스·양쪽 검증 계약·FE 짧은 총평](h5-application-20261003.md)을 구현하고 배포 묶음과 PR을 준비했다. 운영 전환은 아직 하지 않았으며 아래 S7 운영 관측과 구분한다.

**2026-10-02 17:55 KST 후속:** [testvideo1 실제 API 검증·수정](testvideo1-verification-20261002.md).
G2P 사전 환경 누락과 MFA zombie 회수를 수정·운영 배포했다. 실제 API 분석 51의 core는
`ACCEPT / ok`였으나 Backend journal JSON 응답 결함으로 후속 보관 전에 실패했다.
[Backend PR #95](https://github.com/voice-coaching/VC-BE/pull/95)는 준비됐고 운영 교체 승인 대기다.
전체 B2/callback E2E 완료는 아직 아니다. 아래 미수행 문장은 각 과거 시점의 기록이다.

**2026-10-02 16:13 KST 후속:** [core 실행기 수정·재배포와 FE UI 복원](incident-20261002-executor-and-ui.md).
analysis 48의 감시기 Python/pidfd 문제를 수정하여 런타임 5개 파일과 영구 overlay에 반영했다.
RunPod·Backend v4 접수 활성, AWS 설정 주소의 분석·TTS readiness 200을 확인했다.
실제 분석/GPT/callback E2E는 미수행이며 아래 readiness를 실제 분석 성공으로 해석하지 않는다.
FE는 사용자 결정에 따라 별도 v4 화면을 철회하고 기존 화면에 v4 계약만 연결하는
[PR #37](https://github.com/voice-coaching/VC-FE/pull/37)을 생성했다. 병합/FE 운영 배포는 미수행이다.

**최신 상태: [2026-10-02 서버 배포·제한 v4 접수 활성](rollout-execution-20261002.md#제한-v4-접수-활성화).**
RunPod 전체 실행기·부팅·writer 키와 Backend reader·검증·journal/apply를 운영 반영했다.
11:39~11:41 KST 양쪽 admission ON 및 공유 계약 9개 SHA 일치를 확인했다.
canonical migration은 **V33~V36**이다. 명시 opt-in 단독 음성 발음 학습만 활성 범위이며
기존 API·FE·TTS 계약은 유지한다. 실제 추론/GPT/B2/callback E2E는 미수행이다.
FE 테스트는 FE 개발자 책임이다. 아래 미연결/미배포·옛 migration 번호는 과거 기록이다.

이 문서는 AI·Backend·Frontend 담당자를 위한 실제 배포 대비 차이와 통합 진행 기록이다. **AI/LLM 동결 완료와 서비스 통합·배포 완료는 다르다.** 2026-09-30 20:54~21:03 KST에 RunPod 원본과 양쪽 저장소의 최신 원격 브랜치를 읽었다. 현재 운영은 여전히 legacy Seungun + 입술 P0/P1 + GPT이며 canonical Native/P1 + STEP7으로 전환되지 않았다.

최초 원본 조사 이후 [v4 구현 현황](callback-v4-plan.md#14-구현-진행-현황)의 schema·직렬화·보관 연결 코드와 Backend/Frontend 기반 코드를 추가했다. **전체 통합은 미완료**다. Backend receipt·결과 inbox/검증/저장·공개 조회/업무 조건·non-ACCEPT 재녹음 복구는 로컬 구현됐지만 canonical 프로세스 기동/취소·전송, FE 소비·후처리, 개발자 수용 및 운영 배포가 남아 있다. 내부 코드는 컴파일·정적 검토 범위이며 실제 실행 성공으로 보고하지 않는다.

2026-10-01 후속: Backend에는 v4 분기·비동기 callback 원문 검증 inbox·최종 결과/포인터 저장과 캐시 후처리, 요청별 프로필 전달·원자적 outbox/registry 발행, 현재 검증 결과 기반 학습 완료·시험 점수 불가 조건, 별도 `GET /api/v2/analyses/{analysisId}` public allowlist/actions를 추가했다. 같은 세션의 검증된 REJECT/INCONCLUSIVE는 기존 업로드 URL 요청에서 재녹음 상태로 복구하며 새 recording 등록·선택·analyze를 사용한다. 수신/worker 스위치와 공개 admission은 off이고 V32–V34도 미적용이다. 기존 영상 정리 hook은 자동 안전 검토의 별도 승인 요구로 보류됐으며 영상/기존 segments가 있는 v4 반영은 임시 차단된다. FE 소비·RunPod 실행/전송·실제 복구 수용·운영 전환은 여전히 남아 있다. 자세한 범위는 [실행 기록](rollout-execution-20261001.md)의 마지막 절을 따른다.

## 원본 위치와 불변 식별자

[PostgreSQL 복구 기록 구현](backend-journal.md): 사용자가 저장 경계 변경을 승인했다. V35·내부 journal API·서버 callback apply/ACK와 RunPod 원격 client를 로컬 구현했다. B2 원본 정본·기존 공개 API는 유지하며 SQLite는 운영 fallback이 아니다. 실제 DB 적용·전체 실행기/boot·FE 연결·키·배포·v4 활성화는 아직 남아 있다.

[실행기·저장소 gate](executor-storage-gate.md): frozen R launcher adapter와 Linux subreaper를 로컬 구현했다. 10/1 08:29 UTC 실제 Pod는 `/workspace` FUSE + container overlay였다. 이 조사로 위 PostgreSQL 변경을 선택했으며 별도 volume 승인 대기는 없다. boot/FE/운영 키·배포·활성화는 미완료다.

[callback 내구성·복구 구현](callback-delivery.md): private journal v3에 고정 event/body와 send claim/ACK를 보관하고, 정상 전송 및 재시작 시 원래 worker의 Backend heartbeat 재확인·ACK-only 확인 경로를 추가했다. Backend `result/ack`는 새 결과를 적용하지 않는다. 런타임 scheduling/executor 연결과 실제 운영 수용은 아직 남아 있으며 접수는 활성화하지 않았다.

[2026-10-01 계약 보존 리팩토링](refactoring-review-20261001.md): 로컬 변경 재검토, private JSON/schema codec·Backend 실패 분류 분리, 검증기 bundle 의존 파일 확인에 이어 두 검증 worker의 원본 읽기와 내부 HTTP bounded body 읽기를 공통화했다. 공개 API·schema·v4 접수 조건은 변경하지 않았다. 최신 미연결 구간과 배포 순서는 해당 문서의 2차 재검토를 따른다. 아래 원본 조사/최초 구현 설명은 그 시점의 기록이며 운영 배포 완료를 뜻하지 않는다.

[운영 제한 키·전체 통합·배포 계획](production-rollout-plan.md): 역할별 키 발급/주입, Backend·RunPod·FE 미연결 구현, 개발자 수용 후 제한 접수, 확대 및 원복 순서를 정의한다. 2026-10-01 계획 작성이며 새 운영 배포 관측이 아니다.

[2026-10-01 공개 FE 계약 재점검](frontend-contract-review.md): 기본 main은 `81b27dee88ae1c09bb4f9c2581be3552ac283137`로 아래 최초 dev 기준과 다르다. 공개 API·실제 완료/실패/입술/이력 소비 경로를 대조해 운영 계획의 호환성 gate를 보강했다. 운영 FE 배포 SHA 확인이나 E2E 수행은 아니다.

[2026-10-01 계획 실행 기록](rollout-execution-20261001.md): RunPod/AWS/DB 기준선, 제한 키 발급·보관, 근거 등록/검증 worker·receipt client 구현, AWS 독립 오프라인 검증기 설치 readiness. 최초 Native 401은 해소됐다. 서비스 키 주입·Backend/RunPod 전환·v4 접수 활성화는 아직 미완료다.

RunPod 관리 API에서 `s8hb5b0l5k4qlx` RUNNING과 SSH mapping을 재확인해 접속했다. 문서 파일명만으로 최신 운영 상태를 판단하지 않았다.

| 대상 | RunPod 절대 경로 |
|---|---|
| Canonical core R | `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda` |
| Frozen LLM S7 | `/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1` |
| 기존 GPT smoke·STEP8 | `/workspace/intelligentai/validation/canonical_llm_live_step8_20260928T163122Z_a1de23` |
| 최신 추가 감사 V2 | `/workspace/intelligentai/validation/final_handoff_revision_20260930T104815Z_276aba` |
| 실제 운영 A | `/opt/intelligentai/app` |

| 확인 대상 | 식별자 |
|---|---|
| R HEAD 및 `canonical-ai-pre-llm-20260928` tag target | `f27d6b31cec30b1174372e51c6bf5d19ee359247`, 작업 트리 clean |
| R 검증 source commit | `c566ccaf80c6a364ddbd02d6cbba2dd40bc05cfd`; 최종 metadata commit과 구분 |
| R `release_manifest.json` SHA-256 | `95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2` |
| S7 `contracts/llm/llm_contract_manifest.json` SHA-256 | `1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73` |
| S7 `contracts/llm/dependency_lock.json` SHA-256 | `d8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01` |
| S7 `llm_system_prompt.txt` SHA-256 | `362ccc1f15d2e166110cca52d90fd3d6e81ee85646e96eb7018d8b8278898714` |
| V2 `DEPLOYMENT_HANDOFF_KO_V2.md` SHA-256 | `25981869b2db77c4907d5ca4ab3cc23931368c15cbb2e3985a8da7cdb303de85` |
| 실행 API revision | `32d98e39767b56697bc7e58fb50e3e9d61432349` |
| 조사 시작 intelligentAI HEAD | `cfe89ebca8131d0be46cb78b2b7dfd505d65bf63`, main, clean |
| fetch한 VC-BE `origin/develop` | `b48db988e7485943097acaa4c71308ef306175a8` |
| fetch한 VC-FE `origin/dev` | `d54ceae5065880ccabd8f00ffe83500ab9077dfd` |

R manifest의 작은 파일 **86개**, S7 dependency lock의 **11개**를 실제 바이트 SHA와 대조해 불일치 0개였다. 대형 모델·미디어 전체 재해시나 동결 smoke 재실행은 하지 않았다. R의 오래된 `LLM_CONTRACT_STATUS=PENDING_SOURCE_AUDIT`는 pre-LLM 부모 시점의 metadata이며 자식 S7의 freeze를 취소하는 상태가 아니다.

21:02:44 KST RunPod 8082 인증 `/health/services`는 200 ready, executorReady=true, reasonCode=null, 위 운영 revision, `OPENAI / gpt-6-astra`였다. 이 조회는 canonical readiness나 실제 GPT 생성 확인이 아니다. AWS·TTS는 이번 범위에서 재검증하지 않았으며 이전 관측은 [운영 문서](../runpod-current-pipeline.md)를 따른다.

## 인계서 상태에서 확인한 차이

사용자 제공 요약의 core freeze / LLM offline freeze / 기존 non-production smoke / STEP8 ready는 각 단계의 근거다. 원격의 최신 V2 추가 감사 문서는 **`DRAFT_BLOCKED`, `BLOCKED_IDENTITY_SEMANTICS`, `FINAL_HANDOFF_REVISION_READY=false`**였다. 기존 STEP8 READY를 철회한 것이 아니라 서비스 소비 계약의 승인·검증이 남았다는 뜻이다.

이번에는 그 V2가 확보하지 못했던 Backend/Frontend source를 fetch한 immutable SHA로 대조했다. 소비 파일이 확인됐다고 DB 실제 데이터·웹 배포 버전·E2E까지 확인된 것은 아니다. 로컬 VC-BE 작업 브랜치는 `runpod이랑-AWS-연결`, HEAD `4d6d2c0…`이며 사용자 문서 삭제/추가가 많아 편집·checkout하지 않았다. VC-FE 로컬 `dev` HEAD `b59135af…`도 fetch된 `origin/dev`와 다르며 변경하지 않았다.

## 기존 배포와 새 계약의 차이

| 계층 | 기존 운영 | 동결 canonical 경로 | 통합 시 보존 |
|---|---|---|---|
| 발음 모델 | Seungun CUDA detector | Native XLS-R CPU/float32 + Refined P1 | 이름만 바꾼 같은 모델이 아님; 기존 경고를 병행 합산하지 않음 |
| 입력 gate | 기존 분석 오류 처리 | corruption, exact digital zero, demo content gate | 일반 VAD·잡음·near-silence 판별로 확대하지 않음 |
| 상태 | job COMPLETED/FAILED, 기존 coaching 상태 | ACCEPT/REJECT/INCONCLUSIVE/SYSTEM_FAILURE | job/core/adapter/generation 축을 분리 |
| 위치 | CTC nonblank span | MFA word/phone 위치 | `MFA_LOCATION_ONLY`, 초 단위 원본; 조음 길이·속도 진단 아님 |
| 역할 | 철자 기반 writtenRole | G2P expectedRole | field substitution 금지 |
| 근거 권한 | ACTIONABLE 및 REVIEW_ONLY 정책 | PRONUNCIATION_REVIEW, CLEAR_CONTRAST | 실제 발음 오류 확정으로 표현하지 않음 |
| 선택 | 반복·첫 등장 최대3; budget에 따라 추가 축소 | 반복·첫 등장 최대3; 이후 ID/순서/개수/guidance 고정 | 예산 초과면 호출 차단; 몰래 축소 금지 |
| ID | candidate-N, phone-N | attemptScope SHA를 포함한 scoped ID | 자르거나 짧은 ID로 바꾸지 않음 |
| GPT | visual v2 prompt, strict json_schema, allowedText | 동결 prompt, json_object, 일반 연습 범위 표현 | 두 validator·prompt·request를 섞지 않음 |
| 영상 | Seungun 후보에 결속한 관찰·중립적 재확인 | S7 NOT_CONNECTED, correctiveClaimsAllowed=false | 기존 CTC anchor를 MFA anchor로 자동 치환하지 않음 |
| 운율 | canonical prosody 미연결 | numeric/reference 근거 보존 | NO_APPROVED_GUIDANCE_MAPPING; 개인 교정·칭찬 생성 금지 |
| 점수 | null | null, 미보정/근거 부족 | logit/margin을 0..1 점수로 변환하지 않음 |

Native는 `slplab/wav2vec2-xls-r-300m_phone-mfa_korean@e26ff9dfb62169acf445d0060ef56863c018b20e`, MFA는 `MontrealCorpusTools/korean_mfa@0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd` historical `--use_g2p`다. P1 threshold·연산은 manifest가 가리키는 원본을 사용하며 보고서 숫자로 재구현하지 않는다. 원격 core를 로컬 `production.py`의 Seungun adapter로 대체했다고 보고하지 않는다.

## 실제 소비 코드에서 확인한 비호환

VC-BE `origin/develop`의 `src/main/java/org/example/voice/analysis/` 기준:

- `domain/model/AnalysisCoaching.java`: evidence ID에 `phone-[0-9]{1,4}`, 첫 evidence와 candidate의 대응, `CTC_NONBLANK_SPAN`을 요구한다. `writtenRole`과 단일 startMs/endMs 구조다. 새 ID·역할·MFA 시간 provenance를 그대로 넣을 수 없다.
- `controller/dto/RunPodAnalysisResultCallbackRequestDto.java`: 기존 v1/v2/v3 필드에서 worker result를 조립한다. canonical 전체 상태·reason·stage·provenance와 expression 결과의 정식 수용 필드가 없다.
- `application/AnalysisRunPodCallbackService.java`: 지원 schema v1/v2/v3, active request/execution, worker 소유, lease/deadline, event/payload digest 및 중복 ACK를 검사한다. 새 경로에서도 이 제어 경계를 유지해야 한다.
- `domain/entity/AnalysisResult.java`: analysis row가 recording 관계와 active request/execution을 보유하고 `coaching_document` JSONB를 저장한다. candidate별 전역 DB key로 저장한다는 근거는 발견하지 않았지만, 이것만으로 scoped ID를 제거해도 된다고 판단하지 않는다.

VC-FE `origin/dev` 기준:

- `src/lib/coaching.ts`: `voice-coaching.coaching-result.v1`만 표시 대상으로 받고 위치 role을 writtenRole로, replay를 CTC span으로 해석한다. `coachingSeekTime`은 MFA를 수용하지 않는다.
- `src/components/coaching-view.tsx`: 배열 순서대로 표시하고 React key는 candidateId다. 역할 설명은 “글자 위치”다. scoped ID는 그대로 key로 쓸 수 있지만 새 result schema/status/role/시간 소비는 별도 구현이 필요하다.
- `src/components/analysis-view.tsx`: `supportedCoaching` 결과에 따라 기존 화면으로 분기한다. 새 결과를 옛 실패·정상 화면으로 잘못 떨어뜨리지 않는 명시적 version 분기가 필요하다.

따라서 **ID regex만 넓히는 수정으로 끝나지 않는다**. 후속 사용자 결정은 **callback v3를 전면 개편하여 v4로 승계**하는 것이다. [callback v4 전환 계획](callback-v4-plan.md)에 상태·scoped ID·MFA·원본 보존·Backend/FE 변경과 원복 순서를 정리했다. 전환 중에는 v3 reader와 진행 중 작업을 수용하지만, 신규 canonical writer의 최종 계약은 v4다. 방향 선택 대기는 해소됐으며 세부 schema·공개 projection·보존 정책은 구현 리뷰 대상이다. 아직 기존 closed schema나 운영 callback은 변경하지 않았다.

## 이번에 구현한 AI 내부 연결 계층

새 코드는 운영 factory와 분리되어 있다. 실제 운영 호출을 전환하는 flag도 추가하지 않았다.

- [canonical_bridge.py](../../src/voice_coach/backend_analysis/canonical_bridge.py): 외부에서 pin한 core manifest·LLM manifest·dependency lock과 파일을 검증한 뒤 동결 bridge를 격리된 모듈 이름으로 로드한다. 기존 `bridge` import나 pyc로 대체하지 않는다.
- `FrozenCanonicalCoaching.express_admitted`: 이미 승인된 `AnalysisInput`, materialized audio, 서버 소유 canonical producer의 원본 결과 bytes와 prepared WAV를 받는다. 실제 음성 파일 SHA·크기, script SHA, canonical request/audio identity, 요청 workspace 내부 prepared WAV SHA를 재대조한다.
- Backend integer analysisId와 core UUID를 바꾸지 않고 request/execution/recording/content/core SHA와 함께 내부 association으로 보존한다. 신규 HTTP grant·서명·동의 증빙 필드를 만들지 않는다. 호출자 인증과 claim은 기존 서버 경계 책임이다.
- 동결 `Bridge.dispatch`를 그대로 호출한다. non-ACCEPT는 생성 0회, 허용 후보의 고정 순서·개수·guidance, 실패 시 deterministic fallback을 유지한다. 새 영상 근거나 운율 교정을 삽입하지 않는다.
- 반환값은 원본 core bytes, trusted binding, association, expression 및 prepare selection 결과를 별도 bytes로 보존하는 **private 객체**다. 새 [canonical_result.py](../../src/voice_coach/backend_analysis/canonical_result.py)는 이 원본과 검증 receipt를 대조하는 내부 v4 serializer이며 공개 사용자 DTO가 아니다. 메모리 보존은 영구 보관 완료가 아니다.
- [canonical_evidence_store.py](../../src/voice_coach/backend_analysis/canonical_evidence_store.py)와 [canonical_retention.py](../../src/voice_coach/backend_analysis/canonical_retention.py)는 비공개 B2 version 지정 업로드·readback과 manifest 조립 경계다. 실제 실행/Backend receipt 경로에는 미연결이며 업로드를 수행하지 않았다. 원본 보존·삭제 유예는 사용자 결정으로 무기한이다. 실제 저장 대상·설정은 [RunPod 문서](../../deploy/runpod/README.md#canonical-callback-v4-근거-보관-설정-미활성)를 따른다.
- [canonical_transport.py](../../src/voice_coach/backend_analysis/canonical_transport.py): 동결 요청 bytes를 재조립하지 않고 고정 OpenAI HTTPS endpoint로 전달한다. 모델·JSON mode·120초·1200 output·low·store=false를 검사하고 redirect/env proxy/자동 retry를 사용하지 않는다. 응답은 128 KiB로 제한하고 provider 응답 본문·키·대본을 예외로 노출하지 않는다.

STEP7 dependency인 Python 3.12 + jsonschema 4.25.1 환경을 요구한다. 현재 app의 jsonschema 4.26.0에 자동으로 얹거나 기존 환경을 downgrade하지 않는다. 별도 pinned worker의 packaging/기동 연결이 남아 있다. 공식 문서는 JSON mode와 schema 강제가 다르며 refusal/출력 중단 처리가 필요함을 설명한다. 이번 구현은 동결 계약을 존중해 JSON mode를 strict schema로 바꾸지 않았다. [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

이 진입점은 임의 client가 core JSON과 일치하는 SHA를 보내는 공개 endpoint가 아니다. producer는 해당 admitted job을 새로 실행한 서버 코드여야 한다. 단순 hash 일치가 사용자 소유권이나 producer 인증을 대신하지 않는다.

## 아직 연결하지 않은 구간과 결정

1. **v4 결과 계약 구현:** [전환 계획](callback-v4-plan.md)에 따라 네 core 상태와 reason/stage/provenance, 원본 scoped ID·G2P role·MFA word/phone 위치, adapter/generation 결과를 별도 수용·저장·공개 투영한다. 비공개 원본 저장의 실제 설정과 수명은 배포 전에 확정해야 한다. v3 summary/failureReason에 JSON을 숨기지 않는다.
2. **Canonical executor:** R `scripts/run_release.py`의 READY → batch → S7 호출을 기존 claim/heartbeat/cancel/deadline 안에 연결해야 한다. 원본 launcher와 MFAClient는 별도 process session을 생성한다. 기존 executor group만 죽이면 모두 종료된다고 가정할 수 없다. 해당 실행의 자식 추적·제한 workspace·socket 정리·정상 종료가 필요한 미구현 항목이다. 미검증 launcher를 서비스에 바로 꽂지 않았다.
3. **Backend:** develop 기반 별도 작업에서 DTO/schema/저장·reader·상태 표시를 함께 개정하고 PR로 인계한다. 사용자 작업 브랜치를 수정하지 않는다. DB/공개 계약 변경은 명시적으로 리뷰한다.
4. **Frontend:** 지원 version 분기, 네 core 상태·LLM fallback 구분, MFA 위치 재생과 G2P role 설명을 구현한다. 무경고·판단 보류를 정상/만점으로 바꾸지 않는다.
5. **운영 전환:** 개발자 non-production E2E와 원복 확인 후 별도 배포한다. 기존 분석·TTS·8082 gateway와 동결 R/S7/S8은 이번에 변경하지 않았다.

기존 single GPT smoke PASS는 일반 성공률·context 여유·SLA나 이번 코드 QA를 보장하지 않는다. content gate의 일반 잡음/near-silence 한계, deletion 표시 억제, CPU authoritative, MFA 장기 soak 미검증, 자유 문장 의미 검증 한계를 그대로 유지한다.

## 확인 및 인계 상태

수행: 읽기 전용 원격 조사, manifest 정적 SHA 대조, Backend/Frontend fetch·source 대조, 로컬 연결 코드·문서 작성, Python 구문 및 문서 정적 확인. 원본 frozen validator·prompt·사례·모델·threshold는 수정하지 않았다.

미수행: 새 canonical 모델/MFA 실행, bridge 실제 실행, GPT API 호출, 자동 회귀 테스트, E2E, 품질 QA, DB migration 적용, commit/push/PR, 운영 배포. **통합 완료가 아니라 단계별 로컬 구현 상태**다. Backend/Frontend 변경은 원격 기준 별도 worktree에 작성했으며 기존 사용자 checkout은 변경하지 않았다. 후속 구현의 확인 범위와 미연결 구간은 [v4 현황](callback-v4-plan.md#14-구현-진행-현황)을 따른다.
