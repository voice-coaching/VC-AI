# Backend 인계: 원본 영상 직접 업로드 권한

2026-11-08 구현. 상세 계약은 [브라우저 원본 영상 API v1](../api/browser-original-video-v1.md).
AI 브랜치 `feat/browser-original-video-20261108`, Backend 브랜치
`feat/browser-video-upload-grant-20261108`. 운영 설정 변경·재시작·배포는 수행하지 않았다.

## 구현 범위

- Backend `POST /api/training-sessions/{sessionId}/video-analysis-upload-grants`를 구현했다.
  Access Token 서명/유효기간·활성 로그인·실제 세션 소유권을 확인하고 서버가 선택한
  문장과 업로드 원본 해시에 묶인 300초 Ed25519 grant를 발급한다.
- Backend 호환 인증 필터의 X-User-Id 입력을 이 권한의 신뢰 근거로 사용하지 않는다.
  또한 이 revision의 `findByIdAndUserId` 기본 메서드가 owner 조건을 생략하므로,
  새 reader는 반환 entity의 userId를 명시적으로 비교한다. 기존 필터·repository의
  전역 정책은 이 변경에서 수정하지 않았다.
- AI는 새 브라우저 전용 경로에서 로컬 공개키로 grant를 검증하고 MP4/WebM/MOV 원본을 받는다.
  정규화·실행·저장에 Backend 호출이 없으며 기존 서버 Bearer API는 유지한다.
- grant 재발급은 같은 immutable binding에 같은 AI 작업을 연결한다. jti·iat·exp가
  바뀌어도 새 실행을 만들지 않는다. 이미 업로드된 원본·CORE·결과를 덮어쓰거나
  재시작 중단 작업의 추론/GPT를 자동 재실행하지 않는다.

## Backend 적용 파일

Backend 작업 폴더: `/home/2020067/VC-BE-browser-video-20261108`.
기준 revision `6e16f6f186d4991d6dd0a8040c757e5d5150e4ca`에서 분리했다.

`training/` 아래 변경:

- controller/BrowserVideoGrantController.java
- controller/dto/BrowserVideoGrantRequest.java 및 BrowserVideoGrantResponse.java
- application/BrowserVideoGrantService.java
- domain/port/BrowserVideoSessionReader.java 및 BrowserVideoGrantEncoder.java
- infrastructure/BrowserVideoSessionReaderImpl.java 및 BrowserVideoGrantSigner.java
- 관련 BrowserVideo*Test.java

후속 운영 요청의 Backend 후보에는 공개키 endpoint용
`controller/BrowserVideoPublicKeysController.java`, 최초 키 설치용
`scripts/deployment/browser_video_keys.py`와 관련 검사 및 기존 CI 배포 연결도 포함된다.
개인키는 AWS의 `/etc/alpha-browser-video/private.pem`에서 생성·보존하며 J406이나
GitHub로 전송하지 않는다. AI는 공개키 endpoint를 배포 시에만 읽어 로컬에 고정한다.

기존 DB table·migration·공식 점수 검산표 게이트를 추가하지 않는다. 코드·설정 검토 후
최신 develop에 적용하고 Backend 담당자가 검증·배포한다. AI 코드만 배포해도 Backend
발급 API가 생기는 것은 아니다. 개인키는 Backend에만, 공개키는 AI에만 설치한다.

## 결과 수집 연결 시 필요한 작업

이 변경은 권한 발급과 AI의 직접 업로드·실행 연결을 구현한다. Backend의 기존
recording/analysis 이력 생성·결과 수집 worker를 브라우저 영상 작업에 자동 연결하는
변경은 포함하지 않는다. 기존 오디오 independent collector에 영상을 넣어 우회하지 않는다.

Backend가 이력에도 저장하려면 grant 발급 시 owner·sessionId·clientRequestId·원고/원본
digest의 매핑을 보존하고, 서버 인증으로 다음 절차를 연결한다.

1. `/v2/audiovisual-analysis-jobs?clientRequestId=...`에서 AI jobId를 조정한다.
2. 같은 UUID의 상태·resultAvailable을 조회한다. AI 재접수·GPT 재생성을 수집 재시도로 쓰지 않는다.
3. `/result` bytes를 SHA와 함께 보존하고 score·details·coaching·visual을 그대로 전달한다.
4. 필요 시 `/artifacts/{kind}`와 manifest에 해당하는 원본들을 서버 인증으로 수집한다.
   MEDIA_RECEIPT의 sourceSha256은 원본이며 canonicalPcm/VideoSha256은 AI 정규화 결과다.
5. 실제 저장 후 결과 SHA로 `/ack`한다. 브라우저 grant에는 artifacts/ack 권한이 없다.

Frontend가 즉시 결과를 표시할 때는 브라우저 `/result` 응답의 기존
`score.overallScore`·`score.details`·`coaching`·`visual`을 사용한다. Backend의 기존
분석 ID가 아직 없는 이 경로에서 UUID를 DB 정수 ID로 가장하지 않는다.
입술 null은 기준/관측 부족일 수 있으므로 프론트가 전체 결과를 거부하거나 0점으로
바꾸지 않도록 [기존 점수 명세](../api/frontend-score-details-response.md)를 함께 적용한다.

## 운영 전 확인

명세의 양쪽 설정·키·Origin allowlist, AI HTTP 런타임 cryptography 설치,
ffmpeg H.264/AAC encoder, AWS 프록시 OPTIONS/PUT/크기·시간 제한을 확인한다.
권한 발급은 Backend 자체의 사용자 인증·세션 DB만 필요하며 AI readiness를 기다리지 않는다.
AI browser readiness는 공개키와 로컬 영상 실행기만 확인한다.

오프라인 인증/업로드/정규화·Backend 단위 검증과 실제 Seungun/GPT 전체 검증은 별개다.
[실행한 검사 기록](../api/browser-original-video-verification-20261108.md)을 함께 전달한다.
PR/merge 전 실제 분석 검증은 저장소 AGENTS.md의 범위를 따른다. 과거 승인된 단일
GPT 시험을 새 버전의 시험으로 재사용했다고 보고하지 않는다.
