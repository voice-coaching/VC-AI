# WP5 S202 별도 표현 pairwise·ordinal 감사기 production readiness

최초 작성: 2026-07-29 KST  
상태: **정적 preflight 구현 완료 / production trainer·실제 실행·결과 0**

## 1. 결론

S202 전용 strict config, read-only fail-closed contract/CLI, 데이터·모델
카드, 논문 amendment template과 bounded test를 구현했다. 이 package는
S108 final passed commit, 그 commit에 결합된 S200 readiness lock,
S201 completed content-hard-gate commit을 순서대로 public/static +
deep artifact validation한 뒤에만 S202 preregistration으로 이동한다.
세 선행자보다 data/model/output path를 먼저 해석할 수 없다.

S1과 backbone, alignment, label, feature, split/holdout, objective를
분리했다. S1 prediction·score·label·WavLM/explicit feature,
oracle/predicted alignment, AI Hub silver/phone proxy label과 Whisper
transcript/WER/CER/content mismatch/ASR score를 S202 feature 또는 target으로
사용하지 못하도록 exact denylist와 allowlist를 검증한다.

저장소 근거는 HuBERT family와 fairseq source/license 수준까지만
확인된다. Exact checkpoint/revision/preprocessor/local bundle, 전문가
pairwise·ordinal schema, rater/adjudication, inner selection, loss,
metric·CI·gate는 근거가 없어 모두 `TBD_PREREGISTRATION`으로 고정했다.
이를 임의 값으로 채운 production trainer는 만들지 않았다.

현재 S108 final commit이 없으므로 실제 full preflight는 첫 선행자에서
의도대로 종료된다. 실제 데이터·모델·download·GPU·metric·prediction·
checkpoint·result·output은 모두 0이며 S202 완료나 WP5 배점 근거가
아니다.

## 2. S202 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/s2_s202_independent_auditor_v1.yaml` | 선행자 순서, 독립성, TBD blocker, label/split/seed/metric 정본 |
| `src/voice_coach/training/s2_s202_auditor_contract.py` | strict YAML/JSON, source pin, S108/S200/S201 deep validation, symlink/hash/TOCTOU guard |
| `scripts/prepare_s2_s202_independent_auditor.py` | config-only/full read-only fail-closed CLI |
| `tests/unit/test_s2_s202_auditor_contract.py` | policy·hash·symlink·traversal·TOCTOU·S201 artifact fault test |
| `tests/integration/test_prepare_s2_s202_independent_auditor.py` | 실제 workspace zero-access와 S108-first 실패 검사 |
| `docs/S2_S202_INDEPENDENT_AUDITOR_DATA_MODEL_CARD_KO.md` | 모델·label·rater·split·privacy·license·claim 경계 |
| `docs/paper/S2_S202_INDEPENDENT_AUDITOR_AMENDMENT_TEMPLATE_KO.md` | 실제 commit 전 승격 금지 PENDING 논문 표·문장 |

기존 tracker, 정본 원고, S104–S201과 R00 파일은 수정하지 않았다.
Trainer, model code, downloader, feature cache와 metric code도 추가하지
않았다.

## 3. 선행자 deep-validation 계약

Runtime 순서는 다음과 같다.

```text
S202 C1 config/source pins
  -> S108 passed commit + completed registry + 필수 artifact 전수 hash
  -> S200 readiness lock + S108/config/source/data-card binding
  -> S201 committed content-hard-gate result + completed registry
     + 6개 필수 artifact 전수 hash
  -> S202 frozen preregistration
  -> expert label/split attestations
  -> exact local-only model bundle
  -> 별도 production trainer 구현 대기
```

S108/S200은 이미 검증된 S201 public static contract와 그 deep validator를
재사용하되, S202가 선언한 path가 S201 contract와 정확히 같은지도 먼저
대조한다. S201 commit에는 다음을 강제한다.

- schema/status/decision:
  `voice_coach.s2_s201_whisper_content_commit.v1` / `committed` /
  `content_hard_gate_passed`
- production runner와 content gate 실제 완료
- gate bypass, S202 rescue, fine-phone/prosody 역할 모두 false
- threshold/model/data attestation SHA와 metric/prediction 존재
- S1 prediction, oracle alignment, Whisper timestamp feature, locked-test,
  결과 기반 재선택 count 0
- S108/S200/S201 config hash binding
- completed registry와 config/model-card/normalization/report/privacy-safe
  prediction/independent-receipt 6개 artifact의 path·size·SHA 전수 검증

모든 path는 canonical relative path여야 하고 symlink/traversal을
거부한다. 파일은 열린 descriptor의 `fstat` 전후 identity와 최종 path
identity를 비교한다. 최종 gate 직전에 source, predecessor metadata와
artifact를 다시 snapshot 검증해 TOCTOU를 차단한다.

## 4. S202 독립성·모델·라벨 blocker

### 독립성

- raw input: 가명 item/speaker ID, waveform, routing-only
  `content_gate_pass`
- feature: independent SSL hidden state/attention mask, S2 intrinsic
  uncertainty만 허용
- alignment: `alignment_free_utterance`
- S1 다섯 split 재사용 금지
- S1/WavLM/silver/oracle/Whisper ASR score exact denylist
- expert holdout은 S203 owner이고 S202 access false

### 모델

고정 가능한 것은 fairseq source revision
`3d262bb25690e4eb2e7d3c1309b1e9c406ca4b99`와 MIT license hash뿐이다.
Exact model ID/checkpoint revision/preprocessor/weight manifest/local bundle은
모두 `TBD_PREREGISTRATION`; local-files-only true, network fallback와
자동 download false다.

