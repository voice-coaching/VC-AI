# Phase 1 m1/m3 한국어 MFA 환경·아티팩트 게이트 독립 감사

기준 시각: 2026-07-29 KST

플랫폼: Ubuntu 24.04, Linux `aarch64`, CPython 3.12.3

판정: **BLOCKED — 설치·모델 획득·정렬·DTW·성능 실험 0**

## 1. 결론

PDF가 Phase 1에서 요구하는 MFA 경로는 아직 구현·실행되지 않았다.

- m1은 `phone-CTC | XLS-R/WavLM + g2pK/MFA`를,
- m3은 `duration / pause | MFA + DTW`를 요구한다.

현재 저장소에는 관측 TextGrid phone용 CTC 준비 코드와
energy-threshold pause/gold-interval duration proxy가 있을 뿐, MFA나
DTW production 경로는 없다. 로컬에는 Conda 계열 도구와 MFA runtime도
없다.

더 중요한 차단점은 플랫폼이다. 공식 설치 경로는 Conda Forge인데,
`montreal-forced-aligner==3.4.1`의 필수 의존성인 `kalpy>=0.10`에는
2026-07-29 조회 기준 `linux-aarch64` 빌드가 하나도 없다. MFA 본체가
`noarch`이고 Kaldi/Pynini에는 ARM64 빌드가 있어도 이 의존성 공백 때문에
이 머신에서 공식 prebuilt Conda 경로가 성립했다고 볼 수 없다. 실제
solver는 로컬에 Conda가 없어 실행하지 않았으며, 이 판정은 공식 설치
문서와 Conda Forge 배포 메타데이터에 대한 정적 실패 판정이다.

공식 한국어 모델도 바로 사용하지 않는다.

1. MFA 3.4용 Hugging Face bundle은 정확한 commit으로 고정할 수 있지만,
   모델 카드와 실제 dictionary/acoustic/G2P metadata 사이에 수량·버전
   불일치가 있고, 문서가 설명하는 bundle-level `LICENSE` 파일이 실제
   snapshot에 없다.
2. legacy v3.0.0 release는 세 archive 모두 upstream digest를 제공하지
   않는다. 또한 공식 Korean G2P v3.0.0 페이지가 held-out WER/PER를 각각
   `100.00%`로 보고한다.
3. MFA는 transcript와 pronunciation dictionary에 조건화된 **강제정렬기**다.
   출력 phone과 경계는 독립적인 실제 발음 관측 정답이 아니며, 공식
   3.4 model-format 문서도 pretrained model의 범위 밖에 발음
   goodness 평가를 둔다.

따라서 이 감사에서는 설치, Conda 환경 생성, 모델/weight archive 다운로드,
AI Hub audio 접근, 정렬, DTW, metric 계산을 수행하지 않았다. 다음 단계는
ARM64를 우회할 격리된 x86_64 CPU runner와 artifact/license 승인을 먼저
확정한 뒤, G2P 자동 보완을 끈 non-locked micro-smoke를 실행하는 것이다.

## 2. 감사 범위와 비수행 항목

### 읽은 내부 근거

- 원문 PDF:
  [`한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처.pdf`](../한국어%20AI%20보이스%20코칭을%20위한%20폐쇄루프%20TTS%20아키텍처.pdf)
- 실행 matrix:
  [`PDF_REQUIREMENTS_EXECUTION_MATRIX_KO.md`](PDF_REQUIREMENTS_EXECUTION_MATRIX_KO.md)
- 기존 Phase 1 환경 감사:
  [`PHASE1_TOOL_ENV_AUDIT_20260729_KO.md`](PHASE1_TOOL_ENV_AUDIT_20260729_KO.md)
- AI Hub 기대/관측 정렬 감사:
  [`AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`](../AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md)
- TextGrid target policy:
  [`textgrid_target_policy.yaml`](../../seungun/configs/textgrid_target_policy.yaml)
- 데이터·privacy 경계:
  [`DATA_CARD_AIHUB71490_KO.md`](../DATA_CARD_AIHUB71490_KO.md),
  [`DATA_PRIVACY_BOUNDARY_KO.md`](../DATA_PRIVACY_BOUNDARY_KO.md)

### 수행한 읽기 전용 점검

