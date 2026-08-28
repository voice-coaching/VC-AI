# 고정 프롬프트·다각도/3D 기반 발음 코칭 개선안의 실현성·실용성 검토

- 기준일: 2026-08-25
- 검토대상: 제시된 프롬프트의 네 단계 개선안
- 결론 등급: **조건부 실현 가능**
- 중요한 범위 제한: **외부 입술·턱 동작 보조진단은 가능하지만, 단일 45도 RGB 영상에서 혀·연구개·성문을 포함한 실제 3D 구강구조와 정확한 음소를 복원한다는 주장은 근거 불충분**
- 원문 코퍼스: 신규 검증 PDF 45건 + 기존 OLKAVS 1건
- 세부 논문별 근거: [PAPER_EVIDENCE_MATRIX.csv](./PAPER_EVIDENCE_MATRIX.csv)
- 검색·다운로드 감사: [SEARCH_AND_DOWNLOAD_LOG.md](./SEARCH_AND_DOWNLOAD_LOG.md)

## 1. 요약 판정

현재 제안은 그대로 한 번에 구현하기보다 다음처럼 재정의하면 현실성이 있다.

> 고정 문장 발화를 음성 MDD가 1차 판정하고, 카메라는 외부에서 관측 가능한 입술 폐쇄·개구·돌출·원순성·턱 움직임·타이밍을 보조 증거로 제공한다. 3D 얼굴은 진단의 정답이 아니라 정규화와 설명용 표현으로 사용한다. 혀·연구개·성문과 같이 보이지 않는 조음은 음향 근거 또는 별도 UTI/EMA/RT-MRI 연구 장비가 없으면 단정하지 않는다.

이 범위라면 고정 프롬프트, 전문가 기준군, 사용자 촬영 비교, 쉬운 피드백 모두 구현 가능하다. 반대로 `한 명의 아나운서 3D 메시와 사용자의 단일 45도 메시 간 거리 = 발음 오류`로 정의하면 해부학 차이, 단안 깊이 모호성, 조음의 다대일 관계, 공동조음 때문에 타당하지 않다.

### 단계별 등급

| 제안 단계 | 기술 실현성 | 서비스 실용성 | 판정 | 필요한 수정 |
|---|---:|---:|---|---|
| 1. 자유발화 없음/고정 프롬프트 | 높음 | 높음 | **실현 가능** | 프롬프트가 ASR을 정답으로 복원시키는 편향을 별도 감사 |
| 2. 아나운서 다각도 영상을 외부 얼굴 3D로 전사 | 중상 | 중간 | **조건부 가능** | 한 명이 아니라 여러 기준 화자; 3D는 외부 표면과 타이밍만 |
| 2-b. 같은 영상에서 실제 혀·연구개·성문 3D를 복원 | 낮음 | 낮음 | **근거 불충분** | UTI/EMA/RT-MRI 등 직접 센서가 필요 |
| 3. 사용자 단일 45도 영상의 3D 전사 | 중간 | 중상 | **조건부 가능** | 카메라 보정·품질 게이트·불확실성; 전면+45도 비교 실험 권고 |
| 3-b. 기준 메시와 원시 좌표 차이로 오류 확정 | 낮음 | 낮음 | **부적절** | 개인 해부학과 자세를 제거한 조음 특징·시간 정렬로 비교 |
| 4. 쉬운 피드백 생성 | 중상 | 높음 | **조건부 가능** | 구조화된 근거만 문장화; 불확실하면 보류; 학습효과 사용자 실험 |
| 전체: 완전한 3D 구강 진단 서비스 | 낮음 | 낮음 | **현재 근거 불충분** | 연구용 직접 조음 데이터와 한국어 L2 오류 gold 필요 |
| 전체: 음성 MDD + 외부 입술 보조증거 MVP | 중상 | 높음 | **권고** | 단순 기준선 대비 3D의 추가가치가 입증될 때만 유지 |