### Pairwise·ordinal label

허용 family는 pairwise preference와 ordinal audit지만 preference enum,
tie/abstain, pair sampling, ordinal scale/category, rater 수·자격·blind·
calibration, disagreement/adjudication과 agreement gate는 모두
`TBD_PREREGISTRATION`이다. S1/AI Hub/phone proxy/Whisper score 파생 label은
금지한다.

### Split·seed·selection

Item·speaker·prompt disjoint를 모두 요구하고 overlap count는 0이어야 한다.
실제 attestation은 아직 없어 TBD다. Seed 42/43/44와 seed artifact 분리,
결과 기반 재선택 금지, S203 one-shot holdout만 고정했다. Inner split,
selection metric/direction, epoch·early stop, dev schedule, ensemble과
freeze manifest는 TBD다.

### Objective·metric·CI·gate

Pairwise/ordinal family와 3상태 출력만 고정했다. Loss와 weighting,
uncertainty/decision rule, primary/secondary metric, CI, resampling,
multiplicity, development/promotion gate는 모두 TBD다. Phase 3의 최종
S1+S2 gate를 근거 없이 S202 dev gate로 복사하지 않았다.

## 5. 실제 검증 결과

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s202_independent_auditor.py --config-only
```

결과: `valid_config_only`.

- C1 source pin read: 15
- predecessor/preregistration/external/data/model/protected/holdout/
  S1/oracle/silver/Whisper-score/locked/GPU 접근: 모두 0
- metric/prediction/checkpoint/result/output path/output: 모두 0
- trainer/execution/completion claim: false

### 현재 full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s202_independent_auditor.py
```

결과: 의도된 exit 1, `stage=predecessor_s108`.

- S202 source pin read 15
- S201 public C1/static source 검증으로 predecessor metadata read 13
- S108 commit read attempt 1, successful S108 read 0
- S108/S200/S201 successful validation count 모두 0
- preregistration/external/data/model/protected/holdout/S1/oracle/silver/
  Whisper-score/locked/GPU/metric/prediction/checkpoint/result/output 모두 0

### Test·정적 품질

- S202 unit+integration targeted: **18 passed**
- S200→S202 관련 회귀: **54 passed**
- 전체 unit 회귀: **577 passed**, 기존 warning 7개만 발생
- Ruff check/format: passed
- mypy: passed

Test는 duplicate YAML/JSON 경계, source hash drift, config/source/registry
symlink, predecessor traversal, S1/oracle/silver/Whisper-score 누수, 미승인
model/metric 값, source identity TOCTOU, 완전한 합성 S201 commit과 artifact
tamper를 포함한다. 모든 실패에서 output은 0이다.

## 6. 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `d401ab5466971e02adb706379eaa01a9e116b232a86eca396dfd2348be22aa65` |
| contract | `0ba78271b3d4d99ec3fff865e65bbe992428edfb587785021632677bc46fdaac` |
| preflight CLI | `05cddea0070d146a43f830c3e0c767163bc913530bace8dbd61d53b37ec7c97d` |
| unit test | `8db7ee5448735ce2e8c0313b6dce86634406c59c63f6d78d0e5dc5334fc9a64e` |
| integration test | `0370d387170105a810bd0175e3d8f69afdbcdf09d4ee54509b1171510d3e36bf` |
| data/model card | `cadafe868cda02955134a5d89f9d94aa4b6b881e812f0732a2e7d30cc2729c3d` |
| paper amendment template | `62da51dbe9289acf349f177f69c4585ef08c48d2b363055c36874bd235935417` |

Readiness 문서 자체 hash는 자기참조를 피하기 위해 상위 tracker/handoff에서
기록한다.

## 7. C/H/M 위험 감사

현재 정적·합성 구현 범위의 open blocker:

- Critical: 0
- High: 0
- Medium: 0

검사 범위는 predecessor 순서 우회, data/model/output 조기 해석,
S1/oracle/silver/Whisper-score 누수, model identity 허위 고정, label·metric
임의 값, symlink/traversal/hash/identity/TOCTOU, 불완전 S201 commit과
완료·결과 허위 주장이다.

다음은 결함을 숨긴 것이 아니라 실행을 의도적으로 막는 외부 선행
조건이다.

1. S108 final passed commit 없음
2. S200 readiness lock 없음
3. S201 completed hard-gate commit/runner/result 없음
4. S202 모델·label·rater·split·objective·metric·CI·gate preregistration 없음
5. S2 expert data/governance attestation 없음
6. exact local-only model bundle 없음
7. production trainer·실제 checkpoint/result/commit 없음

실제 자료·모델·trainer가 생기면 C/H/M을 다시 독립 감사해야 한다.

## 8. 다음 실행

1. S108 final, S200 lock, S201 completed commit을 순서대로 완료·검증한다.
2. 데이터나 model path를 열기 전에 모든 TBD를 사람 승인
   preregistration으로 동결한다.
3. 평가자/adjudication과 item·speaker·prompt disjoint 원장을 aggregate
   attestation으로 만든다.
4. Exact model/revision/preprocessor를 승인하고 offline local bundle을
   전수 hash attestation한다.
5. 이 package와 분리해 production trainer, registry, commit, deep
   result validator를 구현·공격감사한다.
6. 3-seed 실행과 독립 metric/CI 재계산 뒤에만 amendment template을
   정본 원고/evidence map으로 승격한다.
