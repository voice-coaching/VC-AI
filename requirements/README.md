# 재현 환경

`scorer-lock-linux-aarch64-cu130.txt`는 NVIDIA GB10에서 검증한 scorer
환경의 exact package snapshot이다.

- Ubuntu 24.04
- Linux aarch64
- Python 3.12
- CUDA runtime 13.0
- PyTorch 2.12.1+cu130

`torch==2.12.1+cu130`은 일반 PyPI 인덱스만으로 resolve되지 않는다.
2026-07-29 clean 환경 재생성에서 기본 PyPI 단독 설치가 실제로 실패했고,
공식 PyTorch CUDA 13.0 wheel index를 추가해야 같은 local-version build를
찾을 수 있음을 확인했다.

```bash
python3 -m venv /tmp/voice-coach-scorer-clean/venv
/tmp/voice-coach-scorer-clean/venv/bin/python -m pip install \
  --requirement requirements/scorer-lock-linux-aarch64-cu130.txt
/tmp/voice-coach-scorer-clean/venv/bin/python -m pip install \
  --no-build-isolation --no-deps .
```

위 URL에는 credential이 없으며 공식 공개 wheel source다. 사설 mirror나
인증 URL을 사용할 경우 그 값이나 token은 lock·문서·registry에 기록하지
않는다. 두 번째 명령은 저장소의 `voice-coach` package만 설치하며
dependency resolver를 다시 실행하지 않는다. 이를 생략하면 pytest의
직접 import는 `pythonpath=src`로 통과할 수 있어도 테스트가 띄우는 별도
Python subprocess에서 `voice_coach`를 찾지 못한다.

설치 뒤 다음을 확인한다.

```bash
python -m pip check
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
python -m pytest -q
```

현재 머신의 `nvidia-cusparselt-cu13` metadata는 aarch64 플랫폼 지원
경고를 보고하지만 WavLM Base+/Large CUDA BF16 smoke test와 프로젝트
테스트는 통과했다. 재생성 환경에서는 이 경고와 실제 kernel 실행을 모두
기록한다.

## 논문 Phase 1 음향 도구

`paper-acoustic-tools.in`은 PDF m4/m5의 Parselmouth와
openSMILE eGeMAPSv02를 확인하기 위한 **별도 연구 환경**의 direct pin이다.
현재 S104가 사용하는 `.venv-scorer`에는 설치하지 않는다.

```bash
python3.12 -m venv .venv-paper-acoustics
.venv-paper-acoustics/bin/python -m pip install \
  --requirement requirements/paper-acoustic-tools.in
```

첫 source build는 Parselmouth aarch64 wheel과 Python 개발 header가 없어
실패했다. 이후 시스템 설치나 sudo 없이 Ubuntu
`python3.12-dev=3.12.3-1ubuntu0.15`와
`libpython3.12-dev=3.12.3-1ubuntu0.15` deb를 임시 디렉터리에
다운로드·추출하고 기본·multiarch include/library 경로를 build에만
주입해 성공했다. 생성된 cp312 Linux aarch64 wheel SHA-256은
`8589ea46113a899abbe77375ddacbb998c1f7e456c0584225dbadd60fdf175f1`다.

검토된 wheelhouse가 있는 환경에서는 wheel hash를 먼저 확인한 뒤
다음 combined exact freeze를 사용한다. PyPI만 사용하는 새 ARM64
환경에서는 위 개발 header를 다시 임시 제공해야 하므로 이 명령만으로
source build가 보장되지는 않는다.

```bash
.venv-paper-acoustics/bin/python -m pip install \
  --find-links /path/to/reviewed-wheelhouse \
  --requirement requirements/paper-acoustic-tools-linux-aarch64.lock.txt
```

openSMILE만 재생성하려면 이전 subset freeze를 사용할 수 있다.

```bash
.venv-paper-acoustics/bin/python -m pip install \
  --requirement requirements/paper-opensmile-linux-aarch64.lock.txt
```