## 2. 원 프롬프트의 핵심 주장 감사

### 2.1 “ASR이 그럴듯한 발음으로 보정한다”

**방향은 맞지만 ‘현재 모델은 잘못된 발음을 잡아내지 못한다’는 절대 표현은 과도하다.**

Park(2026)은 L2-ARCTIC와 SpeechOcean762에서 낮은 WER의 현대 ASR일수록 의도한 단어를 복원해 오류를 가리는 경향을 보였다. L2-ARCTIC의 심각 오류에서 Parakeet top-1의 false acceptance rate(FAR)는 80.8%였고 surface-faithful reranking 후에도 74.8%였다. 즉 문제는 실제지만 일부 개선은 가능하며, 잔여 오류가 매우 크다. 이 결과는 영어 읽기 발화이고 한국어 학습자 서비스에서 그대로 재현됐다는 뜻은 아니다.

따라서 필요한 것은 일반 ASR의 WER가 아니라 다음 MDD 지표다.

- 오류를 정상으로 통과시키는 FAR
- 정상을 오류로 경고하는 FRR
- 오류 유형 진단오류율 DER
- phone별 macro-F1/AUPRC
- 심각도별·L1별·화자별 성능
- 신뢰도 이하에서 판정을 보류했을 때의 selective risk

### 2.2 “입모양 모델 정확도 50% 미만”

**보편적 사실로 쓸 수 없다.** 과제와 지표가 다르면 값이 크게 달라진다.

- Petridis et al.(2017)은 OuluVS2의 10개 고정 영어 구문 분류에서 단일 45도 시점 평균 88.8%, 전면 91.8%, 전면+측면 94.8%, 전면+45도+측면 95.6%를 보고했다. 그러나 이는 52명이 깨끗하게 촬영한 10구문 폐쇄집합 분류이며 음소 오류 검출이 아니다.
- OLKAVS의 한국어 문장 시각-only baseline은 validation CER 26.64%였다. 이것도 문자 전사 오류율이지 발음 오류 진단 정확도가 아니다.
- 음소/단어/문장 분류 정확도, CER, WER, MDD F1은 서로 교환할 수 없다.

따라서 “50% 미만” 대신 `한국어 L2 음소 오류의 시각-only 검출 성능은 검증되지 않았다`가 정확하다.

### 2.3 “음성+입모양 혼합 정확도 67.84%”

**출처와 지표가 확인되지 않으며, 두 가지 오독 가능성이 있다.**

1. OLKAVS 표의 all-view visual model이 전면 영상에서 CER 32.16%를 기록한다. 이를 `100 - CER = 67.84% 정확도`로 바꾼 것이라면 잘못이다. CER은 삽입 때문에 100%를 넘을 수도 있고 단순 정답률의 보수가 아니다. 또한 이 표는 audio+video 융합이 아니라 **visual-only 모델의 multi-view 학습 효과**다.
2. Rademacher et al.(2006)의 TIMIT 표에는 67.84%가 정확히 존재하지만, 이는 음향-only vocal-tract-length-invariant feature의 phoneme recognition accuracy다. 같은 표의 MFCC는 69.19%로 더 높다.

OLKAVS에서 실제 AVSR 결과는 다음과 같다.

| 조건 | Audio CER | Visual CER | Audio-Visual CER | 해석 |
|---|---:|---:|---:|---|
| 전체 validation | 3.57 | 26.64 | 3.64 | AV가 audio보다 근소하게 나쁨 |
| clean | 2.00 | 25.08 | 1.98 | AV 이득은 0.02 pp |
| -5 dB | 30.76 | 25.08 | 19.75 | 강한 잡음에서 AV 이득 큼 |

이 결과는 “영상이 음성 MDD보다 항상 낫다”가 아니라 “영상은 특히 잡음에서 전사 보조 신호가 된다”를 지지한다.

