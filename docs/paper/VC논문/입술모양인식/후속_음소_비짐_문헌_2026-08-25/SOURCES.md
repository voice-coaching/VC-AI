# 한국어 특화 및 후속 음소-비짐 문헌

- 조사·다운로드일: 2026-08-25 (Asia/Seoul)
- 기준 논문: Bear et al., *Phoneme-to-viseme mappings: the good, the bad, and the ugly* (2017)
- 범위: 정면 또는 입술 중심 발화 영상에서 한국어 시각 음성 단위를 정의하거나, 영상으로부터 음소·자소·단어·문장을 추정하는 연구, 그리고 비짐의 일대다 모호성·화자 차이·잡음 문제를 개선한 후속 연구
- 수집 원칙: 학회·저널·기관의 공식 공개 PDF만 저장했다. 검색 결과에 원문이 노출되지 않거나 접근권한이 필요한 자료는 저장하지 않았다.

## 결론과 권장 읽기 순서

1. **한국어 비짐 정의:** Choi et al. (2017)이 가장 직접적인 한국어 대응 연구다. 한국어 초성·중성·종성별 혼동행렬로 비짐군을 검증하므로 한국어 phone-to-viseme 사전과 평가용 혼동군의 근거로 적합하다.
2. **영상에서 명시적 음소 출력:** Thomas et al. (2025)의 VALLR가 가장 직접적인 현대적 후속 구조다. Video Transformer+CTC로 38개 음소를 먼저 출력하고 LLM이 문장을 복원한다.
3. **비짐의 일대다 모호성:** Kim et al. (2022)의 MVM은 Bear 논문의 핵심 문제인 `한 비짐 -> 여러 음소`를 멀티헤드 시청각 메모리로 명시적으로 모델링한다.
4. **한국어 정면 영상·대규모 화자:** Yoon et al. (2025)의 KM-AVSR은 약 6,000명 규모 AI Hub 한국어 정면 촬영 데이터를 사용한다. 다만 출력은 음소가 아니라 형태소 기반 하위 단어이고, 음성도 입력하는 AVSR이다.
5. **발음 피드백 적용:** 위 연구 어느 것도 한국어 학습자의 오발음을 판정하거나 교정 피드백을 직접 생성하지 않는다. 실제 적용에는 `시각 phone posterior + 음향 phone posterior + 기준 음소열 정렬 + 오류 규칙/피드백 생성` 단계가 추가로 필요하다.

## 다운로드 논문 비교

| 파일 | 연도·언어·데이터 | 방법과 보고 결과 | Bear (2017) 대비 진보점·intelligentAI 관련성 | 핵심 한계 |
|---|---|---|---|---|
| `2017_Choi_Analysis_Korean_Viseme_System.pdf` | 2017, 한국어; KS-MWL-A 단음절어, 남녀 화자 영상, 성인 32명 시각 전용 독화 | 초성·중성·종성 음소 혼동행렬과 60% 기준으로 한국어 비짐군 도출 | 한국어 음소-비짐 표와 시각적으로 혼동되는 음소군을 정하는 가장 직접적인 언어학적 근거 | 자동 영상 인식 모델이나 발음평가 모델은 아님 |
| `2019_Kandala_Speaker_Adaptation_Visual_Identity_Vectors_Korean.pdf` | 2019, 영어 GRID + 자체 한국어 코퍼스; 한국어 66명, 10문장, 문장당 10회 | visual i-vector를 립리딩 은닉층에 추가; GRID 미관측 화자에서 논문 보고 상대 개선 15%/10% | 화자별 입술 모양 차이를 적응 벡터로 다뤄 사용자 개인화에 연결 가능 | 단어 CTC/문장 인식이며 음소 출력·MDD가 아님; 한국어 코퍼스 비공개 |
| `2022_Kim_Multi-Head_Visual-Audio_Memory_Homophenes.pdf` | 2022, 영어 LRW/LRS2·중국어 LRW-1000 | AV 데이터로 MVM을 학습하고 추론 시 영상만으로 저장된 후보 음향 표현을 조회; 다중 시간 문맥 사용 | 일대다 viseme-to-phoneme 대응을 명시적으로 구현하여 homophene 구분을 개선 | 한국어 검증 없음; 음향 지식이 학습 단계에 필요하며 최종 출력은 단어/문장 |
| `2023_Hu_Universal_Viseme-Phoneme_Mapping_Transfer.pdf` | 2023, 영어 LRS3 433시간·LRS2 224시간 | UniVPM으로 시각 신호에서 깨끗한 음향 표현을 복원하고 잡음 AVSR/VSR에 전이 | 시각 정보의 잡음 불변성을 음향 인식에 전달하여 실제 소음 환경의 강건성 설계에 유용 | 한국어·학습자 발음평가 검증 없음; 명시적 사용자 피드백 모델이 아님 |
| `2024_Cho_Korean_Lip-Reading_Dataset_Sentence.pdf` | 2024, 한국어 방송(뉴스·다큐·드라마), 12만+ 발화; 구축 346.9시간 중 290.3시간 사용 | 균일 얼굴 ROI 전처리 + 자소 단위 Transformer-CTC; 논문 보고 GER 30.71%, CER 37.27% | 제한 단어가 아닌 대규모 한국어 문장 VSR과 한국어 자소 출력의 실증 근거 | 방송 데이터이며 통제된 학습자 정면 발음 데이터가 아님; 음소·오발음 피드백 미지원 |
| `2025_Yoon_KM-AVSR_Korean_Morpheme.pdf` | 2025, 한국어 AI Hub 입모양 음성인식 데이터; 약 6,000명 정면 촬영 | RGB/원시 음성 이중 인코더, MLP 융합, CTC+Transformer, 1,207 BPE 하위 단어; 논문 보고 CER 15.66%(기준 25.81%) | 한국어·정면·다화자 조건과 형태론을 반영한 최신 AVSR 설계 | 영상 단독이 아니라 AV 입력; 같은 단일 데이터셋의 8:2 평가이며 음소/MDD 출력이 아님 |
| `2025_Thomas_VALLR_Phoneme-Centric_VSR.pdf` | 2025, 영어 LRS2/LRS3 | Video Transformer+CTC `영상 -> 38 음소`, 미세조정 LLM `음소 -> 문장`; 논문 보고 LRS3 WER 18.7(30시간 라벨 영상) | 음소를 해석 가능한 중간 출력으로 유지하므로 한국어 phone posterior·오류 정렬 구조에 가장 직접적으로 이식 가능 | 영어 전용; LLM 문장 복원은 실제 오발음을 정상 문장으로 보정할 수 있어 피드백 경로와 분리해야 함 |

