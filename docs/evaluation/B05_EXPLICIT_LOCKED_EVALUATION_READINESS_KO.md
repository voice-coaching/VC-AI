# B05 explicit baseline locked evaluation STATIC READINESS

기준일: 2026-07-29 KST  
상태: **정적 실행 준비 계약 구현 완료, 실제 locked 실행 미승인**

## 1. 이 문서가 주장하는 범위

이 패키지는 B03 explicit oracle-boundary baseline과 S108 final S1 arm을 같은
사전등록 locked batch에서 정확히 한 번 평가하기 위한 실행 전 계약이다.
현재 패키지는 다음 작업을 하지 않았다.

- locked-test locator 해석, row/audio/label/prediction 읽기
- B03 checkpoint byte 읽기 또는 역직렬화
- 모델 추론, GPU 사용
- metric, speaker-cluster bootstrap, Holm 보정 계산
- 결과·registry·commit 작성 또는 배포
- B05/S108 점수 부여, 완료·배포 주장

따라서 현재 실제 locked access, 모델/GPU/metric/output 수는 모두 0이고
B05 점수는 0이다. 이 문서와 synthetic unit test는 연구 실험 증거가 아니다.

## 2. 고정된 B05 arm

B05는 B03 정본을 그대로 carry forward한다.

| 항목 | 고정값 |
|---|---|
| config SHA-256 | `cff6439e6b99b5f725c4214dfe1dda64b20892e8b59fbd064d88d3542c2615bd` |
| dev report SHA-256 | `a5c3ade0738777409bb57956b088e025081cb63886808c6d6fc87724d0875519` |
| registry SHA-256 | `0445d69cb18fd9105a531a4e5d342d60c7cad94050c6a031c0d7ee3ef6e2141e` |
| seed | `42, 43, 44` |
| checkpoint set SHA-256 | `5a94575da57dfd936bced3eb0068ee5fe803cba1c69ee69b68377b21b67fefda` |
| feature allowlist SHA-256 | `6a0ac985f61c3208680780962741be7d084752e720e911522827d92366e20f2f` |
| phone threshold | `0.5` (`sklearn predict`의 B03 기본 경계) |
| seed 정의 | 예측 ensemble 없이 seed별 독립 평가 후 산술 평균 |
| alignment 역할 | oracle target boundary, 연구 진단 전용 |

B06 threshold ablation은 post-hoc exploratory이며 `failed_unchanged`이다.
B06 config, checkpoint, threshold 또는 결과가 B05 정본을 대체할 수 없다.
B03의 `locked_test_evaluation_allowed: false`도 변경하지 않는다. 별도 joint
authorization이 B03 config 수정 없이 단 한 번의 외부 실행만 승인한다.

## 3. 실행 게이트와 첫 차단점

검증 순서는 다음과 같고, 앞 단계가 실패하거나 SHA가 `TBD_`이면 뒤 단계의
경로조차 해석하지 않는다.

1. 35개 static source pin, 9개 strict schema, 2개 unresolved template, B03/B06 경계
2. G04: 240/240 완료, correct 228 이상, accuracy 0.95 이상
3. D07: calibration 800, 실제 locked gold 200–1200, 2 raters + 1 adjudicator,
   agreement 및 실제 completion
4. S107 aggregate commit
5. S108 immutable arm freeze
6. no-TBD joint preregistration
7. B05 1개와 S108 1개만 포함하는 shared batch manifest
8. B03 checkpoint 3개의 실제 byte hash 검증
9. 별도 one-shot joint authorization
10. protected executor로 handoff하고 이 preflight는 종료

현재 canonical 첫 차단점은
`g04_gate.completion_sha256: TBD_G04_COMPLETION`이다. Full preflight는
`stage=g04_completion`에서 실패하며 G04 path/read, 모든 후속 gate,
checkpoint hash read, locked/model/GPU/metric/output 카운터가 0이다.

## 4. checkpoint 보안 경계

Config-only와 현재 full preflight는 checkpoint 경로를 해석하거나 byte를 읽지
않는다. 미래에 G04부터 batch manifest까지 모두 통과한 경우에만 다음 절차를
수행한다.

- config에 고정된 세 파일만 대상으로 한다.
- workspace-relative 정규 경로만 허용하고 `..`, 절대경로, symlink chain을
  거부한다.
- regular file을 `O_NOFOLLOW`로 열고 inode/device/size/mtime을 전후 비교한다.
- byte SHA-256을 exact pin과 비교하고 세 파일 snapshot을 다시 확인한다.
- pickle을 import/load하지 않으며 deserialize counter는 항상 0이어야 한다.
- hash 검증이 끝난 뒤에만 별도 one-shot authorization을 읽을 수 있다.

Checkpoint hash read와 model access/deserialization은 별도 카운터다. 단순 byte
hash가 모델 로드 또는 추론으로 잘못 기록되지 않도록 분리했다.

## 5. 단방향 artifact hash DAG

Content hash cycle을 피하기 위해 artifact 의존성은 아래 방향만 허용한다.

```text
preregistration / S108 freeze / batch manifest / authorization
  -> B05, S108 prediction manifests
  -> 각 arm의 독립 report
  -> consumed joint receipt
  -> 각 arm의 독립 registry
  -> single joint root commit
```

구체적으로:

- B05 prediction manifest와 aggregate report는 최종 batch receipt를 참조하지
  않는다. 둘은 preregistration, batch manifest, authorization, 동일 one-shot
  transaction만 bind한다.
- B05 report는 S108 report SHA를 참조하지 않는다. 두 report의 비교·결합은
  joint receipt 이후 계층에서만 한다.