### 2.4 “3D 인체구조 23개, 최신 30개·공기역학 추가”

**23과 30은 각각 근거가 있지만 관계 설명이 부정확하다.**

- Birkholz et al.(2006)의 3D vocal-tract **합성 모델**은 7개 wireframe surface를 23개 기하 파라미터로 제어한다. 이는 사용자의 실제 구조를 측정하는 모델이 아니며, 논문도 비생리적 형상이 생성될 위험을 명시한다.
- Sering(2023)의 PAULE/VocalTractLab은 30 control channels를 사용한다. 구성은 **19개 vocal-tract parameter + 11개 glottis parameter**다. 후자에는 F0, transglottal pressure, 성대 가장자리 위치, posterior chink area, phase lag, amplitude, aspiration 등이 포함된다.
- Birkholz et al.(2024)의 공기역학 손실 연구는 물리 복제물의 압력-유량을 맞추기 위해 3개의 viscous/kinetic loss 파라미터를 적합한다. 이 값은 학습자에게서 카메라로 관측하는 3D 조음 제어변수가 아니다.

따라서 `23개에 공기역학 7개를 더한 최신 30개 측정 모델`이라는 설명은 기각해야 한다. 30개는 합성기 제어 채널이며, 영상에서 직접 얻는 관측값이 아니다.

### 2.5 “FMRI로 입모양·구강구조 촬영”

본 과제에 해당하는 영상은 **fMRI가 아니라 RT-MRI(real-time MRI)**다. fMRI는 주로 뇌의 혈중산소 신호를 측정한다. Lim et al.(2021)의 75화자 데이터는 동적 2D midsagittal RT-MRI와 동기 음성, 지속음의 정적 3D volume을 제공한다. 동적 full-3D 구강 영상을 일반 스마트폰 영상의 정답으로 제공하는 데이터가 아니다.

## 3. 문헌이 실제로 지지하는 것

### 3.1 고정 프롬프트는 가장 강한 설계 선택

고정 텍스트는 발음 구간 강제정렬, 기대 음소열, 오류 후보, 기준 타이밍을 알 수 있게 한다. 자유발화보다 데이터 요구량과 모델 불확실성을 크게 낮춘다. Park(2026)의 방법도 읽기 발화와 알려진 문장을 전제로 한다.

그러나 동일한 장점 때문에 언어모델이 정답 단어를 복원하기 쉬워진다. 따라서 다음 두 경로를 분리해야 한다.

1. `prompt-conditioned path`: 기대 문장과 강제정렬을 이용해 위치를 찾음
2. `surface-faithful path`: 기대 문장을 보지 않거나 약하게 사용해 실제 들린 음소/음향 속성을 보존

두 경로가 불일치할 때만 MDD 후보를 만들고, 최종 판정은 사람 주석으로 보정한다.

### 3.2 다중시점은 유용하지만 45도 단독의 우월성은 없음

Petridis et al.(2017)의 가장 큰 증가는 전면+측면 두 시점에서 나왔다. 45도는 전면+측면에 추가할 때 작은 이득을 주며, 단일 45도는 전면보다 낮았다. OLKAVS도 여러 시점으로 학습한 모델이 전면만 학습한 모델보다 모든 시점에서 좋아졌다.

따라서 MVP 촬영 후보는 실험으로 비교해야 한다.

- A: 전면 한 대
- B: 45도 한 대
- C: 전면+45도 동기 두 대
- D: 전면+측면 동기 두 대(연구 상한)

서비스 부담을 생각하면 C가 현실적인 연구안이고, 한 대만 허용한다면 전면과 45도를 같은 조건에서 직접 비교한 뒤 선택해야 한다.

### 3.3 단안 3D 얼굴 복원은 ‘외부 표면 추정’으로는 가능

DECA/EMOCA/SPECTRE는 단안 이미지에서 FLAME 계열 identity/expression/pose를 복원한다. AVFace와 Speech4Mesh는 음성과 단안 영상을 함께 써서 말할 때의 하안면/입술 복원을 개선한다. 이들은 다음에 유용하다.

