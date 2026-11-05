# Native 발음 검산 연결과 GPT 단일 요청

사용자는 Seungun 원본 보존을 최우선으로 기존 발음 채점표와의 연결을 완성하고, GPT API를 한 번만 호출하도록 요청했다. 이 문서는 구현과 후보 패키지를 설명한다. **운영 활성화·실제 음성/GPT 품질 QA 완료 기록이 아니다.**

## 한 분석의 처리 순서

1. 기존 Seungun CORE bytes와 admitted binding을 검증한다.
2. `phonemeAssessment.samples`, `alignment`, `observed`, `coverage`를 읽어 별도 발음 채점 근거를 만든다.
3. 기존 순수 서버 `phone_rubric.worksheet()`로 9항목 발음 점수를 계산한다.
4. 기존 운율 5항목과 40/10/10/10/15/15 비중으로 종합점수를 계산한다.
5. system prompt, 서버 채점 자료, 원본 CORE를 **한 요청의 messages 배열**에 넣는다.
6. GPT API에 POST 한 번만 수행한다. 서버 채점 결과와 하위 최대 3개 항목의 피드백을 한 응답으로 받는다.
7. 원본·검산표·응답을 서버에서 대조하고 기존 공개 점수/피드백 필드로 전달한다.

여러 user 메시지는 여러 API 호출을 뜻하지 않는다. [OpenAI Chat Completions 요청 명세](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)는 하나의 요청에 `messages` 배열을 받는다.

`SingleCallTransport`는 호출 전에 예산을 소비한다. 실패·시간 초과 후에도 예산을 되돌리지 않는다. 준비·최종 검산은 네트워크를 사용하지 않으며, 별도 GPT 채점·수정·재시도·도구 호출 경로를 추가하지 않는다. 입력이 거절되거나 평가 항목이 전혀 없거나 원본을 포함한 입력이 한도를 초과하면 호출은 0회다. 입력을 잘라 보내거나 여러 요청으로 쪼개지 않는다.

## 원본 보존

- Seungun 소스와 기존 CORE 패키지는 수정하지 않는다.
- `core_result.pronunciation.status=not_requested`는 퇴역한 하위 경로의 값으로 그대로 둔다.
- 원본 `phonemeAssessment`, GOP, 정렬, 인덱스, 시간, 제외 내역, 음소 동치 묶음을 그대로 보존한다.
- 마지막 user 메시지는 `core_bytes.decode('utf-8')`이며 역인코딩한 bytes가 원본과 같은지 검사한다. JSON을 다시 직렬화하거나 필드 투영하지 않는다.
- 별도 자료에는 원본 SHA-256·request/script/audio/prepared-WAV binding을 기록한다.
- 외부 HTTP body의 JSON 문자열 escaping은 정상 전송 인코딩이다. 비교 대상은 messages 마지막 content를 복원한 UTF-8 bytes이며 HTTP body 전체가 아니다.

## 새 근거 정책

설정 파일은 `configs/pronunciation_feedback/native_phone_rubric_evidence_v1.json`이다.

| 설정 | 값/의미 |
| --- | --- |
| `revision` | `native-phone-rubric-evidence-v1` |
| `validationStatus` | `EXPERT_REVIEW_PENDING` |
| `sourceRevision` | `gop-ctc-af-sd-native-v1` |
| `gopFlagThreshold` | 임시 공통값 `-2.0` |
| `flagRule` | `gop <= gopFlagThreshold`이면 Flagged, 그 외 Clear |
| 정렬 모호성 | 해소되지 않은 `alignmentAmbiguous=true`는 발음 점수 평가불가 |
| 누락·비유한 값 | 보간·정상 처리·0점 대체 없이 평가불가 |
| 삽입 | 원본에 보존하며 추가 배점을 만들지 않음 |

임계값은 전문가가 검증한 발음 경계가 아니다. `Clear/Flagged`는 새 정책의 모델 지지 분류이며, 과거 검출기의 `is_error`나 인간의 정오 라벨과 같은 것으로 표시하지 않는다. GOP를 인간의 정답 확률로 바꾸지 않는다. 기존 95/80/60/40% 단계 경계는 집계된 근거 단위에만 적용한다.

| 원본 alignment operation | 지지 충분 | 지지 부족 |
| --- | --- | --- |
| `MATCH` | `matchedClear` | `matchedFlagged` |
| `SUBSTITUTION` | `substitutedClear` | `substitutedFlagged` |
| `DELETION` | `deletedClear` | `deletedFlagged` |

원본의 토큰 정렬을 실제 오발음 판정으로 해석하지 않는다. 모델이 구분하지 못하는 음소가 같은 토큰이면 추가 벌점을 주지 않으며, `indistinguishablePhones`를 기록한다. 누락된 음소를 정상으로 만들어 표본 수를 채우지 않는다.

## 인덱스·집계 검증

- `alignment.expectedIndex`는 `samples[index]`를 가리킨다. `sourceIndex`는 원래 기대 음소 위치로 별도 보존한다.
- 모든 평가 대상 표본과 관측 토큰이 원본 정렬에서 순서대로 정확히 한 번 소비돼야 한다.
- `MATCH`는 token ID가 같고 `SUBSTITUTION`은 달라야 한다. 삭제·삽입의 nullable 인덱스를 검사한다.
- 기록된 소리 없는 초성 제외 목록과 평가 표본의 sourceIndex는 중복·누락 없이 원래 위치를 구성해야 한다.
- `expected_phone_count`는 GOP 평가 표본 수다. 제외 전 sourceIndex 범위의 크기로 덮어쓰지 않는다.
- `coverage`의 표본 수·삭제 수·근거 단위를 원본과 대조한다.
- 각 음소는 8개 음소 집합 중 하나와 coverage에만 집계한다. 기존 계산기가 집계 합·정수 단위·단계·최종 반올림을 검산한다.

