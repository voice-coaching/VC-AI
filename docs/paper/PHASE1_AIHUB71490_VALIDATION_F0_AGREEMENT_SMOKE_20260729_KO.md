# Phase 1 m4 AI Hub 71490 project-dev 실제 F0 agreement smoke

작성: 2026-07-29 KST  
판정: **v4 project-dev 3/4 비교 완료 / incomplete / m4 미완료**

## 1. 목적과 제한

이 실행은 합성음에서만 확인했던 Parselmouth와 torchcrepe F0 contour
배선을 AI Hub 71490 공식 `Validation` 발음 음원 중 프로젝트
`dev` 역할에만 연결한 작은 진단이다. 공식 partition과 프로젝트 split은
같은 개념이 아니며, 역할은 pinned modeling manifest의 `split`으로만
정했다. 다음을 주장하지 않는다.

- 두 backend 중 하나가 gold이거나 더 정확하다는 주장
- 발음·음질·자연스러움·학습 효과를 측정했다는 주장
- 외국어 사용자와 한국어 사용자를 비교했다는 주장
- 현재 floor/ceiling, periodicity, silence, octave 값이 검증된 과학
  기본값이라는 주장
- PDF Phase 1 m4 또는 downstream ablation을 완료했다는 주장

Parselmouth는 signed error의 방향을 정하는 `reference` 역할만 하며
사람 정답이 아니다. 현재 결과는 backend 간 기술적 agreement다.

## 2. 선택과 개인정보 경계

선택 순서는 다음과 같이 고정했다.

1. modeling manifest SHA-256
   `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1`
   을 검증했다.
2. 전체 322,380행에서 `audio_path`, `official_partitions`,
   `pcm_sha256`, `split` 네 metadata column만 읽었다. 이 scan은 audio
   content를 열지 않는다.
3. 공식 `Validation` pronunciation filename inventory 35,820개를
   modeling manifest와 1:1 join했다. unique 35,820, duplicate 0,
   missing 0, official-partition mismatch 0이었다.
4. join된 inventory의 프로젝트 split은 train 16,799 / dev 3,964 /
   stack 3,905 / calibration 3,872 / locked_test 7,280이었다.
5. 이 중 `dev` 3,964개만 rank 모집단으로 삼아 seed `20260729`,
   domain-separated SHA-256 filename rank로 4개를 고정했다.
6. 그 뒤에만 선택된 source PCM을 열어 manifest PCM digest를 4/4
   확인하고 decode/backend를 실행했다.

selection digest는
`00afff488b9e040bd08df6f4c3577284d7b7782b0d0b59178e6c33de5b78217d`,
project-dev inventory digest는
`e847c395f5d0b73243ed07596d80722bb8ef2a39b8c8d1f58b3ae69fb56cb23d`
다. decode/backend/agreement 실패가 나도 다음 파일을 채우지 않았고,
acoustic score나 threshold는 selection에 사용하지 않았다.

start/end snapshot 비교를 위해 전체 restricted metadata scan을 두 번
수행해 322,380행/scan, 총 644,760 metadata row를 읽었다. 이는 실제
audio 접근과 구분된다. 실제 source audio 접근은 프로젝트 `dev` 4건,
`locked_test` 0건이다.

artifact와 parent stdout에는 raw audio, 파일명·경로, source PCM hash,
발화/화자 ID, 전사, 개별 contour, 개별 metric을 남기지 않았다.
scorer parent가 성공적으로 decode한 음원을 16 kHz exact float32 PCM으로
만든 뒤 `/dev/shm`의 mode `0700` 임시 디렉터리 안 mode `0600` NPY
하나로 두 worker에 전달했다. contour는 capture된 subprocess stdout
memory pipe로만 parent에 돌아왔고, 만들어진 NPY와 디렉터리는 3/3
정리됐다.

## 3. 고정 파라미터

| 경계 | 고정값 |
|---|---|
| target PCM | mono 16 kHz float32, clipping/normalization 없음 |
| downmix | channel mean을 float64로 계산 후 float32 |
| resample | SciPy `resample_poly`, sample-rate GCD, float64 후 float32 |
| resource cap | 20초, 최대 8채널, agreement 4,000 frames |
| Parselmouth | 0.4.7, step 0.01초, 75–500 Hz, `keep_zero` |
| torchcrepe | 0.0.24, CPU, hop 160, `tiny`, `viterbi`, batch 32, pad |
| torchcrepe voicing | periodicity 0.21, silence -80 dBFS, `keep_zero` |
| RNG | NumPy/Torch 모두 20260729 |
| common grid | torchcrepe native grid, shared support 밖 point trim |
| interpolation | linear |
| unvoiced | gaps 보존 |
| octave diagnostic | nearest non-zero 1,200 cents ±50 cents |

