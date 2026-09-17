# 시청각 발음 진단 관련 논문 62편 비교표

주된 판단 기준은 **발음 오류 진단과 교정의 정확도**다. 문장 전사 정확도, 동기화 성능, 사용성, 실제 재발음 개선은 서로 다른 결과로 취급한다. 아래 62편은 서로 다른 연구이며, 같은 논문의 arXiv·학회·저널 버전은 중복 계산하지 않았다. 연도는 최초 원고와 확인된 출판 시점을 구분했다. 사전공개·원고 표기는 이 목록에서 확인한 버전이며 이후 출판 이력 전체를 뜻하지 않는다.

검색 범위는 2026-09-13까지의 공개 자료다. 공식 출판 페이지, 학회 아카이브, 논문 원고와 저자 데이터 설명을 바탕으로 한 범위 문헌 검토이며, 모든 논문의 전문 정독이나 체계적 메타분석을 의미하지 않는다. 행마다 확인 범위를 표시했다. **초록 기반 행에서는 재현성, 분할, 통계 검정까지 확인된 것으로 해석하면 안 된다.** 본문 색인은 원문 관련 절이 검색 결과에 제공된 범위이며 전문 접근과 구별된다.

A는 음성, V는 얼굴·입술 영상이다. 음성+텍스트, 교육용 아바타 출력, EPG·EMA 센서 연구를 A+V 입력 모델의 직접 검증으로 합산하지 않는다. 번호는 [상세 분석 보고서](C:/Users/to264/Documents/GitHub/intelligentAI/docs/research/av_pronunciation_fusion_review_20260913.md)의 인용 번호와 같다.

| 범주 | 편수 | 이 프로젝트에 대한 역할 |
|---|---:|---|
| 시청각 진단·평가 관련 | 5 | 가장 가까운 근거이나 대리 과제·소규모·시스템 제안 포함 |
| 교정·학습 효과 | 9 | 진단 이후 실제 발음 개선 여부와 시각 피드백의 범위 |
| 음성 기반 MDD·평가 | 15 | 강한 음성 기준선, 라벨·정렬·문맥 편향 |
| AV 표현 학습·인식 | 14 | 공동 표현의 기술적 가능성; 진단에는 간접 근거 |
| 결측·신뢰도·효율 | 9 | 영상 실패와 모달리티 편향을 포함한 설계 |
| 데이터 | 6 | 사전학습 자료와 오류 정답 자료의 구별 |
| 동기화·지각 | 4 | 시간 대응과 잘못된 시차 해석 방지 |
| **합계** | **62** | **독립된 62개 연구** |

논문마다 자료·분할·잡음·정답 정의가 다르므로 아래 내용을 하나의 성능 순위로 해석하지 않는다. ‘프로젝트 활용’과 각 연구 결과에서 도출한 한계는 본 검토의 분석적 판단이다.

