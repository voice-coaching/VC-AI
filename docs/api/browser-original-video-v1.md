# 브라우저 원본 영상 직접 업로드 API v1

2026-11-08 구현 명세. 코드 구현과 운영 활성화는 별개다. 이 변경은 운영 키·설정·서비스를
변경하지 않는다. AI 브랜치 `feat/browser-original-video-20261108`, Backend 브랜치
`feat/browser-video-upload-grant-20261108`에 대응한다.

## 처리 순서

1. 브라우저는 로그인한 사용자의 훈련 세션과 업로드할 원본 파일을 선택한다.
2. 원본 파일의 SHA-256과 바이트 수를 계산하고 Backend에서 5분짜리 권한을 발급받는다.
3. Backend 응답의 metadata를 AI에 보내 작업을 만들고, 원본 영상 하나를 바이너리 PUT한다.
4. start를 호출하면 AI가 원본을 정규화하고 기존 독립 영상 실행기로 분석·피드백을 생성한다.
5. 브라우저는 상태와 결과를 조회한다. Backend의 결과 수집은 별도 서버 Bearer 경로를 사용한다.

AI는 접수·실행·결과 저장 중 Backend readiness·callback·heartbeat·lease를 호출하지 않는다.
권한 검증 공개키도 로컬 설정에서 읽는다. 원본 영상이나 프레임을 GPT에 보내지 않으며,
기존 Seungun 원본 JSON 보존과 요청당 GPT 생성 최대 1회 정책을 유지한다.

## 1. Backend 권한 발급

`POST /api/training-sessions/{sessionId}/video-analysis-upload-grants`

헤더: 사용자 **로그인 Access Token**의 `Authorization: Bearer <accessToken>`,
`Origin: <실제 브라우저 origin>`, `Content-Type: application/json`.
`X-User-Id`만으로 권한을 발급하지 않는다. 서명·만료·활성 로그인 세션, 훈련 세션의 실제
소유자, 지원 학습 유형과 현재 세션 상태를 확인한다. 원고는 Backend가 세션에서 선택한다.

```json
{
  "clientRequestId": "f00f86ac-72ab-4bb2-a923-73f67596d2a4",
  "source": {
    "sha256": "<원본 영상의 소문자 SHA-256 64자리>",
    "fileSizeBytes": 2097382,
    "mimeType": "video/mp4"
  },
  "videoProcessingConsentAccepted": true,
  "videoProcessingConsentPolicyRevision": "voice-video-processing-consent-v1"
}
```

`source.mimeType`은 `video/mp4`, `video/webm` 또는 `video/quicktime`, 크기는 1~268435456 bytes다.
`clientRequestId`는 같은 시도의 업로드·권한 갱신·결과 조회에 계속 사용하는 소문자 UUID다.
기존 얼굴 영상 처리 동의는 Backend에서 확인·기록한다. 그 증빙을 AI 내부 API에 추가하지 않는다.
활성 상태는 RECORDING·UPLOADING·FAILED, 학습 유형은 PRONUNCIATION이다.

200 응답은 기존 Backend `ApiResponse` envelope이며 `Cache-Control: no-store`다.

```json
{
  "result": true,
  "message": "영상 분석 업로드 권한을 발급했습니다.",
  "data": {
    "schemaVersion": "voice-coaching.browser-video-grant.v1",
    "aiBaseUrl": "https://ai.example",
    "jobsPath": "/browser/v1/video-analysis-jobs",
    "grantToken": "<단기 서명 토큰>",
    "expiresAt": 1794139500,
    "metadata": {
      "clientRequestId": "f00f86ac-72ab-4bb2-a923-73f67596d2a4",
      "scriptText": "<Backend가 선택한 원고>",
      "contentId": 123,
      "source": {
        "sha256": "<같은 원본 SHA-256>",
        "fileSizeBytes": 2097382,
        "mimeType": "video/mp4"
      }
    }
  }
}
```

예시 host·시간·해시는 설명용이다. `aiBaseUrl`은 Backend 설정값이며 반환된 값과
metadata를 사용한다. 토큰을 URL·로그·localStorage에 저장하지 않는다.
Backend 실패는 기존 envelope와 HTTP 400/401/403/404/409/503을 사용한다.

## 2. AI 브라우저 경로

