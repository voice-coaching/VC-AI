# 세션 analyze API의 음성·영상 연결 검토

2026-10-07. **상태: 소스 정적 검토와 계획 보완. 구현·운영 호출·배포 미수행.**

## 결론과 확인 범위

`POST /api/training-sessions/{sessionId}/analyze`를 음성·영상 분석의 공통 시작점으로 유지할 수 있다. 다만 현재 구현에서는 영상 요청이 차단된다. [병렬 분석 계획](canonical-parallel-lip-scoring-20261007.md)의 AI 변경과 함께 Backend 접수·결과 검증·저장·조회 및 FE 표시 변경까지 적용해야 한다.

이 API는 파일 업로드나 최종 피드백 반환 API가 아니다. 로그인 사용자가 소유한 세션의 **최종 선택 recording**을 분석하고 `analysisId`, `status`, `requestedAt`을 반환한다. 파일/영상 URL을 analyze body에 추가하는 것은 현재 계약이 아니다.

- Backend 검토 revision: `6edbd0ffb777a34ec1272dc8a25fec2e334f779d` (`backend-dlpc-20261006`, 검토 시 clean).
- AI 검토 revision: `94164ad532da687b9ba7d2db1c1bca4dd2dc4088` (`dlpc-ai-20261005`).
- 운영 서버·원격 최신 HEAD는 이번에 재조회하지 않았다. FE 소스도 이번 검토 범위에 포함하지 않았다. 아래는 해당 revision의 구현 사실이다.

## 1. 현재 요청 경로

| 순서 | API / 구성요소 | 확인된 동작 |
| --- | --- | --- |
| 1 | `POST /api/training-sessions/{sessionId}/recordings/upload-url` | 파일명·MIME·크기로 업로드 URL 발급 |
| 2 | 발급 URL로 파일 업로드 → `POST /api/training-sessions/{sessionId}/recordings` | objectKey 등 등록. 영상은 기존 공개 등록 계약의 영상 처리 동의 정보 필요 |
| 3 | `FfmpegS3RecordingMediaNormalizer` | canonical MP4 생성 후 **그 MP4에서** 16k mono PCM WAV 추출. 양쪽 objectKey·SHA·크기를 recording에 연결 |
| 4 | `PATCH /api/training-sessions/{sessionId}/recordings/{recordingId}/select` | 음질 PASS인 recording 선택 |
| 5 | `POST /api/training-sessions/{sessionId}/analyze` | body는 `accepted`, `policyRevision`. 소유·선택·음질·발음 연습 범위·중복 검사 |
| 6 | `TrainingAnalysisRequestService.toWorkerRequest` | 선택 recording에 visual object가 있으면 `visualInput` 구성 |
| 7 | `RunPodOutboxAnalysisJobPublisher` | **현재 여기서 영상 차단.** 통과 요청은 outbox → HTTP client로 전달 |
| 8 | AI `POST /v1/analysis-jobs` | request v3 / result v5 / canonical handoff 실행 경로 |
| 9 | 결과 handoff·검증·저장 → 상세 조회 | 완료된 공개 canonical view를 클라이언트가 별도 조회 |

`RunPod` 클래스 이름은 HTTP adapter 명칭이며 실제 upstream은 설정에 따른다. RunPod 서버를 다시 추가할 필요는 없다. 공개 동의 필드와 내부 HTTP 계약을 구분한다. 내부 `RunPodAnalysisJobRequest`에 동의 증빙·grant·서명을 새로 추가하지 않는다.

## 2. 차단 지점과 변경 범위

Backend Java 경로 prefix는 `src/main/java/org/example/voice/`, AI Python prefix는 `src/voice_coach/`다.

| 구간 | 소스 근거 | 영향 / 변경 |
| --- | --- | --- |
| Backend schema 선택 | `training/application/TrainingAnalysisSchemaAdmissionService.java` | 헤더 생략도 v5 canonical handoff. 구형 schema 헤더로 영상 경로 우회 불가 |
| Backend dispatch | `analysis/infrastructure/runpod/RunPodOutboxAnalysisJobPublisher.java:98`, `analysis/infrastructure/canonical/CanonicalRequestScope.java:17` | visual input/object가 있으면 제외. 나머지 조건을 충족한 영상 요청도 HTTP 503 `ANALYSIS_INTEGRATION_UNAVAILABLE`; outbox 생성 전 transaction 실패 |
| 내부 요청 | 양쪽 `docs/contracts/runpod_analysis_request_v3.schema.json`, Backend `RunPodAnalysisJobRequest.java` | **video 객체 표현은 이미 가능.** PTS/정규화 receipt 등 새 근거 계약은 추가 필요 |
| AI 접수 | `backend_analysis/http_runtime.py:342` | non-legacy + visual input이면 503 `NOT_READY`. Backend 조건만 제거해도 실행 불가 |
| AI 실행 | `backend_analysis/canonical_executor.py:74`, `canonical_producer.py:136` | canonical 음성 전용 조건. 상위 media orchestration에서 음성 core와 영상 특징 분리 실행·결합 |
| GPT | `backend_analysis/canonical_bridge.py:274`, frozen H5 | 영상 미연결 association. MFA join·검증된 입술 근거를 GPT 입력에 결속해야 함 |
| 결과 계약 | 양쪽 `docs/contracts/runpod_result_v5.schema.json` | visual 및 association 상태가 `NOT_CONNECTED`로 고정. 점수만 추가하면 validator와 충돌 |
| Backend 저장 | `analysis/application/CanonicalCallbackCommitter.java:54` | visual object 또는 기존 segment가 있으면 503 `NOT_READY`. 영상 보관·삭제 lifecycle과 새 근거 저장·검증을 함께 구현 |
| 공개 응답 | `analysis/domain/model/CanonicalAnalysisView.java` | `Visual`은 status·correctiveClaimsAllowed만 보유. 음소별 관찰·점수·보류 사유 DTO/projection 확장 필요 |

