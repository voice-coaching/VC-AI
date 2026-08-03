# E01 duration matched-control v2 합성 기술 gate

상태: **합성 matched-control 기술 gate 완료 / 논문·production readiness NO**

기준일: 2026-07-29 KST

## 1. 목적과 범위

이 gate는 duration treatment의 변화량에서 PSOLA 자체 재합성 collateral을
숨기지 않도록 `k=1` identity-PSOLA control을 새 계약으로 추가한다. 기존
`e01_duration_transform.py`와 v1 worker/runner/config/test/doc은 수정하거나
import하지 않았다. v2는 별도 primitive, worker, pair orchestrator, runner,
config, test, 영수증으로 격리된다.

두 역할은 다음과 같다.

- `identity_psola_control`: `k=1.0`; six-point DurationTier 값은 모두 정확히
  `1.0`; tier area는 원 입력 duration `D`; theoretical/actual sample count는
  모두 `N`.
- `duration_treatment`: `k=1.2`; v1과 같은
  `p=(kL-t)/(L-t)=1.2222222222222223` plateau 및 exact continuous-area
  의미를 유지한다.

두 역할 모두
`six_point_duration_tier_replace_seeded_overlap_add_no_fast_path_v2` 경로에서
동일하게 `To Manipulation` → 실제 PitchTier/PointProcess 추출 → 빈
DurationTier에 6점 삽입 → `Replace duration tier` → seed 설정 →
`Get resynthesis (overlap-add)` 1회 → `finally`의 safe RNG reset을 실행한다.
identity에는 PCM 복사 fast-path가 없다.

입력은 16 kHz, 1초, 180 Hz sine에 seed `777`의 PCG64 noise 구간
`[4000, 5200)`, `[10400, 11800)`을 넣은 voiced/unvoiced 혼합 합성
waveform이다. 실제 한국어 음성, corpus, ID, network, GPU, locked split,
S203은 사용하지 않는다.

## 2. pair 실행과 비교 해석

`identity_psola_control`과 `duration_treatment`은 각각 별도의 fresh
acoustic Python process에서 정확히 한 job만 실행한다. 두 worker는 같은
effective input PCM digest
`f77c093ebb0af98acd35d0ff75490ece825c31d27970bd518f0e12faf6fa2eec`,
Praat seed `5489`, source/config/runtime pin을 사용한다.

같은 seed는 **paired incremental estimate만** 지원한다. duration 변화로
voiceless interval의 RNG draw count가 달라질 수 있으므로 두 역할의 RNG
draw sequence가 같다고 가정하지 않는다.

세 비교는 모두 PCM에서 직접 계산한다.

- raw → identity: PSOLA reconstruction collateral
- identity → treatment: paired incremental estimate
- raw → treatment: 전체 변화

identity 결과를 raw→treatment metric에서 빼는 control subtraction은 하지
않는다. 길이가 다른 비교의 MAE/RMSE/correlation은 공통 prefix에서
계산하며 sample-count delta를 별도로 보고한다. 따라서 아래 값은 합성
diagnostic이지 time-warp-aware perceptual score가 아니다.

## 3. 실행·보안 경계

이 receipt의 threat model은
`trusted_local_host_and_venv_no_hostile_same_user_race`다. 외부 trusted
launcher가 시작한 signed attestation이 아니며, 최초 runner startup bytes,
scorer/acoustic Python의 전체 startup import closure, NumPy 설치 내용,
전체 OS image는 attestation하지 않았다. NumPy는 version만 검증한다.
따라서 hostile same-user가 runner 실행 전후 path를 교체하거나 venv에
same-version package, `sitecustomize`, `.pth`를 삽입하는 공격에 대한
resistance는 주장하지 않는다. machine-readable `trust_boundary`도 이
미검증 항목을 모두 `false`로 고정한다.

runner는 canonical config와 새 source 네 개를 `O_NOFOLLOW` descriptor로
읽고 SHA-256을 검증한다. application과 primitive는 검증한 source bytes만
custom loader로 실행하며 package `__init__`를 우회한다. Python, worker,
Parselmouth extension과 libseccomp는 검증 bytes의 write-sealed memfd에서
실행 또는 로드된다. 이 selected-artifact 검증은 위 trusted host/venv
가정 이후의 경계이며 전체 import closure provenance가 아니다.

