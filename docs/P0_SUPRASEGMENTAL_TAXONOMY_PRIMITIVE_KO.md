# Phase 0 초분절 taxonomy·결측·다중 라벨 provenance 순수 계약

기준일: 2026-07-29 KST  
상태: **PDF 일반 taxonomy 계약 구현 / 실제 annotation·한국어 ToBI rubric·학습·평가 0**

## 1. 감사 결론

설계 PDF 5.2절은 초분절 오류를 다음 일반 범위로 열거한다.

- 과도한/불충분한 duration
- 잘못된 위치의 pause
- 단조로운 억양, 과도한 pitch excursion
- 경계 톤 불일치
- 잘못된 위치의 prominence
- 불규칙 rhythm

기존 저장소에는 이 목록 전체를 표현하는 label 계약이 없었다.

- `nine_axis_labels.py`는 PDF의 9개 평가 점수와 결측 mask를 보존하지만
  `intonation` 안의 경계톤·prominence 오류 유형을 정의하지 않는다.
- AI Hub 71490 ontology에는 `boundary_intonation_error`와
  `stress_error`라는 넓은 원자료 tag가 있고 C01 집계 support는 각각
  3,177건과 1,187건이다. 그러나 해당 raw tag를 PDF의
  `boundary_tone_mismatch` 또는 `misplaced_prominence` 정답으로
  동일시하는 전문가 근거나 변환 rubric은 없다.
- Parselmouth·torchcrepe·eGeMAPS primitive는 음향 측정 경계이며
  측정값을 위 오류 label로 바꾸는 threshold나 annotation 근거가 아니다.

따라서 기존 raw tag 자동 변환, 한국어 억양 범주 추정 또는 측정값
thresholding을 추가하지 않았다. 이번 구현은 PDF에 실제로 적힌 일반
taxonomy와 label 상태·lineage 표현 공백만 닫는다.

## 2. canonical 범위

구현 파일:
`src/voice_coach/evaluation/suprasegmental_taxonomy.py`

| 일반 축 | canonical error type |
|---|---|
| `duration` | `excessive_duration`, `insufficient_duration` |
| `pause` | `misplaced_pause` |
| `f0_contour` | `monotone_intonation`, `excessive_pitch_excursion` |
| `boundary_tone` | `boundary_tone_mismatch` |
| `prominence` | `misplaced_prominence` |
| `rhythm` | `irregular_rhythm` |

축은 위 여섯 개, 오류 유형은 위 여덟 개로 고정한다. 이는 한국어 ToBI의
tone inventory 또는 문장 유형별 경계톤 규칙이 아니다. 특히 `H%`, `L%`,
`HL%` 같은 category, phrase level, break index, prominence cue 조합을
정의하지 않는다.

## 3. missing과 observed-negative

`build_suprasegmental_label_bundle(...)` 입력은 여섯 축을 모두 명시한다.

- `None`: 해당 축 label이 결측이다.
- 빈 sequence: 해당 축을 실제 관측했으며 이 일반 taxonomy의 positive
  error가 없었다.
- 하나 이상의 enum sequence: 해당 축에 부여된 positive error다.

결측 축에는 caller-defined `missing_reason_id`가 필수다. 관측 축에
missing reason을 넣거나 결측 축에 positive label을 넣으면
fail-closed한다. 여섯 축 모두 결측인 bundle도 coverage 감사를 위해
보존한다. 이것은 학습 가능 row, 정답 row 또는 음질 양호 판정이 아니다.

관측된 `0`과 결측을 별도 mask로 구분하는 기존 9축 계약과 같은 원칙을
따르지만, 이 primitive는 score vector를 만들지 않는다.

## 4. 다중 라벨과 축별 provenance

한 발화·구간에 둘 이상의 일반 오류가 공존할 가능성을 배제할 근거가
없으므로 multi-label을 보존한다.

- 같은 error type 중복은 거부한다.
- label 입력 순서는 의미로 사용하지 않고 canonical 순서로 정규화한다.
- error type은 정해진 parent axis에만 들어갈 수 있다.
- 서로 다른 축의 label을 하나의 종합 prosody class로 합치지 않는다.
- `positive_label_count`와 `is_multilabel`은 표현용 count이며 metric이나
  pass/fail이 아니다.

각 축은 별도의 `SuprasegmentalLabelProvenance`를 갖는다.

| 필드 | 의미 |
|---|---|
| `evidence_kind` | human annotation, dataset annotation, derived measurement 중 caller가 명시한 출처 종류 |
| `source_schema_id` | row identifier가 아닌 source schema/policy ID |
| `source_artifact_sha256` | finalize된 source artifact의 lowercase SHA-256 |
| `annotation_protocol_id` | caller가 명시한 annotation/derivation protocol ID |
| `rubric_id` | rubric이 있을 때만 명시, 현재 `None` 허용 |
| `external_expert_review` | `pending_or_not_attested` 또는 caller attestation |

