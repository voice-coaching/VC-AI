# 409 연쇄 오류와 journal 연결 실패 복구

## 확인한 장애

2026-10-05 18:20–18:25 KST, 세션 131·녹음 96·분석 80·execution `f49333f9-2c4a-4003-b125-5ebe5f7c87b6`을 조사했다. AWS JAR는 `d2e36954b6fcab2e806c61ea1cd19b016678c2a1`, RunPod 실행 로그의 worker revision은 `32d98e39767b56697bc7e58fb50e3e9d61432349`였다. 현재 저장소 main과 실제 실행 revision은 구분한다.

- 18:23:47: 분석 접수 성공.
- 18:23:51: 결과 GET `/api/v3/analyses/80?waitSeconds=8`가 409. 같은 시각 worker claim 성공. 배포 소스는 조회 중 상태가 변경되면 `CANONICAL_ANALYSIS_CHANGED`를 반환한다. FE는 이를 일시적 읽기 충돌로 취급하지 않았다.
- 18:23:52–55: RunPod `RESERVE_AND_MATERIALIZE`가 3.039초 후 `BACKEND_JOURNAL_UNAVAILABLE`, upstreamStatus 503으로 종료. 당시 adapter는 HTTP 오류와 통신 예외를 구분하지 않았다. AWS 예약 접근 로그와 journal 행이 없어 연결 제한 3초의 시간초과가 유력하나 정확한 네트워크 단계는 확정할 수 없다.
- 18:24:03·08: 같은 세션에서 업로드 URL을 재발급하려다 `INVALID_SESSION_STATE` 409. 세션은 ANALYZING, 분석은 18:24:04에 FAILED/runpod_execution_failed로 마감됐다. core·canonical 결과·handoff·journal 행은 없었다.

## 변경 파일과 동작

`src/voice_coach/backend_analysis/canonical_backend_journal.py`

1. 실제 HTTP 응답은 `HTTP_RESPONSE`와 실제 upstreamStatus로 기록한다. 통신 예외의 upstreamStatus는 null이며 CONNECT/READ/WRITE/POOL timeout, 연결·읽기·쓰기·프로토콜 오류, DNS/TLS 오류, 전체 요청 시간초과를 구분한다. DNS/TLS 구분은 예외 cause/context에 해당 타입이 보존된 경우에만 가능하다. 공개 오류 코드와 내부 호환 status는 유지한다.
2. 로그에는 작업 종류·메서드·request/execution ID·경과시간만 남긴다. 토큰, 전체 URL, 원본 응답, 예외 문자열, 녹음·원고를 기록하지 않는다.
3. `/reserve`의 연결·읽기·쓰기·pool·프로토콜·전체 요청 시간초과에만 복구한다. 같은 실행의 journal을 GET하고 기존 예약이 있으면 재사용한다. GET이 HTTP 404일 때만 원래 bytes·worker·revision으로 POST `/reserve`를 한 번 더 요청한다. GET 실패나 두 번째 POST 실패는 즉시 상위로 전달한다. HTTP 오류 응답과 DNS/TLS·분류되지 않은 오류는 자동 재시도하지 않는다.
4. 예약과 복구 조회 모두 현재 lease/deadline 안에서 수행한다. 성공 snapshot의 request/execution/analysis/worker와 두 revision을 검증하고 producerStarted가 false여야 진행한다. 기존 Backend의 동일 예약 멱등 계약을 이용한다. `/start`, 업로드 begin/PUT/checkpoint, 모델 추론은 자동 반복하지 않는다.

`src/voice_coach/backend_analysis/canonical_executor.py`

최종 오류 로그에 failureKind와 실제 upstreamStatus를 반영한다. 예약 복구 후에는 별도 성공 이벤트를 남긴다. 아직 발생 단계를 알 수 없는 과거 503을 실제 AWS HTTP 503으로 단정하지 않는다.

FE 동반 수정은 일시적 결과 조회 409를 기존 deadline/backoff 안에서 재조회하고 접수 후 재업로드를 막는다. Backend 상태 전이와 오류 계약은 변경하지 않는다. 직접 FE→RunPod 경로의 인증이나 코칭·채점 정책도 변경하지 않는다. 이 adapter 수정은 AWS journal을 사용하는 경로에 적용된다.

## 확인 범위와 배포

변경 Python 소스 컴파일과 diff 정적 확인을 수행했다. 저장소 지침에 따라 자동 회귀 테스트·모델 추론·브라우저 QA는 실행하지 않았다. 개발자 확인 대상은 첫 예약 연결 실패→404→한 번 재예약, 응답 유실→기존 예약 재사용, revision 불일치·producer 시작·만료 lease에서 중단, 4xx/5xx와 checkpoint의 재실행 방지다.

로컬 전체 source-pin 정적 검사는 673개 불일치로 실패했다. 이번 변경 전 기준 작업본에서도 출력 전체가 동일한 SHA256으로 확인됐으며 이 수정으로 추가된 차이는 없다. 전체 저장소 검사가 통과했다고 보고하지 않는다.

이번 작업은 코드·문서·PR 인계다. AWS/RunPod 서비스 설정·재시작·배포·세션 131 데이터는 변경하지 않는다. 병합 SHA와 실제 실행 revision을 배포 후 대조해야 하며 PR 생성이나 컴파일 통과를 운영 복구 완료로 보지 않는다. 기존 Native 승인 조건을 변경하지 않는다.
