# E01 duration 합성 single-job worker 영수증

상태: **content-addressed 합성 기술 gate 완료 / 논문·production readiness NO**

기준일: 2026-07-29 KST

## 1. 이 gate가 확인한 것

`configs/experiments/e01_duration_synthetic_worker_v1.json`은 실제 음성이나
식별자를 입력으로 받지 않는 단 하나의 합성 duration job을 고정한다.
16 kHz, 1초의 180 Hz sine에 NumPy `PCG64`, seed `777`로 만든 내부 noise
구간 두 개를 넣고, 정렬 구간 `[0.2, 0.4]`초의 평균 duration factor를
`1.2`로 바꾼다. `k=1` matched control은 이 gate의 범위가 아니며 config와
validator가 모두 이를 금지한다.

worker는 프로세스마다 정확히 한 번만 primitive를 호출한다. Praat seed
`5489`, process isolation
`dedicated_single_job_subprocess`, RNG reset
`safe_unpredictable_finally_then_process_exit`, sample rounding `half_up`과
duration error allowance `0`은 모두 config의 명시적 값이다.

이 결과가 확인하는 범위는 다음뿐이다.

- 고정 합성 입력을 fresh acoustic-environment process에서 한 번 변환
- 선택된 runtime/source/binary의 exact pin과 digest 검증
- Python 실행 전 kernel seccomp 설치, mode `2` 확인, native socket
  negative probe `EPERM` 차단
- 파형을 저장하지 않고 aggregate와 digest만 영수증에 기록
- 같은 입력·source·runtime에서 byte-identical 영수증 재사용

## 2. verified bootstrap과 격리

실행 경계는 다음 순서로 fail-closed 동작한다.

1. scorer 환경의 얇은 runner가 canonical config를 읽는다.
2. runner, worker, application module, duration primitive의 SHA-256을
   `O_NOFOLLOW` descriptor read로 config pin과 대조한다.
3. application module은 project package `__init__`를 통하지 않고 검증된
   source bytes를 custom `importlib` loader로 직접 실행한다.
4. runner는 검증한 Python과 worker bytes를 각각 write-sealed memfd에
   복제하고 그 descriptor를 `/proc/self/fd/<fd>`로 실행한다. 원본
   owner-writable inode를 hash 뒤 다시 실행하지 않는다.
5. child의 Python exec 전에 exact `libseccomp 2.5.5` filter를 설치한다.
   socket 계열 syscall과 `io_uring_*` 우회를 `EPERM`으로 거부한다.
   retry나 두 번째 worker process는 없다.
6. worker는 reviewed wheel과 설치 extension을 no-follow descriptor로
   해시한다. Parselmouth extension도 검증 bytes를 write-sealed memfd로
   복제한 뒤 그 descriptor에서 로드한다.
7. duration primitive도 해시가 일치하는 source bytes만 custom
   `importlib` loader로 직접 로드한다. `voice_coach.evaluation.__init__`는
   실행하지 않는다.
8. kernel seccomp mode `2`를 확인하고 native `_socket.socket` negative
   probe가 `EPERM`인지 검사한다. 이어 Python 수준의
   bind/connect/connect_ex/sendto, create_connection,
   getaddrinfo, gethostbyname/ex/byaddr, getnameinfo를 차단한 상태에서 합성
   waveform 생성과 transform 한 건만 수행한다.

공개 실패 출력은 고정된 failure code만 포함한다. exception message,
절대경로, wheel 위치, PCM, speaker/utterance ID는 출력하지 않는다.

## 3. 선택된 exact provenance

현재 config SHA-256은
`4edf03ffb485e3dcf04d411f1d2ff61d6832f40f0abbb604637a1356b3982683`다.
config가 고정하고 실제 worker가 다시 검증한 환경은 다음과 같다.
이는 아래에 열거한 선택된 artifact의 exact provenance이며 NumPy의 모든
native dependency나 전체 OS image digest라는 뜻은 아니다.

| 항목 | exact 값 |
|---|---|
| Python | CPython `3.12.3`, cache tag `cpython-312` |
| Python build | `3.12.3 (main, Mar 23 2026, 19:04:32) [GCC 13.3.0]` |
| Python SOABI | `cpython-312-aarch64-linux-gnu` |
| base interpreter SHA-256 | `6242e0e8650d7dbdebbc25e08bf4c9359ddaf65f54fcae0e57fa99395fa5357a` |
| platform | Linux aarch64, glibc `2.39` |
| NumPy | `2.5.1` |
| praat-parselmouth | `0.4.7` |
| Praat | `6.1.38` |
| reviewed Parselmouth wheel SHA-256 | `8589ea46113a899abbe77375ddacbb998c1f7e456c0584225dbadd60fdf175f1` |
| installed extension SHA-256 | `81cfbf20dd0167f10bff5305dfe224a8fa41c2ab2bc122b5cbaa40069737e08b` |
| libseccomp | `2.5.5`, SHA-256 `3d128123b494ca9e88f3c237b0912397145af1754e86ea99f460af6857441f19` |

source pin은 다음과 같다.