- 머리 자세와 얼굴 크기 정규화
- lip aperture, protrusion proxy, corner spread, jaw opening의 시간곡선
- 기준 화자와 사용자 영상을 같은 저차원 계수 공간에서 비교
- 피드백 애니메이션 표시

하지만 이 메시의 깊이는 2D 관측과 얼굴 prior에서 추정한 값이다. 혀·연구개·성문을 직접 보지 않으며, 외부 메시가 음소의 유일한 조음 원인도 아니다. VOCA/FaceFormer/CodeTalker/EMOTE는 주로 자연스러운 메시를 **생성**하는 모델이라 학습자의 이상 동작을 평균적·그럴듯한 동작으로 덮을 위험이 있다. 진단 경로에는 생성 메시가 아니라 관측 기반 특징과 불확실성을 써야 한다.

### 3.4 AAI는 연구 가치가 높지만 즉시 서비스 센서는 아님

- Csapó et al.(2020)은 같은 화자의 음성에서 68×68 midsagittal RT-MRI를 예측해 평균 CW-SSIM 약 0.94를 얻었지만 speaker-dependent다.
- Hao et al.(2024)은 English EMA에서 Dutch EMA로 옮길 때 최고 PCC 0.564와 30% 미만 성능저하를 보고했으나 언어와 장비 차이가 섞여 있다.
- McGhee et al.(2023)은 HuBERT→9 tract variables→phone recognition에서 L2-ARCTIC 전체 accuracy 78.0%, substitution accuracy 68.1%를 얻었다. 입술로 구별되는 일부 대립은 57.1%에 그쳤다.
- Azzouz et al.(2026)은 한 프랑스어 화자의 3.5시간 RT-MRI에서 glottis-to-lips contour 평균 RMSE 1.48 mm를 보고했지만 단일 화자 preprint이고 MDD가 아니다.

AAI는 숨은 조음의 **가설 생성** 또는 연구실 검증용으로 적합하다. 스마트폰 RGB 결과에 ‘실제 혀 위치’라는 이름을 붙이는 용도로는 아직 부적합하다.

### 3.5 물리 3D 없이도 조음 수준 MDD가 가능

Wei et al.(2025)은 실제 3D 얼굴이나 EMA 없이 audio-derived articulatory features(AF)를 XLSR 기반 MDD에 결합했다. L2-ARCTIC에서 ART-M2의 DER는 18.62%로 PHN-M2 25.12%보다 상대 25.88% 감소했다. AF 일관성은 약 74–82%였고 일부 자음에서는 오히려 불리했다.

이는 3D 시스템 전에 반드시 비교할 더 싼 기준선이다.

- audio-only phone MDD
- audio + derived manner/place/voicing/rounding features
- audio + 2D lip landmarks
- audio + normalized 3D expression coefficients

3D가 2D보다 MDD나 학습효과를 유의하게 개선하지 못하면, 3D는 진단에서 제거하고 설명 애니메이션에만 남기는 편이 실용적이다.

### 3.6 피드백은 만들 수 있지만 학습효과 증거가 약하다

UTI/EMA 시각 피드백은 특정 소리의 짧은 훈련에서 개선을 보이지만 장비·전문가 의존성이 크다. Visual Learning 2는 전면·측면 원어민 영상, MRI/UTI, 사용자 녹화를 동기 비교하는 매우 가까운 선례지만 demo이며 학습효과를 검증하지 않았다. Zhong et al.(2024)은 10명의 L2 평가에서 GPT-4+Viterbi+articulatory feature 피드백의 도움도 MOS 3.79를 보고했지만, 실제 장기 발음 향상을 측정하지 않았다.

따라서 `이해하기 쉬움`, `도움이 된다고 느낌`, `즉시 따라함`, `새 문항에 전이`, `1주 후 유지`를 별도 평가해야 한다.

