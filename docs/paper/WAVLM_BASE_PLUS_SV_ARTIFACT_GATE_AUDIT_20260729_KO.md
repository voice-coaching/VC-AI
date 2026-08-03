# WavLM Base+ speaker-verification artifact gate 독립 감사

감사 시각: 2026-07-29 19:26 KST  
범위: PDF Phase 1 m8 `WavLM-SV speaker/style similarity`  
실행 경계: **공식 공개 metadata, HTTP HEAD, 작은 text source와 기존
로컬 환경 metadata만 read-only 조회**

## 1. 결론

Microsoft가 게시한 Hugging Face
`microsoft/wavlm-base-plus-sv`의 현재 exact revision, 전체 5개 파일,
checkpoint 게시 크기와 LFS SHA-256/Xet hash, config의 WavLM/X-Vector
구조와 16 kHz 전처리 계약을 확인했다. 정확한 revision의 checkpoint
URL에 body를 받지 않는 HTTP `HEAD`를 적용했고, API의 LFS SHA-256과
`x-linked-etag`가 일치했다.

그러나 현재 m8 모델 실행 gate는 통과하지 않았다.

- `pytorch_model.bin` 404,547,053 bytes를 다운로드하거나 로컬에서
  다시 hash하지 않았다.
- checkpoint를 역직렬화하거나 모델을 생성하지 않았다.
- GPU/CPU inference, audio decode, embedding, cosine, threshold와
  한국어 타당도 실험은 모두 0이다.
- model repo의 YAML metadata에는 `license`가 없고 별도 `LICENSE` 파일도
  없다. Model card의 license 링크는 UniLM이 아니라
  `microsoft/UniSpeech/main/LICENSE`이며, 현재 대상 본문은
  CC BY-SA 3.0 Unported다. 이 링크가 checkpoint byte에 적용되는 정확한
  범위와 연구 사용·재배포 조건은 별도 문장으로 설명돼 있지 않다.
- Microsoft UniLM source는 MIT, 현재 project가 고정한 Hugging Face
  Transformers 실행 source는 Apache-2.0이다. 이 코드 라이선스들을
  checkpoint 라이선스로 바꿔 쓰지 않는다.
- Model card가 지지하는 용도는 speaker verification과 embedding cosine
  retrieval이다. 한국어 발음 정확성, 자연스러움, 운율·스타일, 교육
  적합성 또는 음성 변환 성공을 검증한다는 근거는 없다.
- Speaker embedding과 cosine은 화자 정체성 신호를 강하게 포함한다.
  따라서 높은 score는 “한국인처럼 발음함”이나 “좋은 한국어 스타일”을
  자동 증명하지 않는다.
- Reference/candidate audio의 명시적 생체처리 동의, 철회·보존·삭제,
  목적 제한과 D1 embedding 보호가 승인되지 않았다.

현재 판정은 **upstream metadata·구조 gate 일부 통과 / checkpoint
license authorization, local acquisition, safe offline load, synthetic
smoke, consented Korean validity gate 미통과**다. 기존
`SpeakerSimilarity` 수치 primitive가 완료됐다는 사실도 실제 WavLM-SV
실행이나 m8 논문 결과를 뜻하지 않는다.

## 2. 감사 방법과 비실행 증거

다음 primary source와 read-only API를 사용했다.

