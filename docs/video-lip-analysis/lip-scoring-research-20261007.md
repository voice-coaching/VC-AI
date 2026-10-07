# 입술 점수 계산식 문헌 검토

2026-10-07. 작업 유형: 연구·점수 모델 설계. 원문 또는 출판사/저자 제공 자료에서 확인한 방법과 프로젝트 제안을 구분한다. 아래 연구의 인식률·동기화 성능을 한국어 발음 정확도 점수의 검증 성적으로 사용하지 않는다.

## 1. 우선 참고할 논문

| 논문 | 확인한 방법 | 이번 적용 | 적용 한계 |
| --- | --- | --- | --- |
| Honey Gupta, **Perceptual Synchronization Scoring of Dubbed Content Using Phoneme-Viseme Agreement**, WACV Workshops 2024, pp.392–402 | PhoVis: MFA 음소 시각과 입술 keypoint를 결합하고 참조 viseme와 관측 형상의 거리로 동기화를 평가 | 음소–프레임 연결과 기하 거리의 주 참고 | 더빙/입술 합성 평가이며 학습자의 발음 정답 점수 연구가 아님. 같은 clip의 화자·촬영 조건을 이용한 reference를 다른 화자로 일반화하는 것은 추가 과제 |
| Kyungsun Lee et al., **Visual Speech Recognition Using Weighted Dynamic Time Warping**, IEICE E98-D(7), 2015, pp.1430–1433 | HOG/LBP 시계열의 WDTW, 시간 변형 band, 경로 길이 정규화 | 같은 음소의 실제 입술 궤적과 참조 궤적 비교 | 시각 음성 인식이며 조음 교정 점수 검증은 아님. 한국어 단어 자료도 사용했지만 실험은 화자 종속 설정 |
| Helen L. Bear, Richard Harvey, **Phoneme-to-viseme mappings: the good, the bad, and the ugly**, Speech Communication 95, 2017, pp.40–67 | 음소→viseme의 다대일 관계와 mapping 선택 문제 | 시각적으로 구별할 수 없는 음소를 별도 정답처럼 채점하지 않는 근거 | 영어 등에서 얻은 mapping을 한국어 정답표로 직접 복사할 수 없음 |
| Tareq Altalmas et al., **Lips Tracking Identification of a Correct Quranic Letters Pronunciation for Tajweed Teaching and Learning**, IIUM Engineering Journal 18(1), 2017, pp.177–191 | 전문가/학습자의 입술 움직임을 추출해 변위·형상을 비교 | 전문가 참조 영상과 비교하는 구조의 보조 근거 | 출판사 초록 확인. 일반 사용자 확장 평가를 후속 과제로 명시. 보편적인 0–100 환산식의 검증 근거로 사용하지 않음 |
| 박희준, **스마트폰 기반 3차원 모음공간 분석 및 입술 윤곽 추출을 활용한 발음 평가·교정 시스템 개발**, 산업과 과학 5(1), 2026, pp.72–79 | KCI 초록: 포먼트 분포와 MediaPipe 입술 개구도·원순성 피드백 | 국내 한국어 관련 후속 검토 대상 | 원문 수식·독립 평가 상세를 확인하지 못했으므로 이번 계산식의 직접 근거로 채택하지 않음 |

출처:

