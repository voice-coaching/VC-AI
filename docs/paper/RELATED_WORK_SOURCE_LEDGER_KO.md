# 관련연구 원문 source ledger

- 감사 기준일: 2026-07-29 KST
- 대상: `docs/paper/CLAIM_GAP_MAP_KO.md`의 R01–R08
- 목적: WP10 P04/P05에서 사용할 수 있는 1차 자료와 사용할 수 없는 주장을
  분리하고, 논문·공식 저장소·실제 라이선스 파일의 출처를 고정한다.
- 범위 제한: 이 문서는 관련연구 source 감사 결과이지 본 연구의 실험 결과나
  모델 승격 증거가 아니다.
- 편집 범위: 이번 감사에서는 이 ledger만 작성했다. 원고, evidence-map,
  tracker, 모델·실험 파일은 수정하지 않았다.

## 1. 판정 규칙과 요약

| 상태 | 의미 |
|---|---|
| `verified` | 1차 자료가 현재 statement를 직접 지지한다. 단, 아래에 적은 데이터·과제 범위를 넘겨 일반화하지 않는다. |
| `partially_verified` | 후보 모델·구조·도구의 존재는 확인됐지만 한국어 L2 발음평가 또는 본 연구의 효과는 직접 지지하지 않는다. |
| `unverified` | 핵심 문장을 직접 검증한 1차 자료를 확인하지 못했다. 원고의 사실 문장으로 승격하지 않는다. |

| ID | 감사 결과 | 원고에서 허용되는 최소 범위 |
|---|---|---|
| R01 | `partially_verified` | WavLM Large와 layer-wise weighted representation을 후속 후보로 제시 |
| R02 | `partially_verified` | HiPAMA의 정확한 논문·코드는 확인; 영어 SpeechOcean762에서 검증된 계층·multi-aspect 구조로 한정 |
| R03 | `partially_verified` | Whisper는 transcript/WER 후보, HuBERT는 별도 SSL representation 후보라는 역할 구분 |
| R04 | `verified` — 공개 여부만 | CosyVoice 2 이후 모델과 공식 저장소의 CosyVoice2 GRPO recipe가 공개돼 있음 |
| R05 | `unverified` | multi-reference와 TTS reference의 선행 사용만 인용; 특정 robust aggregation의 우월성은 ablation 동기 |
| R06 | `verified` — 공식 SWivid artifact만 | F5-TTS 코드는 MIT, 공식 pretrained weights는 CC-BY-NC-4.0으로 분리 |
| R07 | `unverified` | UTMOS의 OOD 위험을 이유로 인간평가를 병행한다는 제한만 허용; L2 억양 과벌점 단정 금지 |
| R08 | `unverified` | 기존 PDF의 달성형 문구는 계속 철회; 후속 내부 실험 artifact 없이는 인용으로 복구 불가 |

## 2. 논문 원문·인용 메타데이터 원장

아래 URL은 모두 저자 제출 arXiv 원문, 출판사 DOI/프로시딩 또는 공식
저장소다. arXiv와 출판본이 같은 연구인 경우 참고문헌에는 출판본 하나만
두고 arXiv를 원문 확인용으로 사용한다.

이 절의 `Source P01`–`Source P14`는 참고문헌 원장 내부 ID다. 예를 들어
`Source P04`는 HuBERT 논문이며 tracker의 논문 편집 task `WP10-P04`와
다르다.

