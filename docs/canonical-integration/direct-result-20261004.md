# v5 결과 전달의 사용자 대기 제거

구현·정적 확인 기록이며 운영 적용·시간 재측정 완료 기록이 아니다.

기존 분석 72의 HANDOFF 4.948초에는 원본 구성/전송/영속 수신이 포함되고 인계 완료→최초 public GET 4.538초에는 AWS 독립 검증/polling/조회가 포함됐다. 두 구간은 사용자 대기였다. 이를 단일 네트워크 비용으로 보거나 새 구현에서 9.486초가 모두 절감됐다고 단정하지 않는다.

## 변경 경로

RunPod PREFLIGHT → RESULT_PUBLISH(결과 JSON) → FE 직접 수신.
RunPod는 이후 HANDOFF(원본)/durable RECEIVED까지 계속 실행하며 AWS가 DB/B2 후처리를 수행한다.

- 새 `POST /api/internal/ai/analyses/{id}/handoffs/result`에 기존 result v5 bytes를 전송한다. 인증은 callback Bearer/worker header 그대로이며 최대 1 MiB다.
- 202 응답의 status=PUBLISHED/eventId/payloadSha256(RFC8785)을 확인한다. 이 응답은 영속 ACK가 아니며 슬롯/lease/재전송 책임을 해제하지 않는다.
- optional publication의 전체 교환 제한 2초. 구 BE/네트워크 실패이면 기존 원본 handoff를 계속한다. 첫 full handoff는 멱등 POST부터 보내고, 불확실한 재전송에서만 GET probe한다.
- AWS는 bounded memory(128건/32 MiB/1시간)에 결과를 등록하고 기다리는 FE에 알린다. EBS fsync, DB 쓰기, B2를 기다리지 않는다. 공개 조회의 현재 사용자/녹음/시도/worker/digest/revision 확인과 전송 schema 검사는 유지한다.
- delivery ON에서 AWS Python 의미 검증과 설치 readiness 의존을 제거한다. RunPod preflight가 의미 검증 책임을 가진다. 원본 hash, 현재 실행 fencing, 영속 저장 재시도는 후처리에 남긴다.
- FE는 `GET /api/v3/analyses/{id}?waitSeconds=8`의 결과를 직접 렌더링한다. 1초 상태 polling→별도 결과 GET을 제거한다. 구 BE의 빠른 pending 응답만 1초 간격으로 호환한다. SAVING은 기존대로 표시하고 완료 권한은 DB 저장 후 승인한다.

## 복구·배포

publication은 임시 공개다. AWS 재시작/포화 시 기존 원본 인계/영속 큐 경로로 복구하며 evidence 파일을 자동 삭제하지 않는다. 현재 단일 EC2/BE 전용이다. 취소/시도 변경/로그아웃은 현재 소유권 조회에서 차단하고 preview만으로 timeout sweeper를 제외하지 않는다.

BE develop 자동 배포 → RunPod 코드 배포 → FE dev/main/Vercel 반영 순서를 권장한다. 실제 배포 묶음의 FE asset/AI revision pin도 갱신해야 한다. 코드 rollback은 기존 full handoff로 호환되며, pending 큐가 있으면 delivery를 끄거나 디렉터리를 교체하면 안 된다.

확인 범위: Python 두 변경 파일 AST, BE compileJava/compileTestJava, FE tsc/prettier, diff 정적 확인. 자동 회귀/브라우저/실제 추론 QA는 수행하지 않았다. 다음 운영 측정은 RESULT_PUBLISH, canonical_result_publish.published, 최초 공개 응답, HANDOFF 완료, DB 저장, B2 완료를 각각 구분한다.
