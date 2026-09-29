# GPT-6 연결의 목표 설계

작성일: 2026-09-30. 최초 목표 설계이며, 이후 구현한 P0/P1의 확정 계약·제한·배포 상태는 [실행 기록](p0-p1-deployment.md)을 따른다. 아래 가상 예시 전체가 그대로 공개 API에 도입된 것은 아니다. P2 개인 교정은 계속 비활성이다.

[목차](README.md) · [구체적인 수정·도입 순서](implementation-plan.md)

이 문서에서 “원래 연결되어야 하는 방식”은 확인되지 않은 과거 설계 의도를 복원한다는 뜻이 아니다. 프로젝트의 근거 보존 원칙과 현재 배포의 제약을 기준으로 제안하는 바람직한 역할 분담이다. 실제 배포 사실은 [2026-09-29 관측](deployment-evidence.md), 처리 과정은 [pipeline.md](pipeline.md)에 별도로 보존한다. 이번 작성에서는 Pod를 재조회하지 않았다.

## 1. 결론: GPT는 측정기가 아니라 근거에 제한된 코치

**Seungun은 음성의 확인 대상을 찾고, 영상 파이프라인은 같은 구간의 보이는 입술을 측정하며, 서버가 두 근거로 말할 수 있는 범위를 정하고, GPT-6는 그 범위의 연습을 이해하기 쉽게 설명해야 한다.**

단순히 원본 영상이나 ratio를 GPT에 주고 “틀린 입모양을 교정해 달라”고 하는 구조는 권장하지 않는다. GPT의 추론 능력으로 영상의 시간 정렬, 유효 표본 부족, reference 부재를 보완했다고 간주해서도 안 된다.

| 담당 | 정해야 하는 것 | 정하면 안 되는 것 |
|---|---|---|
| Seungun | 원본 음소열·검출 근거·CTC 구간 | 영상 입모양의 실제 상태, 보정된 학습 점수 |
| 영상 측정기 | 동일 영상의 좌표·수치·시각·QC·sync 근거 | 개인 발음 오류의 원인, 교정 방향의 정답 |
| 서버 근거 정책 | 후보·관찰 연결, 사용 가능성, 허용 statement와 action | 없는 표본·신뢰도·평가 결과 만들기 |
| GPT-6 | 허용된 대상의 간결한 설명·연습·self-check | 후보 추가, 숫자 변경, 교정 권한 확대, 과거 사례를 현재 진단으로 복사 |
| 결과 renderer | 서버 원본 근거와 생성 문장 결합, 총평 제한, fallback | GPT가 출력한 근거 ID를 검증 없이 신뢰 |

`gpt-6-astra`와 현재 Chat Completions 경로를 우선 유지한다. 모델 교체, 다른 API로의 이전, raw 영상 전송은 이 개선의 선행조건이 아니다.

## 2. 이미 연결된 부분과 부족한 부분

현재는 `analyze_coaching_observations`가 만든 관찰이 `build_coaching_input.visual`에 들어가고, `messages_for`가 `currentAttempt`로 직렬화해 GPT에 보낸다. 따라서 **연결이 없는 것이 아니라, 연결의 의미·추적성과 교정 근거가 제한되어 있는 상태**다.

| 현재 구현/관측 | 목표와 수정 방향 |
|---|---|
| 관찰은 `expectedIndex`, 값, 구간, decode 시각을 가짐 | `candidateId`·음성 `evidenceId`·영상 `observationId`를 명시적으로 결합 |
| 기본 중심 1프레임과 beta 구간 평균이 비슷한 measurement 형태 | `aggregation`, 실제 사용 시각, 유효 표본 수, 정확한 측정 범위를 구분 |
| `sampleCount=null` | upstream에서 실제 수를 측정한 뒤 새 계약으로 제공; decode 수로 채우지 않음 |
| `project_candidates`는 후보·facts를 줄이지만 visual은 복사 상태 유지 | 제외한 후보의 영상 근거도 GPT view에서 함께 제거 |
| 내부 binding/QC/sync 검사 후 일부 정보만 GPT에 투영 | 내부 검증은 유지하고 GPT에는 제한된 quality 상태·불가 사유를 전달 |
| sidecar의 교정 claim과 GPT용 관찰의 권한이 다름 | GPT 전용 policy가 관찰/비교/교정 허용 범위를 명시적으로 결정 |
| 생성 결과는 후보·guidance ID만 참조 | 영상 statement 참조를 새 내부 생성 계약에서 검증; 실제 사용 여부를 추정하지 않음 |
| JSON mode와 로컬 구조/길이 검사 | strict schema + 근거 참조 검사 + 의미 위험에 대한 안전한 renderer를 함께 적용 |
| 동일 reference를 40개 음소에 적용, calibration/evaluation 각 2사례 | 음소·역할·문맥·촬영조건에 맞는 별도 검증 전까지 개인 영상 교정 금지 |

