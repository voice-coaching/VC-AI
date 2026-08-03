# 문서 구조·통합 안내

현재 `docs/`에는 52개의 Markdown 정본만 남긴다. AI Hub 접근·라이선스·데이터
경계, 현재 실행 상태, 활성 coaching/WavLM 계약, API·architecture·database,
논문 원고와 evidence에 직접 필요한 문서만 보존했다. 개인 연구에 적용하지 않는
기관 승인·학습효과 사전등록·외부평가 패키지·미래 단계별 카드와 중복 계획 문서는
삭제하고, 그 처리 내역은 아래 `retired-contracts` 절에 통합했다.

## 폴더별 내용

| 위치 | 내용 |
|---|---|
| `docs/` 루트 | 현재 상태·계획·AI Hub 데이터/라이선스 경계·감사 절차·coaching/WavLM readiness |
| `governance/` | 프로젝트 인수인계와 protocol deviation 원장 |
| `paper/` | 실험 원고, 주장·증거 갭, 방법 감사, source ledger와 현재 표 |
| `api/` | endpoint 목록과 API specification |
| `architecture/` | architecture, component, directory, flow, state |
| `database/` | 데이터베이스 schema |
| `audit/` | 공개 가능한 수동 정렬 감사 집계가 있을 때 사용하는 영역 |

## 읽는 순서

1. `governance/PROJECT_STATUS_AND_HANDOFF_KO.md`와
   `RESEARCH_COMPLETION_TRACKER_KO.md`에서 현재 상태와 선행 gate를 확인한다.
2. AI Hub 작업은 `DATA_CARD_AIHUB71490_KO.md`,
   `AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`,
   `DATA_PRIVACY_BOUNDARY_KO.md`, `DATA_LICENSE_SOURCE_LEDGER_KO.md`를 확인한다.
3. 실제 구현 계약은 `C01`/`C02`, `WAVLM_S107`/`WAVLM_S108`, `WP4` 문서를 확인한다.
4. 논문 반영은 `paper/EXPERIMENTAL_MANUSCRIPT_KO.md`와
   `paper/CLAIM_GAP_MAP_KO.md`를 먼저 확인한다.

## retired-contracts

다음 문서군은 개인 연구의 현재 실행 범위에서 제거했다. 기존 source/config의
pin도 함께 제거했으며, 해당 미래 단계는 새 계약을 정리하기 전까지 현재
실행·완료 근거로 승격하지 않는다.

| 제거한 문서군 | 통합된 대체 위치 |
|---|---|
| 기관 승인·윤리·학습효과·종단 수집 | 현재 상태와 범위는 `RESEARCH_COMPLETION_TRACKER_KO.md` |
| 외부평가·S2 독립감사·D07/D08/R04/R05 세부 카드 | 필요한 AI Hub 경계는 `DATA_PRIVACY_BOUNDARY_KO.md`와 `DATA_LICENSE_SOURCE_LEDGER_KO.md` |
| Phase 1 도구 감사·PDF/promotion 계획·미사용 primitive | 현재 실행 계약과 논문 원고/evidence |
| 이동 전 중복 stub·생성 결과 | 해당 원본 artifact 또는 `paper/tables/`의 현재 표 |

삭제된 문서의 미확인 실행·metric·사람 평가를 완료로 승격하지 않는다. AI Hub
원본·파생 음성·제한 manifest·checkpoint는 계속 Git과 공개 문서에서 제외한다.
