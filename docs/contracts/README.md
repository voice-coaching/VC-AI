# 현재 분석 계약

신규 내부 분석은 request v4 → result v6 → handoff v2다.
[통합 명세와 Backend 적용 조건](../canonical-integration/v6-unification.md),
[API 명세](../api/specification.md)를 따른다. 파일명 버전은 wire 메시지 버전과
각각 독립적이다. 이 소스 변경을 운영 배포·준비 완료로 해석하지 않는다.

| 파일 | 현재 역할 |
|---|---|
| [direct practice result](direct_practice_result_v1.schema.json) | 공개 direct의 전체 녹음 평가 범위·기존 점수/총평 projection |
| [request v4](runpod_analysis_request_v4.schema.json) | 음성 전용 또는 실제 음성·영상 신규 접수 |
| [result v6](runpod_result_v6.schema.json) | 통합 결과·association v2와 내부 audio 검증 구조 |
| [handoff v2](runpod_canonical_handoff_v2.schema.json) | 같은 원본·projection·실행 identity의 영속 인계 |
| [media preparation](runpod_media_preparation_v1.schema.json) | 실제 동일 원본 normalization receipt |
| [visual coaching input](runpod_visual_coaching_input_v1.schema.json) | 검증된 영상 관찰의 GPT 입력 경계 |
| [HTTP control](runpod_http_control_v1.schema.json) | 상태·취소·claim/heartbeat·서버 제어 메시지 |
| [HTTP control 확장](runpod_http_control_v1_2.schema.json) | 원본 manifest 등 내부 제어 구조 |
| [canonical parent](runpod_canonical_parent.schema.json) | frozen core 원본의 구조 검증 |
| [canonical input](runpod_canonical_input.schema.json) | frozen selection/projection 원본 구조 |
| [canonical output](runpod_canonical_output.schema.json) | frozen GPT expression 원본 구조 |
| [canonical journal](runpod_canonical_journal_v1.schema.json) | one-shot 실행 journal 경계 |

result v6의 audioResult/audioAssociation/audioVisual은 내부 음성 의미 검증용이다.
별도 wire 버전이나 신규 접수 경로가 아니다. 음성 전용은 NOT_PROVIDED와 null 영상
근거를 사용하며 가짜 영상·receipt·0점으로 채우지 않는다.

현재 codec·frozen 원본 검증에서 사용하는 다른 schema는 해당 구조의 검증용이며
과거 HTTP 요청의 신규 접수를 허용하지 않는다. JSON 구조 검사만으로 provenance,
소유권·lease·원본 SHA·prepared 시간축·GPT 근거의 검증을 대체하지 않는다.
공개 direct UUID 계약도 내부 Backend 정수 identity로 변환하지 않는다.

삭제한 request v3/result v5/handoff v1 계약과 이전 전환 문서는 아카이브하지 않는다.
기존 사용자 원본·미완료 journal을 삭제하거나 새 버전으로 재작성하지 않는다.