각 child의 Python exec 전에 pinned libseccomp filter를 설치한다. worker는
seccomp mode `2`를 확인하고 native socket negative probe가 `EPERM`인지
검증하며, Python IP socket 경계도 차단한다. 각 worker 결과의 확인값은
mode `2`, native block `1`, Python intercepted operation `0`이다.

두 합성 output PCM은 direct pair 비교를 위해 parent memory로만
base64 little-endian float64 IPC된다. parent가 length/digest/finite 값을
검증한 직후 scalar metric과 digest만 남기고 pair receipt 작성 전에 PCM
field를 제거한다. 영수증에는 raw/output PCM, base64 payload, path,
speaker/utterance ID가 없다. 포함 가능한 worker receipt의 nested key는
recursive exact allowlist로 제한하며, 모든 numeric scalar는 정확한
`int`/유한 `float` type과 계약상 값·범위를 검증한다. 따라서 문자열 경로,
`bool`의 정수 위장, `NaN`/`Inf`, 음수 오차는 공개 전에 거부된다.

pair receipt store는 run 시작 전에 workspace root device/inode lineage를
고정한다. symlink를 따르지 않으며 staging write/fsync와 hard-link
no-overwrite로 원자 공개한다. 기존 receipt는 exact bytes, mode `0600`,
link count `1`일 때만 재사용한다. trusted runner가 시작된 뒤 검증한
descriptor/config 경계 안의 source/IPC mismatch, role/k mismatch, 두 번째
worker partial failure, symlink parent, hardlinked/tampered receipt는 fail
closed 한다. hostile same-user pre-start/race tamper는 이 문장의 범위가
아니다. 공개 오류는 고정 failure code만 사용한다.

publication staging 이름은 content digest에 고정되고 `O_EXCL`을 사용한다.
강제 종료로 staging residue가 남으면 이후 실행은 무결성을 우회하지 않고
계속 fail closed 하며, 자동 crash recovery는 구현하지 않았다. 이는
availability limitation이지 기존 receipt를 덮어쓰는 경로가 아니다.

## 4. exact provenance

현재 config SHA-256은
`0b613674c32ff41e51a0ea870d404a0d9cc2d962e9323b940b9dc5cd8e1b8743`다.

| 항목 | SHA-256 |
|---|---|
| runner | `73403b50069dd88eb692e188a7d1b0db707bf0cf9a4a3b8725d010f312419ec6` |
| worker | `d51d6e2c6098b14d260590f1d96ba401ddbf052754110c7c92ae6042f75c708a` |
| pair application | `b7ce89689672773782ea7e441a1a54252648131840baa2d49a341e8375b35a72` |
| matched-control primitive | `7ad3bd38622d68c2cb069cb0ca5e5b18d863fc893f5fd1e6c0e23b927735acc9` |

selected runtime pin은 v1 reviewed environment와 동일하다. 아래 목록은
선택된 artifact/version 증거이며 전체 Python import closure나 OS
attestation이 아니다.

- CPython `3.12.3`, Linux aarch64, glibc `2.39`
- NumPy `2.5.1`
- praat-parselmouth `0.4.7`, embedded Praat `6.1.38`
- reviewed wheel SHA-256
  `8589ea46113a899abbe77375ddacbb998c1f7e456c0584225dbadd60fdf175f1`
- installed extension SHA-256
  `81cfbf20dd0167f10bff5305dfe224a8fa41c2ab2bc122b5cbaa40069737e08b`
- libseccomp `2.5.5` SHA-256
  `3d128123b494ca9e88f3c237b0912397145af1754e86ea99f460af6857441f19`

## 5. 실제 pair receipt

현재 receipt:

`artifacts/phase1/e01_duration_matched_control_pair_v2_receipts/a379e154ea2c5c6b59e6df148f73a6c0033b93661315cc9b75f9ecb8e65d2786/receipt.json`

file SHA-256과 generation directory digest는 모두
`a379e154ea2c5c6b59e6df148f73a6c0033b93661315cc9b75f9ecb8e65d2786`다.
receipt mode는 `0600`, link count는 `1`; generation/output directory는
`0700`이다. 재실행 결과는 `receipt_created=false`,
`idempotent_reuse=true`이며 전후 byte hash가 같다.

