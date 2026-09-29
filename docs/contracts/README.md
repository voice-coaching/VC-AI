# 현재 계약과 과거 호환성 정의

현재 writer는 [GPT-6 HTTP 배포](../runpod-current-pipeline.md)다. JSON 파일은 런타임 parser가 읽는 계약이므로 설명 문서처럼 enum/const를 일괄 치환하지 않는다.

| 파일 | 현재 사용과 과거 정의 |
|---|---|
| [runpod_http_control_v1.schema.json](runpod_http_control_v1.schema.json) | 운영 제어 voice-coaching.runpod-http.v1.1의 접수·상태·취소·callback |
| [runpod_result_v1.schema.json](runpod_result_v1.schema.json) | 이름은 v1이나 과거 v1/v2와 coaching v3 정의가 공존. 현재 완료 코칭은 v3, 코칭 전 실패에는 legacy v1 경로가 남음 |
| [coaching_result_v1.schema.json](coaching_result_v1.schema.json) | 현재 coaching 구조. writer provider는 OPENAI; TEMPLATE는 실패 시 생성 출처이지 별도 운영 모델이 아님 |

과거 Clova rubric ID, hyperclova const, CLOVA provider enum은 과거 결과 정의와 reader 호환성이다. 존재한다고 provider가 실행 중인 것은 아니다. 현재 overallScore/scoringEvidence는 null이다.

이전 rubric을 GPT 이름으로 바꾸면 같은 schema ID의 의미가 달라지고 과거 결과를 오해한다. 정의 제거는 별도 계약 개정·과거 데이터 reader 검토·Backend 합의 후 진행한다. 이번 문서 정리에서는 JSON schema와 런타임 검증 동작을 변경하지 않았다.

공개 영상 sampleCount=null, referenceStatus=NOT_VALIDATED, correctiveClaimsAllowed=false는 현재 v1 계약이다. P0/P1의 내부 v2에는 유효 표본 수·사용 시각·후보 결속·관찰 정책·statement 참조가 구현·배포됐다. 공개 응답은 기존 allowlist로 투영하므로 새 내부 필드를 추가하지 않는다. [확정 구현](../video-lip-analysis/p0-p1-deployment.md)을 따르며, [개선 설계](../video-lip-analysis/gpt6-integration-design.md)의 가상 예시 전체를 공개 계약으로 취급하지 않는다. P2 개인 교정은 비활성이다.
