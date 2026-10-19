# Seungun 독립 음향 파이프라인

배포된 Native/GOP core `f6d56e1d…`의 소스와 MFA DirectAligner 실행 경로를 포함한다. 입력은 로컬 음성·원고이며, 결과는 g2pK·MFA 구간·Native CTC·P1 근거·GOP·운율/reference 비교다. **GPT 채점·코칭·OpenAI 키·Backend·AWS·RunPod/DLPC 연결은 필요하지 않다.** 기존 `seungun/` 연구 detector를 재학습하는 도구가 아니다.

## 실행

필수 준비는 Python 3.10 이상, 실행 중인 Docker Desktop의 Linux 컨테이너 지원이다. Windows는 WSL2 backend를 사용한다. 저장소 루트에서 다음을 수행한다.

```sh
python standalone/seungun/run.py prepare
python standalone/seungun/run.py doctor
python standalone/seungun/run.py analyze --audio recording.wav --text "원고를 여기에 입력합니다." --output my-result
```

Windows에서 `python` 대신 `py -3`, macOS에서 `python3`를 사용할 수 있다. 최초 `prepare`는 이미지 build와 고정 모델 다운로드를 수행한다. 다운로드 도중 끊겼다면 `fetch`로 재개한다. `analyze`와 `doctor`는 네트워크가 없는 컨테이너에서 실행한다. 새 결과 디렉토리를 지정해야 하며 이전 결과를 덮어쓰지 않는다.

CUDA는 Windows WSL2/Linux의 NVIDIA 환경에서만 명시적으로 선택한다.

```sh
python standalone/seungun/run.py --device cuda prepare
python standalone/seungun/run.py --device cuda analyze --audio recording.wav --text "원고" --output gpu-result
```

기본 CPU를 CUDA로 자동 전환하거나 실패한 추론을 재실행하지 않는다. 취소는 Ctrl+C로 하며 컨테이너 종료 시 해당 분석 프로세스를 정리한다. 원고는 최대 500자, 음성 파일은 최대 20 MiB이며 실제 음성 길이·유효성 판정은 배포 코어 정책을 따른다.

## 포함 범위와 확인 상태

- Git: 배포 core 실행 소스·MFA loader·정책·집계 reference·모델 manifest·플랫폼별 설치 recipe.
- 최초 다운로드: 공개 Native/MFA 모델·NLTK 사전 23개 파일, 총 1,349,595,536 bytes. 정확한 크기·SHA·출처는 `manifests/assets.json`이다. cache 기본 위치는 이 디렉토리의 `.cache/`이며 Git에서 제외한다.
- 로컬 변형: CPU/CUDA device 설정과 로컬 resource 연결. 원본 source hash와 변형 core identity를 각각 기록한다. 운영 allowlist는 변경하지 않는다.
- 출력: `evidence.json` 원본 음향 근거, `prepared.wav`(생성된 경우), `result.json` 로컬 실행·출처, `runtime.log` 로컬 진단.
- **확인 범위:** 소스 hash·Python 컴파일·문서/manifest 정적 확인. Windows/macOS의 이미지 build·실제 녹음 실행·품질/성능 수용은 개발자가 확인해야 한다. doctor 성공은 실제 추론 성공이 아니다.

AMD64 Python base와 CUDA MFA는 실제 배포 conda lock을 사용한다. 배포 Kaldi가 `libcuda.so.1`에 직접 연결되므로 AMD64 CPU는 공식 Kalpy 0.10.5의 dependency 계약에 맞춰 같은 버전의 CPU Kaldi를 선택한다. Apple Silicon은 Linux ARM64 Kalpy 바이너리가 없어 고정 source archive에서 CPU Kaldi에 연결해 빌드한다. ARM build·전체 실행 동등성은 미검증이며 지원 완료로 표기하지 않는다. CPU/CUDA 역시 수치 동등성을 자동 보장하지 않는다.

소스·모델 최초 준비에는 인터넷 접근이 필요하지만 API 키는 요구하지 않는다. 설치 후 실제 분석은 로컬이다. 모델을 Git에 넣거나 사용자 음성을 업로드하지 않는다.

자세한 안내: [Windows](../../docs/seungun-standalone/setup-windows.md), [macOS](../../docs/seungun-standalone/setup-macos.md), [자산·출처](../../docs/seungun-standalone/assets.md), [출력 계약](../../docs/seungun-standalone/output-contract.md), [개발자 수용](../../docs/seungun-standalone/developer-acceptance.md).
