# CLOVA overallScore 기준표 v1 — 이전 계약

상세 항목과 조건부 채점 프롬프트는 [v2 기준표](clova-detailed-scoring-v2.md)를 사용한다. 아래는 기존 저장 결과 호환을 위한 v1 기록이다.

버전: `clova-phone-rubric-v1`. 서비스의 발음 학습·승급용 운영 기준이며, 사람 평가와 보정된 발음 정확도·확률·공인 시험 점수가 아니다. 억양·속도·입술 품질은 측정 근거가 없어 채점하지 않는다.

## 입력과 배점

같은 녹음에 대한 Seungun 전체 기대 음소 목록과 모든 음소의 정렬·검출 결과를 사용한다. 선택된 교정 음소 하나나 detectorScore의 크기를 종합 점수로 환산하지 않는다.

| 항목 | 배점 | CLOVA 계산 기준 |
| --- | ---: | --- |
| 정렬 일치 | 60 | 60 × C / N. C는 alignment_operation=correct 개수 |
| 정렬 커버리지 | 20 | 20 × A / N. A는 correct 또는 substitution 개수 |
| 검출 기준 충족 | 20 | 20 × U / N. U는 is_error=false 개수 |

N은 전체 기대 음소 수(1~4096). 각 항목은 소수점 첫째 자리 HALF_UP 후 합산하여 overallScore(0~100)를 만든다. 예: N=10, C=7, A=9, U=9 → 42.0+18.0+18.0=78.0.

CTC deletion/substitution은 관측 후보이며 실제 생략·오발음을 확정하지 않는다. unflagged는 오류가 없다는 보증이 아니다. 100점도 모든 발음이 정확하다는 보증이 아니다. 인식 누락이 정렬 일치와 커버리지에 함께 반영되는 것은 v1의 명시적인 배점이다. 향후 사람 평가에 따른 보정은 새 기준표 버전으로 관리한다.

## 생성과 검증

RunPod의 고정 revision HyperCLOVA에 개인 식별자·경로·미디어 없이 N/C/A/U 집계와 위 기준표만 전달한다. CLOVA는 alignmentScore, coverageScore, stabilityScore, overallScore 네 숫자를 생성한다. temperature=0이며 모델 ID·revision·정상 종료·중복 키·유한 숫자·필드 집합을 검사하고 워커와 백엔드에서 산식을 다시 검증한다.

근거 불완전, CLOVA 장애, 잘못된 출력 또는 산식 불일치는 `analysis_execution_failed_closed`로 분석을 실패 처리한다. 임의 점수나 문구 fallback의 점수로 승급시키지 않는다. 사용자는 실패 분석 retry API로 재시도할 수 있다. 피드백 문구의 기존 fallback과 점수 실패 처리는 별개다.

## HTTP 계약과 저장

- 기존 v1 결과는 계속 수신하고 점수 필드를 거절한다.
- `voice-coaching.runpod-analysis-result.v2`의 COMPLETED 결과에는 overallScore와 scoringEvidence가 필수다. COACHING_READY와 COMPLETED_NO_ISSUE에 모두 적용한다. FAILED의 두 필드는 null이다.
- scoringEvidence: rubricRevision, generator=hyperclova, modelRevision, evidenceSha256, expectedPhoneCount, alignedPhoneCount, correctPhoneCount, unflaggedPhoneCount, alignmentScore, coverageScore, stabilityScore.
- 기존 owner·lease·deadline·해시·eventId 중복 검증을 유지한다. 전체 콜백의 canonical digest에 점수와 근거도 포함한다.
- 백엔드는 동일 트랜잭션에서 overall_score와 clova_score_evidence(JSONB)를 저장한다. V27 migration은 기존 결과에 점수를 채워 넣지 않는다.
- 공개 `GET /api/analyses/{analysisId}`는 기존 overallScore 필드에 number를 반환한다. STT·다른 점수·segments는 이번 변경으로 생성하지 않는다.
- 승급 submit은 소유자·시험 세션·콘텐츠·선택 녹음·완료 상태를 검사한 뒤 DB의 overallScore와 시험 생성 시 저장된 passingScore를 비교한다. 클라이언트 점수는 받지 않는다.
- 점수 없는 기존 분석은 승급 채점에 사용하지 못한다. 새 녹음 분석이 필요하다. 완료된 분석을 retry로 재채점하거나 기존 점수를 소급 변경하지 않는다.

## 배포 순서

백엔드 v1/v2 호환 계약과 V27을 먼저 배포한다. 이후 워커 코드를 배포하고 `AI_ANALYSIS_CLOVA_SCORING_ENABLED=true`를 해당 HTTP 워커에 설정한다. 모델 가중치는 변경하지 않는다. 워커 설정 기본값 false는 기존 배포 호환용이며 운영 활성화 여부는 따로 검증한다.