| 측정 | identity | treatment |
|---|---:|---:|
| `k` / plateau | `1.0 / 1.0` | `1.2 / 1.2222222222222223` |
| tier area / error | `1.0 / 0.0 s` | `1.04 / 0.0 s` |
| input / theory / actual sample | `16000 / 16000 / 16000` | `16000 / 16640 / 16640` |
| DurationTier / PitchTier / PointProcess point | `6 / 82 / 150` | `6 / 82 / 150` |
| PSOLA call / duration error sample | `1 / 0` | `1 / 0` |
| output peak | `0.17003379830171492` | `0.1319382915195883` |

실제 source/final PitchTier digest는 두 역할 모두
`e1edd6c06863f59303b47213890e5e2303b21240e1e3219df1db1e17d13ee31d`,
source/final PointProcess digest는
`2ced49a0ed66d2a5580c43a2ac6a63c0fe7b13fb67587c17830b05a956349be5`로
replacement 전후 exact 일치한다.

| 직접 비교 | MAE | RMSE | max abs | correlation | sample delta |
|---|---:|---:|---:|---:|---:|
| raw → identity | `0.016284133482790605` | `0.03156137963864918` | `0.10087669101075357` | `0.8834328053862368` | `0` |
| identity → treatment | `0.06085151179427156` | `0.08369660546961881` | `0.2025365865344535` | `0.17172495747288205` | `640` |
| raw → treatment | `0.06947347767642881` | `0.09457616094095792` | `0.1836885786572916` | `-0.06215646120407424` | `640` |

identity worker receipt digest는
`a83526605508f343e6803ca38455a4ea178c45131b9be7656fb7445ebdaf7ca2`,
treatment worker receipt digest는
`351e151fbbcb2cd5d2c5e241ea0cdffa6c92728efeada7af66981035870dd616`로
pair receipt 안에 각각 결합돼 있다.

별도 fresh acoustic process에서 기존 public v1 primitive를 같은 합성
입력·`k=1.2`·seed로 실행한 PCM SHA-256도
`25d4c96b55dc56115aa7dd6cfd8b66adaeb3ec862944ec1d7b3f874d981e000f`로
v2 treatment PCM과 byte-exact 일치했다. 이는 treatment 경로가 v1
plateau/area뿐 아니라 현재 pinned runtime의 실제 waveform도 보존함을
확인한 **test-only regression**이다. v1 source hash와 이 비교 결과는 pair
receipt 자체에 결합되지 않았으므로 논문 결과 근거나 receipt-bound
provenance로 사용하지 않는다.

## 6. 검증

- 신규 primitive/worker/pair application/runner/test Ruff format/check: 통과
- 위 네 source mypy `--strict`: 통과
- 신규 test mypy `--strict --follow-untyped-imports`: 통과
- 신규 unit/adversarial/actual tests: `39 passed`
- 기존 v1 primitive `92` + v1 worker `21` 결합 회귀: 총 `152 passed`
- 실제 두 fresh worker pair 실행과 반복 byte-identical smoke: 통과

테스트는 identity/treatment role과 `k`, exact six-point values, 실제 backend
call count, no-fast-path, PitchTier/PointProcess mismatch, RNG failure의
`finally` reset, config/source/IPC tamper, partial second-worker failure,
symlink/hardlink/tampered publication, sanitized argv failure, native seccomp
proof, nested numeric scalar의 type/finite/range mutation, voiced/unvoiced 합성
fresh-process 반복성을 포함한다.

## 7. 남은 과학적 gate

이 결과는 `paper_ready=false`, `production_ready=false`다. 다음은 하지
않았다.

- 승인된 실제 한국어 발화와 alignment remap
- S1/S2 relation 평가
- F0/voicing, energy, spectrum, seam, content, naturalness, speaker similarity
  collateral 평가
- 여러 발화·화자·seed와 cross-platform 반복성
- time-warp-aware identity/treatment perceptual 비교
- 실제 한국어 E01 pass rate 또는 코칭 효과 주장

따라서 matched control의 실행 경로와 합성 direct-comparison 측정은
완성됐지만, 논문 성능 주장이나 실제 한국어 E01 실험 완료를 뜻하지 않는다.