코드 근거는 [coaching_evidence.py](../../src/voice_coach/backend_analysis/coaching_evidence.py)의 `project_candidates`, [coaching.py](../../src/voice_coach/backend_analysis/coaching.py)의 `pack`·`validate_generated`, [visual adapter](../../src/voice_coach/backend_analysis/adapters/visual.py)의 관찰 투영이다. 로컬 provider 조립에는 과거 Clova 분기가 남아 있으므로 배포 상태 판단에는 [배포 관측](../runpod_deployed_pipeline_20260928.md)을 우선한다.

## 3. 권장 처리 순서

1. 기존과 같이 음성 분석을 한 번 수행하고 원본 Seungun 결과를 보존한다.
2. 버전 있는 코칭 정책으로 허용 후보 최대 3개와 대표 음성 근거를 정한다.
3. 후보별 영상 관찰을 수집한다. 영상 오류는 해당 후보의 관찰 불가로 처리하되 요청 전체의 무결성 오류와 구분한다.
4. 서버에서 같은 시도·음소·시간축·QC·sync·표본·reference 적용 범위를 검증한다.
5. 후보마다 `allowedStatements`, `allowedActions`, `forbiddenClaims`를 결정한다. 이 목록은 GPT나 원시 요청 데이터가 정하지 않는다.
6. 최종 입력 예산에 맞춰 후보·음성 근거·영상 관찰·허용 statement를 **하나의 묶음**으로 줄인다. source 전체와 GPT view를 분리한다.
7. system 규칙, 기존 두 전문가 사례, 현재 시도 view를 한 요청으로 GPT에 전달한다. 새 추가 생성 호출을 기본값으로 넣지 않는다.
8. 출력 구조·후보 coverage·참조 권한·문장 형식을 검사하고, 통과한 문장에만 서버 원본 근거를 붙인다.
9. 실패하면 동일 후보의 일반 목표음 연습으로 돌아간다. 영상이 없거나 실패했다고 유효한 음성 결과를 지우지 않는다.

영상 분석을 후보별 별도 GPT 호출로 나누지 않는 이유는 후보 간 설명 중복, 상태 결합, 비용·지연과 실패 경계를 불필요하게 늘리지 않기 위해서다. 이 판단은 이 프로젝트의 설계 제안이다.

## 4. 관찰과 교정의 세 단계

아래 상태명은 **새 내부 정책의 제안값**이다. 현재 API의 enum이 아니다.

| 단계 | 조건 | 허용되는 사용 |
|---|---|---|
| `UNAVAILABLE` | 영상 없음, binding/QC/sync/표본 조건 불충족 | 음성 코칭만 수행; 관찰 불가를 오류나 정상으로 바꾸지 않음 |
| `OBSERVATION_ONLY` | 실제 관찰은 있으나 적용 가능한 교정 reference 없음 | 측정 범위의 중립적 관찰 또는 replay 안내; 일반 목표음 연습과 개인 진단을 구분 |
| `VALIDATED_CORRECTION` | 독립적으로 검증된 reference·action 정책이 해당 음소/문맥/촬영조건에 적용 가능 | 서버가 승인한 영상 교정 action만 설명; 여전히 혀·기류·의학적 원인 추정 금지 |

**현재의 목표값은 `OBSERVATION_ONLY` 또는 `UNAVAILABLE`다.** 마지막 단계는 연구·전문가 검증·계약 개정·운영 승인이 끝난 후의 장기 목표다. `correctiveClaimsAllowed=true` 한 줄 변경으로 활성화하지 않는다. 권한은 관찰/claim별로 결정하고 전역 플래그로 전체 영상을 승격하지 않는다.