- B05 registry는 B05 predictions/report와 consumed receipt만 참조하고 S108
  arm 산출물을 직접 소유하지 않는다.
- single joint root commit이 두 arm의 predictions, reports, registries 및
  receipt를 최종 결합한다.
- child registry/commit은 joint root를 역참조할 수 없고 joint root도 S108
  child commit을 참조하지 않는다. 상호 commit 참조는 금지한다.

성공 receipt는 두 arm execution count가 각각 1이고 predictions/report SHA
4개가 모두 존재하며 failure SHA는 null, 두 arm sealed가 true여야 한다.
실패 receipt는 authorization을 소비하고 failure SHA만 공개하며 부분
predictions/report SHA를 모두 null로 유지한다. 성공과 실패 모두 rerun은
금지되며 새 실행에는 새 independent test set이 필요하다.

## 6. 대규모 prediction 저장 계약

수십만~수백만 row를 하나의 JSON array로 만들지 않는다.
`b05_locked_row_predictions_v1.schema.json`은 D1 restricted Parquet shard
manifest의 JSON schema다.

- phone: seed 42/43/44 각각 최소 한 shard
- utterance: seed 3개 × pronunciation/fluency/comprehensibility 3개,
  정확히 9개 조합 각각 최소 한 shard
- 각 조합의 shard index는 0부터 연속이어야 한다.
- shard path는 normalized relative `.parquet`, SHA-256과 row count 필수
- manifest에 gold label, raw audio/locator, direct identifier, free text 금지
- row schema는 pseudonymous SHA identifiers와 prediction 값만 고정

Pure validator는 exact 조합과 중복, 연속 index, 합산 row count를 검사하지만
preflight는 실제 Parquet shard를 읽지 않는다.

## 7. 사전등록 통계 계약

B03 정본에서 이미 고정된 항목은 TBD로 되돌리지 않는다.

- slice axes: `l1`, `gender`, `proficiency`, `duration_bucket`,
  `target_phone`, `error_tag`
- cluster unit: `speaker_id_sha256`
- bootstrap repetitions: 5000
- confidence level: 0.95
- multiple comparison correction: Holm

아직 근거가 필요한 정확한 resampling method, estimand/family mapping,
locked population·confusion 정의, missing/undefined/minimum-support 정책은
사전등록 승인 전까지 TBD다. Approved preregistration에서는 모든 TBD가
해소되어야 하며 canonical fixed value와 충돌하는 승인값은 거부한다.
승인본은 SHA 정책만 두지 않고 concrete minimum-support threshold들과
`slice_result_keys`, `bootstrap_result_keys`, `holm_hypotheses`의 exact
manifest를 최소 1개 이상 명시해야 한다.

Aggregate validator는 다음을 강제한다.

- phone seed 결과 정확히 3개, confusion/support 산술 일치
- utterance seed × outcome 정확히 3×3, 중복 금지, 각 5×5 ordinal confusion
  합과 support 일치
- preregistered missing/undefined 상태와 null 값 일치
- 사전등록된 모든 slice/bootstrap/Holm key의 exact equality; 빈 배열,
  누락, 추가, 순서 변경, 중복 금지
- concrete minimum-support threshold에 따른 estimable/undefined 상태
- speaker bootstrap method/5000/0.95 binding과 interval 순서
- Holm family size, hypothesis 중복, raw ≤ adjusted, adjusted p 기반 decision

## 8. 파일

- 설정: `configs/experiments/b05_explicit_locked_evaluation_v1.yaml`
- 계약: `src/voice_coach/evaluation/b05_locked_contract.py`
- CLI: `scripts/prepare_b05_explicit_locked_evaluation.py`
- schemas/templates: `configs/evaluation/b05_*.json`
- unit test: `tests/unit/test_b05_locked_contract.py`
- integration test:
  `tests/integration/test_prepare_b05_explicit_locked_evaluation.py`

## 9. 운영 명령

Static-only:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_b05_explicit_locked_evaluation.py --config-only
```

Ordered full preflight:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_b05_explicit_locked_evaluation.py
```

현재 full command의 nonzero exit는 정상적인 fail-closed 결과다. G04 실제
completion artifact와 exact SHA가 없는 상태에서 이 값을 임의로 채우거나
후속 gate/locked data를 먼저 조회해서는 안 된다.

## 10. 다음 작업

1. G04 240건 수동 정렬 audit을 완료하고 privacy-safe completion artifact와
   immutable SHA를 외부 승인 절차로 확정한다.
2. G04 통과 후에만 D07 실제 completion을 완료·고정한다.
3. S107 실제 commit과 S108 no-locked dry-run을 완료한 뒤 immutable S108 arm
   freeze를 작성한다.
4. 미정 통계 정책을 독립 통계 검토로 확정하고 no-TBD preregistration을
   세 승인자가 서명한다.
5. data custodian이 locator 없는 shared batch control manifest를 작성한다.
6. 모든 앞 단계를 통과한 뒤 checkpoint byte hash를 검증하고 별도 one-shot
   authorization을 발행한다.
7. 별도 restricted executor가 두 arm을 같은 transaction에서 한 번 실행한다.
   성공 또는 실패 모두 authorization을 소비한다.
8. 단방향 DAG 순서로 receipt, registries, joint root commit을 seal한 뒤에만
   결과를 함께 공개하고 논문 수치를 갱신한다.

이전 단계의 결과를 보고 arm, threshold, feature, analysis family를 바꾸거나
같은 locked set을 다시 실행하는 것은 금지한다.
