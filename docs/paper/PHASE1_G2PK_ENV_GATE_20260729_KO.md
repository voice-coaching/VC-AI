# Phase 1 m1 g2pK 격리 환경·실행 게이트

기준 시각: 2026-07-29 KST  
판정: **ARM64/Python 3.12 offline smoke 및 expected-phone primitive 완료 /
실제 m1 정확도·CTC·MFA 결합 미완료**

## 1. 범위

PDF Phase 1 m1의 `g2pK/MFA` 중 g2pK가 현재 ARM64 머신에서 실제로
설치·실행되는지 확인했다. 이 작업은 transcript에서 **기대 발음열**을
만드는 경로만 다룬다. AI Hub 원본 TextGrid의 관측 발음, JSON 기대
annotation, G04 사람 감사와 MFA predicted alignment를 서로 대체하지
않는다.

다음에는 접근하지 않았다.

- AI Hub/KSS/CoreaSpeech audio·transcript·speaker row
- locked/stack/calibration split
- MFA archive·runtime·alignment·DTW
- phone-CTC 학습·checkpoint·metric
- S104 PID 386918, GPU와 S104/S106 protected source

## 2. 공식 배포와 공급망 경계

[공식 PyPI 프로젝트](https://pypi.org/project/g2pK/)와
[공식 GitHub 저장소](https://github.com/Kyubyong/g2pK)를 확인했다.

| 항목 | 확인값 |
|---|---|
| PyPI release | `g2pK==0.9.4`, 2020-08-05 |
| Python metadata | `>=3.6` |
| wheel | `g2pK-0.9.4-py3-none-any.whl`, 27,637 bytes |
| wheel SHA-256 | `8410ecba4a98e35fc17db70f4c4322d4ddcc31300eebe6eedd723f6569203465` |
| upload | Trusted Publishing 미사용 |
| declared dependencies | `jamo`, `nltk`, `konlpy`, `python-mecab-ko` |
| GitHub `master` | `3bb9d5afc5159220d5d16492aca7a58f121b6073` |
| 마지막 upstream commit 시각 | 2020-08-03T21:37:03Z |
| g2pK license metadata/repo | Apache-2.0 |

Universal wheel 표기와 `Python >=3.6`만으로 Python 3.12/aarch64
호환성을 주장하지 않고 아래 실제 설치로 확인했다. PyPI wheel과 현재
GitHub master는 시기상 가까우나, wheel byte를 Git tree와 암호학적으로
동일하다고 주장하지 않는다.

## 3. 격리 설치 결과

새 `.venv-paper-g2pk`만 사용했다. 첫 명령에서 아직 배포되지 않은
`pip==26.2`를 잘못 지정해 package 설치 전에 실패했다. PyPI가 실제
제공한 최신 후보 중 `pip==26.1.2`로 다시 고정한 뒤 성공했다.

```bash
python3.12 -m venv .venv-paper-g2pk
.venv-paper-g2pk/bin/python -m pip install \
  --index-url https://pypi.org/simple 'pip==26.1.2'
.venv-paper-g2pk/bin/python -m pip install \
  --index-url https://pypi.org/simple 'g2pK==0.9.4'
.venv-paper-g2pk/bin/python -m pip check
```

결과는 `No broken requirements found`였다.

- platform: Linux aarch64
- Python: 3.12.3
- g2pK: 0.9.4
- python-mecab-ko: 1.3.7
- python-mecab-ko-dic: 2.1.1.post2
- NLTK: 3.10.0
- environment 크기: 약 248 MiB
- exact freeze:
  `requirements/paper-g2pk-linux-aarch64.lock.txt`
- freeze SHA-256:
  `f0cb524d23d9d4be290884267a034dac1a1f203f7f76e51dffeb065f9a604785`

이 freeze는 성공한 version snapshot이며 모든 wheel hash를 강제하는
`--require-hashes` lock은 아니다.

## 4. CMUdict 자동 다운로드 문제와 offline 고정

g2pK 0.9.4의 `g2pk/g2pk.py`는 import 시
`nltk.data.find("corpora/cmudict.zip")`가 실패하면
`nltk.download("cmudict")`를 호출한다. 한국어-only 입력에서도
`G2p()` 생성자가 English 변환용 CMUdict 전체를 읽는다.

첫 raw smoke에서 이 upstream 동작 때문에 기본 home NLTK data root에
CMUdict zip과 추출본 약 4.6 MiB가 생성됐다. 이 파일은 이번 작업에서
생성된 시각과 내용만 확인했지만, home 소유권 경계를 보존하기 위해
임의 삭제하지 않았다. 이후 같은 artifact를 전용
`.venv-paper-g2pk/nltk_data`에 명시적으로 다운로드했고, 프로젝트
adapter는 다음 세 파일이 모두 일반 파일이고 exact digest가 맞지 않으면
g2pK를 import하지 않는다.

| artifact | bytes | local SHA-256 |
|---|---:|---|
| `corpora/cmudict.zip` | 896,069 | `d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6` |
| `corpora/cmudict/cmudict` | 3,820,830 | `cad209c39eb87677d64e93d97f8eed10b7e6f9bdd42de8e7ca8efc8e17d62e8a` |
| `corpora/cmudict/README` | 3,808 | `b0556be7a2b12bea6a667277b75864f802dd4854480657ce298c62e6897e766b` |

NLTK data repository의 가변 branch를 논문 revision으로 간주하지 않는다.
향후 재현은 위 세 local digest와 environment freeze를 함께 사용한다.
Adapter는 검증 중 NLTK search path를 이 root 하나로 제한하고
`nltk.download`를 실패 함수로 바꾼 뒤 원상 복구한다.

## 5. Strict expected-phone adapter

`src/voice_coach/phonology/g2pk_expected.py`는 다음을 강제한다.

- g2pK와 핵심 dependency exact version
- absolute·non-symlink NLTK data root와 세 CMUdict digest
- plain NFC text, caller-explicit 최대 codepoint 수
- `descriptive`, `group_vowels`, word-boundary와 허용 punctuation 정책
- `verbose=False`, `to_syl=False`
- modern conjoining-jamo onset/vowel/simple-coda만 AI Hub와 같은 40개
  compatibility-jamo phone으로 명시 매핑
- 미승인 punctuation, Hangul syllable, Latin, complex coda와 기타
  backend output은 fail closed
- backend 오류의 원문·경로·ID를 public error/cause에 남기지 않음
- input text와 raw g2pK output을 결과 object에 보존하지 않음

이는 expected label 경로다. 관측 TextGrid의 보수적 label normalizer가
금지한 “Hangul syllable→phone” 변환을 우회하는 것이 아니다. 두 source는
각각 `expected_g2pk`와 `observed_textgrid_candidate`로 분리해야 한다.
반환 phone tuple과 digest도 transcript-derived R1이며 익명화된 공개
통계가 아니다. Row artifact나 log에 쓰려면 제한 저장소·pseudonymous
join과 삭제 정책을 별도로 적용해야 한다.

신규 단위 테스트 51개가 통과했다.

## 6. 실제 offline smoke

공식 README의 공개 예문 세 개만 사용했다. Process 수준에서 socket
connect를 실패시키고, adapter의 NLTK download 차단과 exact artifact
검증을 함께 적용했다. `descriptive=false`, `group_vowels=false`,
word boundary drop, punctuation `(",", ".")`를 명시했다.

| input SHA-256 | phone 수 | token SHA-256 | 같은 process 반복 |
|---|---:|---|---|
| `f462900f03eab0cce1b82927625fbb95bdd5bc94f0e4534708196202f3d5b8e1` | 38 | `d38cabdc7be6fbcf5836cc11a988fd32bda0122c63d6308fd613eac5b0c72d73` | exact |
| `d9930ae5762784992f79269d344e63712ddb9dace3e3dcf0cd94c2732128b098` | 45 | `fc47ad118171f4e156dbbeaff40fbe20a9cddbf495d3ffe20875b9e4bef18430` | exact |
| `fb7d1022e6ff9597d3e025392d61e037d37e37f601a9a8e229f1334e507b9371` | 42 | `e40f566036b85c86dbbdae991361bb5470ab0b73d39fe7397de0cdd4fd8b7111` | exact |

세 row aggregate SHA-256은
`f8f5898c62740830b4f4aff73f20868a3d260d3dd4173174987ef96d5193e780`다.
이 결과는 설치·형태소 분석·규칙 변환·phone mapping의 배선 증거일 뿐,
g2pK의 한국어 정확도나 AI Hub label과의 일치 성능이 아니다.

## 7. 라이선스·배포 경계

설치 metadata에서 확인한 표기는 다음과 같다.

- g2pK, jamo, NLTK, python-mecab-ko-dic: Apache-2.0 계열 표기
- python-mecab-ko: BSD-3-Clause 표기
- KoNLPy: GPLv3+ 표기
- CMUdict README: 저작권 고지·조건·면책을 보존하는 재배포 조건

g2pK가 현재 code path에서 KoNLPy를 직접 import하지 않더라도 PyPI
dependency가 설치를 요구한다. 따라서 production 또는 재배포 환경으로
자동 승격하지 않고 transitive license와 artifact notice를 별도 승인한다.

## 8. 남은 m1 gate

1. 실제 train/dev transcript에 적용할 정규화, 숫자·영문·punctuation,
   prescriptive/descriptive와 word-boundary 정책 사전등록
2. AI Hub expected JSON과 source를 분리한 dev-only agreement/error audit
3. g2pK 2020 rule set의 한국어 표준발음·L2 prompt 적합성 사람 감사
4. G04 통과 뒤 observed TextGrid phone-CTC checkpoint 학습
5. x86_64 MFA predicted alignment와 g2pK expected phone의 명시적 phone
   mapping·OOV audit
6. content/phone metric, slice, 불확실성, ablation과 독립 평가

따라서 현재 판정은 **g2pK 환경·offline primitive 실행 완료**이며
**Phase 1 m1 실험 완료가 아니다**.