영상과 음성 근거가 상충할 때도 한쪽을 “정답”으로 골라 확정하지 않는다. 예를 들어 음성이 확인 후보인데 입술 관찰은 reference 차이가 없어도 음성의 오류를 영상이 부정했다고 쓰지 않는다. 외부 입술로 관찰할 수 없는 음향 차이가 있기 때문이다. 영상만으로 새 코칭 후보를 만드는 확장은 별도 근거 정책 없이는 제외한다.

## 5. 권장 내부 입력 계약

현재 공개 callback DTO와 다른 **내부 `coaching-input.v2` 제안**이다. 필드명과 enum은 구현 전에 정식 schema로 확정해야 한다.

### 서버에만 남길 정보

request/execution binding, 음성·영상·PCM digest, QC/sync/window receipt, 모델·정책·reference revision 및 hash를 내부 provenance로 유지한다. 원본 경로·객체 key·인증정보·raw 좌표 배열은 GPT view에서 제외한다. GPT에는 필요하면 요청 내부의 짧은 불투명 evidence ID만 준다.

이는 Backend–RunPod 요청에 새 grant·서명·정책 revision 필드를 요구한다는 뜻이 아니다. 기존 양방향 인증·소유 관계·무결성 확인을 유지하고, 정책 revision은 RunPod가 관리하는 실행 자산이다.

### 관찰이 가져야 할 내용

- 연결: `observationId`, `candidateId`, `audioEvidenceId`, `expectedIndex`. 서버가 실제 매핑을 검증한다.
- 시간: 음성 CTC 구간, offset 적용 후 영상 구간, 사용 프레임 시각을 구분한다. GPT가 ms를 재계산하지 않는다.
- 측정: cue·unit·value와 `aggregation`을 결합한다. 예: `NEAREST_FRAME`, `MEAN_IN_PHONE_SPAN`, `MAX_IN_PHONE_SPAN`.
- 표본: decode 프레임 수와 cue별 유효 프레임 수·사용 시각을 따로 기록한다. 변화율에는 직전 context 프레임 의존성도 남긴다.
- 품질: QC·sync의 적용 policy와 통과/불가 상태, 관찰 불가 사유. “confidence 95%” 같은 미보정 수치를 새로 만들지 않는다.
- 해석: reference 적용 여부, 허용 statement/action 및 금지 claim. 유효값 0과 missing/null을 구분한다.

모든 quality 세부값을 매번 GPT에 보낼 필요는 없다. 내부 원본은 보존하고, 문장 선택에 필요한 상태와 사유만 allowlist로 투영한다. 기존 측정기가 모르는 필드는 null/unknown으로 남기며, 이 경우 더 강한 해석을 막는다.

### 가상 예시: 관찰 전용 후보

아래 수치와 ID는 설계 설명용이며 **실제 녹음 결과가 아니다**. 현재 parser에 그대로 넣을 수 있는 payload도 아니다. 상위 utterance·facts·guidance 등은 생략했다.

```json
{
  "schemaVersion": "voice-coaching.coaching-input.v2",
  "candidates": [
    {
      "candidateId": "candidate-5",
      "evidenceIds": ["phone-5"],
      "visualObservationIds": ["visual-5-0"],
      "allowedStatementIds": ["statement-5-replay"],
      "allowedVisualActionIds": []
    }
  ],
  "visualObservations": [
    {
      "observationId": "visual-5-0",
      "candidateId": "candidate-5",
      "audioEvidenceId": "phone-5",
      "expectedIndex": 5,
      "interpretationMode": "OBSERVATION_ONLY",
      "videoPhoneSpanMs": {"start": 1000, "end": 1020},
      "measurements": [
        {
          "cueId": "lip_aperture_ratio",
          "value": 0.08,
          "unit": "ratio",
          "aggregation": "NEAREST_FRAME",
          "usedFrameTimesMs": [1000.0],
          "validSampleCount": 1,
          "selectedFrameInsidePhoneSpan": true
        }
      ],
      "decodedFrameCount": 6,
      "quality": {"qc": "PASSED", "sync": "POLICY_ACCEPTED"},
      "referenceStatus": "NOT_VALIDATED",
      "correctiveClaimsAllowed": false
    }
  ],
  "allowedStatements": [
    {
      "statementId": "statement-5-replay",
      "candidateId": "candidate-5",
      "observationIds": ["visual-5-0"],
      "kind": "NEUTRAL_REPLAY_GUIDANCE",
      "text": "같은 구간을 다시 보며 입술 움직임을 확인해 보세요."
    }
  ]
}
```