## 1. 시청각 진단·평가와 가까운 연구

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[1]** Picard 외 (2010). [Detection of Specific Mispronunciations using Audiovisual Features](https://www.isca-archive.org/avsp_2010/picard10_avsp.html). AVSP. | 입력 A+V; 특징 결합<br>스웨덴어 모음 대비; 소규모 남성 표본 | 시각 특징 선택에 따라 일부 모음쌍 구별이 개선되지만 결합 후 악화도 존재 | 두 평가자가 올바르게 발음했다고 판단한 모음만 사용. 실제 오류 검출 전체 성능으로 해석 불가 | 음소별 시각 기여와 화자 독립 검증이 필요<br>**확인:** 본문 방법·표 |
| **[2]** Karbasi 외 (2019). [Toward Robust Mispronunciation Detection via Audio-Visual Speech Recognition](https://link.springer.com/chapter/10.1007/978-3-030-20518-8_54). IWANN, Springer. | 입력 A+V; concat / turbo 결합<br>GRID의 정상 발화 음소쌍; 다중 잡음 조건 | 단순 연결보다 결합 전략과 잡음 조건의 영향이 큼 | GRID에 실제 오발음이 없어 정상 음소 대비로 대리 평가; 화자 종속 설정 | 실제 한국어 L2 오류 자료에서 다시 비교<br>**확인:** 본문 방법·결과, 저자 원고 |
| **[3]** Yu, Huang, Liu, Zhao (2026-09-06 v1). [When Speech Meets Lips: Interpretable Audio-Visual Synchronization for L2 Pronunciation Assessment](https://arxiv.org/abs/2609.06788). arXiv 사전공개. | 입력 A+V; cross-attention 시차 설명<br>여러 AV 자료 표기; 교사 10명·학생 20명 사용성 평가 | 시간 대응을 시차 궤적으로 보여주는 발음 피드백 접근 | 설문과 동기화 지표는 음소 오류 정답 기반 진단 정확도·학습 효과 검증을 대체하지 않음 | 시차 시각화 참고; 시차를 오류 점수로 직접 채택하지 않음<br>**확인:** HTML 본문 관련 절 |
| **[4]** 박희준 (2026). [Development of a Smartphone-Based Pronunciation Assessment and Feedback System Using Three-Dimensional Vowel Space Analysis and Lip Contour Extraction](https://doi.org/10.23153/AI-Science.2026.5.1.072). 산업과 과학 5(1), 72–79. | 입력 A+V; 음향 분석+입술 윤곽<br>스마트폰 모음 분석 시스템 | LPC 포먼트·분포 및 입술 윤곽을 함께 활용하는 설계 | 확인된 초록만으로 독립 평가 표본·진단 성능·교정 효과를 확정할 수 없음 | 현 분리 분석을 보완하는 시스템 참고; 공동 신경망 학습 근거와 구분<br>**확인:** KCI 등록 초록 |
| **[61]** Sage 외 (2024). [Hybridization of Acoustic and Visual Features of Polish Sibilants Produced by Children for Computer Speech Diagnosis](https://www.mdpi.com/1424-8220/24/16/5360). Sensors 24(16), 5360. | 입력 A+V; 특징의 통계적 분석<br>폴란드 5–8세; /s/ 183명, /ʂ/ 178명 | /s/와 /ʂ/의 구별에 기여하는 시각·음향 특징 비중이 다름 | 집단 간 특징 차이는 독립 검증된 오류 분류기 성능이 아님; 두 표본을 합산하면 중복 위험 | 시각 기여를 음소별로 학습·검증<br>**확인:** 초록·본문 방법/결과 색인 |

## 2. 시각 피드백과 실제 교정·학습 효과

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[5]** Engwall (2008). [Can audio-visual instructions help learners improve their articulation? – an ultrasound study of short term changes](https://www.isca-archive.org/interspeech_2008/engwall08_interspeech.html). Interspeech. | 출력 AV 아바타; 초음파 관찰<br>스웨덴어 학습 프랑스어 화자 7명 | 단기 발음·조음 변화의 가능성을 관찰 | 사람이 미리 생성된 교정을 선택하는 Wizard-of-Oz; 자동 AV 진단 검증 아님 | 진단 모델 정확도와 교정 지시의 효과를 따로 평가<br>**확인:** 본문 관련 절 |
| **[6]** Badin 외 (2010). [Visual articulatory feedback for phonetic correction in second language learning](https://www.isca-archive.org/l2ws_2010/badin10_l2ws.html). L2WS. | 음향→조음 역추정; 시각 출력<br>조음 모델·언어 학습 기술 | 내부 조음을 설명하는 시각 피드백 체계 | 추정된 혀 형태와 실제 카메라 관찰은 다름 | 관찰·추정·교육용 예시를 피드백에서 분리<br>**확인:** 초록 |
| **[7]** Hazan, Sennema (2007). [The impact of visual training on the perception and production of a non-native phonetic contrast](https://www.isca-archive.org/avsp_2007/hazan07_avsp.html). AVSP. | 시각 단독 훈련; A/V/AV 평가<br>일본어 화자 훈련 15명·대조 11명, /r-l/ | V·AV 구별 향상에도 청각 단독으로 전이되지 않고 발음 개선 근거 없음 | 시각 단독 훈련 결과이며 모든 AV 훈련의 무효를 뜻하지 않음 | 시각 구별 정확도 대신 실제 재발음 결과를 측정<br>**확인:** 본문 방법·결과 |
| **[8]** Massaro 외 (2008). [Pronunciation training: the role of eye and ear](https://www.isca-archive.org/interspeech_2008/massaro08_interspeech.html). Interspeech. | 출력 AV / 내부 조음 아바타<br>중국어 모음·아랍어 폐쇄음 훈련 | 눈과 귀에 제공하는 조음 정보의 교육적 가능성 | 예비 결과; 현대 AV 오류 검출기 비교가 아님 | 어떤 교정 표시가 학습에 도움이 되는지 별도 설계<br>**확인:** 초록 |
| **[9]** Nissen, Nissen (2019). [Using Real-Time Visual Biofeedback for Second Language Instruction](https://www.isca-archive.org/interspeech_2019/nissen19b_interspeech.html). Interspeech Show & Tell. | EPG 센서→시각 피드백<br>제2언어 교육 시연 | 혀-입천장 접촉 정보를 실시간으로 제시 | 시연이며 일반 웹캠으로 확보되는 근거가 아님 | 센서 기반 결과를 입술 랜드마크 성능으로 인용하지 않음<br>**확인:** 초록 |
| **[10]** Katz 외 (2014). [Opti-speech: a real-time, 3D visual feedback system for speech training](https://www.isca-archive.org/interspeech_2014/katz14_interspeech.html). Interspeech. | EMA 센서→3D 조음 표시<br>성인 예비 발화 훈련 | 혀·턱 움직임을 시각적으로 피드백 | 자기식 위치 센서 사용; 카메라 AV 융합과 다른 입력 | 숨은 조음의 측정 수단을 명확히 구분<br>**확인:** 초록 |
| **[11]** Bu 외 (2020). [Visual-speech Synthesis of Exaggerated Corrective Feedback](https://arxiv.org/abs/2009.05748). arXiv 원고. | 출력 음성+viseme 애니메이션<br>강조된 교정 합성·사용자 연구 | 교정 부위를 과장해 전달하는 표현 방식을 제안 | 진단 입력을 통합한 연구가 아님; 사용자 연구 세부 통제는 별도 확인 필요 | 근거가 확정된 후 설명·시범 전달 단계에 참고<br>**확인:** 초록 |
| **[12]** Ying Li (2024 온라인 / 2026 권호). [Enhancing L2 sound learning through the integration of audio-visual information: Phonetic training in the classroom](https://journals.sagepub.com/doi/10.1177/13621688231219773). Language Teaching Research 30(6). | AV 고변이 음성 훈련<br>중국인 학습자 70명; 전통 교사 수업과 비교 | /θ, ð/ 산출 개선, /ɪ/ 개선 없음 | 청각 단독 HVPT와의 직접 비교가 아니므로 영상 자체의 순수 기여를 분리하지 못함 | 가시성이 높은 목표와 그렇지 않은 목표의 교육 효과를 나누어 검증<br>**확인:** 초록·원문 색인 |
| **[13]** Shinohara (2021). [Audiovisual English /r/–/l/ Identification Training for Japanese-Speaking Adults and Children](https://pubs.asha.org/doi/10.1044/2021_JSLHR-20-00506). JSLHR. | AV 식별 훈련; A/V/AV 조건 평가<br>일본어 성인·아동, 영어 /r-l/ | AV 훈련 후 AV 식별 개선이 단일 모달 평가보다 큼; 시각 단독 개선도 청각 단독보다 큼 | 식별 성능을 발음 산출 교정 효과와 동일시할 수 없음 | 인지·생산·유지 효과를 구분<br>**확인:** 초록·본문 관련 절 |

## 3. 음성 기반 오류 진단·평가 기준 연구

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[14]** Witt, Young (2000). [Phone-level pronunciation scoring and assessment for interactive language learning](https://www.sciencedirect.com/science/article/pii/S0167639399000448). Speech Communication. | 입력 A+목표 음소<br>언어 학습 발음 채점 | 음소별 음향 적합도 평가의 GOP 기반 | 일치도 점수가 구체적 조음 원인이나 교정 성공을 보장하지 않음 | 음성 단독 기준 모델과 임계값 비교<br>**확인:** 초록 |
| **[15]** Gong 외 (2022). [Transformer-Based Multi-Aspect Multi-Granularity Non-Native English Speaker Pronunciation Assessment](https://arxiv.org/abs/2205.03432). ICASSP; GOPT. | 입력 음향·음소 정보; 영상 없음<br>speechocean762 | 음소·단어·발화 단위의 여러 평가 항목을 공동 학습 | 다중 과제이지 영상 통합이 아님; 점수 상관과 오류 진단은 별개 | 음소 오류와 운율·유창성 head를 분리할 기준<br>**확인:** 초록 |
| **[16]** Ye 외 (2021 원고 / 2022 ICASSP). [An Approach to Mispronunciation Detection and Diagnosis with Acoustic, Phonetic and Linguistic (APL) Embeddings](https://arxiv.org/abs/2110.07274). ICASSP. | 음향+음소+언어 embedding; 영상 없음<br>L2-ARCTIC | 서로 다른 표현을 결합해 MDD에 활용 | multimodal 표현을 A+V로 오해하면 안 됨 | 현재 음향 hidden과 기대 음소 결합을 강화할 기준<br>**확인:** 초록 |
| **[17]** Xu 외 (2021). [Explore Wav2vec 2.0 for Mispronunciation Detection](https://www.isca-archive.org/interspeech_2021/xu21k_interspeech.html). Interspeech. | 입력 A; SSL 표현<br>L2-ARCTIC | wav2vec 2.0 표현의 오류 검출 전이 가능성 | 영어 데이터·정렬 방식에 의존; 한국어 영상 기여 근거 아님 | 큰 AV 모델 이전에 강한 음성 기준선 확보<br>**확인:** 초록·원문 관련 색인 |
| **[18]** Wu 외 (2021). [Transformer Based End-to-End Mispronunciation Detection and Diagnosis](https://www.isca-archive.org/interspeech_2021/wu21h_interspeech.html). Interspeech. | 입력 A+발음 관련 정보<br>CU-CHLOE | Transformer 기반 MDD를 음소 인식과 연결 | 데이터·평가 정의가 다른 논문의 F1과 직접 비교 불가 | 인식과 진단을 구분한 평가 설계<br>**확인:** 초록 |
| **[19]** Peng 외 (2021). [A Study on Fine-Tuning wav2vec2.0 Model for the Task of Mispronunciation Detection and Diagnosis](https://www.isca-archive.org/interspeech_2021/peng21e_interspeech.html). Interspeech. | 입력 A; SSL fine-tuning<br>L2 음성 MDD | 사전학습 음성 표현의 적응 전략을 비교 | L2 오류 라벨과 적응 조건의 영향 | Seungun 개선 대조군으로 활용<br>**확인:** 초록·원문 관련 색인 |
| **[20]** Yang 외 (2022). [Improving Mispronunciation Detection with Wav2vec2-based Momentum Pseudo-Labeling for Accentedness and Intelligibility Assessment](https://www.isca-archive.org/interspeech_2022/yang22v_interspeech.html). Interspeech. | 입력 A; 반지도 학습<br>L2 발음·악센트·명료도 자료 | 의사 라벨을 통해 비라벨 음성 활용 | 모델이 놓친 오류를 의사 라벨이 재생산할 수 있음 | 비라벨 활용 시 독립 수동 정답 보존<br>**확인:** 초록 |
| **[21]** Li, Yan, Lo 외 (2026). [Investigating Context-aware CTC for Pronunciation Assessment: Mitigating Peaky Behavior and Context Independency Assumption](https://aclanthology.org/2026.bea-1.3/). BEA, ACL. | 입력 A; CTC-GOP+평가 모델<br>speechocean762 | CTC peakiness와 문맥 독립성 보완이 평가 표현을 개선 | PCC 개선은 실제 음소 경계 정확도나 AV 진단 효과의 직접 증거가 아님 | CTC 타임스탬프를 정답으로 고정하지 말 것<br>**확인:** 본문 관련 절 |
| **[22]** Geng 외 (2026). [Beyond Acoustic Sparsity and Linguistic Bias: A Prompt-Free Paradigm for Mispronunciation Detection and Diagnosis](https://arxiv.org/abs/2604.22133). arXiv 사전공개. | 입력 A; prompt-free MDD<br>L2-ARCTIC·IqraEval | 희소 음향 증거와 목표 문장 편향을 문제로 삼음 | 사전공개 결과; 한국어·AV로 일반화 미확인 | 기대 발음과 실제 발화 추정의 별도 경로<br>**확인:** 초록 |
| **[23]** Kim 외 (2022). [Automatic Pronunciation Assessment using Self-Supervised Speech Representation Learning](https://www.isca-archive.org/interspeech_2022/kim22k_interspeech.html). Interspeech. | 입력 A+텍스트; SSL<br>한국인 아동의 영어 학습 발화 | SSL 표현과 텍스트를 이용한 발음 평가 | 한국인이 참여해도 목표 언어는 영어; 한국어 발음 성능 근거 아님 | 화자 모국어와 목표 언어를 명확히 기록<br>**확인:** 초록 |
| **[24]** Dong 외 (2025). [Automatic Pronunciation Assessment for L2 English by Incorporating Suprasegmental Features and Weighted Loss Function](https://www.isca-archive.org/slate_2025/dong25_slate.html). SLaTE. | 입력 A; 운율 특징+평가 모델<br>L2 영어 평가 | 초분절 특징과 가중 손실로 평가 항목을 보완 | 음소 오류와 운율 평가는 동일 목표가 아님 | 입술 융합으로 리듬·억양까지 해결된다고 주장하지 않음<br>**확인:** 초록 |
| **[25]** Parikh 외 (2025). [Zero-Shot Speech LLMs for Multi-Aspect Evaluation of L2 Speech: Challenges and Opportunities](https://www.isca-archive.org/slate_2025/parikh25_slate.html). SLaTE. | 입력 A; speech LLM<br>speechocean762, Qwen2-Audio | LLM 평가의 가능성과 과대평가·세부 오류 검출 한계를 보고 | 폭넓은 점수 허용오차의 성능과 음소 오류 정밀도를 혼동하면 안 됨 | Clova의 역할은 검증된 근거의 설명으로 유지<br>**확인:** 초록·본문 관련 부분 |
| **[26]** 경나 외 (2025). [Self-supervised learning-based Korean phoneme recognition for evaluating Korean pronunciation of non-native speakers](https://www.eksss.org/archive/view_article?pid=pss-17-1-51). 말소리와 음성과학 17(1), 51–61. | 입력 A; SSL·라벨 정제<br>AI Hub 비원어민 한국어 자료 | 한국어 음소 인식과 오류 라벨 정제의 중요성을 제시 | ASR이 오발음을 정규 발음으로 복원하는 문제가 본문에 명시됨; 자동 정제 라벨은 수동 정답과 다름 | 한국어 오류 정답셋·비정규 발음 보존을 우선<br>**확인:** 본문 관련 절 |
| **[27]** Phan 외 (2025). [Mispronunciation Detection Without L2 Pronunciation Dataset in Low-Resource Setting: A Case Study in Finland Swedish](https://www.isca-archive.org/interspeech_2025/phan25_interspeech.html). Interspeech. | 입력 A; L1 기반 전이<br>핀란드 스웨덴어; L1 89시간, L2 평가 33분 | 저자원 MDD에서 검출 정밀도와 재현율의 절충을 보고 | L1 인식 학습만으로 신뢰할 만한 L2 교정이 자동 확보되지 않음 | AV 사전학습과 오류 라벨 학습의 필요성을 구분<br>**확인:** 초록 |
| **[62]** 장재석, 임보영, 권혁윤 (2023). [Multimodal Learning Model for Detecting Pronunciation Error Segments of Children's and Foreigners' Speech Data](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002985816). 정보과학회 컴퓨팅의 실제 논문지 29(8), 396–401. | 입력 음성+텍스트; 영상 없음<br>AI Hub 한국인 아동·외국인 한국어 | 음성과 텍스트의 결합으로 오류 구간 탐지를 다룸 | CER/WER를 대리 지표로 사용; 시청각 공동 진단의 직접 증거가 아님 | 국내 선행연구와 연결하되 실제 입력과 평가 목표를 확인<br>**확인:** KCI 등록 초록 |

## 4. 시청각 표현 학습과 공동 음성 인식

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[28]** Shi 외 (2022). [Learning Audio-Visual Speech Representation by Masked Multimodal Cluster Prediction](https://arxiv.org/abs/2201.02184). ICLR; AV-HuBERT. | 학습 A+V; 시청각 SSL<br>LRS3 등 대량 비라벨 AV | 마스킹·군집 예측으로 AV 표현을 사전학습 | 적은 라벨은 적은 총학습량을 뜻하지 않음; MDD 학습 아님 | AV 표현 기준 모델 후보<br>**확인:** 초록 |
| **[29]** Shi, Hsu, Mohamed (2022). [Robust Self-Supervised Audio-Visual Speech Recognition](https://arxiv.org/abs/2201.01763). Interspeech. | 입력 A+V; 잡음 학습<br>LRS3 및 대규모 AV 사전학습 | 잡음 조건에서 음성 단독보다 시청각 전사 오류를 크게 줄임 | WER 개선을 발음 오류 진단 향상률로 전환할 수 없음 | 잡음 하에서 영상의 추가 근거를 검증할 핵심 기준<br>**확인:** 본문 방법·결과 |
| **[30]** Afouras 외 (2018 원고 / 후속 TPAMI). [Deep Audio-Visual Speech Recognition](https://arxiv.org/abs/1809.02108). arXiv / IEEE TPAMI. | 입력 A+V; 별도 encoder+결합<br>자연 영상 문장 인식 | 시청각 문장 인식을 대규모 자연 영상으로 확장 | 정상 전사 복원이 목표; 오류 설명의 정답은 없음 | 시청각 결합 위치·문장 문맥의 영향 참고<br>**확인:** 초록 |
| **[31]** Chung 외 (2017). [Lip Reading Sentences in the Wild](https://openaccess.thecvf.com/content_cvpr_2017/papers/Chung_Lip_Reading_Sentences_CVPR_2017_paper.pdf). CVPR. | 입력 A/V; attention 인식<br>방송 등 자연 문장 영상 | 실험실 고정 문장을 넘어 문장 수준 시각 인식 | 일반 영상의 높은 인식력과 학습자의 미세 오발음 식별은 다름 | 입술 영상 표현 학습의 출발점<br>**확인:** 초록 |
| **[32]** Assael 외 (2016). [LipNet: End-to-End Sentence-level Lipreading](https://arxiv.org/abs/1611.01599). arXiv 원고. | 입력 V; spatiotemporal+CTC<br>GRID | 문장 수준 end-to-end lipreading 가능성 | 제한 문법·화자 분할에 민감; 높은 GRID 정확도는 일반 환경 보장 아님 | 시각만으로 구분되지 않는 음소와 도메인 차이 검토<br>**확인:** 초록 |
| **[33]** Ma, Petridis, Pantic (2021). [End-to-end Audio-visual Speech Recognition with Conformers](https://arxiv.org/abs/2102.06657). ICASSP. | A/V encoder+Conformer+결합<br>LRS2·LRS3 | 모달리티별 특징과 시계열 모델링을 공동 활용 | 하나의 AV 모델도 입력별 encoder가 필요 | 권장 중간 융합의 비교 기준<br>**확인:** 초록 |
| **[34]** Ma 외 (2023). [Auto-AVSR: Audio-Visual Speech Recognition with Automatic Labels](https://arxiv.org/abs/2303.14307). ICASSP. | A+V; ASR 생성 전사로 학습<br>대규모 자동 라벨 AV | 수작업 전사를 줄여 학습 규모를 확장 | 정답 전사 의사 라벨에 실제 오발음이 지워질 수 있음 | 표현 사전학습용; 오류 정답 라벨 대체 불가<br>**확인:** 초록 |
| **[35]** Ma, Petridis, Pantic (2022). [Visual Speech Recognition for Multiple Languages in the Wild](https://www.nature.com/articles/s42256-022-00550-z). Nature Machine Intelligence. | 입력 V; 다국어 인식<br>다국어 자연 영상 | 시각 인식의 언어 확장을 제시 | 다국어라고 한국어 L2 오류 성능이 검증된 것은 아님 | 언어별 viseme·발음 적응 필요<br>**확인:** 초록 |
| **[36]** Haliassos 외 (2022 원고 / 2023 CVPR). [Jointly Learning Visual and Auditory Speech Representations from Raw Data](https://arxiv.org/abs/2212.06246). CVPR; RAVEn. | 학습 A+V; 비대칭 자기증류<br>AV 비라벨 자료 | 청각과 시각 표현을 상호 학습 | 공동 사전학습과 추론 시 공동 판단은 구분해야 함 | 학습 단계 AV 활용 후 별도 head 유지 가능<br>**확인:** 초록 |
| **[37]** Haliassos 외 (2024). [BRAVEn: Improving Self-Supervised Pre-training for Visual and Auditory Speech Recognition](https://arxiv.org/abs/2404.02098). arXiv 원고. | 학습 A+V; SSL<br>청각·시각 인식 자료 | 시청각 사전학습을 개선하는 후속 표현 학습 | 모델 규모·사전학습 비용과 MDD 미검증 | AV-HuBERT 외 표현 후보로 검토<br>**확인:** 초록 |
| **[38]** Haliassos 외 (2024 원고). [Unified Speech Recognition: A Single Model for Auditory, Visual, and Audiovisual Inputs](https://arxiv.org/abs/2411.02256). arXiv; USR. | 추론 A/V/AV를 단일 모델로 처리<br>LRS3 | 단일 모델에서 모달리티 조합별 인식 지원 | 하나의 모델이라는 사실이 현재 파이프라인보다 저렴하거나 진단에 우수함을 뜻하지 않음 | 영상 결측 시 음성 경로 유지 설계 참고<br>**확인:** 본문 구조·표 |
| **[39]** Zhang 외 (2022 원고). [Self-Supervised Audio-Visual Speech Representations Learning By Multimodal Self-Distillation](https://arxiv.org/abs/2212.02782). arXiv; AV2vec. | 학습 A+V; 자기증류<br>시청각 표현 학습 자료 | 클러스터 반복 학습을 줄이는 사전학습 방법 | 학습 시간 절약을 서비스 추론 지연 감소로 옮길 수 없음 | 사전학습 비용 비교 후보<br>**확인:** 초록 |
| **[40]** Pan 외 (2022). [Leveraging Unimodal Self-Supervised Learning for Multimodal Audio-Visual Speech Recognition](https://aclanthology.org/2022.acl-long.308/). ACL. | 단일 모달 SSL→A+V 결합<br>시청각 음성 인식 | 기존 음성·영상 표현을 활용해 멀티모달 적응 | 전사 목표에서 얻은 개선; MDD 레이블로 확인 필요 | Seungun을 출발점으로 하는 점진적 융합의 근거<br>**확인:** 초록 |
| **[41]** Zhang, Yang, Shan, Chen (2024). [ES³: Evolving Self-Supervised Learning of Robust Audio-Visual Speech Representations](https://openaccess.thecvf.com/content/CVPR2024/papers/Zhang_ES3_Evolving_Self-Supervised_Learning_of_Robust_Audio-Visual_Speech_Representations_CVPR_2024_paper.pdf). CVPR. | 학습 A+V; 표현 보완<br>시청각 인식 | 모달리티 고유 정보와 상호 보완적 표현을 다룸 | 공통 표현만 강화하면 모든 오류 정보가 남는다는 보장은 없음 | 독립 음향·영상 head를 유지할 설계 참고<br>**확인:** 원문 초록 색인 |

## 5. 결측·신뢰도·연산 효율

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[42]** Dai 외 (2024). [A Study of Dropout-Induced Modality Bias on Robustness to Missing Video Frames for Audio-Visual Speech Recognition](https://arxiv.org/abs/2403.04245). CVPR. | A+V; missing-frame 학습·적응<br>MISP2022; oracle 분절 | 완전 영상일 때의 이득이 영상 결측 때 손실로 바뀔 수 있음 | 논문 수치는 CER이며 WER·발음 오류율이 아님 | 영상 결측·저품질에서 A 단독 대비 성능 저하를 검증<br>**확인:** 본문·Table 1 |
| **[43]** Yu, Zeiler, Kolossa (2022). [Reliability-Based Large-Vocabulary Audio-Visual Speech Recognition](https://www.mdpi.com/1424-8220/22/15/5501). Sensors 22(15), 5501. | A+V; 신뢰도 기반 결합<br>대어휘 AV 인식 | 입력 신뢰도에 따른 동적 가중 결합 | 정답 전사를 사용하는 oracle 최적 가중치는 실제 배포 방식이 아님 | 배포 중 관찰 가능한 QC 신호만 gate 입력으로 사용<br>**확인:** 초록·본문 색인 |
| **[44]** Burchi, Timofte (2023). [Audio-Visual Efficient Conformer for Robust Speech Recognition](https://arxiv.org/abs/2301.01456). WACV. | A+V; Efficient Conformer<br>잡음 시청각 인식 | 인식 강건성과 학습 효율을 함께 다룸 | 학습 step 감소는 동일 배수의 wall-clock 감소가 아님 | 연산 절감은 실제 장비·입력 길이로 별도 산정<br>**확인:** 초록 |
| **[45]** Wang 외 (2024). [DCIM-AVSR: Efficient Audio-Visual Speech Recognition via Dual Conformer Interaction Module](https://arxiv.org/abs/2409.00481). arXiv 원고. | A+V; dual Conformer 상호작용<br>시청각 인식 | 두 모달리티 상호작용 모듈로 효율·성능 추구 | 인식용 효율 결과를 현재 가벼운 입술 분석과 비교 불가 | 작은 fusion 모듈의 설계 후보<br>**확인:** 초록 |
| **[46]** Yeo 외 (2025). [MMS-LLaMA: Efficient LLM-based Audio-Visual Speech Recognition with Minimal Multimodal Speech Tokens](https://arxiv.org/abs/2503.11315). arXiv 원고. | A+V→압축 토큰→LLM<br>LRS3 등 시청각 전사 | 적은 멀티모달 토큰으로 AVSR 수행 | 토큰 압축의 미세 조음 오류 보존은 별도 문제; 기존 MLLM 대비 비용 결과 | 초기 진단기 교체보다 추후 비용 연구 후보<br>**확인:** 초록 |
| **[47]** Cappellazzo 외 (2024 원고 / 2025 ICASSP). [Large Language Models Are Strong Audio-Visual Speech Recognition Learners](https://arxiv.org/abs/2409.12319). ICASSP. | A/V encoder+projector+LLM<br>AV 음성 인식 | LLM을 시청각 전사에 적응 | 전사 성공으로 교정 근거의 사실성까지 확보되지 않음 | Clova 직접 미디어 진단 전환의 근거로는 부족<br>**확인:** 초록 |
| **[48]** Zhao 외 (2025). [AMG-AVSR: Adaptive Modality Guidance for Audio-Visual Speech Recognition via Progressive Feature Enhancement](https://proceedings.mlr.press/v260/zhao25a.html). ACML, PMLR 260. | A+V; 점진적 modality guidance<br>LRS2 | 학습 중 모달리티 불균형을 완화하는 특징 강화 | 대어휘 전사 실험; 한국어 음소별 오류 진단 미검증 | 음향 지배·영상 무시 현상을 비교할 방법 후보<br>**확인:** 초록 |
| **[49]** Hsu, Shi (2022). [u-HuBERT: Unified Mixed-Modal Speech Pretraining And Zero-Shot Transfer to Unlabeled Modality](https://arxiv.org/abs/2207.07036). NeurIPS. | 학습 A와 AV; modality dropout<br>혼합 모달 비라벨 말뭉치 | 모달리티가 다른 자료를 통합해 표현 학습 | zero-shot 전이는 오류 확률 보정·교정 정확도 보장이 아님 | 음성 단독 자료와 AV 자료를 함께 활용<br>**확인:** 초록 |
| **[50]** Laux 외 (2023 원고). [LiteVSR: Efficient Visual Speech Recognition by Learning from Speech Representations of Unlabeled Data](https://arxiv.org/abs/2312.09727). arXiv 원고. | 학습 A→V 지도; 추론 V<br>비라벨 시청각 자료 | 음성 표현을 이용해 시각 인식 학습을 효율화 | 학습 때 음성을 사용해도 추론에서 AV 공동 진단하는 것은 아님 | 영상 encoder 사전학습 대안<br>**확인:** 초록 |

## 6. 학습·평가 데이터

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[51]** Park 외 (2023 v1 / 2025 v2). [OLKAVS: An Open Large-Scale Korean Audio-Visual Speech Dataset](https://arxiv.org/abs/2301.06375). arXiv 데이터 논문. | 한국어 A+V; 9시점<br>음성 1,150시간·1,107명 | 한국어 AV 표현 학습에 직접 관련된 대규모 자료 | 9시점을 독립 음성 시간으로 중복 합산하면 안 됨; L2 오류·교정 정답셋 아님 | 한국어 시청각 사전학습 우선 후보<br>**확인:** 초록 |
| **[52]** Anwar 외 (2023). [MuAViC: A Multilingual Audio-Visual Corpus for Robust Speech Recognition and Robust Speech-to-Text Translation](https://arxiv.org/abs/2303.00628). arXiv 데이터 논문. | 9개 언어 A+V<br>약 1,200시간; 한국어 미포함 | 다국어 AV 인식·번역 평가 자료 | 언어 다양성이 한국어 오류 라벨을 대체하지 않음 | 표현 전이·잡음 강건성 보조 자료<br>**확인:** 초록 |
| **[53]** Afouras, Chung, Zisserman (2018). [LRS3-TED: a large-scale dataset for visual speech recognition](https://arxiv.org/abs/1809.00496). arXiv 데이터 논문. | 자연 연설 A+V<br>TED·TEDx 400시간 이상 | 시청각 인식의 주요 공개 기준 자료 | 자연 전사·단어 정렬이지 L2 음소 오류 주석이 아님 | 표현·동기화 사전학습용<br>**확인:** 초록 |
| **[54]** Cooke 외 (2006). [An audio-visual corpus for speech perception and automatic speech recognition](https://doi.org/10.1121/1.2229005). JASA 120(5), 2421–2424; GRID. | A+V 고정 문법 문장<br>34화자×1,000문장; 배포 영상 일부 결측 | 통제된 시청각 말뭉치 제공 | 자연 발화·L2 오발음 분포와 다름; 논문 원문 접근 제한 | 초기 AV 실험과 현재 목표의 도메인 차이 설명<br>**확인:** 서지·저자 공식 데이터 설명 |
| **[55]** Zhao 외 (2018). [L2-ARCTIC: A Non-native English Speech Corpus](https://www.isca-archive.org/interspeech_2018/zhao18b_interspeech.html). Interspeech. | 입력 A; 비원어민 영어<br>초기 발표 10화자·5개 모국어 | 발음 오류 연구용 음성 자료 | 후속 확장판과 초기 표본을 혼동하지 말 것; 동시 영상 없음 | MDD 방법 비교용; AV 정답셋 대체 불가<br>**확인:** 초록 |
| **[56]** Zhang 외 (2021). [speechocean762: An Open-Source Non-Native English Speech Corpus for Pronunciation Assessment](https://www.isca-archive.org/interspeech_2021/zhang21x_interspeech.html). Interspeech. | 입력 A; 다층 수동 점수<br>5,000발화·250화자·5평가자 | 음소부터 발화까지 발음 평가 기준 제공 | 영상이 없고 모든 교정 동작의 정답이 제공되는 것은 아님 | 주관 점수·평가자 합의 설계 참고<br>**확인:** 초록 |

## 7. 동기화와 시청각 지각

| 번호·서지·원문 | 입력·학습 방식 / 자료 | 주요 결과·기여 | 근거의 한계 | 프로젝트 활용 / 확인 범위 |
|---|---|---|---|---|
| **[57]** Chung, Zisserman (2016). [Out of time: automated lip sync in the wild](https://www.robots.ox.ac.uk/~vgg/publications/2016/Chung16a/). ACCV Workshop. | A+V; 교차 모달 동기화<br>자연 영상 | 음성과 입 움직임의 시간 대응을 학습 | 동기화 점수는 발음 적절성 점수가 아님 | 장치 지연·오디오 교체 검출 등 QC 후보<br>**확인:** 초록 |
| **[58]** Chung, Chung, Kang (2018 원고 / 2019 ICASSP). [Perfect match: Improved cross-modal embeddings for audio-visual synchronisation](https://arxiv.org/abs/1809.08001). ICASSP. | A+V; 교차 모달 embedding<br>AV 동기화 자료 | 시간 일치 판단에 적합한 표현 학습 | 불일치의 원인이 발음 오류라는 인과 근거는 없음 | 파일 무결성에 더해 시각 근거 품질을 판단<br>**확인:** 초록 |
| **[59]** Schwartz, Savariaux (2014). [No, There Is No 150 ms Lead of Visual Speech on Auditory Speech, but a Range of Audiovisual Asynchronies Varying from Small Audio Lead to Large Audio Lag](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003743). PLOS Computational Biology. | 사람의 A-V 시간 관계 분석<br>발화·조음 시간 관계 | 시각 선행 시간을 단일 상수로 보기는 어려움 | 해당 범위를 모든 기기·언어의 오류 임계값으로 쓰면 안 됨 | 자연 공동조음과 기기 지연을 분리<br>**확인:** 본문 관련 절 |
| **[60]** McGurk, MacDonald (1976). [Hearing lips and seeing voices](https://www.nature.com/articles/264746a0). Nature. | 사람의 시청각 지각 실험<br>불일치 음성·입술 자극 | 시각 정보가 들리는 소리의 지각에 관여 | 사람의 지각 효과는 AI 발음 진단 성능 증거가 아님 | A/V 충돌 상황에서 잘못 확신하는 진단을 경계<br>**확인:** 초록 |

## 원문 접근 보충 및 Sources

각 행의 제목 링크가 해당 연구의 기본 출처이며, 저자·연도·제목·출판처를 함께 보존했다. 다음은 본문 접근 또는 데이터 해석에 이용한 추가 1차 자료다. 같은 연구의 추가 경로이므로 편수를 늘리지 않는다.

- [2] Karbasi 외의 [저자 공개 원고](https://www.researchgate.net/publication/333627881_Toward_Robust_Mispronunciation_Detection_via_Audio-Visual_Speech_Recognition): GRID 대리 과제와 결합 실험.
- [3] Yu 외의 [v1 HTML 원문](https://arxiv.org/html/2609.06788v1): 동기화 설명과 사용성 평가.
- [4] 박희준의 [KCI 등록 초록](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003299473): 시스템 구성 확인.
- [12] Li의 [원문 재게시·색인](https://www.researchgate.net/publication/377142093_Enhancing_L2_sound_learning_through_the_integration_of_audio-visual_information_Phonetic_training_in_the_classroom): 초록과 원문 일부. 출판 연도는 공식 출판 페이지 기준.
- [13] Shinohara의 [저자 공개 PDF](https://researchmap.jp/7000007444/published_papers/33111962/attachment_file.pdf): 식별 훈련 연구.
- [21] Li 외의 [ACL PDF](https://aclanthology.org/2026.bea-1.3.pdf): CTC 구조와 평가.
- [42] Dai 외의 [PDF](https://arxiv.org/pdf/2403.04245): Table 1의 CER 및 oracle 분절 조건.
- [54] Cooke 외의 [저자 공식 GRID 설명](https://spandh.dcs.shef.ac.uk/gridcorpus/): 문법·화자 구성과 영상 결측 주의사항. 논문 본문 접근 제한을 이 자료로 보완.
- [61] Sage 외의 [PMC 원문 경로](https://pmc.ncbi.nlm.nih.gov/articles/PMC11359356/): 출판 원문. 접속 제한으로 확보된 원문 색인 범위만 해석에 사용.

발표 제목만 비슷한 자료, 같은 논문의 버전 중복, 실제 입력·평가 목표를 확인할 수 없는 자료는 근거 집계에서 제외했다. 직접적인 한국어 L2 시청각 진단의 대규모 독립 검증이 이 목록에서 확보된 것은 아니다.