openSMILE open-source core는 private/research/education 용도이며 상업 제품은
별도 라이선스가 필요하다. Parselmouth/Praat는 GPL 계열이다. 따라서 이
환경과 생성 feature는 noncommercial research lineage로 격리하고, 제품
배포나 production dependency 승격 전에는 별도 라이선스 검토를 거친다.

`paper-acoustic-tools.in`은 direct requirement 파일이지 transitive lock이
아니다. exact combined freeze는
`paper-acoustic-tools-linux-aarch64.lock.txt`, openSMILE-only subset은
`paper-opensmile-linux-aarch64.lock.txt`에 기록했다. `pip check`는
통과했다. 16 kHz 1초 합성 sine에서 eGeMAPSv02 functionals
`[1, 88]`, finite 88/88과 Parselmouth Pitch 97/97 voiced,
중앙 F0 `180.000170208 Hz`를 확인했다. 실제 코퍼스 cache·reference
normalization·ablation은 아직 완료되지 않았다.

## 논문 Phase 1 torchcrepe CPU 환경

torchcrepe는 PyTorch 의존성이 커서 위 음향 도구 환경 및 S104
`.venv-scorer`와 분리한 `.venv-paper-torchcrepe`에서만 검증했다.
`paper-torchcrepe-linux-aarch64.lock.txt`는 Python 3.12/Linux aarch64의
성공한 exact version snapshot이며, Torch 계열 package는 공식 PyTorch
CPU index에서 받는다.

```bash
python3.12 -m venv .venv-paper-torchcrepe
.venv-paper-torchcrepe/bin/python -m pip install \
  --requirement requirements/paper-torchcrepe-linux-aarch64.lock.txt
.venv-paper-torchcrepe/bin/python -m pip check
```

현재 확인 환경은 `torchcrepe==0.0.24`,
`torch==2.11.0+cpu`, `torchaudio==2.11.0+cpu`이며 CUDA를 사용하지 않는다.
이 freeze도 모든 wheel hash를 강제하는 `--require-hashes` lock은 아니다.
실제 180 Hz 합성 smoke와 raw backend의 무음/NaN 경계, 정본 파형 생성
순서는
`docs/paper/PHASE1_TORCHCREPE_ENV_GATE_20260729_KO.md`에 기록했다.
프로젝트 adapter는 입력 finite/normalized PCM, CPU-only, RNG state,
output shape/bounds와 무성 정책을 별도로 fail-closed 검증한다. 실제
한국어 corpus agreement·calibration과 논문 성능은 아직 완료되지 않았다.

## 논문 Phase 1 g2pK offline 환경

g2pK는 import 시 CMUdict가 없으면 NLTK download를 호출하므로
`.venv-paper-g2pk`와 그 내부 NLTK data root에 격리한다.
`paper-g2pk-linux-aarch64.lock.txt`는 Python 3.12/Linux aarch64에서
실제 설치·smoke에 성공한 exact version snapshot이다.

```bash
python3.12 -m venv .venv-paper-g2pk
.venv-paper-g2pk/bin/python -m pip install \
  --requirement requirements/paper-g2pk-linux-aarch64.lock.txt
.venv-paper-g2pk/bin/python -m pip check
NLTK_DATA="$PWD/.venv-paper-g2pk/nltk_data" \
  .venv-paper-g2pk/bin/python -m nltk.downloader \
  -d "$PWD/.venv-paper-g2pk/nltk_data" cmudict
```

다운로드 뒤에는 zip·추출 dictionary·README의 세 SHA-256을
`docs/paper/PHASE1_G2PK_ENV_GATE_20260729_KO.md`와 대조한다.
프로젝트 adapter는 이 digest를 다시 확인하고 runtime network download를
금지한다. 이 freeze도 모든 wheel hash를 강제하는 fully hashed lock은
아니다. g2pK expected phone은 AI Hub 관측 TextGrid나 MFA predicted
alignment를 대체하지 않는다.
