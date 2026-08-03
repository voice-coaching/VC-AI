# P04·P05 정합성·의미 최신성 감사 준비도

기준 시각: 2026-07-29 KST  
판정: **무결성 통과 / 의미 갱신 대기**
(`integrity_pass_semantic_update_pending`)

## 1. 결론과 완료 경계

P04 gap 40행과 P05 증거 정합성을 현재 고정 source에서 재계산하고,
그 결과를 content-addressed JSON report·receipt와 atomic commit으로
봉인하는 경로를 구현하고 실제 실행했다.

현재 확인된 값은 다음과 같다.

- P04: 총 40행, 각 domain `S/M/E/F/R=8`, `A/B/C=33/0/7`,
  coverage `82.5%`
- P05 evidence validation: artifact `36/36`, claim `475/475`
- PENDING marker: `18`, 정렬 multiset SHA-256
  `1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673`
- locked-test guard: 16개 entry 모두 access `false`
- registry metric split: `dev`, `dev_exploratory`, `inner_val`만 존재
- P04/P05 tracker SHA 기록과 full SHA reference: 일치

이 결과는 P04 또는 P05 완료 판정이 아니다. Report는
`p04_complete=false`, `p05_complete=false`,
`manuscript_semantic_stale_resolved=false`를 명시한다. 외부 사람 평가와
locked-test 완료도 주장하지 않는다.

## 2. Mixed-resolved 의미 상태

무결성은 통과했고, 이전 세 항목 중 두 항목은 canonical 문서와
evidence chain에서 해결됐다. B07 한 항목만 의미 갱신 대기로 남는다.

남은 finding:

1. `B07_COMPLETED_EVIDENCE_NOT_PROMOTED`
   - B07 registry와 commit은 3-seed dev-only oracle/silver 실행을
     `completed`로 봉인했다.
   - 원고와 claim-gap에는 B07 lineage·metric이 없고 GOP를 후속 작업으로
     서술한다.
   - 이 결과는 locked-test, deployable, independent-gold 또는 P04/P05
     완료 근거로 승격할 수 없다.
해결 검증:

1. `CLAIM_GAP_P04_C_COUNT_RESOLVED`
   - Canonical gap table과 claim-gap의 P04 C 개수가 모두 정확히 `7`이다.
   - 기존 stale `9` anchor가 없음을 함께 검증한다.
2. `DATA_LICENSE_LEDGER_EVIDENCE_MAPPING_RESOLVED`
   - Evidence-map alias `data_license_source_ledger`가 ledger의 exact
     path/hash를 가리키고, 원고가 ledger path를 정확히 한 번 언급한다.
   - 이는 mapping 해결일 뿐이다. KSS 원권리, model artifact·재배포
     감사와 원고의 license `PENDING`은 여전히 해제할 수 없다.

따라서 정확한 상태는 `integrity_pass_semantic_update_pending`이며,
`passed`, `complete`, `semantic_stale_resolved`로 축약하면 안 된다.

## 3. 구현 산출물

| 역할 | 경로 |
|---|---|
| 고정 계약 | `configs/experiments/p04_p05_gap_validation_v1.yaml` |
| validator·publisher·deep validator | `src/voice_coach/reproducibility/p04_p05_gap_validation.py` |
| CLI | `scripts/validate_p04_p05_gap.py` |
| 단위·변이 테스트 | `tests/unit/test_p04_p05_gap_validation.py` |
| CLI 통합 테스트 | `tests/integration/test_validate_p04_p05_gap.py` |
| stable namespace lock | `artifacts/reproducibility/.p04_p05_gap_validation.publish.lock` |
| output-local lock | `artifacts/reproducibility/p04_p05_gap_validation/.publish.lock` |
| 현재 atomic pointer | `artifacts/reproducibility/p04_p05_gap_validation/commit.json` |
| 현재 report | `artifacts/reproducibility/p04_p05_gap_validation/generations/29f81cbfca87530dd1a825fb8f1bff407ca91b0a158733919cd9f2e165bcbc2b/report.json` |
| 현재 receipt | `artifacts/reproducibility/p04_p05_gap_validation/generations/29f81cbfca87530dd1a825fb8f1bff407ca91b0a158733919cd9f2e165bcbc2b/receipt.json` |

