# 영상 입술 채점·피드백 논문 검토

기준일: 2026-11-06. 작업 범위는 문헌 수집과 설계다. 운영 코드·모델·시스템 프롬프트를 변경하거나 사용자 영상을 새로 분석하지 않았다.

## 1. 수집 결과와 읽은 범위

총 16편을 정리했다. 출판사·저자 기관·학술대회·arXiv의 공개 PDF 15편을 저장했고, P14는 다운로드에 실패하여 출판사 초록만 확인했다.
기존 [2026-10-07 문헌 검토](lip-scoring-research-20261007.md)를 보완하며 과거 기록은 보존한다.

- [전체 서지·원문 URL·다운로드 상태·SHA-256](../../literature/lip-analysis-20261106/manifest.json)
- [BibTeX](../../literature/lip-analysis-20261106/references.bib)
- 로컬 PDF 디렉터리: `/home/2020067/intelligentAI/literature/lip-analysis-20261106/papers/`
- 각 PDF의 관련 방법·실험·제한 절을 확인했다. 논문의 실험 재현이나 전체 데이터 검증을 수행한 것은 아니다.
- 원문 PDF는 로컬 열람 자료다. 논문·데이터·모델의 공개 접근 가능 여부와 재배포·상용 사용 조건은 별개이며 이번 작업에서는 Git에 추가하거나 외부에 재배포하지 않았다.
- 아래 '적용'은 프로젝트 설계 제안이다. 논문이 현재 제품의 한국어 0~100점이나 임시 가중치를 검증했다는 뜻이 아니다.

## 2. 문헌 목록

