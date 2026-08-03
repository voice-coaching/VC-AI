# Phase 1 g2pK expected-phone primitive

`src/voice_coach/phonology/g2pk_expected.py`는 transcript-conditioned
g2pK 출력을 논문 m1의 **기대 발음 후보**로만 변환하는 in-memory
primitive다.

## 계약

- production은 `g2pK==0.9.4`와 핵심 dependency exact version,
  absolute NLTK data root 및 CMUdict 세 파일 digest를 검증한다.
- 검증 전 g2pK가 import됐으면 실패한다.
- NLTK path를 고정하고 runtime download를 금지한다.
- 입력은 plain NFC string이며 길이 상한을 caller가 명시한다.
- `descriptive`, `group_vowels`, word-boundary, 허용 punctuation 정책을
  caller가 모두 명시한다.
- backend에는 항상 `verbose=False`, `to_syl=False`를 전달한다.
- modern conjoining jamo만 40개 compatibility-jamo phone으로 명시
  변환한다. complex coda, syllable, Latin과 미승인 punctuation은
  추측하지 않고 실패한다.
- 결과는 phone tuple, count, option과 digest만 가지며 input text와
  raw backend output을 보존하지 않는다.

Phone tuple과 digest 자체도 transcript-derived R1이다. 원문을 결과
object에 중복 저장하지 않는다는 뜻이지 익명화 또는 공개 가능하다는
뜻이 아니다.

Fake-backend 단위 테스트 51개와 실제 ARM64 offline smoke 세 문장이
통과했다. 설치·artifact·수치 증거는
`docs/paper/PHASE1_G2PK_ENV_GATE_20260729_KO.md`에 기록했다.

## 의미 경계

이 출력은 표준발음 규칙으로 예측한 expected sequence다. AI Hub JSON
expected annotation, 원본 TextGrid observed candidate, G04 이후 사람
감사 gold, MFA predicted alignment와 의미가 다르다. 어느 source도 다른
source를 덮어쓰지 않으며 join table에는 provenance와 역할을 별도 열로
남겨야 한다.

실제 corpus agreement, 정확도, CTC checkpoint, MFA/DTW와 m1 논문 metric은
아직 없다.
