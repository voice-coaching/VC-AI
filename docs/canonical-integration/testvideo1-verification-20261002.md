# testvideo1 실제 음성 분석 검증·수정 (2026-10-02)

사용자가 지정한 영상의 실제 API 검증을 요청했고 전용 검증 계정 생성을 승인했다.
이 문서는 해당 단일 입력의 운영 실행 기록이다. 광범위 회귀·브라우저·발음 품질 QA가 아니다.

## 입력과 재현

- Pod: `s8hb5b0l5k4qlx`.
- 현재 Pod에는 과거 원본 경로가 없어 로컬 `tests/testvideo1.mp4`의 해시를 대조해
  전용 `/run/intelligentai/testvideo1-20261002/`에 복원했다.
- 영상 SHA-256: `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`.
- ffmpeg로 PCM signed 16-bit, 16 kHz, mono WAV를 추출했다. 길이 4.842688초,
  155044 bytes, SHA-256 `3ec858673030f563fc5deef5d0bed4aac507a998c72545ead5959fab2f62515a`.
- 대본은 원본 전문가 사례와 같은 `쌀과 콩을 깨끗한 그릇에 담아요`.
- 기존 사용자 분석이나 journal을 재실행·초기화하지 않았다.

## 재현된 원인과 수정

1. **G2P 사전 경로 누락**: canonical child allowlist 및 model subprocess 환경에
   `AI_ANALYSIS_NLTK_DATA_ROOT`/`NLTK_DATA` 전달이 없어 g2pK가
   `/nonexistent/nltk_data`에 cmudict를 다운로드하려다 `PermissionError`가 발생했다.
   준비된 읽기 전용 사전 경로만 전달한다. 서비스 자격증명을 core에 상속하지 않는다.
   부팅 검사에서도 실제 core Python으로 NLTK 사전 조회·G2p 초기화를 수행한다.
2. **실행 중 입양된 zombie 미회수**: MFA 정렬 자체가 성공해도 worker 종료 후
   resource tracker가 service subreaper의 zombie 자식으로 남았다. 기존 guard는
   command 종료 후에만 회수했고, frozen supervisor는 그 전에 3초간 생존자를 검사해
   `OWNED_CHILD_SURVIVOR`로 실패했다. guard 실행 루프에서 종료된 입양 자식만
   `waitpid(WNOHANG)`로 회수한다. 주 command의 exit status는 Popen에 맡기며 살아 있는
   하위 프로세스를 이 경로에서 종료하지 않는다. 취소·deadline 전체 subtree 회수는 유지한다.

수정 전 동일 영상: `SYSTEM_FAILURE / dependency`, MFA receipt는 생성됐지만 종료 실패.
수정 후 동일 영상: launcher exit 0, `ACCEPT`, core `ok`, expected phones 28,
자음 위치 근거 15개, 허용된 교정 후보 0개. MFA 정상 종료, survivors `[]`.
동결 R/S7·모델·정렬 beam·후보 정책·공개/내부 API schema는 변경하지 않았다.

최근 사용자 분석 50에는 별도로 MFA `NoAlignmentsError`도 있었다. 이를 발음 오류로
해석하지 않는다. 지정 영상의 두 결함 수정 성공이 모든 입력의 MFA 성공을 보증하지 않는다.

## 운영 배포

| 항목 | 값 |
|---|---|
| 수정 소스 | `0adcdef3c67434a13f61534240879d2f195640a3` |
| 배포 시각 | 2026-10-02 08:29:50 UTC / 17:29:50 KST |
| 기존 base sourceRevision | `32d98e39767b56697bc7e58fb50e3e9d61432349` 유지 |
| overlay SHA-256 | `41f30bdb15f02c2f2141845fddfb7b95b9004c7a4fde5b406943ed1f4e518d1d` |
| bundle SHA-256 | `018556b184037523233231361a430e050d65434ad608576ff4b82c96c31d09d6` |
| 원복 백업 | `/workspace/intelligentai/deployment/canonical-runtime-backup-20261002T082946Z` |

`canonical_process_guard.py`, `canonical_producer.py`, `canonical_executor.py`,
`canonical_settings.py`를 실행 사본·영구 overlay에 함께 반영했다. 기존 boot/manager
정상 종료·재개, prepare_http, 양쪽 bootstrap 및 canonical admission 확인을 수행했다.
키·Backend JAR·FE·gateway·TTS는 변경하지 않았다.

