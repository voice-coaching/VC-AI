# announcer-composite-exponential-v2

사용자가 지수 감점식으로 새 채점 버전을 만들도록 요청했고, 후속 답변에서 **발음 정확성은 기존 9항목 채점표와 서버 RUBRIC_RESULT를 그대로 사용**하도록 지정했다. 따라서 이전 제안의 GOP 참조 평균 감점·75/25 혼합은 적용하지 않는다. 기존 v1 설정·9항목 계산기·운영 H5는 보존하고 v2를 명시적으로 선택해 실행한다.

## 점수 정의

발음 정확성 `R`은 기존 `phone-rubric-20261003-v1`의 최종 `RUBRIC_RESULT.overallScore`다. 모음 25, 평음·경음·격음·마찰음·파찰음·비음 각 10, 유음 5, coverage 10을 유지한다. 단계 경계 95/80/60/40%, 표본 0의 null/배점 제외, coverage `4*(sampleCount-deletedClear-deletedFlagged)`, 정수 보조값, 마지막 HALF_UP 1자리 계산도 원래 계산기를 그대로 사용한다. 발음 정확성에는 평균 거리 감점을 추가하지 않는다.

나머지 다섯 항목의 원시 지표마다:

```
squaredDeviation = (userValue - referenceMean)^2
normalizedSquaredDeviation = squaredDeviation / referenceStd^2
score = 100 * exp(-lambda * normalizedSquaredDeviation)
```

기본 `lambda=-ln(0.8)=0.2231435513142097`. 평균과 같으면 100점, ±1 표준편차면 80점, ±2면 약 41점, ±3이면 약 13.4점이다. 정규분포 확률이나 백분위 계산이 아니라 사용자가 요청한 대칭 참조 유사도 함수다. 계수와 배점은 전문가 검토 전 임시 정책이며 평균에서 멀다는 사실을 발성 오류로 단정하지 않는다.

| 항목 | 입력 | 배점 |
| --- | --- | ---: |
| 발음 정확성 | 기존 서버 `RUBRIC_RESULT.overallScore` | 40 |
| 발음 속도 | v2 `speech_rate` | 10 |
| 조음 속도 | v2 `articulation_rate` | 10 |
| 쉼 | `pause_count_per_10_syllables`, `pause_duration_per_10_syllables`의 **각 지수 점수**를 0.5씩 평균 | 10 |
| 억양·음높이 | v2 `f0_range_st` | 15 |
| 음량·강도 | v2 `intensity_range_db` | 15 |

`total = 0.40*R + 0.10*speaking + 0.10*articulation + 0.10*pauses + 0.15*pitch + 0.15*intensity`

기존 R은 원래 규칙으로 확정된 1자리 결과를 그대로 사용한다. 운율의 지수 계산·쉼 가중평균·종합 합산은 Decimal 정밀도 50으로 수행하고 중간 표시 점수로 다시 계산하지 않는다. 표시값과 최종 종합점수만 소수점 첫째 자리 HALF_UP 반올림한다. 하위 세 항목은 표시 점수 오름차순, 동률은 여섯 항목의 고정 순서다.

## 실제 참조 자료와 유효성

정책은 `configs/pronunciation_feedback/composite_exponential_v2.json`이다. 평균·표준편차·표본 수·분위수는 `standalone/seungun/assets/reference.json`의 **현직아나운서** 그룹에서 그대로 복사했다. 각 지표의 참조 화자는 37명이며, 화자별 중앙값으로 균형 집계된 자료다. 이 정책의 중심값은 화자별 지표 분포의 **mean**이다. p50을 mean으로 바꾸어 부르지 않는다.

원본 참조 SHA-256: `e230b1fe524b18d008d1d3007d0b098cd718c9704541a70478119a03273ee722`. CLI와 H5 빌더는 파일 해시·집계 방식·정책에 복사한 통계의 일치를 확인한다. 실행 시 CORE의 참조 파일 해시·집단·버전·추출 호환성·창 정의와 지표별 값·n·분위수도 대조한다. 참조 파일 또는 집단을 바꾸려면 정책의 실제 통계와 digest를 함께 갱신해야 한다.

원래 v1의 최소 음절 수·분석 길이·F0 유효 비율 같은 측정 조건을 유지한다. F0 중앙값·F0 유효 비율 자체를 능력 점수로 더하지 않는다. 속도는 원고 음절 기반, 쉼은 RMS 무음 통계, 음높이는 전체 F0 범위, 강도는 상대 RMS 범위라는 한계도 유지한다.

필수 값 누락, 참조 불일치, 표본 2명 미만, 평균/표준편차 미설정, 표준편차 0은 해당 항목을 null/UNSCORABLE로 만든다. 한 항목이라도 계산 불가이면 **종합점수는 null**이다. 배점을 나머지 항목에 재분배하지 않는다. 유효한 다섯 항목 또는 그 일부의 점수·연습은 계속 제공한다. 0점과 평가불가는 별개다.

## 기존 발음 채점표 연결

