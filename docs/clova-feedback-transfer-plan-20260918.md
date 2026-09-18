# Seungun → CLOVA 피드백 전달 분석 및 개선 계획

2026-09-18. 현재 배포 기준 `fbca0c59d174a36fee6becc44185a8e229d31858`의 소스와 `testvideo1.mp4`의 기존 실제 호출 자료를 확인했다. 이 문서는 제한 해제 변경과 후속 정보 전달 개선 계획을 구분한다. 새 모델 학습·임계값 변경은 범위 밖이다.

## 결론

정보 축소는 확인됐다. 다만 원본이 모든 개인별 조음 진단에 충분하다는 결론은 아직 근거가 없다. 원본의 CTC 후보·확률·정렬과 실제 오류/조음 원인은 다르다. 데이터에 있는 관찰을 전달하는 개선과, 없는 근거를 새로 측정하는 개선을 분리한다.

확인 자료는 배포 전 실제 CLOVA 호출 테스트의 `analysis-evidence.json`, `feedback-request.json`이다. 운영 분석 15의 원본 LLM 요청 캡처와 동일한 자료라고 취급하지 않는다. 원본 녹음·식별자·인증정보는 이 문서에 복사하지 않는다.

## 실제 처리 경로와 손실

AI 저장소 상대 경로 기준:

1. `seungun/src/korean_phone_ctc/production_pipeline.py`: production 결과에 `evidence_bundle`과 선택 정책 `decision`을 함께 생성한다.
2. `src/voice_coach/backend_analysis/adapters/seungun.py::_analysis_snapshot`: JSON snapshot으로 production 결과와 전체 evidence를 유지한다. 이 단계에서 전체 음소 배열이 사라지는 것은 아니다.
3. `src/voice_coach/backend_analysis/service.py::process_admitted`: Seungun → 영상 보조 → 피드백 생성 → 별도 점수 생성 순서다. 채점용 입력은 피드백 입력과 다르고 채점 결과도 피드백보다 나중에 생성된다.
4. `src/voice_coach/backend_analysis/grounded_feedback.py::BackendGroundedFeedback.render`: 전체 `analysis_evidence`를 bridge에 전달한다. 현재 `COACHING_READY`만 피드백 생성 대상이다.
5. `src/voice_coach/pronunciation_feedback/seungun_bridge.py::_selected_phone`: 임계값 통과 오류 후보 중 검출 점수가 가장 높은 한 음소만 선택한다.
6. 같은 파일의 `build_grounded_payload_from_seungun`: 선택 음소의 일부 필드와 원문 위치, 고정 연습 문구를 만든다.
7. `src/voice_coach/pronunciation_feedback/video_grounded_advice.py`: 검증된 시각 근거 일부를 자연어 claim으로 합친다.
8. `src/voice_coach/pronunciation_feedback/adapters/advice_generator_hyperclova.py::_complete`: 축약된 payload만 CLOVA 메시지에 넣는다.
9. `grounded_advice.py` 및 `video_grounded_advice.py`: 응답 검증 후 문구를 반환하며 실패하면 기존 claim 문구로 폴백한다.

