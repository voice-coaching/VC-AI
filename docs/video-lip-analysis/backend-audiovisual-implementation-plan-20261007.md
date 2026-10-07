# 음성·영상 분석 Backend 통합 구현 계획

2026-10-07. **계획 제안이며 미구현·미배포.** 기존 [계산·병렬 처리 설계](canonical-parallel-lip-scoring-20261007.md)와 [API 연결 검토](training-session-analyze-review-20261007.md)를 확장한다. 충돌 시 이번 문서의 접수·저장·조회 범위를 우선하고, 계산식과 보정 제한은 기존 설계를 따른다.

## 1. 범위와 기준

- 공통 시작점: `POST /api/training-sessions/{sessionId}/analyze`. 최종 선택 recording의 미디어로 분석한다. analyze body에 영상 URL·점수·시간 구간을 받지 않는다.
- 영상 업로드/등록 → 선택 → 접수 → DLPC 병렬 분석 → 근거 검증 → 결과 저장 → 상세 조회·FE 표시를 하나의 구현 단위로 다룬다.
- Backend는 현재 Spring/Java/PostgreSQL 구조를 유지하고 AI는 FastAPI/service/adapter 구조를 유지한다. `.codex` 계층 원칙을 적용하되 기존 파일 구조를 임의 변경하지 않는다.
- 검토 기준: Backend `6edbd0ffb777a34ec1272dc8a25fec2e334f779d`, AI `94164ad532da687b9ba7d2db1c1bca4dd2dc4088`. Backend HEAD/clean 상태를 이번에 재확인했다. 원격 최신·운영 서버·FE 현재 소스는 새로 확인하지 않았다.
- 구현 시작 시 양쪽 대상 브랜치의 최신 SHA와 migration 번호를 재확인한다. 아래 새 이름·버전·필드는 **구체적인 제안**이며 기존 API 명세나 운영값이 아니다.

### 완료의 정의

같은 영상에서 준비한 PCM을 Seungun에 한 번 전달하고, 병렬 영상 특징과 MFA 음소 구간을 합쳐 GPT에 한 번 전달한다. 결과의 입술 관찰·점수·피드백이 동일 request/execution/recording에 결속되어 Backend 저장 후 조회된다. 영상 분기만 실패하면 유효한 음성 결과를 유지한다. 음성 종합점수와 입술 점수는 분리한다.

## 2. 계약과 호환 방식

기존 v5를 그대로 변형하지 않고 새 계약을 추가한다. 다음 번호는 구현 전 원격 충돌 확인 후 확정한다.

| 항목 | 기존 음성 요청 | 음성·영상 신규 제안 |
| --- | --- | --- |
| public analyze URL/body | 기존 유지 | 기존 유지 |
| `X-Analysis-Result-Schema` | 생략 또는 result v5 | `voice-coaching.runpod-analysis-result.v6` 명시 |
| internal request | request v3 | `voice-coaching.runpod-analysis-request.v4` |
| execution profile | 기존 handoff v5 | `CANONICAL_AUDIOVISUAL_20261007_V6` |
| result / handoff | result v5 / handoff v1 | result v6 / handoff v2 |
| 공개 상세 view | canonical-analysis-view v2 | canonical-analysis-view v3 |

- 초기 전환에서 헤더 생략은 기존 음성 v5를 유지한다. 영상 recording + v5는 silent audio-only 처리하지 않고 명시적 미지원 오류를 반환한다. v6는 audio-only도 지원하되 `visual.status=NOT_PROVIDED`로 반환한다.
- FE는 capabilities가 새 profile/schema와 VIDEO admission을 제공할 때 v6를 요청한다. 헤더만으로 readiness/소유/음질 검사를 우회하지 못한다.
- 기존 `GET /api/v3/analyses/{analysisId}`에서 저장된 profile에 맞는 view를 반환하도록 확장한다. URL의 v3와 payload schema v3는 다른 개념이다. 구 FE는 새 profile을 생성하지 않게 막고 알 수 없는 view를 잘못 렌더링하지 않는다.
- `result-contract`는 현재처럼 result v5를 상수 반환하지 않고 **해당 execution에 저장된 계약**을 반환한다. analyze 응답은 기존 `analysisId/status/requestedAt`를 유지한다.
- 기존 과거 결과 byte/hash를 새 schema로 재직렬화하거나 v6로 backfill하지 않는다. v5 reader/validator도 보존한다.

