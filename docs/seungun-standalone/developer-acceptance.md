# 개발자 수용과 현재 확인 범위

에이전트는 배포 source manifest 82개 파일의 hash 대조, export source/asset manifest 대조, Python 구문 컴파일, 문서 상대 링크와 Git diff 정적 확인을 수행한다. 자동 회귀·브라우저 자동화·synthetic/test fixture·모델/GPT 실행은 수행하지 않는다. 기존 vendor 모듈의 평가 함수를 import만으로 실행하지 않는다.

현재 Windows x64 CPU/CUDA, Intel Mac CPU, Apple Silicon ARM64 CPU의 **image build·실제 녹음·음질/성능 수용은 미수행**이다. 배포 Kaldi는 ELF에서 libcuda.so.1 의존성을 확인했다. AMD64 CPU는 공식 dependency 계약이 허용하는 같은 버전 CPU Kaldi를 선택한다. ARM64 CPU는 Kalpy 바이너리가 없어 동일 source를 CPU Kaldi에 연결하는 build 경로를 제공한다. Windows/macOS build 성공을 검증한 결과는 아니다. 이 문서를 QA 통과나 정식 플랫폼 지원 보증으로 읽지 않는다.

개발자가 확인할 순서는 다음과 같다.

1. Docker/Linux backend와 Python만 있는 별도 PC에서 main을 clone하고 README의 prepare·doctor를 수행한다. OS/arch/device·image ID와 환경 receipt를 기록한다.
2. 운영 계정·Backend/AWS/B2/OpenAI 설정 없이 본인 음성·원고를 분석한다. 초기 준비 뒤 네트워크가 차단된 분석 컨테이너에서 완료되는지 확인한다.
3. 사용 권한이 있는 동일 입력을 배포 기준과 로컬 profile에서 비교한다. 원본 digest/음소 mapping·정렬·Native/GOP·운율/reference·실패/누락 의미를 검토한다. GPT 채점/코칭은 비교 범위가 아니다.
4. 비교 전에 정수·enum·digest의 정확 일치와 부동소수점/시각의 허용 오차를 정한다. CPU/CUDA/ARM의 결과를 보고 통과용 오차를 사후 조정하지 않는다.
5. 한글·공백 경로, 입력 한도·무음/짧은/긴 음성, 손상/누락 cache·download 재개, 새 출력 폴더 충돌, timeout·Ctrl+C·worker 세대 종료를 확인한다. 다른 프로세스가 종료되지 않는지도 확인한다.
6. 준비에 필요한 디스크, 입력별 latency·RAM/VRAM을 측정한다. 메모리 admission과 실행 실패를 품질 성공으로 보고하지 않는다.

source export에는 계정 키·운영 env·H5/API client·사용자 음성·행별 연구 label을 넣지 않는다. 부모 manifest와 새 local derivative identity는 구분하며 기존 production allowlist·운영 schema·애플리케이션은 변경하지 않는다. main 배포 workflow의 전체 diff 판정도 독립 실행 QA와 별도다.
