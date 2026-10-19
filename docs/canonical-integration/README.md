# Canonical 분석 통합

현재 소스의 신규 내부 분석 계약은 **request v4 → result v6 → handoff v2**다.
음성 전용도 같은 결과 버전을 사용하고 영상 부재를 명시한다.
정본은 [v6 통합 명세](v6-unification.md), [schema 목록](../contracts/README.md),
[API 상세](../api/specification.md)와 실제 parser·serializer다.

| 경계 | 소스 |
|---|---|
| 신규 요청·미디어 구분 | [http_models.py](../../src/voice_coach/backend_analysis/http_models.py) |
| 공통·영상 readiness | [http_runtime.py](../../src/voice_coach/backend_analysis/http_runtime.py) |
| 원본 음성 의미 검증 | [canonical_result.py](../../src/voice_coach/backend_analysis/canonical_result.py) |
| 통합 v6 결과·영상 결속 | [canonical_audiovisual_result.py](../../src/voice_coach/backend_analysis/canonical_audiovisual_result.py) |
| one-shot 실행·인계 | [canonical_executor.py](../../src/voice_coach/backend_analysis/canonical_executor.py) |
| 동일 bytes 인계·복구 | [canonical_handoff.py](../../src/voice_coach/backend_analysis/canonical_handoff.py), [canonical_recovery.py](../../src/voice_coach/backend_analysis/canonical_recovery.py) |
| Backend 독립 검증 | [canonical_verify.py](../../deploy/runpod/canonical_verify.py) |

frozen core/H5·원본 관측·prepared PCM/MFA 출처와 서버 소유 관계를 보존한다.
신규 계약 통합은 모델·점수 정책·실행 identity 승인을 변경하지 않는다.
공개 direct, 내부 인계, archive, 사용자 이력 저장 상태를 혼동하지 않는다.

운영 대상과 시각이 있는 실행 관측은 해당 장애·이전 기록을 따른다. 소스 구현과
설치 readiness, 실제 생성·품질, Backend/FE 반영은 별도다. Backend는 별도 저장소이며
이번 변경만으로 양쪽 연동 완료를 보고하지 않는다. 전환 시 admission/drain과 남은
인계 조정 절차는 [v6 통합 명세](v6-unification.md)의 운영 반영 절을 따른다.