### 입력 media receipt 제안

Backend가 만든 immutable `mediaPreparation` 객체에 `schemaVersion`, `sourceSha256`, `canonicalVideoSha256`(영상 없으면 null), `canonicalPcmSha256`, `normalizationRevision`, audio/video 시작 시각·time base, 회전 적용 정보, `audioToVideoTimeMap`, receipt digest를 담는다. 시각 단위와 원점은 schema에서 고정한다.

- PCM은 기존 Backend normalizer가 만든 것을 그대로 사용한다. AI는 재추출 대신 objectKey/MIME/크기/SHA와 receipt를 검증한다.
- VFR 프레임의 실제 PTS는 AI decoder가 수집하고 동일 canonical video digest에 결속한다. Backend는 전체 frame 배열을 HTTP request에 넣지 않는다.
- `audioToVideoTimeMap`은 변환 근거가 있을 때만 유효하다. offset을 모르면 0을 넣지 않고 sync 상태를 보류한다. 미디어 identity 누락·충돌은 거절하고, identity는 확정됐으나 정밀 동기화가 불가한 영상은 음성 처리 후 `UNSCORABLE`로 남길 수 있다.
- 사용자 입력 시간표·외부 arbitrary URL은 신뢰하지 않는다. 기존 사용자 인증/소유 검사, 양방향 서버 인증, 저장소 prefix 및 파일 무결성 검사를 유지한다. 내부 HTTP에 동의 증빙·grant·서명을 추가하지 않는다.

## 3. Backend 접수 변경

### 처리 순서와 transaction

1. `RecordingUploadService`와 `VoiceRecordingService`에서 기존 소유·업로드 intent·영상 등록 계약을 유지한다. normalizer가 MP4/PCM/receipt를 반환하고 writer가 recording과 receipt를 함께 등록한다. 저장 실패 시 기존 object 정리 경로에 양쪽 자산을 포함한다.
2. `TrainingAnalysisSchemaAdmissionService`는 지원 schema와 전체 readiness를 검사한다. 실제 media/profile 적격성은 선택 recording을 읽은 application 경계에서 판단한다.
3. `TrainingAnalysisRequestService`는 세션·선택 recording을 기존 방식으로 잠그고 소유·PASS·발음 연습·중복 검사 후 profile을 검증한다. retry에도 동일 음질·media 검사를 적용한다.
4. 같은 transaction에서 PENDING 생성/실패 재시도 전이, 새 request/execution ID, deadline, 미디어 snapshot, outbox를 기록한다. 외부 HTTP/ffmpeg/영상 계산을 이 transaction 안에 넣지 않는다.
5. `CanonicalRequestScope`는 기존 강좌·시험 제외 조건을 유지하고 **profile별** audio/video 지원을 판단한다. publisher의 별도 `visualInput != null` 차단을 중복 정책으로 남기지 않는다. action/rerecord policy도 같은 판단을 사용한다.
6. dispatcher는 저장된 request byte를 같은 identity로 재전송한다. media 또는 schema를 변경해서 동일 execution에 재전송하지 않는다. 사용자 retry는 새 execution과 snapshot을 만든다.

### 접수 오류 제안

| 조건 | 응답 원칙 |
| --- | --- |
| 인증·소유·선택·음질·중복 실패 | 기존 상태 코드/오류 계약 유지 |
| 알 수 없는 schema·헤더 중복 | 기존 입력 오류 유지 |
| 영상 recording에 v5 요청 | 422 `ANALYSIS_MEDIA_PROFILE_UNSUPPORTED` 신규 제안; 재시도 반복 대신 지원 client 안내 |
| 신규 영상의 receipt 미생성/identity 불일치 | 422 `ANALYSIS_MEDIA_BINDING_INVALID` 신규 제안; 재등록 필요 |
| 옛 영상 recording에 검증 가능한 receipt 없음 | 가짜 snapshot backfill 금지. 명시적 재준비 또는 재업로드 전에는 신규 영상 분석 접수 불가 |
| schema/verifier/reference 설치 또는 저장 공간 준비 미완료 | 503 `ANALYSIS_INTEGRATION_UNAVAILABLE`; transaction rollback, PENDING 방치 금지 |

## 4. AI 실행과 Backend 수신 계약