다음 경로는 모두 `Authorization: Bearer <grantToken>`과 일치하는 `Origin`이 필요하다.
Backend 로그인 토큰·AI 서버 Bearer는 이 경로의 권한을 대신하지 못한다.

| Method | Path | 요청 | 성공 |
|---|---|---|---|
| OPTIONS | 아래 경로 | 브라우저 preflight, Authorization 불필요 | 204 |
| POST | `/browser/v1/video-analysis-jobs` | Backend 응답의 metadata JSON | 202 작업 상태 |
| GET | `/browser/v1/video-analysis-jobs?clientRequestId=UUID` | 접수 응답 유실 조정 | 200 작업 상태 |
| GET | `/browser/v1/video-analysis-jobs/{jobId}` | 상태 조회 | 200 작업 상태 |
| PUT | `/browser/v1/video-analysis-jobs/{jobId}/source` | **원본 파일 bytes**, 선언 MIME | 200 작업 상태 |
| POST | `/browser/v1/video-analysis-jobs/{jobId}/start` | 본문 없음 | 202 작업 상태 |
| GET | `/browser/v1/video-analysis-jobs/{jobId}/result` | 결과 조회 | 200 원본 결과 JSON bytes |
| POST | `/browser/v1/video-analysis-jobs/{jobId}/cancel` | 본문 없음 | 200 작업 상태 |

POST metadata는 `clientRequestId`, `scriptText`, `contentId`, `source`만 허용한다.
`scriptText`는 공백만이 아닌 1~4000 Unicode 문자, `contentId`는 양의 정수 또는 null이다.
각 값은 서명된 권한과 일치해야 한다. 추가 필드·중복 JSON key·NaN은 거부한다.
JSON은 최대 65536 bytes, Content-Encoding은 identity만 허용한다.

PUT은 multipart/form-data가 아니라 `Content-Type: video/mp4` 또는 `video/webm`의
파일 bytes다. `fileSizeBytes`와 SHA-256을 스트리밍 검증한다. 클라이언트 파일명·경로·URL을
입력받지 않는다. 한 번의 업로드 제한은 120초, 브라우저 경로의 동시 업로드 제한은 2개다.

상태 응답은 기존 [독립 영상 작업 상태](independent-audiovisual-v1.md)를 사용한다.
`uploaded`는 이 경로에서 `[]` 또는 `["source"]`다.

```json
{
  "schemaVersion": "voice-coaching.independent-audiovisual.v1",
  "jobId": "<UUID>",
  "executionId": "<UUID>",
  "clientRequestId": "<UUID>",
  "status": "AWAITING_MEDIA",
  "sequence": 1,
  "createdAt": 1794139200.0,
  "updatedAt": 1794139200.0,
  "uploaded": [],
  "resultAvailable": false,
  "resultSha256": null,
  "failure": null,
  "acknowledgedAt": null
}
```

상태 전이: AWAITING_MEDIA → QUEUED → RUNNING → COMPLETED/FAILED.
취소하면 CANCELLED이며 원본·근거를 삭제하지 않는다. 생성 후 900초 동안 start하지 않은
브라우저 작업은 FAILED/UPLOAD_EXPIRED로 종료해 업로드 예약 용량을 해제한다.
권한 5분 만료와 작업 수명은 별개다. 이미 QUEUED/RUNNING인 작업은 권한 만료로 중단되지 않는다.
AI 실행 deadline은 900초이며 원본 정규화도 그 안에 포함된다.

만료 후 상태·결과 조회가 필요하면 Backend에서 **같은 clientRequestId·같은 원본**의
권한을 다시 발급받는다. 같은 소유자·세션·origin·원고·원본으로 재접수하면 같은 jobId를
돌려준다. jti·iat·exp가 바뀌어도 새 분석을 만들지 않는다. 같은 ID의 내용 변경은 409다.
다른 소유자의 jobId로 조회·업로드·취소·결과 접근을 시도하면 404다.
실행 중 재시작으로 중단된 작업은 실패로 남기며 GPT를 자동 재실행하지 않는다.

## 3. 정규화·분석·결과

