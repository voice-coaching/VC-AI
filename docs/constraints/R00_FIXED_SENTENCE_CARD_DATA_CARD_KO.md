# R00 고정 연습 문장 데이터 카드

작성일: 2026-07-29 KST  
상태: **R00 완료 — 문장 텍스트만 고정·자동 검증됨**

## 1. 결론과 완료 범위

R00의 완료 기준인 “라이선스가 명시된 20개 고정 문장”을 충족했다.
20문장은 다른 코퍼스 전사를 복사하거나 고쳐 쓴 것이 아닌 이 프로젝트의
신규 통제 문장이다. 정확한 문장 텍스트에는
[`CC0-1.0`](https://creativecommons.org/publicdomain/zero/1.0/legalcode)을
적용했고 별도 `CC0_NOTICE.txt`로 적용 범위를 고정했다. 비상업 제한은
없고 재배포·개작이 허용된다.

다음 항목은 R00 완료에 포함되지 않는다.

- TTS 후보 생성, TTS 학습·미세조정
- 원어민 녹음이나 사람 발음 검수
- MOS/CMOS·교육 적합성 평가
- 최종 reference bank 또는 production 공개

따라서 이 카드로 R01 이후의 성능이나 사람 평가 완료를 주장할 수 없다.
R00에서는 텍스트, 선정 규칙, 라이선스, 교육 목표와 downstream 허용
범위만 사전 고정했다.

## 2. 왜 native 4-source 전사를 사용하지 않았는가

정본 native data card와 license lineage를 먼저 검토한 뒤 모든 native
전사를 fail-closed로 제외했다.

| source | license | 비상업 전용 | R00 문장 사용 | 결정 |
|---|---|---:|---:|---|
| FLEURS ko-KR | CC-BY-4.0 | 아니오 | 0 | 프로젝트 정본의 native transcript Git 반입 금지 |
| KSS | CC-BY-NC-SA-4.0 | 예 | 0 | NC·SA와 raw 재배포 경계 |
| Seoul Corpus | CC-BY-NC-2.0 | 예 | 0 | NC와 raw 재배포 경계 |
| ZEROTH-Korean | CC-BY-4.0 | 아니오 | 0 | 프로젝트 정본의 native transcript Git 반입 금지 |

이번 작업에서 위 네 source의 문장 원문, audio, locator, row-level
개인정보를 읽지 않았다. 공개 manifest와 집계 report의 source,
license, role만 사용했다. 따라서 native source에 대한 문자열 중복
검색도 원문 접근으로 우회하지 않았다. 그 검사는 각 문장에
`not_checked_prohibited_input`으로 명시했다.

선택한 유일한 source는 `voice-coach-r00-original-v1`이다.

- source type: `project_original_controlled_sentences`
- corpus-derived: `false`
- SPDX: `CC0-1.0`
- attribution required: `false`
- noncommercial only: `false`
- share-alike required: `false`
- redistribution/adaptation allowed: `true/true`
- notice:
  `data/reference/r00/CC0_NOTICE.txt`

CC0는 정확한 20개 문장 텍스트에만 적용된다. 기존 코퍼스, audio,
annotation, 모델, 코드와 다른 프로젝트 artifact의 접근·라이선스를
변경하지 않는다.

## 3. 사전 고정 선정 규칙

선정 규칙 ID는 `r00-educational-stratified-selection-v1`이다. R01
후보 생성 전에 규칙과 문장을 함께 고정했으며, 이후 문장 교체는 새
card version을 요구한다.

1. source gate를 먼저 적용해 project-original CC0 문장만 허용한다.
2. 문장은 NFKC 후 Unicode 공백을 하나로 축약한다.
3. 정확히 20개, 문장당 10–18개 한글 음절과 4–8개 어절을 요구한다.
4. 난이도는 초급 7, 중급 7, 고급 6으로 고정한다.
5. 전체 bank에서 초성 19종, 관측 한국어 모음 18종, 받침 표면군 7종,
   C01의 phone-localizable 음운 규칙 8종을 모두 포함한다.
6. `spn`은 관측 target label이지만 잡음 label이므로 교육 목표에서
   제외한다.
7. 관측 target 목록에 없는 현대 모음 `ㅒ/ㅙ/ㅞ`는 보충 철자 목표로만
   포함하고 관측 phone coverage에 섞지 않는다.
8. card 내부 정규화 SHA-256 중복과 AI Hub 예약 prompt 20개 hash와의
   교집합을 모두 0으로 요구한다.
9. ASCII 영문, 숫자, URL·email, control character, 고정 PII·민감·위험
   금칙어가 하나라도 있으면 거부한다.
10. 순서는 `R00-01`부터 `R00-20`까지 고정한다.

음절의 초성·중성·종성은 Unicode Hangul composition을 결정적으로
분해했다. 종성은 교육용 표면군
`ㄱ/ㄴ/ㄷ/ㄹ/ㅁ/ㅂ/ㅇ`으로 보수적으로 모았다. 이는 문장 선정용
**철자 proxy**이며 실제 화자의 표면 발음이나 사람 gold transcription이
아니다.

## 4. 20개 고정 문장

| ID | 문장 | 음절 | 난이도 | 핵심 교육 목표 | SHA-256 앞 12자리 |
|---|---|---:|---|---|---|
| R00-01 | 쌀과 콩을 깨끗한 그릇에 담아요. | 13 | 초급 | 된소리·격음·받침, 목록 리듬 | `7731a3516838` |
| R00-02 | 노란 나비가 맑은 하늘을 날아요. | 13 | 초급 | 유음·비음·겹받침 | `46aebe96eb9b` |
| R00-03 | 따뜻한 물을 천천히 마셔요. | 11 | 초급 | 된소리, 명료한 속도 | `d23e5814aa8f` |
| R00-04 | 빨간 꽃과 파란 풍선을 골라요. | 12 | 초급 | 된소리·격음·원순 모음 | `3c366af28f69` |
| R00-05 | 짧은 치마를 입고 뛰어 보세요. | 12 | 중급 | 겹받침·`ㅟ` | `8b336972e402` |
| R00-06 | 야외에서 예쁜 유리병을 찾아요. | 13 | 초급 | `ㅑ/ㅖ/ㅚ/ㅠ` | `f76da1650515` |
| R00-07 | 왜 웨이터가 예약 얘기를 먼저 꺼냈나요? | 16 | 중급 | 보충 `ㅒ/ㅙ/ㅞ`, WH 의문 경계 | `cc260d1b06c7` |
| R00-08 | 회의 뒤에 위층 정원으로 올라오세요. | 15 | 중급 | `ㅚ/ㅝ/ㅟ/ㅢ`, 긴 구 경계 | `916734d236d0` |
| R00-09 | 국물 냄새가 정말 구수해요. | 11 | 초급 | 비음화 `국물→궁물` | `22152368d2ca` |
| R00-10 | 신라의 달빛은 유난히 밝아요. | 12 | 고급 | 유음화 `신라→실라` | `ec564efd2b3a` |
| R00-11 | 모두 날씨가 좋다며 활짝 웃었어요. | 14 | 중급 | 격음화 `좋다→조타` | `2f748e16bc2b` |
| R00-12 | 같이 밭일을 마치고 쉬어요. | 11 | 고급 | 구개음화 `같이→가치` | `2b1ef1163119` |
| R00-13 | 옷이 젖어도 끝까지 걸어요. | 11 | 중급 | 연음 `옷이→오시` | `a441b804a473` |
| R00-14 | 국밥 한 그릇을 꼭 먹어요. | 10 | 초급 | 경음화 `국밥→국빱` | `d1687bc8d5aa` |
| R00-15 | 담요를 덮고 편히 쉬세요. | 10 | 중급 | ㄴ 첨가 `담요→담뇨` | `f3515d0b3fbd` |
| R00-16 | 놓아 둔 꽃병을 조심히 옮겨요. | 12 | 고급 | 음운 탈락 `놓아→노아` | `dfcb8c0ea4b0` |
| R00-17 | 앞문을 닫는 동안 잠깐 기다려요. | 13 | 고급 | 비음화·받침 중화, 구 경계 | `dcb50d91c558` |
| R00-18 | 책상 위 연필 세 자루를 천천히 세어 보세요. | 17 | 중급 | 목록 리듬·긴 요청문 호흡 | `9de318dfb723` |
| R00-19 | 오늘은 비가 오지만 내일은 맑을까요? | 15 | 고급 | 대조 초점·예/아니요 의문 경계 | `42a5bb910543` |
| R00-20 | 조용히 듣고 핵심 낱말을 또박또박 말해요. | 17 | 고급 | 낱말 초점·반복 리듬 | `6fe58458af1a` |

표에는 읽기 편하게 hash 앞 12자리만 표시했다. machine card에는
각 문장의 NFKC+공백 정규화 텍스트와 전체 SHA-256을 저장했다.

## 5. 교육 목표 coverage

자동 재계산 결과는 다음과 같다.

| 범주 | 요구 | 관측 |
|---|---:|---:|
| 고정 문장 | 20 | 20 |
| 고유 정규화 hash | 20 | 20 |
| 초성 | 19 | 19 |
| 관측 한국어 모음 | 18 | 18 |
| 받침 표면군 | 7 | 7 |
| localizable 음운 규칙 | 8 | 8 |
| 보충 모음 | 3 | 3 |
| 난이도 초급/중급/고급 | 7/7/6 | 7/7/6 |
| 고유 운율 target | 최소 4 | 11 |

AI Hub aggregate의 target label 수는 38이다. 개인정보 안전 aggregate
결과에서 확인한 구성은 `spn`과 한국어 jamo 37종이다. R00은 한국어 jamo
37종을 전부 철자 proxy로 포함하고 `spn`은 교육 target에서 제외했다.
정확한 label 목록은 row prediction이 아닌 frozen-head aggregate report의
`target_label.groups[].value`만 읽어 고정했고, 그 report SHA-256
`ba869265c6e6c1381677fe922922b68da7df92cbb9cbaf5501fff5dafa3fdd23`을
evidence pin으로 검증한다.

음운 규칙 예시 8개는 C01 stable ID와 연결했다.

- `tensification`
- `nasalization`
- `aspiration`
- `liquidization`
- `liaison_rule`
- `n_insertion`
- `palatalization`
- `phoneme_deletion`

예상 실현 표기는 `engineering_target_pending_human_pronunciation_review`다.
C01의 실제 `tag × target phone` joint aggregate나 사람 검수를 대신하지
않으며 자동 발음 정답 주장에 사용할 수 없다.

## 6. 자연성 self-audit

자동 검사 뒤 문법, 일반적인 화행, 주어-서술어 의미 연결과 교육문장
적합성을 별도로 self-audit했다. 이 과정에서 초기 문장 두 개를 다음처럼
교체하고 모든 hash·coverage·quota·예약 prompt 검사를 처음부터 다시
실행했다.

| ID | 교체 이유 | 고정한 최종 문장 |
|---|---|---|
| R00-07 | 희소 모음을 한 문장에 넣으면서 맥락이 다소 인위적이었음 | 왜 웨이터가 예약 얘기를 먼저 꺼냈나요? |
| R00-11 | 초기 주어 생략과 `꽃을 폈다`의 의미 연결이 어색했음 | 모두 날씨가 좋다며 활짝 웃었어요. |

최종 20문장에서는 명백한 문법 오류나 비자연스러운 논항 연결을 발견하지
않았다. 이는 연구자/agent 수준의 text self-audit이지 원어민 패널 평가나
R04 자연스러움 MOS가 아니다. D08/R04 전까지 음성 실현과 교육 적합성은
미검증 상태다.

## 7. 중복·PII·금칙 검사

| 검사 | 결과 | 해석 |
|---|---:|---|
| card 내부 hash 중복 | 0 | 통과 |
| AI Hub 예약 prompt hash 교집합 | 0/20 | prompt 원문을 읽지 않은 exact hash 비교 |
| native plaintext 파일 read | 0 | 접근·재배포 경계 유지 |
| PII pattern match | 0 | 숫자·email·URL·연락처형 표현 없음 |
| 금칙 content match | 0 | 고정 민감·위험 term 없음 |
| ASCII letter / decimal digit | 0/0 | 한글 통제 문장 경계 유지 |

정규식 검사는 모든 개인정보 가능성을 증명하는 도구가 아니다. 다만
이 문장들은 실재 인물·기관·연락처를 source로 하지 않은 신규 일반 문장이고,
검사기는 선언된 `passed` 값을 신뢰하지 않고 text에서 다시 계산한다.

## 8. downstream TTS 허용 범위

R00 policy ID는 `r00-research-reference-text-only-v1`이다.

허용:

- R01의 격리된 연구용 TTS candidate text 입력
- D08의 동의된 원어민 reference 녹음 prompt
- R02 ASR 내용 일치 검사의 reference text
- R04 blind 평가의 sentence label

후속 gate 전 금지:

- TTS 모델 학습·미세조정
- production reference bank 공개
- 자동 발음 정확도 주장
- D08 없이 human reference라고 부르는 행위
- R04 없이 blind 품질 우위를 주장하는 행위
- native corpus audio/transcript 연결

라이선스 자체에는 비상업 제한이 없지만, 연구 프로토콜은 R02/R04 전
production 승격을 허용하지 않는다. 법적 허용과 연구 완료 gate를
분리한다.

## 9. 산출물과 재현

| 산출물 | SHA-256 |
|---|---|
| machine sentence card | `08dc149ccf10006a587e6216dda0471c19b5c55eab6b320b4348587527d82c4d` |
| JSON Schema | `1f8bcf0325414e1923cda0def9a973ac2f7be7bd85cca4ee869743ef862a7b92` |
| CC0 notice | `ffd86dc89cf0957dccc61f6ecb7af1c7ebbbdbff2bd87c951e54c40afc1262c9` |
| deterministic validator | `967517e74194936c91dad1eed4fcfb0c059b5480af32b8adfc9725fdb7d6e7d0` |
| targeted tests | `251b0d01c1a32773168586d0dacd823dbb9453845b98df4dd1b83fbdcc804f30` |
| immutable validation report | `95b4fd861a3c21222ff13859cfbc9c196e3fd06493852bca823d8a63dc2816dd` |

검증 명령:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_r00_sentence_card.py --validate-only

./.venv-scorer/bin/python -m pytest \
  tests/unit/test_r00_sentence_card.py -q
```

결과:

- targeted tests: 13 passed
- Ruff check: passed
- Ruff format check: passed
- mypy validator: passed
- immutable report status: `valid`
- C/H/M: `0/0/0`

검증기의 실제 access count:

- public sentence card/schema/license notice: `1/1/1`
- public 또는 aggregate evidence: 10
- privacy-safe reserved prompt hash: 20
- native plaintext/row-level/raw audio/locator/environment/model/human rating:
  모두 0

`C/H/M 0/0/0`은 R00 bundle의 자동 검증 finding 수다. 사람 발음 검수,
TTS 품질, 학습 효과 또는 R01–R04 완료를 뜻하지 않는다.
