# 논문 publication lint·receipt 정적 준비도

기준 시각: 2026-07-29 KST  
현행 판정: **v2 오프라인 publication lint·봉인 publication boundary 통과 /
논문 완료·제출·수동 승인 아님**

## 0. 현행 v2 판정

이 문서의 현행 근거는 v2다. 아래 v1 수치·generation·고정 해시는 당시
snapshot을 보존하기 위한 historical ledger이며, 현행 보안·준비도 통과
근거로 재사용하지 않는다. v1 config, commit, receipt와 5개 generation의
파일 manifest는 v2 구현·검증 전후 동일했다.

v2는 다음 namespace를 새로 사용한다.

| 역할 | 경로 |
|---|---|
| 정적 계약 | `configs/paper/publication_v2.yaml` |
| descriptor/openat 공통 primitive | `src/voice_coach/reproducibility/protected_paths.py` |
| 감사·publication·deep validator | `src/voice_coach/reproducibility/paper_publication_v2.py` |
| 오프라인 CLI | `scripts/audit_paper_publication_v2.py` |
| adversarial 테스트 | `tests/unit/test_paper_publication_v2.py` |
| stable root namespace lock | `.paper_publication_v2.publish.lock` |
| output-local lock | `artifacts/reproducibility/paper_publication_v2/.publish-v2.lock` |
| visibility commit | `artifacts/reproducibility/paper_publication_v2/commit-v2.json` |
| content-addressed receipt | `artifacts/reproducibility/paper_publication_v2/generations/74b2da59330ab27b4b8fa78f1345a8bc63279d0800bd81c822635f7952fabde6/publication-audit-v2.json` |

Config, receipt, commit은 다음 completion state를 exact type/value로
공유한다.

```yaml
paper_complete: false
submission_status: not_submitted
manual_approval_required: true
manual_approval_status: not_approved
```

`False/True` 대신 `0/1`을 넣는 것도 거부한다. 이 authoritative state는
runtime에서 변경할 수 없는 mapping으로 유지한다. 따라서 v2 정적 audit
통과는 논문 완료, 제출 또는 사람 승인을 의미하지 않는다.

Locked-evaluation gate는 report에서 관측된 정확히 16개 key를 sorted
unique set으로 고정한다. 정렬 key를 trailing LF 없이 `\n`으로 결합한
SHA-256은
`de011d5deb430036e293b3b0b16593508cc22f200a30cdcb314c4996184d2f11`
이며, config/report/receipt/commit에서 count `16`, 같은 digest, 모든
access flag `false`, locked file read `0`을 확인한다. Key 삭제·이름 변경·
추가와 bool/int alias를 모두 fail-closed한다.

경로 정책은 component를 separator boundary로 판정한다. `raw`,
`locked`, `restricted`, `env`, `environment`, `secret`, `credential`
계열과 `.env*`를 거부하지만 `drawer`, `secretary`, `credentialed`,
`unlocked`는 허용한다. 모든 source/output traversal은 held workspace
root dirfd에서 component별 `openat`과 `O_DIRECTORY|O_NOFOLLOW`를
사용한다. Source ancestor, workspace/output root, output descendant와
lock pathname의 inode 재결박을 검출한다. Platform이 필수 flag/dirfd
기능을 제공하지 않으면 fallback하지 않고 실행을 거부한다.

Generation, receipt, commit, atomic replace, rollback과 directory `fsync`는
held output dirfd 계보에서 수행한다. Atomic staging fd는 rename까지
열어 두고 staging name과 publish target이 같은 inode인지 전·후
검증한다. Post-commit deep validation 실패 시 같은 held fd에서 이전
deep-valid commit을 복원하거나, 이전 commit이 없으면 새 commit을
제거한다. Symlink/hardlink/wrong-mode/불가독 기존 commit은 부재로
간주하지 않고 overwrite 전에 거부한다.

Receipt의 `access_counters` 범위는
`single_deterministic_receipt_reconstruction` 한 번으로 명시한다.
해당 counter에서 privacy, protected-file no-read, network request,
canonical/output write 관측을 계산하며 수동 boolean을 허용하지 않는다.
실제 publication write는 별도 `current_publish_transaction` counter로
기록한다. Optional network observation도 exact counter, URL 순서,
expected status를 pinned base contract에 결박하고 canonical completion
evidence로 사용하지 않는다. 이번 production/deep 검증에서는 network를
호출하지 않았다.

현행 실행 결과:

- config-only: config read `1`, referenced/canonical/asset/protected/network/
  output `0`, output namespace 미생성
