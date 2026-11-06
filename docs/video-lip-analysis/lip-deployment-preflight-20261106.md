# 입술 채점 운영 배포 사전 확인 — 2026-11-06

상태: **미배포**. 사용자의 배포 승인은 유지되며, 실제 분석 검사를 통과한 것으로 간주하지 않는다.

## 운영 관측

- 대상: J406.2020067.notebook.dlpc.
- `/health/deployment`의 sourceGitRevision: `e772721f542c06ede0559c7309970d4e321b0f19`.
- 내부 CORE: `a22fe17d4cb4ac0875672cf1b9b01f7435f8bbe53466f2693f0ad7749c074988`.
- 내부 H5: `aab733aa434cfa4566b03a71ab9e05ef48f06fa44c65cb59353f1b4a560fc6a9`.
- 확인 시 activeJobs=0, Direct ready=true.
- 영상 활성화 flag와 visual Python 설정은 미설정이고, 현재 내부/Direct H5에 시각 reference catalog가 없다.
- 얼굴 landmark 모델 파일은 설치돼 있다. 이것을 실제 영상 추론 통과로 해석하지 않는다.

## 진행한 준비

운영 CORE/H5는 수정하지 않았다. manifest에 결속된 소스·계약 파일만 별도 `/tmp`에 복사하여 신규 CORE/H5 후보를 패키징했다.
새로운 내부/Direct sidecar CORE의 이름이 충돌하지 않도록 builder에 directory 옵션을 추가했다.
두 CORE의 실제 runner에서 sidecar hook이 각각 정확히 한 번 대응함을 확인했다.

패키징 기록: `/tmp/intelligentai-lip-predeploy-20261106T114524Z/packaging.json`.
해당 후보의 catalog는 자료가 없는 관찰용 catalog다. 최종 입술 채점 릴리스나 활성화된 패키지가 아니다.
실제 참조가 완성되면 H5/lock을 다시 생성하고 그 최종 identity로 검사·등록해야 한다.
현재 후보 패키지에는 productionAllowlisted=false 또는 productionActivated=false가 표시돼 있다.

분리된 Python 3.12.13/jsonschema 4.25.1에서 기존 음성 회귀와 합성 입술 검사 33개를 다시 통과했다.
실제 음성/GPT 검사, 영상 특징 추출·참조 생성, 신규 enum의 Backend·Frontend 소비 검증은 통과한 것으로 보고하지 않는다.
PR 생성·merge·운영 재시작·환경 변경은 수행하지 않았다.

## 초기 입력 상태 — 이후 정상 B2 원본으로 복원

처음에는 지정 파일이 없었으며, 사용자 재확인 지시 이후 해당 경로에 파일이 나타났다.
최종 확인 경로: `/home/2020067/intelligentAI/tests/testvideo1.mp4`.

- 파일 크기: 230 bytes.
- SHA-256: `279dc5dd0122024052dc362e881c6d027829c9fbeb05d3bb94ce6cb11c67cfb0`.
- MP4의 ftyp/moov/mdat/moof 표지를 찾지 못했다.
- ffprobe: `moov atom not found`, `Invalid data found when processing input`, exit 1.
- 따라서 재생시간·음성 트랙·영상 프레임을 얻지 못했고, 이 파일로 실제 분석 검사를 실행할 수 없다.

파일을 임의로 복원하거나 정상 영상인 것처럼 참조·점수를 채우지 않았다. 원본 파일은 수정하지 않았다.
정상 원본 파일 확보 뒤 실제 참조 생성 및 최종 후보 검사, 계약 소비 확인, 새 릴리스 등록·drain·적용·외부 분석 확인 순으로 이어간다.
현재 정상인 음성 운영 버전은 그대로 유지한다.

기록 시각: 2026-11-06T11:47:37.493507+00:00.

## B2 복원 및 로컬 검사 후속 기록

위 230바이트 파일 문제는 해결됐다. 사용자 지정 B2 `test/video/`의 정상 원본과 manifest를
읽기 전용으로 다운로드한 뒤 크기·SHA-1·SHA-256을 검증했다. 기존 로컬 파일을 백업한 뒤 정상 원본으로 복원했다.
인증 키는 문서·소스·검사 산출물에 저장하지 않았다.

| 원본 | 바이트 | SHA-256 |
| --- | ---: | --- |
| testvideo1.mp4 | 2,097,382 | e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9 |
| testvideo2.mp4 | 1,193,779 | 1dd299f28ae9a0c2fcc2422e46ef3e162c62805a2267a19e1f7a277087a81379 |

두 대본은 사용자가 확인한 `쌀과 콩을 깨끗한 그릇에 담아요`다.
실제 MediaPipe 특징 추출은 각각 145/81 프레임을 관측했다. CORE/MFA는 원본 JSON을 보존한 sidecar CORE v3에서
두 영상 모두 통과했고 각각 12.255/7.747초 걸렸다. 이 단계는 GPT 호출 0회다.

private 근거 위치:

- `/var/lib/intelligentai/voice-smoke/lip-local-core-run6/report.json`
- `/var/lib/intelligentai/voice-smoke/lip-local-score-run3/report.json`

첫 번째 영상의 실측 reference 13개를 사용자 지정 임시 기준으로 만들었다. 전문가 calibration은 0개다.
참조 원본에는 자기 비교 점수를 부여하지 않는다. 두 번째 영상은 평가 대상 14곳 중 1곳에서만 참조 비교가 가능해
입술 총점은 null이다. 음성 종합점수는 각각 72.6/33.6으로 보존한다. 세부 한계와 계산은
[구현 기록](lip-score-implementation-20261106.md)에 정리했다.

기존 Frontend reader는 `PROVISIONAL`, `COMPARE_REFERENCE`, 선택적 `subScores`를 받지 못한다.
Backend의 공개 projection은 새로운 `subScores`를 DTO에 담지 않아 누락한다. 신규 영상 출력을 활성화하기 전에
소비 계약을 맞추고 실제 내부 result v6/handoff 및 공개 조회를 확인해야 한다. AI만 활성화하면 점수 표시가 깨질 수 있다.

후속 회귀 검사는 38개를 통과했다. 실제 영상의 전체 GPT 검사는 사용자에게 파생 분석 JSON·대본 전송 범위를 확인한 뒤
진행한다. 위 로컬 결과는 후보 head/tree 결속 배포 receipt 또는 실제 Backend 내부 요청 성공 증거가 아니다.
PR 생성·merge·운영 설정 변경·재시작은 아직 수행하지 않았다.

사용자가 두 영상의 파생 분석 JSON·대본 전송을 명시적으로 승인한 뒤 실제 GPT 검사를 두 건 수행했다.
각 POST 1회·유효 응답·원본 CORE 전송 SHA 일치를 확인했다. private 기록은
`/var/lib/intelligentai/voice-smoke/lip-gpt-derived-run1/report.json`이다.
이후 국소 입술 연습의 coverage 분리 수정이 있어 최종 후보 검사는 별도로 필요하다.
Frontend reader 확장 후 tsc, Backend DTO 확장 후 compileJava/processResources는 통과했다.
Backend/Frontend 후보는 별도 `feat/lip-score-readers-20261106` 브랜치이며 아직 운영 반영되지 않았다.
