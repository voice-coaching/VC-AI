# WP5 S200 독립 감사기 production readiness

최초 작성: 2026-07-29 KST  
상태: **정적 계약 구현 100% / S200 실제 완료 0% / S2 실험 0%**

## 1. 결론

S200의 machine-checkable 독립성 계약, 데이터 카드, fail-closed preflight와
합성 fault test를 구현했다. 이 패키지는 S1과 S2의 backbone·alignment·
holdout·목표함수·학습 label·model feature 분리를 강제하고,
S200→S201→S202→S203의 입출력·privacy·license·metric·seed·registry/hash
계약을 고정한다.

현재 존재하는 S108 no-locked dry-run은 S200 선행자가 아니다. 별도 S108
final S1 commit이 `research_candidate_passed`와 completed registry를
제공하기 전에는 full preflight가 첫 predecessor metadata 확인에서
fail-closed한다. 따라서 현재 상태에서 읽은 S2 행·모델·보호자료·holdout·
locked-test와 생성한 metric·result·파일 출력은 모두 0이다.

S201 Whisper trainer나 S202 HuBERT trainer를 이 패키지에 가짜로 추가하지
않았다. 정확한 checkpoint·S201 WER 정책·S202 ensemble 정책이 각각 별도
사전등록 config로 고정될 때까지 downstream 실행은 금지된다.

## 2. 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/s2_s200_independent_auditor_v1.yaml` | S1/S2 분리, DAG, 데이터 카드, privacy/license/metric/seed/registry/hash 정본 |
| `src/voice_coach/training/s2_s200_auditor_contract.py` | strict YAML/JSON, source pin, predecessor·attestation, leak/alignment, 원자 publish 검증 |
| `scripts/prepare_s2_s200_auditor.py` | config-only와 full fail-closed CLI |
| `tests/unit/test_s2_s200_auditor_contract.py` | bounded 합성 policy·fault·TOCTOU test |
| `tests/integration/test_prepare_s2_s200_auditor.py` | 실제 workspace zero-access·선행자 우선 실패 검사 |
| `docs/S2_S200_DATA_CARD_KO.md` | 사람 판독용 데이터 카드와 제한 |
| 이 문서 | 구현·검증·실행 인계 |

기존 tracker, 원고, S104–S108 파일은 이 작업에서 수정하지 않았다.

## 3. 정본 독립성 계약

| 분리 차원 | S1 | S2 |
|---|---|---|
| backbone | WavLM | S201 Whisper 내용 gate, S202 HuBERT/승인된 non-WavLM SSL |
| alignment | S108 commit의 oracle/predicted provenance | utterance-level alignment-free; S1 boundary·confidence 금지 |
| split | train/dev/stack/calibration/locked | 별도 train/validation/expert holdout |
| 평가자 | S1 labeler cohort | hash가 다른 별도 S2 expert cohort |
| 목표함수 | phone·utterance·fusion | transcript hard gate + pairwise preference + ordinal audit |
| 학습 label | AI Hub silver/holistic, D07 S1 gold | 별도 S2 전문가 pairwise/ordinal만 |
| feature | WavLM·explicit·alignment confidence·S1 component | Whisper content consistency, 별도 SSL, S2 intrinsic uncertainty |

S1 prediction은 S201/S202 입력·label·feature·selection에 들어갈 수 없다.
S203에서 S1과 S2를 모두 동결한 뒤 평가 전용 동일-item join에만 쓸 수
있다. realized phone, alignment operation, error tag/count, oracle/predicted
interval, S1 logit/probability/confidence/threshold/embedding은 exact
denylist다.

## 4. 선행자와 외부 증거 순서

Full preflight는 순서를 바꿀 수 없다.

1. config와 C1 source SHA-256 검증
2. 미래 S108 final commit 검증
3. commit이 pin한 completed registry 검증
4. 그 뒤에만 S203 aggregate expert-holdout attestation 검증
5. 모든 source·commit·registry·attestation을 precommit에서 다시 읽어
   inode/size/mtime/hash 동일성 확인