- v2 adversarial suite: `70 passed`
- v1 회귀와 합계: `99 passed`
- Ruff check/format: 통과
- mypy production/CLI/test: 통과
- production audit: `passed`
- repeat audit: 동일 generation 재사용, generation 수 `1`
- deep validate: `passed`
- receipt reconstruction: referenced source `16`, aggregate `9`, canonical
  `3`, local asset `4`
- raw/environment/secret/locked read `0/0/0/0`
- network request `0`, canonical write `0`
- generation/receipt/commit mode·nlink: `0550/0440/0440`, `2/1/1`
- 두 lock mode·nlink: `0600/1`
- source와 v1 publication SHA manifest: config-only→tests→production→deep
  전후 동일

현행 고정값:

| 대상 | SHA-256 |
|---|---|
| v2 config | `45539442ba5848ca8c81fe3eb3e9e77e7a6afab411dc7e3bf797221887171459` |
| v2 module | `d71a24ea721a7e1fbb876fbc0a3a2e11cf1278b5204480af4689df53152cae68` |
| protected path primitive | `c645417444aa69f7bb54e6804b224e84fc9adc5f25934563fa17d8c87862bbc4` |
| v2 CLI | `e14eba4b0e745214130e106bce0e74f245bb29e6f6185e9993e820e8e2def437` |
| v2 tests | `6f54d0976b7f584cb31fe05cd84b565cb3a6919eb9951fd735c753d9ff138053` |
| v2 commit | `52413afac00aeeaaf0982661f152841cc0b9df7e98d99747e203b460f41ea540` |
| v2 receipt/generation | `74b2da59330ab27b4b8fa78f1345a8bc63279d0800bd81c822635f7952fabde6` |