- OS/architecture/Python 확인
- PATH와 알려진 project venv에서 Conda/Mamba/MFA 패키지 부재 확인
- MFA 공식 설치·model-version·alignment·validation 문서 확인
- MFA 공식 GitHub release/tag와 Conda Forge 패키지 metadata 확인
- 공식 MFA Models catalog와 release asset metadata 확인
- 공식 `MontrealCorpusTools/korean_mfa` Hugging Face repository의
  commit/tree/LFS pointer/small text metadata 확인

### 수행하지 않은 항목

- Conda/Mamba/Micromamba 설치 또는 환경 생성
- MFA/Kaldi/Kalpy/Pynini 설치
- model archive 또는 acoustic/G2P weight 다운로드
- AI Hub 및 기타 실제 음성 열기
- `mfa validate`, `mfa align`, `mfa align_hf`, G2P, DTW 실행
- source build, Docker pull, QEMU/x86 emulation
- train/dev/locked-test metric 또는 논문 결과 생성

## 3. PDF 요구와 현재 구현도

PDF의 표와 architecture는 다음을 명시한다.

| ID | PDF 요구 | 현재 확인 상태 | 판정 |
|---|---|---|---|
| m1 | phone-CTC, XLS-R/WavLM + g2pK/MFA, 내용·음소 일치 | observed TextGrid phone CTC 준비 코드·manifest·43-token processor는 있으나 G04 pending, checkpoint 0, g2pK/MFA 경로 없음 | 미완료 |
| m3 | duration/pause, MFA + DTW | energy threshold pause와 gold interval duration proxy만 존재, MFA/DTW source 없음 | proxy only |

`rg`로 `src/`, `seungun/src/`, `scripts/`, `configs/`, `tests/`,
`pyproject.toml`, `requirements/`를 검사했으나 MFA import/CLI adapter나
DTW 구현은 확인되지 않았다. 이 감사가 환경·artifact gate를 설계했다고
해서 m1/m3 구현도가 올라가지는 않는다.

PDF의 `g2pK`와 MFA bundle에 포함된 Phonetisaurus G2P가 같은 구현이라는
근거도 없다. 이 문서의 MFA G2P artifact 감사는 별도 g2pK 구현 완료로
계산하지 않는다.

## 4. 로컬 환경 증거

실행한 읽기 전용 확인의 핵심 결과는 다음과 같다.

```text
Linux
aarch64
Python 3.12.3
conda: NOT_FOUND
mamba: NOT_FOUND
micromamba: NOT_FOUND
mfa: NOT_FOUND
mfa_update: NOT_FOUND
montreal-forced-aligner=NOT_INSTALLED
kalpy=NOT_INSTALLED
pynini=NOT_INSTALLED
praatio=NOT_INSTALLED
```

`/home/cmsl-dgx01/miniconda3`, `/home/cmsl-dgx01/miniforge3`,
`/home/cmsl-dgx01/mambaforge`, `/opt/conda`도 없었다. `.venv-scorer`에서도
위 네 Python package는 모두 없었다. 이는 검사한 PATH·고정 경로·venv의
부재 증거이며, 파일시스템 전체에 동일한 이름의 비활성 환경이 절대 없다는
주장은 아니다.

## 5. MFA runtime과 ARM64 설치 게이트

### 5.1 공식 안정 runtime pin

[MFA 공식 설치 문서](https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html)는
Conda Forge 환경을 표준 설치 경로로 제시하고 Mamba 사용을 권장한다.
2026-07-29 확인한 안정 release는 다음과 같다.

| 항목 | 고정값 |
|---|---|
| MFA release | `v3.4.1` |
| published | `2026-07-11T20:31:49Z` |
| Git tag commit | `f01293108ac1c1025baf5f82ad4820dfb2e21598` |
| Conda package | `noarch/montreal-forced-aligner-3.4.1-pyhd8ed1ab_0.conda` |
| Conda size | `11,580,774` bytes |
| Conda SHA-256 | `c77bd22e5c8f39ffc8637202bc779a55c20a58df27d5c9ebccfb179e0aed7613` |
| 필수 핵심 의존성 | `kaldi 5.5.1172.*`, `kalpy >=0.10`, `openfst`, `pynini` |
| source LICENSE | MIT text, SHA-256 `ffaac053ec76f9df77885df9102299bf0950f3ca5b3dfd04b898aee0c77bc605` |

