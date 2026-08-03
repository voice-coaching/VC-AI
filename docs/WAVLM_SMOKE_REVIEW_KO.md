# Frozen WavLM 100건 smoke 독립 reviewer

최종 갱신: 2026-07-29 KST  
상태: 실제 Large v2 smoke 100건 생성·독립 review 승인 완료, full combined
gate 통과, canonical full cache 실행 중; v1 실패 산출물 보존, full 완료·
독립 validation은 아직 미완료

## 목적

`scripts/review_wavlm_feature_cache_smoke.py`는 Base+ 또는 Large builder가
생성한 정확히 100발화짜리 smoke cache를 독립적으로 전수 검사하고,
full cache 실행 게이트가 소비할 aggregate review JSON을 원자적으로
출력한다. 사람이 템플릿의 boolean이나 resource 값을 직접 채우는 과정은
없다.

Reviewer는 다음 입력만 읽는다.

- smoke cache의 `utterance/`, `phone/`, `word/` Parquet shard
- 해당 smoke report
- 고정 실험 config와 builder
- config가 가리키는 privacy-safe prompt reservation
- 고정 local model config/checkpoint
- Large인 경우 local preprocessor config와 Base engine dependency

Source modeling manifest, 원본 음성, `data/raw`, `data/restricted`, `.env*`는
읽지 않는다. 실행 중인 full cache 또는 config에 기록된 canonical full
cache/report 경로를 입력으로 받지 않으며, review 출력으로 덮어쓰는 것도
거부한다.

## 실행

실제 smoke가 원자 publish되고 builder report가 `completed`가 된 뒤에만
실행한다. 아래 placeholder를 실제 smoke 전용 경로로 바꾼다. Canonical
full cache/report 경로를 넣으면 종료 코드 `2`로 거부된다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/review_wavlm_feature_cache_smoke.py \
  --cache-root <SMOKE_CACHE_ROOT> \
  --report <SMOKE_REPORT_JSON> \
  --config configs/experiments/wavlm_large_frozen_v1.yaml \
  --builder scripts/build_wavlm_large_feature_cache.py \
  --output artifacts/reviews/wavlm_large_frozen_v1_smoke100_v2_automated_review.json
