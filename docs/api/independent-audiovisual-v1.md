# 독립 영상 분석 API v1

AI가 영상 작업을 접수·실행·저장하고 Backend가 결과를 수집한다. 기존 음성 전용
`/v2/analysis-jobs` 및 익명 `/direct`와 다른 인증된 영상 API다. Backend DB 정수 ID,
callback URL, claim·heartbeat·journal·handoff 준비 상태를 입력받거나 조회하지 않는다.

서버 Bearer와 정규화 WAV/MP4를 사용할 수 없는 브라우저는 별도
[원본 영상 직접 업로드 API](browser-original-video-v1.md)를 사용한다.

## 인증·readiness

모든 아래 요청은 기존 `AI_ANALYSIS_API_TOKEN`의 `Authorization: Bearer …`로 인증한다.
Backend는 로그인·녹음 소유권 및 사용자에게 허용된 영상 처리 범위를 검증하고,
자신의 recording/analysis ID와 AI의 UUID jobId·executionId·clientRequestId를 별도 저장한다.

`AI_INDEPENDENT_AV_ENABLED=true`이면 `/health/audiovisual`은 독립 영상 실행기의 상태다.
준비 시 200, 미준비 시 503이며 다음 필드를 포함한다.

| 필드 | 의미 |
|---|---|
| contractVersion | `voice-coaching.independent-audiovisual.v1` |
| executionMode | `INDEPENDENT_AUDIOVISUAL_PULL_V1` |
| readinessScope | `AI_LOCAL` |
| executorConfigured / admissionEnabled | 로컬 모델·영상 도구·저장소 및 접수 상태 |
| resultStoreReady | 별도 AI 작업 저장소가 열렸는지 |
| backendRequired | 항상 false |
| reasonCode | 준비 시 null, 그 외 로컬 준비/설정/hold 사유 |
| activeJobs | 독립 영상 QUEUED·RUNNING 수 |
| workerRevision | 실행 중 소스 revision |

기존 `/health/services`·`/health/handoff`는 기존 음성/handoff 경로의 상태다. 이를 독립
영상 기능의 선행 조건으로 쓰지 않는다. 새 영상 계약을 기존 handoff v2로 해석하지 않는다.

## 접수와 미디어 업로드

| Method | Path | 성공 응답 |
|---|---|---|
| POST | `/v2/audiovisual-analysis-jobs` | 202, AWAITING_MEDIA 또는 동일 작업 |
| GET | `/v2/audiovisual-analysis-jobs?clientRequestId=UUID` | 200, 접수 응답 유실 조정 |
| PUT | `/v2/audiovisual-analysis-jobs/{jobId}/media/audio` | 200, WAV bytes 저장 |
| PUT | `/v2/audiovisual-analysis-jobs/{jobId}/media/video` | 200, MP4 bytes 저장 |
| POST | `/v2/audiovisual-analysis-jobs/{jobId}/start` | 202, 두 파일 검증 후 QUEUED |
| GET | `/v2/audiovisual-analysis-jobs/{jobId}` | 200, 작업 상태 |
| GET | `/v2/audiovisual-analysis-jobs/{jobId}/result` | 200, 확정 결과 JSON bytes |
| GET | `/v2/audiovisual-analysis-jobs/{jobId}/artifacts/{kind}` | 200, 저장된 원본 근거 |
| POST | `/v2/audiovisual-analysis-jobs/{jobId}/ack` | 200, 수령 확인 |
| POST | `/v2/audiovisual-analysis-jobs/{jobId}/cancel` | 200, 로컬 취소 |

POST 접수는 `application/json`, 모든 본문은 `Content-Encoding: identity`다. JSON은
다음 필드만 허용하며 모두 필수다. contentId는 null 또는 양의 정수다.

```json
{
  "clientRequestId": "<Backend가 보존할 소문자 UUID>",
  "scriptText": "<1~4000자 원고>",
  "contentId": null,
  "audio": {"sha256":"<WAV SHA-256>","fileSizeBytes":123456,"mimeType":"audio/wav"},
  "video": {"sha256":"<MP4 SHA-256>","fileSizeBytes":2345678,"mimeType":"video/mp4"},
  "mediaPreparation": {"receipt":{},"receiptSha256":"<receipt JCS SHA-256>"}
}
```

위 receipt 빈 객체는 필드 위치 설명용이며 유효 입력이 아니다. 실제 receipt는 기존
[미디어 정규화 계약](../contracts/runpod_media_preparation_v1.schema.json) 전체 필드를
따른다. 동일 원본에서 생성한 PCM·MP4 digest와 실제 시계 정보를 보존한다. 서명이나
권한 증빙을 추가하지 않는다. syncStatus=UNVERIFIED만으로 정렬이 검증됐다고 간주하지
않으며, AI는 디코딩 시계와 PCM을 대조해 검증할 수 없으면 시각 점수를 제외한다.