| ID | 논문·발표 정보 | 연구 대상과 채택할 부분 | 주요 한계 |
| --- | --- | --- | --- |
| P01 | Kjellström et al., **Audio-visual phoneme classification for pronunciation training applications**, Interspeech 2007. [원문](https://www.isca-archive.org/interspeech_2007/kjellstrom07_interspeech.html), DOI `10.21437/Interspeech.2007-294` | 스웨덴어 음소의 음성·입 영상 분류. 음성·영상의 근거를 각각 확보하고 결합하는 기준 비교 | 한 화자 실험. 분류 성공률을 한국어 입술 정확도 점수로 복사하지 않음 |
| P02 | Lee et al., **Visual Speech Recognition Using Weighted Dynamic Time Warping**, IEICE, 2015. [원문](https://www.jstage.jst.go.jp/article/transinf/E98.D/7/E98.D_2015EDL8002/_article), DOI `10.1587/transinf.2015EDL8002` | HOG/LBP 특징과 시간 왜곡을 제한하는 WDTW. 적은 참조로 궤적 비교하는 기준 모델 | 인식 연구이며 교정 점수 연구가 아님. 한국어 자체 자료 실험도 화자 종속 평가. landmark 특징으로 바꾸는 것은 별도 검증 대상 |
| P03 | Gupta, **Perceptual Synchronization Scoring of Dubbed Content Using Phoneme-Viseme Agreement**, WACV Workshops, 2024. [원문](https://openaccess.thecvf.com/content/WACV2024W/VAQ/papers/Gupta_Perceptual_Synchronization_Scoring_of_Dubbed_Content_Using_Phoneme-Viseme_Agreement_WACVW_2024_paper.pdf) | PhoVis: 음소 시간과 입술 형상 참조를 연결해 더빙 동기화를 평가. 음소–프레임 결합 방식 참고 | 더빙의 지각적 동기화와 학습자 조음 정확성은 다른 목표. clip 내부 참조를 모든 화자의 정상 입 모양으로 일반화하지 않음 |
| P04 | Bear & Harvey, **Phoneme-to-viseme mappings: the good, the bad, and the ugly**, Speech Communication, 2017. [저자 원고](https://arxiv.org/abs/1805.02934), DOI `10.1016/j.specom.2017.07.001` | 여러 음소가 하나의 시각 범주를 공유하는 문제. 시각적으로 판정 가능한 항목을 제한하는 근거 | 영어 mapping을 한국어 정답표로 직접 사용하지 않음. 입술 관측만으로 음소 전체의 정답을 보증할 수 없음 |
| P05 | Yu et al., **Visual Emphasis of Lip Protrusion for Pronunciation Learning**, IEICE, 2019. [원문](https://www.jstage.jst.go.jp/article/transinf/E102.D/1/E102.D_2017EDP7411/_article/-char/en), DOI `10.1587/transinf.2017EDP7411` | 입술 돌출을 시각적으로 강조하는 학습 지원. 43명 대상 실험과 시연 방식 참고 | RGB-D 카메라로 깊이를 측정한 연구. 정면 RGB 영상이나 MediaPipe의 추정 z값을 실제 돌출 길이로 바꾸는 근거가 아님 |
| P06 | Bu et al., **Visual-speech Synthesis of Exaggerated Corrective Feedback**, ACM Multimedia, 2020. [저자 원고](https://arxiv.org/abs/2009.05748), DOI `10.1145/3394171.3414444` | 움직임·길이·시각 대비를 강조한 교정 시연. 지각·훈련 사용자 실험 참고 | 32명 식별 실험과 14명 훈련 실험 등 규모·과제가 제한됨. 자동 오류 검출기의 타당성을 검증한 논문이 아님. 과장 시연을 실제 발화의 영구 목표로 강요하지 않음 |
| P07 | Son, Kim & Cho, **Supralaryngeal articulatory signatures of three-way contrastive labial stops in Korean**, Journal of Phonetics, 2012. [출판사](https://www.sciencedirect.com/science/article/pii/S0095447011001021), [저자 기관 PDF](https://tcho.hanyang.ac.kr/documents/11105253/13205893/Son_Kim_Cho_2012.pdf/a72bf46d-54b4-16b9-94cb-0492843bccec?t=1743398926092), DOI `10.1016/j.wocn.2011.11.002` | 서울말 7화자의 EMA 실험. 양순 파열음·인접 모음의 폐쇄 및 움직임을 조건별로 다루는 근거 | EMA의 운동학 결과다. 휴대전화 2D 영상으로 평음·경음·격음을 독립 판별할 수 있다는 검증은 아님 |
| P08 | Son, **Word-boundary and rate effects on upper and lower lip movements in the articulation of the bilabial stop /p/ in Korean**, 말소리와 음성과학, 2018. [학회 원문](https://www.eksss.org/archive/view_article?pid=pss-10-1-23), DOI `10.13064/KSSS.2018.10.1.023` | 서울말 8화자, 단어 경계·속도 조건의 입술 움직임. 속도와 문맥을 참조 조건에 포함 | 관측된 정상 변이를 오류로 처리하지 않도록 함. 논문의 위치 수치를 카메라 landmark의 허용 오차로 직접 복사하지 않음 |
| P09 | Shi et al., **Learning Audio-Visual Speech Representation by Masked Multimodal Cluster Prediction**, ICLR, 2022. [저자 원고](https://arxiv.org/abs/2201.02184) | AV-HuBERT. 기하 특징 기준 모델 이후 검토할 학습 표현 | 주 평가는 음성 인식·독순. WER나 임베딩 유사도를 입술 정확도·교정 방향으로 해석하지 않음. 현재 CPU/GPU 예산도 별도 측정 필요 |
| P10 | Park et al., **OLKAVS: An Open Large-Scale Korean Audio-Visual Speech Dataset**, ICASSP 2024. [저자 원고 v2](https://arxiv.org/abs/2301.06375) | 한국어 1,107화자, 1,150시간, 여러 시점의 자료. 한국어 시각 표현·정상 변이 연구 후보 | preprint 최초 공개는 2023, 수집 버전은 2025년 갱신 v2. 전사·인식용 자료가 전문가의 입술 오류·교정 라벨을 대신하지 않음 |
| P11 | McGhee, Gales & Knill, **Comparative Pronunciation Assessment and Feedback with Interpretable Speech Features**, SLaTE, 2025. [기관 원고](https://www.repository.cam.ac.uk/items/092494b9-905d-4941-951b-cbd992460ef1), DOI `10.21437/slate.2025-8` | 설명 가능한 특징과 여러 발화 참조를 이용하는 비교형 평가. 단일 참조 때문에 허용 발화를 오류로 잡는 문제 참고 | 실제 입술 영상 연구가 아니라 음향에서 추정한 조음 특징을 사용함. 단어 수준 성능 제한도 명시하므로 입술 교정 검증 근거로 대체하지 않음 |
| P12 | Yu et al., **When Speech Meets Lips: Interpretable Audio-Visual Synchronization for L2 Pronunciation Assessment**, arXiv, 2026. [원문](https://arxiv.org/abs/2609.06788) | attention·lag 궤적·LSI와 시각화. 30명 사용자 설문을 포함한 탐색적 후속 방향 | 이번 조사에서 동료심사 확인 못 함. 설문·동기화 지표를 한국어 입술 오류 정확도·학습 향상 검증으로 취급하지 않음. 코드·독립 재현·정답 라벨 평가가 추가로 필요 |
| P13 | Kartynnik et al., **Real-time Facial Surface Geometry from Monocular Video on Mobile GPUs**, CVPR Workshop/저자 원고, 2019. [원문](https://arxiv.org/abs/1907.06724) | 단안 영상의 468점 얼굴 mesh. 현재 landmark 추출 계열의 기술 배경 | AR용 근사 geometry이며 발음 평가 모델이 아님. 좌표의 정밀도를 곧바로 전문가 채점 정확도로 해석하지 않음 |
| P14 | Altalmas et al., **Lips Tracking Identification of a Correct Quranic Letters Pronunciation for Tajweed Teaching and Learning**, IIUM Engineering Journal, 2017. [출판사 초록](https://journals.iium.edu.my/ejournal/index.php/iiumej/article/view/646), DOI `10.31436/iiumej.v18i1.646` | 전문가·학습자의 입술 변위 비교라는 직접적인 유사 구조 | 이번에는 초록만 확인. 대상 확대를 후속 과제로 설명하므로 일반화된 한국어 채점식의 근거로 채택하지 않음 |
| P15 | Schwartz & Savariaux, **No, There Is No 150 ms Lead of Visual Speech on Auditory Speech, but a Range of Audiovisual Asynchronies Varying from Small Audio Lead to Large Audio Lag**, PLOS Computational Biology, 2014. [원문](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003743) | 준비 동작과 연속 발화의 동작을 구분하고 자연스러운 A/V 시간 관계가 가변적임을 보임 | '0ms가 정답'이나 '항상 150ms 선행이 정답'이라는 채점식을 채택하지 않는 핵심 근거. 논문의 범위를 모든 한국어 허용 범위로 고정하지 않음 |
| P16 | Chandrasekaran et al., **The Natural Statistics of Audiovisual Speech**, PLOS Computational Biology, 2009. [원문](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1000436) | 입 벌림·음향 envelope·발화 리듬의 자연적 관계를 분석 | 발음의 정답/오답 평가가 아님. 시각 선행에 관한 해석은 P15의 조건별 재검토와 함께 읽어 고정 지연 규칙을 만들지 않음 |

## 3. 논문에서 지지되는 방향과 제품에서 검증할 가설

### 채택할 방향

1. **음소·문맥에 연결된 움직임 비교:** 얼굴 전체나 한 프레임의 입 크기를 점수화하는 대신, 목표 소리와 인접 문맥의 입술 동작을 비교한다. P02·P03은 비교 계산, P07·P08은 한국어 조건 설정의 참고다.
2. **시각 평가 범위를 제한:** 눈으로 보이는 입술 벌림·폭·폐쇄·움직임과, 음향에서 얻는 발음 근거를 분리한다. P01·P04·P07.
3. **정상 시간 관계 보존:** 파일의 동기화 오류와 실제 조음의 자연스러운 선행·후행을 구분한다. P15·P16. attention 대각선 일치 자체를 정답으로 삼지 않는다.
4. **점수와 행동 지시를 별도로 검증:** 높은 참조 거리는 교정 방향을 알려 주지 않는다. 차이의 방향과 교정 행동을 독립적으로 연결한다. P05·P06·P11.

### 프로젝트 가설이며 논문이 보장하지 않는 것

- 기하 거리의 지수 변환을 0~100점의 초기 연구 점수로 사용하는 것.
- 음성 60%·입술 40%의 종합 비중과, 영상 피드백 3개 중 2개를 입술에 우선 배정하는 것.
- 한국어 음소별 기준 분포·허용 범위·교정 문구 및 현재 하드웨어에서의 지연 목표.
- MediaPipe, WDTW 또는 AV-HuBERT를 조합하면 한국어 전문가와 충분히 일치할 것이라는 기대.

위 항목은 [실행 계획](lip-scoring-feedback-plan-20261106.md)의 자료 구축·전문가 평가·별도 테스트를 거쳐 판단한다.
공통 입 벌림 평균 하나와 카메라 신뢰도만으로 발음 정확도를 계산하는 방식은 채택하지 않는다.

## 4. 근거를 과장하지 않기 위한 판독 규칙

- `음소/단어 인식률`, `더빙 동기화 점수`, `참조 유사도`, `전문가가 평정한 보이는 조음 적절성`, `실제 학습 효과`는 다른 결과 변수다.
- 얼굴 검출 신뢰도를 발음 점수에 곱하지 않는다. 가림·검출 실패는 관측 가능성의 문제다.
- 논문에 여러 언어·한국어 자료가 포함됐다는 사실만으로 현행 화자·기기·교육 목표의 일반화를 주장하지 않는다.
- 영어용 L2 음성 평가나 audio+text 모델을 입술 영상을 검증한 연구로 분류하지 않는다.
- P12와 같은 최신 preprint는 탐색 후보로 기록하며 기존 동료심사·한국어 원저를 대체하는 결정적 근거로 사용하지 않는다.

검색 범위는 위 주제의 목적 지향 문헌 조사다. 전 세계 관련 연구를 빠짐없이 포함한 체계적 문헌고찰이나 메타분석을 수행했다는 주장은 하지 않는다.
