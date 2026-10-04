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

## 2026-10-04 운영 전환 기록

운영 선행조건은 문서상의 준비 완료 여부와 분리해 직접 확인했다. 기존 Backend PR #102·#103 병합 후 자동 배포는 `/tmp` 업로드 공간 부족으로 실패했고, 당시 운영 JAR은 `6393d76d5ccc4896edd168d743d865577374843e`, DB는 V37이었다. 이전 업로드 사본을 해시 검증 후 비공개 보관 위치로 이동하고, 새 배포는 `/var/tmp` 업로드·실행별 정리·Python bytecode 생성 억제를 적용했다.

### 백업·마이그레이션·복구 범위

- PostgreSQL 18 서버에 맞는 `postgres:18` 도구로 `/var/lib/alpha-deploy/backups/v5-20261004/before-v38-v39.dump`를 생성했다. 2,386,249 bytes, SHA-256 `fe6d6becddd3ada86ce4dd011ce3d3e5f0dcbce393895707ddfd1738f6d74a02`, root 전용 0600이다. 같은 비공개 디렉터리에 이전 설정·배포 manifest를 보존했다.
- 격리한 PostgreSQL 18에 백업을 복원하고 V38·V39를 적용해 기존 행과 v4 결과 9건의 보존을 확인했다. 임시 검증 컨테이너는 제거했다. 이후 운영 Flyway에서도 V38·V39의 성공을 확인했다.
- 이전 JAR과 DB의 기존 컬럼·원본은 보존했다. 이전 JAR을 새 DB에 재실행하는 복구 QA는 수행하지 않았다. v5 인계 후에는 v5 원본·worker·읽기 계약을 보존해야 하며, v4 JAR만 교체하거나 DB를 무조건 되돌리는 복구는 허용하지 않는다.
- DB spool 한도는 512 MiB, 보존은 `INDEFINITE`로 설정했다. 이 논리적 한도는 원격 DB 호스트 파일시스템의 잔여 용량 확인을 대신하지 않는다.

### 배포한 구성

| 대상 | 고정 revision / 산출물 |
|---|---|
| Backend | [PR #104](https://github.com/voice-coaching/VC-BE/pull/104), develop 병합 `0d3079d81d008592964c7df04f947a435f54c70c` |
| RunPod | 소스 `8464746e0d7c9d5534f1e59793c9c707794c1513`, bundle `693d12356899a6f089b4f57c8f33360a1daaed980f136f1f92ac2388fc96ec4f` |
| AWS verifier | bundle `62380c8e908222272c26719a5676e9863d4cd7ef40d6410191474299f8466250` |
| FE | [PR #47](https://github.com/voice-coaching/VC-FE/pull/47) dev 통합 → [PR #48](https://github.com/voice-coaching/VC-FE/pull/48) main 반영, 운영 `b020df11685a4d49fd95eeb3b45ddb8d5275fbd0` |

공유·v5 스키마 9개의 SHA를 Backend JAR, verifier, RunPod에서 맞췄다. Windows 줄바꿈 변환으로 계약 digest가 달라지지 않도록 신규 v5 스키마는 Git 원본 바이트를 고정했다. H5와 Seungun 전체 음소 기반 서버 채점표는 유지하며 resident 추론은 활성화하지 않았다.

AWS의 전용 B2 writer는 `speakai2026/canonical-evidence/v5/` 범위의 읽기·쓰기 권한을 갖고 삭제 권한은 없다. 실제 실행 프로세스의 키로 B2 인증과 범위·권한을 확인했다. 유효한 opaque application key의 구두점을 기존 검증식이 거절하던 문제를 수정했다. 키는 AWS root 전용 파일과 프로세스에만 공급하며 RunPod에는 전달하지 않는다. 90일 만료 전에 교체해야 한다.

FE는 Vercel 성공 상태와 실제 `vc-fe.vercel.app`의 페이지·참조 JavaScript 18개 해시를 확인하고 AWS 배포 profile에 고정했다. 자산에 v5 요청·v3 결과 경로·view v2가 포함된다. Vercel 팀 빌드 로그 접근 권한은 확보하지 못했다.

### 검증 범위

Backend CI, Java 빌드, FE TypeScript 컴파일과 운영 읽기 전용 검증을 수행했다. 신규 접수를 차단하고 진행 중 v4 작업이 없음을 확인한 뒤 구성 전환·readiness 확인을 거쳐 접수를 열었다. 자동 배포도 같은 차단·drain·검증·개방 절차를 사용한다.

2026-10-04 15:05 KST 최종 관측:

- [Backend 자동 배포 #37181591572](https://github.com/voice-coaching/VC-BE/actions/runs/37181591572)는 성공했다. 배포 상태 `COMPLETE`, 실제 JAR SHA-256 `5defc0859a61cf6e92d71d372dc4de312bed7874bb837d73587c927bb68077c2`가 release manifest와 일치한다. 업로드 JAR·스크립트 사본 정리도 성공했다.
- 외부 maintenance 차단 파일은 없고 Backend·RunPod v5 admission/readiness, verifier installation·고정 digest 검증이 통과했다. 스키마 9개와 FE 자산 18개가 운영 profile과 일치한다.
- drain 항목(분석·dispatch·취소·receipt·callback·apply·effect·upload·journal·lease·transaction·ack·Pod 작업)은 모두 0이다. handoff 및 archive job도 아직 0건이다.
- 운영 DB의 V38·V39 `success=true`, AWS 애플리케이션 호스트 루트 잔여 공간은 약 3.10 GiB다. DB 호스트 디스크 잔여 공간은 별도 미확인이다.

실제 녹음 제출, 브라우저 QA, 추론 품질 평가와 실제 분석 자료의 B2 archive 완료 검증은 수행하지 않았다. 따라서 readiness 성공을 종단간 음성 분석 또는 archive 품질 QA 통과로 해석하지 않는다.
