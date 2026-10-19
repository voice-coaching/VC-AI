# Canonical AI 출력 계약 v1 (LLM 연결 전)

이 문서는 릴리스 코드 source/scripts/run_canonical_batch.py가 실제 생성한 canonical_ai_predeploy_v1 envelope와 릴리스 자체의 ACCEPT·REJECT 결과 5건에서 도출했습니다. 기계 판독 규약은 canonical_ai_output.schema.json입니다. 운영 callback 또는 Astra 입출력 계약이 아닙니다. LLM_CONTRACT_STATUS = PENDING_SOURCE_AUDIT.

최상위 필드는 schema_version, request_id, input_validation, content_gate, feedback_delivery_allowed, core_result, pronunciation_feedback_source, pronunciation_evidence, llm_facing_evidence, native, mfa_receipt, timing_marks_monotonic, prepared_wav_sha256, source_audio_sha256, expected_phone_count, stage_durations_s입니다. 최종 사용자 발음 피드백의 유일한 근거는 REFINED_P1_ONLY입니다. core_result.pronunciation에 남아 있는 역사적 진단을 추가 사용자 경고로 합치지 않습니다.

## 상태

실제 입력 판정은 input_validation.status입니다. 값은 ACCEPT, REJECT, INCONCLUSIVE, SYSTEM_FAILURE입니다.

- ACCEPT: 지원 범위에서 분석 가능하며 발음·운율 결과를 사용할 수 있습니다.
- REJECT: 입력 자체에 충분한 결정적 무효 증거가 있습니다. 발음·운율 코칭 대상으로 쓰지 않습니다.
- INCONCLUSIVE: 무효라고 확정할 근거가 부족하거나 원인이 미정입니다. 사용자 잘못이라고 단정하지 않습니다. feedback_delivery_allowed와 실제 결과를 함께 확인합니다.
- SYSTEM_FAILURE: 도구·모델·런타임의 시스템 실패입니다. 사용자 발음/원고 오류로 표현하지 않습니다.

feedback_delivery_allowed는 코드가 계산한 실제 전달 가능성입니다. core_result.status는 내부 분석 상태로 입력 판정의 대체물이 아닙니다. 실제 reason enum은 AUDIO_DECODE_OR_CORRUPTION, SILENCE_OR_NO_SPEECH, ALIGNMENT_FAILURE, SYSTEM_ANALYSIS_FAILURE, INSUFFICIENT_CONTENT, TRANSCRIPT_MISMATCH이며 nullable입니다. 실제 stage enum은 input, audio, g2p, alignment, textgrid, acoustic, feedback, storage, dependency, internal입니다. input_validation에는 schema_version, gate_version, status, reason_code, stage, evidence, source_provenance, original_error, subject, decision_basis가 있습니다. 원래 오류는 original_error에 보존합니다. 별도 retryable 필드는 현재 envelope에 없습니다.

## 발음·운율 증거

pronunciation_evidence는 평가한 자음 위치별 내부 증거입니다. 예상 음소·역할·단어 인덱스·MFA 위치·native 관측·CTC score/margin·localization·policy decision·presentation state를 포함합니다. MFA는 위치 증거이지 실제 발음 정답이 아닙니다. CTC logit과 margin은 보정 확률이나 발음 정확도 퍼센트가 아닙니다. policy_warning도 인간이 오류를 확정했다는 뜻이 아닙니다.

presentation_state는 CLEAR_CONTRAST, AMBIGUOUS_CONTRAST, INSUFFICIENT_EVIDENCE, NO_FEEDBACK입니다. CLEAR_CONTRAST는 동결 P1이 선택한 모델 관측 표현입니다. AMBIGUOUS_CONTRAST는 내부 보존 상태로 추가 사용자 안내는 검증되지 않아 OFF입니다. 현재 llm_facing_evidence.pronunciation_items에는 llm_visible=true인 CLEAR_CONTRAST만 들어갑니다. INSUFFICIENT_EVIDENCE와 NO_FEEDBACK도 제외됩니다. 피드백 없음은 완벽한 정상 발음의 증명이 아닙니다.

llm_facing_evidence는 현재 deterministic projection의 이름일 뿐 Astra prompt/API 계약이 아닙니다. prosody는 동결 branch의 결과입니다. REJECT 검증에서는 pronunciation_items=[], prosody=null, mfa_receipt=null이었습니다. native.device=cpu, native.dtype=float32입니다.

## 입력 범위

runner 입력은 JSON 배열의 {id, audio, script} 사례입니다. script는 읽어야 할 원래 원고입니다. 결과는 원본 audio와 canonical WAV의 SHA를 별도로 기록합니다. 제품 endpoint, callback shape, UI 문구, Astra 표현 규칙은 포함하지 않습니다.