6. 마지막에만 A1 aggregate readiness lock 경로를 resolve·생성

S108 commit은 다음을 모두 증명해야 한다.

- schema `voice_coach.wavlm_s108_final_s1_commit.v1`
- `status=committed`
- `decision=research_candidate_passed`
- `research_candidate_completed=true`
- locked batch access 1회, rerun 0, 결과 기반 재선택 false
- deployment approved false
- oracle 또는 predicted alignment mode의 명시적 provenance
- final config·checkpoint·calibrator·policy·report·source chain·독립
  validation receipt를 포함한 8개 필수 artifact hash와 completed registry
  hash

Oracle mode이면 `oracle_only=true`,
`predicted_alignment_evaluated=false`, deployment claim false를 함께
강제한다.

## 5. S201–S203 계약

### S201

- Whisper는 transcript content consistency/WER hard gate 전용
- sampling false, temperature 0, timestamp feature false
- S202 전에 평가하고 bypass·S202 rescue 금지
- fine phone/prosody 단독 판정 역할 금지
- FLEURS ko-KR와 ZEROTH-Korean은 ASR 외부평가 전용
- exact checkpoint·정규화·WER threshold가 별도 config에 고정되기 전
  실행 금지

### S202

- HuBERT를 우선하며 WavLM 금지
- pairwise preference + ordinal audit
- 별도 전문가 label만 허용
- seed 42/43/44와 seed별 prediction 분리
- holdout 전에 ensemble 정책 고정
- exact checkpoint와 trainer는 아직 없음

### S203

- 별도 1,000–2,000 item, 평가자 2인, adjudicator 1인
- S1 selection split item/speaker와 S1 labeler overlap 모두 0
- freeze 뒤 단 한 번 expert holdout 평가
- serious S1 error recall ≥0.80
- false-high-score 상대 감소 ≥0.30
- 정상 referral ≤0.15
- disagreement의 전문가 오류 적중 ≥0.60
- 화자 cluster bootstrap 5,000회, seed 20260729, 95% CI 병기
- S203 자체는 deployment 승인을 만들지 않음

## 6. 원자성·복구·공격면

구현은 다음 경우 출력을 만들기 전에 중단한다.

- duplicate YAML/JSON key, NaN/Inf, schema·enum·boolean 변조
- 절대경로·`..` traversal·비정규 상대경로
- config/source/predecessor/registry/attestation symlink
- source SHA-256 drift
- 불완전·실패 S108 또는 oracle/predicted provenance 혼동
- S1/S2 item/speaker/rater overlap
- 개인 연구 scope attestation·동의·blinding·보존/폐기 누락
- 기존 readiness lock 존재

Lock은 동일 directory file descriptor 안에서 임시 파일을 `O_EXCL`로
만들고 fsync한 뒤 hard-link로 no-overwrite publish한다. 합성
`after_temporary_write`, `before_publish` fault는 final lock과 `.part`를
남기지 않는다. Publication 직전 source와 두 aggregate evidence의
identity/hash를 재검증해 source-to-output TOCTOU를 차단한다. 동시
publication에서는 최초 한 건만 final 이름을 얻을 수 있다.

## 7. 검증 결과

### 전용 test

```bash
PYTHONPATH=src ./.venv-scorer/bin/python -m pytest \
  tests/unit/test_s2_s200_auditor_contract.py \
  tests/integration/test_prepare_s2_s200_auditor.py -q
```

결과: **18 passed**.

포함 범위:

- 실제 config-only zero runtime access/output
- 실제 full preflight의 S108-first fail-closed
- source pin 변조와 symlink
- predecessor traversal·symlink
- S1 prediction leakage guard 약화
- valid synthetic aggregate metadata의 단일 lock
- 순차·동시 no-overwrite
- 두 publication fault와 `.part` cleanup
- output parent symlink
- source TOCTOU
- oracle/predicted 혼동
- S1 train overlap

### 정적 검사