재현 명령:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication_v2.py --config-only

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication_v2.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication_v2.py --validate-receipt-only
```

공통 `protected_paths.py`는 다음 version의 P04 계열 validator에서도
재사용할 수 있다. 다만 현행 P04 v1 config/receipt를 변경하면 historical
pin이 무효화되므로 이번 작업에서는 P04 코드에 연결하지 않았다.

## 1. v1 historical snapshot 범위

> 이 절부터 10절까지의 “현재”, “통과”, “미해결 0” 표현은 v1 receipt가
> 생성된 당시 기록이다. 현행 판정은 위 0절의 v2 결과만 사용한다.

이 구현은 현재 실험 원고의 공개·집계 범위만 읽어 다음을 자동 감사한다.

- 기존 manuscript evidence validator source와 완료 report의 exact
  SHA-256·크기·`passed` 상태
- evidence report의 artifact `36/36`, claim `475/475`, PENDING `18`,
  locked-test guard와 registry metric split
- 본문 인용과 참고문헌 `1..11`의 정의·사용·순서·중복
- Markdown 표 13개의 구조와 열 수 일치
- 원고가 링크한 로컬 asset 4개의 exact path·SHA-256·크기
- PENDING marker 18개의 정렬 multiset SHA-256
- 관련연구 source ledger의 정본 URL 36개와 기대 HTTP 상태
  `200: 32`, `202: 4`

이 감사는 최종 PDF 렌더링, 실제 외부 URL 본문 재검토, citation-to-claim의
학술적 판단, S104 이후 결과 생성 또는 P05 완료 판정을 대신하지 않는다.
원고와 evidence-map을 수정하지 않으며 PENDING을 해제하지 않는다.

## 2. v1 historical 구현 산출물

| 역할 | 경로 |
|---|---|
| 정적 계약 | `configs/paper/publication_v1.yaml` |
| 감사·원자 publication·deep validator | `src/voice_coach/reproducibility/paper_publication.py` |
| CLI | `scripts/audit_paper_publication.py` |
| 단위·CLI·fault/recovery 테스트 | `tests/unit/test_paper_publication.py` |
| visibility commit | `artifacts/reproducibility/paper_publication/commit.json` |
| stable namespace lock | `artifacts/reproducibility/.paper_publication.publish.lock` |
| output-local lock | `artifacts/reproducibility/paper_publication/.publish.lock` |
| content-addressed receipt | `artifacts/reproducibility/paper_publication/generations/54eb24c8a1937f799cc4d8403fe6b27bd9e0ffd89dc85d723014bfdcca9d0498/publication-audit.json` |

Receipt의 파일 SHA-256 자체가 generation ID다. Commit은 receipt의
path·SHA-256·크기, canonical config 경로·해시와 output namespace를
결합하고 자체 `binding_sha256`을 가진다. Generation은 정확히 receipt 한
파일만 허용하며 directory `0550`, receipt `0440`·`st_nlink=1`로 봉인한다.
Commit도 `0440`·`st_nlink=1`이다. 두 고정 lock은 모두 `0600`·
`st_nlink=1`이다. Stable parent namespace lock을 먼저 얻고, 그 안에서
output directory descriptor와 output-local lock을 획득한다. Source audit
시작부터 commit 이후 deep validation 종료까지 두 exclusive `flock`과
held output dirfd를 유지한다.

모든 deep/collision read는 path `lstat` 전·후와 열린 descriptor의
device/inode/mode/nlink/size/mtime/ctime을 비교한다. Symlink, hardlink,
경로 교체 race를 거부한다. 모든 generation/receipt/commit publish·read·
rollback은 canonical 문자열 경로가 아니라 held output dirfd 아래에서
수행한다. Output root를 rename한 뒤 같은 canonical 경로를 재생성하는
공격은 stable namespace lock으로 직렬화되고 기존 transaction은
`output_root_rebound`로 fail-closed한다. Post-commit deep validation이
실패하면 직전에 deep-valid였던 commit byte를 atomic restore하고, 이전
valid commit이 없으면 새 commit만 unlink한 뒤 held parent directory를
`fsync`한다. 봉인된 generation은 삭제하지 않아 재현·감사 이력을
보존한다.

## 3. Fail-closed 실행 순서

1. config schema만 검사하는 `--config-only`에서는 referenced source,
   canonical manuscript/evidence-map, local asset, network, output 접근을
   모두 0으로 유지한다.
2. production audit의 첫 referenced source로 기존
   `manuscript_evidence_validation.json`을 연다.
3. report exact hash·크기와 `passed`, `36/36`, `475/475`, PENDING
   `18`, locked access 16개 전부 `false`를 확인한다.
4. 기존 validator script/module과 validation 문서의 exact pin을 확인한다.
5. 그 뒤에만 canonical manuscript/evidence-map/source ledger를 읽는다.
6. 인용, 13개 표, 로컬 asset, PENDING과 offline URL 계약을 검증한다.
7. canonical config path
   `configs/paper/publication_v1.yaml`, config SHA-256, output root·파일명을
   하나의 namespace로 결속한다.
8. Stable parent namespace lock과 output-local lock에서 순서대로 exclusive
   `flock`을 얻고 output dirfd identity를 고정한다. Config를 재검증하고,
   두 lock과 dirfd를 유지한 채 canonical source와 구현 source를 모두
   다시 해시한다.
9. content-addressed receipt를 새 generation에 쓰고 directory `0550`,
   receipt `0440`·single-link exact tree로 봉인한다.
10. 이전 commit이 현재 source에 대해 deep-valid인지 확인한 뒤 `0440`
    atomic commit을 publish한다.
11. 같은 두 exclusive lock 안에서 generation tree, namespace, commit
    binding, receipt hash, 현재 source의 완전 재구성을 byte-for-byte로
    재검증한다. 실패하면 이전 valid commit restore 또는 새 commit 제거를
    수행하고 parent directory를 `fsync`한다.

경로는 workspace-relative regular file만 허용한다. 절대경로, traversal,
symlink와 `.env*`, `raw`, `restricted`, `secret`, `credential` 경로
component를 거부한다. Alternate config 경로·output namespace는 동일
내용이어도 거부한다.

## 4. v1 historical 실제 실행 결과

### Config-only

- 상태: `valid_config_only`
- source record schema: `13`
- referenced/canonical/local asset/raw/environment/secret/locked 파일 read:
  모두 `0`
- network request: `0`
- output write: `0`

### Offline publication audit

- 상태: `passed`
- scope: `publication_static_audit_only`
- reference definition/use: `11/11`
- undefined/unused/duplicate reference: `0/0/0`
- Markdown table: `13`
- local asset: `4`
- PENDING marker: `18`
- PENDING multiset SHA-256:
  `1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673`
- locked access check: `16`, 모두 `false`
- locked-test file read: `0`
- source-ledger URL: `36`
- manuscript external URL: `14`, 모두 ledger 부분집합
- network request: `0`
- raw/environment/secret file read: `0/0/0`
- canonical manuscript/evidence-map 수정: `false`
- generation ID:
  `54eb24c8a1937f799cc4d8403fe6b27bd9e0ffd89dc85d723014bfdcca9d0498`
- publication namespace config SHA-256:
  `a99500c9f3cdd0abea45778e5e19b0faa753d4cb1418d7f6ec1b24291f7c0eeb`
- generation directory/receipt/commit/output-lock/namespace-lock mode:
  `0550/0440/0440/0600/0600`
- receipt/commit/output-lock/namespace-lock `st_nlink`: 모두 `1`
- generation directory `st_nlink`: `2`
- generation exact tree: `publication-audit.json` 한 파일
- 반복 audit: 동일 generation 재사용, generation 수 `5` 유지
- deep `--validate-receipt-only`: `passed`

첫 인용 순서는 현재 원고의
`1,2,3,4,5,6,7,8,10,11,9`를 exact contract로 기록한다. 참고문헌 정의
순서는 계속 정확히 `1..11`이다. 최종 출판 style에서 첫 등장 순 번호를
요구하면 bibliography 재번호화와 원고 전체 인용 갱신을 별도 review
candidate로 수행해야 하며, 이 감사가 정본을 자동 변경하지 않는다.

## 5. Network observation 분리

기본 실행은 network를 전혀 호출하지 않는다. `--observe-network`는 명시적
선택이며 다음을 강제한다.

- 별도 `voice_coach.paper_publication_network_observation.v1` schema
- UTC timestamp
- `status=observation_only`
- `canonical_completion_evidence=false`
- 별도
  `artifacts/reproducibility/paper_publication/network_observations/`
  경로

따라서 HTTP 성공·실패는 canonical audit의 통과 조건이나 연구 결과 완료
근거가 아니다.

## 6. v1 historical 실행 명령

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication.py --config-only

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication.py

PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/audit_paper_publication.py --validate-receipt-only
```

