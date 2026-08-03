# Frozen WavLM feature cache 독립 검증

최종 갱신: 2026-07-29 KST  
상태: Base+와 Large full cache 실제 검증 완료(`passed`)

## 목적

GPU builder가 원자 publish한 frozen WavLM full feature cache를 학습 전에
독립적으로 전수 검사한다. Base+와 Large full cache 모두 이 검사를
완료했다.
이 validator는 source modeling manifest,
원본 음성, `data/raw`, `data/restricted`, `.env*`를 읽지 않는다.

Base+ generic validator의 기본 입력은 다음 다섯 가지다.

- publish된 cache root
- 완료 builder report
- 실험 config
- cache를 만든 builder 파일
- aggregate-only validation report 출력 위치

Config가 가리키는 local model config/checkpoint와 privacy-safe prompt
reservation은 실제 SHA-256을 계산한다. Source modeling manifest는 읽지
않고 config의 pinned SHA, builder report와 모든 Parquet schema metadata가
같은지만 교차검증한다.

Large 전용 validator는 여기에 full-run 승인의 aggregate/hash-linked
입력을 추가로 읽는다. Base+ head report와 registry metadata, registry에
등록된 cache shard·12개 seed artifact, Large v2 smoke report/review,
reviewer CLI/core가 대상이다. Head/registry/smoke JSON 문서는 계약 검사를
위해 파싱하고, registry가 등록한 cache shard와 seed artifact는 파일
형식과 무관하게 content 역직렬화 없이 SHA-256과 크기만 검사한다. `raw`,
`restricted`, `.env*`, stack/calibration/locked-test 경로는 content
open/hash 전에 거부한다.

## Base+ 실제 검증 결과

2026-07-29 기준 Base+ full cache와 독립 validation report는 다음과 같이
고정됐다.

- cache builder report:
  `data/processed/aihub71490/features/wavlm_base_plus_frozen_v1_report.json`
  - SHA-256:
    `34da4c3ec48088b88e1e44763868cd15a21b7baaa06a2cb253338866262ef41d`
  - schema `voice_coach.wavlm_feature_cache_report.v1`,
    `status=completed`, `full_run=true`, `limit=null`,
    `source_scan_complete=true`
- 독립 validation report:
  `artifacts/reviews/wavlm_base_plus_frozen_v1_full_validation.json`
  - SHA-256:
    `7abb93395af73460e4542ecc92b0edb3dd1e96b942551ad4759972da5fdd6c4b`
  - schema `voice_coach.wavlm_feature_cache_validation.v1`,
    `status=passed`, `errors=[]`

| kind | 검사 행·벡터 | 검사 embedding scalar | nonfinite | all-zero | 금지 split |
|---|---:|---:|---:|---:|---:|
| utterance | 169,837 | 260,869,632 | 0 | 0 | 0 |
| phone | 2,233,039 | 1,714,973,952 | 0 | 0 | 0 |
| word | 408,790 | 313,950,720 | 0 | 0 | 0 |
| **합계** | **2,811,666** | **2,289,794,304** | **0** | **0** | **0** |

Phone join 2,233,039건과 word join 408,790건도 전수 확인했다. 검사는
embedding 전체 행렬을 만들지 않았고 최대 batch는 2,048행,
최대 embedding batch는 3,145,728 bytes였다. Validation report는
aggregate-only이며 원본·환경 파일·source modeling manifest를 읽지 않았고
행 식별자를 내보내지 않았다. 이는 cache 완결성 검증 결과이지 모델 성능
metric이나 frozen-head 학습 결과가 아니다.

## Base+ 재검증 명령

아래는 위 고정 결과를 재검증하는 명령이다. Full cache가 `completed`
상태로 원자 publish된 뒤 저장소 루트에서 실행하며 cache 생성 중에는
실행하지 않는다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_wavlm_feature_cache.py \
  --cache-root data/processed/aihub71490/features/wavlm_base_plus_frozen_v1 \
  --report data/processed/aihub71490/features/wavlm_base_plus_frozen_v1_report.json \
  --config configs/experiments/wavlm_base_plus_frozen_v1.yaml \
  --builder scripts/build_wavlm_feature_cache.py \
  --output artifacts/reviews/wavlm_base_plus_frozen_v1_full_validation.json
