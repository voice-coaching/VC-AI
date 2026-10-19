# macOS에서 Seungun 독립 실행

Python 3.10 이상과 Docker Desktop의 Linux 컨테이너 환경을 사용한다. GPT 키와 기존 운영 서버 접속 정보는 필요하지 않다.

```sh
python3 standalone/seungun/run.py prepare
python3 standalone/seungun/run.py doctor
python3 standalone/seungun/run.py analyze --audio "$HOME/Recordings/audio.wav" --text "분석할 원고입니다." --output "$HOME/Recordings/result-1"
```

Intel Mac은 `linux/amd64`, Apple Silicon은 `linux/arm64`를 선택한다. macOS에 CUDA를 선택하면 중단하며 GPU를 조용히 CPU로 바꾸지 않는다. MPS/ANE 가속은 구현하지 않았다.

GPU 없는 환경에 배포 CUDA Kaldi를 그대로 쓰지 않으며, 고정 Kalpy 0.10.5의 conda-forge Linux ARM64 바이너리도 없으므로 Apple Silicon 이미지에는 동일 버전 공개 source tar와 SHA로 빌드하는 recipe를 포함한다. Kaldi/OpenFst 버전과 Python 3.14 경계를 유지한다. build는 regression test를 실행하지 않는다. ARM solver가 만들 package 집합은 image 안의 `/opt/seungun-build/installed-mfa.explicit`에 남는다.

**Apple Silicon native build와 실제 실행은 미검증이다.** amd64 에뮬레이션이나 원격 Linux 추론을 ARM 지원 완료 근거로 쓰지 않는다. ARM package/build 실패는 환경 준비 실패이며 다른 Kalpy·MFA 버전으로 자동 대체하지 않는다. Intel Mac의 실제 수용도 별도다.

Docker VM 메모리·디스크는 모델과 두 Python 환경을 수용해야 한다. 다운로드 모델은 약 1.35 GB이며 image·환경·scratch는 별도다. 이미지 build와 입력별 peak 사용량을 측정하지 않았으므로 최소 메모리 숫자를 보장하지 않는다. 분석 중 메모리 admission이 닫히면 진단 로그를 확인한다.

cache는 `--cache`로 이동할 수 있으며 준비 뒤에는 네트워크 없이 분석한다. scratch·socket·worker DB는 host bind mount가 아닌 container Linux 파일시스템을 사용한다. [자산](assets.md), [개발자 수용](developer-acceptance.md)을 참조한다.
