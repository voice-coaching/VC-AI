# Seungun 독립 실행 구현과 main 인계 — 2026-10-19

사용자 후속 결정에 따라 GPT 채점·코칭·H5·API 접근은 범위에서 제외했다. 이전 계획의 full/evidence 구분은 폐기하고 단일 로컬 음향 분석 경로를 구현했다.

## 구현

- 실제 배포 core `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`의 manifest 82개 파일을 읽기 전용 hash 대조했다. 배포 설정은 `dlpc-4cpu`이며 미배포 J406 성능·compact H5 후보는 포함하지 않았다.
- [독립 패키지](../standalone/seungun/README.md)에 배포 acoustic core source closure·추가 MFA loader·정책·집계 reference를 포함했다. H5·OpenAI transport·Backend client를 포함하거나 호출하지 않는다.
- 공개 Native/MFA·NLTK 23개 파일의 주소·크기·SHA manifest와 재개 다운로드·검증·atomic 설치를 구현했다. 모델 weights·운영 키·사용자 음성은 Git에서 제외한다.
- `prepare`, `fetch`, `doctor`, `analyze`를 제공한다. 분석은 네트워크가 없는 Linux 컨테이너에서 수행하고 CPU/CUDA를 명시적으로 선택한다.
- 배포 conda explicit lock과 음향 package pins를 포함했다. 배포 Kaldi의 libcuda.so.1 의존성을 ELF에서 확인했으므로 AMD64 CPU는 공식 dependency 계약이 허용하는 CPU Kaldi를 선택한다. Apple Silicon에는 Linux ARM Kalpy 0.10.5 바이너리가 없어 같은 버전 source를 CPU Kaldi에 연결하는 build recipe를 제공한다.
- 원본 source는 보존하고 로컬 경로·device 변형과 새 core manifest identity를 별도로 기록한다. 기존 production registry·wire 계약·서버 기동 설정은 변경하지 않는다.
- 결과는 원본 evidence bytes·prepared WAV·로컬 receipt다. 점수·GPT 텍스트·Backend ID·보관 성공을 만들지 않는다.

## 확인과 남은 수용

에이전트 확인은 source/asset manifest 대조·Python 컴파일·문서 링크·Git diff 정적 확인이다. Windows/macOS의 image build·음성 처리·수치 동등성·품질/성능 수용은 개발자가 직접 수행해야 한다. 자동 회귀·브라우저 자동화·fixture/test 실행·생성·실제 추론은 수행하지 않는다.

[Windows 설치](seungun-standalone/setup-windows.md), [macOS 설치](seungun-standalone/setup-macos.md), [자산 출처](seungun-standalone/assets.md), [출력 계약](seungun-standalone/output-contract.md), [개발자 수용](seungun-standalone/developer-acceptance.md)을 제공한다. ARM build 경로가 있다는 사실을 Apple Silicon 지원 검증 완료로 표현하지 않는다.

## main 반영 범위

기존 작업 트리에는 문서 정리·터널 수정·v6 통합 등 다른 작업이 함께 있다. 별도 worktree에서 최신 원격 main을 기준으로 `standalone/seungun/`, 위 문서와 필요한 목차 항목만 commit·push한다. GPT/H5·운영 API 변경과 다른 미커밋 파일은 포함하지 않는다. 사용자 제공 GitHub 토큰은 인증 프로세스의 환경에만 두고 파일·Git·문서에 저장하지 않는다.

main push의 기존 CI/배포 workflow는 저장소 설정에 따라 동작한다. 독립 패키지는 기존 deploy client의 runtime source prefix 밖에 두지만, 실제 deploy revision과 target 전체 diff는 별도다. main 업로드를 production 추론/품질 QA나 운영 core 승격으로 보고하지 않는다.