- `http_models.py`에 새 profile/request parser를 추가하고 `http_runtime.py`의 canonical 영상 일괄 거절을 profile별 admission으로 바꾼다. recovery 시에도 같은 계약 검증을 적용한다.
- 상위 audiovisual service가 준비 PCM을 음성 전용 core에 넘기고 독립 영상 feature worker를 시작한다. core의 음성 전용 assertion을 무조건 삭제하지 않고 adapter로 역할을 분리한다.
- MFA 완료 후 expected index·실현 label·역할·문맥·span과 frame PTS를 join한다. 점수와 허용 observation/action ID를 서버가 산출한 뒤 frozen H5에 결속한다. GPT는 서버 점수를 변경하지 않는다.
- timeout 예산은 `execution deadline - 현재 시각 - GPT/인계 예약 시간`에서 파생한다. 영상 단계 상한을 별도 설정하고 실제 측정으로 고정한다. 아직 측정하지 않은 고정 성능 목표를 운영값으로 쓰지 않는다.
- 영상 예외는 typed branch result로 회수한다. 늦은 영상 결과로 이미 생성·저장된 GPT 피드백을 변경하지 않는다. cancellation/deadline에는 자식 프로세스 종료를 확인한 뒤 임시 파일을 정리한다.

### 추가 handoff artifact

기존 CORE/BRIDGE_RESULT/SELECTION_PROJECTION/BINDING/ASSOCIATION에 다음을 추가한다.

| kind 제안 | 내용 | 조건 |
| --- | --- | --- |
| `MEDIA_RECEIPT` | 정규화·동일 시도·시간 변환 근거 | v6에 필수. audio-only도 PCM 준비 근거 포함 |
| `VISUAL_EVIDENCE` | 영상 상태, MFA anchor, 선택된 실제 PTS/특징, 품질·참조·보정 revision, 비교값/점수/null 사유, 허용 observation/action | 유효한 영상 요청에서 필수. 실패 시에도 typed 사유 envelope를 남김 |

미디어 검증 전에 종료된 전역 실패에는 기존 global failure와 같은 최소 결과를 허용하고 visual evidence를 위조하지 않는다. 일반 영상 결과에서는 artifact 누락을 조용히 성공 처리하지 않는다. artifact별 schema/최대 bytes/필수 조건, 총 reservation, verifier 입력 한도를 함께 정의한다. 프레임 영상·MP4·전체 무제한 landmark 배열을 DB handoff에 넣지 않는다.

## 5. DB·저장·B2 설계

### 추가 migration 제안

기존 PostgreSQL V33/V39 및 후속 migration을 수정하지 않고 최신 번호 뒤에 새 migration을 추가한다.

| 대상 | 변경 제안 | 무결성/호환 |
| --- | --- | --- |
| 신규 `recording_media_preparations` | recording_id PK/FK, schema_version, receipt JSON, receipt_sha256, created_at | normalizer 완료 시 생성. FK cascade 삭제는 recording의 기존 실제 삭제 정책과 맞추며 임의 도입 금지. receipt 내용 갱신 대신 새 recording 사용 |
| 신규 `analysis_execution_media` | execution_id PK/FK, media_type, audio_sha256, video_sha256(nullable), receipt_sha256, snapshot JSON | request 생성 transaction에 immutable insert. 영상이면 video digest 필수; audio-only면 null. 기존 execution은 row 없음 허용, v6는 필수 |
| `analysis_canonical_executions/results` | profile/request/result 조합 CHECK에 v6 tuple 추가 | 과거 tuple 유지. request hash·활성 execution fencing 보존 |
| handoff/archive artifact tables | 새 kind CHECK, artifact/총량 제한 및 schema 조합 확장 | 기존 immutable bytes·unique(handoff_id,kind)·digest 불일치 거절 유지 |

별도 음소 점수 테이블은 우선 만들지 않는다. 검증된 canonical result와 version 있는 visual evidence를 정본으로 하고 공개 DTO는 projection한다. 점수를 옛 `analysis_segments`에 억지로 변환하지 않는다. FK/삭제·조회 인덱스·CHECK·migration 호환은 Backend `docs/database/schema.md`에 구현과 함께 기록한다.

### 검증·노출·commit 순서