- [Microsoft WavLM/UniLM 공식 source](https://github.com/microsoft/unilm/tree/833df7e7832e5064a281131ee64a481afa8e5b95/wavlm)
- [Microsoft WavLM 공식 README](https://github.com/microsoft/unilm/blob/833df7e7832e5064a281131ee64a481afa8e5b95/wavlm/README.md)
- [Hugging Face 공식 model repo](https://huggingface.co/microsoft/wavlm-base-plus-sv)
- [Hugging Face model API](https://huggingface.co/api/models/microsoft/wavlm-base-plus-sv)
- [Hugging Face refs API](https://huggingface.co/api/models/microsoft/wavlm-base-plus-sv/refs)
- [Hugging Face exact-revision tree API](https://huggingface.co/api/models/microsoft/wavlm-base-plus-sv/tree/feb593a6c23c1cc3d9510425c29b0a14d2b07b1e?recursive=true&expand=true)
- [Hugging Face exact-revision model card](https://huggingface.co/microsoft/wavlm-base-plus-sv/blob/feb593a6c23c1cc3d9510425c29b0a14d2b07b1e/README.md)
- [Transformers v5.14.1 exact source](https://github.com/huggingface/transformers/blob/a08ace4bbd97e721c98751deec37d87b026acadc/src/transformers/models/wavlm/modeling_wavlm.py)
- [Model card가 실제로 연결한 UniSpeech LICENSE](https://github.com/microsoft/UniSpeech/blob/6112826ac13a4327f4c9a7afa2a505e35b763514/LICENSE)

Checkpoint URL에는 `HEAD`만 사용했다. Model API/tree JSON과
`.gitattributes`, `README.md`, `config.json`,
`preprocessor_config.json` byte만 읽었다. 알려진 두 Hugging Face cache
root에는 `models--microsoft--wavlm-base-plus-sv` snapshot이 없었고,
workspace에서도 이 model ID 아래 `pytorch_model.bin`을 찾지 못했다.
이는 전체 host를 대상으로 한 부재 증명이 아니라 이번 감사가 새
checkpoint를 내려받지 않았다는 제한된 확인이다.

다음을 실행하지 않았다.

- `from_pretrained`, `torch.load`, pickle deserialization
- package 설치·변경, cache 생성, model/processor 생성
- microphone·audio·reference·candidate·embedding 접근
- CUDA query·GPU inference·CPU inference
- score, threshold, EER, ROC, 한국어 또는 style validity 계산

이 감사는 기존 m8 primitive, D08/R02 계약, 정본 config/template,
README, PDF 실행 매트릭스와 S104/S106–S108 source를 수정하지 않는다.
대조한 내부 경계는
[m8/Phase 4 speaker-cosine primitive](../PHASE1_SPEAKER_SIMILARITY_PRIMITIVE_KO.md),
[D08 reference data·license·privacy card](../D08_NATIVE_REFERENCE_DATA_MODEL_LICENSE_PRIVACY_CARD_KO.md),
[R02 hard-gate data/model card](../R02_HARD_GATE_DATA_MODEL_CARD_KO.md),
[연구 데이터 개인정보 경계](../DATA_PRIVACY_BOUNDARY_KO.md)다.

## 3. Hugging Face model snapshot과 전체 manifest

2026-07-29 감사 시각의 공식 API 상태는 다음과 같다.

| 항목 | 확인값 |
|---|---|
| model ID | `microsoft/wavlm-base-plus-sv` |
| visibility/gate | public, `private=false`, `gated=false` |
| default branch | `main` |
| branch/tag | `main` 1개, tag 0개 |
| exact revision | `feb593a6c23c1cc3d9510425c29b0a14d2b07b1e` |
| last modified | `2022-03-25T10:39:41Z` |
| library/tag | `transformers`, `wavlm`, `audio-xvector`, `speech`, `en` |
| model-card metadata | `language: [en]`, `tags: [speech]`, `license` 없음 |
| pipeline tag | 없음 |

Exact revision에는 파일이 정확히 5개 있고 표시 크기 합계는
404,611,134 bytes다.

| path | bytes | repo blob SHA-1 | LFS content SHA-256 / Xet hash |
|---|---:|---|---|
| `.gitattributes` | 1,175 | `6d34772f5ca361021038b404fb913ec8dc0b1a5a` | LFS 아님 |
| `README.md` | 4,052 | `09bcae205c83ecdc42853bf33bcd6f7f9f93af12` | LFS 아님 |
| `config.json` | 58,639 | `1b6ea0f4e7116bf5ff45c29c05d161dbc083b2c8` | LFS 아님 |
| `preprocessor_config.json` | 215 | `10f6def8c83d70a2b087a567dcf523b75152a80b` | LFS 아님 |
| `pytorch_model.bin` | 404,547,053 | `c66ab15295b8b2c06b5628f64882abbbc6ca2079` | `e906bce2fa42fb497a1d1a9ecf81548adb7e03b12a5644e32d2f42f0d6500fad` / `a43e188ea6506e6327525f4df9cfa09fb65cbfbb8c3ae9792150127a04f686c1` |

Checkpoint의 exact-revision `HEAD`는 다음을 반환했다.

- `x-repo-commit`:
  `feb593a6c23c1cc3d9510425c29b0a14d2b07b1e`
- `x-linked-size`: `404547053`
- `x-linked-etag`:
  `e906bce2fa42fb497a1d1a9ecf81548adb7e03b12a5644e32d2f42f0d6500fad`
- `x-xet-hash`:
  `a43e188ea6506e6327525f4df9cfa09fb65cbfbb8c3ae9792150127a04f686c1`

따라서 upstream metadata 내부의 revision/path/size/LFS SHA/ETag
일관성은 확인됐다. Repo blob SHA-1은 Git LFS pointer object
식별자이고 LFS SHA-256은 게시된 checkpoint content 식별자다. Xet hash도
별도 storage 식별자다. 어느 값도 실제 local byte를 내려받아 계산한
SHA-256을 대신하지 않는다.

작은 파일은 실제 byte SHA-256까지 확인했다.

| path | byte SHA-256 |
|---|---|
| `.gitattributes` | `fa057bb09b78fe6d33af5a01440be5e8c881cd8055de35ee5588ea759cf57bfc` |
| `README.md` | `97f5513cde351b3adb4e182b60ec23154dadba7ca83e667ceace237177855b8e` |
| `config.json` | `c6ac8c0ce55b18d4612677931036fe5ce78093e87361c552a173f00a0bb07514` |
| `preprocessor_config.json` | `99272fe8ccfab114b68b478681ea47ee3a1ce62bb788cb92dd6e4f69fb1f1da2` |

`pytorch_model.bin`은 pickle 계열 PyTorch serialization이다. Tree API의
scanner는 `collections.OrderedDict`, `torch._utils._rebuild_tensor_v2`,
`torch.FloatStorage`를 탐지하고 `safe`로 표시했다. 외부 scanner 결과는
hash 검증 전 load를 허용하거나 임의 code 실행 부재를 보증하지 않는다.
Model repo에는 `safetensors` 대안이 없다.

## 4. Config, pooling과 head

Exact `config.json`과 `preprocessor_config.json`에서 확인한 실행 계약은
다음과 같다.

| 항목 | 확인값 |
|---|---|
| architecture | `WavLMForXVector` |
| base name | `microsoft/wavlm-base-plus` |
| dtype | `float32` |
| encoder | hidden 768, 12 layers, 12 heads, FFN 3,072 |
| convolution | dim 512×7, kernel `[10,3,3,3,3,2,2]`, stride `[5,2,2,2,2,2,2]`, group norm, bias false |
| layer aggregation | `use_weighted_layer_sum=true` |
| TDNN dimensions | `[512,512,512,512,1500]` |
| TDNN kernel | `[5,3,3,1,1]` |
| TDNN dilation | `[1,2,3,1,1]` |
| statistic pooling | time mean과 standard deviation 연결 |
| embedding | statistic pool 3,000 → linear 512 |
| classifier | embedding 512 → 512, 학습 objective는 AM-Softmax |
| speaker labels | `label2id` 1,211개 |
| preprocessing | mono waveform feature size 1, 16,000 Hz, right padding, attention mask true |
| waveform normalization | `do_normalize=false` |
| serialized Transformers version | `4.15.0.dev0` |

Audited Transformers `WavLMForXVector` source는 12개 transformer layer와
input embedding을 포함한 13개 hidden state를 softmax weight로 합친다.
그 뒤 768→512 projector, 다섯 TDNN, time mean/std statistic pooling,
3,000→512 `feature_extractor`, 512→512 classifier를 적용한다.
`outputs.embeddings`는 classifier 전 512차원 값이다. Model card 예시는
이 값을 명시적으로 L2 normalize한 뒤 cosine을 계산한다.

Model card의 `0.86`은 같은 card에서 dataset-dependent threshold라고
명시한다. 이 숫자를 한국어, multi-reference 또는 m8 hard threshold로
복사하지 않는다. 기존
`score_speaker_similarity(...)` primitive도 threshold를 제공하지 않고
집계 정책을 caller가 명시하도록 한 현재 경계가 맞다.

Config의 `transformers_version=4.15.0.dev0`은 exact Git revision이 아니다.
참고 비교로 공식 v4.15.0 tag
`05fa1a7ac17bb7aa07b9e0c1e138ecb31a28bbfe`의
`modeling_wavlm.py` SHA-256은
`c45dff5d05e919db8e423b45c279d8d3a4d09e90bf4d2fb803522c92f1247107`이며
동일한 X-Vector/TDNN/statistic-pooling 큰 구조를 가진다. 이것만으로
역사적 `4.15.0.dev0` byte나 현재 runtime과의 수치 동일성을 증명하지
않는다.

## 5. 실행 code snapshot과 환경 pin

두 code lineage를 구분한다.

### Microsoft UniLM/WavLM source

| 항목 | 확인값 |
|---|---|
| repository | `microsoft/unilm` |
| current `master` commit | `833df7e7832e5064a281131ee64a481afa8e5b95` |
| commit 시각 | `2026-01-23T04:09:35Z` |
| root LICENSE | MIT |
| LICENSE SHA-256 | `904dc4d8749877f1dba1cda48200d2462dccbeb7c134d5e4ef6fa75e0198c8fe` |
| `wavlm/README.md` SHA-256 | `b0a10869ea8d79c49c5d0efb026724bdef9abea3ee4a1604b8b43b2581c0e635` |
| `wavlm/WavLM.py` SHA-256 | `c39e65f49c9027b330519c30a576700f4ef2e74ccda52f7f60f259d3aad0d343` |

UniLM은 WavLM encoder의 공식 upstream source다. Hugging Face model
checkpoint가 직접 실행하는 `WavLMForXVector` head는 Transformers
implementation이므로 UniLM code pin 하나로 실행 source가 고정되지는
않는다.

### 현재 project의 Transformers source

현재 scorer lock은 `transformers==5.14.1`,
`torch==2.12.1+cu130`, `huggingface_hub==1.25.1`,
`safetensors==0.8.0`을 고정한다. Lock byte SHA-256은
`f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2`다.

Official Transformers v5.14.1 annotated tag는 commit
`a08ace4bbd97e721c98751deec37d87b026acadc`를 가리킨다. 현재 설치된
두 WavLM source byte는 해당 commit의 raw source와 일치했다.

| source | bytes | SHA-256 |
|---|---:|---|
| `modeling_wavlm.py` | 69,766 | `b77924cf473cb95dd74f3de893f0e9774a40b7f85a963930a93ef13164d48607` |
| `configuration_wavlm.py` | 13,102 | `2ab3a6662a0b2b3ac7e6963fc19df2fa8f75f9441164e0de7e6965bdfcb610b3` |

Transformers exact source LICENSE는 Apache-2.0이고 byte SHA-256은
`77fd4710def9ec3c0f6225800e0235f15a425abd4a8b03559127fcd782612049`다.
다만 현재 requirements lock은 version lock이지 모든 wheel의
`--hash`를 포함하는 fully hashed installation lock은 아니다. 따라서
위 source 일치는 좋은 후보 evidence이지만 future m8
`runtime_environment_lock_sha256`과 설치 artifact provenance를 모두
완료하지 않는다.

S104가 현재 같은 scorer/GPU 환경을 사용하므로 별도 m8 환경 설치,
checkpoint acquisition과 smoke는 S104 종료 및 명시적 resource
allocation 뒤에만 한다.

## 6. Code, checkpoint와 model-card license 경계

| 대상 | 현재 evidence | 판정 |
|---|---|---|
| Microsoft UniLM/WavLM code | exact source의 MIT `LICENSE` 본문 | code 표기 확인 |
| Hugging Face Transformers code | exact v5.14.1 source의 Apache-2.0 `LICENSE` 본문 | 실행 code 표기 확인 |
| HF model repo YAML/API | `license` key/tag 없음 | SPDX metadata 미확인 |
| HF model repo 파일 | 5개 중 별도 `LICENSE` 없음 | checkpoint-local license 본문 없음 |
| exact model card license 문장 | `microsoft/UniSpeech/main/LICENSE` 링크 | 링크 존재 확인 |
| 현재 링크 대상 | CC BY-SA 3.0 Unported, exact LICENSE SHA-256 `74295d561f60f770a4aa7525b71c0d119ec70422e9f5c601ee3c77e1b7822c91` | 대상 본문 확인 |
| checkpoint byte 적용 범위 | checkpoint를 명시한 별도 scope 문장 없음 | 미확인 |
| research use·보관·재배포 | 외부 human/legal attestation 없음 | 승인 false |

이 specific model card의 license 링크가 UniSpeech를 가리킨다는 사실은
중요하다. UniLM MIT LICENSE를 연결한다고 기록하거나, UniLM code
license를 checkpoint license로 승격하면 source 사실과 불일치한다.
따라서 기존 source ledger의 일반 WavLM 항목이 “model card는 UniLM
LICENSE로 연결”한다고 적은 부분은 이 Base+ SV artifact에는 적용할 수
없으며, 상위 문서가 별도로 교정해야 한다.
반대로 현재 CC BY-SA 링크를 발견했다는 사실만으로 checkpoint, embedding,
논문 표와 배포물에 어떤 의무가 생기는지 이 문서가 법적 결론을 내리지
않는다. 외부 승인자가 exact model-card byte와 현재 링크 대상, 링크의
가변 branch 문제, checkpoint scope와 upstream training-data 조건을 함께
검토해야 한다.

Model card는 pretraining corpus로 Libri-Light 60k h, GigaSpeech 10k h,
VoxPopuli 24k h를 적고, Base+ SV를 VoxCeleb1에 X-Vector/AM-Softmax로
fine-tune했다고 적는다. UniLM README의 SV 표는 별개의 WavLM Large
실험을 VoxCeleb2 dev로 fine-tune하고 VoxCeleb1에서 평가했다고 설명한다.
이를 Base+ SV checkpoint의 추가 lineage 증명으로 합치지 않는다.
Exact training recipe, sample list와 third-party data 조건은 model repo에
봉인돼 있지 않아 이번 감사에서는 미확인이다.

## 7. Intended use와 문서상 비지원·미검증 사용

Model card가 직접 지지하는 범위는 다음이다.

- 16 kHz speech input
- `WavLMForXVector` speaker-verification embedding
- embedding L2 normalization과 cosine similarity retrieval
- audio-only pretrained model이며 tokenizer가 없음

Card는 as-is ASR을 지원하지 않으며 ASR에는 tokenizer와 labeled text
fine-tuning이 필요하다고 적는다. `language: en` metadata 외에 한국어
L1/L2 validation, Korean pronunciation coaching, 한국어 style rating,
fairness, subgroup error, spoof/OOD 또는 security operating point를
제공하지 않는다.

따라서 다음은 “공식 금지”라고 추측하지는 않지만 **model card가
타당성을 지지하지 않는 사용**으로 처리한다.

- 한국어 내용·음소 정확성, 억양·리듬·경계톤 또는 자연스러움 점수
- “원어민 같음”, 교육적 개선, 학습 효과 또는 사람 평가 대체
- 화자 식별, 1:N 검색, 인증·출입 통제와 감시
- voice cloning, impersonation 또는 특정 원어민 정체성으로의 변환 목표
- 미성년자·취약집단·임상·고위험 의사결정
- spoof, replay, 합성음, codec, noise, microphone와 cross-language
  domain에서 검증된 보안 판단

논문에서 허용할 현재 이름은
`WavLM-SV auxiliary speaker-embedding cosine similarity`다.
사람의 한국어 style rating과 별도 construct validation을 통과하기 전에는
이를 `style validity score`, `native-likeness` 또는 발음 정답률로
호칭하지 않는다.

## 8. Biometric, consent와 privacy gate

음성 및 speaker embedding은 가명화 뒤에도 화자와 연결될 수 있는
생체유래 연구 데이터다. 공개 checkpoint와 public model ID는 reference
화자의 동의나 learner audio 처리 권한을 대신하지 않는다.

실제 audio 접근 전에 다음을 모두 외부 승인·봉인해야 한다.

1. Reference 화자별 활성 동의 receipt가 음성 녹음뿐 아니라
   speaker-embedding 생성, candidate-reference 비교, 보관기간,
   철회 시 파생 embedding 처리와 연구 결과 반출 범위를 명시한다.
2. Learner/candidate audio도 같은 목적 제한, 자동 분석, human review,
   보존·삭제·철회와 model-input 범위를 가진다.
3. D08의 기존 `model input` 여부를 TBD로 둔 계약을 m8이 임의로
   `true`로 해석하지 않는다.
4. R0 raw audio, R1 identity/locator, D1 pseudonymous embedding·pair
   score·digest, A1 aggregate 결과를 분리한다.
5. Embedding, input/result digest와 pair assignment는 익명 정보라고
   부르지 않는다. 재연결, membership inference와 목적 외 검색 위험을
   D1 접근정책에 포함한다.
6. 1:N identity search, reference speaker 추정, speaker name 복원과
   외부 biometric database join을 금지한다.
7. 철회·만료·범위 위반 receipt가 하나라도 있으면 해당 audio,
   embedding과 pair score를 quarantine하고 승인된 삭제/보존 절차를
   따른다.
8. 실제 m8 scorer에는 source path, speaker name, raw ID, prompt text와
   embedding vector를 결과 artifact에 쓰지 않는 allowlist를 둔다.

기존 순수 cosine primitive의
`consented_reference_embeddings` 인자명은 상위 gate 의도를 표현할 뿐
receipt를 검증하지 않는다. Production adapter가 consent/retention
attestation을 먼저 검증한 뒤에만 primitive를 호출해야 한다.

## 9. Exact acquisition gate

S104 종료와 별도 실행 승인 뒤에도 다음 순서를 모두 통과해야 한다.

### A0. 정책·권리 동결

1. Model ID와 revision을
   `microsoft/wavlm-base-plus-sv@feb593a6c23c1cc3d9510425c29b0a14d2b07b1e`
   로 고정한다. `main`을 acquisition identity로 사용하지 않는다.
2. 위 5개 path/size/blob/LFS/Xet/small-file SHA를 canonical allowlist로
   고정한다.
3. UniLM code, Transformers code, checkpoint와 training-data를 분리한
   license evidence를 외부 human/legal reviewer가 승인한다.
4. Checkpoint research use, local retention, derived embedding,
   publication과 redistribution을 각각 승인하고
   `research_use_authorized=true`를 서명하기 전에는 다운로드하지 않는다.
5. m8의 정확한 construct 이름, multi-reference 집계, normalization,
   missing/nonfinite, threshold 사용 여부와 minimum support를 결과 전에
   사전등록한다. Model-card `0.86`을 채택하지 않는다.

### A1. 격리 staging과 byte 검증

1. Credential이 없고 executable 권한이 없는 별도 staging에 exact
   revision의 allowlist 5개만 받는다.
2. Downloader, API version, UTC, model ID, revision과 응답 metadata를
   canonical acquisition record에 남긴다.
3. 각 작은 파일의 local byte SHA-256을 위 표와 비교한다.
4. Checkpoint local size가 정확히 404,547,053 bytes이고 SHA-256이
   `e906bce2fa42fb497a1d1a9ecf81548adb7e03b12a5644e32d2f42f0d6500fad`
   인지 load 전에 확인한다.
5. 하나라도 다르면 bundle 전체를 quarantine하고 해석·재시도·fallback
   checkpoint 선택을 하지 않는다.
6. Snapshot symlink가 cache root 밖으로 나가지 않는지 확인하고
   Git 추적·공개·덮어쓰기를 금지한다.
7. Canonical local manifest에 실제 local SHA, size, source URL,
   revision과 license evidence를 묶어 manifest byte SHA-256을 만든다.

### A2. Safe offline runtime

1. S104 환경을 변경하지 않고 별도 m8 environment를 exact lock과
   installation artifact hash로 만든다.
2. 선택한 Transformers source가 승인한 commit/source SHA와 일치하는지,
   `WavLMForXVector` pooling/head가 본 문서 계약과 일치하는지 다시
   확인한다.
3. Checkpoint hash 검증 전 pickle load를 금지한다. 최초 load는
   credential·secret·network가 없고 read-only input을 쓰는 격리 process에서
   수행한다.
4. `trust_remote_code=false`, local snapshot path,
   `local_files_only=true`, `HF_HUB_OFFLINE=1`,
   `TRANSFORMERS_OFFLINE=1`을 강제하고 egress trace가 0인지 확인한다.
5. Strict state-dict load의 missing, unexpected, mismatched key가 모두
   0이어야 한다. 임의 `ignore_mismatched_sizes`, random head,
   fallback model은 금지한다.
6. Model class, config/preprocessor/checkpoint/source/runtime hash,
   parameter count, dtype, device, eval mode와 offline evidence만
   aggregate smoke report에 기록한다.

Environment variable만으로 network 차단을 증명하지 않는다. OS-level
egress 차단 또는 syscall/network trace를 함께 남긴다.

## 10. Exact smoke와 validity gate

### S0. Synthetic CPU contract smoke

최초 smoke는 GPU와 human audio 없이 CPU에서 한다.

1. 고정 seed로 생성한 16 kHz mono float32, finite, 1초 이상 synthetic
   waveform을 메모리에서만 사용한다.
2. Caller가 sample rate를 명시하고 16 kHz가 아니면 adapter가 자동
   resample하지 않고 실패해야 한다.
3. `model.eval()`과 inference-only context에서 output embedding shape가
   `[batch, 512]`, finite, nonzero인지 확인한다.
4. 동일 입력 반복의 embedding과 L2-normalized embedding digest가
   사전등록 tolerance 안에서 일치해야 한다.
5. Single-item 실행과 서로 다른 길이를 right-pad한 batch 실행을
   비교한다. Attention-mask/statistic-pooling 차이가 tolerance를 넘으면
   production은 batch padding을 금지하고 one-utterance execution으로
   고정하거나 원인을 감사한다.
6. 빈 입력, stereo, NaN/Inf, 너무 짧은 입력, 잘못된 sample rate가 모두
   fail closed하는 adapter contract를 검증한다.
7. 실제 embedding vector, waveform, path, speaker/candidate/reference
   ID를 report에 저장하지 않는다.
8. Synthetic score 간 차이는 speaker, style 또는 한국어 품질 근거로
   해석하지 않는다.

S0는 artifact와 runtime wiring만 증명한다. 논문 metric 완료가 아니다.

### S1. Consented non-locked Korean technical smoke

S0 뒤에도 D08/learner consent와 biometric gate가 통과하고 사전등록된
sample/support가 있어야만 실제 audio를 연다.

- train/dev의 consented non-locked audio만 사용하고 locked test,
  S1/S2 label/prediction과 원시 identity를 읽지 않는다.
- 16 kHz conversion policy, channel, amplitude, silence/length,
  segmentation과 multiple-take aggregation을 source-blind하게 고정한다.
- Same-speaker/different-speaker, same-content/different-content,
  microphone/noise/codec, L1/L2 Korean slice를 분리 보고한다.
- Model-card threshold를 쓰지 않고 dev speaker-disjoint protocol에서
  continuous distribution, EER/ROC 같은 speaker-verification 성질과
  group uncertainty를 먼저 확인한다.
- 실제 output embedding은 D1에 두고, 공개 후보는 count·CI와
  승인된 aggregate만 사용한다.

이 단계도 style 타당도를 증명하지 않는다.

### S2. Style construct validation

`speaker/style similarity`라는 PDF 표현을 논문 측정값으로 사용하려면
speaker identity와 style을 분리하는 추가 실험이 필요하다.

1. 동일 화자의 서로 다른 speaking style과 서로 다른 화자의 유사한
   style을 포함하는 crossed design을 사전등록한다.
2. 문장/content, 녹음장비, 속도·pitch와 화자를 분리하거나 통계적으로
   통제한다.
3. 독립 한국어 전문가의 blind style/자연스러움 rating과 WavLM-SV
   cosine의 연관, within/between-speaker 성분과 불확실성을 보고한다.
4. m3–m6 duration/F0/energy/rhythm metric, ASR/phone correctness와
   incremental validity를 비교한다.
5. 성별·연령·억양·L1·마이크·noise·codec·human/TTS·spoof/OOD slice를
   보고하고, subgroup support가 부족하면 결론을 내리지 않는다.
6. Style construct validation이 실패하면 m8을 speaker-embedding
   auxiliary metric으로만 남기고 style claim과 optimization reward에서
   제외한다.

높은 WavLM-SV cosine이 특정 reference 화자의 identity를 복제하도록
생성기를 유도할 위험도 별도 점검한다. 본 연구의 개선 목표는 학습자의
발음·운율 코칭이지 특정 사람의 생체 정체성 모사로 바꾸면 안 된다.

## 11. m8 완료 판정과 현재 미확인 목록

| Gate | 현재 상태 |
|---|---|
| exact HF revision/file metadata | 확인 |
| small-file local byte hash | 확인 |
| checkpoint LFS SHA/HEAD consistency | 확인 |
| checkpoint local byte/hash | 미확인, 다운로드 0 |
| UniLM/Transformers code license text | 확인 |
| checkpoint license scope | 미확인 |
| research-use/legal approval | false |
| exact hashed runtime installation | 미완료 |
| safe pickle load/offline proof | 미실행 |
| synthetic CPU smoke | 미실행 |
| consented reference/learner audio | 0 |
| actual embedding/cosine | 0 |
| Korean SV validity | 0 |
| style construct validity | 0 |
| subgroup/spoof/OOD audit | 0 |
| m8 paper result/completion | 0 |

따라서 지금 허용되는 주장은 다음뿐이다.

> WavLM Base+ SV upstream artifact의 exact public metadata, config
> architecture, source와 license 표기 위치를 read-only로 감사했고,
> acquisition·consent·offline smoke·한국어/style 타당도 gate를
> 명시했다.

다음 주장은 금지한다.

- WavLM-SV 설치·실행·한국어 검증 완료
- m8 score, threshold, EER, style 또는 native-likeness 결과 존재
- WavLM-SV가 한국어 화자/스타일 평가에 타당함
- speaker cosine이 발음 정확성·자연스러움·학습 효과를 증명함
- checkpoint의 연구 사용·재배포 권리가 승인됨
- D08 reference와 learner audio의 biometric consent가 완료됨
- Phase 1 또는 논문 실험 완료