이 예시의 `validSampleCount=1`은 **미래 구현에서 실제 사용 프레임을 센 경우만** 유효하다. 현재 aggregate의 `sampleCount=null`을 1로 추정해 바꾸라는 뜻이 아니다. 1프레임 수치로 입술 움직임의 방향·지속시간·범위를 계산하지 않는다.

## 6. GPT 요청·출력·프롬프트

### API 형식

현재의 `response_format={type:json_object}`를, 지원되는 JSON Schema의 `json_schema` + `strict:true` 방식으로 전환하는 것을 권장한다. JSON mode는 JSON 형식에 초점을 두지만 Structured Outputs는 지정 schema를 따르도록 제한한다. 모든 object에서 `additionalProperties:false`, 모든 필드 required와 필요한 null 허용을 명시한다. 거부 응답과 출력 중단은 별도로 처리해야 한다. [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

공식 모델 문서는 `gpt-6-astra`의 Structured Outputs 지원을 명시한다. 다만 이 문서 작성은 현재 계정에서 새 schema 요청을 실제로 성공시킨 검증이 아니다. [GPT-6 Astra 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-astra)

`store=false`, 제한된 timeout·출력 예산·응답 크기·모델 identity 검사, 요청당 최대 1회 생성 원칙을 유지한다. `reasoning_effort=low`는 현 배포 baseline으로 유지하고 근거 개선과 동시에 조정하지 않는다. byte 기반 예산을 실제 token 수라고 표시하지 않는다.

### 생성용 내부 출력

첫 단계는 기존 6필드(`candidateId`, `guidanceId`, `explanation`, `action`, `practice`, `selfCheck`)에 strict schema를 적용할 수 있다. 다음 단계에서는 내부 생성 schema에 `usedVisualStatementIds`를 추가하는 안을 권장한다. 사용하지 않으면 `[]`를 반환한다.

참조 ID는 현재 후보의 allowlist에 속해야 하고, 후보 밖·과거 사례의 ID나 금지된 교정 statement를 참조하면 해당 생성 결과를 거절한다. 서버는 GPT가 실제로 어떤 근거를 내부 추론에 썼는지 증명할 수 없으므로, “입력으로 제공됨”, “출력에서 참조됨”, “표시됨”을 구분한다. 현재 구조만으로 영상이 문장 생성에 활용됐다고 단정하지 않는다.

`usedVisualStatementIds`는 기존 공개 item에 바로 추가하지 않는다. 내부 validation용으로 먼저 도입하고, 외부 reader 계약이 합의되기 전에는 기존 callback 형태로 투영한다.

### 프롬프트 변경 규칙 제안

다음은 활성 prompt 전문을 대체하는 파일이 아니라, 향후 prompt 개정에 포함할 프로젝트 규칙이다.

1. 마지막 `currentAttempt`의 허용 후보만 같은 순서·개수로 설명한다.
2. 음성 관측, 영상 관측, 일반 목표음 연습, 과거 전문가 의견을 구분한다.
3. 영상의 해석 권한은 서버 `interpretationMode`·allowlist를 따른다. ratio로 새로운 정상/비정상 기준을 만들지 않는다.
4. `OBSERVATION_ONLY`이면 개인 입 벌림·비대칭 교정이나 음성 오류의 영상 원인 설명을 하지 않는다. 일반 목표음 안내를 개인 진단으로 포장하지 않는다.
5. `UNAVAILABLE`이면 영상 사실을 언급하지 않고 음성 근거의 연습을 제공한다. 추가 확인 질문으로 서버 작업을 중단하지 않는다.
6. 영상 문구는 허용 statement만 참조하며, 임의 수치·시각·표본 수를 생성하지 않는다. 사실·숫자 문장은 서버 renderer가 출력한다.
7. 전문가 사례의 “입모양을 크게”, “천천히”, “어미 하강”은 해당 과거 녹음의 의견이다. 현재 근거가 없으면 현재 시도에 복사하지 않는다.
8. 기존 짧은 총평 정책을 유지한다. action은 대상 단어와 실행 방법 한 문장, UTF-16 140 이하이며 서버가 action만 묶어 총평 최대 5문장으로 만든다. 상세 provenance/한계는 별도 필드다.

GPT-6 공식 prompting guidance도 작업 경계와 참조 문서의 지시 영향을 점검하도록 안내한다. 여기서는 대본·과거 피드백을 비신뢰 자료로 취급하고 새 운영 지시로 승격하지 않는 방식으로 적용한다. [GPT-6 prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra#prompting-best-practices)

## 7. 출력 안전성은 prompt 한 줄로 해결하지 않는다

Strict schema는 근거의 진실성이나 교육적 적절성을 보장하지 않는다. 공식 문서도 구조화 출력의 내용 오류 가능성을 명시한다. [Structured Outputs의 한계](https://developers.openai.com/api/docs/guides/structured-outputs)

- 후보 ID·guidance ID·statement ID·coverage·순서는 결정적으로 검증한다.
- 숫자·시각·관찰 문장과 영상 교정 방향은 서버 원본 및 승인 template에서 렌더링한다. GPT가 바꾼 값을 채택하지 않는다.
- 일반 연습은 후보에 연결된 guidance에서만 고르게 하고, 영상 특화 action은 검증된 action catalog를 통과해야 한다.
- 모든 자유 텍스트 필드가 금지 진단을 우회할 수 있다. action뿐 아니라 explanation/practice/selfCheck도 검사 대상이다. 키워드 정규식은 보조 수단일 뿐 의미적 안전성을 증명하지 않는다.
- 모호한 문장은 같은 근거의 deterministic 연습으로 대체한다. 정책 안전성 확보 전에는 영상 관련 문장을 통제된 template으로 한정한다.

예를 들어 “‘ㅁ’을 낼 때 두 입술을 가볍게 맞댄 뒤 모음으로 연결해 보세요”는 일반 목표음 안내다. “영상에서 입술을 덜 닫아 발음이 틀렸으니 더 닫으세요”는 현재 허용되지 않는 개인 진단·인과 추정이다. 후자를 금지한다고 전자의 모든 입술 연습까지 막는 것은 아니다.

## 8. 전문가 few-shot과 장기 교정 근거

현재 두 사례 전체를 매 생성에 넣는 `ALL_CASES_EVERY_GENERATION` 정책은 유지한다. [기존 전문가 사례 문서](../expert_feedback_rag_20260928.md)의 source digest와 원문을 보존하며, 이 변경에 top-k 검색·요약·사례 삭제를 몰래 포함하지 않는다.

기존 case는 스타일·단어 연습 방식의 참고다. 현재 관찰의 reference 표본, 교정 정답, 독립 평가셋을 동시에 대신할 수 없다. 추가 사례에는 어떤 관찰이 어떤 전문가 문장을 뒷받침하는지, 허용 범위와 반례·보류 조건을 전문가가 검토한 매핑이 필요하다. 새 사례·projection은 별도 revision으로 검토하며 기존 전체 사례 정책을 바꾸려면 별도 결정이 필요하다.

개인 영상 교정을 도입하려면 적어도 다음 근거가 필요하다.

- 음소·철자 역할·앞뒤 문맥·촬영조건별 reference 적용 범위와 불가 범위.
- 화자와 원본 녹음이 겹치지 않는 개발·보정·평가 분리. 같은 녹음에서 뽑은 인접 프레임을 독립 사례처럼 세지 않음.
- 실제 시간 정렬 오차·측정 반복성·관찰 누락, 조명/자세/가림/장치별 실패 범위.
- 전문가가 검토한 교정 방향과 “교정하지 않아야 하는” 예시. 좌표 오차가 작다는 것만으로 action 정답을 입증하지 않음.
- 공개 전 결정한 오교정률·보류율·적용 coverage 등의 기준과 담당자 승인. 근거 없는 목표 숫자는 이 문서에서 만들지 않음.

현재 2개 사례, 공통 aperture reference, 최소 correlation -1.0 정책만으로 이 조건이 충족됐다고 판단하지 않는다. QA는 개발자가 직접 수행하며, 이번 문서 작업에서 자동 평가·영상 추론·GPT 생성은 실행하지 않았다.