Report는 aggregate source의 path·SHA-256·크기, 집계 검증 결과, 남은
finding 한 개와 해결 증명 두 개만 보존한다. Raw/restricted sample,
개인식별정보, locked metric payload 또는 외부 사람 평가 record를 싣지
않는다.

## 4. 접근·쓰기 경계

`--validate-config-only` 실제 결과:

- declared aggregate source: 14
- declared implementation source: 2
- canonical/aggregate/locked/external/raw source read: 모두 `0`
- output write: `0`
- 상태: `valid_config_only`

실제 감사 결과:

- aggregate source file read: `14`
- raw/restricted file read: `0`
- locked-test file read: `0`
- external-human record read: `0`
- canonical file write: `0`

경로는 workspace-relative regular file만 허용한다. 절대경로, traversal,
symlink, `.env*`, `raw`, `restricted`, `locked`, `secret`, `credential`
경로를 aggregate source로 선언하면 fail-closed한다.

Canonical 원고, evidence-map, methods/ethics gap, claim-gap, tracker,
manuscript evidence 문서·report와 license ledger·matrix는 이 작업에서
수정하지 않았다. 검토 완료된 canonical integration revision을 읽고 새
exact pin으로만 재발행했다.

## 5. 봉인·재검증 계약

1. Strict YAML unique-key loader로 config schema와 exact path allowlist를
   검사한다.
2. canonical config path
   `configs/experiments/p04_p05_gap_validation_v1.yaml`, config SHA-256,
   output root·generation/report/receipt/commit 이름을 하나의 namespace로
   report·receipt·commit에 결속한다. Alternate config/output path는
   동일 byte여도 거부한다.
3. Output root 밖의 고정 namespace lock
   `.p04_p05_gap_validation.publish.lock`과 output-local `.publish.lock`
   (둘 다 `0600`, `st_nlink=1`)을 순서대로 잡는다. Namespace parent와
   output root는 `lstat → O_DIRECTORY|O_NOFOLLOW open → fstat → lstat`로
   device/inode를 결박하고 descriptor를 transaction 종료까지 유지한다.
   Source audit부터 post-publish deep validation 종료까지 exclusive
   lock을 유지하며, read-only deep validator는 같은 경계의 shared
   lock을 사용한다.
4. 구현 module·CLI와 14개 aggregate source의 SHA-256·크기를 확인한다.
   모든 stable read는 pathname `lstat` 전·후와 열린 descriptor의
   device/inode/mode/nlink/size/mtime/ctime을 비교한다.
5. 40행 산술, evidence count, PENDING hash, locked/split guard와 P04/P05
   SHA 기록을 재계산한다.
6. B07 commit·registry·aggregate·paired report lineage와 metric을
   교차 결박한다.
7. B07 finding 한 개와 C-count/license mapping resolution 두 개가
   정확한 순서로 존재할 때만 mixed-resolved 상태를 생성한다. Claim-gap
   C `7`의 canonical 일치와 stale `9` 부재, ledger evidence alias의
   exact path/hash, 원고 ledger path mention을 fail-closed 검증한다.
8. 모든 generation·commit I/O는 고정 output directory descriptor의
   `/proc/self/fd/<fd>` 경로를 통해 수행한다. Canonical pathname이 다른
   inode로 교체되면 `output_root_rebound`로 fail-closed하며, rollback도
   같은 held descriptor에서 수행한다.
9. report·receipt를 각각 `0440`·`st_nlink=1`로 쓰고 generation exact
   tree를 `0550`·`st_nlink=2`로 봉인·fsync한 뒤 원자 rename한다.
10. source와 config를 재검증하고 commit을 `0440`·`st_nlink=1`로
   atomic publish한다.
11. 같은 exclusive lock 안에서 report·receipt·현재 source를 완전히
    다시 계산하는 deep validation을 수행한다.
12. Post-commit deep validation이 실패하면 직전 deep-valid commit
    byte를 atomic restore한다. 이전 valid commit이 없으면 새 commit만
    unlink하고 parent directory를 `fsync`한다. 봉인 generation은
    삭제하지 않는다.