```

종료 코드는 통과 `0`, cache 검증 실패 `1`, 안전하지 않은 출력 경로 같은
CLI 설정 오류 `2`다. 성공·실패 모두 출력 JSON은 임시 `.part`에 쓴 뒤
`os.replace`로 원자 publish한다.

## Full-run 고정 계약

Base+ full validation은 다음 행 수를 코드와 config 양쪽에서 확인한다.

| kind | expected rows | embedding |
|---|---:|---:|
| utterance | 169,837 | fixed float16 1,536 |
| phone | 2,233,039 | fixed float16 768 |
| word | 408,790 | fixed float16 768 |

Report는 `status=completed`, `full_run=true`, `limit=null`,
`source_scan_complete=true`여야 한다. 실제 세 cache에는 train/dev가 모두
있어야 하고 stack/calibration/locked-test 행은 0이어야 한다.

각 shard에서 다음을 검사한다.

- `part-00000.parquet`부터 연속된 이름과 report의 정확한 파일 집합
- 실제 byte size, SHA-256, 행 수, row-group 수
- config shard 상한과 row-group 상한
- report output 합계와 content SHA-256
- config/builder/prompt/model config/model checkpoint 실제 파일 SHA-256
- model ID, revision, hidden size와 schema metadata lineage
- exact column/type/nullability와 float16 fixed-size dimension
- raw/path/stem/realized/operation/transcript/holistic-score 금지 열 0
- privacy metadata와 builder privacy audit flag

## Streaming 전수 검사

Embedding 전체 행렬을 메모리에 만들지 않는다. 한 Parquet row group을
config 상한 이하 batch로 읽어 다음을 전 행 검사한다.

- utterance/speaker/prompt ID의 소문자 SHA-256 형식
- embedding NaN/Inf 0, all-zero vector 0
- oracle alignment, schema version, 구간·frame 기본 수치 계약
- utterance key 중복 0
- phone/word의 utterance ID·split·speaker·prompt join
- 발화별 phone/word index `0..n-1` 연속성과 duplicate/reappearing key 0

Aggregate validation report에는 실제 확인한 row/vector/scalar 수,
split 수, shard/byte 수, 최대 row-group/batch 크기와 최대 embedding batch
byte를 남긴다. 개별 utterance/speaker/prompt ID나 embedding 값은 남기지
않는다.

## Large 전용 post-run 검증

Large는 generic validator를 같은 프로세스에서 호출해 모든 Parquet을
전수 검사한 뒤, `scripts/validate_wavlm_large_feature_cache.py`가 다음
Large 전용 계약을 추가 검증한다.

- WavLM Large model ID/revision/architecture와 `2048/1024/1024` 차원
- local config/checkpoint/preprocessor pin, normalization과 attention mask
- 실제 Hugging Face snapshot의 `../../blobs/...` leaf symlink를 허용하되
  고정 cache/model/revision 경로와 workspace 내부의 해당 model `blobs`
  하위 regular file만 허용하고 세 파일의 실제 bytes SHA-256 검증
- immutable generic builder dependency 경로와 SHA-256
- Base+ head·registry와 v2 smoke report·review, reviewer CLI/core를 포함한
  full-run gate 전체 hash chain 및 의미 계약을 builder 코드 실행 없이 검증
- BF16 autocast 요청, float16 storage, observed dtype, OOM과 peak resource
- source 322,380건의 exact split/inclusion/exclusion accounting
- evaluation audio/encoder/label/metric 미사용
- v2 smoke의 compressed bytes/row를 정수 ceil-div로 재계산한 사전
  capacity upper bound와 실제 full output byte 비교
- full report가 자체 full output에서 다시 계산한 legacy projection은 별도로
  재현 검증하되 독립 capacity 근거로 취급하지 않음

### Large 실제 검증 결과

2026-07-29에 canonical Large full cache의 원자 publish와 전용 독립 검증을
완료했다.

- cache builder report:
  `data/processed/aihub71490/features/wavlm_large_frozen_v1_report.json`
  - SHA-256:
    `98fe95d00e2faff9b21262c3f863134879df0eb45d392648b9aadc250455fe47`
  - schema `voice_coach.wavlm_feature_cache_report.v1`,
    `status=completed`, `full_run=true`, `source_scan_complete=true`,
    `oom_count=0`
- 독립 validation report:
  `artifacts/reviews/wavlm_large_frozen_v1_full_validation.json`
  - SHA-256:
    `d3f34b34a9bdf85844f43d4186ccdf46736f616ad1b61d806e9c59914783676e`
  - schema `voice_coach.wavlm_large_feature_cache_validation.v1`,
    `status=passed`, `errors=[]`

| kind | 검사 행·벡터 | 검사 embedding scalar | shard | nonfinite | all-zero | 금지 split |
|---|---:|---:|---:|---:|---:|---:|
| utterance | 169,837 | 347,826,176 | 17 | 0 | 0 | 0 |
| phone | 2,233,039 | 2,286,631,936 | 9 | 0 | 0 | 0 |
| word | 408,790 | 418,600,960 | 5 | 0 | 0 | 0 |
| **합계** | **2,811,666** | **3,053,059,072** | **31** | **0** | **0** | **0** |

Phone join 2,233,039건과 word join 408,790건, SHA-256 형식의
utterance/speaker/prompt key 각 2,811,666건을 전수 확인했다. Split별
행은 utterance train/dev `137,759/32,078`, phone
`1,808,724/424,315`, word `331,056/77,734`이고 금지 split은 0이다.
BF16 autocast를 요청했고 관측 hidden output은 float32 172,412개 chunk,
저장은 float16이었다. 실제 compressed cache는 `5,851,341,472` bytes로
v2 smoke에서 사전 계산한 upper bound `7,481,336,023` bytes 이하였다.
Base+ 31개 cache shard, 3개 seed와 registry artifact 13개 및 Large v2
smoke 100/1,798/321행의 full-run gate도 모두 재검증했다.

검증 report는 source modeling manifest, 원본 음성과 evaluation audio를
읽지 않은 aggregate-only artifact다. 이 통과는 Large frozen head의
성능이나 완료를 뜻하지 않으며, 실제 head 학습 전 cache readiness만
증명한다.

Builder 프로세스가 종료되고 canonical cache와 report가 원자 publish된
뒤 저장소 루트에서 다음 명령을 실행한다. 실행 중인 cache나 temporary
output에는 사용하지 않는다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_wavlm_large_feature_cache.py \
  --workspace-root . \
  --cache-root data/processed/aihub71490/features/wavlm_large_frozen_v1 \
  --report data/processed/aihub71490/features/wavlm_large_frozen_v1_report.json \
  --config configs/experiments/wavlm_large_frozen_v1.yaml \
  --builder scripts/build_wavlm_large_feature_cache.py \
  --base-plus-head-report \
    artifacts/experiments/aihub71490_wavlm_base_plus_frozen_head_v1/report.json \
  --smoke-review \
    artifacts/reviews/wavlm_large_frozen_v1_smoke100_v2_automated_review.json \
  --output artifacts/reviews/wavlm_large_frozen_v1_full_validation.json \
  --batch-rows 2048
```

