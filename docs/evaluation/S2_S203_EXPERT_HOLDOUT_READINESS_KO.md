# WP5 S203 전문가 holdout one-shot 감사 production static readiness

최초 작성: 2026-07-29 KST  
상태: **정적 preflight 구현 완료 / production evaluator·holdout 접근·실제 결과 0**

## 1. 결론

S203 전용 strict config, read-only fail-closed contract/CLI, 평가 카드,
논문 amendment template과 bounded fault test를 구현했다. 이 package는
S108 final passed commit, 그 commit에 결합된 S200 readiness lock, S201
completed content-hard-gate commit, S202 completed/frozen seed·aggregate
commits를 순서대로 public/static + deep artifact validation한 뒤에만 S203
preregistration과 expert-holdout attestation을 읽는다.

Production evaluator, holdout manifest/row/audio 접근, frozen S1/S2 prediction
생성·읽기·join, metric·CI·gate 계산, generation·registry·commit publisher는
의도적으로 구현하지 않았다. 현재 S108 final commit이 없으므로 full
preflight는 첫 runtime 선행자에서 종료되고, 보호 자료·모델·prediction·
holdout·metric·output 접근은 모두 0이다. 따라서 S203 완료나 WP5 배점
근거가 아니다.

## 2. S203 전용 새 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/s2_s203_expert_holdout_audit_v1.yaml` | 선행 순서, frozen input, D07/locked 분리, gate/TBD, one-shot no-access 정본 |
| `src/voice_coach/training/s2_s203_holdout_contract.py` | strict YAML/JSON, source pin, S108–S202 public/deep validation, symlink/hash/TOCTOU guard |
| `scripts/prepare_s2_s203_expert_holdout.py` | config-only/full read-only fail-closed CLI |
| `tests/unit/test_s2_s203_holdout_contract.py` | gate/TBD/leak/order/hash/symlink/TOCTOU/no-overwrite/S202 deep fault test |
| `tests/integration/test_prepare_s2_s203_expert_holdout.py` | 실제 workspace config-only 0-access와 S108-first 실패 검사 |
| `docs/S2_S203_EXPERT_HOLDOUT_EVALUATION_CARD_KO.md` | D07·holdout·locked·prediction join·privacy·완료 증거 경계 |
| `docs/paper/S2_S203_EXPERT_HOLDOUT_AMENDMENT_TEMPLATE_KO.md` | 실제 commit 전 승격 금지 PENDING 표·문장 |

최초 구현에서는 기존 tracker, 논문 정본, S104–S202 및 R00 파일을
수정하지 않았다. 이후 R00 관측값의 정본 원고 반영에 맞춰 S200–S203
config/readiness pin을 순서대로 갱신했다. 계속 바뀌는 tracker를 S203
config가 source로 pin하면 tracker가 S203 hash를 기록하는 순간 즉시
순환 drift가 생기므로, tracker pin은 제거하고 정본 원고·선행 config·
readiness·실행 소스 pin만 유지한다.

## 3. 불변 실행 순서와 S202 이중 검증

```text
S203 C1 source pins
  -> S202 현행 static contract public validation
  -> S108 research_candidate_passed commit deep validation
  -> S200 readiness lock deep validation
  -> S201 content_hard_gate_passed commit deep validation
  -> S202 seed 42/43/44 commit public validation
  -> S202 aggregate commit public validation
  -> seed registries·16 seed artifacts deep validation
  -> aggregate 5 artifacts deep validation
  -> frozen S203 preregistration
  -> expert-holdout attestation
  -> production evaluator 부재에서 중단
```

S202 public validator는 status/decision, 세 seed, S108/S200/S201/config
binding, S2 preregistration·label/split/model attestation, holdout/S1/locked
leak count 0, registry와 artifact hash 선언을 검사한다. Deep validator는
세 completed registry와 seed당 4개 artifact, aggregate 5개 artifact를
전수 hash·size·root containment로 다시 검사한다. Registry·artifact
symlink, path traversal, duplicate artifact, hash/size drift는 거부한다.

모든 C1와 predecessor snapshot은 preregistration 전에 다시 열어
device/inode/size/mtime/hash 동일성을 확인한다. 중간 변경은 TOCTOU로
fail-closed한다.