이 값은 실행 전에 고정됐고 실제 결과를 본 뒤 바꾸지 않았다.

## 4. 구현 버그와 교정

초기 worker의 network guard는 `socket.socket` 타입 자체를 함수로
교체했다. torchcrepe의 lazy import가 socket type contract를 기대해
raw `TypeError`로 중단됐지만, 초기 generic failure mapping 때문에
데이터 문제처럼 보였다.

교정은 threshold나 음원 길이를 바꾸는 우회가 아니다.
`socket.socket`의 subtype을 유지하고 IP `connect`, `connect_ex`,
`sendto`만 차단하도록 변경했다. 실제 torchcrepe worker 회귀에서 exact
float32 synthetic 길이별 결과는 다음과 같았다.

| sample count | actual backend frames | 결과 | socket attempt |
|---:|---:|---|---:|
| 16,000 | 101 | 성공 | 0 |
| 16,001 | 101 | 성공 | 0 |
| 16,159 | 101 | 성공 | 0 |
| 16,160 | 102 | 성공 | 0 |

따라서 pad frame ceil/floor나 non-divisible length가 이번 초기 실패의
원인은 아니었다.

## 5. v3 split 감사와 실제 v4 결과

v3는 공식 `Validation`을 프로젝트 non-locked 역할처럼 해석한 의미론
오류가 있었다. privacy-safe 사후 join에서 v3 fixed 4는 modeling
manifest 4/4 unique match, source PCM 4/4 match였지만 프로젝트 split은
train 2 / calibration 2 / dev 0 / locked_test 0이었다. 따라서 v3가
locked audio를 읽지 않았다는 사실은 맞지만, calibration audio와
metric까지 관측했으므로 project-dev m4 evidence로 사용할 수 없다.
v3는 `mixed train+calibration technical diagnostic`로만 보존하고
canonical에서 제외한다.

v4는 앞 절의 project-dev-only 계약으로 새 표본을 audio read 전에
고정했다.

| 집계 | 값 |
|---|---:|
| fixed project-dev selections / PCM identity match | 4 / 4 |
| source metadata/decode attempt / decode 완료 | 4 / 3 |
| Parselmouth 완료 | 3 |
| torchcrepe 완료 | 3 |
| cross-process PCM digest match | 3 |
| agreement 완료 | 3 |
| network socket attempt | 0 |
| locked_test source audio access | 0 |
| 실패 | `derived_pcm_outside_closed_unit_interval`: 1 |

남은 1건은 source PCM identity 검증을 통과했지만 decode/downmix/resample
뒤 derived float32 PCM이 고정된 closed unit interval 계약을 벗어나
fail closed했다. 이 결과를 보고 clipping/normalization을 추가하거나
표본을 교체하지 않았다. 따라서 v4 상태는
`incomplete_bounded_technical_diagnostic`이다.

성공한 3건을 합친 pooled 기술 통계는 다음과 같다.

| 통계 | 값 |
|---|---:|
| requested / evaluated / trimmed common frames | 1,240 / 1,226 / 14 |
| jointly voiced / Parselmouth-only / torchcrepe-only / jointly unvoiced | 182 / 62 / 417 / 565 |
| pooled voicing agreement | 0.6092985318107668 |
| pooled jointly voiced rate | 0.14845024469820556 |
| torchcrepe-minus-Parselmouth mean | 3.2232511100955397 Hz / 40.26836322654496 cents |
| pooled MAE | 6.288689428628748 Hz / 62.57219302507527 cents |
| pooled RMSE | 19.256440998547447 Hz / 226.00431065717217 cents |
| maximum absolute error | 111.50113507009877 Hz / 1264.5676093195475 cents |
| octave diagnostic | 4 / 182 = 0.02197802197802198 |

이는 완료된 3건만의 기술 통계이며 실패한 1건을 누락한 성능 점수,
accuracy 또는 quality metric으로 사용하면 안 된다.

## 6. artifact 계보와 무결성

Current canonical bounded project-dev diagnostic v4:

- `artifacts/phase1/aihub71490_validation_f0_agreement_smoke_20260729_v4.json`
- mode: `0600`
- payload SHA-256:
  `5fffdad1050ed4c19e1440c7944742fb1784629a537041146160221353d60700`
- file SHA-256:
  `842a4b0f3fd68cc6a7d23838f2adc7e9e9f22f1ea164022f8cb70737eae54dfb`