공식 근거:

- [MFA v3.4.1 release](https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/releases/tag/v3.4.1)
- [Conda Forge MFA metadata API](https://api.anaconda.org/package/conda-forge/montreal-forced-aligner)
- [tag-pinned LICENSE](https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/blob/f01293108ac1c1025baf5f82ad4820dfb2e21598/LICENSE)

### 5.2 `linux-aarch64` 정적 의존성 판정

Conda Forge metadata snapshot은 다음을 보였다.

| package | 필요한/확인 버전 | `linux-aarch64` |
|---|---:|---|
| `montreal-forced-aligner` | 3.4.1 | 본체는 `noarch` |
| `kaldi` | 5.5.1172 | 있음 |
| `pynini` | 2.1.7 | 있음 |
| `kalpy` | >=0.10, 최신 0.10.4 | **없음** |

`kalpy` package에 등록된 전체 subdir은 `linux-64`, `osx-64`,
`osx-arm64`, `win-64`였고, `linux-aarch64`는 없었다.
`kalpy==0.10.4`의 Linux build도 `linux-64`뿐이다.

근거:

- [Conda Forge Kalpy metadata API](https://api.anaconda.org/package/conda-forge/kalpy)
- [Conda Forge Kaldi metadata API](https://api.anaconda.org/package/conda-forge/kaldi)
- [Conda Forge Pynini metadata API](https://api.anaconda.org/package/conda-forge/pynini)

따라서 현재 머신에서 다음 명령을 실행할 준비가 됐다고 주장할 수 없다.

```bash
mamba create -n aligner -c conda-forge \
  montreal-forced-aligner=3.4.1
```

MFA 문서는 binary가 동작하지 않으면 Kaldi/OpenFst/MFA source build를
시도할 수 있다고 설명한다. 그러나 source build는 공식 prebuilt와 다른
compiler/ABI/supply-chain 검증 과제이며 이 문서의 “안정 설치 경로”로
간주하지 않는다. 별도 승인 없이 이 머신에 compiler stack이나 source
build를 추가하지 않는다.

### 5.3 허용 가능한 다음 runtime 경로

첫 실행 후보는 **격리된 native `linux-64` CPU runner**다. 다음을 모두
충족해야 한다.

1. `MFA 3.4.1`, `Kalpy 0.10.4`, `Kaldi 5.5.1172 CPU`,
   Python minor와 전체 transitive package를 explicit lock한다.
2. solver 결과의 channel URL, filename, build string, byte size,
   SHA-256을 manifest로 고정한다.
3. `mfa --version`, `mfa align_hf --help`, `mfa validate --help`를
   설치 환경 자체에서 캡처한다.
4. `MFA_ROOT_DIR`와 temporary/output directory를 Git 밖의 제한
   workspace로 고정한다.
5. ARM64 host의 QEMU/emulation 또는 Docker image architecture는 별도
   검증 전 사용하지 않는다.

이 경로도 아직 환경을 만들거나 solve하지 않았으므로 “설치 성공”이 아니다.

## 6. 현재 3.4 형식 한국어 bundle 감사

MFA 3.4는 legacy zip 세 개 대신 acoustic model, dictionary, G2P를 한
Hugging Face directory로 묶는 새 형식을 도입했다. 공식
[MFA model versions 문서](https://montreal-forced-aligner.readthedocs.io/en/stable/user_guide/models/model_versions.html)는
repo/revision을 `MODEL@revision` 형태로 고정할 수 있다고 설명한다.

### 6.1 정확한 snapshot

| 항목 | 값 |
|---|---|
| repository | `MontrealCorpusTools/korean_mfa` |
| tag | `v3.3.0` |
| exact commit | `0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd` |
| last modified | `2026-06-09T20:00:55Z` |
| 공개/gated | public / not gated |
| files | 14 |
| total bytes | `86,610,461` |
| LFS files/bytes | 4 / `85,626,926` |
| repository tag | `license:cc-by-4.0` |

근거:

- [exact repository revision](https://huggingface.co/MontrealCorpusTools/korean_mfa/tree/0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd)
- [model API](https://huggingface.co/api/models/MontrealCorpusTools/korean_mfa)
- [revision tree API](https://huggingface.co/api/models/MontrealCorpusTools/korean_mfa/tree/0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd?recursive=true&expand=true)

고위험 파일의 server-declared LFS OID는 SHA-256이다.

| 파일 | bytes | SHA-256/LFS OID |
|---|---:|---|
| `acoustic/final.alimdl` | 33,868,683 | `30f0ae1f6117978348b8d8c0b1130105cf856b6932bcf6e7eee1b9e444e07c84` |
| `acoustic/final.mdl` | 33,868,683 | `e26c3ffd8b7ff641d4a4026b4f144d5dd5c9b255a0a95b4e4e585f39d0f7c2e5` |
| `acoustic/tree` | 344,276 | `31e25e8a47cfe489edb9319ad10dd973e8c6afbad97853deb09509302ad11be8` |
| `g2p/korean_mfa/model.fst` | 17,545,284 | `f9c7ad663bae63a324aa22a5cec2344b0fd29b71f06d246b4126851aac24434c` |

small text artifact는 저장하지 않고 exact revision에서 stream으로 읽어
다음 SHA-256을 확인했다.

| 파일 | bytes | SHA-256 |
|---|---:|---|
| `README.md` | 9,795 | `f726e12aa9c5e74d299db4f7bef5f7998c34b715ef1476fb1aa7bc05f49540f2` |
| `acoustic/meta.json` | 9,730 | `0fb6c26ceda3e65934d9186a2d832630316a6e374fca7c69fcbb9b40ef754aaf` |
| `dictionary/korean_mfa.dict` | 934,391 | `49e223fddb518bc441baa4cb9fec1a108e80dae9a2b54e5834dbff30e89c7d34` |
| `dictionary/rules.yaml` | 2,955 | `3edde0dd28478489174844ca40a65f1638b99347f75426a5e33d6fdaa4d2ec91` |
| `g2p/korean_mfa/meta.json` | 2,344 | `0ee77a72d521c9f021674a96ac532705fdf5e64587311b24035a34366418194c` |

weight body는 읽거나 로컬에 저장하지 않았다. 위 네 weight digest는
Hugging Face tree API의 LFS metadata이며, 실제 획득 뒤 local SHA-256
재검증이 별도로 필요하다.

### 6.2 metadata 불일치

같은 exact commit 안에서도 다음 차이가 있다.

| 항목 | model card | 실제 pinned file |
|---|---:|---:|
| dictionary words | 79,188 | 21,005 pronunciation rows, 17,971 unique first-column tokens |
| phones | 95 | acoustic meta 107, G2P meta 107, dictionary 사용 symbol 105(`spn` 포함) |
| graphemes | 2,250 | G2P meta 66 |
| acoustic version | 미기재 | 3.3.0 |
| G2P version | 미기재 | 3.2.0 |
| G2P evaluation | 미기재 | 5,348 words, WER/PER both `null` |

dictionary와 acoustic inventory의 집합 차이도 있다.

- dictionary에만 있는 symbol: `spn`
- acoustic/G2P에는 있으나 dictionary pronunciation row에 없는 phone:
  `p͈ʷ`, `p͈ː`, `s͈ː`

이 차이가 runtime incompatibility라는 결론을 내리지는 않는다. acoustic와
G2P의 107-phone set은 서로 같고, dictionary가 모든 acoustic phone을
실제로 사용해야 하는 것은 아니다. 다만 “model card 수치가 맞다”거나
“dictionary/acoustic/G2P 호환 검증이 끝났다”고 주장할 수 없으므로
실제 `mfa validate`와 strict inventory audit 전에는 fail closed한다.

### 6.3 license/provenance 차단점

model card front matter와 API tag는 `cc-by-4.0`을 선언한다. 그러나 exact
snapshot의 14-file tree에는 `LICENSE`가 없고, exact revision의
`LICENSE` URL은 404다. MFA 3.4 model-format 문서는 bundle 구조에
`LICENSE`가 있으며 모델 라이선스 정보를 담는다고 설명한다.
또한 README가 연결하는 `config.yaml`도 snapshot에 없고 exact URL은
404다.

모델 카드가 열거한 학습 corpus에는 ELRA, CC BY-NC-ND 4.0,
CC BY-NC-SA 4.0 자료가 포함된다. 이것만으로 model의 CC BY 4.0 선언이
틀렸다고 단정하지는 않지만, 코드 MIT·모델 metadata CC BY 4.0·각 학습
corpus 권리는 서로 다른 층이다. 연구/재배포/상업 사용 가능성을 자동으로
승인해서는 안 된다.

현재 판정은 다음과 같다.

- exact artifact pin 가능: **예**
- weight body 검증 완료: **아니오**
- bundle-level license text 확보: **아니오**
- model card와 실제 content 일치: **아니오**
- G2P held-out 성능 확인: **아니오**
- 실험 사용 승인: **아니오**

## 7. Legacy Korean MFA v3.0.0 감사

기존 공식
[MFA Models catalog](https://mfa-models.readthedocs.io/en/latest/)의
한국어 세 artifact는 모두 v3.0.0이며, 각 페이지는 compatible MFA
version을 `v3.0.0`으로 표시한다. 세 release tag는 같은 exact commit
`f76a59f7491eadda0fee212b329521e20e349e75`를 가리킨다.

| 종류 | release tag / 파일 | bytes | upstream digest |
|---|---|---:|---|
| dictionary | `dictionary-korean_mfa-v3.0.0` / `korean_mfa.dict` | 937,903 | 없음 (`digest: null`) |
| acoustic | `acoustic-korean_mfa-v3.0.0` / `korean_mfa.zip` | 61,574,295 | 없음 (`digest: null`) |
| G2P | `g2p-korean_mfa-v3.0.0` / `korean_mfa.zip` | 5,530,577 | 없음 (`digest: null`) |

published time은 각각 2024-02-24
`00:12:57Z`, `00:08:43Z`, `00:10:52Z`다.
release asset archive는 받지 않았으므로 local SHA-256은 미확인이다.

공식 페이지와 pinned metadata의 핵심은 다음과 같다.

| artifact | 사실 | 판정 |
|---|---|---|
| dictionary v3.0.0 | 17,968 words, MFA phone set, pinned meta의 phone 105개, CC BY 4.0 | archive digest 없음 |
| acoustic v3.0.0 | MFCC GMM-HMM, trained 2024-02-17, CC BY 4.0 | read/low-noise 중심 domain drift 주의 |
| G2P v3.0.0 | Phonetisaurus, train 49,742 words/108 phones/2,112 graphemes, evaluation 5,457 words | 공식 WER 100%, PER 100% |

세 artifact의 pinned `LICENSE` 내용은 같고 SHA-256은
`7e7170e3cebf88a9f60c7b8421418323c09304da1af4d5e90f4da1dc1c8a2661`이다.

공식 근거:

- [Korean dictionary v3.0.0](https://mfa-models.readthedocs.io/en/latest/dictionary/Korean/Korean%20MFA%20dictionary%20v3_0_0.html)
- [Korean acoustic model v3.0.0](https://mfa-models.readthedocs.io/en/latest/acoustic/Korean/Korean%20MFA%20acoustic%20model%20v3_0_0.html)
- [Korean G2P model v3.0.0](https://mfa-models.readthedocs.io/en/latest/g2p/Korean/Korean%20MFA%20G2P%20model%20v3_0_0.html)
- [dictionary release](https://github.com/MontrealCorpusTools/mfa-models/releases/tag/dictionary-korean_mfa-v3.0.0)
- [acoustic release](https://github.com/MontrealCorpusTools/mfa-models/releases/tag/acoustic-korean_mfa-v3.0.0)
- [G2P release](https://github.com/MontrealCorpusTools/mfa-models/releases/tag/g2p-korean_mfa-v3.0.0)

acoustic 페이지의 WER/CER `0%`는 같은 training data에 language model을
붙인 sanity check이며 held-out generalization 수치가 아니라고 공식
페이지 자체가 경고한다. 논문 성능 근거로 인용하지 않는다.

Legacy G2P의 WER/PER `100%`는 원인 추측이나 임의 정정 없이 그대로
red flag로 남긴다. 첫 MFA smoke에서는 G2P/OOV 자동 생성을 완전히 끄고,
dictionary coverage 100%인 전사만 사용해야 한다.

## 8. 기대 발음·관측 발음·MFA 예측의 경계

이 프로젝트에는 서로 바꿔 쓸 수 없는 세 의미층이 있다.

| 층 | 원천 | 의미 | 허용 명칭 |
|---|---|---|---|
| expected | AI Hub JSON `RecordingMetadata.phonemic.phones` | prompt의 기대 발음열 | expected/target |
| observed candidate | AI Hub 원본 TextGrid phone tier | 실제 실현 phone 후보, G04 수동 감사 pending | observed candidate/oracle candidate |
| predicted | transcript+dictionary+acoustic model에 조건화된 MFA 출력 | 모델이 강제로 배치한 phone/경계 | `mfa_predicted_*` |

MFA workflow는 text를 dictionary에서 얻은 phone과 acoustic model로
speech에 정렬한다. dictionary 문서도 모든 pronunciation phone을
alignment에 포함해야 하며 phone별 기본 최소 길이가 있다고 설명한다.
따라서 학습자의 실제 삭제·삽입·치환이 있어도 canonical phone을 짧게
억지 배치하거나 인접 word 경계를 흔들 수 있다.

다음은 금지한다.

- MFA 출력 phone을 AI Hub observed TextGrid 정답으로 덮어쓰기
- MFA forced alignment 자체를 독립 발음 goodness 평가로 사용
- expected JSON과 MFA output이 같다는 이유로 정확한 발음이라고 판정
- MFA/G2P output으로 G04 pending observed-phone CTC target을 교체
- gold TextGrid metric과 predicted MFA metric을 한 표에서 출처 표시 없이
  혼합

m1에서 MFA가 맡을 수 있는 역할은 transcript-conditioned segmentation
후보와 alignment confidence뿐이다. 치환·삭제·삽입의 독립 관측에는
phone recognizer/CTC competing hypothesis 및 사람 감사가 별도로 필요하다.

m3에서 MFA interval로 계산한 duration/pause는 **predicted alignment
feature**다. 기존 TextGrid interval을 쓴 값은 oracle/proxy로 표시하고,
동일 non-locked item에서 두 경로를 분리 평가해야 한다.

## 9. phone inventory와 mapping gate

현재 CTC 정책은 관측 TextGrid에서 41개 phone을 사용하고 special token
2개를 더해 vocabulary 43을 만든다. MFA current bundle은 107-phone IPA
inventory를 사용한다. 이는 단순 alias 차이가 아니다.

따라서 다음 mapping을 이 문서에서 임의로 만들지 않는다.

- MFA IPA 107 → 관측 호환 자모 41
- long/aspirated/tense/palatalized/labialized symbol의 collapse
- `sil`, `spn`, `<unk>`, deletion/insertion 의미
- 여러 MFA phone과 한 자모의 many-to-one timing aggregation

실험 전 별도 versioned mapping에는 최소한 다음이 필요하다.

1. source/target inventory의 exact digest
2. 모든 source phone의 explicit mapped/unmapped decision
3. silence/noise/OOV policy
4. many-to-one duration 합산과 경계 선택 규칙
5. unknown phone 발생 시 utterance 전체 reject
6. 한국어 음성학 검토자 승인과 non-locked 예시 감사
7. CTC target 생성과 MFA feature 생성의 서로 다른 lineage

Current HF bundle에서도 acoustic 107개와 dictionary 실제 사용 105개가
다르므로 mapping보다 먼저 bundle 내부 호환 검증이 필요하다.

## 10. 데이터·split·privacy·speaker gate

### split

- 첫 smoke는 locked test를 읽지 않는다.
- 개발 판단은 speaker-disjoint train/dev의 사전 지정 micro-subset에서만
  수행한다.
- AI Hub 공식 Training/Validation speaker overlap이 있었으므로 공식
  폴더명이 독립 split을 보장한다고 간주하지 않는다.
- model 선택, mapping 수정, beam 조정, OOV 사전 보완에는 locked 결과를
  사용하지 않는다.

### G04와 oracle

TextGrid target policy는 `manual_alignment_gate.status: pending`,
`training_allowed: false`다. MFA 환경이 준비돼도 이 값을 바꾸지 않는다.
G04 240건, 최소 95% 감사 전에는 observed TextGrid phone CTC 학습을
시작하지 않는다.

MFA package 실행성만 보는 최초 micro-smoke는 별도 권리 승인된
non-locked fixture로 할 수 있지만, 이를 AI Hub oracle 품질 또는 m1/m3
성능으로 승격하지 않는다. AI Hub TextGrid와 경계 비교가 필요하면 G04
승인 후 사전 지정된 non-locked audit subset에서만 한다.

### speaker adaptation

current HF acoustic metadata는 speaker adaptation을 사용한다고 표시한다.
학습자/원어민 cohort와 split을 넘는 적응은 leakage가 될 수 있다. 첫
micro-smoke는 `--single_speaker` 또는 설치된 CLI가 확인한 동등한
`uses_speaker_adaptation=false` 설정으로 adaptation을 끈다. 이후
adaptation ablation은 별도 사전등록하며 train speaker만으로 fit한다.

### privacy

- 원본 음성·전사·TextGrid locator: R0
- MFA TextGrid·alignment log·상세 OOV/unaligned item: R1
- pseudonymous interval feature/DTW trace: D1
- count/rate/CI와 개인정보 없는 표: A1

R0/R1/D1은 Git, 공개 issue, 외부 모델 hub, 일반 log에 올리지 않는다.
오류 메시지·CLI 출력에도 raw path, speaker ID, transcript가 남지 않도록
restricted log를 분리하고 외부 보고에는 aggregate와 checksum만 남긴다.

## 11. 승인 후 exact acquisition gate

다음 순서를 하나라도 건너뛰면 다운로드하지 않는다.

1. **runtime 장소 결정**
   - native x86_64 CPU 격리 runner를 승인하거나,
   - ARM64 source build를 별도 supply-chain 과제로 승인한다.
2. **한 model line만 선택**
   - 우선 후보: MFA 3.4.1 +
     `MontrealCorpusTools/korean_mfa@0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd`
   - legacy v3.0.0 세 archive와 current HF bundle을 섞지 않는다.
3. **license/provenance 승인**
   - missing bundle `LICENSE`, model card/content 불일치,
     training-corpus 조건을 검토하고 signed decision을 남긴다.
4. **Conda solve lock**
   - exact Python/platform/channel/package build/SHA를 모두 고정하고
     solver 재현성을 확인한다.
5. **quarantine 획득**
   - mutable `main`, `latest`, unqualified `korean_mfa`를 금지하고 exact
     commit만 받는다.
6. **content 검증**
   - 14-file allowlist, byte size, LFS/local SHA-256, regular-file type,
     no symlink/path escape, total size ceiling을 검사한다.
7. **static model 검증**
   - acoustic/G2P phone equality, dictionary subset, OOV special symbol,
     Unicode normalization, all finite probability, duplicate pronunciation,
     model version과 CLI compatibility를 검사한다.
8. **offline 재실행**
   - cache를 read-only로 전환하고 network fallback 없이 같은 digest를
     재현한다.

legacy archive를 선택한다면 upstream digest가 없으므로 첫 승인 획득 때
두 독립 reviewer가 동일 URL/size를 확인하고 local SHA-256 manifest를
만들어야 한다. 그 local digest를 upstream 제공 digest인 것처럼 쓰지
않는다.

## 12. exact non-locked micro-smoke 계약

이 절은 실행 결과가 아니라 **향후 실행 계약**이다. threshold는 사람이
확인할 작은 pilot을 보기 전에 별도 preregistration에서 고정한다.

### 12.1 입력

- 권리 승인된 한 명의 한국어 화자 읽기 음성 정확히 5개
- mono PCM, 16 kHz, finite, clipping/길이 QC 통과
- 한 utterance당 정확한 UTF-8 transcript 1개
- non-locked environment-only fixture이며 model 선택·성능 주장에 재사용 금지
- dictionary coverage 100%, OOV 0
- G2P 자동 생성/`--use_g2p` 비활성
- raw/derived output은 Git 밖 restricted directory

### 12.2 runtime preflight

```text
mfa --version == 3.4.1
platform == linux-64 native
kalpy == 0.10.4
kaldi == 5.5.1172 CPU
model revision == 0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd
network egress during offline run == 0
locked-test paths opened == 0
```

설치된 3.4.1 CLI help를 캡처해 실제 option 이름을 확인한 뒤에만 command를
고정한다. 공식 문서의 legacy/HF signature는 각각 다음과 같다.

```text
mfa align CORPUS DICTIONARY ACOUSTIC_MODEL OUTPUT
mfa align_hf CORPUS HF_MODEL OUTPUT
mfa validate [OPTIONS] CORPUS_DIRECTORY DICTIONARY_PATH
# optional validation option: --acoustic_model_path ACOUSTIC_MODEL_PATH
```

current HF 후보는 conceptually 다음 exact model ID를 사용한다.

```text
MontrealCorpusTools/korean_mfa@0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd
```

실제 command에는 isolated `MFA_ROOT_DIR`, temporary/output directory,
`--num_jobs 1`, speaker adaptation off, TextGrid cleanup off를 고정한다.
이 option 조합은 설치된 `--help`에서 확인하기 전까지 실행 명령으로
간주하지 않는다.

### 12.3 output structural gate

모든 조건을 만족해야 한다.

- input/output utterance 수가 정확히 같음
- OOV, unreadable audio, missing transcript, unaligned utterance 0
- word/phone tier 존재와 exact schema
- 모든 time이 finite이고 `0 <= start < end <= audio_duration`
- 같은 tier interval이 단조 증가하고 허용되지 않은 overlap 0
- 모든 emitted phone이 pinned acoustic/silence inventory에 속함
- 비정상적으로 최소 duration에 몰린 phone과 beam failure를 aggregate로
  보고
- output/log에 raw identity와 공개 불가 transcript가 없음
- clean rerun 두 번의 canonicalized interval digest가 같음

### 12.4 사람 경계 감사

구조 검증은 alignment 정확도를 증명하지 않는다. 한국어 음성학/강제정렬
검수자가 source-blind하게 다음을 확인한다.

- word/phone boundary의 청각적 타당성
- 실제 deletion/substitution에서 canonical phone 강제 배치 여부
- 짧은 phone과 인접 word boundary 전이
- pause 시작/끝
- 외국어 화자와 원어민에서 실패 양상의 차이

표본 수, 허용 boundary error, reviewer 수, disagreement 처리, pass
threshold는 결과를 보기 전에 고정한다. 이 문서에서는 임의 수치를
만들지 않는다.

### 12.5 m3 DTW 후속 gate

MFA smoke가 통과한 뒤에도 DTW는 별도 구현이다.

1. same-sentence learner/reference pair만 허용
2. predicted MFA interval에서 duration/pause sequence를 생성
3. distance, normalization, insertion/deletion cost, Sakoe-Chiba window,
   tie-break를 고정
4. empty/one-token/non-finite/very-short sequence를 fail closed
5. input order와 thread 수에 무관한 결정성 단위 테스트
6. oracle TextGrid DTW와 predicted MFA DTW를 별도 열로 보고
7. non-locked dev에서만 energy proxy 대비 incremental ablation

MFA 정렬 성공만으로 DTW나 m3 완성을 주장하지 않는다.

## 13. 승격 기준과 현재 카운터

| gate | 현재 상태 |
|---|---|
| 로컬 MFA runtime | 0 |
| native ARM64 공식 Conda route | kalpy 부재로 blocked |
| x86_64 exact solve/lock | 0 |
| model weight/archive 획득 | 0 |
| local artifact SHA 검증 | 0 |
| bundle license 승인 | 0 |
| dictionary/acoustic/G2P compatibility validate | 0 |
| G2P 자동 OOV 사용 | 금지 |
| non-locked audio 접근 | 0 |
| MFA alignment | 0 |
| 사람 predicted-boundary 감사 | 0 |
| DTW 구현/실행 | 0 |
| m1/m3 논문 metric | 0 |
| locked-test 접근 | 0 |

승격은 다음 순서만 허용한다.

```text
platform/runtime lock
  -> artifact/license approval
  -> static inventory validation
  -> offline no-audio preflight
  -> G2P-off non-locked micro-smoke
  -> human predicted-boundary audit
  -> explicit phone mapping
  -> m3 DTW implementation
  -> non-locked ablation
  -> preregistered final evaluation
```

이 문서의 현재 최종 판정은 **MFA artifact/environment gate 설계 완료,
실제 Phase 1 m1/m3 MFA 작업은 미완료**다.
