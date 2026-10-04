# v5 단일 배포 계약

2026-10-04 사용자 결정: v4는 과거 계약 문서로만 참고한다. 신규 접수·재시도·RunPod 실행·결과 조회·배포 readiness는 v5로 통일한다. 이전 v4 DB 원본과 immutable migration 이력은 삭제하거나 v5로 변환하지 않는다.

## 활성 경로

FE는 `X-Analysis-Result-Schema: voice-coaching.runpod-analysis-result.v5`로 제출하고 `/api/v3/analyses/{id}`에서 `voice-coaching.canonical-analysis-view.v2`를 읽는다. capability/404에 따른 v4 fallback은 없다. Backend는 헤더를 생략해도 v5를 선택하고 다른 결과 버전은 400으로 거절한다. RunPod는 request v3/profile `CANONICAL_HANDOFF_20261004_V5`만 접수한다.

원본의 DB 인계 → 동일 근거의 의미 검증 → 결과 commit → B2 archive 순서다. B2 readback과 영수증은 사용자 결과 완료 조건에 포함되지 않는다. H5 프롬프트·서버 채점표·근거 무결성은 유지한다. 인계 상세는 [v5 계약](handoff-v5-20261004.md)을 따른다.

v4 공개 `/api/v2/analyses/{id}` 및 `/worker-readiness/v2`는 등록하지 않는다. 기존 result callback/ack는 인증 후 410 `HANDOFF_REQUIRED`로 종료한다. 옛 journal의 prepare/upload/manifest/ack 라우트와 receipt/callback verifier 소비자는 중단한다. v5에서도 쓰는 reserve/status/start와 claim/heartbeat는 유지한다. 과거 v4 이력의 상세 결과는 새 FE에서 표시하지 않으며 새 시도로 분석해야 한다.

## 배포

v5 verifier → V38/V39와 Backend JAR → Pod overlay 및 gateway `/health/handoff` → FE 운영 빌드 확인 → 신규 접수 개방 순서다. 진행 중 v4 요청을 먼저 drain하고 DB 비공개 백업을 확보한다. 신규 작업에서 v4 rollback/fallback은 허용하지 않는다. 장애 시 신규 접수를 닫고 이미 인계된 원본·archive 작업은 보존한다.

Backend `analysis.canonical.handoff.*`의 worker/archive/admission, `INDEFINITE` 보존, DB spool 예산과 전용 B2 writer를 설정한다. Pod에는 B2 writer를 주입하지 않는다. v5 전용 health는 공유 제어·근거 schema 6개와 request/result/handoff schema 3개의 digest를 비교한다. 옛 request v2/result v4는 readiness에 포함하지 않는다.

이 문서는 구현 계약이다. 운영 revision·migration·readiness 확인 결과는 배포 후 아래에 별도로 기록한다. 자동 회귀·브라우저 QA·추론 품질 검증은 실행하지 않는다.

Backend archive writer는 `deploy/runpod/provision_backend_handoff_writer.py`로 `canonical-evidence/v5/`에 한정하여 발급한다. 기존 Pod writer·Backend reader와 분리하고 삭제 권한은 부여하지 않는다. 발급 결과는 AWS root 전용 공급 파일에 저장하며 RunPod에는 전달하지 않는다. 키 유효기간은 90일이며 만료 전 교체가 필요하다.

`/health/services`의 `executorReady`는 v5 실행 lane의 실제 준비 상태를 표시하고 `handoffReady`는 Backend 연동까지 포함한다. 폐기된 legacy executor의 상태를 배포 완료 조건으로 사용하지 않는다.
