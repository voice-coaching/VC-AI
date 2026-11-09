# 브라우저 영상 피드백·세부점수 전달 수정

## 확인한 장애

2026-11-09 운영의 최근 영상 요청 두 건은 GPT HTTP 200, 요청당 POST 1회를 기록했으나
`FEEDBACK_PRACTICE_FORMAT` 검증에 실패했다. 전체 음성 점수와 6항목 점수는 이미 서버에서
계산·저장됐고 공개 피드백은 준비 실패 문구로 대체됐다. 원본 GPT 응답이 보존되지 않아
어느 표기가 위반했는지는 확정할 수 없다. 종전 검사는 화살표 공백·글머리표·화살표 생략을
모두 내용 실패와 동일하게 처리했다.

별도로 브라우저 `/summary`가 `score.details`를 제외해 화면의 6항목 점수도 누락됐다.
최근 촬영본의 정렬 구간에 보존된 63프레임은 모두 `NO_FACE`였다. 따라서 그 촬영본의
입술점수 null은 피드백 표기 실패나 참조 모델의 삭제에 의한 것이 아니다.

## 수정 동작

- 하나의 완성된 연습 지시는 화살표가 없어도 표시한다. 화살표 공백과 `->`, 중복 글머리표
  하나만 표기 정규화한다. 문장 내용·근거·점수는 새로 만들거나 바꾸지 않는다.
- 점수·근거 참조 검증, 줄 수·문장 수·길이 제한, 내부 측정값 노출 방지 검사는 유지한다.
  요청당 GPT POST 1회와 자동 재시도 금지도 유지한다.
- 기존 Seungun CORE와 시스템 프롬프트·채점 정책·AI-Hub 모델을 유지하고,
  새 H5 두 개에 `bridge/pipeline.py` 변경만 고정한다. 이전 H5와 결과는 보존한다.
- 브라우저 요약에 기존 `voice-coaching.score-details.v1` 최종 6점수를 전달한다.
  음소별 좌표·GOP·프레임·원본 분석 JSON은 전달하지 않는다.

## Frontend API 계약

`GET /browser/v1/video-analysis-jobs/{jobId}/summary`의 버전과 인증은 그대로다.
`score.details`가 선택 필드로 추가된다. 전체 결과 수집 API `/result`의 저장 bytes는 바꾸지 않는다.

| 경로 | 타입 | 표시 의미 |
| --- | --- | --- |
| `score.overallScore` | number 또는 null | 서버 종합점수 그대로 표시 |
| `score.details.schemaVersion` | string | `voice-coaching.score-details.v1` |
| `score.details.aggregationMode` | string | `AUDIO_ONLY` 또는 `AUDIO_VISUAL` |
| `score.details.audioScore` | number 또는 null | 음성 종합점수 |
| `score.details.visualScore` | number 또는 null | 입술점수 |
| `score.details.audioWeight`, `visualWeight` | integer | 종합점수의 음성·영상 비중 |
| `score.details.criteria[]` | 6개 객체 | 아래 항목의 최종 점수 |
| `criteria[].criterionId` | string | 항목 식별자 |
| `criteria[].label` | string | 한국어 항목명 |
| `criteria[].score` | number 또는 null | 항목 점수; 0과 null 구분 |
| `criteria[].status` | string | `SCORED` 또는 `UNSCORABLE` |
| `criteria[].weight` | integer | 음성 점수 안에서의 항목 배점 |
| `criteria[].reasonCode` | string | 서버 판정 사유 |
| `coaching.feedback` | string | 그대로 표시할 최종 연습 피드백 |
| `visual.score` | number 또는 null | 입술점수; null이면 숫자로 대체하지 않음 |
| `visual.scoreScope` | string | `WHOLE_CLIP`, `ASSESSED_PHONES_ONLY`, `UNAVAILABLE` |

criteria 순서는 `pronunciation_accuracy`, `speaking_rate`, `articulation_rate`, `pauses`,
`pitch`, `intensity`다. 숫자는 모두 0~100 범위다. `validationStatus`는 기존 계약의
`EXPERT_REVIEW_PENDING`이며 사용자 피드백에 면책 문구로 붙이지 않는다.

기존 Frontend의 `canonicalScoreSchema`는 이미 이 선택 필드를 읽는다. 다만 확인한
최신 dev `597cc6962058efcf7586a770f792c60cb2e1e49d`와 main
`a99ee7fa8d899a69bd0b1058348915f04266e88e`의 `browserVideoPresentation`은
화면 데이터로 넘기지 않으므로 반환 객체에 다음 한 줄을 추가해야 한다.

```ts
scoreDetails: failed ? undefined : result.score.details,
```

이후 `AnalysisView`가 기존 `analysis.scoreDetails` 경로로 표시한다. 첨부
[Frontend 패치](browser-score-details-frontend.patch)는 해당 dev 기준이다.
격리 checkout에서 성공 시 원본 객체 전달, 실패 시 제외, 과거 details 누락 시 전체 점수
유지를 실제 TypeScript 함수 실행으로 검사했다. Frontend merge·화면 QA·배포는 담당자가
진행한다. 별도 산술·릴리스 해시 검사·Backend 채점표 검증은 추가하지 않는다. 실제 배포된
Frontend revision은 별도 확인 대상이다. 누락된 과거 details는 전체 점수 표시를 막지 않는다.
요약 응답의 `X-Result-SHA256`와 `ETag`는 실제 요약 bytes를 기준으로 검증한다.
전체 수집 결과의 digest와 요약 digest를 서로 비교하지 않는다.

## 검증과 적용

새 후보의 실제 음성/장애 7건 및 시험 영상 1건은 원본 녹음과 별개 작업으로 검증한다.
검증 영수증을 후보 HEAD/tree·CORE/H5·fixture SHA에 결속한 후 PR·운영 적용한다.
기존 사용자 요청은 재분석하거나 GPT로 다시 보내지 않으며, 과거 실패 피드백을 덮어쓰지 않는다.
이 문서 자체가 운영 반영 완료 증빙은 아니다.
