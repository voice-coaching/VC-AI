# Phase 1 m1 g2pK–AI Hub expected-phone dev smoke

> **현행 판정: `superseded_current_split_drift`**
>
> 이 문서와
> `artifacts/phase1/g2pk_aihub71490_dev_agreement_smoke_20260729.json`은
> 삭제하거나 덮어쓰지 않고 역사적 실행 기록으로만 보존한다. 당시 사용한
> `aihub71490_mvp_speaker_disjoint.jsonl`의 고정 16건을 현행
> `aihub71490_modeling.parquet`에 다시 결합한 결과는 train 11,
> dev 1, calibration 2, locked_test 2였다. 따라서 아래의
> `development_only=true`, non-locked dev 근거, 0.9464885752688172
> 수치는 현행 project dev 논문 근거로 사용할 수 없다. 정정 감사와
> 현행 dev 재실행은
> `PHASE1_G2PK_CURRENT_SPLIT_REPAIR_20260729_KO.md`를 기준으로 한다.

기준일: 2026-07-29 KST  
판정: **고정 dev 16행의 비식별 agreement diagnostic 실행 완료 /
정확도·관측 발음·Phase 1 m1 완료 근거 아님**

## 1. 의미와 실행 범위

이번 비교의 두 입력은 모두 **기대 발음**이다.

- g2pK: transcript와 표준발음 규칙에 조건화된 expected prediction
- AI Hub JSON: `RecordingMetadata.phonemic.phones[].phone` expected
  annotation

AI Hub TextGrid observed candidate, audio, MFA alignment, 사람 감사 gold,
phone CTC, 학습과 checkpoint는 읽거나 실행하지 않았다. 결합 manifest의
모든 행은 JSONL parse만 했고, `split=dev`가 아닌 287,723행은 split 확인
직후 source를 열지 않았다. raw JSON source 접근은 dev 34,657행뿐이다.

따라서 아래 수치는 두 expected source 사이의 작은 개발 진단이다.
정확한 발음, 관측 발음 일치, g2pK 정확도, 모델 우월성 또는 primary
선택 기준으로 해석하면 안 된다.

## 2. provenance와 표본 고정

| 항목 | aggregate |
|---|---:|
| manifest 행 | 322,380 |
| dev 행 / 검증된 dev raw JSON | 34,657 / 34,657 |
| 비-dev source 미접근 행 | 287,723 |
| manifest parse·schema·dataset·중복·path·annotation-count 구조 오류 | 0 |
| manifest SHA-256 | `6d121591f98d3916617df9efe6b0f86371c92bdd2c6c951b003cdabde6ec7a1a` |
| data config SHA-256 | `c9373c838f2c083d3b498b5b2032849a52360e31d9282c9216092178b619ce09` |
| dev provenance SHA-256 | `0974e0b494d08ea222c45fe143b7fa6f918ee6550c816b38a364659349bacebc` |
| dev raw-content aggregate SHA-256 | `419af6675bdbb71ece9cbe1bf493bdfb44555f1922bd67747a24682fd5105124` |

실행 implementation과 환경 lock도 caller가 exact SHA로 고정했다. runner는
시작 시 실제 파일을 검증하고 16행 처리가 끝난 뒤 같은 파일들을 다시
해시했다. 시작 검증과 종료 불변 검증은 모두 통과했다.

| implementation role | SHA-256 |
|---|---|
| runner | `2c1802391e9435594347461016e11129c7b1964cbf8083bdb7f5b1cbfdf070a0` |
| expected-phone agreement source | `c4bfc4d7ce8890910a0a23dd50462f815517d258678de5f792e68a87347b8205` |
| existing g2pK adapter source | `887864723991f129391d2b4f54ddc6bdd3192ddc531c032ad2ca1c74ebcfd95c` |
| existing phone inventory source | `ad87e6c8edb961dc3b031342f433143d7d1412ac07812a5b7d5fd7741e13ac3b` |
| g2pK environment lock | `f0cb524d23d9d4be290884267a034dac1a1f203f7f76e51dffeb065f9a604785` |

표본은 canonical 여부나 agreement 결과를 보기 전에 전체 dev 34,657행을
모집단으로 고정했다. policy는
`sha256_domain_seed_row_identity_target_reference_v1`, seed는 `42`이고
가장 작은 digest score 16개를 선택했다. 실제 identity와 source
reference는 artifact에 쓰지 않았다.

- 고정 sample 수: 16
- selection SHA-256:
  `4ce87fb7ebd6f14e36c5e56ee118b72bc36469cd85de68feac8bdeb4937ad1ea`
- 선택 후 annotation/transcript policy rejection: 0
- 실제 g2pK 시도 / comparison 완료: 16 / 16

즉 canonical-only pool에서 뽑은 표본이 아니다. 먼저 고정한 16행이 이번
정책에서 우연히 모두 실행 가능했다.

## 3. 사전 고정 정책

### g2pK