- 독립 payload 재계산: valid
- 동일 source pins·selection·파라미터 repeat: payload와 file byte가 exact
  일치

이전 artifact는 원인·의미론 감사용으로 보존하지만 canonical
project-dev evidence로 사용하지 않는다.

| artifact | 상태 | 비교 | file SHA-256 |
|---|---|---:|---|
| `..._20260729.json` | generic guard failure, superseded | 0/4 | `5d61123c52276a3402658835c1e58e3bb3fa6fbf452c38f13b6b99ce35d557bd` |
| `..._20260729_v2.json` | safe category 확장 전 guard failure, superseded | 0/4 | `b0d2d34d9815c07e1c4290b65386e5b695c2e4a4979d0d7bacf0f7aa3a251f9c` |
| `..._20260729_v3.json` | mixed train+calibration, semantic split-contract failure, superseded | 3/4 | `881be86143791b11ea6a2d3b833ec47c81a8b3749ccca94b7af7e64c59cc2bee` |
| `..._20260729_v4.json` | project-dev-only incomplete diagnostic, current | 3/4 | `842a4b0f3fd68cc6a7d23838f2adc7e9e9f22f1ea164022f8cb70737eae54dfb` |

## 7. source 및 환경 pin

| role | SHA-256 |
|---|---|
| modeling manifest | `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1` |
| parent runner | `a5a473be0ed5649bfd41267e61153ef6ed8d77daaa0399a44b441adf2c9fa79f` |
| isolated worker | `a30109876cec145c1630bdbe1ba45a397dc81bb9001a505b473ac34b4b4e3ade` |
| smoke module | `ee6fcdbe34079bef8ade21ac88ec8b86d3f91dbf9ce7baf7835298040d359ec0` |
| agreement primitive | `5be0f44260d388fccd6f6150b99dec6cb99c5de9f8026043bae0f60e3a4b61fe` |
| Parselmouth adapter | `94bb38029ea6c429fdbfe883f19ccd02fab4028d8e1e417ad6634d9969bdeab1` |
| torchcrepe adapter | `5bea9a6df7f1bdda546afb9a7eb7985fd71acc2cbd141f0100179c4571860721` |
| source PCM hash implementation | `654d7b6c258801a02f2361c3d547eafb6dbb08d847bb1863b0aa24c84af57aef` |
| scorer lock | `f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2` |
| acoustic-tools lock | `77ee274aad798e6daeaad8d9da1055c2f572d45ae25e41f65bdfc00920ae8498` |
| torchcrepe lock | `5af1c06f88063de0962e7d8a0cd1480d5f3117734aee33a4d2fa189b39373086` |

환경은 scorer Python 3.12.3 / NumPy 2.5.1 / PyArrow 25.0.0 /
SciPy 1.18.0 / SoundFile 0.14.0, Parselmouth worker Python 3.12.3 /
NumPy 2.5.1 / praat-parselmouth 0.4.7, torchcrepe worker Python 3.12.3 /
NumPy 2.4.6 / torch 2.11.0+cpu / torchcrepe 0.0.24다.

## 8. 실행 명령

