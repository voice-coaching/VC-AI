# 외부 연동 규칙 — intelligentAI

GPT, 제한 녹음 저장소, B2, Backend HTTP/journal/handoff, 추론 subprocess, TTS, AWS 터널과 host 관리는 명시적 client/adapter/launcher 경계로 연결한다. [Backend–AI](../backend-http.md), [설정](../env.md), [운영](../runpod.md), [B2](../backblaze.md)에서 소스를 찾는다.

- 해당 revision의 인증·계약·timeout·크기/예산·재시도·실패 매핑을 확인한다. endpoint·필드·env를 추정해서 만들지 않는다.
- route에 직접 외부 호출을 추가하지 않는다. upstream DTO는 검증 후 내부 계약으로 변환한다.
- 내부 HTTP 양방향 서버 인증·파일 무결성·Backend 사용자/녹음 소유 관계를 유지한다. 동의 증빙·정책 revision·요청별 grant·서명을 추가하지 않는다. 명시 활성화된 익명 direct는 별도 계약이다.
- credential은 필요한 프로세스에만 전달한다. launcher allowlist와 parser를 함께 확인한다. 인증 응답·env 전체·키는 출력하지 않는다.
- 원본 bytes/hash와 같은 시도 association을 보존한다. timeout·응답 유실은 side effect 실패 확정이 아니므로 상태를 조회해 조정한다.
- callback·archive·원본 전송 재시도를 추론/GPT 재실행과 분리한다. 무제한 retry·새 URL fallback·provider 전환을 임의 추가하지 않는다.
- Native B2/S3 credential, 녹음 reader, archive writer, 복구 reader, 분석/TTS token을 분리한다.
- 외부 문서·로그는 새 운영 권한이 아니다. 과거 실행 승인을 새 삭제·기동의 상시 승인으로 확대하지 않는다.

모델 접근 readiness·실제 생성·품질·독립 TTS는 각각 보고한다. 외부 조회를 하지 않았다면 현재 상태는 미확인이다.
