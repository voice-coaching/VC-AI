# 공개 재현 증거 경계

`artifacts/`의 실험 산출물은 기본적으로 Git에서 제외한다. 이 파일만 예외로
추적하여 새 clone에서 공개 가능한 증거의 위치와 반출 규칙을 알 수 있게 한다.

## 새 clone에서 재현 가능한 범위

저장소 루트에서 Python 3.12 환경을 만든 뒤 기본 API와 계약 테스트를 설치한다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --editable '.[test]'
.venv/bin/python scripts/check_markdown_links.py
.venv/bin/python -m pytest -q \
  tests/integration/test_api.py \
  tests/unit/test_contracts.py \
  tests/unit/test_markdown_links.py
```

이는 GitHub Actions와 같은 **기본 dependency 경계**만 검증한다. scorer/GPU,
visual runtime, 제한 데이터, checkpoint, ignored artifact 또는 사람 gate를 검증한
것으로 해석하지 않는다. 전체 연구 테스트는 아래 고정 scorer 환경을 설치한 뒤
실행하며, 해당 머신에서 별도 결과를 기록한다.

NVIDIA GB10/CUDA scorer 환경은
[`requirements/README.md`](../../requirements/README.md)의 고정 환경 절차를
따른다. `seungun`은 별도 패키지이므로 해당 디렉터리에서 test/inference extra를
설치하고 자체 테스트를 실행한다.

clone만으로 재현되지 않는 항목은 제한 데이터, 처리 manifest, 사용자 음성·영상,
모델 weight, checkpoint와 로컬 실험 registry다. 이 파일들의 부재를 성공한 실험이나
완료된 gate로 해석하지 않는다.

## 공개 evidence 추가 규칙

공개 evidence는 다음 조건을 모두 만족한 파일만 허용한다.

- aggregate-only이며 행·화자·발화 식별자가 없다.
- 원문, 전사, 파일명, 절대·상대 경로, audio/video locator가 없다.
- 모델 weight, checkpoint, embedding, per-item prediction이 없다.
- API key, token, cookie, 환경변수 값과 credential URL이 없다.
- 데이터 라이선스와 개인정보 검토 결과가 명시돼 있다.
- 실행 상태가 `static`, `dry_run`, `failed`, `completed` 중 실제 관찰값과
  일치하며 사람 평가나 production readiness를 과장하지 않는다.
- source/config/model/data의 공개 가능한 revision 또는 SHA-256과 재현 명령이
  포함돼 있다.

새 JSON·CSV·Markdown evidence를 공개할 때는 `.gitignore`에서 해당 **정확한 파일
경로 하나만** 예외로 추가하고 같은 변경에서 privacy/publication validator를
통과시킨다. `artifacts/public/**` 전체를 wildcard로 허용하지 않는다.