```bash
CUDA_VISIBLE_DEVICES='' HIP_VISIBLE_DEVICES='' ROCR_VISIBLE_DEVICES='' \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
NUMEXPR_NUM_THREADS=1 PYTHONPATH=src \
.venv-scorer/bin/python \
scripts/run_aihub71490_validation_f0_agreement_smoke.py \
  --dataset-root /home/cmsl-dgx01/intelligent04/intelligentAI/data/restricted/aihub/dataset-71490 \
  --modeling-manifest /home/cmsl-dgx01/intelligent04/intelligentAI/data/processed/aihub71490/manifests/aihub71490_modeling.parquet \
  --expected-modeling-manifest-sha256 32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1 \
  --output /home/cmsl-dgx01/intelligent04/intelligentAI/artifacts/phase1/aihub71490_validation_f0_agreement_smoke_20260729_v4.json \
  --private-temp-root /dev/shm \
  --parselmouth-python /home/cmsl-dgx01/intelligent04/intelligentAI/.venv-paper-acoustics/bin/python \
  --torchcrepe-python /home/cmsl-dgx01/intelligent04/intelligentAI/.venv-paper-torchcrepe/bin/python \
  --expected-agreement-source-sha256 5be0f44260d388fccd6f6150b99dec6cb99c5de9f8026043bae0f60e3a4b61fe \
  --expected-parselmouth-adapter-source-sha256 94bb38029ea6c429fdbfe883f19ccd02fab4028d8e1e417ad6634d9969bdeab1 \
  --expected-parselmouth-environment-lock-sha256 77ee274aad798e6daeaad8d9da1055c2f572d45ae25e41f65bdfc00920ae8498 \
  --expected-runner-sha256 a5a473be0ed5649bfd41267e61153ef6ed8d77daaa0399a44b441adf2c9fa79f \
  --expected-scorer-environment-lock-sha256 f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2 \
  --expected-smoke-module-sha256 ee6fcdbe34079bef8ade21ac88ec8b86d3f91dbf9ce7baf7835298040d359ec0 \
  --expected-source-pcm-hash-source-sha256 654d7b6c258801a02f2361c3d547eafb6dbb08d847bb1863b0aa24c84af57aef \
  --expected-torchcrepe-adapter-source-sha256 5bea9a6df7f1bdda546afb9a7eb7985fd71acc2cbd141f0100179c4571860721 \
  --expected-torchcrepe-environment-lock-sha256 5af1c06f88063de0962e7d8a0cd1480d5f3117734aee33a4d2fa189b39373086 \
  --expected-worker-sha256 a30109876cec145c1630bdbe1ba45a397dc81bb9001a505b473ac34b4b4e3ade \
  --seed 20260729 --sample-size 4 --target-sample-rate-hz 16000 \
  --maximum-duration-seconds 20 --maximum-channel-count 8 \
  --downmix-policy mean_channels_float64_then_float32_v1 \
  --resample-policy scipy_resample_poly_gcd_float64_then_float32_v1 \
  --parselmouth-time-step-seconds 0.01 \
  --pitch-floor-hz 75 --pitch-ceiling-hz 500 \
  --parselmouth-unvoiced-policy keep_zero \
  --torchcrepe-hop-length-samples 160 \
  --torchcrepe-model tiny --torchcrepe-decoder viterbi \
  --torchcrepe-batch-size-frames 32 --torchcrepe-pad true \
  --torchcrepe-periodicity-threshold 0.21 \
  --torchcrepe-silence-threshold-dbfs -80 \
  --numpy-random-seed 20260729 --torch-random-seed 20260729 \
  --torchcrepe-unvoiced-policy keep_zero \
  --common-grid-policy torchcrepe_native_grid_trim_to_shared_support_v1 \
  --interpolation-policy linear \
  --endpoint-policy trim_to_shared_support \
  --agreement-unvoiced-policy preserve_gaps \
  --octave-tolerance-cents 50 \
  --maximum-agreement-frame-count 4000 \
  --reference-role parselmouth_reference_for_error_sign_only \
  --worker-timeout-seconds 300
```

## 9. 검증

```bash
CUDA_VISIBLE_DEVICES='' HIP_VISIBLE_DEVICES='' ROCR_VISIBLE_DEVICES='' \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
NUMEXPR_NUM_THREADS=1 PYTHONPATH=src \
.venv-scorer/bin/pytest -o addopts='' -q \
  tests/unit/test_actual_f0_agreement_smoke.py \
  tests/unit/test_run_aihub71490_validation_f0_agreement_smoke.py \
  tests/unit/test_run_phase1_f0_backend_worker.py \
  tests/unit/test_pitch_contour_agreement.py \
  tests/unit/test_parselmouth_pitch.py \
  tests/unit/test_torchcrepe_pitch.py
```

결과: **202 passed**. 선택/runner/worker 테스트는 35개이며, project
dev-only 사전 선택, train/calibration/locked source 미개방, manifest
pin과 scan 중 변경의 audio-before-fail 차단, duplicate join 차단, source
PCM mismatch 무교체, artifact privacy, 실제 torchcrepe CPU worker의
divisible/non-divisible 길이 4종을 포함한다. source 세 파일과 test 세
파일은 Ruff check/format을 통과했고, source 세 파일은 mypy를 통과했다.

## 10. 남은 m4 작업

1. `derived_pcm_outside_closed_unit_interval` 결과에 맞춰 즉석
   clipping/normalization을 추가하지 말고, resample overshoot와 headroom
   처리 정책을 별도 calibration protocol로 사전등록·감사한다.
2. 성별·연령·L1·억양·화자 slice별 pitch 범위와 voicing 정책을
   사전등록한다.
3. bounded smoke가 아닌 독립 calibration set에서 backend calibration,
   CI와 민감도 분석을 수행한다.
4. 문장 말미 경계톤, prominence, speaker normalization과 alignment를
   연결한다.
5. m4 feature ablation과 downstream 평가, 사람 annotation을 수행한다.

따라서 이 문서는 실제 음원 경로의 배선 공백을 줄였지만 논문 실험
완료 증거가 아니다.