- PhoVis [CVF 출판 페이지](https://openaccess.thecvf.com/content/WACV2024W/VAQ/html/Gupta_Perceptual_Synchronization_Scoring_of_Dubbed_Content_Using_Phoneme-Viseme_Agreement_WACVW_2024_paper.html), [본문](https://openaccess.thecvf.com/content/WACV2024W/VAQ/papers/Gupta_Perceptual_Synchronization_Scoring_of_Dubbed_Content_Using_Phoneme-Viseme_Agreement_WACVW_2024_paper.pdf), [보충 자료](https://openaccess.thecvf.com/content/WACV2024W/VAQ/supplemental/Gupta_Perceptual_Synchronization_Scoring_WACVW_2024_supplemental.pdf), [저자 소속기관 자료](https://www.amazon.science/publications/perceptual-synchronization-scoring-of-dubbed-content-using-phoneme-viseme-agreement).
- WDTW [출판 페이지](https://www.jstage.jst.go.jp/article/transinf/E98.D/7/E98.D_2015EDL8002/_article), [공개 원문](https://www.jstage.jst.go.jp/article/transinf/E98.D/7/E98.D_2015EDL8002/_pdf/-char/en). DOI `10.1587/transinf.2015EDL8002`.
- Bear/Harvey [기관 저장소](https://uel-repository.worktribe.com/output/475233), [저자 원고](https://arxiv.org/abs/1805.02934). 출판 연도는 2017, arXiv 등록은 2018이다. DOI `10.1016/j.specom.2017.07.001`.
- Altalmas et al. [출판사 초록](https://journals.iium.edu.my/ejournal/index.php/iiumej/article/view/646). DOI `10.31436/iiumej.v18i1.646`.
- 박희준 [KCI 초록](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003299473). DOI `10.23153/AI-Science.2026.5.1.072`.

## 2. 수식에서 빌려오는 부분

### PhoVis의 공간 거리

보충 자료 §3.5.1은 정규화 L2, cosine, Chebyshev, correlation 거리를 비교한다. 그중 L2 형태는 다음과 같다.

\[
d_{L2}=\frac1K\sum_{k=1}^{K}\frac{\|L_k-L'_k\|_2}{r}
\]

이번 설계는 landmark 대응점 사이의 거리를 사용하는 아이디어를 채택한다. **얼굴 기준 정규화, 한국어 문맥별 참조, 동적 궤적, 전문가 점수 보정은 프로젝트 확장**이다. 원문의 pixel threshold·clip 내부 reference·viseme 분류를 그대로 운영 정책으로 복제하지 않는다. CVF 직접 다운로드는 403이었고 본문 §3.3–3.6 및 보충 §3.5.1의 검색 색인 본문과 출판사/저자 초록을 대조했다. PDF 전체 전수 검토를 주장하지 않는다.

### WDTW의 시간 거리

원문 §2.3, 식 (3)–(6)의 핵심은 local distance에 시간차 가중치를 곱해 경로를 구하고 경로 길이로 정규화하는 것이다.

\[
C(a,b)=w_{|a-b|}d(x_a,y_b)+\min\{C(a-1,b),C(a,b-1),C(a-1,b-1)\}
\]

동일 음소라도 속도가 다를 때 비교할 수 있으나, 지나친 변형은 닫힘/열림의 시간 오류를 숨길 수 있다. 이번 설계에서는 MFA 구간 밖으로 이동하지 않는 제한 경로, 실제 PTS, 별도 duration/closure 진단을 사용한다. 원문의 HOG/LBP를 기하 특징으로 교체하고 보정 점수를 추가하는 것은 프로젝트 제안이다.

## 3. 점수에 대한 결론

검토 자료는 **음소 시각에 대응하는 참조 입술 형상과 궤적의 차이를 계산할 근거**를 제공한다. 모든 한국어 음소의 올바른 입모양을 단일 숫자로 정하는 완성된 표준 공식이 확인된 것은 아니다.

따라서 서버가 계산한 `referenceDistance`와 연구용 `referenceSimilarity`, 독립된 전문가 평정으로 보정한 `visualScore`를 구분한다. GPT는 검증된 수치와 교정 가능한 차이를 설명한다. 계산식·병렬 실행·현재 연결 결함은 [통합 설계](canonical-parallel-lip-scoring-20261007.md)에 명시한다.

검색에서 함께 확인한 [V(is)owel](https://arxiv.org/html/2507.06202v1)은 음향 포먼트의 시각화 연구로, 입술 landmark 정답 점수의 직접 근거로 채택하지 않았다. [스마트폰 음향·초음파 조음 연구](https://www.sciencedirect.com/science/article/pii/S0167639322001066)는 점수 제공 없이 조음 훈련을 지원한다고 명시하므로 0–100 입술 점수의 근거로 인용하지 않는다.