동일 source에서는 같은 generation을 재사용한다. Report/receipt byte,
commit binding, source hash 또는 generation tree가 달라지면 읽기 전용
deep validator가 거부한다. Generation/report/receipt/commit의 symlink,
hardlink, mode/nlink drift와 read 도중 pathname 교체도 fail-closed한다.

현재 봉인 결과:

- generation:
  `29f81cbfca87530dd1a825fb8f1bff407ca91b0a158733919cd9f2e165bcbc2b`
- report SHA-256:
  `e352d3e8bcd7b948b244ec9e574757689d72dcf7e9d05fdb42bef616c62e32f9`
- receipt SHA-256:
  `f836b0b0e5f70293fbe1ffe8ff8b05e4f8b5f33b62250840be87c4a024146f98`
- commit SHA-256:
  `d418a0853796a9f45486b798b7703218f41e77ccbf88cab1e3bd05345d2167b5`
- config/namespace SHA-256:
  `273ed1269680c3433d3b5209f2b75da4d691f69ddb23da747d3856301d6caa25`
- implementation module SHA-256:
  `619562e6ff65176cc86512beb39f02bfed45f74cc0c433fb28e923283bc14a51`
- unit test SHA-256:
  `ccfa5ab64be82bfc75faea2f0182f6e0f821096fda10c674025ad4f30d81b04b`
- generation/report/receipt/commit/namespace-lock/output-lock mode:
  `0550/0440/0440/0440/0600/0600`
- generation/report/receipt/commit/namespace-lock/output-lock `st_nlink`:
  `2/1/1/1/1/1`
- 반복 audit: 동일 generation 재사용, generation 수 `10` 유지
- `--validate-commit-only`: `valid`

## 6. 실행 명령

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_p04_p05_gap.py --validate-config-only

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_p04_p05_gap.py

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_p04_p05_gap.py \
  --validate-commit-only \
  --commit artifacts/reproducibility/p04_p05_gap_validation/commit.json
