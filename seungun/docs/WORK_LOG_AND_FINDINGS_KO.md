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

## 11. 전체 학습 준비 자동화

사용자의 전체 학습 진행 요청에 따라 자동화할 수 있는 준비 단계를
수행했다. 다만 독립 수동 정렬 감사를 통과하기 전에는 smoke 및 전체
학습을 시작하지 않도록 gate를 유지했다.

### 11.1 확정한 TextGrid target 정책

`configs/textgrid_target_policy.yaml`을
`textgrid-observed-phone-v1`으로 갱신했다.

- 빈 interval: 시간 공백으로 보고 target에서 제외
- 정확한 ASCII `-`: 기대 음소 삭제 placeholder로 보고 제외
- 공백으로 구분된 알려진 자모: 개별 phone으로 분리
- 알려진 단일 자모가 붙어 있는 label: 개별 phone으로 분리
- `spn`: 관측 잡음/미지 phone으로 보존
- 그 밖의 미해결 label: 임의 교정하지 않고 발화 전체 제외
- phone tier 누락, 정규화 후 빈 target, source QC flag: 발화 전체 제외
- 동일 PCM: 첫 레코드만 보존
- 20초 초과 발화: 메모리 안정성을 위해 제외

완성형 음절 G2P, punctuation 제거, `SPN` case folding, 임의 alias는
적용하지 않았다.

### 11.2 CTC manifest 생성 결과

기존 전체 raw manifest와 enriched Parquet를 같은 순서로 함께 읽어
관측 발음 CTC manifest를 생성했다.

| 항목 | 결과 |
|---|---:|
| 입력 발화 | 322,380 |
| 채택 발화 | 316,896 |
| 제외 발화 | 5,484 |
| train | 217,176발화 / 1,263명 |
| dev | 34,424발화 / 197명 |
| test | 65,296발화 / 370명 |
| train/dev/test 화자 교집합 | 모든 조합 0 |
| 채택 오디오 시간 | 약 303.59시간 |

기존 5-way split은 다음과 같이 CTC 3-way split으로 보수적으로
재사용했다.

```text
train + stack + calibration -> train
dev                         -> dev
locked_test                 -> test
```

`locked_test`를 학습 또는 모델 선택에 합치지 않았다.

제외 내역:

```text
audio_too_long                 3,002
unresolved_textgrid_label      1,354
source_qc_flag                 1,071
empty_observed_phone_sequence     45
duplicate_pcm                     12
```

생성 경로:

```text
manifest/train.jsonl
manifest/dev.jsonl
manifest/test.jsonl
manifest/speaker_split.json
manifest/vocab.json
manifest/phone_inventory.json
manifest/preparation_report.json
```

`manifest/`는 제한 데이터에서 파생된 로컬 산출물이므로 Git에서 제외한다.

### 11.3 vocabulary와 processor

Training 관측 target만으로 41개 phone과 특수 토큰 2개를 구성했다.

```text
vocab size: 43
<pad>: 0, CTC blank
<unk>: 1
word boundary token: 사용하지 않음
```

Transformers tokenizer가 `word_delimiter_token=None`을 문자열 `"None"`으로
추가하는 동작을 발견했다. vocab 크기가 몰래 44로 증가하지 않도록 이미
존재하는 `<pad>`를 delimiter 자리에도 지정했고, phone decode는 명시적인
ID-to-token 변환만 사용하도록 했다. 저장 후 processor vocabulary 재로딩
일치 검증을 통과했다.

Processor 경로:

```text
processor/
```

### 11.4 base model 고정과 forward 검증

Hugging Face의 현재 `main`을 조회해 다음 revision을 고정했다.

```text
model: facebook/wav2vec2-xls-r-300m
revision: 1a640f32ac3e39899438a2931f9924c02f080a54
```

가중치는 저장소 공용 Hugging Face cache에 내려받았다. 43개 출력 head는
새로 초기화되며 feature encoder는 freeze하도록 학습 코드에 반영했다.

첫 검증에서 모델 전체를 BF16으로 직접 변환하고 FP32 입력을 넣어 dtype
mismatch가 발생했다. 실제 학습 방식인 FP32 model과 CUDA BF16 autocast로
다시 실행해 다음 결과를 확인했다.

```text
logits shape: (1, 49, 43)
dtype: bfloat16
finite: true
peak CUDA allocation: 1,902,228,992 bytes
```

### 11.5 구현한 추가 코드

- `src/korean_phone_ctc/data/ctc_manifest.py`
  - 보수적 TextGrid target 정규화와 발화 채택/제외 판정
- `src/korean_phone_ctc/training_utils.py`
  - CTC greedy collapse, PER/S/D/I, 수동 감사 gate
- `scripts/prepare_ctc_data.py`
  - 전체 manifest, 분포, vocab, split 보고서 생성
- `scripts/create_ctc_processor.py`
  - 고정 vocab processor 생성과 재로딩 검증
- `scripts/train_ctc.py`
  - 같은 코드 경로의 smoke/full 학습, dev 선택, locked test 평가,
    confusion matrix, 대표 예측, checkpoint 재로딩 검증
- `tests/test_ctc_manifest.py`
- `tests/test_training_utils.py`

학습 스크립트는 수동 감사 결과 JSON의 `status=passed`와
`alignment_accuracy>=0.95`를 확인하지 못하면 모델을 로드하기 전에
종료한다.

### 11.6 현재 품질 검사

```text
pytest: 17 passed
Ruff lint: passed
Ruff format: 25 files passed
mypy strict: 12 source files, no errors
```

### 11.7 현재 중단 지점

다음 worksheet의 240개 행은 모두 미검수 상태다.

```text
../data/processed/aihub71490/manifests/
  aihub71490_manual_alignment_audit.tsv
```

확인 결과:

```text
reviewer_id: 240/240 빈 값
alignment_correct: 240/240 빈 값
maximum_boundary_error_ms: 240/240 빈 값
```

이 단계는 한국어 음성학/정렬 역량이 있는 독립 검수자가 실제 음성과
TextGrid를 함께 확인해야 한다. 자동 분석을 독립 인간 검수로 대신 기록하지
않았다. 미완료 G0 audit JSON을 smoke 명령에 전달했을 때 exit code 1로
정상 차단되는 것도 확인했다.

따라서 현재까지 smoke training, checkpoint 생성, PER 평가 및 전체
학습은 시작되지 않았다.