원본 MP4/WebM/MOV은 오디오 1개·영상 1개가 있어야 하며 길이 0.5~120초,
입력 해상도는 축별 3840 이하·면적 3840×2160 이하, 오디오는 8~192kHz·1~8채널이다.
손상 파일·선언 MIME과 다른 컨테이너·영상만 있거나 음성만 있는 파일은 정규화에 실패한다.
이 실패는 비동기 작업의 FAILED/MEDIA_NORMALIZATION_FAILED로 조회한다.

AI는 네트워크를 차단한 ffmpeg/ffprobe에서 공통 시간 원점을 유지한다. 이미 H.264/HEVC·AAC,
회전 없음, 양축 1920 이하·면적 1920×1080 이하, 30fps 이하·3600프레임 이하인 입력은
재인코딩하지 않고 트랙을 보존해 MP4로 remux한다. 그 외에는 회전을 적용하고 세로/가로
방향을 유지한 1920×1080 면적·30fps H.264/AAC로 변환한다. 그 **MP4에서** mono 16kHz
16bit WAV를 추출한다. 독립적으로 오디오/영상 PTS를 각각 0으로 초기화하지 않는다.
디코더 CPU·메모리·출력 파일 크기·wall time을 제한한다. 원본은 그대로 보존한다.

AI 생성 receipt는 `schemaVersion=voice-coaching.ai-media-preparation.v1`,
`normalizationRevision=ai-original-video-pcm-20261108-v2`이다. 기존 Backend 생성 receipt
v1을 위장하거나 기존 내부 요청 계약을 완화하지 않는다. 실제 영상 오디오와 WAV의
샘플 일치·연속 시간 정보는 기존 visual 분석기가 다시 확인한다. 이전 로컬 시험의 v1
receipt는 읽기 호환으로 보존하며 새 정규화는 v2를 기록한다.

J406의 MediaPipe 1.0.1 buffer 모델 초기화 경로에서 native heap 오류가 재현돼, 검증된
동일 모델 bytes를 Linux 익명 파일에 복사·봉인한 뒤 공식 파일 로더로 전달한다. 모델·
landmark mapping·품질 기준·점수 정의는 바꾸지 않는다. 파일 descriptor는 native detector
종료까지 유지하고 닫는다. 네트워크 차단과 자원 제한은 유지한다.

결과 본문은 `voice-coaching.independent-audiovisual-result.v1` 그대로다.
`score.overallScore`, optional `score.details`, `coaching`, `visual`은 기존 영상 결과와
동일하며 점수 정의나 입술 기준표를 변경하지 않는다. 미측정 점수 null을 0으로 채우지 않는다.
`sourceIdentity`에는 기존 정규화 WAV/MP4 해시 외에 `originalVideoSha256`과
`normalizationRevision`이 추가된다. result GET은 bytes를 다시 직렬화하지 않고
`X-Result-SHA256`·ETag를 반환한다.

오류가 분석 시작 전에 발생하면 실제 HTTP 오류로 응답하며, 접수된 비동기 실행의 실패는
상태의 `failure.code`로 확인한다. FAILED이지만 `resultAvailable=false`이면 result GET은
409이며 실패 결과를 임의로 생성하지 않는다.

| HTTP | 대표 reasonCode | 의미 |
|---|---|---|
| 400 | INVALID_JSON | 잘못된 JSON |
| 401 | INVALID_UPLOAD_GRANT | 서명·키·alg·typ·발급자·대상·scope·만료 검증 실패 |
| 403 | ORIGIN_NOT_ALLOWED / UPLOAD_GRANT_BINDING_MISMATCH | origin·원고·영상·시도 권한 불일치 |
| 404 | NOT_FOUND | 작업 없음·다른 소유자·브라우저 미제공 경로 |
| 408 | UPLOAD_TIMEOUT | 업로드 시간 초과 |
| 409 | IDEMPOTENCY_CONFLICT / JOB_STATE_CONFLICT / MEDIA_NOT_READY / RESULT_NOT_AVAILABLE | 내용·상태 충돌 |
| 413 | PAYLOAD_TOO_LARGE | 선언 크기 또는 JSON 한도 초과 |
| 415 | UNSUPPORTED_MEDIA_TYPE | MIME·encoding 불일치 |
| 422 | VALIDATION_FAILED / PAYLOAD_DIGEST_MISMATCH | 타입·해시·정확한 크기 불일치 |
| 429 | CAPACITY_EXCEEDED / UPLOAD_CAPACITY_EXCEEDED | 동시 처리 한도 |
| 500 | INTERNAL_ERROR | 서버 처리 실패 |
| 503 | BROWSER_VIDEO_NOT_CONFIGURED / NOT_READY / SPOOL_FULL | 로컬 설정·실행기·용량 미준비 |