1. handoff receive/stage는 기존 STAGING → RECEIVED → VERIFYING 흐름으로 byte/hash·필수 artifact·실행 소유를 검사한다. 동일 identity/동일 bytes 재전송은 멱등 처리하고 다른 bytes는 충돌이다.
2. 네트워크·추론 없는 offline verifier가 schema뿐 아니라 recording/execution snapshot ↔ video/PCM digest ↔ MFA index/span ↔ PTS/receipt ↔ reference/calibration ↔ GPT observation/action ID를 대조한다. 수치가 finite/범위 내인지, 보류 점수가 null인지 검사한다.
3. 현재 result-first 경로의 `CanonicalDeliverySpool`/`CanonicalPublishedResults`도 동일 검증 결과로만 노출한다. DB에 들어갔다는 이유나 브라우저에 먼저 보여주기 위해 검증을 생략하지 않는다. 기존 verified preview의 SAVING/RETRYING 의미를 유지한다.
4. `CanonicalCallbackCommitter`는 짧은 transaction에서 현재 execution/선택/취소 fence를 재확인하고 canonical result·완료 상태·effect·archive job을 함께 기록한다. 영상 존재 자체를 차단하던 조건은 새 계약 검증과 lifecycle 준비 조건으로 대체한다.
5. 기존 segment 존재 조건은 별도 유지한다. 새 영상 지원을 이유로 legacy segment를 지우지 않는다. legacy segment가 남은 분석은 명시적 이행 정책이 생기기 전 제외한다.
6. B2 archive는 commit 후 비동기로 실행한다. archive 장애는 이미 검증된 결과를 사용자 발음 실패로 바꾸지 않는다. status/retry와 운영 지표에 분리 기록한다. `CanonicalArchiveWriter`의 kind allowlist와 복구 validator를 새 artifact에 맞춘다.

### 미디어 보관·삭제

- 원본 녹음 저장소와 B2 근거 archive는 다른 역할이다. raw 사용자 영상을 B2 일반 백업에 자동 복사하지 않는다. B2에는 이번 기능에 필요한 제한된 근거 JSON을 기존 canonical archive 계약에 따라 보관한다.
- 현재 canonical 근거 저장은 immutable/장기 보관 제약을 갖는다. 얼굴 프레임·전체 궤적을 그 제약에 새로 넣지 않고, 재검증에 필요한 최소 음소 특징/참조만 설계한다. 특징도 사용자 자료로 취급한다.
- 분석 완료만으로 recording MP4를 즉시 삭제하지 않는다. 기존 recording 삭제/사용자 탈퇴/cancel 정책과 outbox를 유지하며 활성 worker/재시도 사용 중에는 물리 삭제를 지연한다. 임시 decode 산출물은 작업 종료 후 정리한다.
- 삭제·선택 변경·탈퇴 시 결과 공개는 즉시 기존 visibility fence로 차단한다. 별도 보관 근거의 삭제 가능 여부는 기존 immutable retention 계약과 명시적으로 대조한다. 임의 TTL이나 cascade를 정하지 않는다. 구현 인계 시 객체별 저장소/사용 기간/삭제 책임표가 확정되지 않으면 영상 admission을 열지 않는다.

## 6. 상세 조회·FE 계약

`GET /api/v3/analyses/{analysisId}`의 새 view에서 아래 `canonicalAnalysis.visual`을 제안한다. 필드와 null 의미는 schema에 고정하고 기존 v5 view는 그대로 읽는다.

| 필드 | 타입/의미 |
| --- | --- |
| `status` | NOT_PROVIDED / OBSERVED / REFERENCE_ONLY / SCORED / UNSCORABLE / FAILED / TIMEOUT |
| `correctiveClaimsAllowed` | boolean; 검증된 action이 있는 경우만 true. 각 phone/action도 별도 검사 |
| `score`, `scoreKind` | nullable number(0–100), enum NONE/REFERENCE_SIMILARITY/CALIBRATED. 일반 발음 정답 확률로 표시 금지 |
| `coverage` | target/visuallyEligible/observed/scored count와 nullable 비율. 분모 0이면 null |
| `phoneAssessments[]` | expectedIndex, expectedPhone, role, audioStart/EndMs, videoStart/EndMs(nullable), 상태·점수·scoreKind·reasonCodes·observations·허용 actions |
| `reasonCodes[]` | NO_FACE/LOW_VISIBILITY/SYNC_UNCERTAIN/ALIGNMENT_AMBIGUOUS/REFERENCE_MISSING/CALIBRATION_MISSING 등 version 있는 사유. 원시 오류 문자열 금지 |

