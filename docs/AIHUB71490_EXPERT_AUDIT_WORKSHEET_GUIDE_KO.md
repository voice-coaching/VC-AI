# AI Hub 71490 전문가 수동 감사 worksheet·음성 경로 안내

기준 시각: 2026-07-31 KST
최종 갱신: 2026-07-31 KST (비식별 공개 worksheet 사본 추가)

이 문서는 240건 수동 정렬 감사(G04)를 수행할 독립 전문가에게
worksheet 위치, 채워야 하는 항목, 음성·라벨 파일 경로 구조를 안내한다.
상세 판정 절차는
[`AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`](AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md)를,
기본 검수 경로인 token 보호 로컬 UI는
[`LOCAL_ALIGNMENT_REVIEW_UI_KO.md`](LOCAL_ALIGNMENT_REVIEW_UI_KO.md)를 따른다.

## 1. 왜 worksheet 원본이 GitHub에 없는가

worksheet의 utterance ID와 파일 경로에는 AI Hub 원본 파일 stem이 그대로
포함되며, 이 stem은 화자 `UserID`와 연결될 수 있다. AI Hub 데이터는
제3자 재배포가 금지되고, 저장소 정책상 제한 데이터에서 파생된 경로와
annotation 상세는 Git에 커밋하지 않는다. 따라서 worksheet 실물과 음성
WAV는 실행 서버 로컬에만 두고, GitHub에는 아래의 비식별 공개 사본만
올린다. 음성 파일은 private repo라도 GitHub에 올리지 않는다.

## 1.1 비식별 공개 worksheet 사본

전문가가 판정을 기록할 수 있도록 locator를 모두 제거한 공개 사본을
저장소에 포함했다.

```text
docs/audit/aihub71490_manual_alignment_audit_public.tsv
```

- 생성기: `scripts/export_public_alignment_audit_worksheet.py`
- 행 수: 240 (판정 열은 빈 값)
- 원본 worksheet SHA-256:
  `7e70e1d4d5742688bd1e6544f97527f8362712efdc28827df8f0e7503c1a237c`
- 공개 사본 SHA-256 (판정 기입 전):
  `9d6f541a3d890a5216f106c553edfec4126010e382f9ca1e8e07e8a4c678225c`
- `utterance_id`, `audio_path`, `json_path`, `textgrid_path`,
  `alignment_detail_line`은 제거했다.
- `item_id`는 로컬 review UI와 동일한 namespaced SHA-256
  (`voice-coach.aihub71490.alignment-review-item.v1:` + file stem)이므로,
  공개 사본에 기록된 판정은 실행 서버 안에서만 `item_id`로 원본
  worksheet와 결합해 채점한다.
- 음성 청취는 4절의 서버 내 경로 또는
  [`LOCAL_ALIGNMENT_REVIEW_UI_KO.md`](LOCAL_ALIGNMENT_REVIEW_UI_KO.md)의
  token 보호 로컬 UI(항목별 WAV stream)로만 수행한다.

## 2. 전문가가 채워야 하는 파일

실행 서버(`/home/cmsl-dgx01/intelligent04/intelligentAI`) 안의 다음
TSV 한 개다. 이 디렉터리는 `.gitignore`의 `/data/processed/` 규칙으로
Git에서 제외된다.

```text
data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv
```

- 형식: UTF-8(BOM) tab 구분, header 1행 + 데이터 240행
- 현재 상태: 판정 4개 열이 240행 모두 빈 값 (0/240 검수)
- SHA-256 (판정 기입 전 원본):
  `7e70e1d4d5742688bd1e6544f97527f8362712efdc28827df8f0e7503c1a237c`
- 표본 seed: `aihub71490-g0-alignment-audit-v1`, 50개 층(L1×성별×숙련도×
  오류 유무) 각 최소 2건, 나머지 모집단 비례

동반 파일(모두 Git 제외, 같은 디렉터리):

```text
aihub71490_manual_alignment_audit_details.jsonl   # 자동 phone mapping 상세
aihub71490_manual_alignment_audit_metadata.json   # 표본 추출 메타데이터·checksum
```

## 3. worksheet 열 구성

### 자동으로 이미 채워진 열 (수정 금지)