AI 오류 형식은 `{reasonCode,message,requestId,executionId,retryable}`이며
429·503에는 Retry-After가 있다. 허용 origin의 오류도 CORS 헤더를 유지한다.
401이면 같은 시도 권한을 갱신하고 상태를 조회한다. 응답 유실·5xx 때문에 새로운 시도 ID를
만들거나 분석/GPT를 자동 재실행하지 않는다.

## 4. 브라우저 호출 예

아래는 Backend에서 받은 `data`를 사용하는 연결 예이며 화면 코드는 변경하지 않는다.

```javascript
const requestId = crypto.randomUUID(); // 같은 시도에서는 보존
const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
const sha256 = [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, "0")).join("");
const issued = await fetch(`${backend}/api/training-sessions/${sessionId}/video-analysis-upload-grants`, {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
  body: JSON.stringify({ clientRequestId: requestId,
    source: { sha256, fileSizeBytes: file.size, mimeType: file.type.split(";")[0] },
    videoProcessingConsentAccepted: true,
    videoProcessingConsentPolicyRevision: "voice-video-processing-consent-v1" })
});
if (!issued.ok) throw new Error(`Grant HTTP ${issued.status}`);
const { data: grant } = await issued.json();
const auth = { Authorization: `Bearer ${grant.grantToken}` };
const base = grant.aiBaseUrl + grant.jobsPath;
const accepted = await fetch(base, { method: "POST",
  headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify(grant.metadata) });
if (!accepted.ok) throw new Error(`Create HTTP ${accepted.status}`);
const job = await accepted.json();
const upload = await fetch(`${base}/${job.jobId}/source`, { method: "PUT",
  headers: { ...auth, "Content-Type": grant.metadata.source.mimeType }, body: file });
if (!upload.ok) throw new Error(`Upload HTTP ${upload.status}`);
const started = await fetch(`${base}/${job.jobId}/start`, { method: "POST", headers: auth });
if (!started.ok) throw new Error(`Start HTTP ${started.status}`);
// GET /{jobId}로 조회하고 resultAvailable=true일 때 /result를 조회한다.
```

브라우저는 Origin을 자동 전송한다. 쿠키 전달은 사용하지 않으며
Access-Control-Allow-Credentials를 활성화하지 않는다. 실제 동의 UI에서 사용자가 수락한
뒤 위 요청을 수행해야 한다. 이 예시는 동의 UI 구현을 대신하지 않는다.

## 5. 서버 설정·운영 전 조건

| AI 설정 | 값 |
|---|---|
| AI_INDEPENDENT_AV_ENABLED | true, 기존 독립 영상 자산·spool 필요 |
| AI_BROWSER_VIDEO_ENABLED | true, 기본 비활성 |
| AI_BROWSER_VIDEO_ISSUER | Backend와 동일한 고정 발급자 문자열 |
| AI_BROWSER_VIDEO_AUDIENCE | Backend와 동일한 고정 대상 문자열 |
| AI_BROWSER_VIDEO_ALLOWED_ORIGINS | 정확한 HTTPS origin의 JSON 배열 |
| AI_BROWSER_VIDEO_PUBLIC_KEYS_FILE | 절대경로, JSON `{kid: Ed25519 공개키 PEM}` |

| Backend Spring 설정 | 값 |
|---|---|
| ai.browser-video.enabled | true, 기본 false |
| ai.browser-video.issuer / audience | AI와 동일 |
| ai.browser-video.ai-base-url | AI의 HTTPS origin, 끝 `/` 없음 |
| ai.browser-video.allowed-origins | 정확한 HTTPS origin의 쉼표 구분 목록 |
| ai.browser-video.key-id | `[A-Za-z0-9_-]{1,64}`, AI map의 kid와 동일 |
| ai.browser-video.private-key-file | Backend 전용 PKCS#8 Ed25519 PEM 파일 절대경로 |
| ai.browser-video.public-key-file | 대응하는 Ed25519 공개키 PEM 파일 절대경로 |