## 공식 출처와 접근 조건

| 파일 | DOI / 공식 원문 | 접근·권리 확인 |
|---|---|---|
| `2017_Choi_Analysis_Korean_Viseme_System.pdf` | DOI `10.12963/csd.17390`; <https://e-csd.org/upload/csd-22-3-615.pdf> | 공식 공개 PDF. PDF에 CC BY-NC 4.0 명시 |
| `2019_Kandala_Speaker_Adaptation_Visual_Identity_Vectors_Korean.pdf` | DOI `10.21437/Interspeech.2019-3237`; <https://www.isca-archive.org/interspeech_2019/kandala19_interspeech.pdf> | ISCA 공식 저장소 공개 PDF. PDF는 `Copyright © 2019 ISCA`; 별도 재배포 라이선스는 확인하지 못함 |
| `2022_Kim_Multi-Head_Visual-Audio_Memory_Homophenes.pdf` | DOI `10.1609/aaai.v36i1.20003`; <https://ojs.aaai.org/index.php/AAAI/article/download/20003/19762> | AAAI 공식 공개 PDF. PDF에 `All rights reserved` 명시 |
| `2023_Hu_Universal_Viseme-Phoneme_Mapping_Transfer.pdf` | DOI `10.18653/v1/2023.acl-long.848`; <https://aclanthology.org/2023.acl-long.848.pdf> | ACL Anthology 공식 공개 PDF. 2016년 이후 ACL 자료는 CC BY 4.0 |
| `2024_Cho_Korean_Lip-Reading_Dataset_Sentence.pdf` | DOI `10.9766/KIMST.2024.27.2.167`; <https://jkimst.org/upload/pdf/KIMST-2024-27-2-167.pdf> | 학회 공식 공개 PDF. PDF는 학회 저작권을 표시하며 별도 CC 라이선스는 확인하지 못함 |
| `2025_Yoon_KM-AVSR_Korean_Morpheme.pdf` | DOI `10.9708/jksci.2025.30.08.075`; <https://journal.kci.go.kr/jksci/archive/articlePdf?artiId=ART003234202> | KCI 공식 공개 PDF. PDF는 학회 저작권을 표시하며 별도 CC 라이선스는 확인하지 못함 |
| `2025_Thomas_VALLR_Phoneme-Centric_VSR.pdf` | ICCV 2025, pp. 2846-2856; <https://openaccess.thecvf.com/content/ICCV2025/papers/Thomas_VALLR_Visual_ASR_Language_Model_for_Lip_Reading_ICCV_2025_paper.pdf> | CVF 공식 Open Access 사본. 저작권은 저자 또는 권리자에게 유지되며 원 권리 조건을 따라야 함 |

공개 다운로드 가능 여부와 재배포·상업 이용 허가는 같은 의미가 아니다. 특히 CC 라이선스가 명시되지 않은 PDF는 연구용 열람 사본으로만 취급하고, 제품 번들링이나 외부 재배포 전 권리 확인이 필요하다.