Large validation의 고정 source accounting은 다음과 같다.

| 구분 | train | dev | stack | calibration | locked_test |
|---|---:|---:|---:|---:|---:|
| source | 150,677 | 35,074 | 35,211 | 35,106 | 66,312 |
| included utterance | 137,759 | 32,078 | 0 | 0 | 0 |

Exclusion은 `split_not_allowed=136629`, `qc_flags_present=642`,
`reserved_prompt=15272`여야 한다.

| kind | rows·vectors | float16 dimension | scalar | shard |
|---|---:|---:|---:|---:|
| utterance | 169,837 | 2,048 | 347,826,176 | 17 |
| phone | 2,233,039 | 1,024 | 2,286,631,936 | 9 |
| word | 408,790 | 1,024 | 418,600,960 | 5 |

고정 embedding byte 합계는 `6,106,118,144`이며 Parquet metadata와 다른
열의 byte는 포함하지 않는다. 성공 결과는
`voice_coach.wavlm_large_feature_cache_validation.v1`,
`status=passed`, `errors=[]`이고 위 aggregate 경로 하나에만 원자
publish된다. 이는 cache 완결성과 lineage 승인이지 성능 metric 승인이나
학습 완료 주장이 아니다.

승인된 v2 smoke의 관측 행은 utterance/phone/word
`100/1,798/321`이다. 고정 projection은 utterance `723,509,017`, phone
`4,398,939,038`, word `862,620,763`, 합계 `5,985,068,818` bytes다.
안전계수 `1.25`를 적용한 사전 upper bound는 `7,481,336,023` bytes이며,
full cache 실제 출력 byte 합계가 이 값을 넘으면 검증에 실패한다.

출력은 예측 가능한 임시 이름을 쓰지 않고 같은 디렉터리에 `O_EXCL` 기반
고유 임시 파일을 생성해 flush/fsync 후 `os.replace`하고 디렉터리도
fsync한다. 기존 output leaf symlink와 parent symlink는 거부한다. 이
보호는 validator 실행 중 workspace 디렉터리를 공격자가 동시에 바꾸지
않는 신뢰된·정지 상태의 로컬 workspace를 경계로 하며, 적대적인 동시
디렉터리 교체까지 방어하는 sandbox는 아니다.

## Synthetic 검증 범위

Synthetic multi-shard fixture는 실제 Base+ full 검증과 별도로 validator의
정상·실패 경로를 검사한다.

```bash
./.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_validate_wavlm_feature_cache.py \
  tests/unit/test_validate_wavlm_large_feature_cache.py
```

정상 multi-shard, shard 변조/SHA drift, builder hash 변경, NaN,
불연속·중복 index, forbidden split/join mismatch, privacy 금지 열,
Large model/preprocessor/runtime/source/gate/resource 변조, 실패 exit code와
aggregate-only atomic JSON을 포함한다. 실제 HF blobs symlink 정상 경로와
escape/dangling/wrong-blobs/intermediate symlink, strict NaN/duplicate JSON,
bool OOM, 독립 gate 의미 변조, smoke 사전 상한 초과, precreated symlink도
회귀 검사한다.