```

Base+ smoke는 config와 builder만 다음과 같이 바꾼다.

```text
--config configs/experiments/wavlm_base_plus_frozen_v1.yaml
--builder scripts/build_wavlm_feature_cache.py
```

종료 코드는 승인 `0`, 검증 실패 `1`, 안전하지 않은 경로 `2`다. 승인과
검증 실패 JSON은 모두 임시 `.part`에 기록한 뒤 `os.replace`로
원자 publish한다. 경로 안전성 실패는 어떤 파일도 쓰지 않는다.

## 승인 계약

승인은 다음 조건이 모두 성립할 때만 생성된다.

- report schema/status가 고정값이고 `limit=100`, `full_run=false`
- config와 builder SHA-256이 reviewer의 Base+/Large 고정 pin과 일치
- report의 config/builder/model ID/revision/hash가 실제 파일과 일치
- Large builder dependency, model config/checkpoint, preprocessor의 실제
  SHA-256과 preprocessing 필드가 고정 pin과 일치
- 모든 reported shard의 실제 파일 집합, 연속 이름, byte size, SHA-256,
  행 수, row-group 수, content hash가 일치
- utterance 행이 정확히 100개이고 phone/word가 비어 있지 않음
- 모든 phone/word 행이 utterance의 ID/split/speaker/prompt와 join되고,
  발화별 index가 `0..n-1`로 연속됨
- fixed-size float16 embedding 차원이 Base+ `1536/768/768` 또는 Large
  `2048/1024/1024`이고 모든 scalar가 finite이며 영벡터가 없음
- utterance/speaker/prompt ID가 소문자 SHA-256이고 privacy 금지열이 없음
- report frame stride/receptive field와 row의 frame/chunk 합계가 config에서
  독립 계산한 convolution geometry와 일치
- `oom_count=0`
- smoke 조기 종료이므로 `source_scan_complete=false`이고, scanned row가
  included 100건과 exclusion count로 정확히 보존됨
- post-filter `limit=100`은 manifest 순서를 유지하므로 smoke output에는
  `train/dev` 중 비어 있지 않은 일부만 나타날 수 있음. 각 kind의 실제
  `observed_output_splits`는 비어 있지 않고 전부 allowed split이어야 하며,
  report의 `included_rows_by_split`은 실제 utterance shard 집계와 정확히
  일치해야 함
- evaluation split annotation은 split filtering 과정에서 역직렬화될 수
  있으나 audio decode, encoder 입력, 학습·metric label 사용은 없음
- 실제 shard 압축 byte/row를 full reference rows로 확장하고 config
  safety factor를 적용해 계산한 upper estimate가 builder report와 일치

성공 JSON은
`voice_coach.wavlm_feature_cache_smoke_review.v1` schema,
`status=approved`, `full_run_approved=true`, 입력 SHA, aggregate row/shard
수, 기술 검증 결과와 측정 resource만 포함한다. 또한 reviewer CLI/core의
현재 SHA-256, kind별 `observed_output_splits`,
`allowed_split_coverage_complete`, 그리고 full cache에서는 모든 allowed
split이 반드시 존재해야 한다는 별도 검증 규칙을 기록한다. 따라서
train-only smoke 승인은 full cache의 train/dev 완전성을 뜻하지 않는다.
개별 ID, embedding, 원본 경로는 포함하지 않는다.

Full-run gate는 review에 기록된 reviewer CLI/core SHA-256을 현재 저장소
파일에서 다시 계산해 비교한다. Full cache 자체는 독립 validator에서 각
kind에 `train`과 `dev`가 모두 존재해야 승인된다.

## 실제 Large v2 승인과 full 실행 상태

2026-07-29 KST 실제 v2 smoke와 review의 고정 결과는 다음과 같다.

- smoke status/행/OOM: `completed`, utterance/phone/word
  `100/1,798/321`, `oom_count=0`
- smoke report SHA-256:
  `38cef40a99542fe881f03ad7ec00bb1b9b6ff4c2f3859d81d3e15b15a672f813`
- review status/full gate: `approved`, `full_run_approved=true`
- approved review SHA-256:
  `42b58406c761cc3bfda2694f2bafde62b27553da5f7462b3272ab34fbdf7bd8c`
- Large builder SHA-256:
  `bfca5a5d065a7a55b3fce5671750dd4011f8a2b0e538d3c3a6aa8eab4972772f`
- reviewer CLI/core SHA-256:
  `6e42189f5a927c9ba91062e04ca5337c5a8adff94b28cd1e56f826c85ce261f3`/
  `b7dc9a105d9817daa8d109910106c4bea74b0742c09b1ef5f8292ca6f9b7c85c`
- Large config SHA-256:
  `0191afdba13e38619ab6f2c24494f0766cf9f16a802575a132eda7dadcd9c4fd`
- 선행 Base+ head report/registry SHA-256:
  `112eedfeb6348c1b5a258ae2ca7fd4b435c96b433d3326ade94a7f4399949a2c`/
  `0f994aa578a503697c53a3ed84377baea38ca34ebd4fc6e01bafac7a31b9f79f`
- config·builder·model/preprocessor·reviewer source와 위 Base+ gate를 묶은
  full combined gate: `passed`

Manifest 순서를 유지한 bounded first-100이므로 실제 v2 smoke의 관측
split은 train-only이고 `allowed_split_coverage_complete=false`다. 이는
승인 오류가 아니라 smoke가 허용 split의 부분집합만 대표한다는 명시적
한계다. Canonical Large full cache 명령은 exec session `28800`에서
시작했지만 아직 실행 중이다. Full output의 원자 publish와 독립
validator의 모든 kind별 train/dev 완전성 검증 전에는 cache 완료,
S102 완료 또는 head 실행 가능을 주장하지 않는다.

## 실패 출력

검증 실패 시 `status=failed`, `approved=false`,
`full_run_approved=false`와 비식별 오류 code/count만 기록한다. 실패
JSON에는 smoke path, 개별 SHA ID, embedding 값, 원본 locator 또는
exception 문자열을 넣지 않는다. 따라서 실패 파일 자체도 aggregate-only
산출물이다.

## Synthetic 검증

실제 GPU나 기존 smoke 산출물을 사용하지 않고 다음 범위를 검사한다.

```bash
./.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_review_wavlm_feature_cache_smoke.py
```

- 정확히 100발화인 Base+/Large synthetic multi-shard 승인
- 정렬된 train-only 100건 prefix 승인 및 coverage 불완전 기록
- 빈 split, forbidden/evaluation split 거부
- shard byte/SHA 변조
- NaN, 영벡터, 불연속 index, parent join mismatch, privacy 금지열
- limit, OOM, source scan, evaluation access, frame geometry, resource estimate,
  builder dependency pin 변조
- config/builder pin drift
- reviewer CLI/core SHA 및 split coverage 필드 누락·위조
- canonical full cache/report 입력 및 출력 덮어쓰기 차단
- 승인/실패 aggregate JSON의 원자 publish와 `.part` cleanup

## 보존된 최초 실패

최초 v1 smoke cache/report와 실패 review는 재현 근거이므로 덮어쓰거나
삭제하지 않는다. 실패 원인은 실제 output이 train-only였는데 당시
reviewer가 모든 allowed split을 smoke의 각 kind에서 강제한 것이었다.
v1 smoke report SHA-256은
`67b395299c94b5d58a489e9ed0da8eb4c140522f3a0454639acd13ec68b8bba2`,
실패 review SHA-256은
`5cd9e7571166b4676ecd4b877e282fd443de81a8931f5c82a5fe9e19ae1331f5`다.
재검토와 이후 실행은 `_v2` 경로만 사용하며 v1 실패를 승인으로
덮어쓰지 않는다.