이 값들은 모두 caller attestation이다. SHA-256도 artifact 동일성만
나타내며 annotation 정확도, 연구 동의, 라이선스, rubric 타당성 또는
전문가 검토를 증명하지 않는다. `caller_attested_complete`를 선택하려면
`rubric_id`를 함께 제공해야 하지만, primitive가 실제 검토를 확인하지는
않는다.

speaker, participant, utterance, audio path, filesystem locator는 이
privacy-neutral 계약에 넣지 않는다. 실제 item과의 join은 별도 승인된
manifest 경계에서 담당해야 한다.

## 5. 기존 P0 9축·C01과의 경계

이 구현은 다음 기존 파일을 수정하거나 대체하지 않는다.

- `src/voice_coach/evaluation/nine_axis_labels.py`
- `src/voice_coach/phonology/error_tags.py`
- `src/voice_coach/coaching/ontology.py`

향후 expert-approved adapter는 9축 score annotation과 이 label bundle을
동일 item의 별도 증거로 연결할 수 있다. 그러나 현재 primitive에는
다음 자동 매핑이 없다.

- AI Hub `경계억양 오류` → `boundary_tone_mismatch`
- AI Hub `강세 오류` → `misplaced_prominence`
- F0 range/contour 값 → monotone 또는 excessive excursion
- energy 값 → prominence
- duration·pause·nPVI 값 → error label
- 일반 오류 label → 9축 scalar score

이 경계는 broad dataset tag, acoustic observation, 사람 annotation,
model target을 서로 다른 의미 계층으로 유지한다.

## 6. fail-closed 항목

다음 입력은 `SuprasegmentalTaxonomyError`로 거부한다.

- 여섯 축 key 누락, 문자열 key 또는 중복/non-canonical axis
- 문자열 error type, 잘못된 parent axis, 중복 error
- 결측 reason 누락, 관측 축에 붙은 결측 reason
- 결측 축의 positive label
- row·path 대신 쓰기 부적절한 공백/path 형태 policy ID
- 64자리 lowercase가 아닌 source artifact SHA-256
- enum이 아닌 evidence/review status
- rubric ID 없는 expert-review-complete caller attestation
- canonical 순서·타입 invariant를 우회한 직접 dataclass 생성

## 7. 의도적으로 남긴 외부 과학 gate

다음은 구현하지 않았고 완료로 주장할 수 없다.

1. 한국어 ToBI 또는 다른 체계의 exact boundary-tone inventory
2. prominence 정의와 F0·energy·duration cue 결합 규칙
3. 문장 유형·억양구·accentual phrase 단위와 location schema
4. label rubric, annotator 교육, adjudication, multi-label guideline
5. feature-to-label threshold와 speaker normalization
6. raw AI Hub tag와 PDF label의 전문가 승인 crosswalk
7. 실제 annotation coverage, inter-rater agreement, calibration
8. 9축 head/loss 연결, 실제 audio 추론, locked 평가

다음 gate는 외부 한국어 억양 전문가가 승인한 rubric·category·location
단위·crosswalk를 versioned artifact로 확정하고, non-locked 소표본을
독립 이중 annotation하여 agreement와 결측 기전을 감사하는 것이다.

## 8. 검증

```bash
CUDA_VISIBLE_DEVICES='' OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MKL_NUM_THREADS=1 PYTHONPATH=src ./.venv-scorer/bin/pytest -q \
  tests/unit/test_suprasegmental_taxonomy.py

./.venv-scorer/bin/ruff check \
  src/voice_coach/evaluation/suprasegmental_taxonomy.py \
  tests/unit/test_suprasegmental_taxonomy.py

./.venv-scorer/bin/ruff format --check \
  src/voice_coach/evaluation/suprasegmental_taxonomy.py \
  tests/unit/test_suprasegmental_taxonomy.py

./.venv-scorer/bin/mypy \
  src/voice_coach/evaluation/suprasegmental_taxonomy.py
```

검증 결과:

- 전용 unit test: **30 passed**
- 전용 + 기존 9축·phone/error taxonomy 인접 회귀: **96 passed**
- Ruff check/format: 통과
- mypy: 통과

테스트는 taxonomy 상수, axis ownership, observed-negative와 missing 구분,
all-missing coverage 표현, multi-label canonicalization, 축별 provenance,
직접 생성 invariant와 금지된 implicit mapping을 확인한다. 실제 음성,
annotation, 데이터셋 row, GPU, network, S104/S106 또는 locked split은
읽거나 수정하지 않았다.