Network observation은 기본 검증 명령에 포함하지 않는다.

## 7. v1 historical 검증

- publication focused unit/CLI/fault/recovery: `29 passed`
- manuscript evidence validator·cohort SVG 회귀와 합계: `77 passed`
- P04/P05 semantic validator까지 포함한 관련 회귀: `111 passed`
- config-only zero-output 전·후 current commit/5개 generation hash·mode·
  nlink 동일
- production offline audit: `passed`
- 반복 collision/reuse audit: `passed`, 새 중복 generation `0`
- deep `--validate-receipt-only`: `passed`
- Ruff check/format check: 통과
- mypy production module+CLI: 통과
- mypy test:
  `--follow-untyped-imports --ignore-missing-imports` 조건에서 통과

테스트는 다음 실패를 명시적으로 거부한다.

- evidence report가 `passed`가 아니거나 첫 source가 아님
- undefined/unused/duplicate·순서가 다른 citation/reference
- malformed Markdown table 또는 table 수 drift
- local asset hash drift
- PENDING 제거·변경
- locked access flag `true`
- source-ledger URL set drift
- forbidden restricted/secret 경로
- generation extra file, receipt byte tamper
- receipt/generation/commit mode drift
- receipt/commit hardlink, receipt symlink, read 도중 경로 교체 race
- alternate config path와 commit namespace 위조
- post-publish deep validation 시점까지 두 exclusive lock과 held dirfd 유지
- output root inode rename/recreate 공격의 single-writer lock 분할
- commit publish fault와 receipt publish 직후 fault
- post-commit deep failure 시 no-prior commit 제거·parent fsync
- post-commit deep failure 시 이전 valid commit atomic restore

Fault 경로는 canonical commit이 없으면 새 visibility commit만 제거하고,
이전 deep-valid commit이 있으면 byte-for-byte 복원한다. 두 경우 모두
봉인 generation을 보존해 다음 실행에서 정확히 재사용한다.

## 8. v1 당시 C/H/M 자체감사

감사 범위는 publication-static source와 aggregate receipt다.

- **Critical**: PENDING/locked 해제, raw/restricted/env/secret 접근,
  canonical manuscript/evidence-map 수정, network 결과를 completion
  evidence로 사용 — 미해결 `0`
- **High**: evidence report보다 먼저 canonical source 접근, hash/status
  drift 수용, citation/asset/URL 누락, commit/receipt binding 위조,
  alternate config/output namespace, concurrent last-writer, output-root
  inode 재결박, post-commit deep 실패 뒤 invalid commit 노출, generation
  collision — 미해결 `0`
- **Medium**: duplicate YAML/JSON key, non-finite 값, traversal/symlink,
  hardlink·경로 교체 race, source TOCTOU, table 구조 drift, exact
  mode/nlink 위반, owner-writable generation, 중간 publish 장애 복구 실패
  — 미해결 `0`

실제 후속 결과가 원고에 반영되거나 publication asset/PDF renderer가
변경되면 해당 새 revision에 대해 C/H/M을 다시 수행해야 한다.

## 9. v1 generation 보존 ledger