AI에는 공개키만 배포한다. Backend 서명 개인키·기존 AI 서버 Bearer는 브라우저에
전달하지 않는다. AI는 grant의 `alg=EdDSA`, 전용 typ, kid, 발급자, 대상, scope,
정수 iat/nbf/exp(최대 300초), sub·session·시도·원고·원본·origin을 확인한다.
키 선택 URL·jku·x5u·임의 alg는 허용하지 않는다. 키 회전은 AI 공개키를 먼저 추가한 후
Backend kid를 바꾸며, 구성은 시작 시 로드하므로 작업을 drain한 뒤 재시작해야 한다.

Backend 배포 후보에는 AWS 내부에서 키를 최초 1회 생성하고 기존 키를 재사용하는
`scripts/deployment/browser_video_keys.py --apply`와 전용 systemd 환경 파일 설치가
포함된다. `GET /api/browser-video/.well-known/upload-grant-keys`는 공개 검증키만
`{schemaVersion:"voice-coaching.browser-video-keyset.v1",keys:{kid:publicPem}}`로 제공한다.
AI 운영 도구는 기존 Backend HTTPS origin에서 배포할 때만 이를 가져와 로컬 파일에
고정한다. 운영 게이트웨이의 기존 서버 Bearer로 공개키를 조회하며, 서버 토큰으로
사용자 권한을 발급할 수는 없다. 요청 중 공개키를 갱신하거나 Backend를 조회하지 않는다. Backend의 키쌍은
시작 시 서명·검증으로 일치 여부를 확인한다. 비활성 상태의 공개키 endpoint는 503이다.

인증된 `GET /health/browser-video`는 200/503과 executorConfigured·admissionEnabled·
reasonCode를 반환한다. 기존 `/health/audiovisual`과 별개여서 브라우저 키 설정 실패가
기존 서버용 영상 경로를 닫지 않는다. 브라우저 readiness도 Backend를 조회하지 않는다.

AWS 프록시가 새 `/browser/v1/video-analysis-jobs` 경로와 OPTIONS·PUT을 통과시키고
268435456-byte 원본 업로드 및 120초 업로드 시간을 허용해야 한다. 경로에 별도 서버
Bearer를 강제하면 브라우저 grant가 막히므로 프록시 설정도 담당자가 확인한다.
브라우저는 private artifacts·ack에 접근할 수 없다. Backend 서버의 기존
`/v2/audiovisual-analysis-jobs` 조회·result·artifacts·ack 계약은 유지한다.

Backend 이력 수집/AI jobId 매핑 및 Frontend 화면 연결·배포는 이 API 구현과 별개다.
[Backend 인계](../hand-offs/backend-browser-original-video-20261108.md)를 따른다.

서명 검증 설계 참고: [JWT 보안 권고 RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html),
[cryptography Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

## 2026-11-08 후속 후보: 오디오 시간축·부분 입술 점수

정규화 revision `ai-original-video-pcm-20261108-v3`는 원본을 보존하고, 오디오 프레임의
국소·누적 타임스탬프 오차가 각각 2ms 이내인 경우에만 새 파생 영상의 시간축을 샘플 수로
정리한다. 첫 decoded audio PTS를 유지해 실제 영상과의 시작 시각 차이를 지우지 않는다.
실제 단절·누적 drift는 이 처리로 숨기지 않으며, 입술 분석의 기존 sample-exact PCM
동일성 검사와 연속 오디오 시간축 검사를 모두 통과해야 한다. 기존 v1/v2 receipt 읽기도 유지한다.

응답의 `visual.score`, `visual.scoreKind`, `visual.coverage`, `visual.subScores`,
`visual.phoneAssessments` 이름은 유지한다. 승인된 임시 부분 비교의 표시·종합점수 반영 범위는
[입술 소비 계약의 후속 정책](../contracts/lip-provisional-score-v1.md#2026-11-08-임시-부분-비교-정책)을 따른다.
`PARTIAL_REFERENCE_COVERAGE`에서는 채점된 음소 범위의 임시 점수이며, 음성 종합점수는 보존한다.
다른 단어 참조의 동작 타이밍이나 보이지 않는 입술의 점수는 추정하지 않는다.

후속 후보는 운영 배포 완료를 뜻하지 않는다. 기존 사용자 job/result/spool을 수정하거나 자동 재실행하지 않는다.