| 원본 데이터 | 기존 피드백 입력 | 판단/계획 |
|---|---|---|
| 전체 `expected_phones` 30개 | 선택 음소 1개 | 전체 기대열과 단어·음절 소유 관계 전달 |
| 전체 `phones` 30개 | 선택 음소 일부 필드 | 모든 음소를 잃지 않는 근거 묶음 생성; LLM 입력은 명시적 크기 예산 안에서 선택 |
| `ctc_hypothesis` 28개 | 없음 | 정답 전사가 아닌 모델 후보열로 표시 |
| 음소별 `alignment_operation` | 선택 음소의 자연어 한계 문구로 변환 | correct/substitution/deletion을 구조화해 유지 |
| 음소별 `observed_candidate` | 선택 음소만; 기존 출력은 후보 공개 금지 | 후보와 실제 발음 확정을 구분하여 전달·설명 |
| `start_ms/end_ms` | 선택 음소만 | 다른 음소도 유지; null·관측·보간 시각을 구별 |
| `error_score/operating_threshold/score_semantics` | 선택 음소만, 소수 6자리 | 원래 정밀도를 보존; 정답 확률로 설명 금지 |
| `soft_features` 9개 | 전부 없음 | 특징값과 의미·한계를 구조화하여 보존 |
| `insertions` | 없음 | 스키마 지원 필요. 이번 샘플 배열은 비어 있어 실제 삽입 사건이 누락됐다는 증거는 아님 |
| `notices` 3개 | 일부 문구로만 반영 | 불확실성·개별 판정 검증 한계 유지 |
| 모델/설정 digest | 일반 피드백에서 제외 | LLM 교육 정보로 불필요; 서버 내부 출처 추적에 보존 |
| 영상 cue·상태·단위·시간·출처 | 허용된 claim 일부 | 측정값을 자연어만이 아닌 구조화 evidence로 유지 |
| scoringEvidence/항목별 점수 | 피드백 입력에 없음 | 점수 설명 필요 시 채점 후 피드백으로 순서 변경 설계 |

`soft_features`: blank_prob, competitor_prob, entropy, expected_margin, expected_prob, hard_error, has_predicted_span, predicted_prob, span_seconds. 이 값들은 오류 원인이나 혀 위치의 직접 측정값이 아니다. 특히 CTC span을 실제 발음 지속시간으로 치환하지 않는다.

샘플의 선택 ㅇ은 deletion이며 후보·시간이 null이다. 인접 ㅋ에는 ㄱ substitution 후보가 있지만 원본은 이를 실제 대치의 정답이라고 보장하지 않는다. 기대열의 연속 ㅇ과 초성/종성 표기 처리도 모델 학습 토큰 규칙과 대조할 필요가 있다. 현재 관측만으로 해당 ㅇ의 생략이나 토큰화 버그를 확정하지 않는다.

## 이번 제한 해제 변경

입력 v5에서는 생성 내용 제한용 `constraints`를 비우고 `requiredFragments`를 빈 배열로 전송한다. 기존 v3/v4 입력은 읽어서 현재 표현 정책으로 변환한다. 출력 JSON 버전과 AWS callback 계약은 유지한다.

- 조음 지식·대조 예시·연습 방법 추가 금지, 한 항목 설명 제한, 후보 설명 금지 해제.
- 모든 claim 선택, 필수 구절·위치 문구 그대로 복사, 재녹음 및 영상 실패 문구 강제 해제.
- 4~8문장/고정 순서/특정 용어 사용 금지 해제.
- 기존 40~1,000자 표현 정책은 비어 있지 않은 문자열과 백엔드 계약 상한 20,000자로 변경.
- JSON 형식, 요청 연결 ID, 유효한 source claim ID, 개인정보 보호, 없는 관측을 만들어내지 않는 원칙은 유지.
- 모델 실행의 4,096 입력 토큰·512 출력 토큰·150초 생성 한도는 별도 운영 자원 제한으로 유지. 장문 생성의 실질 상한이 남아 있음을 명시한다.
- 전체 음소 전달, 다중 오류 선택, `COACHING_READY` 외 생성은 이번 구현에 포함하지 않는다. 생성 문구의 개수 제한 해제가 실제 입력 음소를 늘리는 것은 아니다.
- 점수 계산, detector threshold, 모델 가중치, AWS 스키마는 변경하지 않는다.

생성 정책 revision: `evidence-explanation-v2-20260918`. 근거에 없는 개인 진단을 허용한다는 의미가 아니다. 일반적인 발음 방법과 사용자에 대한 측정·추정을 구별하도록 프롬프트에 명시한다. 새 표현을 허용해도 입력에 없는 실제 오류 원인이 생기는 것은 아니다.

## 후속 개선 순서

### 1. 원본 근거 보존 계약

전달용 `feedback_evidence.v1`을 별도 설계한다(아직 구현 아님). target text, 기대열, 후보열, 전체 음소 행, 삽입, notices, 영상 evidence를 포함한다. 음소별 원본 index, 원문 글자/단어/음절 위치, onset/nucleus/coda 구분, 값·단위·출처·시각 provenance를 둔다. 원본 digest와 요청 연결 정보는 서버 내부에 두고 LLM에 파일 경로·사용자 ID·비밀값을 보내지 않는다.

