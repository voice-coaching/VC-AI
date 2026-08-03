# 문서 구조 안내

이 디렉터리는 프로젝트 문서를 주제별로 분류한다. 실행 계약이 직접
참조하는 정본 문서는 기존 경로 호환성을 위해 일부 `docs/` 루트에
그대로 둔다. 이동된 문서 중 기존 링크가 많은 항목에는 짧은 이동 안내
stub를 남겼다. 문서 이동은 데이터·오디오·checkpoint·제한 manifest를
이동하거나 공개하는 작업이 아니다.

## 폴더별 내용

| 폴더 | 내용 |
|---|---|
| `governance/` | 프로젝트 상태·인수인계와 protocol deviation 같은 운영·거버넌스 기록 |
| `evaluation/` | R02–R05, D07, D08, E00, H04, S2 holdout 등 평가 readiness와 data/model/privacy card |
| `pipeline/` | Phase 0/1 primitive, E01 변환 primitive, R03 selector, S201 metric, WavLM/GAN 실험 보조 문서 |
| `paper/` | 원고·evidence map·출판 감사 문서. `paper/planning/`은 PDF/출판 계획과 amendment 문서 |
| `api/` | API endpoint와 specification |
| `architecture/` | 시스템 architecture, component, directory, flow, state |
| `database/` | 데이터베이스 schema |
| `audit/` | 공개 가능한 수동 정렬 감사 집계 TSV |

## 읽는 순서

1. `governance/`와 루트의 `RESEARCH_COMPLETION_TRACKER_KO.md`에서 현재 상태와
   선행 gate를 확인한다.
2. 데이터 작업은 루트의 `DATA_PRIVACY_BOUNDARY_KO.md`,
   `DATA_LICENSE_AND_ROLE_MATRIX_KO.md`, `DATA_LICENSE_SOURCE_LEDGER_KO.md`를
   확인한다.
3. 실험별 readiness/data card는 `evaluation/`, WavLM·primitive 구현은
   `pipeline/`에서 찾는다.
4. 논문 반영은 `paper/`의 evidence map과 validation 문서를 먼저 확인한다.

실제 실행 가능성, 모델 성능, 사람 평가 완료 여부는 이 분류만으로
추론하지 않는다. 각 정본 문서의 현재 상태와 fail-closed gate를 따른다.
