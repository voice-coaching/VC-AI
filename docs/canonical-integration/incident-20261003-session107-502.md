# 세션 107 상태 조회 502·배포 계약 불일치

조사: 2026-10-03 16:14~16:23 KST. 사용자 제보 구간은 16:00~16:13 KST이며 사이트는 `https://vc-fe.vercel.app/practice/103?returnTo=%2Fhome&start=1`이다. AWS nginx/journald/DB, RunPod 실행 원본의 상태 필드, GitHub 배포 기록, FE 공개 JavaScript를 읽었다. 서버 변경·재배포·새 분석·브라우저 자동화는 수행하지 않았다.

## 결론

1. 상태 조회 502는 16:09:46 AWS Backend 재시작 중 nginx → `127.0.0.1:8080` 연결 거절이다. RunPod 추론 실패나 Content-Length 경고가 원인이 아니다.
2. FE는 일시적인 GET 오류를 재시도하지 않고 폴링을 종료했다. 서버의 분석 61은 계속 진행되어 DB COMPLETED/ACCEPT로 반영됐다. 네트워크 조회 실패와 분석 작업 실패가 화면에서 충분히 구분되지 않는다.
3. 현재는 별도 장애가 있다. 새 채점 Backend JAR만 배포되고 AWS verifier와 RunPod는 이전 H5다. 16:20 KST 관측에서 canonical 신규 접수가 차단됐다. 단순히 다시 녹음해도 배포 불일치가 해결되지는 않는다.

## 시각별 근거

모든 시각은 KST다. nginx의 UTC 로그에 9시간을 더했다.

| 시각 | 근거 | 결과 |
| --- | --- | --- |
| 16:04:30 / 16:04:42 | GitHub Deploy production 두 실행 생성 | H5, 이어 채점 변경 배포 |
| 16:07:07~16:07:46 | journald 첫 Backend 재시작 | JAR 2ae6737 |
| 16:08:31~16:08:37 | 세션107 upload-url/등록/선택 | HTTP200, 녹음71 |
| 16:08:42 | POST 세션107 analyze | HTTP200 |
| 16:08:46 / 16:08:47 | 분석61 claim / RunPod job 접수 | HTTP200 / 202 |
| 16:08:43~16:09:45 | 세션107 status 조회31회 | 모두 HTTP200 |
| 16:09:45.884 | journald | Backend stopping |
| 16:09:46 | nginx 세션107 status | HTTP502, `connect() failed (111: Connection refused)`; upstream localhost:8080 |
| 16:09:47.870 | journald 두 번째 재시작 | JAR 1bdd876 |
| 16:09:53~16:10:22 | 분석61 heartbeat | HTTP502 24회, 이후200 복구 |
| 16:10:23.322 | Tomcat8080 시작 | HTTP upstream 복구 |
| 16:11:24 이후 | journal prepare, 근거5개 stage/readback, receipt | 정상 진행 |
| 16:13:18~16:13:36 | result callback | 검증·반영 대기 중503 7회 |
| 16:13:43.729 | DB analyzed_at | COMPLETED |
| 16:13:47 | result callback / RunPod completed.json | HTTP200 / COMPLETED |

해당 502 이후 조사 시점까지 nginx에 세션107의 후속 status GET은 없었다. 브라우저 오류만으로 서버 작업을 FAILED라고 판단하면 안 된다.

## 최종 분석 결과

- analysisId=61, recordingId=71, executionId=`418bd51e-1ad6-4d69-a1a4-6b2f8fa5b4dd`, retry_count=0.
- DB status=COMPLETED, failure_code=null, overall_score=null.
- RunPod callback canonical decision=ACCEPT, adapterStatus=NO_PERMITTED_COACHING_CONTENT, generationStatus=NOT_DISPATCHED, dispatchAttempts=0.
- pipeline=`canonical-R-421fda-H5-20261003-v1`. 점수 null 사유는 NO_APPROVED_SCORING_CONTRACT다. 당시 실행은 새 9항목 채점 하네스가 아니며 GPT 코칭도 호출하지 않았다.
- `/practice/103`의 103은 FE route의 contentId다. 실제 trainingSessionId=107과 숫자가 다른 것은 그 자체로 세션 혼선 근거가 아니다.

## 연결 경계 재검토

### FE 브라우저 → Vercel 프록시 → AWS