| Source ID | 정확한 메타데이터 | DOI·원문 | 확인일 |
|---|---|---|---|
| P01 | Sanyuan Chen et al., “WavLM: Large-Scale Self-Supervised Pre-Training for Full Stack Speech Processing,” *IEEE JSTSP* 16(6), 1505–1518, 2022 | DOI [10.1109/JSTSP.2022.3188113](https://doi.org/10.1109/JSTSP.2022.3188113), [arXiv:2110.13900](https://arxiv.org/abs/2110.13900) | 2026-07-29 |
| P02 | Heejin Do, Yunsu Kim, Gary Geunbae Lee, “Hierarchical Pronunciation Assessment with Multi-Aspect Attention,” *ICASSP 2023*, 1–5 | DOI [10.1109/ICASSP49357.2023.10095733](https://doi.org/10.1109/ICASSP49357.2023.10095733), [arXiv:2211.08102](https://arxiv.org/abs/2211.08102) | 2026-07-29 |
| P03 | Alec Radford et al., “Robust Speech Recognition via Large-Scale Weak Supervision,” 2022 | [arXiv:2212.04356](https://arxiv.org/abs/2212.04356) | 2026-07-29 |
| P04 | Wei-Ning Hsu et al., “HuBERT: Self-Supervised Speech Representation Learning by Masked Prediction of Hidden Units,” *IEEE/ACM TASLP* 29, 3451–3460, 2021 | DOI [10.1109/TASLP.2021.3122291](https://doi.org/10.1109/TASLP.2021.3122291), [arXiv:2106.07447](https://arxiv.org/abs/2106.07447) | 2026-07-29 |
| P05 | Zhihao Du et al., “CosyVoice 2: Scalable Streaming Speech Synthesis with Large Language Models,” 2024 | [arXiv:2412.10117](https://arxiv.org/abs/2412.10117) | 2026-07-29 |
| P06 | Zhihao Du et al., “CosyVoice 3: Towards In-the-wild Speech Generation via Scaling-up and Post-training,” 2025 | [arXiv:2505.17589](https://arxiv.org/abs/2505.17589) | 2026-07-29 |
| P07 | Zhihong Shao et al., “DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models,” 2024; GRPO를 PPO 변형으로 소개 | [arXiv:2402.03300](https://arxiv.org/abs/2402.03300) | 2026-07-29 |
| P08 | Jan Nouza, “Training Speech through Visual Feedback Patterns,” *ICSLP 1998* | DOI [10.21437/ICSLP.1998-821](https://doi.org/10.21437/ICSLP.1998-821) | 2026-07-29 |
| P09 | Yujia Xiao, Frank K. Soong, “Proficiency Assessment of ESL Learner’s Sentence Prosody with TTS Synthesized Voice as Reference,” *Interspeech 2017*, 1755–1759 | DOI [10.21437/Interspeech.2017-64](https://doi.org/10.21437/Interspeech.2017-64) | 2026-07-29 |
| P10 | Dorel Picovici, Abdulhussain E. Mahdi, “New Output-Based Perceptual Measure for Predicting Subjective Quality of Speech,” *ICASSP 2004* vol. 5, V-633–V-636 | DOI [10.1109/ICASSP.2004.1327190](https://doi.org/10.1109/ICASSP.2004.1327190) | 2026-07-29 |
| P11 | Yushen Chen et al., “F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching,” 2024/ICLR 2025 | [arXiv:2410.06885](https://arxiv.org/abs/2410.06885) | 2026-07-29 |
| P12 | Takaaki Saeki et al., “UTMOS: UTokyo-SaruLab System for VoiceMOS Challenge 2022,” *Interspeech 2022*, 4521–4525 | DOI [10.21437/Interspeech.2022-439](https://doi.org/10.21437/Interspeech.2022-439), [arXiv:2204.02152](https://arxiv.org/abs/2204.02152) | 2026-07-29 |
| P13 | Christoph Minixhofer, Ondřej Klejch, Peter Bell, “TTSDS2: Robust Objective Evaluation for Human-Quality Synthetic Speech,” *SSW 2025*, 68–75 | DOI [10.21437/SSW.2025-11](https://doi.org/10.21437/SSW.2025-11) | 2026-07-29 |
| P14 | Wen-Chin Huang, Nicholas Sanders, Erica Cooper, “CodecMOS-Accent: A MOS Benchmark of Resynthesized and TTS Speech from Neural Codecs Across English Accents,” 2026 preprint | [arXiv:2603.14328](https://arxiv.org/abs/2603.14328) | 2026-07-29 |

P07은 GRPO의 방법 출처이지 TTS에서의 유효성 근거가 아니다. P08–P10도
multi-reference 또는 robust distance의 인접 근거일 뿐, 본 연구의 정확한
aggregation을 검증한 논문으로 취급하지 않는다. P14는 2026년 arXiv
preprint이므로 동료평가 출판물로 표기하지 않는다.

## 3. 공식 저장소·모델·실제 LICENSE 감사

가변 `main`/`master` 대신 감사 시점 commit을 URL에 포함했다. SHA-256은
각 commit의 raw `LICENSE` 파일 바이트에 대해 계산했다.

| Artifact | 고정 source | 실제 LICENSE와 SHA-256 | 모델 weight 범위 판정 |
|---|---|---|---|
| WavLM/UniLM | [WavLM code @ `833df7e`](https://github.com/microsoft/unilm/tree/833df7e7832e5064a281131ee64a481afa8e5b95/wavlm), [Microsoft `wavlm-large` revision `c1423ed`](https://huggingface.co/microsoft/wavlm-large/tree/c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c) | [MIT LICENSE](https://github.com/microsoft/unilm/blob/833df7e7832e5064a281131ee64a481afa8e5b95/LICENSE), `904dc4d8749877f1dba1cda48200d2462dccbeb7c134d5e4ef6fa75e0198c8fe` | HF weight repo에는 SPDX metadata와 별도 LICENSE 파일이 없다. 모델 카드는 UniLM LICENSE로 연결하지만 weight 재배포 범위를 별도 문장으로 명시하지 않으므로 `unverified` |
| HiPAMA | [official author repo @ `89e3f65`](https://github.com/doheejin/HiPAMA/tree/89e3f650e224e27b76e87f9cfe4fca2a30b142ab) | [BSD-3-Clause LICENSE](https://github.com/doheejin/HiPAMA/blob/89e3f650e224e27b76e87f9cfe4fca2a30b142ab/LICENSE), `e8fad537e80143a933e2492e169be38742810a3d7f76452de2c826ffd11f419e` | 코드 저장소만 확인; 별도 공식 pretrained weight 배포는 이 감사 범위에서 확인하지 못함 |
| Whisper | [official repo @ `90fdc51`](https://github.com/openai/whisper/tree/90fdc51112dc59242201d038a48add97ffd24b5d) | [MIT LICENSE](https://github.com/openai/whisper/blob/90fdc51112dc59242201d038a48add97ffd24b5d/LICENSE), `b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d` | 공식 README가 code와 model weights 모두 MIT라고 명시 |
| HuBERT/fairseq | [HuBERT code/checkpoints @ `3d262bb`](https://github.com/facebookresearch/fairseq/tree/3d262bb25690e4eb2e7d3c1309b1e9c406ca4b99/examples/hubert) | [MIT LICENSE](https://github.com/facebookresearch/fairseq/blob/3d262bb25690e4eb2e7d3c1309b1e9c406ca4b99/LICENSE), `52412d7bc7ce4157ea628bbaacb8829e0a9cb3c58f57f99176126bc8cf2bfc85` | 공식 fairseq README가 MIT가 pretrained models에도 적용된다고 명시. 저장소는 2026-03-20 archive/read-only 상태 |
| CosyVoice | [official repo @ `074ca6d`](https://github.com/QwenAudio/CosyVoice/tree/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc), [CosyVoice2 GRPO recipe](https://github.com/QwenAudio/CosyVoice/tree/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/examples/grpo/cosyvoice2) | [Apache-2.0 LICENSE](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/LICENSE), `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` | [official HF CosyVoice2 weight revision `eec1ae6`](https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B/tree/eec1ae6c79877dbd9379285cf8789c9e0879293d)의 card metadata는 `apache-2.0`이지만 별도 LICENSE 파일은 없음 |
| F5-TTS | [official repo @ `9c614e9`](https://github.com/SWivid/F5-TTS/tree/9c614e9657089213efc6a7421b30630be138a3f5) | [MIT LICENSE](https://github.com/SWivid/F5-TTS/blob/9c614e9657089213efc6a7421b30630be138a3f5/LICENSE), `c70811d1402134de900e811d3eaa349eb60f4d38a8f635b27b1e378c630facd2` | 공식 README는 pretrained weights를 CC-BY-NC로 분리하고, [official HF revision `84e5a41`](https://huggingface.co/SWivid/F5-TTS/tree/84e5a410d9cead4de2f847e7c9369a6440bdfaca)의 metadata는 `cc-by-nc-4.0`. HF weight repo에는 별도 LICENSE 파일이 없음 |
| UTMOS22 | [official repo @ `2d6d612`](https://github.com/sarulab-speech/UTMOS22/tree/2d6d612cdf5bc610fde1a1b913c2d6c6fac8e063) | [MIT LICENSE](https://github.com/sarulab-speech/UTMOS22/blob/2d6d612cdf5bc610fde1a1b913c2d6c6fac8e063/LICENSE), `87785aa9e65eeec4be1a4ec8bf9ab16bf39a5564b03dc7972d4fe3858ede2e61` | LICENSE는 코드 저장소에 존재한다. 외부 checkpoint·demo weight의 별도 라이선스 파일 범위는 `unverified` |

CosyVoice 저장소의 과거 `FunAudioLLM/CosyVoice` URL은 현재
`QwenAudio/CosyVoice`로 redirect된다. 이 ledger는 redirect 후 canonical
repository와 commit을 기록했다. 모델 card의 license metadata는 실제 weight
repo의 선언이지만, 별도 LICENSE 파일이 없는 경우 이를 코드 LICENSE 파일과
동일한 법적 문서로 합쳐 쓰지 않는다.

## 4. R01 — WavLM Large·계층·fusion 후보

### 직접 지원 가능한 주장

- P01은 WavLM이 masked prediction과 denoising을 결합한 SSL speech
  representation이고, Base/Base+/Large를 포함하며 Large를 여러 speech
  benchmark에서 평가했음을 지지한다.
- 공식 WavLM README는 각 Transformer layer representation 추출 방법과
  layer representation의 weighted sum 사용 권고를 명시한다. 따라서
  “WavLM Large와 layer-wise fusion을 후속 후보로 둔다”는 설계 문장은
  인용 가능하다.
- 현재 프로젝트가 고정한 HF revision
  `c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c`는 공식 Microsoft model
  repository에서 실제 존재한다.

### 지원 불가·`unverified`

- frozen WavLM 또는 Large가 한국어 L2 phone 오류, word score, utterance
  ICC를 개선한다는 주장은 P01이 직접 지지하지 않는다.
- 공식 HF model card는 이 checkpoint를 영어 pretraining 모델로 설명한다.
  영어 benchmark 결과를 한국어 발음평가 성능으로 일반화할 수 없다.
- “계층·fusion이 유효하다”를 본 연구의 관측 결과처럼 쓰면 안 된다.
  실제 Base+/Large/head/fusion 3-seed ablation이 필요하다.
- MIT code LICENSE를 HF weight 재배포 허가로 자동 확대 해석하지 않는다.

**R01 상태: `partially_verified`.**

## 5. R02 — HiPAMA식 phone→word→utterance

### 정확한 식별

HiPAMA는 P02의 *Hierarchical Pronunciation Assessment with Multi-Aspect
Attention*이다. 정확한 DOI, 저자 공식 코드 저장소와 BSD-3-Clause LICENSE를
확인했으므로 논문 식별 자체는 더 이상 미확인이 아니다.

### 직접 지원 가능한 주장

- P02는 phoneme, word, utterance granularity를 계층적으로 표현하고 같은
  level의 aspect 관계를 multi-aspect attention으로 결합한다.
- SpeechOcean762의 영어 비원어민 5,000 utterance와 5회 run에서 기존
  비교군과 ablation을 수행했다는 범위까지 인용할 수 있다.

### 지원 불가·표현 교정

- “phone→word→utterance attention”은 하나의 attention 연산이 세 level을
  순차 통과한다는 뜻으로 읽힐 수 있다. 원문에 맞는 표현은
  “phoneme→word→utterance 계층 표현과 level별 multi-aspect attention”이다.
- WavLM embedding을 HiPAMA에 넣는 조합, 한국어 phone ontology, 본 연구의
  score label과 loss에서의 우월성은 P02가 검증하지 않았다.
- SpeechOcean762 결과를 현재 AI Hub cohort의 성능값 또는 채택 판정으로
  사용할 수 없다.

**R02 상태: `partially_verified`.**

## 6. R03 — Whisper/HuBERT 독립 감사 역할

### 직접 지원 가능한 주장

- P03과 공식 구현은 Whisper가 multilingual ASR·translation·language
  identification을 수행하는 transcript 생성 모델임을 지지한다. 따라서
  content consistency와 WER hard gate의 후보 도구로 두는 것은 원 모델
  task와 일치한다.
- P04는 HuBERT가 masked hidden-unit prediction으로 acoustic와 language
  structure를 학습하는 SSL representation임을 지지한다. S1과 다른
  representation family의 후보 backbone으로 둘 수 있다.
- Whisper code와 weights는 MIT, fairseq code와 pretrained models도 MIT라는
  범위가 공식 README와 실제 LICENSE에서 확인됐다.

### 지원 불가·`unverified`

- Whisper가 현재 한국어 L2 cohort에서 지정 WER gate를 만족한다는 결과는
  없다.
- “Whisper는 세밀한 phone·운율 감사를 할 수 없다”는 부정적 성능 단정도
  P03만으로는 지지되지 않는다. 현재 문서의 역할 제한은 독립성 확보를 위한
  설계 정책으로 표현한다.
- HuBERT가 별도 학습 없이 prosody auditor가 되거나 pairwise/ordinal head가
  인간 평가와 일치한다는 주장은 P04가 직접 지지하지 않는다.
- 동일 label·alignment·training data를 공유하면 backbone 이름이 달라도
  독립 감사가 되지 않을 수 있다. 독립성은 후속 실행 protocol로 증명한다.

**R03 상태: `partially_verified`.**

## 7. R04 — CosyVoice 2 이후 계열과 GRPO 공개 여부

### 직접 지원 가능한 주장

- P05는 CosyVoice 2, P06은 후속 CosyVoice 3를 서로 다른 공식 기술보고서로
  확인한다.
- 공식 repository commit `074ca6d…`에는
  `examples/grpo/cosyvoice2/`가 실제 존재한다. README는 veRL을 이용한
  CosyVoice2 LLM의 GRPO training recipe, reward server, training·export
  절차를 제공한다.
- P07은 GRPO라는 명칭과 PPO 변형의 원 출처를 확인하지만 TTS 효과 근거는
  아니다.

### 지원 불가·주의

- repository README에 제시된 CER 개선 수치는 공식 코드 설명이지만
  peer-reviewed CosyVoice 2/3 논문의 본 연구 결과가 아니다. 본 원고의
  성능값으로 옮기지 않는다.
- CosyVoice/GRPO가 한국어 학습자에게 더 좋은 reference를 만들거나 발음
  학습 효과를 높인다는 주장은 검증되지 않았다.
- 공개 recipe의 존재는 본 프로젝트에서 GRPO를 실행·완료했다는 뜻이 아니다.
- code LICENSE는 Apache-2.0이다. 공식 HF weight card도 Apache-2.0 metadata를
  가지지만 standalone weight LICENSE 파일은 없다는 사실을 함께 기록한다.

**R04 상태: 공개 존재 statement에 한해 `verified`; 효과는 `unverified`.**

## 8. R05 — multi-reference aggregation rationale

### 확인된 인접 1차 자료

- P08은 pronunciation training UI에서 여러 reference utterance를 보여 주는
  설계의 선행 사용을 보고한다.
- P09는 영어 학습자 문장 prosody를 native recording 또는 TTS reference와
  비교했고, 해당 데이터에서 두 reference 유형의 분류 정확도가 비슷했다고
  보고한다. 이는 TTS reference 후보의 가능성만 지지한다.
- P10은 별도의 non-intrusive speech-quality 문제에서 reference codebook에
  대한 `median minimum distance`를 사용했다. robust distance 집계가 speech
  분야에 전례가 있다는 인접 근거다.

### 직접 지원하지 않는 주장

- 위 논문들은 “품질이 낮은 reference 하나가 pure minimum을 부당하게
  낮춘다”를 한국어 L2 발음 bank에서 실험하지 않았다.
- `k-nearest median`과 `trimmed soft-min` 중 어느 것이 더 좋은지, trimming
  비율·temperature·k 값이 무엇인지 직접 지지하는 source를 확인하지 못했다.
- multi-reference가 단일 reference 대비 정상 발화 오탐을 20% 줄인다는
  문장은 사전 gate일 뿐 관측 결과가 아니다.

수학적으로 `min(d_1,…,d_n)`에 작은 거리 하나를 추가하면 값이 증가하지
않는다는 것은 정의에서 바로 따른다. 그러나 저품질 reference가 실제 embedding
공간에서 작은 거리를 만드는지는 경험적 가설이다. 따라서 pure minimum 위험은
ablation 동기로, `k-nearest median/trimmed soft-min`은 사전 지정 후보로만
표현하고 단일·minimum·median·trimmed soft-min을 같은 split에서 비교해야 한다.

**R05 상태: 핵심 aggregation 선택은 `unverified`.**

## 9. R06 — F5-TTS code/weight license 분리

### 직접 지원 가능한 주장

- P11과 저자 공식 SWivid repository가 F5-TTS 논문·구현·checkpoint의 대응을
  확인한다.
- commit `9c614e9…`의 실제 `LICENSE` 파일은 MIT다.
- 같은 commit의 공식 README는 pretrained models를 training data Emilia
  때문에 CC-BY-NC로 별도 선언한다.
- 공식 HF model repository revision `84e5a41…`의 card metadata는 더
  구체적으로 `cc-by-nc-4.0`이다. 따라서 “공식 F5-TTS code와 pretrained
  weights의 license가 다르다”는 문장은 직접 지지된다.

### 지원 불가·주의

- HF weight repository에는 standalone LICENSE 파일이 없다. 그러므로
  weight 조건은 README와 card metadata에서 확인한 선언이라고 정확히 적는다.
- 모든 F5 계열·제3자 fine-tune·처음부터 새 데이터로 학습한 weights가
  비상업이라는 일반화는 지원되지 않는다. artifact별 lineage와 data license를
  다시 감사해야 한다.
- 실행계획의 `F5R-TTS`가 공식 F5-TTS의 어느 code revision·weight를 뜻하는지
  현재 고정돼 있지 않다. 이 이름의 artifact 정체와 GRPO 구현은 `unverified`다.

**R06 상태: 공식 SWivid code/weight 분리에 한해 `verified`.**

## 10. R07 — UTMOS L2 억양 bias-risk

### 직접 확인된 범위

- P12는 UTMOS가 Blizzard/Voice Conversion Challenge의 MOS를 예측하도록
  개발됐고 in-domain과 별도 OOD track을 평가했음을 지지한다.
- P13은 20개 최근 voice-cloning system과 clean/noisy/wild/children domain의
  인간 MOS 비교에서 UTMOS의 cross-domain correlation이 일관되지 않았음을
  보고한다. 따라서 UTMOS 하나를 인간평가 대체물로 쓰지 않는 제한은
  근거가 있다.
- P14는 10개 영어 accent의 codec/TTS 자료에서 UTMOS와 subjective
  naturalness의 상관을 보고했으며, 해당 실험에서는 system-level 상관이
  높았다. 또한 listener와 speaker accent가 같은 경우의 인간평가 bias를
  분석했다.

### 지원 불가·상충 범위

- P12–P14 중 어느 것도 “UTMOS가 한국어 L2 억양을 과도하게 벌점한다”를
  직접 검증하지 않았다. 이 문장은 `unverified` 위험 가설이다.
- P13의 OOD 실패는 noisy/wild/children TTS domain이고 L2 accent 과벌점의
  직접 증거가 아니다.
- P14는 오히려 영어 accent TTS에서 UTMOS가 naturalness를 잘 예측한 범위를
  보고하므로 “UTMOS는 accent speech에 일반적으로 편향된다”는 포괄적
  단정도 허용하지 않는다. P14 자체도 동료평가 전 preprint이며 한국어
  학습자 음성이 아니다.
- 필요한 해제 조건은 L1·숙련도·성별 slice별 인간 MOS/CMOS와 UTMOS residual,
  rank reversal, calibration을 본 데이터에서 비교하는 것이다.

따라서 원고에는 “UTMOS의 domain generalization이 보장되지 않으므로 artifact
gate로만 사용하고 blind 인간평가를 병행한다”고 쓸 수 있다. “L2 억양을
과벌점한다”는 결과 문장은 쓸 수 없다.

**R07 상태: L2 억양 과벌점 주장은 `unverified`.**

## 11. R08 — 기존 PDF의 달성형 주장

감사 대상 로컬 PDF:

- 경로: `docs/한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처.pdf`
- SHA-256:
  `eb403f04e9df77932baba9c671c4e7ee6b2adc2a7eb31bbdc43970e321907ebb`
- PDF metadata author: `Perplexity Computer`
- 생성일: 2026-07-25 15:45:48 KST

PDF 결론의 “WavLM-large + HiPAMA로 정확도 상한을 확장” 및
“F5R-TTS(GRPO) 로드맵으로 온라인 루프를 확보”는 프로젝트 내부 달성
주장이다. P01–P14 같은 외부 관련연구는 구성요소의 존재와 선행 실험만
지지할 수 있으며, 이 프로젝트가 정확도 상한을 확장했거나 online loop를
구현·검증했다는 사실을 증명할 수 없다.

이 문구를 해제하려면 적어도 실제 S101–S108 3-seed comparison,
fusion/HiPAMA ablation, R04 blind reference 평가, 고정된 F5R/GRPO artifact와
reward-hacking audit가 필요하다. 그 전에는 현재 원고처럼 명시적으로
철회하고 `[PENDING]`으로 유지한다.

**R08 상태: `unverified`; 외부 citation으로 복구 불가.**

## 12. 링크 접근·중복·인용 감사 결과

- paper/repository/license/model URL 36개(중복 URL 0개)를 2026-07-29에
  직접 요청했다. 32개는 HTTP 200, IEEE DOI 4개는 DOI가 정확한 IEEE document로
  redirect된 뒤 bot-aware HTTP 202를 반환했다. DOI와 document ID의 대응은
  Crossref/출판사 metadata와 일치했다.
- F5-TTS의 OpenReview URL은 browser verification 화면으로 redirect되어
  본문 접근을 자동 검증할 수 없었다. 따라서 이 ledger의 P11 본문 근거는
  접근 가능한 저자 arXiv 원문을 사용했다.
- 같은 연구의 중복 후보는 P01 WavLM(arXiv/IEEE), P02 HiPAMA(arXiv/IEEE),
  P04 HuBERT(arXiv/IEEE), P12 UTMOS(arXiv/ISCA)다. 최종 참고문헌에서는
  각각 출판본 한 건으로 deduplicate한다.
- P05 CosyVoice 2와 P06 CosyVoice 3는 후속 관계이지만 서로 다른 논문이므로
  중복이 아니다. P07 DeepSeekMath는 GRPO 정의용이며 CosyVoice TTS 결과의
  citation으로 대체하지 않는다.
- repository README의 self-reported 성능, model card, LICENSE는 각각
  “공개 기능”, “artifact metadata”, “사용 조건” 확인에만 사용한다.
  peer-reviewed 성능 근거와 섞지 않는다.

## 13. WP10-P04/P05 인용 gate

1. `verified` 또는 `partially_verified`의 “직접 지원 가능한 주장”만
   관련연구 문장 후보로 사용한다.
2. `unverified` 문장은 사실형 종결로 쓰지 않고 위험 가설, ablation 동기,
   사전 gate 또는 `[PENDING]`으로 유지한다.
3. 모델 이름만 인용하지 말고 정확한 논문 revision/DOI, code commit,
   weight revision과 artifact별 license를 함께 고정한다.
4. 한국어 L2 cohort에 대한 성능·공정성·학습 효과는 외부 논문이 아니라
   본 연구의 hashed aggregate artifact로만 해제한다.
5. P05 최종 감사에서 참고문헌 deduplication, DOI resolve, URL 접근,
   citation-to-claim 범위와 license appendix의 code/weight/data 분리를
   다시 확인한다.