기존 4개 generation은 삭제·chmod·내용 수정하지 않았다. 다섯 generation
모두 directory 이름과 receipt SHA-256이 일치하고 exact tree가 receipt
한 파일이며, receipt는 `0440`·`st_nlink=1`이다. 현재 hardened validator는
canonical commit이 가리키는 generation만 현재 config/source와
byte-for-byte 재구성한다.

| generation | config SHA-256 | dir mode | namespace | 현재 상태 |
|---|---|---:|---|---|
| `907b16398d31fd88c3c6ffaaa4b6086d67af975f0b9bdc7c191feed1504571c0` | `71105bc82b9529a4087624048b28f6a164c488f45a1fc885038bb5a94fbe729a` | `0750` | 없음 | historical development receipt; self-address/tree/file 검증 통과, 현행 exact dir mode·namespace·current source deep 기준 비정본 |
| `907911007aeed314b0b359b38b236e9fdd85eaec96d8d6822fd27554bd892e89` | `bcccd6afd99154d57e9dda1786338d1198948f05512d1916759718b0b6946d58` | `0750` | 없음 | historical development receipt; self-address/tree/file 검증 통과, 현행 exact dir mode·namespace·current source deep 기준 비정본 |
| `2f250211ecc1722d489efd1be396d644ea844515603a151c9cab555be7bce93e` | `ecfb89531a03100e589d38e151b055b8f4b94b7f33eca14a19a7047aa44e0ddc` | `0750` | 없음 | prior production-static receipt; self-address/tree/file 검증 통과, 현행 exact dir mode·namespace·current implementation pin 기준 비정본 |
| `6b2191809102d485480803a4ddb3cc17800cf89ead4ec998a70d092ec2c9e197` | `fb7cb8e77ad553f9ecfe5109a0cbb33e1a2f1f31eef2e60fc676e4a03e698ac5` | `0550` | exact | prior hardened receipt; self-address/tree/file 검증 통과, 이전 canonical source·implementation pin에 결박 |
| `54eb24c8a1937f799cc4d8403fe6b27bd9e0ffd89dc85d723014bfdcca9d0498` | `a99500c9f3cdd0abea45778e5e19b0faa753d4cb1418d7f6ec1b24291f7c0eeb` | `0550` | exact | v1 namespace의 마지막 commit 대상; v2 현행 판정과 분리 |

v1 `commit.json`은 마지막 행만 가리킨다. Prior generation은 감사
이력이며 v2 canonical visibility나 현행 deep-pass 주장으로 사용하지
않는다.

## 10. v1 historical 고정 해시

| 대상 | SHA-256 |
|---|---|
| config | `a99500c9f3cdd0abea45778e5e19b0faa753d4cb1418d7f6ec1b24291f7c0eeb` |
| module | `dde43c6f6a8788bc7b85b79e838de569494a7415efc61f87ba93f3cef5612f0d` |
| CLI | `037e14d1c69a74c488f805aedc488aa17f9d9068d87e4a9439e7e62467811956` |
| tests | `5048197f2c137d39c348b3846f4112834b69c2754e5533905862c0d3255c1023` |
| commit | `366b6267ed84c63ef12ddb3b511c3a9df970cd4801825cbbef69bf45049d1d2d` |
| receipt | `54eb24c8a1937f799cc4d8403fe6b27bd9e0ffd89dc85d723014bfdcca9d0498` |
| cohort SVG | `d697e42cea9a820c24c082dd183653bc0a59a9ba4e3406c85eafb8117437ce9e` |

Canonical 원고/evidence-map SHA-256은 각각
`dea994728ae8c73be098a29e9a70af2ab4730a651063e64687b40c07433b17eb`,
`1a4d897f3872a3a685be73667ac1e5676cd085711420f790e950f1b7af9e28be`로
감사 전과 같다. `docs/RESEARCH_COMPLETION_TRACKER_KO.md`도 수정하지
않았으며 SHA-256은
`7edddb0ad54fcf81ff36a12dd2fe878e9fabd02709444b4f5405203138dd4e20`이다.

## 11. 남은 실제 blocker

이 정적 통과는 다음을 완료하지 않는다.

- S104–S108 actual 결과와 최종 S1/calibration figure
- S2/E01 actual 결과
- G04/D07 뒤 B05 one-shot locked 평가
- C03 200건, R04 blind 평가
- H00 승인·동의·모집과 H04
- 최종 PDF build/postflight와 사람의 시각 감사

따라서 P04/P05, 최종 PDF와 논문 전체 상태는 계속 `진행 중`이다.