## 실제 Backend API 실행

- 전용 검증 사용자 40, 사용자 문장 49, 학습 세션 97, 녹음 61.
- 회원가입 → 사용자 문장 생성 → 세션 생성 → 업로드 URL → 실제 WAV PUT → 녹음 등록:
  성공, 녹음 품질 `PASS`.
- 녹음 선택 후 기존 analyze API에 `X-Analysis-Result-Schema:
  voice-coaching.runpod-analysis-result.v4`를 전달했다.
- 분석 51, 접수 2026-10-02 17:35:45 KST.
- request ID: `cab6abe4-1d8d-4e6e-b865-b77e9d1f81ba`.
- execution ID: `b691103e-fe98-4828-bc3e-23a5a089ae85`.
- core launcher exit 0, `ACCEPT / ok`, MFA 근거 생성·종료 회수 성공. 수정 전 G2P와
  zombie 결함은 실제 API 실행에서도 해소됐다.
- 동결 LLM bridge는 `NO_PERMITTED_COACHING_CONTENT`, GPT dispatch 0이다.
  이 입력은 개인 교정 후보가 없어 GPT 생성을 호출하지 않는 것이 정상이다.
- 표현 결과 조립·공유 schema preflight 통과, 근거 5개가 Backend journal에 PREPARED.
- 이후 journal status의 JSON wire 결함으로 실패. 17:40:11 KST 공개 상태 `FAILED`,
  `canRetry=true`. B2 PUT·VERIFIED receipt·callback 적용은 이 시도에서 도달하지 못했다.

## 추가 발견: Backend JSON wire 결함

운영 Backend는 Spring Boot 4.1.0이며 MVC 기본 Jackson 3와 내부 계약의 Jackson 2
`JsonNode`가 섞여 있었다. DB journal 원본은 유지됐지만 status 응답 association/metadata가
원래 JSON 대신 `nodeType`, `object`, `array` 같은 bean 속성으로 전송됐다.
RunPod는 이 잘못된 응답을 schema 검증에서 거부했다. 이를 허용하도록 RunPod schema를
완화하거나 원본을 임의 변환하지 않는다.
[Spring Boot 공식 JSON 설명](https://docs.spring.io/spring-boot/reference/features/json.html)

Backend 수정은 journal reserve/status 및 evidence register/status의 응답만
`RunPodContract.encode`로 검증한 UTF-8 JSON bytes + `application/json`으로 반환한다.
receipt의 중첩 association에도 같은 문제가 있어 함께 수정했다. 전역 ObjectMapper,
URL/필드/null/상태 코드/인증/worker 소유 관계/DB/schema는 변경하지 않는다.

- Backend [PR #95](https://github.com/voice-coaching/VC-BE/pull/95), base `develop`,
  branch `fix/canonical-json-wire-20261002`, hotfix `c036312`.
- `a241a99` 운영 rollout이 아직 develop에 없어 PR에는 그 선행 커밋도 포함된다.
- `bootJar -x test` 성공, 회귀 테스트 미실행.
- 운영 JAR `7f9f4c988c81fe7e0c73ba0fce52db1db1482519f43fb39fcc0b08c50073ce12`와
  새 JAR `0eb49d7541915000ebd25bbd7b9ef791b29bce79240de91ccf6b9eda40c4093c` 비교:
  analysis/controller 외 클래스·리소스·의존성 해시 동일.
- 현재 Backend 운영 JAR 교체 승인 대기. 교체 전에는 전체 API 문제가 해결됐다고
  보고하지 않는다. 승인 후 정상 retry API로 새 실행을 만들고 보관·callback까지 확인한다.

17:55:10 KST RunPod `/readyz=200/ready`, canonical `executorConfigured=true`,
`admissionEnabled=true`를 재확인했다. readiness는 위 Backend wire 결함이나 전체 E2E 성공을
보증하지 않는다. 원본/GPT/품질/FE QA와 단일 API 실행 결과를 구분한다.

계정 인증값·signed upload URL·원본 근거는 공유 문서나 Git에 보관하지 않는다.
검증 계정·세션은 기존 사용자와 분리했고 학습 완료·시험 점수 조작은 하지 않는다.
