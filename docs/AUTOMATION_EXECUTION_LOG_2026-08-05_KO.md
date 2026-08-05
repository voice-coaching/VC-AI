# 자동화 실행 로그

기준 시각: 2026-08-05 KST

이 문서는 사람 평가를 대신하지 않는 Codex 자동화 작업과 그 판정만
기록한다. 제한 데이터 원문, raw identifier, 원천 경로, secret, 음성
산출물은 기록하지 않는다.

## 1. S104 v2 terminal 확인

- `intelligentai-s104-v2.service`는 확인 시점에 supervisor 아래에서
  실행 중이었다.
- v2 출력 루트에는 `execution_lock.json`만 있었고 terminal artifact,
  completed registry, `commit.json`은 확인되지 않았다.
- 독립 검증 명령을 실행했으나 `builtins.FileNotFoundError`로 종료했다.
- 판정: S104 v2 미완료. 이 결과는 fail-closed이며 실행 실패를 성공으로
  해석하지 않는다.

실행한 검증:

```text
.venv-scorer/bin/python scripts/train_wavlm_s104_adapter.py \
  --config configs/experiments/wavlm_s104_adapter_v2_current_code.yaml \
  --validate-commit-only
```

## 2. 사람 gate를 건드리지 않는 정적 검증

다음 명령은 모두 데이터·음성·GPU·locked split·평가 결과를 읽지 않고
산출물을 쓰지 않는 config-only 검증이다.

| 대상 | 결과 | 의미 |
|---|---|---|
| S104 v2 | `config_valid` | config와 고정 source pin 유효; 실제 실행·결과 아님 |
| S105 hierarchy | `config_valid` | S104 완료 전 실제 실행은 금지 |
| S106 fusion | `config_valid` | predecessor와 stack을 읽지 않은 정적 검증 |
| C03 expert audit | `valid_config_only` | 사람 평가·rating·metric·결과 0 |
| E01 metamorphic | `valid_config_only` | perturbation·model·metric 실행 0 |
| E00 closed loop | `valid_config_only` | session·audio·API·coaching·rerecord 실행 0 |

## 3. 후속 자동화 상태

- S104 terminal commit 및 독립 검증 전에는 S105 v2 re-pin과 S106 이후
  실제 실행을 시작하지 않는다.
- G04 최종 지표 결정, D07 scope/evaluator consent/calibration/independent
  evaluation, S2 auditor, C03 expert audit, D08 rights/consent는 사람 gate로
  유지한다.
- Codex/Claude가 만든 체크리스트·분석·실행 패키지는 사람 평가 완료로
  승격하지 않는다.


## 4. 제약 문서 물리적 이동과 의존성 검증 (2026-08-05)

- 제약·gate·privacy/license·상태 정본 30개를 `docs/constraints/`로 이동하고 카탈로그를 추가했다. 루트 `docs/README.md`, README, `.codex` 시작 문구, config/source/test의 참조를 새 경로로 갱신했다.
- B07 완료 commit의 불변 source-pin은 `docs/GOP_ACOUSTIC_POSTERIOR_READINESS_KO.md`를 계속 기록하므로, canonical 파일은 `docs/constraints/`에 두고 두 legacy 경로는 동일 파일을 가리키는 호환 symlink로만 보존했다. B07 commit/config/registry를 소급 수정하지 않았다.
- Markdown canonical local link 검사: 127개 검사, 누락 0개. YAML path·SHA-256·size source-pin 교차검증: `stale_pairs=0`.
- 주요 계약·통합 회귀 묶음과 publication v1/v2 테스트가 통과했다. 이는 문서/계약 정합성 검증이며 S104 실제 완료, locked 평가, 사람 평가, 연구 결과 승격을 의미하지 않는다.