- 배열 순서/index는 MFA 원본 anchor에 결속한다. 점수 없음은 0점이 아니다. calibrated와 research/reference similarity를 같은 집계에 섞지 않는다.
- `coaching`의 기존 음성 feedback은 유지하며 별도 입술 feedback/근거 ID 연결을 추가한다. observation은 관찰된 사실, action은 검증된 교정으로 구분한다. GPT 실패 시 서버 관측과 기존 deterministic fallback·사유를 반환한다.
- 공개 응답에 storage objectKey, private receipt/manifest bytes, raw frame 좌표, 서버 경로, 내부 token을 노출하지 않는다.
- `CanonicalPublicProjection`의 엄격 DTO parser, `CanonicalCommittedResultReader`, query/await service, verified preview 모두 새 view를 지원한다. GET 중 B2 다운로드·GPT·영상 추론을 실행하지 않는다.
- `/analysis/status`와 세션 `/analysis`는 기존 진행·점수 요약으로 유지한다. FE는 analyze의 analysisId → result-contract → 상세 view를 읽는다. FE 저장소의 실제 uploader/parser/component 경로는 구현 첫 단계에 확인·기록한다.

## 7. 상태·실패 판단

| 상황 | 작업/음성 결과 | visual / 처리 |
| --- | --- | --- |
| 음성만 제공 | 기존 음성 처리 | NOT_PROVIDED, score null |
| 양쪽 성공·보정 적격 | 기존 완료 상태 | SCORED, 근거에 연결된 입술 feedback |
| 관찰 성공·참조 또는 보정 부족 | 유효 음성 완료 유지 | OBSERVED/REFERENCE_ONLY/UNSCORABLE; null/연구용 비교 구분 |
| 얼굴 없음·가림·정렬/동기화 불확실 | 유효 음성 완료 유지 | UNSCORABLE와 사유; 임의 교정 금지 |
| 영상 worker 오류·분기 timeout | 유효 음성 완료 유지 | FAILED/TIMEOUT; 전체 분석 실패로 덮지 않음 |
| 음성 core 실패·전체 deadline | 기존 실패 계약 | 독립 입술 발음 판정으로 성공 전환 금지 |
| digest/다른 시도/변조 근거 | 접수 또는 verification 거절 | 무결성 문제를 정상 영상 fallback으로 숨기지 않음 |
| 사용자 cancel·새 retry·선택 변경 | 기존 취소/세대 fence | 늦은 결과 저장·표시 금지, child 종료 후 정리 |
| Backend 일시 저장 실패 | verified preview/persistence 상태 유지 | 같은 handoff로 재전송; 재추론/GPT 재호출 금지 |

## 8. 파일별 작업 묶음

아래 Backend 파일은 `src/main/java/org/example/voice/` 기준이며, 새 파일은 제안으로 표시한다.

| 묶음 | 대상 | 완료 산출물 |
| --- | --- | --- |
| BE 미디어 | `training/application/{RecordingUploadService,VoiceRecordingService}.java`, `training/infrastructure/storage/FfmpegS3RecordingMediaNormalizer.java`, recording reader/writer, 새 media preparation model/repository | 동일 MP4/PCM receipt 생성·저장·조회와 정리 |
| BE 접수 | `TrainingAnalysisRequestService`, `TrainingAnalysisSchemaAdmissionService`, `RunPodOutboxAnalysisJobPublisher`, `RunPodAnalysisJobRequest`, `CanonicalRequestScope`, `CanonicalExecutionRegistryImpl`, capabilities/action/rerecord policy | v5/v6 분기, immutable snapshot/outbox, retry 일치 |
| BE 수신·검증 | `CanonicalHandoffDocument/Store/Worker`, `CanonicalCallbackDocument`, `CanonicalSemanticVerifier`, offline verifier 배포 패키지, `RunPodContract` | 새 artifact/schema/semantic 확인, 과거 결과 호환 |
| BE 저장·archive | `CanonicalCallbackCommitter`, `CanonicalDeliverySpool/Worker`, `CanonicalPublishedResults`, `CanonicalArchiveWriter/Worker`, recording deletion scheduler/dispatcher, 새 migration | 검증·노출 fence, 저장 멱등성, B2 재시도/복구 |
| BE 조회 | `CanonicalAnalysisView`, `CanonicalPublicProjection`, `CanonicalCommittedResultReader`, `CanonicalAnalysisQueryService`, `CanonicalResultAwaitService`, `CanonicalHandoffAnalysisController`, `CanonicalResultContractController` | 신규 view와 실제 profile 반환, 기존 조회 보존 |
| AI | `backend_analysis/{http_models,http_runtime,canonical_executor,canonical_producer,canonical_bridge}.py`, 새 audiovisual service/ports, visual adapter·MFA matcher·scorer, frozen H5/lock, handoff/archive adapter | 1회 음성·1회 GPT, 병렬 feature, 근거 결속 |
| 공통 계약·문서 | 양쪽 `docs/contracts/`의 새 버전, API specification/endpoints, database/architecture 문서 | 필드·nullable·enum·error·DB/lifecycle 문서와 실제 구현 일치 |
| FE | 실제 저장소 확인 후 uploader/selection/analyze client/contract parser/detail renderer 지정 | 영상 보존·v6 요청·음성/입술 구분·부분 실패 표시 |
| DLPC 배포 | immutable bundle/manifest, reference/calibration pins, Backend verifier pins, 복구/start hook/B2 manifest | 재부팅 시에도 같은 media/GPT/schema/reference 조합 재현 |