## 4. 권고 시스템 구조

### 4.1 기준군

한 명의 아나운서를 절대 정답으로 쓰지 않는다.

- 최소 3–5명의 표준발음 숙련 화자
- 성별·연령·얼굴/구강 크기와 말하기 스타일 분산
- 각 문항 3회 이상 반복
- 음성학자/발음교육 전문가가 음소별 허용범위와 눈에 보이는 핵심 단서를 승인
- 원 메시가 아니라 정규화된 특징의 분포(평균, 분산, 허용구간)를 reference bank로 저장

박지연 아나운서는 중요한 전문가이지만, 한 화자의 해부학적 동작을 모든 사용자에게 강제하지 않도록 `대표 기준 화자 중 한 명 + 교육적 문구 감수자`로 두는 것이 안전하다.

### 4.2 입력과 품질 게이트

- 음성: 최소 16 kHz mono, clipping/SNR/VAD/sync 검사
- 영상: 최소 1080p 30 fps 권고; 입술 ROI 해상도, 노출, blur, 가림, head pose 검사
- 촬영: 전면/45도 후보를 비교; 두 시점이면 clap/flash 또는 하드웨어 타임스탬프로 동기화
- 카메라: 초점거리·거리·높이 가이드와 간단한 얼굴 크기 calibration
- 실패 시: 재촬영 안내 또는 audio-only fallback

품질을 통과하지 않은 영상에서 3D를 억지로 생성하지 않는다.

### 4.3 특징과 정렬

외부 영상에서 허용할 진단 특징:

- lip aperture/closure duration
- lip corner spread와 rounding
- protrusion의 상대값(단안에서는 높은 불확실성)
- jaw opening/closing timing
- 좌우 비대칭과 head-motion 분리값
- 음소 경계 대비 onset/peak/release timing

음성에서 결합할 특징:

- target-free phone posterior와 prompt-conditioned alignment의 불일치
- GOP/anti-phone/AF 기반 오류 score
- VOT, formant, spectral cues, voicing, duration, F0/억양

비교 전 처리:

1. identity/head pose/scale 제거
2. 화자별 중립 얼굴로 normalization
3. forced alignment 후 음소/음절 단위 분할
4. duration normalization 또는 제한된 DTW
5. reference distribution과 Mahalanobis/확률적 거리 계산
6. 모델 간 disagreement와 uncertainty 저장

원 메시 vertex-to-vertex L2 거리는 사용하지 않는다.

### 4.4 판정·피드백 계약

판정기는 구조화된 evidence만 출력한다.

```text
target_phone, observed_error_type, audio_evidence,
visible_evidence, confidence, contraindications, abstain_reason
```

피드백기는 이 필드만 다음 순서로 표현한다.

1. 어디에서 차이가 났는지
2. 귀로 들리는 차이와 눈으로 보이는 차이를 분리
3. 한 번에 하나의 행동 지시
4. 짧은 기준 영상/과장 애니메이션
5. 재시도 후 변화 표시

예: `“ㅂ”의 입술 닫힘이 기준보다 약하고 시작이 늦었습니다. 소리를 내기 전에 두 입술을 완전히 붙였다가 한 번에 여세요.`

혀나 연구개가 보이지 않았다면 `혀가 뒤에 있습니다`라고 말하지 않는다. LLM은 검증된 구조화 결과를 자연스럽게 바꾸는 역할만 하고, 새로운 해부학적 원인을 추론하지 못하게 한다.

## 5. 위험요인과 완화책