원본 필드별 `전달/내부 보관/해당 없음` 표를 버전으로 관리한다. 원본에 없는 SNR·혀 위치·실제 기류·검증되지 않은 운율 수치는 null/미제공으로 표시한다.

### 2. 근거 선택과 입력 크기

원본 전체 보존과 LLM 메시지 크기를 분리한다. 현재 4,096 토큰에 긴 발화의 모든 특징을 무조건 넣지 않는다. 전체 요약 + 우선 오류 후보 + 주변 음소를 구성하고 `totalPhoneCount`, `includedIndices`, `omittedIndices`, 선택 이유를 기록한다. 조용한 절단은 금지한다. 모든 음소 설명이 필요하면 단어별 분할 생성·최종 통합과 지연 예산을 별도 설계한다.

현재 Seungun `coach_one` 및 백엔드 단일 `pronunciationEvidence`는 그대로인데 CLOVA만 다중 확정 오류를 만들게 하지 않는다. 초기에는 하나의 공식 선택 근거 + 다른 모델 후보를 구분하고, 다중 진단은 upstream policy/계약 변경으로 별도 검토한다.

### 3. 영상 전달

영상의 없음/동의·정책 제외/추출 실패/정렬 실패/품질 부족/관찰 성공을 구별한다. 관찰 성공 시 cue별 값·단위·음성 anchor·실제 표본 시각·관찰 프레임 수·범위·누락 이유를 전달한다. 현재 단일 프레임 또는 평균값만 있는 경우 개폐 속도·동작 순서를 만들어내지 않는다. 원본에 없는 메타데이터는 extractor에서 추가할 대상이지 bridge에서 추측할 대상이 아니다.

### 4. 설명과 교정 연결

관측 결과, 모델 후보, 일반 조음 안내를 서로 다른 섹션으로 입력한다. 발음 위치 → 관측/추정 → 적용 문맥에 맞는 방법 → 짧은 대조 연습 → 사용자 확인 기준으로 설명하도록 한다. 받침과 초성에 동일한 지시를 쓰지 않는다. 알려진 위치·교육 지식은 구체적으로 설명하되 실제 혀/기류 상태를 측정했다고 표현하지 않는다.

### 5. 점수와 피드백 일치

항목별 점수 해설이 필요하면 `분석 → 기존 채점 → 피드백` 순서로 변경한다. CLOVA가 숫자를 다시 계산하지 않고 검증된 scoringEvidence와 NOT_APPLICABLE/UNAVAILABLE 상태를 설명하게 한다. 채점 실패·피드백 실패의 결과 정책과 전체 deadline을 함께 검토한다.

### 6. 검증과 배포 기준

개발자 QA에서 원본→전달 필드 대응, 음소 수·index·후보·시각·정밀도 보존, 반복 음소 위치, 초성/종성 ㅇ, 음운변동, 삭제/삽입, 영상 미제공을 확인한다. 개인정보가 LLM 메시지에 포함되지 않는지도 확인한다.

기존 testvideo1을 동일 근거로 비교해 재녹음 문구 반복, 허위 생략·혀 위치 진단, 연습의 구체성, fallback 여부, 입력/출력 토큰, 총 지연을 별도로 기록한다. health 200은 피드백 품질 검증이 아니다. 요청 ID와 내용 원문 대신 내부 상관키·단계·policy revision·포함/제외 필드 수·fallback 이유를 기록하는 로그를 먼저 마련한다.

전체 전달 확장과 생성 길이 확대는 새 flag로 점진 적용하고 이전 표현 정책으로 원복할 경로를 둔다. 이번 문서는 계획이며 이 절의 전달 확장은 배포하지 않았다.

## 운영 적용 및 검증

아래는 작업 완료 후 실제 적용 결과로 갱신한다. 코드 AST 검사와 정적 diff를 검토했으며 자동 회귀 QA·새 음성 분석은 아직 실행하지 않았다.