- exact runtime: `g2pK==0.9.4`
- `descriptive=false`
- `group_vowels=false`
- `word_boundary_policy=drop`
- `verbose=false`, `to_syl=false`는 기존 adapter가 강제
- absolute `.venv-paper-g2pk/nltk_data`와 기존 CMUdict digest gate 사용
- process 실행 구간에서 socket connect, connect-ex, create-connection,
  address-resolution helper를 실패 함수로 교체

`descriptive=false`와 `group_vowels=false`는 이번 standard-rule smoke의
고정 조건일 뿐, 데이터로 선택한 최적 option이나 primary 성능 결론이
아니다.

### word boundary와 annotation boundary

AI Hub expected annotation에는 g2pK의 공백-derived `|`와 직접 대응하는
word-boundary row가 없으므로 비교 전 g2pK word boundary를 drop한다.

AI Hub 쪽은 40개 compatibility-jamo만 허용한다. `sil`을 포함한
비정규/boundary/noise 표기를 추측해 지우거나 alias하지 않고, 선택된
행에서 하나라도 나오면 그 행을 **선택 후 reject**한다. 전체 dev에서
확인된 aggregate는 다음과 같다.

| 항목 | 값 |
|---|---:|
| annotation phone occurrence | 467,529 |
| canonical 40-jamo occurrence | 466,901 |
| 비정규 occurrence / 포함 행 | 628 / 628 |
| 비정규 distinct vocabulary | 1 |
| 비정규 vocabulary SHA-256 | `ba3627c71108debdc10fec0529148752de349a41d8a23d3a9be11be1d856a717` |
| exact `sil` occurrence | 0 |

비정규 값 자체는 문서·artifact·log에 쓰지 않았다. 이 smoke에서 그
값의 boundary 의미를 단정하지 않으며 후속 source-policy 감사 대상이다.

### punctuation과 입력 계약

허용 punctuation은 데이터에서 자동 수집하지 않고 다음 exact tuple로
미리 고정했다.

```text
!, ", ', (, ), ,, -, ., :, ;, ?
```

policy ID는 `fixed_ascii_sentence_punctuation_v1`, tuple SHA-256은
`772fdb70b5aaa449e9114fb37c57ef88bf3eefc6ce2ff8dcd662b2bbd437b1ad`다.
전체 dev에서 미허용 punctuation 행은 0이었다.

plain non-empty NFC, 양끝 공백 없음, control/surrogate/private-use 없음의
기존 g2pK 입력 계약을 적용했다. 전체 dev 325행이 이 결합 계약을
통과하지 못했다. 원문과 세부 원인을 출력하지 않았고, 자동 정리나
문자 치환도 하지 않았다. resource cap 236 codepoint는 고정 manifest의
관측 최대 길이와 같고 scientific decision threshold가 아니다.

## 4. 순수 agreement primitive

`src/voice_coach/phonology/expected_phone_agreement.py`는 두 sequence를
in-memory에서만 검증·비교한다.

- 두 source 역할을 각각
  `expected_g2pk_transcript_conditioned`,
  `expected_aihub_json_annotation`으로 고정
- exact non-empty tuple과 40개 compatibility-jamo만 허용
- g2pK option·count·boundary·punctuation·token digest 일관성 재검증
- unit-cost Levenshtein distance
- 대칭 정규화:
  `1 - distance / max(g2pK phone 수, AI Hub phone 수)`
- caller-explicit 최대 cell 수와 hard cap 1,000,000
- threshold, pass/fail 성능 기준, alias, phone mapping 없음
- 결과에 transcript, phone tuple, row/speaker identity, source path,
  alignment path 없음

정규화 값 이름은 `normalized_edit_agreement_rate`지만 accuracy가 아니다.

## 5. 실제 16행 aggregate

| 항목 | 값 |
|---|---:|
| sample 고정 / rejection / comparison 완료 | 16 / 0 / 16 |
| g2pK expected phone 수 | 150 |
| AI Hub expected phone 수 | 145 |
| exact sequence match | 11 / 16 |
| Levenshtein distance 합 | 8 |
| macro normalized edit agreement | 0.9464885752688172 |
| row 최소 / 최대 normalized edit agreement | 0.6666666666666667 / 1.0 |
| comparison-result aggregate SHA-256 | `f104e0fe7e56c6ec9e643abc384a2bc422a55feeb3995e718d103a2e7dc007ba` |
| socket attempt | 0 |

16행은 매우 작은 diagnostic이며 신뢰구간, population estimate, slice,
사람 검토 또는 독립 평가가 없다. `11/16`과 normalized edit agreement를
정확도나 m1 완료 수치로 사용하지 않는다.

## 6. artifact와 무효 1차 산출물

정정된 aggregate artifact:

- `artifacts/phase1/g2pk_aihub71490_dev_agreement_smoke_20260729.json`
- file SHA-256:
  `dd42ca66dc7bd1b1c908f1f11bae1722579af1207d9f9debaeaf2985466c8e7b`
- payload SHA-256:
  `59ffb6fefa0d18711acf5235311685de35e4f531a2d94c8d80301284deb2ba0c`
- schema: `voice_coach.g2pk_aihub71490_dev_smoke.v2`
- mode: `0600`