## 4. Holdout·D07·locked-test 분리

D07은 S1 gold·정렬·calibration 외부 감사이며 S203 holdout이 아니다.
다음을 모두 금지했다.

- D07 item·label·rater·prediction 재사용
- D07 완료를 S203 attestation으로 대체
- S1 train/dev/stack/calibration/locked-test 재사용
- S2 auditor train/validation 재사용
- S108 locked-test row·label·prediction을 S203 평가로 재사용

미래 attestation은 S203 대 모든 선행 cohort의 item·speaker·prompt
overlap 0과 rater cohort overlap 0을 각각 증명해야 한다. 1,000–2,000
발화, 독립 rater 2인, adjudicator 1인은 문서 근거가 있는 구조로
보존했지만 adjudication trigger·protocol·agreement gate는 TBD다.

Holdout의 최대 접근 횟수만 1로 고정했다. 정확히 무엇을 하나의 access
family로 묶는지와 원자 transaction/recovery 방법은 근거가 없어
`TBD_PREREGISTRATION`이며, 이 상태에서는 attestation 이후에도 data
manifest를 해석하지 않는다.

## 5. Frozen S1/S2와 evaluation-only join

S1은 S108 연구 후보, S2는 S202 세 seed ensemble로 동결해야 한다.
S1/S2 retrain, reselection, 결과 기반 threshold 변경은 모두 false다.

허용되는 join 열은 다음뿐이다.

- `item_id_sha256`
- `speaker_id_sha256`
- `frozen_s1_decision`
- `frozen_s2_decision`
- `adjudicated_s2_expert_gold`

Prediction은 최종 평가 입력으로만 결합한다. 학습/모델 feature, target,
pseudo-label, selection, mining에 사용할 수 없다. 현재 prediction path는
TBD이고 read/join count는 0이다.

## 6. 수치 gate와 fail-closed TBD

문서 근거가 있는 다음 수치만 보존했다.

| 정본 이름 | 보고 alias | Gate |
|---|---|---:|
| `serious_s1_misjudgment_recall` | `severe_false_negative_recall` | `>=0.80` |
| `false_high_score_relative_reduction` | — | `>=0.30` |
| `normal_sample_referral_rate` | — | `<=0.15` |
| `disagreement_expert_error_hit_rate` | — | `>=0.60` |

Metamorphic expectation `>=0.95`는 Phase-3 non-holdout gate로만 기록하고
S203 primary metric으로 복제하지 않았다.

다음은 전부 `TBD_PREREGISTRATION`이다.

- primary family, 네 metric 정의·numerator·denominator
- severe taxonomy·threshold, normal/referral/false-high/disagreement 정의
- missing·abstention policy
- CI method·level, resampling unit·반복 수, cluster 정의
- slice 차원·최소 support·small-slice policy
- multiplicity control과 gate statistic
- rater 자격·blind, adjudication trigger·protocol, agreement gate
- prediction aggregation, one-shot access family·transaction

TBD 하나라도 남은 preregistration은 거부하고 attestation으로 진행하지
않는다.

## 7. 실제 workspace 검증

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s203_expert_holdout.py --config-only
```

결과: `valid_config_only`.

- S203 source pin read: 24
- predecessor public/static validation 및 metadata/artifact read: 0
- preregistration·expert attestation·D07 read: 0
- data path/manifest/row/audio/protected/holdout: 0
- model path/weight/retrain/reselection: 0
- S1/S2 prediction·join·locked-test·GPU·metric·result·output: 0
- evaluator/holdout/metric/completion claim: false

### 현재 full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s203_expert_holdout.py
```

결과: 의도된 exit 1, `stage=predecessor_s108`, stdout 0 bytes.

- S203 source pin 24, S202 public source 15, public validation 1
- C1/static predecessor metadata read 13
- S108 commit read attempt/success: 1/0
- S108/S200/S201/S202 public/S202 deep validation: 전부 0
- predecessor artifact read: 0
- preregistration·attestation read attempt: 0/0
- D07/data/model/prediction/holdout/locked/GPU/metric/result/output: 전부 0

