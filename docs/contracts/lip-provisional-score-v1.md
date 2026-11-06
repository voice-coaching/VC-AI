# 영상 입술 점수 소비 계약

2026-11-06 후보 구현 기준. 배포 완료를 뜻하지 않는다. 기존 내부 result v6 및 공개
`voice-coaching.canonical-analysis-view.v3`의 필드 이름과 소유 관계를 유지한다.

## 종합점수

Frontend는 기존 `canonicalAnalysis.score.overallScore`를 표시한다. 숫자 0은 유효한 점수다.
서버가 계산한 점수를 그대로 사용하며 발음 채점표·운율·입술 점수를 클라이언트에서 다시 합산하거나 검산하지 않는다.
Direct는 기존 `result.score.overallScore` 경로를 유지한다. Direct의 음성 접수 성공을 내부 영상 접수 성공으로 해석하지 않는다.

서버 내부에서 음성 종합점수 A와 유효한 입술 총점 L이 모두 있으면 `HALF_UP(0.6A+0.4L,1)`이다.
L을 만들 수 없으면 음성 점수 A를 보존한다. A도 없으면 종합점수는 null이며 입술만으로 대체하지 않는다.
원본 Seungun JSON과 기존 발음 검산표·음성 종합 검산표는 바뀌지 않는다.

## visual 영역의 확장

공개 Backend 응답 경로는 `canonicalAnalysis.visual`이다. 내부 result v6는 `visual`이다.
기존 필드의 필수 여부는 유지하고 다음 값을 추가로 수용한다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `scoreKind` | 기존 `NONE`, `CALIBRATED`에 `PROVISIONAL` 추가 | 전문가 검증 전 참조 적합성 점수 |
| `subScores` | 선택 배열, 최대 4개 | 입술 하위 항목 요약. 구 응답에서는 필드 없음 |
| `phoneAssessments[].scoreKind` | 기존 enum에 `PROVISIONAL` 추가 | 해당 발생 위치의 임시 점수 |
| `phoneAssessments[].calibrationRevision` | 기존 문자열 또는 null | 임시 점수는 전문가 calibration이 없으므로 null 가능 |
| `phoneAssessments[].allowedActionIds` | 빈 배열 또는 `["COMPARE_REFERENCE"]` | 근거가 있는 참조 비교 연습만 허용 |
| `phoneAssessments[].subScores` | 선택 배열, 최대 4개 | 해당 발생 위치의 하위 항목과 측정 근거 |

`correctiveClaimsAllowed=false`는 유지한다. PROVISIONAL을 전문가 검증 점수·발음 정답률·오류 확률로 표시하지 않는다.
`SCORED`는 CALIBRATED 또는 PROVISIONAL 점수에 사용할 수 있다. 임시 점수에 calibrationRevision을 강제하지 않는다.
평가 범위가 부족하면 루트 `score=null, scoreKind=NONE, status=OBSERVED`이면서 개별 발생 위치에는
SCORED·PROVISIONAL과 국소 하위 점수가 있을 수 있다. 이 경우 음성 종합점수와 국소 관측을 보존한다.

## subScores 객체

| 필드 | 형식 |
| --- | --- |
| `criterionId` | `lip_aperture`, `lip_rounding`, `lip_closure`, `lip_timing` |
| `label` | 문자열 |
| `score` | 0~100 숫자 또는 null |
| `status` | `SCORED`, `UNSCORABLE`, `NOT_APPLICABLE` |
| `reasonCode` | 문자열 |
| `measurements` | 선택 배열, 최대 1개 |

SCORED는 숫자, 나머지는 null이다. `measurements`의 객체는 metricId, value, referenceMedian,
referenceScale, referenceCount, tolerance, unit을 가진다. unit은 PHONE_PHASE 또는 FACE_SCALE_NORMALIZED다.
referenceScale은 양수인 임시 비교 척도이며 모집단 표준편차라는 뜻이 아니다.
Backend는 선택 필드가 있으면 그대로 전달하고, 없으면 생략한다. null 점수는 생략하거나 0으로 바꾸지 않는다.
하위 점수는 부모 점수를 설명하는 근거이며 종합점수에 다시 더하지 않는다.

## 근거·실패·피드백

coverage의 기존 targetCount, visuallyEligibleCount, observedCount, scoredCount와 coverage 비율을 보존한다.
`NO_FRAME_IN_ALIGNED_SPAN`은 해당 음소 시간 안에 관측 프레임이 없다는 뜻이며 얼굴 가림과 다르다.
자기 비교·정렬 모호성·참조 부재는 임의 만점이나 0점으로 변환하지 않는다.

서버는 유효한 입술 연습을 최대 2개 우선 선정하고 음성 대상으로 총 3개까지 채운다.
입술 전체 점수가 보류돼도 개별 근거가 유효하면 해당 위치의 비교 연습을 선정할 수 있다.
GPT는 최대 1회 호출하고 공개 feedback은 최대 3줄·전체 5문장·300자다.

Backend·Frontend는 수치 계산과 공식 검산표 게이트를 추가하지 않는다. 인증·녹음 소유 관계·요청 및 실행 identity·
파일 무결성 검사는 유지한다. 전체 녹음·얼굴 좌표·원본 CORE를 공개 응답에 추가하지 않는다.

## 적용 순서와 확인 범위

Backend DTO/공유 schema와 Frontend reader가 위 확장을 수용한 뒤 AI의 영상 접수를 활성화한다.
타입 검사·컴파일·국소 필드 파싱은 실제 Backend 영상 정규화·result v6/handoff·공개 조회 및 운영 DB 저장을 대신하지 않는다.
이번 후보의 실제 영상 검사에서 짧은 음소 구간의 프레임 부족으로 입술 총점은 보류됐다. 참조·coverage 기준을
통과시키기 위해 임의로 낮추지 않으며 음성 점수와 측정 가능한 국소 연습을 유지한다.
