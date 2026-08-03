# UTMOSv2 artifact gate 독립 감사

감사 시각: 2026-07-29 19:22 KST  
범위: Phase 1 m7, R02 `acoustic_artifact`, R04 보조 자연스러움 지표  
실행 경계: **공식 공개 메타데이터와 작은 텍스트 source만 read-only 조회**

## 1. 결론

UTMOSv2의 현재 공식 code/model 저장소와 정확한 revision, Hugging Face
model repo의 7개 파일, 5개 checkpoint의 크기와 LFS SHA-256을 확인했다.
정확한 revision에 대한 HTTP `HEAD`의 `x-linked-etag`도 5개 LFS
SHA-256과 일치했다.

그러나 이 결과만으로 R02 artifact attestation을 승인할 수 없다.

- checkpoint byte는 내려받거나 로컬에서 다시 hash하지 않았다.
- code를 설치하거나 checkpoint를 역직렬화하지 않았다.
- GPU, CPU inference, audio, metric을 실행하지 않았다.
- 현재 code는 checkpoint 외에도 `facebook/wav2vec2-base`와 timm
  `tf_efficientnetv2_s.in21k_ft_in1k` pretrained artifact를 model 생성
  시점에 요청할 수 있다. 이 transitive artifact의 exact revision,
  file manifest, license는 아직 감사하지 않았다.
- model card는 `license: mit`를 표기하지만 model repo 자체에는 별도
  `LICENSE` 파일이나 checkpoint 범위를 설명하는 license 본문이 없다.
- 학습 데이터 목록에는 CC BY-NC-SA와 별도 조건을 가진 자료가 포함된다.
  model-card 표기만으로 checkpoint의 연구 사용·재배포 권한을 법적으로
  승인했다고 간주하지 않는다.
- exact normalization, silence removal, segmentation, random-crop/TTA seed,
  fold aggregation, threshold와 minimum support가 사전등록되지 않았다.
- 한국어 또는 L2 한국어 자연스러움에 대한 타당도 실험은 0이다.

따라서 현재 판정은 **upstream metadata gate 일부 통과 / acquisition,
license authorization, offline-runtime, smoke, 한국어 validity gate
미통과**다. R02의 `research_use_authorized`,
`approved_by_external_human`, actual R02와 논문 성능 주장은 계속 0이다.

## 2. 감사 방법과 비실행 증거

다음 공식 endpoint만 조회했다.