```

## 7. 검증 결과

- focused unit/integration: `34 passed`
- cohort SVG·manuscript evidence·publication audit 회귀 포함:
  `110 passed`
- Ruff check: 통과
- Ruff format-check: 통과
- mypy production module+CLI: 통과
- mypy unit/integration:
  `--follow-untyped-imports --ignore-missing-imports` 조건에서 통과
- config-only: 통과, source/output 접근 `0`
- 실제 aggregate audit: 통과
- commit-only deep validation: 통과
- 반복 collision/reuse audit: 통과, 새 중복 generation `0`

변이 테스트는 다음 drift를 fail-closed로 확인한다.

- 40행·domain·A/B/C count 변경
- source 또는 tracker full hash 변경
- PENDING 18개 또는 marker hash 변경
- locked access 16개 중 하나라도 `true`
- 외부 전용 task를 완료로 오기
- B07 completed lineage/metric 또는 원고·claim-gap omission drift
- B07 commit의 aggregate/paired artifact record 결박 drift
- resolved C `7` anchor가 stale `9`로 되돌아가는 변경
- license evidence alias의 exact ledger path/hash drift
- 원고의 exact license ledger path mention 누락
- `.env.local` 같은 환경 파일 변형을 source로 선언
- commit JSON의 중복 key
- 계약 밖 output root로 publish 시도
- publish 뒤 report byte 변조
- alternate config path와 namespace binding 위조
- 두 concurrent publisher의 source/report build가 겹치지 않는 단일 writer
  직렬화
- 첫 writer가 lock을 잡은 뒤 output root inode를 rename·재생성해도
  두 publisher의 generation publish가 겹치지 않으며, 첫 writer는
  `output_root_rebound`로 중단되고 displaced root에는 새 commit이 남지
  않음
- post-publish deep 시점까지 exclusive lock 유지
- generation/report/receipt/commit exact mode·nlink drift
- report/receipt/commit hardlink, receipt symlink와 read 도중 pathname 교체
- 기존 generation collision의 report hardlink
- post-commit deep 실패 시 no-prior commit 제거·parent fsync
- post-commit deep 실패 시 이전 valid commit atomic restore

## 8. C/H/M 자체감사

- **Critical**: PENDING/locked 해제, raw/restricted/secret 접근, canonical
  원고·evidence·gap·tracker 수정, P04/P05 false-complete — 미해결 `0`
- **High**: source hash/count/split drift 수용, B07 lineage 결박 누락,
  mixed-resolved finding/resolution set 누락, C `7`/stale `9` guard 또는
  license path/hash/mention 결박 누락, alternate config/output namespace,
  concurrent last-writer, output-root inode 재바인딩에 의한 lock 분리,
  commit/report/receipt 위조, post-deep 실패 뒤 invalid commit 노출 —
  미해결 `0`
- **Medium**: duplicate YAML/JSON key, traversal/symlink, TOCTOU,
  hardlink·pathname 교체 race, exact mode/nlink 위반,
  non-content-addressed/비원자 publish, 절대경로 노출 — 미해결 `0`

Canonical 문서가 검토 후 갱신되면 기존 pin은 의도대로 실패한다. 새
revision의 source hash와 기대 semantic finding을 review한 뒤 config를
갱신하고 이 감사를 다시 수행해야 한다.

## 9. Generation 보존 ledger

기존 9개 generation은 삭제·chmod·내용 수정하지 않았다. 열
generation 모두 directory의 `generation_id`와 report 내부 ID가
일치하고, exact tree는 `receipt.json`, `report.json` 두 파일이다.
Directory는 `0550`·`st_nlink=2`, 두 파일은 `0440`·`st_nlink=1`이다.

| generation | config SHA-256 | namespace | 현재 상태 |
|---|---|---|---|
| `314864865df981b2233b535ee4b9a9991a51e81c60b82b69d2c1f0f94f3b17d9` | `492e531add9ea0541b893f7263d48453bdec2de3a14ad9ff65fadf88b7858d6b` | 없음 | historical development receipt; ID/tree/mode/nlink 유효, 현행 config/source·namespace deep 기준 비정본 |
| `73d936d689a1ebc16cbff71c04bd8e685a77ee3aebb52d966fbb770c315586ef` | `26a70183be91e9245cae2ff974b588000fcbb86aaf733121a09280c725ac0ef8` | 없음 | historical development receipt; ID/tree/mode/nlink 유효, 현행 config/source·namespace deep 기준 비정본 |
| `afc5464aa8f0d7291c84c75ceea7b318400055599eea6161860c7626c1c3e41d` | `73591c7ceea66968067cba7fbbf8c333c678d878e2f747aa05bb74e3753b0769` | 없음 | historical development receipt; ID/tree/mode/nlink 유효, 현행 config/source·namespace deep 기준 비정본 |
| `c117744c1058e100b68530aa7f50a7efde4c46b81b39d85b58d25bd8c557909b` | `60c6611e4f56329fd18b867dbf0d5122130b523c1677a783c6df4baa3d2c58dd` | 없음 | historical development receipt; ID/tree/mode/nlink 유효, 현행 config/source·namespace deep 기준 비정본 |
| `cb07782002e704159b989adddeced4fb7af578fdcd0a4cecaea9b46f0cc4f6df` | `fdbfa5ce0dc1b5b180b25ffce682a5ef064c9ab652b52a31ab23e1452e5721ba` | 없음 | prior production-static receipt; ID/tree/mode/nlink 유효, 현행 implementation pin·namespace deep 기준 비정본 |
| `d20ad194db9e1556a45e50a3fbebc133d3880d9fe46c9251d15d7e1b491455f9` | `3a6bdc3e6a3ed064dfa6dd19a5b3c4055dc6b76269a9e120028232fe5f4d0ccd` | 없음 | historical development receipt; ID/tree/mode/nlink 유효, 현행 config/source·namespace deep 기준 비정본 |
| `b10b206bd08590cba857cb2ccffd25e134d7325ff6a1becf6c5fb0d5c677e1c1` | `e17dab5d604a20ace8ec425f8f3102fd0a72e6e071f193bbeae7f0add1951981` | exact | prior publication-hardening receipt; 현행 implementation pin 기준 비정본 |
| `47d7660e108a7bdd256f1e62f270149e429573d65dcb049619f53f46ec5f37f2` | `159ac15500891600e5586abf653d29577dbf24c9705c151ab7983ab15f4a145b` | exact | output-root rebind 방어의 pre-format receipt; 현행 implementation pin 기준 비정본 |
| `31716def3839e5eb219dbbedeafab87953bc4e541f6c22ab0c16e3afb53707fa` | `141cdee03b2d0b90db4d5b972839e03a8adb9a7cbdebc8d861d4d2193fff5ce9` | exact | prior rebind-hardening receipt; canonical semantic integration 전 revision |
| `29f81cbfca87530dd1a825fb8f1bff407ca91b0a158733919cd9f2e165bcbc2b` | `273ed1269680c3433d3b5209f2b75da4d691f69ddb23da747d3856301d6caa25` | exact | **현재 canonical commit 대상**, mixed-resolved·rebind 회귀·offline audit·repeat reuse·deep validation 통과 |

`commit.json`은 마지막 행만 가리킨다. Prior generation은 감사 이력이며
현재 canonical visibility나 현행 deep-pass 주장으로 사용하지 않는다.

## 10. 고정 해시

| 대상 | SHA-256 |
|---|---|
| config | `273ed1269680c3433d3b5209f2b75da4d691f69ddb23da747d3856301d6caa25` |
| module | `619562e6ff65176cc86512beb39f02bfed45f74cc0c433fb28e923283bc14a51` |
| CLI | `b7f2d51f58c701d9617f52ebba2358ed4011fa3d1a1c3c67b679bddfad57b0dc` |
| unit test | `ccfa5ab64be82bfc75faea2f0182f6e0f821096fda10c674025ad4f30d81b04b` |
| integration test | `9fb50da75f7df908ed3b4adfbbd7b6bf2891106eb1f1a22454401aa690d190bc` |
| current report | `e352d3e8bcd7b948b244ec9e574757689d72dcf7e9d05fdb42bef616c62e32f9` |
| current receipt | `f836b0b0e5f70293fbe1ffe8ff8b05e4f8b5f33b62250840be87c4a024146f98` |
| current commit | `d418a0853796a9f45486b798b7703218f41e77ccbf88cab1e3bd05345d2167b5` |

감사 전후 canonical 핵심 source SHA-256:

- tracker:
  `7edddb0ad54fcf81ff36a12dd2fe878e9fabd02709444b4f5405203138dd4e20`
- methods/ethics gap:
  `50847eb7eea13f5819a07d1aea3202ae40b2f528a9ea8c07d6d3661e6879a90c`
- claim-gap:
  `2168233b459bbe0b2aac411de94d57720408be2479c96e589b14388ff3d5d7a1`
- manuscript:
  `dea994728ae8c73be098a29e9a70af2ab4730a651063e64687b40c07433b17eb`
- evidence-map:
  `1a4d897f3872a3a685be73667ac1e5676cd085711420f790e950f1b7af9e28be`
- manuscript evidence validation 문서/report:
  `c40075548b70eac32efed6fe012db1ae8435652532858a0c49829ab9c07ee051`,
  `026799ab9ceb3d93128cc7c4558dcbe8d5ed4f0be3afefe19db198bb5e48b7bd`
- license ledger/matrix:
  `b6ceeb1f874de3071462e98c3526136302ab54d3ea9ecc109400d1a3f03f7cd4`,
  `3257b0dcdb511b79576ffd99329bcde0754e477b6215b5ae81f2c609684dcf58`

## 11. 다음 실행 순서

1. S104·S105·B07 amendment candidate 경로로 B07의 dev-only 결과와 한계를
   원고/evidence-map review candidate에 반영한다.
2. B07 review candidate가 실제 canonical revision으로 승인될 경우에도
   dev-only·oracle·silver·no-promotion 한계와 license `PENDING`을
   유지한다.
3. 사람의 diff 검토와 명시적 적용 뒤 manuscript evidence validator,
   publication audit, 본 P04/P05 감사를 새 pin으로 순서대로 재실행한다.
4. 남은 C 7개, 외부 사람 평가, one-shot locked 평가와 최종 PDF audit가
   완료될 때까지 P04/P05 상태를 `진행 중`으로 유지한다.