| 위험 | 영향 | 완화 |
|---|---|---|
| 한 음향 목표에 여러 조음이 가능한 many-to-one 관계 | 정답과 다른 정상 동작을 오류로 판정 | 여러 기준 화자·허용분포·음향+영상 합의·abstention |
| 공동조음과 말속도 | 같은 음소도 문맥별 모양이 달라짐 | 문항/좌우 음소 문맥별 기준; 시간 정렬; 속도 조건화 |
| 단일 45도 깊이 모호성 | protrusion/턱 깊이 오차 | 전면+45도 실험; calibration; 3D uncertainty |
| 생성모델의 평균화 | 실제 오류를 그럴듯한 메시로 보정 | 관측 기반 landmark/계수만 진단; 생성은 표시 전용 |
| 프롬프트 복원 편향 | 실제 오류를 canonical text로 덮음 | target-free branch; drop/swap/sham prompt audit; FAR 우선 |
| 한 아나운서 편향 | 성별·해부학·스타일을 표준으로 오인 | 3–5명 이상 기준 분포와 허용 변이 |
| Korean L2 gold 부족 | 정확도 검증 불가 | 전문가 perceived-phone/visible-cue 주석을 먼저 구축 |
| 지표 오용 | CER/WER 개선을 MDD 개선으로 오인 | FAR/FRR/DER/macro-AUPRC와 학습효과를 주 지표로 |
| 조명·기기·네트워크 변화 | 현장 성능 급락 | 기기/환경 holdout; 품질 게이트; on-device 또는 최소 업로드 |
| GPU 지연·배터리 | 재시도 흐름 악화 | 2D 우선, 3D 비동기/선택적 실행, 모델 경량화 |
| 얼굴·음성 생체정보 | 개인정보·재식별·초상권 위험 | 명시적 동의, 최소수집, 암호화, 짧은 보존, 삭제/철회, 파생물 포함 정책 |
| 데이터/FLAME/VOCASET 라이선스 | 상업 배포 차단 | 논문 공개성과 자산 사용권을 분리해 법무 검토 |
| 규범적/차별적 피드백 | 억양 차이를 오류로 과잉 교정 | intelligibility 우선; 허용 변이; L1/성별/연령 subgroup 감사 |

## 6. 최소 검증 설계

전체 학습보다 먼저 아래 단계로 진행한다.

### Gate 0 — 주장·데이터 감사

- 한국어 최소대립과 실제 서비스 문장 중 20–50개 고정 문항 선정
- 각 문항에 `음향으로만 판별`, `외부 입술로 보조 가능`, `혀/연구개 직접 관측 필요` 태그
- reference 3–5명, learner 30–50명 이상을 성별·연령·L1에 걸쳐 모집
- perceived-phone, 오류 유형, 심각도, visible cue를 두 명 이상 전문가가 독립 주석
- Cohen/Fleiss kappa 또는 Krippendorff alpha와 불일치 adjudication
- 기존 한국어 CTC label과 split이 있으면 unresolved label을 추측 복구하지 않고 제외/재주석; speaker overlap 0을 확인

통과 조건: 주석 지침, 허용 변이, 개인정보/초상권, 데이터 사용권, 화자분리 계획이 승인됨.

### Gate 1 — 촬영·정렬 QC

- 전면, 45도, 전면+45도 세 조건을 동일 화자·문항에서 수집
- frame/audio sync, lip ROI, blur, pose, SNR 실패율 기록
- 동일 화자 반복 간 특징 신뢰도 ICC와 전문가 관측 가능성 측정

통과 조건: 사전 정의한 최소 QC 통과율과 반복신뢰도를 만족하고, 실패 시 안전한 fallback이 동작함.

### Gate 2 — 작은 smoke 비교

speaker-disjoint 작은 train/dev만 사용해 다음을 동일 분할로 비교한다.

- B0: audio-only MDD
- B1: B0 + audio-derived articulatory features
- B2: B0 + 2D lip landmarks
- B3: B0 + normalized 3D expression coefficients
- B4: B0 + 전면/45도 두 시점 특징

필수 ablation:

- 실제 영상 대신 shuffle video
- audio mute/video mute
- 잘못된 prompt로 교체
- visible-phone 대 invisible-phone
- single-reference 대 multi-reference distribution

