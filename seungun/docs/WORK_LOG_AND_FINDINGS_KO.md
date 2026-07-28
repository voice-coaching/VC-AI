# XLS-R 한국어 음소 인식 베이스라인 작업 기록

최종 갱신: 2026-07-29 (Asia/Seoul)

## 1. 작업 목적과 안전 원칙

목표는 XLS-R-300M CTC 모델이 입력 음성에서 실제 발음된 한국어 음소열을
예측하도록 만드는 것이다. 전체 학습 전에 반드시 데이터 감사, 라벨 의미
검증, 화자 비중첩 smoke training, 평가를 차례로 통과해야 한다.

현재까지 전체 학습과 smoke training은 시작하지 않았다. JSON
`RecordingMetadata.phonemic.phones`가 실제 관측 전사가 아니라 기대
발음열이라는 중단 조건이 확인됐기 때문이다.

## 2. 저장소와 실행 환경

- 저장소: `/home/cmsl-dgx01/intelligent04/intelligentAI`
- 작업 디렉터리: `seungun/`
- 구현 커밋: `5ac0bb95fb506c78f6e67c03cc58b94d88e5d14b`
- 커밋 메시지: `feat: add gated AI Hub phone label audit`
- GPU: NVIDIA GB10 1개, unified memory
- 시스템 메모리: 약 119.69 GiB
- CUDA driver/toolkit: 13.0/13.0.88
- Python: 3.12.3
- PyTorch: 2.12.1+cu130
- Transformers: 5.14.1
- bf16: 지원

저장소 루트에는 이 작업 전부터 존재한 미커밋 변경이 있다. `seungun/`
커밋에는 해당 변경을 포함하지 않았다.

## 3. 데이터 식별 결과

요청서에는 dataset ID가 `71479`로 기재됐지만 서버에서 발견되고 파일
목록이 일치하는 데이터는 `71490`이다.

```text
/home/cmsl-dgx01/intelligent04/intelligentAI/data/restricted/aihub/dataset-71490
```

서버 데이터의 제목은 `교육용 중·일어 모국어 사용자의 한국어 음성
데이터`다. 따라서 요청서의 “중·일어 제외” 설명과도 불일치한다. 이
불일치는 향후 dataset manifest와 모델 manifest에 반드시 기록해야 한다.

외부 데이터 준비 프로세스가 만든 추출 파일 수는 다음과 같다. 각
디렉터리의 `.verified-zip-extraction-complete.json`은 아래 수에 포함하지
않는다.

| 분할 | WAV | JSON | TextGrid |
|---|---:|---:|---:|
| Training | 286,560 | 286,560 | 286,560 |
| Validation | 35,820 | 35,820 | 35,820 |

## 4. 구현한 코드

- `scripts/audit_aihub_pronunciation.py`: ZIP 스트리밍 감사 CLI
- `src/korean_phone_ctc/data/audit.py`: 파일 대응, 무결성, 분포 및
  라벨 의미 감사
- `src/korean_phone_ctc/data/aihub_reader.py`: AI Hub JSON 및 TextGrid
  parser
- `src/korean_phone_ctc/data/speaker_split.py`: 결정적 화자 단위 층화 분할
- `src/korean_phone_ctc/data/phone_vocab.py`: 새 phone vocabulary 생성
- `src/korean_phone_ctc/evaluation/phone_alignment.py`: Levenshtein 정렬
- `src/korean_phone_ctc/io_utils.py`: 원자적 JSON/JSONL/CSV 저장
- `configs/xlsr300m_smoke.yaml`: 비활성 smoke 설정 초안
- `configs/xlsr300m_full.yaml`: 전체 학습 명시적 비활성 설정

## 5. Validation 전체 감사 결과

ZIP 내부 Validation 35,820건을 모두 검사했다.

| 항목 | 결과 |
|---|---:|
| WAV/JSON/TextGrid | 각각 35,820 |
| 동일 stem triplet | 35,820 |
| 매칭률 | 100% |
| 누락/중복 | 0/0 |
| 깨진 JSON/TextGrid/WAV header | 0/0/0 |
| ZIP CRC 실패 | 0 |
| 독립 UserID | 1,812 |
| 총 녹음 시간 | 137,094.15초 |
| 빈 JSON phone sequence | 0 |
| 잘못된 JSON/TextGrid interval | 0/0 |
| 정상/오류 샘플 | 25,735/10,085 |
| JSON–TextGrid 음소열 불일치 | 10,439 |

원본 sample rate는 8, 16, 24, 44.1, 48, 96kHz가 혼재하며 모든 파일은
mono였다. 학습 loader에서 16kHz로 동적 resampling해야 한다.

JSON phone inventory는 38개다.

```text
spn ㄱ ㄲ ㄴ ㄷ ㄸ ㄹ ㅁ ㅂ ㅃ ㅅ ㅆ ㅇ ㅈ ㅉ ㅊ ㅋ ㅌ ㅍ ㅎ
ㅏ ㅐ ㅑ ㅓ ㅔ ㅕ ㅖ ㅗ ㅘ ㅚ ㅛ ㅜ ㅝ ㅟ ㅠ ㅡ ㅢ ㅣ
```

## 6. 라벨 의미 검증과 중단 결정

오류 태그가 있는 100개 표본을 JSON과 TextGrid로 비교했다. 95개에서
서열이 달랐다.