- [GitHub 공식 저장소](https://github.com/sarulab-speech/UTMOSv2)
- [GitHub REST repository API](https://api.github.com/repos/sarulab-speech/UTMOSv2)
- [GitHub exact commit](https://github.com/sarulab-speech/UTMOSv2/tree/cc2700db57bb83ee13dc31ebe1b868c254e15d09)
- [Hugging Face 공식 model repo](https://huggingface.co/sarulab-speech/UTMOSv2)
- [Hugging Face model API](https://huggingface.co/api/models/sarulab-speech/UTMOSv2)
- [Hugging Face refs API](https://huggingface.co/api/models/sarulab-speech/UTMOSv2/refs)
- [Hugging Face exact-revision tree API](https://huggingface.co/api/models/sarulab-speech/UTMOSv2/tree/506474f2b33dc77c234d668cc419be1861899cad?recursive=true&expand=true)

Weight URL에는 body를 받지 않는 `HEAD`만 사용했다. Workspace에는
`fold[0-4]_s42_best_model.pth`가 생성되지 않았다. source의 `LICENSE`,
`pyproject.toml`, 관련 Python file, Hugging Face model card처럼 작은
텍스트만 읽었다. UTMOSv2 설치, cache 생성, `torch.load`, model 생성,
audio decode와 inference는 실행하지 않았다.

이 감사는 기존
[Phase 1 도구 환경 감사](PHASE1_TOOL_ENV_AUDIT_20260729_KO.md)와
[R02 정적 준비도](../R02_HARD_GATE_READINESS_KO.md)의
`TBD_ATTESTATION`을 임의로 해제하지 않는다. 정본 config/template/contract
또는 S104/S106–S108 파일도 수정하지 않았다.

## 3. 공식 code snapshot

2026-07-29 감사 시각의 GitHub 상태는 다음과 같다.

| 항목 | 확인값 |
|---|---|
| repository | `sarulab-speech/UTMOSv2` |
| visibility | public |
| default branch | `main` |
| `main` commit | `cc2700db57bb83ee13dc31ebe1b868c254e15d09` |
| commit 시각 | `2026-04-02T21:37:25Z` |
| GitHub verification | `verified=true` |
| root tree | `e34339fd3cd47754dac08bac1f7b7f66d0f46a26` |
| recursive tree | `truncated=false`, blob 112개, 합계 3,539,619 bytes |
| source version | `1.3.1.dev0` |
| 최신 release tag | `v1.3.0` → `14a281c6072d6fad2bf31f992ed2e60304400075` |

현재 default branch는 최신 release보다 뒤의 development snapshot이다.
따라서 “최신”이라는 가변 이름을 쓰지 않는다. 현 default를 채택한다면
`code_revision` 후보는 위 40자리 commit이고, release-only 정책을
채택한다면 별도 호환성 감사를 거쳐 `v1.3.0` commit을 사용해야 한다.
두 선택을 섞으면 안 된다.

주요 source identity는 다음과 같다. 아래 `git_blob_sha1`은 Git object
식별자이며 파일 byte의 SHA-256과 혼동하지 않는다.

| 파일 | bytes | `git_blob_sha1` |
|---|---:|---|
| `LICENSE` | 1,071 | `f48877016e1eb21a63ca24bbbf64aa91fa042bb2` |
| `pyproject.toml` | 1,339 | `b02225ed6624595185f47003fbedd9f25f500ae2` |
| `uv.lock` | 811,782 | `11317f969bdbd6fe559d30d04e8c26cda0a9dc9b` |
| `utmosv2/_core/create.py` | 3,012 | `51e569b00f38ed4f439e205cf8d119d5bad37700` |
| `utmosv2/utils/_download.py` | 1,697 | `d23ef21bd7222780fadd1faabb32484f5cf39d89` |
| `utmosv2/config/fusion_stage3.py` | 3,316 | `105013650c51615293f17542c417daddb930d0f6` |
| `utmosv2/model/ssl.py` | 4,237 | `7ec1973e8b689dedae5555dc5c3e989f37abdede` |
| `utmosv2/model/multi_spec.py` | 6,752 | `c38dbbec47df42c0d76b6ddb3ff06c676501486e` |

Exact-commit [LICENSE](https://raw.githubusercontent.com/sarulab-speech/UTMOSv2/cc2700db57bb83ee13dc31ebe1b868c254e15d09/LICENSE)는
MIT 본문이며 byte SHA-256은
`ddefd7cc91936715d038865d38d5ada0cd42d31916a2583f121dfa94242de76d`다.
[pyproject.toml](https://raw.githubusercontent.com/sarulab-speech/UTMOSv2/cc2700db57bb83ee13dc31ebe1b868c254e15d09/pyproject.toml)도
`license = { file = "LICENSE" }`와 MIT classifier를 표기하며 byte
SHA-256은
`715bf2580da456589a354c56e1fb6bb328da30e94235dd66ea635ab8cb04b830`다.
따라서 **이 exact snapshot의 UTMOSv2 code license 표기**는 MIT로
확인됐다.

Upstream `uv.lock` byte SHA-256은
`8250985069c8d175835027cbd41961edcde3895260a7202322d6c2254014f095`다.
다만 이 lock에는 Python/platform marker에 따른 복수 resolution이
있으며, project dependency도 lower bound를 사용한다. 이 hash를 곧바로
Linux aarch64/Python 3.12 실제 `runtime_environment_lock_sha256`로
재사용할 수 없다.

## 4. 공식 model snapshot과 전체 파일 manifest

Hugging Face refs/model API가 반환한 현재 default는 다음과 같다.

| 항목 | 확인값 |
|---|---|
| model ID | `sarulab-speech/UTMOSv2` |
| visibility/gate | public, `gated=false` |
| default branch | `main` |
| exact revision | `506474f2b33dc77c234d668cc419be1861899cad` |
| revision 시각 | `2025-05-10T18:28:04Z` |
| tags/other branches | tag 0개, branch는 `main` 1개 |
| model-card metadata | `license: mit` |

Exact revision에는 파일이 정확히 7개 있다.

| path | bytes | repo `oid` | LFS content SHA-256 / Xet hash |
|---|---:|---|---|
| `.gitattributes` | 1,519 | `a6344aac8c09253b3b630fb776ae94478aa0275b` | LFS 아님 |
| `README.md` | 1,819 | `9edbc5f3ea10def88cef1ca046bfb5c6b85eea82` | LFS 아님 |
| `fold0_s42_best_model.pth` | 818,531,314 | `d432777555ddc04c3af8b8f95e548dabc8b0be62` | `c8149d988e4bbf3f347e6966b5d769de347a5f8c59ffca1dc4bd4bf5b8585e57` / `149175e37e848270ca81a6f11f621dfd71388e2e3f3cfd369634c181adfca88a` |
| `fold1_s42_best_model.pth` | 818,531,314 | `be5f8247c7e3cdebaab7b9ecc98157827bc44fc7` | `b25a27b8ce23d722395f76a2fff7c259421950458ffb240066edd01d3f28cd1f` / `380105f67c459fe2d304a9e949644850ab62371c764b053a1ebae9e1fefb0b21` |
| `fold2_s42_best_model.pth` | 818,531,314 | `284e29b33f37150c65cd71eb732f2b1817d51fc0` | `c0beedbbed7ceef760adc556b925bf760639f281b20f75103b23fcdbdc5568f2` / `3d5285e01e36ee2d664a48d54665c47f546f82a6812d182246843e67be080018` |
| `fold3_s42_best_model.pth` | 818,531,314 | `15d9a0ee98bfd93493056cec9bbe49e731c9b57c` | `ec4b56921617c762428195daef9449855798bfdd651c95933afa9a29212b8567` / `25f68d1aba34c4369aa9ff69a74ed3e6fa8006360d77045f464dde1000f7d7fd` |
| `fold4_s42_best_model.pth` | 818,531,314 | `6ef334b67e05e1924b76e044fd5b7e133616f8c8` | `f5176ac65f92653a14b3aea1878575a44ebe94ff7eaac4e9057029c19f52c720` / `a70dcae42503229b1e71d8b04b47db06f2e51a1bda87f4f5ab5aefd6e642fafe` |

5개 checkpoint 합계는 4,092,656,570 bytes
(4.09265657 GB, 약 3.8116 GiB)다. 7개 repo file의 표시 크기 합계는
4,092,659,908 bytes다.

Exact-revision `HEAD`는 각 checkpoint에 대해 다음을 반환했다.

- `x-repo-commit`:
  `506474f2b33dc77c234d668cc419be1861899cad`
- `x-linked-size`: `818531314`
- `x-linked-etag`: 위 표의 해당 LFS content SHA-256
- `x-xet-hash`: 위 표의 해당 Xet hash

따라서 upstream metadata 내부의 revision/path/size/LFS-OID/ETag
일관성은 확인됐다. 이것은 **로컬 checkpoint byte 검증을 대체하지
않는다**. 실제 acquisition 뒤 파일 크기와 `sha256sum`이 위 값과 모두
일치해야 한다.

작은 텍스트 파일은 실제 byte도 확인했다.

- `.gitattributes` SHA-256:
  `11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361`
- [model card exact revision](https://huggingface.co/sarulab-speech/UTMOSv2/blob/506474f2b33dc77c234d668cc419be1861899cad/README.md)
  SHA-256:
  `9b74dcbea00dd0da1aeef15522f8a9b6bb882dfbfeeeac078557fae82487c72d`

## 5. License 판정 경계

| 대상 | 현재 evidence | 판정 |
|---|---|---|
| exact GitHub code | MIT `LICENSE` 본문 + `pyproject` MIT 표기 | code 표기 확인 |
| Hugging Face checkpoint repo | model-card front matter `license: mit`, API tag `license:mit` | upstream metadata 표기 확인 |
| checkpoint 전용 license 본문 | model repo 7개 파일에 `LICENSE` 없음 | 미확인 |
| transitive Wav2Vec2/timm artifact | source에 model name만 존재 | exact revision/license 미감사 |
| training data provenance | [official dataset 문서](https://github.com/sarulab-speech/UTMOSv2/blob/cc2700db57bb83ee13dc31ebe1b868c254e15d09/docs/datasets.md)에 BSD-3-Clause, CC BY 4.0, ODbL, MIT, CC BY-NC-SA 4.0 및 별도 Blizzard 자료 링크가 혼재 | checkpoint 권리 영향 법무 검토 미완료 |
| research use/redistribution | external human/legal attestation 없음 | 승인 false |

Model-card SHA는
`checkpoint_license_evidence_sha256`의 **후보 evidence**일 뿐이다.
외부 승인자가 checkpoint 자체에 MIT 표기가 적용되는지, third-party
구성요소와 학습 데이터 조건이 목표 연구와 결과물 배포에 미치는 영향을
검토하기 전에는 `checkpoint_license_spdx=MIT`와
`research_use_authorized=true`를 production attestation에 기록하지
않는다.

## 6. 5개 checkpoint만으로 offline bundle이 되지 않는 이유

Exact code에서 확인한 network/runtime 경계는 다음과 같다.

1. [`create_model`](https://github.com/sarulab-speech/UTMOSv2/blob/cc2700db57bb83ee13dc31ebe1b868c254e15d09/utmosv2/_core/create.py)은
   명시적 `checkpoint_path`가 없고 cache file도 없으면 downloader를
   호출한다.
2. [downloader](https://github.com/sarulab-speech/UTMOSv2/blob/cc2700db57bb83ee13dc31ebe1b868c254e15d09/utmosv2/utils/_download.py)는
   `resolve/main/fold...pth`를 `wget`으로 받는다. exact revision이
   아니므로 R02 실행 경로에서 사용하면 안 된다.
3. `fusion_stage3`는 `facebook/wav2vec2-base`와
   `tf_efficientnetv2_s.in21k_ft_in1k`를 선언한다.
4. SSL model은 `AutoFeatureExtractor.from_pretrained(model_name)`와
   `AutoModel.from_pretrained(model_name)`을 호출한다.
5. spectrogram backbone은 `timm.create_model(..., pretrained=True)`를
   호출한다.

즉 UTMOSv2 checkpoint를 local path로 넘겨도 model construction 전에
Wav2Vec2/timm initializer가 network/cache를 요구할 수 있다. 다음 중
하나를 별도 구현·검증해야 한다.

- transitive artifact를 exact revision/license/file hash와 함께 local
  bundle에 포함하고 완전 offline cache resolution을 증명한다.
- checkpoint가 필요한 모든 parameter를 포함한다는 state-dict 감사를
  먼저 한 뒤, pretrained initializer를 호출하지 않는 좁은 local-only
  adapter를 구현하고 strict load 결과를 증명한다.

어느 쪽도 이번 감사에서는 수행하지 않았다.

## 7. Serialization과 전처리 위험

Model repo의 다섯 `.pth`는 pickle 기반 PyTorch checkpoint다. Hugging
Face tree API는 `OrderedDict`, `LongStorage`, `FloatStorage`,
`_rebuild_tensor_v2` import를 탐지했다고 표시한다. 이는 외부 scanner
결과일 뿐 임의 code 실행 부재를 보증하지 않는다.

Exact source는 `torch.load(checkpoint_path, map_location=device)`를
호출하고 `weights_only`를 명시하지 않는다. 실제 의미는 선택한 PyTorch
version에 의존한다. 따라서 hash 검증 전 deserialization 금지,
credential 없는 격리 환경, read-only bundle, egress 차단, exact
PyTorch lock과 안전 load 정책 검토가 선행돼야 한다.

또한 현재 API/설정에는 결과를 바꿀 수 있는 명시적 결정이 남아 있다.

- 입력 sample rate는 16 kHz이며 다른 rate는 torchaudio로 resample된다.
- `fusion_stage3`는 네 종류 mel spectrogram, 3초 SSL crop, 1.4초
  spectrogram crop 두 개를 사용한다.
- crop은 NumPy random start를 사용한다.
- `predict_dataset` 기본값은 `sarulab`이며 domain embedding에 영향을
  준다.
- `predict(..., remove_silent_section=True)`가 현재 API 기본값이지만,
  source 주석은 이 기능이 VoiceMOS 2024/논문 실험 뒤에 추가됐다고
  명시한다.
- silence removal 구현은 `audio < 0.1` mask를 사용한다. 이 동작을
  일반적인 절댓값 기반 VAD로 해석하면 안 된다.
- 기본 `num_repetitions=1`과
  [공식 inference guide](https://github.com/sarulab-speech/UTMOSv2/blob/cc2700db57bb83ee13dc31ebe1b868c254e15d09/docs/inference.md)의
  5 folds × 5 random frames 평균은 서로 다르다.

따라서 `normalization_id`, `segmentation_id`, silence-removal 여부,
domain ID, seed schedule, folds, repetitions, aggregation은 R02
preregistration 전부에 같은 exact spec으로 고정해야 한다. Human/TTS에
source별 다른 값을 쓸 수 없다.

## 8. R02 attestation field 현재 매핑

| R02 field | metadata 후보 | 지금 채울 수 있는가 |
|---|---|---|
| `plan_backed_model_family` | `UTMOSv2` | 기존 계획값 유지 |
| `exact_model_id` | `sarulab-speech/UTMOSv2` | upstream identity는 확인, attestation 승인은 아직 |
| `exact_checkpoint_revision` | `506474f2b33dc77c234d668cc419be1861899cad` | metadata 확인, local byte 검증 전 승인 금지 |
| `checkpoint_manifest_sha256` | 5개 path/size/LFS SHA 표 | local canonical manifest가 없어 미확인 |
| `code_revision` | current-default 후보 `cc2700db57bb83ee13dc31ebe1b868c254e15d09` | stable/dev 선택 승인 전 미확정 |
| `code_license_spdx` | `MIT` | exact code 표기 확인 |
| `code_license_evidence_sha256` | `ddefd7cc...de76d` | evidence byte 확인 |
| `checkpoint_license_spdx` | model-card metadata 후보 `MIT` | 별도 license/외부 승인 전 미확정 |
| `checkpoint_license_evidence_sha256` | model-card `9b74dcbe...7c72d` | 후보 evidence, 승인값 아님 |
| `normalization_id/spec_sha256` | 없음 | 미확인 |
| `segmentation_id/spec_sha256` | 없음 | 미확인 |
| `research_use_authorized` | 승인 evidence 없음 | `false` 유지 |
| `local_bundle_manifest_sha256` | transitive artifact 포함 bundle 없음 | 미확인 |
| `runtime_environment_lock_sha256` | upstream multi-environment `uv.lock`만 존재 | target 환경 lock 미확인 |
| `approved_by_external_human` | 없음 | `false` 유지 |

## 9. Exact acquisition gate

S104 종료와 별도 실행 승인을 확인한 뒤에도 아래 순서를 모두 지켜야 한다.

### A0. 정책 동결

1. current `main` development commit 또는 `v1.3.0` release commit 중
   하나를 선택한다.
2. `fusion_stage3`, folds 0–4, seed 42, 16 kHz, domain ID, silence removal,
   crop/TTA seed와 반복 수, fold aggregation을 사전등록한다.
3. R02의 metric/threshold/minimum-support/normalization/segmentation은
   결과를 보기 전에 승인한다.
4. code/checkpoint/transitive artifact의 연구 사용과 결과물 보관·배포
   범위를 외부 승인자가 기록한다.

### A1. 격리된 source·weight staging

1. credential이 없고 실행 권한이 없는 staging에 exact source commit을
   checkout한다.
2. `HEAD`가 선택 commit인지, `HEAD^{tree}`가 승인 tree인지 확인한다.
3. 모델은 `main` URL이 아니라
   `sarulab-speech/UTMOSv2@506474f2b33dc77c234d668cc419be1861899cad`
   에서 위 7개 allowlist만 받는다.
4. 각 checkpoint의 실제 byte size와 SHA-256을 위 표와 비교한다. 하나라도
   다르면 전 bundle을 quarantine하고 load하지 않는다.
5. canonical checkpoint manifest에 repo ID, revision, path, size,
   SHA-256, acquisition UTC, downloader/version을 기록한 뒤 그 JSON
   byte의 SHA-256을 `checkpoint_manifest_sha256`으로 사용한다.

### A2. Transitive artifact와 환경 봉인

1. `facebook/wav2vec2-base`와 timm backbone이 실제로 요구하는 모든
   config/weight를 dry construction trace로 식별한다.
2. 각 artifact를 exact revision/path/size/content SHA-256/license에
   고정한다. 가변 alias와 cache `main` ref는 금지한다.
3. Linux aarch64/Python 3.12 전용 dependency lock을 새로 resolve하고
   wheel/source hash, Torch/CUDA 조합, license를 기록한다.
4. source, 5개 checkpoint, transitive artifact와 license evidence를
   하나의 canonical local bundle manifest로 묶는다.
5. runtime에는 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`과 egress
   차단을 함께 적용하고 network attempt가 0임을 syscall/network
   audit로 증명한다. 환경변수만으로 offline을 주장하지 않는다.

### A3. 역직렬화 전 보안 gate

1. 실제 SHA-256 검증이 끝나기 전 `.pth`를 열지 않는다.
2. 정적 pickle scan 결과와 허용 global을 기록한다.
3. exact PyTorch에서 `weights_only` 동작과 strict state-dict load
   compatibility를 확인한다.
4. credential 없는 unprivileged process, read-only bundle, no-network
   환경에서만 첫 load를 수행한다.

## 10. Exact smoke gate

Acquisition gate를 모두 통과한 뒤 protected audio 없이 다음 순서로
실행한다.

1. 16 kHz mono finite `float32`, 4초 합성 파형 한 개를 in-memory로
   준비한다. path, learner, speaker, S1/S2, gold/locked data는 쓰지 않는다.
2. fold 0 checkpoint를 **명시적 local path**로 전달하고 CPU에서 model
   construction/load만 수행한다. download/cache miss/network attempt,
   missing/unexpected state key가 모두 0이어야 한다.
3. caller가 `predict_dataset`, `remove_silent_section`, `num_workers=0`,
   batch size, `num_repetitions`, Python/NumPy/Torch seed를 모두 명시한다.
4. 출력이 shape `(1,)`, finite이고 process 재시작 뒤 고정 seed에서
   재현되는지 확인한다. 이 단계는 값의 정확성이나 자연스러움 타당도를
   주장하지 않는다.
5. folds 0–4 각각을 같은 입력·seed schedule로 실행하고 per-fold,
   per-repetition 값과 사전등록 aggregate를 기록한다. `fold=-1` 같은
   암묵 동작에 의존하지 않고 5개 exact local checkpoint를 각각 지정한다.
6. silence-removal on/off와 1/5 repetitions는 threshold를 정하기 위한
   사후 선택이 아니라 사전등록된 sensitivity audit로만 비교한다.
7. 합성 smoke가 통과한 뒤에만 non-locked, consent/licensed 한국어
   소표본을 human/TTS 동일 pipeline으로 실행한다.
8. 한국어/L2 한국어에서 인간 자연스러움 평정과의 상관·calibration,
   speaker/language/source slice, uncertainty를 독립 평가하기 전에는
   R02 hard threshold나 R04 인간평가 대체 지표로 승격하지 않는다.

## 11. 완료 조건과 현재 남은 값

다음 artifact가 모두 존재하고 독립 deep validation을 통과해야 이 gate가
완료된다.

- exact source checkout receipt와 tree/license hash
- local 5-checkpoint canonical manifest와 실제 byte hash
- transitive Wav2Vec2/timm manifest와 license evidence
- target-platform dependency lock과 hash
- no-network construction/load trace
- safe-deserialization review
- synthetic one-fold 및 five-fold smoke report
- normalization/segmentation/randomness/aggregation preregistration
- external research-use/license approval
- non-locked 한국어 validity/calibration report

현재 생성된 것은 이 **read-only upstream 감사 문서뿐**이다.
Checkpoint/local bundle/environment manifest, attestation, smoke report,
한국어 metric과 성능 결과는 생성되지 않았다.