통과 조건: B2/B3/B4 중 하나가 B0/B1보다 phone macro-AUPRC와 FAR에서 bootstrap CI 기준 개선되고, FRR·촬영 실패·지연이 허용범위임. **B3가 B2보다 낫지 않으면 3D 진단을 중단한다.**

### Gate 3 — 재로드·잠금 평가

- checkpoint/normalizer/reference-bank를 새 프로세스에서 재로드
- 화자·문장·기기·촬영환경 holdout
- phone별 FAR/FRR/DER, macro-F1/AUPRC, calibration ECE/Brier, selective risk
- 성별·연령·L1·카메라 subgroup와 worst-group 성능
- latency p50/p95, 재촬영률, GPU/메모리, 업로드 크기

통과 조건: 사전등록 임계값을 locked test에서 충족하며 subgroup 붕괴가 없음.

### Gate 4 — 전문가 블라인드 청취·사용자 학습효과

전문가가 시스템 이름을 모른 채 다음을 비교한다.

- audio-only feedback
- audio+2D visual feedback
- audio+3D visual feedback
- 인간 교사 간단 피드백

사용자는 즉시 재시도뿐 아니라 미훈련 문항 전이와 1주 후 유지 검사를 받는다. 주요 종점은 전문가 오류 일치도, 피드백 이해도/도움도, 새 문항 발음 개선, 과잉경고로 인한 중도 이탈이다.

통과 조건: 3D가 더 싼 2D 피드백보다 학습효과 또는 전문가 일치도를 유의하게 높이고, 사용자 부담·지연·개인정보 비용을 정당화함.

## 7. 최종 의사결정

### 지금 실현 가능한 제품 방향

1. 고정 프롬프트
2. audio-only surface-faithful MDD를 주 경로로 유지
3. 외부에서 보이는 입술/턱 특징만 영상 보조증거로 사용
4. 한 명의 메시가 아니라 여러 전문가 기준분포와 비교
5. 2D landmark를 기본으로 하고 3D는 증분효과가 검증될 때만 진단에 채택
6. 구조화된 근거를 규칙/제한된 LLM으로 쉬운 문장과 짧은 애니메이션으로 변환
7. 보이지 않는 조음과 낮은 신뢰도에는 판정 보류

### 현재 연구 단계로 남겨야 할 방향

- 단일 45도 스마트폰 영상에서 실제 full 3D vocal tract 복원
- 한 아나운서의 내부 조음 궤적을 모든 사용자 정답으로 사용
- 생성형 3D 말하기 모델의 메시를 학습자 실제 동작으로 간주
- CER/WER 또는 `100-CER`를 MDD 정확도로 보고
- 전문가 gold와 학습효과 없이 “정확한 음소 오류 교정”을 제품 성능으로 주장

## 8. 결론

제안의 핵심 아이디어인 `고정 문장 + 전문가 기준 + 사용자 동작 비교 + 이해 가능한 피드백`은 실용적이다. 실패 가능성이 큰 부분은 아이디어가 아니라 **3D를 실제 내부 조음의 측정값으로 과대해석하는 것**이다. 현 근거에서 가장 타당한 설계는 음성 MDD를 중심에 두고, 외부 입술·턱 움직임을 제한된 보조증거로 추가하는 것이다.

따라서 최종 판정은 다음과 같다.

- **실현 가능:** 고정 프롬프트 정렬, 다화자 기준군, 외부 입술/턱 특징 비교, 구조화된 피드백
- **조건부 가능:** 단안/다중시점 외부 얼굴 3D, audio+visual fusion, LLM 문장화
- **근거 불충분:** 단일 45도 RGB에서 실제 구강 내부 3D와 정확 음소 오류 복원
- **실용성 판단:** 2D보다 3D가 MDD와 학습효과를 유의하게 개선할 때만 3D 진단을 유지