원본 상의 단일 유효 정렬과 전체 유한 GOP가 있을 때 채점한다. 모호성·결측 시 전체 점수 null 규칙은 유지되므로 이 변경이 모든 입력의 숫자 점수를 보장하지는 않는다.

## 저장과 검증 경계

별도 근거는 `BRIDGE_RESULT.sixDimension.rubricEvidence`에 저장한다. 주요 내용은 다음과 같다.

- 정책 원문·revision·해시와 원본 association.
- 표본별 GOP, 모델 정렬 연산, 파생 상태, token/동치 음소.
- 원본 sample/alignment JSON pointer와 sourceIndex/wordIndex/syllableIndex.
- 제외 목록, READY/UNSCORABLE 상태, 구체적 실패 사유.

기존 `legacyGrade`와 `compositeGrade`는 그대로 유지한다. GPT가 재계산하거나 점수를 바꾸면 응답을 거절한다. 실패해도 서버에서 계산한 점수는 유지하고 추가 요청하지 않는다. POST 후 내부 결과 검증이 실패한 경우 이를 `NOT_DISPATCHED`로 위장하지 않는다.

서비스의 `prepare`, `dispatch`, `verify_scoring`, `verify_result`가 같은 근거 정책을 재사용한다. 검산 단계에서는 원본 bytes를 다시 읽어 계산하며, 서비스의 Decimal 파싱 뷰를 다른 GOP 입력처럼 취급하지 않는다.

기존 외부 서버 검산표를 명시적으로 제공하는 CLI 옵션과 새 Native 파생 정책은 동시에 사용할 수 없다. 클라이언트가 점수를 제출하는 새 API는 없다.

## 공개 계약

Frontend와 Backend 코드는 변경하지 않는다. 공개 스키마 이름과 아래 필드를 유지한다.

- `score.overallScore`: 기존 0~100 종합점수 또는 null.
- `score.validity` / `score.reason`: 기존 상태값.
- `coaching.feedback` 및 Direct의 `practiceResult.feedback`: 기존 사용자 피드백.

`rubricEvidence`, `sixDimension`, 공식 검산표는 공개 coaching에서 제외한다. AI의 v4/v6 schema 변경은 새 내부 H5의 정확한 identity 등록이며 프론트에 새 필드를 요구하는 변경이 아니다. `coaching.scoring` 필수 조건은 다시 추가하지 않는다.

## 패키지와 실행

후보 디렉터리는 다음과 같다. 경로 선택을 변경하거나 서비스를 재시작하지 않았다.

```text
/home/2020067/intelligentAI-native-rubric-single-call-release-20261105/
  canonical_native_rubric_single_call_direct_20261105_v1_h5/
  canonical_native_rubric_single_call_internal_20261105_v1_h5/
```

| 경로 | H5 manifest SHA-256 |
| --- | --- |
| Direct | `5a863b03a843def4c9c1792ffe6235bbf9aa890b844bf252eff586d7e4dd870f` |
| Internal | `efd9011f2d3448730b302ad11ac3bb60f8b4e73eef7939760b0a28c453e8aabc` |

모델은 `gpt-6.1-terra`, reasoning effort는 `none`이다. 프롬프트·정책·계산기·어댑터는 dependency lock에 고정한다. 기존 composite 정책의 운율 계산식과 발음 채점표 산식은 같으며 새 근거 정책과 H5 revision으로 파생 규칙 변경을 구분한다.

개발자가 준비된 Seungun 결과를 사용해 확인할 CLI 명령은 다음과 같다. 이 명령은 이번 작업에서 실행하지 않았다.

```sh
python scripts/analyze_six_dimension.py \
  --analysis-dir existing-seungun-result \
  --script-file script.txt \
  --composite-policy configs/pronunciation_feedback/composite_exponential_v2.json \
  --native-rubric-policy configs/pronunciation_feedback/native_phone_rubric_evidence_v1.json \
  --output results/native-rubric-single-call-01
```

`--measurements-only`를 추가하면 GPT를 호출하지 않는다. 이 옵션 없이도 평가 가능한 입력에 대한 생성 호출은 최대 한 번이다. 새 옵션을 지정하지 않는 기존 프로파일은 이전 근거 정책을 유지한다.

## 확인 범위와 인계

정적 확인은 Python 컴파일, JSON/schema 문법, 공개 점수 정의 유지, exact H5/lock/prompt identity, 패키지 파일 해시 및 동결 모듈 import 경계다. 실제 녹음·GPT 생성·브라우저·자동 회귀 테스트는 실행하지 않는다.

개발자는 실제 분석에서 원본 bytes와 GPT 메시지 복원값이 같은지, 유효한 전체 근거의 발음·종합점수가 계산되는지, 요청당 POST가 한 번인지, 오류 후 재호출이 없는지, 모호·결측 입력이 null로 남는지, 기존 공개 응답이 화면에 표시되는지 확인해야 한다. 이 단계는 아직 완료되지 않았다.

변경 전 백업은 `/home/2020067/intelligentAI/systempromptbackup/native-rubric-single-call-20261105T025237Z`다. 운영 반영 시에는 해당 시점의 서비스 상태와 선택된 릴리스를 재확인하고 coordinated 전환을 수행한다.