S202 static C1 검증을 먼저 수행한 뒤 부재한 S108 commit에서 멈췄다.
S200/S201/S202 completed evidence, preregistration 또는 외부 자료는 열지
않았다.

## 8. Test·정적 품질

- S203 unit+integration targeted: **25 passed**
- S200→S203 관련 회귀: **79 passed**
- Ruff check: 통과
- Ruff format check: 통과
- mypy contract+CLI: 통과

Bounded fixture에서 다음 fault를 확인했다.

- 네 문서 gate의 operator/value 변경
- TBD metric/CI/cluster/slice/min-support/multiplicity/adjudication 해제
- S1/S2 retrain·reselection·training-feature·locked reuse
- D07 재사용, holdout 조기 data path, access count 2
- source hash drift, path traversal, duplicate YAML key
- config/source/registry symlink
- snapshot hash·identity TOCTOU
- S202 holdout access count 1
- S202 registry/artifact hash tamper
- 현재 output 0과 미래 atomic/no-overwrite/no-symlink 요구

## 9. SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `d06d056181b032325170789b05dadc8bfcf5227ba5ccc9aa4472ae51b65abeaa` |
| contract | `35ee786e59b247c28eb2cd06e5e58c11096cb4ad7944b74c0ab6950b5d2f578b` |
| CLI | `afa42ee94aceabbb2cfaa6e589c290909c2636fb652ae80a08dcb9b39a3e0756` |
| unit test | `c64e57618d30354592071bf382cf2ddf84883d0ea7e6bb20d477db1da337c22b` |
| integration test | `c803dc67fb8c1d48421151c236b0772bf4f9b744d70d057e45578c0175f2794a` |
| evaluation card | `a9fb81d3d4d5e4b9e6eab5c80e86b056a494e3ef6d52ad61af7aa3e4f59b2a8f` |
| paper amendment template | `e1fec889cbf9456194adec25a9ddd267a6ea67b53bee92999d368fdff216197a` |

Readiness 문서 자체 hash는 자기참조를 피하기 위해 상위 tracker/handoff에서
기록한다.

## 10. C/H/M 위험 감사와 실제 blocker

현재 정적·합성 구현 범위의 미해결 critical/high/medium blocker는
**0/0/0**이다.

- Critical: predecessor 전 holdout/protected/locked 접근, retrain/reselect,
  metric 실행, D07/locked 재사용
- High: S202 public-only 수용, registry/artifact tamper, overlap 또는 prior
  access 무시, prediction을 feature로 사용, undocumented gate 발명
- Medium: symlink/traversal/duplicate key, hash/size/TOCTOU drift, overwrite,
  불완전 preregistration/attestation을 완료로 주장

각 항목은 static guard, exact schema/hash, public+deep validation 또는
bounded fault test로 fail-closed 되는 것을 확인했다. 다음은 구현 결함이
아니라 실제 연구 진행을 막는 외부/선행 blocker다.

1. S108 final `research_candidate_passed` commit 없음
2. S200 readiness lock 없음
3. S201 production commit·registry·artifact 없음
4. S202 production trainer·세 seed/aggregate completed commit 없음
5. 사람 승인 S203 preregistration 없음
6. 독립 expert holdout과 governance/disjointness attestation 없음
7. production evaluator와 실제 one-shot 결과/독립 감사 없음

실제 자료·모델·evaluator가 생기면 실제 chain에 대해 C/H/M을 다시
독립 감사해야 한다.

## 11. 다음 실행

1. S108 one-shot final S1을 완료·독립 검증한다.
2. S200 lock, S201 content gate, S202 independent auditor를 순서대로
   실제 완료한다.
3. 전문가·통계·윤리 담당자가 모든 TBD를 해소하고 preregistration을
   서명·동결한다.
4. D07/S1/S2/locked-test와 분리된 S203 holdout을 수집하고 item·speaker·
   prompt·rater overlap 0, prior access 0 attestation을 발행한다.
5. 별도 production evaluator와 atomic one-shot publisher/validator를
   구현하고 새 bounded/실제-data C/H/M 감사를 수행한다.
6. 동결 S1/S2를 단 한 transaction으로 평가하고 정본 commit을 독립
   deep validate한다.
7. 집계 결과만 amendment template과 evidence-map을 통해 논문에 승격한다.