첫 실행 artifact는 삭제하거나 덮어쓰지 않고 다음 이름으로 보존했다.

- `artifacts/phase1/g2pk_aihub71490_dev_agreement_smoke_20260729.invalid-preaudit.json`
- file SHA-256:
  `0b355da86ef2a6898e7324fb2909265330eff3ccc5a6cd4cf1076759add324d1`
- invalid reason:
  `transcript_contract_early_return_phone_undercount`

1차 구현은 transcript 계약 rejection 뒤 phone inventory 집계를 일찍
종료해 전체-dev 비정규 occurrence를 과소계상했다. 실제 16행 comparison
값이 같더라도 그 artifact 전체를 증거에서 제외한다. 정정판은 transcript
eligibility와 phone inventory를 독립 집계하고 overlap을 non-exclusive
condition count로 남긴다.

그 뒤 생성한 집계 정정판도 implementation/lock SHA를 artifact에 고정하지
않았으므로 다음 이름으로 보존하고 증거에서 제외했다.

- `artifacts/phase1/g2pk_aihub71490_dev_agreement_smoke_20260729.superseded-code-unpinned.json`
- file SHA-256:
  `c0931c88cb03588bf2c0cea3dea230f405794f2eefb48ed59dfc4f2a1e3b5e8a`
- superseded reason:
  `implementation_and_environment_lock_sha_not_pinned`

위 두 이전 artifact의 metric은 주장에 사용하지 않는다. v2만 실행
시작/종료 implementation provenance가 닫힌 현재 증거다.

## 7. 실행·검증

실제 실행은 기존 `.venv-paper-g2pk`와 absolute NLTK data root로 수행했다.
출력 summary는 다음 aggregate뿐이었다.

```text
status=passed_development_diagnostic
dev_row_count=34657
sample_fixed_count=16
sample_rejected_count=0
comparison_completed_count=16
```

실제 v2 command:

```bash
PROJECT_ROOT=/home/cmsl-dgx01/intelligent04/intelligentAI
PYTHONPATH=src .venv-paper-g2pk/bin/python \
  scripts/run_g2pk_aihub71490_dev_smoke.py \
  --manifest "$PROJECT_ROOT/data/processed/aihub71490/manifests/aihub71490_mvp_speaker_disjoint.jsonl" \
  --manifest-sha256 6d121591f98d3916617df9efe6b0f86371c92bdd2c6c951b003cdabde6ec7a1a \
  --data-config "$PROJECT_ROOT/configs/data/aihub71490_mvp.yaml" \
  --data-config-sha256 c9373c838f2c083d3b498b5b2032849a52360e31d9282c9216092178b619ce09 \
  --runner-sha256 2c1802391e9435594347461016e11129c7b1964cbf8083bdb7f5b1cbfdf070a0 \
  --agreement-source-sha256 c4bfc4d7ce8890910a0a23dd50462f815517d258678de5f792e68a87347b8205 \
  --g2pk-adapter-source-sha256 887864723991f129391d2b4f54ddc6bdd3192ddc531c032ad2ca1c74ebcfd95c \
  --phone-inventory-source-sha256 ad87e6c8edb961dc3b031342f433143d7d1412ac07812a5b7d5fd7741e13ac3b \
  --g2pk-environment-lock-sha256 f0cb524d23d9d4be290884267a034dac1a1f203f7f76e51dffeb065f9a604785 \
  --dataset-root "$PROJECT_ROOT/data/restricted/aihub/dataset-71490" \
  --nltk-data-root "$PROJECT_ROOT/.venv-paper-g2pk/nltk_data" \
  --output "$PROJECT_ROOT/artifacts/phase1/g2pk_aihub71490_dev_agreement_smoke_20260729.json" \
  --seed 42 \
  --sample-size 16 \
  --descriptive false \
  --group-vowels false \
  --punctuation-policy fixed_ascii_sentence_punctuation_v1 \
  --word-boundary-policy drop \
  --maximum-codepoints 236 \
  --max-alignment-cells 1000000
```

검증 결과:

- Ruff check: pass
- Ruff format check: pass
- mypy, 신규 source 2개: `Success: no issues found`
- 신규 unit test: `45 passed`
- 기존 g2pK adapter 회귀 포함 관련 test: `96 passed`
- socket 차단 unit test: connect 시도 1건을 차단·aggregate failure로 기록
- 실제 socket attempt: 0

## 8. 남은 gate

1. 325개 transcript-contract rejection과 628개 비정규 annotation
   occurrence의 source-policy 감사
2. 고정 dev 표본 확대 전 사람의 expected-label/표준발음 적합성 검토
3. 사전등록 slice와 uncertainty 계획
4. G04 이후 observed TextGrid/phone recognizer 계열과 source를 분리한 평가
5. MFA phone inventory mapping과 OOV/boundary policy의 별도 승인

현재 확보한 것은 **non-locked dev의 bounded expected-source diagnostic**
뿐이며 **정확도, observed pronunciation 또는 Phase 1 m1 완료가 아니다**.