## 3. 결과 조회 경로

- analyze가 반환한 `analysisId`를 보존한다.
- `GET /api/training-sessions/{sessionId}/analysis/status`는 진행 상태용이다.
- `GET /api/training-sessions/{sessionId}/analysis`는 상태·점수 요약이며 상세 coaching/visual 필드가 없다.
- 현재 canonical handoff 상세 조회는 **`GET /api/v3/analyses/{analysisId}`**이며 `waitSeconds`를 지원한다. `GET /api/analyses/{analysisId}/result-contract`에서 결과 계약을 확인할 수 있다.
- 과거 `GET /api/analyses/{analysisId}`의 `visualSupplement` 필드만으로 새 canonical 영상 결과가 전달되지는 않는다.

새 계약 도입 시 result-contract, 상세 reader/projection, capabilities와 FE parser를 함께 맞춘다. FE가 실제 사용하는 조회 경로는 구현 전에 확인한다. `canonicalAnalysis.visual`에 음소별 관찰·점수·coverage·보류 사유를 제공하고 coaching에서 검증된 영상 근거를 참조하도록 확장하는 것이 제안 방향이다. 아직 배포된 필드가 아니다.

## 4. 기존 계획 보완

다음 개요를 [Backend 접수·저장·조회 상세 계획](backend-audiovisual-implementation-plan-20261007.md)으로 구체화했다. 계약·DB·파일별 작업·전환 순서는 상세 계획의 제안을 따른다.

1. **세션 API 경로를 우선 수직 통합:** 영상 업로드·등록·선택 → analyze → 상세 조회까지 recording/request/execution identity 유지. Direct WAV API 확장만으로는 이 경로가 완성되지 않는다.
2. **미디어 준비 책임:** Backend가 생성한 canonical MP4/PCM을 AI가 검증·재사용한다. PCM 재추출과 Seungun 중복 실행을 피한다. source/canonical digest, 실제 PTS, 시작 offset·회전·추출 변환 근거를 보존한다. 현재 크기·기간 검사만으로 음소 수준 동기화를 증명하지 않는다.
3. **공유 orchestration:** audio와 영상 특징 branch 병렬 시작 → MFA 결과와 join → 입술 비교 → GPT 1회. direct 경로와 계산 service를 공유하되 접수·작업 lifecycle adapter는 분리한다.
4. **계약 일괄 확장:** request/result/handoff artifact·semantic validator·frozen H5·Backend projection을 버전 관리한다. 영상 digest/시간/음소 binding을 execution 등록부터 결과 조회까지 유지한다. 기존 음성 채점은 보존한다.
5. **저장·실패·재시도:** 영상 retention/삭제·B2 archive·복구를 준비한 뒤 callback 차단을 해소한다. 영상 QC/참조 미지원/분기 timeout이면 유효한 음성 결과와 이유를 제공한다. 무결성 불일치는 접수에서 거절한다. retry/cancel/rerecord와 구 execution 결과 노출 방지도 함께 검토한다.
6. **접수·FE 활성화는 마지막:** publisher, 공유 `CanonicalRequestScope`, capabilities, AI accept가 같은 검증된 계약을 지원할 때 허용한다. FE가 영상 파일을 음성으로만 변환해 버리지 않고 등록·선택하도록 확인하며 상세 결과/null 사유를 표시한다. 기존 강좌·시험 등 제외 범위는 임의 확대하지 않는다.
7. **개발자 확인·전환:** branch 시간 중첩, Seungun/GPT 1회, 동일 음소/영상 근거, handoff/조회/표시, 음성 단독, 영상 실패·취소·재시도를 확인한다. AI main·Backend develop·FE 변경과 DLPC 복구 bundle을 함께 인계한다.

## 5. 판단 한계

위 변경을 모두 적용하면 **같은 analyze URL로 음성·영상 분석을 시작하고 별도 상세 조회에서 통합 피드백을 받는 구조**가 가능하다. 현재 코드가 이를 지원한다는 뜻은 아니다.

`testvideo1.mp4` 한 개는 포함된 문장·음소/문맥의 exemplar다. 전체 한국어 입술 점수의 검증된 기준은 아니다. 비교·관찰 가능한 범위부터 제공하며, 보정되지 않은 점수와 지원하지 않는 교정은 null/보류 사유로 유지한다.

이번에는 소스·문서만 정적으로 확인했다. 실제 analyze 요청, 추론, GPT 호출, 자동 회귀 테스트, FE 화면 QA, commit/push/PR/배포는 수행하지 않았다.

## 2026-10-07 구현 후보 반영

현재 구현 사실은 [진행 기록](implementation-progress-20261007.md)과 [v6 인계](audiovisual-v6-handoff-20261007.md)를 따른다. 초안과 달리 첫 v6 후보는 영상 전용이며, 실제 MFA 자음 위치만 사용한다. 참조/보정 catalog는 비어 있어 관찰 전용이다. 아래 계획의 전체 음소·보정 점수·운영 전환까지 완료했다는 의미가 아니다.
