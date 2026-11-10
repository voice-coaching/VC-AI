# announcer-composite-exponential-v3

사용자 요청에 따라 운율 지표의 정규화 거리 지수를 2에서 2/3으로 변경한다.
기존 [v2](composite-exponential-v2.md)는 재현 가능하게 유지하며 새 계산은 별도 정책으로 선택한다.
구현·후보 패키지 생성과 운영 배포는 별개다. 기존 운영 H5·사용자 결과는 덮어쓰지 않는다.

## 계산

```text
z = abs((userValue - mean) / std)
score = 100 * exp(-lambda * z^(2/3))
lambda = 0.2231435513142097
```

분수 지수는 `distancePower={numerator:2,denominator:3}`으로 고정한다. 음수 편차에도 실수 점수를
얻도록 절댓값을 사용한다. 계산기는 Decimal 정밀도 50에서 `cbrt(z²)`를 계산하고 마지막 표시값과
종합점수만 소수 첫째 자리 HALF_UP으로 반올림한다. 측정값·계수·평균·표준편차·배점은 바꾸지 않는다.

평균에서 1표준편차일 때는 종전처럼 80점이다. 1표준편차보다 멀면 v2보다 감점이 완만하고,
0~1표준편차 사이에서는 v2보다 감점이 크다. 평균에서는 두 버전 모두 100점이다.
이 점수는 참조 평균과의 유사도이며 정답률·모집단 백분위가 아니다.

발음 정확성은 기존 9항목 `RUBRIC_RESULT.overallScore`를 그대로 사용한다.
발음 속도·조음 속도·쉼·음높이·강도의 원시 지표에만 새 식을 적용한다. 쉼의 횟수·길이는 각자
변환한 후 기존 0.5/0.5 비율로 합친다. 입술 점수의 식과 영상 결합 배점도 바꾸지 않는다.

## 이중 감점 검증

Seungun의 `core_result.prosody.aihub71557_compatible_metrics_v2.metrics`는 초당 음절 수,
쉼 횟수·시간, F0 범위, 상대 강도 범위의 원시 측정값이다. 참조 비교 출력은 중앙값과의 차이와
구간 포함 여부이며 운율 5항목의 최종 0~100점이 아니다.

`prepare_assessment`는 공용 `assess`의 임시 점수를 만든 후 `composite_assessment`에서 대체한다.
지수 계산은 앞 단계의 `criteria[].score`를 입력받지 않고 CORE 원시 지표를 직접 읽는다.
따라서 임시 점수에 지수 감점이 누적되는 구조가 아니다. 이 중간 계산은 과거 정책 호환 경로다.
회귀 검사는 임시 점수를 0으로 바꿔도 최종 결과와 검산표가 변하지 않음을 확인한다.

2026-11-10 운영 보존 결과 확인에서 속도 두 지표는 각각 6.436781609195402와
6.603773584905661음절/초가 지수 계산에 그대로 입력됐다. 운율 6개 원시 지표 모두 CORE와
검산표 입력값이 같았고, 당시 운영 revision은 `announcer-composite-exponential-v2`였다.
이 확인은 기존 결과 읽기만 수행했으며 음향 추론·GPT·기존 작업 재실행은 하지 않았다.

## 정책·결과·GPT

- [v3 정책](../../configs/pronunciation_feedback/composite_exponential_v3.json)
- [v3 시스템 프롬프트 부속문](../../configs/coaching/composite_exponential_v3.txt)
- [계산기](../../src/voice_coach/six_dimension/composite.py)
- [회귀 검사](../../tests/analysis/test_composite_power.py)

private 결과 envelope `voice-coaching.six-dimension-result.v1`은 유지하고 policy/rubricRevision으로
계산을 구분한다. v2/v3 결과를 모두 읽으며 v3 검산표에는 `distancePowerNumerator=2`,
`distancePowerDenominator=3`, `poweredNormalizedDeviation`을 추가한다. 기존 squared 필드는
계속 실제 제곱값을 뜻하고, 새 필드가 지수 감점의 입력 거리다. 파생 Decimal 값은 문자열이다.

GPT는 새 정책·검산표를 참조하되 서버의 점수를 그대로 사용한다. 공개 API의 `score.overallScore`,
`score.details.criteria` 등 필드 이름은 유지한다. Frontend/Backend에서 재채점하지 않는다.
현재 유효성·평가불가·하위 3개 선정·사용자 피드백 길이 규칙도 유지한다.

CLI는 `--composite-policy configs/pronunciation_feedback/composite_exponential_v3.json`을 선택한다.
[후보 H5 빌더](../../deploy/dlpc/build_composite_power_h5.py)는 YuNet H5로부터 새 디렉터리에
계산기·정책·프롬프트·private 스키마만 갱신한다. CORE·원본 Seungun 출력·영상 참조·GPT 요청
설정은 상속한다. direct/internal 새 H5 identity를 AI 실행기·직렬화 계약·배포 복구 경로에 등록했다.
실제 분석 회귀 검증 후 별도 배포가 필요하며 후보 생성·등록만으로 운영 선택을 변경하지 않는다.