| 열 | 의미 |
|---|---|
| `audit_index` | 1–240 감사 순번 |
| `utterance_id` | AI Hub 파일 stem (예: `<UserID>-<성별>-…-<발화번호>`) |
| `split` | train / stack / calibration / dev / locked_test (240건 구성: 122/22/21/23/52) |
| `l1` | 모국어 (Chinese 134, Japanese 98, Zhuang; Chuang 8) |
| `gender`, `proficiency` | 화자 층화 정보 |
| `error_presence` | 오류 태그 유무 (false 151, true 89) |
| `duration_seconds` | 발화 길이 |
| `target_phone_count`, `realized_phone_count` | 목표/실현 음소 수 |
| `audio_path`, `json_path`, `textgrid_path` | dataset root 기준 상대 경로 |
| `alignment_detail_line` | details JSONL의 해당 행 번호 |

### 전문가가 반드시 채울 열 (행당 4개)

| 열 | 기입 내용 |
|---|---|
| `reviewer_id` | 검수자 식별자 (로컬 UI 사용 시 namespaced SHA-256 pseudonym으로 자동 대체) |
| `alignment_correct` | 자동 mapping의 match/substitution/deletion/insertion이 시간상 올바른 목표·실현 phone을 연결하면 `true`, 아니면 `false` (yes/no, pass/fail, correct/incorrect도 허용) |
| `maximum_boundary_error_ms` | 사람이 판단한 가장 큰 경계 오차 (ms) |
| `notes` | 판단 불가 사유, 오류 유형 등 (필요한 경우) |

빈 판정, 알 수 없는 판정 값, 중복 utterance, 240건 미만/초과는 채점에서
실패 처리된다.

## 4. 음성·라벨 파일 경로 구조

worksheet의 세 경로 열은 모두 다음 dataset root 기준 상대 경로다.

```text
dataset root: data/restricted/aihub/dataset-71490
```

실제 파일 위치는 `dataset root + '/' + 경로 열 값`이다. 240건이 속한
디렉터리는 정확히 다음 6곳이다.

```text
# 음성 WAV (audio_path)
042.교육용_중·일어_모국어_사용자의_한국어_음성_데이터/01-1.정식개방데이터/
  Training/01.원천데이터/extracted_pronunciation/<utterance_id>.wav
  Validation/01.원천데이터/extracted_pronunciation/<utterance_id>.wav

# 목표 발음 JSON (json_path)
042.교육용_중·일어_모국어_사용자의_한국어_음성_데이터/01-1.정식개방데이터/
  Training/02.라벨링데이터/extracted/TL_pronunciation_lab/<utterance_id>.json
  Validation/02.라벨링데이터/extracted/VL_pronunciation_lab/<utterance_id>.json

# 실현 발음 TextGrid (textgrid_path)
042.교육용_중·일어_모국어_사용자의_한국어_음성_데이터/01-1.정식개방데이터/
  Training/02.라벨링데이터/extracted/TL_pronunciation_tag/<utterance_id>.TextGrid
  Validation/02.라벨링데이터/extracted/VL_pronunciation_tag/<utterance_id>.TextGrid
```

원본 WAV·라벨·경로 목록은 서버 밖으로 복사하거나 외부에 공유하지
않는다. 정밀 확인이 필요하면 서버 머신 안에서만 Praat을 사용한다.

## 5. 검수 → 채점 → gate 흐름

1. 기본 경로는 token 보호 로컬 review UI다. UI가 worksheet를 읽어
   항목별 음성 stream, 목표/실현 phone, 자동 mapping을 함께 보여주고,
   판정을 원자적으로 저장한다.
2. 240건 완료 후 UI의 `export`로 privacy-safe scorer TSV를 만든다.
3. 저장소 루트에서 채점한다.

   ```bash
   .venv-scorer/bin/python scripts/score_alignment_audit.py \
     data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_scoring.tsv \
     --output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json
   ```

4. 통과 기준: `sample_count=240`, `valid_decision_count=240`,
   `status=passed`, `alignment_accuracy >= 0.95` (228/240 이상).
5. 결과 JSON은 seungun CTC smoke/full 학습의 fail-closed gate 입력이다.
   통과 전에는 `seungun/scripts/train_ctc.py`가 모델 로드 전에 종료된다.
6. 95% 미만이면 학습하지 않고 실패 사례를 유형별로 분석해 TextGrid
   정규화·제외 정책과 manifest를 다시 생성한다. manifest가 바뀌면 기존
   worksheet를 재사용하지 않고
   `scripts/sample_alignment_audit.py`로 재생성한다.