## 9. 순서·검증·롤백

| 단계 | 작업 | 다음 단계 조건 |
| --- | --- | --- |
| P0 계약 고정 | FE 실제 호출 조사, 양쪽 SHA/버전 번호, reference manifest, 필드/한도/보관표 확정 | 미디어·상태·오류·retention 계약이 서로 일치 |
| P1 기반 확장 | additive DB migration, dual schema reader/verifier, media receipt와 snapshot | 신규 admission OFF 상태에서 구 v5 경로 유지 |
| P2 AI/저장 연결 | 병렬 service·점수/GPT, handoff·preview·commit·archive·상세 projection | 새 근거의 생성부터 조회까지 코드 경로 연결 |
| P3 FE/개발자 QA | capabilities/신규 헤더/화면, 아래 시나리오 개발자 확인 | 실제 품질·오류·자원 결과 기록 |
| P4 제한 활성화 | 승인된 bundle 배포, 양쪽 readiness/schema hash 확인, 영상 admission ON | 실제 영상 1건의 요청→결과 조회→archive 증거 확보 |
| P5 운영 전환 | 대기·실패·자원 관측 후 지원 범위 확대 | DLPC 재기동 복구·rollback 묶음 확인 |

개발자가 확인할 시나리오: 기존 WAV 성공, 참조와 다른 영상 성공, 영상 미관찰·미지원 음소·참조 없음, digest 불일치, VFR/시작 offset, 중복 handoff, timeout/cancel/retry/선택 변경, B2 중단·DB 재시도·DLPC 재시작, 구 결과 조회. self-reference 높은 점수는 품질 통과 증거로 사용하지 않는다.

측정: audio/video branch 시작·종료와 중첩, MFA/GOP/video/GPT/handoff 각 소요, 추론·GPT dispatch 횟수, peak RSS/CPU/GPU·TTS 영향, 관찰/채점 coverage, 영상 fallback 비율. 4 CPU 환경에서는 기존 전역 분석 동시성 예산을 유지하고 thread 과할당 여부를 실제 측정한다.

롤백은 먼저 v6 admission을 닫고 실행 중 작업을 drain 또는 명시적으로 취소한다. v6 결과를 읽을 수 있는 dual reader/verifier/DB 확장은 유지하고 audio v5 producer로 되돌린다. 신규 결과가 저장된 뒤 구 v5-only Backend로 단순 downgrade하거나 migration을 파괴적으로 되돌리지 않는다. 원본 영상/근거를 삭제하는 롤백은 하지 않는다.

AGENTS에 따라 자동 회귀 테스트·실제 품질 QA는 이번 계획 작업에서 실행하지 않았다. 구현 단계의 정적 schema/컴파일 확인과 개발자의 실제 추론·FE·품질 QA를 구분한다. AI main·Backend develop PR 및 FE 대상 브랜치를 확인해 인계하며, PR 작성과 운영 배포는 각각 별도 완료 상태로 기록한다.

## 2026-10-07 구현 후보 반영

현재 구현 사실은 [진행 기록](implementation-progress-20261007.md)과 [v6 인계](audiovisual-v6-handoff-20261007.md)를 따른다. 초안과 달리 첫 v6 후보는 영상 전용이며, 실제 MFA 자음 위치만 사용한다. 참조/보정 catalog는 비어 있어 관찰 전용이다. 아래 계획의 전체 음소·보정 점수·운영 전환까지 완료했다는 의미가 아니다.
