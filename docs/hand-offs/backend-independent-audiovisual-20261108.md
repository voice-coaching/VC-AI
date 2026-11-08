# Backend 인계: 영상 분석 접수·실행·저장 독립화

## 변경 목적과 적용 상태

영상 readiness가 Backend의 영상 supported 값에 막히던 구조를 제거한다. 새 영상
경로는 AI가 접수·미디어 저장·실행·결과 저장을 소유하고 Backend가 비동기로 수집한다.
기존 음성 전용 독립 API로 MP4를 제출하거나 영상 없는 결과를 영상 성공으로 취급하지 않는다.

이 문서는 구현 계약이다. 배포 revision·실제 시험·운영 readiness는 별도 배포 기록으로
확인한다. AI 변경만으로 Backend의 VIDEO capability나 사용자 화면이 활성화됐다고
보고하지 않는다. 기존 사용자 결과·spool·진행 작업은 새 경로로 변환하거나 재실행하지 않는다.

## Backend 개발 작업

1. 새 영상 요청의 clientRequestId를 DB에 먼저 보존하고 로그인·녹음 소유권을 확인한다.
2. 기존 영상 정규화기로 동일 원본의 PCM WAV·canonical MP4와 mediaPreparation receipt를 만든다.
3. 새 영상 접수 API에 metadata를 보내 UUID jobId·executionId를 저장한다.
4. AI의 해당 job에 PCM과 MP4 bytes를 각각 PUT하고, 두 업로드 후 start를 호출한다.
5. 상태를 polling한다. 202/응답 유실은 clientRequestId 조회로 조정한다. 새 작업을 만들어
   추론/GPT를 반복하지 않는다. Backend 재시작·배포는 AI 작업 실행에 영향을 주지 않는다.
6. resultAvailable=true이면 결과 bytes와 SHA-256을 수집하고 자신의 소유권/현재 시도
   identity에 맞게 공개 DTO에 투영한다. DB commit 후 같은 resultSha256으로 ack한다.

전체 endpoint·metadata·상태·오류·재시작 계약은
[독립 영상 API v1](../api/independent-audiovisual-v1.md)을 따른다.

## readiness와 capability 수정

영상 준비 판단은 `/health/audiovisual`의 새 contractVersion,
executionMode=INDEPENDENT_AUDIOVISUAL_PULL_V1, readinessScope=AI_LOCAL,
executorConfigured, admissionEnabled를 사용한다. Backend 자신의 정규화·업로드·수집
클라이언트 준비 상태와 조합해 VIDEO capability를 계산한다.

새 경로에서 다음 기존 조건은 제거한다.

- Backend의 `/worker-readiness/audiovisual`·`/worker-readiness/handoff`를 AI가 다시
  조회해야 한다는 조건 및 reciprocal supported/lease 조건.
- 기존 CanonicalAudiovisualReadiness의 worker/admission 플래그와 handoff 설치·DB
  migration·spool 예약이 새 독립 영상 API 지원의 선행 조건이라는 연결.
- 기존 handoff v2 revision·H5 식별자·공유 schema hash·공식 검산표를 승인하는 게이트.

기존 음성/handoff 경로를 사용하는 작업은 그 경로의 계약을 유지한다. 이미 접수한
legacy 작업의 claim·heartbeat·결과 수령을 중간에 삭제하지 않는다. AI의 새 영상
readiness 응답을 기존 handoff v2로 속여 해석하지 않는다. 정상 설정에서도 Backend의
옛 worker-readiness가 supported=false로 남을 수 있으며 새 영상 실행은 이에 의존하지 않는다.

## 사용자 응답과 보안 경계

score.overallScore·validity·reason·선택 details는 기존 Frontend 필드에 투영한다.
세부점수 누락으로 총점이나 전체 분석을 숨기지 않는다. visual.score·scoreKind·coverage·
phoneAssessments·선택 subScores와 coaching.feedback도 보존한다. AI의 수치를 재계산하지 않는다.

새 응답의 jobId·executionId·clientRequestId는 AI UUID다. Backend의 analysisId·recordingId를
대체하지 않는다. 명시적인 DB 매핑을 통해 같은 사용자·같은 녹음·같은 현재 시도인지
검증한다. 소유권·서버 인증·미디어 digest·음성/영상 시계 결속 검사는 유지한다.
사용자용 응답에는 private CORE·얼굴 landmark 원본·저장 경로·자격증명을 노출하지 않는다.

## 재시도와 운영 인계

Backend가 내려가도 AI는 접수된 작업을 계속 실행하고 저장한다. 결과 수집·ack의
전송 재시도는 허용하지만 추론/GPT 재실행으로 바꾸지 않는다. AI 재시작 중 RUNNING
결과가 불확실하면 EXECUTION_INTERRUPTED이며 수동 확인 없이 새 실행을 만들지 않는다.
취소·ack는 입력/CORE/결과를 삭제하지 않는다. 별도 보관 정책은 추후 명시적으로 정한다.

이번 AI 배포는 Backend 코드·환경·DB·Frontend를 변경하지 않는다. Backend 개발자는
새 클라이언트 적용 후 capability와 기존 화면 DTO 연결을 배포해야 사용자 영상 흐름이 완성된다.