| source | SHA-256 |
|---|---|
| runner | `61b6c1547243c8b28469b81897f31a38a1dfd7c146d87a89bdcd5075ffdb1684` |
| worker | `412f668381aed7982d1249c4d084d767bf298f09479ab6179d60b0e84cd7ae4f` |
| receipt application | `e2fd044567ef22ea1532f17939b812b406c2afefb4b608f7b54ca59027d47552` |
| duration primitive | `35fb6a480678b147cd798f1bf2687985c971297a71e5e443221c6fa1bef4c507` |

## 4. 실제 smoke 결과

현재 config/source pin과 일치하는 content-addressed 영수증은 다음이다.

`artifacts/phase1/e01_duration_synthetic_worker_receipts/9bea2805011efa8119d79dfe2a4e5b1a3cd9bca3eb0b87d9f4e648ded91e0145/receipt.json`

영수증 file SHA-256도 directory digest와 같은
`9bea2805011efa8119d79dfe2a4e5b1a3cd9bca3eb0b87d9f4e648ded91e0145`다.
file mode는 `0600`, link count는 `1`이고 generation과 output root는
`0700`이다.

| 측정 | 값 |
|---|---:|
| worker process / job | `1 / 1` |
| kernel seccomp mode / native probe blocked | `2 / 1` |
| Python guard intercepted operation | `0` |
| input / theoretical / output sample | `16000 / 16640 / 16640` |
| duration sample error | `0` |
| requested mean factor / tier plateau | `1.2 / 1.2222222222222223` |
| DurationTier area / error | `1.04 / 0.0 s` |
| DurationTier / PitchTier point | `6 / 82` |
| input / output peak | `0.1 / 0.1319382915195883` |
| synthetic input PCM digest | `f77c093ebb0af98acd35d0ff75490ece825c31d27970bd518f0e12faf6fa2eec` |
| output contract+waveform digest | `bce5a82c52a24f7fb5bdec78ec073a872052809d6daa7292458a188d13d2020c` |

같은 명령을 반복 실행했을 때
`receipt_created=false`, `idempotent_reuse=true`와 같은 SHA를 반환했다.
기존 영수증 byte hash도 전후 동일했다. 같은 output root의 다른 digest는
다른 과거 source/config lineage일 수 있으므로, 현재 근거는 반드시 위
config SHA와 네 source pin을 함께 만족하는 digest로 식별한다.

## 5. publication과 path 방어

영수증은 raw PCM, output waveform, 절대경로, speaker/utterance ID를
포함하지 않는다. synthetic input aggregate, duration metadata,
source/runtime hashes만 기록한다.

publication은 workspace root부터 descriptor-relative로 고정 경로만
연다. run 시작 시 source read와 worker 실행보다 먼저 workspace root의
device/inode identity를 store에 고정한다. symlink를 따르지 않고, content
SHA directory 안에 mode `0600` staging file을 완전히 쓰고 fsync한 뒤
hard-link no-overwrite로 `receipt.json`을 원자 공개한다. 기존 receipt는
mode, link count, 크기와 byte exact 일치할 때만 재사용한다. symlink
generation parent, hardlinked/tampered receipt, staging collision은
거부한다. 실행 중 output root가 rename되고 같은 경로에 replacement
directory가 생기는 경우도 최초 root identity 불일치로 거부한다.

실행 예시는 다음과 같다. reviewed wheel의 실제 절대경로는 private 실행
인자이고 receipt에 남지 않는다.

```bash
.venv-scorer/bin/python \
  scripts/run_e01_duration_synthetic_receipt.py \
  --workspace-root "$PWD" \
  --reviewed-wheel /path/to/reviewed/praat_parselmouth-0.4.7-cp312-cp312-linux_aarch64.whl
```

## 6. 검증

- worker receipt 신규 unit/adversarial/actual tests: `21 passed`
- duration primitive와 worker receipt 결합: `113 passed`
- E01 duration/F0/waveform/metamorphic/integration 인접 묶음: `238 passed`
- 신규 runner/worker/application/test Ruff: 통과
- 신규 runner/worker/application/test mypy `--strict`: 통과
- 실제 `.venv-paper-acoustics` process 반복 smoke: 통과

테스트는 config scope mutation, `k=1`, 두 job, paper-ready 오표시,
symlink parent, hardlinked receipt, noncanonical config, private failure
path와 argparse argument 누출을 거부한다. 별도 child에서 native `_socket`
호출이 seccomp에 의해 막히는지와 실제 idempotent reuse도 확인한다.

## 7. 아직 완료되지 않은 것

이 영수증의 상태는 명시적으로 `paper_ready=false`,
`production_ready=false`다. 다음은 모두 미완료다.

- 실제 한국어 발화와 승인된 정렬 구간
- S1과 S2의 독립 score/relation 평가
- output F0/voicing, 내용, energy, spectral, seam, 자연스러움,
  speaker similarity collateral 평가
- `k=1` identity-PSOLA matched control
- 실제 발화 반복성과 cross-platform 검증
- 논문 성능 주장, E01 pass rate, 전체 metamorphic robustness

따라서 이 gate는 seeded duration primitive의 격리·재현·provenance 기술
근거를 한 단계 강화할 뿐, 실제 한국어 코칭 효과나 논문 완료를 뜻하지
않는다.
