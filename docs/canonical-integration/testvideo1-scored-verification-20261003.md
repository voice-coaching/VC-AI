# testvideo1 scored API 재현과 provenance 복구

사용자가 실제 음성 추출·API 재현·수정·배포를 명시적으로 요청했다. 이 단일 입력의
운영 확인을 수행하며 기존 사용자 기록을 재실행하거나 DB 상태를 조작하지 않는다.

## 확인된 원인

운영 분석 62·63은 새 scored core의 CORE 단계에서 종료했다. traceback은
`run_canonical_batch.provenance` → `manifest.collect_provenance` → `_git_state`를 가리킨다.
해당 함수는 실제 `git rev-parse HEAD`와 `git status --porcelain`을 요구한다.
원래 core release의 Git HEAD는 `f27d6b31cec30b1174372e51c6bf5d19ee359247`이고,
새 scored core는 파일 manifest만 내보내면서 `.git`이 빠져 Git 조회가 exit 128이었다.
같은 서비스 UID 10001에서 원본은 성공하고 scored core는 실패함을 확인했다.
HTTP 부모는 실패 후 heartbeat를 중지하므로 Backend에는 나중에 execution timeout으로 보였다.

## 수정 범위

- `deploy/runpod/stage_canonical_provenance.py`: 부모·대상 manifest와 원본 파일 hash를
  확인한 뒤 원본 release의 실제 Git 객체·commit을 복제한다. 임의 commit이나 출처를
  만들지 않는다. 기존 scored 소스·하네스 pin·채점 기준을 바꾸지 않고 실제 dirty 상태를 보존한다.
- `src/voice_coach/backend_analysis/canonical_provenance.py`: 서비스 환경에서 release root,
  실제 parent commit 및 Git status 조회를 검사한다. 신뢰 디렉터리는 해당 release 하나로
  제한하고 사용자·시스템 Git 설정을 상속하지 않는다.
- `canonical_producer.py`, `canonical_executor.py`: 프로세스 시작 전과 요청 실행 전에
  provenance 의존성을 확인하여 모델 실행 후에야 이 배포 누락이 드러나는 문제를 막는다.

## 지정 파일 실제 요청

- 로컬 및 Pod 영상 SHA-256: `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`.
- ffmpeg로 새로 추출한 WAV: PCM s16le/16 kHz/mono, 155044 bytes, 4843 ms.
- WAV SHA-256: `3ec858673030f563fc5deef5d0bed4aac507a998c72545ead5959fab2f62515a`.
- 대본: `쌀과 콩을 깨끗한 그릇에 담아요`.
- 기존 전용 검증 사용자 40과 문장 49를 사용했다. 인증값·signed URL은 출력하지 않는다.
- FE 운영 `/api/backend` 프록시를 통해 로그인 → 세션 113 생성 → 실제 WAV 업로드 →
  녹음 77 등록(`PASS`) → 선택 → canonical v4 분석 64 접수까지 확인했다.
- 최초 접수 시각은 2026-10-03 21:07:24 KST이며 수정 전 재현 요청이다.

실행 결과와 최종 배포 근거는 아래 후속 관측에 기록한다. readiness 성공을 실제 채점
응답 성공으로 대신하지 않는다.