## 무결성 검증

모든 파일에 대해 `file`, `pdfinfo`, 전 페이지 `pdftotext`를 실행했다. 7/7개가 PDF로 식별되었고 전 페이지 텍스트 추출이 오류 없이 완료됐다.

| 파일 | 페이지 | 바이트 | SHA-256 |
|---|---:|---:|---|
| `2017_Choi_Analysis_Korean_Viseme_System.pdf` | 14 | 1,005,904 | `d3814cc743d0888919ffde7a60a350ea7f2190432a652541f80e579171d1afb6` |
| `2019_Kandala_Speaker_Adaptation_Visual_Identity_Vectors_Korean.pdf` | 5 | 284,472 | `7593af2bc09858a7e5ee29dbb09f1c3cf2bd1fab646b28d8c50b3708bd5d45e1` |
| `2022_Kim_Multi-Head_Visual-Audio_Memory_Homophenes.pdf` | 9 | 354,070 | `76a46b618655b3abfa8dd82bae326556906c132282a64ec181e9e127fe204a20` |
| `2023_Hu_Universal_Viseme-Phoneme_Mapping_Transfer.pdf` | 20 | 4,231,984 | `4ac005b6de37b58cb3cf35cff117dbd1cd47f58078b425730ae3704ea46a676b` |
| `2024_Cho_Korean_Lip-Reading_Dataset_Sentence.pdf` | 10 | 818,522 | `b4293a6d3ff3a3db03e450b1263bbc9e1be8a119489147c585c7c4b58d4027c3` |
| `2025_Thomas_VALLR_Phoneme-Centric_VSR.pdf` | 11 | 1,033,750 | `c766e88003f78818c81d94edd13b8528d27384d82ed39a0360d7165b6c099a79` |
| `2025_Yoon_KM-AVSR_Korean_Morpheme.pdf` | 11 | 968,128 | `de8c86405eb82392947ad648bb65f70615172f93b9dd72035340f03eb954cde8` |

## 찾았지만 다운로드하지 않은 자료

- Shin, Lee, Kim (2011), *Real-time lip reading system for isolated Korean word recognition*, DOI `10.1016/j.patcog.2010.09.011`: PLV-08(20명, 한국어 100단어, 5회 반복) 기반의 중요한 한국어 선행 연구지만 Elsevier 공식 페이지에서 공개 원문 PDF를 확인하지 못해 저장하지 않았다.
- 주희열 외 (1999), *음소인식 기반의 립싱크 구현을 위한 한국어 음운학적 Viseme의 제안*: DBpia 서지 페이지는 확인했지만 공개 원문 링크를 찾지 못했다. 또한 방향이 `음소 인식 -> 립싱크`라서 사용자의 `영상 -> 음소`와 반대다.
- 주희열 외 (2000), *Korean phonological viseme for real-time LipSynch based on phoneme recognition by GMM*: KIST 저장소에는 서지만 있고 공개 첨부 원문을 확인하지 못했다. 역시 립싱크 생성 방향이다.

## 중복 다운로드하지 않은 기존 자료

- `docs/paper/VC논문/1807.05162v3.pdf`: Shillingford et al., *Large-Scale Visual Speech Recognition* (2018). 영상에서 음소 분포를 거쳐 단어를 디코딩하는 중요한 선행 구조지만 이미 저장돼 있다.
- `docs/paper/VC논문/입술모양인식/2024.findings-emnlp.666.pdf`: *VSP-LLM* (2024). 이미 대상 폴더에 있어 재다운로드하지 않았다.

## 프로젝트 적용 시 주의

- 한국어 비짐군은 Choi et al.의 표를 초기 prior/confusion group으로 사용할 수 있지만, 모델 출력 단위를 비짐으로 축소하면 오발음 차이가 사라질 수 있다. 학습 목표는 한국어 음소 posterior를 유지하고 비짐군은 보조 손실·오류 분석에 쓰는 편이 안전하다.
- VALLR의 LLM 후단은 문장 인식에는 유리하지만 발음평가에서는 오류를 정상 단어로 교정해 버릴 수 있다. MDD 판단은 LLM 이전의 음소 posterior와 CTC alignment에서 수행해야 한다.
- KM-AVSR의 15.66% CER은 해당 논문의 단일 AI Hub 데이터 8:2 실험 결과다. 사용자·화자 분리 여부와 외부 테스트가 충분히 보고되지 않아 현재 intelligentAI 데이터에 그대로 재현된 성능으로 간주하면 안 된다.
- 발음 피드백은 본 수집 논문들에서 검증되지 않았다. 한국어 학습자 영상에 대해 화자 비중복 분할, 음소 라벨 감사, 작은 smoke training, 음소 혼동·MDD 평가, 사람 청취 검증의 순서로 별도 검증해야 한다.
