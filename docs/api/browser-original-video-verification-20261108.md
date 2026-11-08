# 원본 영상 브라우저 API 검증 기록

관측일 2026-11-08, J406의 격리 작업 폴더. 운영 서비스·환경 설정·기존 spool·사용자
작업은 변경하지 않았다. 이 기록은 실제 분석/GPT 전체 통과나 배포 완료 증빙이 아니다.

| 검사 | 결과 |
|---|---|
| AI `tests/analysis/test_*.py` | 최종 71건 통과 |
| 신규 브라우저/정규화 `test_browser_video.py` | 11건 포함 |
| Backend Java 컴파일 및 BrowserVideo*Test | 9건 통과, 실패·오류 0 |
| Backend 실제 Java Ed25519 signer → AI Python verifier | 임시 키·fixture로 서명·source record·UTF-8 원고 해시 호환 확인 |
| 합성 WebM 실제 정규화 | 0.25초 A/V 시작 차이 보존, MP4 재디코딩과 WAV 샘플 일치 |
| 지정 `testvideo1.mp4` 격리 복사본 정규화 | 원본 2,097,382 bytes 보존, receipt·PCM 샘플 일치, 약 5.664초 |
| 변경 Markdown 상대 링크·git diff --check | 통과 |
| 추론·GPT API 호출 | 미수행, 새 GPT POST 0건 |
| 운영 변경·재시작·배포·브라우저 화면 QA | 미수행 |

오프라인 검사는 인증 누락·변조·만료·다른 issuer/audience/typ/alg/kid, origin 제한,
권한 재발급 멱등성, 다른 소유자 격리, MIME·크기·digest 오류, 원본 보존, 재시작 후
binding 복구, 미업로드 예약 만료, 결과 bytes 보존, 오류의 CORS 헤더, 브라우저
readiness와 기존 영상 readiness 분리를 포함한다. Backend 검사는 로그인·활성 세션·
소유자·허용 Origin·기존 얼굴 처리 동의·크기·서명 생성과 비활성 상태를 확인한다.

AI 검사에는 canonical Python의 기존 jsonschema 4.25.1을 유지하고 HTTP 테스트용
패키지 경로를 그 뒤에 추가했다. 운영 패키지나 frozen CORE/H5를 수정하지 않았다.
Backend는 Java 21 및 캐시된 Gradle 의존성으로 offline 빌드했다.

지정 MP4 SHA-256:
`e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`.
새 정규화 시험은 전용 임시 디렉토리를 사용하고 끝나면 그 복사본만 정리했다.
원본 시험 파일·기존 분석 결과·운영 작업은 그대로 두었다.

실제 원본 업로드 → Seungun/입술 분석 → GPT 1회 → 점수/피드백 생성 및 운영 프록시를
포함한 브라우저 검증은 별도다. PR 전 실제 분석 검증은 AGENTS.md 요구를 따른다.
이 후보에서는 PR·merge·push·배포를 수행하지 않았다.


## 후속 배포 요청 중 발견·수정한 사항

초기 후보 52be2e1의 실제 음성·장애 7건은 통과했으나 새 원본 영상 작업은 visual
분기의 native 초기화 실패로 배포 기준을 통과하지 못했다. GPT는 요청당 1회, 총 8회
사용했고 자동 재시도하지 않았다. 기존 시험 결과·원본·CORE·결과 JSON은 보존했다.

MediaPipe 1.0.1의 buffer 모델 초기화 중 malloc heap 오류와 -6/-11 종료를 로컬에서
확인했다. 동일 모델의 파일 로더 비교가 통과해 봉인된 익명 파일을 이용하도록 수정했다.
네트워크 격리·자원 제한·모델 SHA·품질/채점 기준은 유지했다. 또한 지원되는 원본
HEVC/H.264·AAC 트랙을 불필요하게 변환하지 않도록 정규화 v2를 추가했다.

수정본은 서비스 UID로 기존 실패 영상·원본 트랙 보존 영상의 로컬 feature 추출 4회,
각 145프레임·시간축 검증과 WAV/영상 PCM 일치를 통과했다. 오프라인 검사는 73건이다.
새 후보의 전체 실제 분석·GPT 검증과 AI PR·운영 배포는 이 기록 시점에 미완료다.
초기 후보의 GPT 결과를 수정본의 실제 GPT 결과로 재사용하지 않는다.
