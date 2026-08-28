# 엔드포인트 목록 - intelligentAI

이 문서는 목록 전용이다. Request/Response 상세를 작성하지 않는다.

| Method | URL | Auth | 설명 |
| --- | --- | --- | --- |
| GET | /health | public | 상태 확인 |
| POST | /v1/oracle-attempts | research-only | 정답 라벨로 진단·코칭 계약을 검증 |
| GET | / | loopback | T43/I30 전문가 검수 GUI shell |
| GET | /assets/styles.css | loopback | 검수 GUI stylesheet |
| GET | /assets/app.js | loopback | 검수 GUI application |
| GET | /api/v1/review-session | `X-Review-Token` | 현재 profile과 완료 진행률 |
| GET | /api/v1/review-items | `X-Review-Token` | hash 기반 검수 항목 목록 |
| GET | /api/v1/review-items/{item_hash} | `X-Review-Token` | 선택 항목의 interval·기존 판정 |
| GET | /api/v1/review-items/{item_hash}/audio | `X-Review-Token` | 제한 WAV의 loopback stream |
| POST | /api/v1/review-items/{item_hash}/review | `X-Review-Token` | 최초 판정 원자 저장 |
| PUT | /api/v1/review-items/{item_hash}/review | `X-Review-Token` | 기존 판정 원자 교체 |