FE 공개 번들 `1yqk-p9z9a2su.js`에서 requiredHeaders 전체를 `xhr.setRequestHeader`로 넘기는 코드를 확인했다. Backend `S3PresignedUrlProvider.requiredHeaders` 및 기본 PresignedUrlProvider가 Content-Length를 내려준다. 브라우저는 이 헤더를 직접 설정할 수 없으며 본문에 맞게 관리한다([WHATWG Fetch](https://fetch.spec.whatwg.org/#forbidden-request-header)). 이번에는 업로드 등록·선택·분석 접수가 모두 성공했으므로 이 경고와 상태 조회502는 별개다.

수정 위치: Backend의 브라우저 업로드 헤더 계약에서 Content-Length를 제외하고, FE upload client도 금지 헤더를 걸러야 한다. 서버의 파일 크기·객체 무결성 검증과 실제 서명 계약은 유지한다. 서명 자체의 contentLength를 근거 없이 삭제하지 않는다.

공개 번들 `2lwvf_rbqylv7.js`의 pollAnalysis는 getStatus 예외에 finally만 있고 재시도 catch가 없다. `2mkwwbs8wmm_a.js`의 화면은 이를 error phase로 전달하며 상태 재확인 경로는 있지만 자동 회복은 없다. 로컬/원격 소스의 `src/lib/analysis-polling.ts`, `src/components/practice-session.tsx`와 일치한다. 상태 GET의 네트워크 오류·502/503/504에만 제한된 backoff를 적용하고 같은 analysis/session을 유지해야 한다. 새 analyze/retry POST를 자동으로 중복 전송해서는 안 된다. 인증/소유 관계 오류와 서버 FAILED는 따로 처리한다.

`src/app/api/backend/[...proxyPath]/route.ts`는 AWS 응답 상태를 전달하며 자체 fetch 실패는503으로 변환한다. AWS 로그의 정확히 같은 status 경로502로 이번 오류의 AWS 발생 지점을 확인했다. Vercel의 비공개 런타임 환경변수·배포 SHA는 조회하지 않았으므로 특정 FE commit이 운영 배포됐다고 단정하지 않는다.

### AWS 배포·readiness

Backend PR99는 16:04:40에 develop으로 병합됐으며 merge SHA는 `1bdd87650192e21ec75e228d760b2008fed5ed61`이다. 현재 `/opt/alpha/app.jar`도 이 SHA를 가리킨다.

- [첫 배포 실행](https://github.com/voice-coaching/VC-BE/actions/runs/37105135355): 2ae6737, 16:07:55 성공.
- [두 번째 배포 실행](https://github.com/voice-coaching/VC-BE/actions/runs/37105147412): 1bdd876, 16:10:32 성공.

develop push는 production deploy를 자동 실행한다. concurrency는 직렬화되지만 각각의 release를 순서대로 배포하므로 두 차례 재시작을 막지 않는다. `scripts/deploy.sh`는 단일 JAR symlink 교체 후 systemctl restart를 수행한다. 신규 분석 차단·진행 작업 drain·무중단 전환이 없다.

배포 성공 검사는 systemd active, 내부 actuator health, 공개 `/v3/api-docs`뿐이다. canonical verifier identity·공유 schema·실제 admission은 확인하지 않는다. 따라서 배포 작업 success와 canonical 분석 가능 상태가 다르다.

16:20 KST 실제 AWS worker-readiness/v2: HTTP503, canonicalSupported=false, canonicalAdmissionEnabled=false, canonicalStatus=NOT_READY, legacyReady=true. 새 Backend schema SHA는 `e3fd7531e2465885ae27200f7534ed6e8857af7ca21e60d54eda834d389805f3`다.

활성 verifier는 여전히 `/opt/alpha-canonical/releases/021e17aa5e3bb51170b2cd58cd86d70739177ea82fef324d4cc310d3cd490ed4`다. frozen 디렉터리는 core/llm/canonical_h5_20261003 세 개뿐이며, 활성 verifier 소스에는 scoredManifestSha256이 없다. 새 Backend의 CanonicalSemanticVerifier.assertInstalled는 새 core/manifest/lock 응답 필드를 요구하므로 기존 verifier로는 readiness 조건을 충족할 수 없다. readiness의 NOT_READY만으로 B2 인증 장애라고 단정하지 않는다.

### AWS ↔ RunPod

Pod ID s8hb5b0l5k4qlx, SSH 194.68.245.79:22132는 원격 PID1 환경으로 확인했다. 관리 API 조회는403으로 실패했으므로 관리 상태·GPU·요금은 이번에 재확인하지 못했다.

RunPod는 기존 core/H5 경로와 이전 v4 schema SHA `11f7c609421a0c80cb337cb23c1c203cef540f28eea7fce67b16f37577029d5b`를 사용한다. AWS와 schema도 다르다. `/health/canonical`의 HTTP 상태는200이지만 본문은 admissionEnabled=false, reasonCode=canonical_backend_not_ready다. `/health/services`의 ready/200은 canonical 접수 허용과 별개다.

이번 진행 중 작업은 heartbeat502를 견뎌 완료됐다. 하지만 journal의 일부 변경 요청은 불명확한 성공 결과를 무조건 재전송하지 않도록 되어 있어, 실행 중 Backend 재시작이 항상 안전하다고 볼 수 없다. 분석 접수만 잠시 차단하고 진행 작업·callback을 drain한 뒤 변경해야 한다.

## 복구·재발 방지 우선순위

1. 현재 배포를 하나의 호환 묶음으로 맞춘다. 새 AWS verifier + Backend + FE 계약 + RunPod 앱/파생 core/H5를 [채점 배포 순서](scoring-implementation-20261003.md)에 따라 설치하거나, 전체를 검증된 이전 계약으로 복구한다. readiness 우회나 강제 admission으로 해결하지 않는다.
2. Backend 자동 배포에 canonical 의존성 포장·설치와 schema/pin 비교를 포함한다. 신규 접수 차단→실행/보관/callback drain→전환→실제 canonical readiness 검사→접수 재개를 완료 조건으로 삼는다. 단일 restart의 공백을 없애려면 별도 포트의 준비된 인스턴스로 전환하는 방식이 필요하며 기존 worker/lease 경쟁을 함께 설계해야 한다.
3. FE 상태 GET에 제한 재시도와 “연결 복구 중” 상태를 추가한다. 조회 실패는 작업 FAILED와 구분하고 완료된 기존 결과를 다시 가져온다.
4. 브라우저 업로드 requiredHeaders에서 Content-Length를 제거하고 FE에서도 필터링한다.

이번 요청은 원인 분석·파이프라인 재검토이므로 코드·운영 설정 변경, 재시작, 새 분석 제출, PR 생성은 수행하지 않았다. 문서만 추가했다. 실제 추론·브라우저 QA는 수행하지 않았다.