WAV는 mono/16 kHz/16-bit PCM, 0.5~120초, 최대 20 MiB다. MP4는 최대 256 MiB,
기존 영상 추출기가 지원하는 단일 H.264/HEVC 영상 및 AAC 음성 트랙을 사용한다.
크기·digest가 실제 업로드와 달라지면 422다. 원격 URL·S3 objectKey는 받지 않는다.
PUT은 각 선언 MIME의 원본 bytes이며 선언 크기까지만 읽는다. 동시 업로드는 최대 2개다.

같은 clientRequestId·같은 metadata는 같은 jobId다. metadata가 달라지면 409다. 같은
파일의 재전송은 멱등이며 저장된 파일을 다른 bytes로 덮어쓰지 않는다. start는 두
업로드가 끝난 뒤 한 번 호출한다. 응답이 유실되면 같은 작업 조회/start로 조정하며
새 clientRequestId를 만들어 재분석하지 않는다.

## 상태·결과·수령

상태 객체는 schemaVersion, jobId, executionId, clientRequestId, status, sequence,
createdAt, updatedAt, uploaded, resultAvailable, resultSha256, failure, acknowledgedAt다.
schemaVersion은 contractVersion과 같으며 시각은 Unix seconds다. failure는 null 또는
`{"code":"<고정 코드>"}`다. 상태는 AWAITING_MEDIA, QUEUED, RUNNING, COMPLETED,
FAILED, CANCELLED 중 하나다. resultAvailable=false이면 결과 GET은 409다.

결과 schemaVersion은 `voice-coaching.independent-audiovisual-result.v1`이다.
jobId·executionId·clientRequestId, status, score, coaching, visual, sourceIdentity,
failure를 포함한다. score.overallScore·validity·reason 및 선택 score.details는
[기존 세부점수 계약](frontend-score-details-response.md)을 유지한다. visual에는 입술
점수·근거 상태·coverage·phoneAssessments가 있다. Backend/Frontend는 재채점하거나
참조 기준표·내부 H5 hash를 승인 게이트로 사용하지 않는다. 필수 실행 ID 연결은 유지한다.

결과 GET의 `X-Result-SHA256` 및 ETag는 저장된 응답 bytes의 SHA-256이다. Backend는
원본 bytes를 수령·저장하고 자신의 DB commit 후 `{"resultSha256":"<해시>"}`를 ack에
POST한다. ack는 멱등이며 입력·CORE·결과를 삭제하지 않는다.

원본 근거 kind는 CORE, BRIDGE_RESULT, BINDING, ASSOCIATION, MEDIA_RECEIPT,
VISUAL_EVIDENCE다. 인증된 Backend만 수집하며 사용자용 응답에는 내부 원본을 직접
노출하지 않는다. 각 응답에 X-Artifact-SHA256이 있다. Seungun CORE 원본을 재작성하지
않으며, 별도 artifacts-manifest가 동일 작업의 음성·영상·근거 해시를 묶는다.

## 실패·재시작·보존

HTTP 오류는 기존 공통 reasonCode JSON과 실제 4xx/5xx를 사용한다. 인증 401, 미지원
MIME 415, 크기 초과 413, metadata/미디어 무결성 422, ID 충돌·미완료 결과 409,
용량 제한 429, 로컬 미준비/저장소 부족 503이다. 비동기 추론 실패는 작업 FAILED와
고정 failure.code로 조회한다. 음성만 처리됐다는 이유로 영상 성공을 조작하지 않는다.

원음·영상·job은 atomic write+fsync로 보존한다. GPT 생성은 요청당 최대 1회이며
자동 재시도하지 않는다. 취소 시 guard가 해당 작업의 자식 프로세스를 회수한다.
재시작 시 RUNNING 작업은 일치하는 완료 영수증·결과 해시가 있으면 확정하고, 없으면
EXECUTION_INTERRUPTED로 처리한다. 불확실한 작업을 QUEUED로 되돌리지 않는다.

## 운영 설정

AI_INDEPENDENT_AV_ENABLED, AI_INDEPENDENT_AV_SPOOL_ROOT,
AI_INDEPENDENT_AV_WORKSPACE_ROOT와 AI_CANONICAL_VISUAL_PYTHON을 명시한다.
CORE/H5·landmark model·OpenAI 자격증명은 AI 로컬 설정을 사용한다. 영상 worker에는
Backend callback/API token·AWS credential·S3 readiness 설정을 전달하지 않는다.
전용 spool/workspace는 기존 direct/canonical 경로와 겹칠 수 없다. 기본 보관 예산은
2 GiB, 미완료 접수 최대 3건, 작업 최대 1000건, 동시 영상 실행 1건이다.

자동 삭제·B2 archive·서버 소실 복구 보장은 이번 계약에 포함되지 않는다. 운영자는
spool이 있는 볼륨의 수명과 백업을 관리하고 Backend 결과 수집을 지연시키지 않는다.