원본 CORE에 기존 계산기가 요구하는 모든 기대 음소의 관측 상태가 있으면 기존 계산기로 검산표를 산출한다. 현재 Native/GOP CORE에는 그 상태가 없으므로 이 경우 R은 원래 규칙대로 unscorable이다. GOP나 선택 경고를 새 관측 상태로 변환하지 않는다.

이미 별도 서버 계산기가 생성한 공식 검산표가 있으면 CLI의 `--rubric-worksheet`로 제공할 수 있다. 이 파일은 서버/운영자가 관리하는 자료이며, 다음 네 키를 가진다.

```
association     # requestId, coreSha256, scriptSha256,
                # sourceAudioSha256, preparedWavSha256: 현재 실행과 정확히 같아야 함
RUBRIC_JSON
INPUT_DATA
RUBRIC_RESULT
```

모든 상태별 개수로 기존 계산기를 다시 실행하여 정수 보조값·기준표·전체 결과를 대조한다. 바인딩 불일치나 변경된 검산표는 거절한다. 바인딩 검사는 같은 시도와의 연결을 확인하며 입력의 인증이나 발음 판정의 과학적 타당성을 증명하지 않는다. 클라이언트 임의 점수를 받는 공개 API는 추가하지 않는다.

## GPT와 결과

원본 CORE JSON은 마지막 user 메시지에 원래 UTF-8 bytes 그대로 전달한다. 별도 보조 메시지에는 기존 발음 검산표, `COMPOSITE_POLICY`, `COMPOSITE_WORKSHEET`, `COMPOSITE_RESULT`를 제공한다. GPT는 `legacyGrade`에 기존 RUBRIC_RESULT를, `compositeGrade`에 새 COMPOSITE_RESULT를 그대로 복사하며 서버가 exact schema로 검증한다. 원본 점수·항목·순서를 GPT가 바꾸지 않는다.

private 결과는 기존 `voice-coaching.six-dimension-result.v1`과 `docs/contracts/six_dimension_result_v1.schema.json`을 유지한다. 정책 revision으로 새 계산 방식을 구분한다. private 종합점수는 `compositeGrade.overallScore`이며 공개 응답에서는 기존 `score.overallScore`에 전달한다. 기준 평균·표준편차·lambda·편차 제곱·정규화 편차 제곱·반올림 전 점수는 `assessment.auxiliary.composite.worksheet`에 남긴다. 큰 편차의 JSON Infinity를 피하고 수치를 보존하기 위해 파생 Decimal 근거값은 문자열로 기록한다. GPT 실패 시 서버 채점 결과는 보존한다.

사용자 피드백은 유효 항목 중 하위 최대 세 항목, 최대 3줄·5문장·300자 규칙을 유지한다. 공개 envelope와 `score.overallScore` 이름은 유지한다. 후속 사용자 요청에 따라 AI는 새 종합점수를 이 기존 필드로 연결하고, Frontend·Backend에서 공식 검산표의 revision/9항목/level을 필수로 요구하는 게이트를 제거했다. 전체 검산표는 AI private 결과에 보관하며 공개 coaching에는 포함하지 않는다. 프론트·백엔드는 종합점수의 기본 형식과 요청/음원 연결을 처리하고 채점을 재검산하지 않는다. 배포 순서는 [기존 점수 필드 연결](score-forwarding-contract-20261105.md)을 따른다. 운영 배포는 별도 작업이다.

## 실행과 패키징

개발자가 준비된 Seungun 독립 분석 결과를 사용할 때의 명령이다. Python 3.12가 필요하다. 아래 명령은 이번 작업에서 실행하지 않았다.

```sh
python scripts/analyze_six_dimension.py \
  --analysis-dir existing-seungun-result --script-file script.txt \
  --composite-policy configs/pronunciation_feedback/composite_exponential_v2.json \
  --output results/composite-v2-01 --measurements-only
```

공식 검산표가 있으면 같은 명령에 `--rubric-worksheet server-worksheet.json`을 추가한다. `--measurements-only`를 빼면 검증된 입력에 대해서만 기존 모델 `gpt-6.1-terra`, reasoning effort `none`으로 최대 한 번 요청한다. 기본 옵션에는 v1을 유지하므로 v2 정책을 명시해야 한다.

독립 실행 bundle은 기존 `deploy/dlpc/build_six_dimension.py`로 만든다. 서비스 H5 후보는 `deploy/dlpc/build_six_dimension_h5.py`에 `--composite-policy`를 지정해 별도 디렉터리로 만든다. 새 frozen loader는 composite 모듈을 lock에 포함된 경우에만 로드하며 기존 immutable H5의 모듈 순서와 파일은 유지한다. 후보 빌드가 운영 실행 identity 등록이나 배포를 뜻하지 않는다.

백업: `/home/2020067/intelligentAI/systempromptbackup/exponential-composite-20261105T001156Z`. 확인 범위는 컴파일·정책/스키마 구조·참조 통계 출처와 패키지 무결성이다. 저장소 지침에 따라 자동 회귀 테스트·실제 음향 추론·GPT 생성·피드백 품질 QA는 수행하지 않는다.