```text
prompt: 켤레
JSON:     ㅋ ㅕ ㄹ ㄹ ㅔ
TextGrid: ㅋ ㅛ - ㄹ ㅔ
tags: 모음오류, 종성

prompt: 흔들다
JSON:     ㅎ ㅡ ㄴ ㄷ ㅡ ㄹ ㄷ ㅏ
TextGrid: ㅎ ㅡ - ㄷ ㅡ ㄹ ㄷ ㅏ
tags: 종성

prompt: 좋다
JSON:     ㅈ ㅗ ㅌ ㅏ
TextGrid: ㅈ ㅗ ㄷ ㅏ
tags: 격음화
```

결론은 다음과 같다.

1. JSON `phonemic.phones`는 prompt의 기대 발음열이다.
2. TextGrid `phones` tier가 실제 교체와 삭제를 기록한다.
3. JSON phones를 CTC target으로 사용하면 실제 발음 인식 목표와
   불일치한다.
4. 따라서 기존 목표 정의로는 `LABEL_NOT_SUITABLE`이다.
5. manifest, processor, XLS-R 다운로드, smoke training 및 평가는
   수행하지 않았다.

## 7. 생성된 감사 산출물

산출물은 Git에 포함하지 않는다.

```text
artifacts/data_audit.json
artifacts/data_audit.md
artifacts/phone_inventory.json
artifacts/error_tag_distribution.csv
artifacts/missing_pairs.csv
artifacts/sample_error_cases.jsonl
```

Validation 감사 당시 주요 SHA-256:

```text
c75f35d11d2c83980d190162e1f726a265722bd34e7c35a56fff7e4f262ba9d0  data_audit.json
4f4231c2b5220107e409c65dacce807ddda1a10de1a47ef0c2096e50fbc1386b  data_audit.md
b4ea9644b37246c6ef5853a66befb413e0fb7c26741f004361286e7237547144  phone_inventory.json
5faa5277fc0dbc4495f5248ace102bab570a5f2eff1b5bcb1e171a35206123fa  sample_error_cases.jsonl
```

## 8. 초기 품질 검사 결과

- pytest: 9 passed
- Ruff lint: 통과
- Ruff format check: 통과
- mypy strict: 9개 source file, 오류 없음

## 9. 미완료 사항

Training+Validation ZIP 통합 감사 실행 중 외부 추출 프로세스가 ZIP을
이동하여 마지막 CRC 단계가 실패했다. 원자 저장 방식 때문에 기존
Validation 감사 결과는 손상되지 않았다. 현재 원본 payload는 검증된
추출 디렉터리에 있으므로 다음 감사는 ZIP이 아니라 해당 디렉터리를
대상으로 수행한다.

## 10. 후속 TextGrid target 감사

JSON target 부적합 판정 이후 모델 학습은 계속 차단하고, 추출된 전체
Training/Validation을 대상으로 TextGrid phone tier 자체의 사용 가능성을
추가 조사했다.

| 항목 | 결과 |
|---|---:|
| Training matched triplet | 286,560 |
| Validation matched triplet | 35,820 |
| Training/Validation UserID | 1,830/1,812 |
| 공식 분할 UserID 교집합 | 1,812 |
| 전체 TextGrid phone interval | 4,403,404 |
| 빈 interval | 119,378 |
| 정확한 `-` 삭제 placeholder | 23,873 |
| 공백 복합 label | 1,755 |
| raw label inventory | 1,715 |
| phone tier 누락 발화 | 1 |
| 정규화 후 빈 target | 48 |
| JSON 기대열과 raw TextGrid가 다른 발화 | 93,126 |

공식 Validation 화자 1,812명이 모두 Training에도 존재한다. 따라서 공식
분할은 평가에 사용할 수 없고 반드시 UserID 단위로 전면 재분할해야 한다.

raw TextGrid label에는 정상 단일 자모 외에 다음이 섞여 있다.

- 자모 연결: `ㄹㅣ`, `ㄱㅏ`
- 공백 연결: `ㄱ ㅓ`
- 유효하지만 JSON inventory에는 없던 모음: `ㅒ`, `ㅙ`, `ㅞ`
- 삭제 변형: `ㄴ-`, `－`
- 기호와 잡음 표기: `_`, `.`, `x`, `SPN`
- 완성형 음절 또는 단어: `가`, `그`, `백쩜`

단순 공백 분리만 적용하면 1,260개 symbol이 남아 CTC vocabulary로 사용할
수 없다. 알려진 현대 한국어 단일 자모의 연결만 추가로 분리하는 보수적
정책에서도 자동 처리할 수 없는 label이 남으므로 gate 상태는
`review_required`다.

현대 한국어 compatibility jamo 40개와 `spn`을 허용하는 보수적 정책
초안으로 재검사한 결과:

- 알려진 자모 연결로 안전하게 분리 가능한 interval: 12,873
- 자동 처리 불가능한 raw label: 357종
- 자동 처리 불가능한 interval: 1,785
- 미해결 label을 포함한 발화: 1,450
- 정책 후보 inventory: 41개

미해결 상위 label에는 `_`, `ㄴ-`, `－`, `.`, `ㄹ-`, `가`, `그`, `ㄻ`,
`ㄱ-`, `구`, `x` 등이 있다. 이를 punctuation 제거, G2P 또는 임의 alias로
자동 교정하지 않았다.

후속 산출물:

```text
artifacts/textgrid_target_audit.json
artifacts/textgrid_target_audit.md
artifacts/textgrid_raw_label_distribution.csv
artifacts/textgrid_unresolved_labels.csv
artifacts/textgrid_special_cases.jsonl
```

현재 품질 검사는 pytest 11개, Ruff lint/format, mypy strict 모두
통과했다.
