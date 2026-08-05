# 연구 데이터 라이선스 정본 출처 원장

검증 시각: 2026-07-29 20:03 KST

이 원장은
[`DATA_LICENSE_AND_ROLE_MATRIX_KO.md`](./DATA_LICENSE_AND_ROLE_MATRIX_KO.md)의
라이선스·접근 분류를 1차 공식 출처와 연결한다. `verified`는
배포자·권리자 측 공식 페이지가 해당 라이선스 또는 접근 조건을
명시한다는 뜻이다. 권리 연쇄 전체에 대한 법률 의견은 아니며, 미러
metadata만으로 원 저작물의 권리 범위를 확정하지 않는다.

| 데이터 | 상태 | 정본 출처 | 확인 범위와 한계 |
|---|---|---|---|
| AI Hub 71490 | `verified` (접근·이용정책) | [71490 공식 데이터셋 페이지](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71490), [AI Hub 데이터 이용정책](https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105), [AI Hub FAQ](https://www.aihub.or.kr/aihubnews/faq/list.do) | 데이터셋 페이지는 신청과 다운로드 승인 뒤 API 이용을 명시한다. 이용정책은 본인확인·목적 고지, 출처 표시, 무단 제3자 제공 금지와 국외 반출 별도 합의를 요구하고, FAQ는 원본 외부 유출 및 사전 협의 없는 재가공 배포를 금지한다. 공개 페이지 확인은 특정 계정의 승인 상태를 증명하지 않는다. |
| FLEURS ko-KR | `verified` | Google 배포본의 [고정 revision README](https://huggingface.co/datasets/google/fleurs/blob/70bb2e84b976b7e960aa89f1c648e09c59f894dd/README.md), [CC BY 4.0 원문](https://creativecommons.org/licenses/by/4.0/legalcode) | 고정 revision metadata와 본문 모두 CC-BY를 명시한다. 이 판단은 현재 manifest에 고정한 revision에 한정하며 출처 표시 의무를 제거하지 않는다. |
| ZEROTH-Korean | `verified` | [OpenSLR SLR40 공식 배포 페이지](https://www.openslr.org/40/), [CC BY 4.0 원문](https://creativecommons.org/licenses/by/4.0/legalcode) | SLR40 페이지가 코퍼스를 CC BY 4.0으로 직접 명시한다. 라이선스 표시는 archive 무결성이나 모든 원천 권리의 별도 감사를 대신하지 않는다. |
| KSS Dataset | `license-label-verified`; `needs-original-license-review` (재배포·파생 공개) | 원 배포자 Kyubyong Park의 [Kaggle 데이터셋](https://www.kaggle.com/datasets/bryanpark/korean-single-speaker-speech-dataset)과 [공식 API metadata](https://www.kaggle.com/api/v1/datasets/view/bryanpark/korean-single-speaker-speech-dataset), 사용 중인 [고정 미러 README](https://huggingface.co/datasets/Bingsu/KSS_Dataset/blob/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/README.md), [CC BY-NC-SA 4.0 원문](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode) | Kaggle metadata는 CC BY-NC-SA 4.0, 설명은 “NC-SA 4.0”과 상업 이용 금지를 표시하고 미러 metadata도 일치한다. 다만 설명은 출판사 협조로 책에서 추출한 문장이라고 하며 그 권리 문서가 공개되지 않았다. `Kyubyong/kss` 저장소의 Apache-2.0은 모델 코드 라이선스이지 데이터 라이선스가 아니다. 따라서 비상업 격리를 유지하고 원본 재배포·파생 artifact 공개 전 원 권리범위를 재검토한다. |
| Seoul Corpus | `verified` | [OpenSLR SLR113 공식 배포 페이지](https://www.openslr.org/113/), [CC BY-NC 2.0 원문](https://creativecommons.org/licenses/by-nc/2.0/legalcode) | SLR113 페이지가 CC BY-NC 2.0을 직접 명시한다. 현 프로젝트의 원본 재배포 금지는 더 보수적인 내부 반출 정책이며, OpenSLR 페이지 자체가 별도의 재배포 금지 조항을 제시한다는 뜻은 아니다. |

검증 당시 문서 안의 공식 링크 12개는 redirect 이후 HTTP 200이었다.
FLEURS 고정 README의 SHA-256은 manifest의
`688f79f2a5c731af3796e9f683eb02f9b3f09d040decd8c5625d0f37098e71c6`와
일치했다.

공식 페이지와 정책은 변경될 수 있으므로 외부 공개·상업 전환·국외 반출
직전에 다시 검증한다. HTTP 200 확인은 URL 가용성과 당시 표시 문구만
증명하며 법률 자문이나 개별 계정 승인 증명은 아니다.
