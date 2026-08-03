# S104·S105·B07 논문 amendment production-static readiness

## 1. 현재 결론

S104, S105, B07의 completed evidence를 논문에 편입하는 코드 경로는
production-static 기준으로 구현을 완료했다. 그러나 이 문서를 작성한 시점에는
실제 completed commit 세 개를 소비하지 않았고, canonical 원고와
`evidence-map.yaml`, 기존 artifact와 registry도 읽거나 수정하지 않았다.

현재 amendment 전용 manifest의 상태는 `config_only`다. 따라서 기본 CLI는
fail-closed하며, 아래 config-only 명령만 성공한다.

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s104_s105_b07_paper_amendment.py \
  --validate-config-only
```

검증 결과의 실제 counter는 다음과 같다.

| Counter | 현재 값 |
|---|---:|
| actual metric values read | 0 |
| actual artifact/registry files read | 0 |
| actual canonical files read | 0 |
| actual outputs written | 0 |

## 2. 구현한 하위 task와 완성도

| 하위 task | production-static 완성도 | actual 완성도 |
|---|---:|---:|
| config-only/ready manifest 이중 상태와 zero-access preflight | 100% | 0% |
| S104 → S105 → B07 공개 deep-validator 선행 순서 | 100% | 0% |
| completed commit과 report·registry·execution-lock hash-chain 재결박 | 100% | 0% |
| S104 seed/mean/sample-SD paper-safe Markdown·JSON renderer | 100% | 0% |
| S105 committed `ablation_table.md` byte 재사용 검증 | 100% | 0% |
| S105 bootstrap·CI·raw/Holm p·subgroup·incremental family renderer | 100% | 0% |
| B07 GOP·explicit-refit control·selection paper-safe renderer | 100% | 0% |
| JSON `null` → literal `"NA"` 무손실 투영 계약 | 100% | 0% |
| exact active manuscript/evidence-map hash lock | 100% | 0% |
| 18개 PENDING marker와 marker-set SHA 보존 | 100% | 0% |
| candidate-only 원고/evidence-map 생성과 generic evidence validation | 100% | 0% |
| content-addressed generation·renderer receipt·promotion receipt·commit | 100% | 0% |
| manual apply 이전 canonical write 금지 | 100% | 0% |
| 합성 unit/integration·tamper·early-failure 검증 | 100% | 해당 없음 |

production-static 합계는 100%다. 실제 evidence 획득과 논문 편입은 아직
0%이며, 숫자나 gate status를 추정해 채우지 않았다.

## 3. fail-closed 실행 순서

ready 실행은 다음 순서를 바꿀 수 없다.

1. manifest 자체의 schema, `status=ready`, zero pre-execution counter,
   candidate-only 계약을 검사한다.
2. validator script와 stage config의 고정 fingerprint만 확인한다.
3. 공개 validator를 S104, S105, B07 순서로 실행한다.
4. 각 validator가 각각 `valid`와 고정 artifact/registry count를 반환해야
   다음 단계로 간다. S105 validator는 S104 predecessor를 다시 검증한다.
5. 세 validator가 모두 성공한 뒤에만 report, registry, execution lock,
   commit, canonical 원고와 evidence-map을 읽는다.
6. manifest의 모든 입력이 해당 completed commit에 실제 path/hash/size로
   결박됐는지 재검증한다.
7. stack, calibration, locked-test access flag와 registry split을
   fail-closed 검사한다.
8. aggregate를 seed 값에서 다시 계산하고 committed S105
   `ablation_table.md`를 byte 단위로 재현한다.
9. paper-safe Markdown/JSON과 여섯 개 amendment section을 렌더링한다.
10. canonical 바이트는 그대로 둔 채 candidate 원고와 candidate
    evidence-map만 content-addressed generation에 쓴다.
11. candidate 전용 evidence-map으로 generic evidence validator를 실행한다.
12. PENDING count 18과 marker-set SHA
    `1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673`
    가 active/candidate/validation receipt에서 모두 같아야 한다.
13. 모든 source를 TOCTOU 재해시한 뒤 immutable promotion receipt와 atomic
    commit pointer를 쓴다.

CLI에는 canonical apply 기능이 없다. 생성물은 review-only candidate이며,
사람이 diff를 검토하고 별도 명시적 patch를 적용하기 전에는 canonical
원고와 evidence-map에 write가 발생하지 않는다.

## 4. null과 paper-safe 출력 계약

원시 JSON의 `null`은 metric 실패나 미정 상태를 뜻할 수 있으므로 다음 규칙을
고정한다.

- JSON `null`을 0으로 바꾸지 않는다.
- 해당 행이나 열을 생략하지 않는다.
- paper-safe JSON의 evidence 값과 Markdown cell에는 정확히 `NA`를 쓴다.
- Markdown cell에는 보이지 않는 unique evidence comment를 붙이고,
  candidate evidence-map의 exact claim이 같은 paper-safe JSON 값을
  검증한다.
- 숫자는 유한값만 허용하고 기본 여섯 자리 반올림을 generic evidence
  validator가 같은 규칙으로 다시 비교한다.

S105의 committed ablation table도 `null → NA`를 사용하는 기존 deterministic
renderer와 byte 단위로 일치해야 한다.

## 5. candidate amendment 범위

ready manifest는 hash-locked canonical 원고에서 각각 한 번만 나타나는 exact
anchor를 고정해야 한다. renderer는 다음 블록을 만든다.

- Methods: B07 train-only nested selection, S104 adapter, S105 cumulative
  hierarchy와 word no-metric 경계
- Analysis status: B07/S104/S105 completed dev-only 행
- Results: `WAVLM_S104_ADAPTER_RESULTS_TABLE`,
  `WAVLM_S105_HIERARCHY_RESULTS_TABLE`, `B07_GOP_RESULTS_TABLE`
- Discussion: 관찰치 한계와 fusion PENDING 유지
- Reproducibility: commit SHA와 deep-validator status
- Appendix: S105 8-item incremental Holm family

기존 PENDING marker는 좁히거나 삭제하지 않는다. 특히 broad S101–S108,
final S1, whole-study hyperparameter marker를 그대로 보존한다.

## 6. actual 전환 gate

다음 항목이 전부 충족되기 전에는 manifest를 `ready`로 바꾸지 않는다.

1. S104 canonical 실행이 completed registry와 immutable 16-artifact commit을
   만들고 public commit validator를 통과한다.
2. 같은 S104 commit을 predecessor로 고정한 S105 실제 실행이 82-artifact,
   5-registry completed commit을 만들고 public validator를 통과한다.
3. B07 canonical 실행이 21-artifact completed commit을 만들고 public
   full-recomputation validator를 통과한다.
4. 세 config, validator, commit, report, registry, execution lock,
   per-seed metric/selection, S105 ablation table의 path/hash/size를 별도
   ready manifest에 고정한다.
5. active canonical manuscript와 evidence-map SHA를 실행 직전에 고정한다.
6. 여섯 exact anchor가 각 1회이고 기존 amendment block이 없음을 사람이
   검토한다.
7. actual ready manifest 자체를 별도 review한 후에만 candidate 생성 CLI를
   실행한다.

이 gate에서 실제 metric을 보게 되더라도 결과를 먼저 수기로 원고에
복사해서는 안 된다. renderer와 candidate generic evidence validation이
끝난 뒤 review bundle의 diff만 검토한다.

## 7. 검증 범위

합성 검증은 다음을 포함한다.

- config-only counter와 output 0
- blocked manifest가 validator와 경로 resolve 전에 종료
- deep validator 호출 순서 S104 → S105 → B07
- 첫 validator 실패 시 존재하지 않는 report도 열지 않음
- completed commit의 모든 declared input 재결박
- S105 committed table 재사용
- `null → NA`, bootstrap/Holm/subgroup/incremental 표
- 세 결과 block과 여섯 amendment section
- 18개 PENDING marker와 marker-set hash 불변
- candidate generic evidence validator 통과
- canonical 원고/evidence-map byte 불변
- published output tamper 시 immutable loader 거부

2026-07-29 현재 검증 결과:

- amendment targeted unit/integration: **6/6 passed**
- repository 전체 pytest: **1,212/1,212 passed**
- repository 전체 Ruff lint: **passed**
- amendment 4개 Python 파일 Ruff format-check: **passed**
- amendment 4개 Python 파일 mypy: **0 issue**
- config-only CLI: **passed**, actual counter와 output 모두 0
- 실제 amendment output directory: **없음**

저장소 전체 Ruff format-check는 이번 변경 밖의 기존 36개 파일이 formatter
baseline과 달라 실패했다. 저장소 전체 mypy도 기존 22개 파일의 missing
third-party stubs와 선행 typing debt 103건 때문에 실패했다. 새 amendment
파일에서는 format 또는 mypy 오류가 없으며, 실행 중 pin과 사용자 소유
변경을 보호하기 위해 기존 36/22개 파일은 수정하지 않았다.

실제 S104/B07 source, config, runner, contract, readiness, project dependency
pin은 이 작업에서 수정하지 않는다.