```bash
./.venv-scorer/bin/ruff check \
  src/voice_coach/training/s2_s200_auditor_contract.py \
  scripts/prepare_s2_s200_auditor.py \
  tests/unit/test_s2_s200_auditor_contract.py \
  tests/integration/test_prepare_s2_s200_auditor.py

./.venv-scorer/bin/ruff format --check \
  src/voice_coach/training/s2_s200_auditor_contract.py \
  scripts/prepare_s2_s200_auditor.py \
  tests/unit/test_s2_s200_auditor_contract.py \
  tests/integration/test_prepare_s2_s200_auditor.py

PYTHONPATH=src ./.venv-scorer/bin/mypy \
  src/voice_coach/training/s2_s200_auditor_contract.py \
  scripts/prepare_s2_s200_auditor.py
```

결과: Ruff check/format 및 mypy 모두 통과.

### 실제 config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s200_auditor.py --config-only
```

결과: `valid_config_only`.

- C1 source pin read: 12
- predecessor/external aggregate read: 0/0
- data/model/protected/holdout/locked/GPU read: 모두 0
- metric/result/output: 모두 0
- S200 completion claim: false

### 실제 full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s200_auditor.py
```

결과: 의도된 exit 1, `stage=predecessor_validation`.

- S108 final commit read attempt 1, successful read 0
- external aggregate read attempt 0
- data/model/protected/holdout/locked/GPU read 0
- metric/result/output 0

현재 S108 final commit이 없으므로 이 실패가 정본 상태다.

## 8. 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `a0999096662ac4763219375e570423b4bc850409017488ffca73bfc05e35fc79` |
| contract | `b9f13b578ef89586c38fd2d80b0d8d985cf735d52e2532c2958888cb1da1a35b` |
| preflight CLI | `9b4e528f025743fe18dfcf7e6bb3a2ed3e57d3bcf05c76ce459ceac520a5b9d1` |
| unit test | `7f315cbbb3f7009a2e896f4cfb95ff482a98f18a53b1335e6e71b27f9b82fece` |
| integration test | `56927ca577d3b90333661955dd733aac36932d08313227b511c1262ce912537e` |
| data card 문서 | `fb362153895cd5021cd7e066faee28dc0d3137a5d58c9af906f6788ee0d14256` |

이 readiness 문서 자체의 hash는 자기참조를 피하기 위해 외부 tracker에
기록한다. Config hash는 contract와 CLI source hash를 포함한다.

## 9. C/H/M 독립 위험 감사

현재 구현·합성 fixture 범위의 open blocker:

- Critical: 0
- High: 0
- Medium: 0

이 수치는 실제 S2 모델·자료·결과 감사가 아니다. S108 final commit,
실제 external attestation, S201/S202 code와 data가 생기면 다음을 다시
감사해야 한다.

1. exact checkpoint revision·weight hash·license
2. S2 item/speaker/rater disjointness의 원장 기반 검증
3. transcript normalization과 WER threshold의 사전등록 시점
4. S1 prediction이 S202 training pipeline에 유입되지 않았다는 전수 lineage
5. holdout 단일 접근과 seed·ensemble freeze
6. 실제 metric 재계산·slice·failure taxonomy·registry/commit deep validation

## 10. 후속 실행

1. S108 final S1을 단일 locked batch로 완료하고 final commit·registry를
   별도 validator로 검증한다.
2. S2 전문가 자료의 개인 연구 scope attestation·동의·분할·평가자 분리를 완료하고
   row-free aggregate attestation을 만든다.
3. S200 full preflight를 실행해 aggregate readiness lock을 한 번 publish한다.
4. 별도 S201 config에서 Whisper checkpoint, 한국어 transcript 정규화,
   WER threshold와 외부 ASR 평가를 사전 고정한다.
5. S201 runner·deep validator를 구현하고 실제 WER report를 만든다.
6. 별도 S202 config/trainer에서 HuBERT checkpoint·pairwise/ordinal label·
   ensemble을 고정하고 3-seed 실행한다.
7. S203에서 frozen S1/S2를 단 한 번 평가하고 네 primary gate와 CI를
   보고한다.

각 단계는 completed immutable commit과 독립 validator가 있을 때만 다음
단계로 넘어간다.
