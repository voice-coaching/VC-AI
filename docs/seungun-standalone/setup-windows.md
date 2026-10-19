# Windows에서 Seungun 독립 실행

Python 3.10 이상과 Docker Desktop의 WSL2 Linux backend를 설치하고 실행한다. GPU를 쓰려면 NVIDIA GPU와 WSL 호환 driver·container GPU 지원이 필요하다. CPU 기본 실행에는 GPU가 필요하지 않다.

PowerShell에서 clone한 저장소 루트로 이동해 실행한다. 공백·한글 경로는 PowerShell 인자 인용 규칙을 따른다.

```powershell
py -3 standalone/seungun/run.py prepare
py -3 standalone/seungun/run.py doctor
py -3 standalone/seungun/run.py analyze --audio "C:\Recordings\내 음성.wav" --text "분석할 원고입니다." --output "C:\Recordings\분석결과1"
```

준비 중 다운로드가 끊기면 `py -3 standalone/seungun/run.py fetch`를 다시 실행한다. 손상된 완성 cache 파일은 자동 덮어쓰지 않으며 해당 파일을 사용자가 옮기거나 새로운 `--cache` 경로를 지정한다. 결과 폴더는 없어야 한다. `--cache`·`--device` 옵션은 `prepare/analyze` 앞에 둔다.

```powershell
py -3 standalone/seungun/run.py --cache "C:\Seungun Cache" --device cuda prepare
py -3 standalone/seungun/run.py --cache "C:\Seungun Cache" --device cuda analyze --audio "C:\Recordings\audio.wav" --text "원고" --output "C:\Recordings\분석결과2"
```

실행 scratch·lock·socket은 Linux container 파일시스템에만 만든다. 호스트 음성 폴더는 읽기 전용으로 연결한다. 입력 폴더는 container 안에서 보일 수 있으므로 분석에 필요한 파일을 별도 폴더에 두는 것이 명확하다. 출력 폴더에는 본인의 음성과 원고 근거가 저장되며 자동 업로드하지 않는다.

현재 Windows x64의 실제 image build·음성 처리·CUDA 수용은 개발자 확인 전이다. Windows ARM은 지원 대상으로 광고하지 않는다. [출력 계약](output-contract.md)과 [수용 범위](developer-acceptance.md)를 참조한다.
