# 아키텍처 규칙 — intelligentAI

현재 package 경계를 유지한다. [프로젝트 조사](../../docs/project-survey-20261019.md)와 [Backend–AI 안내](../backend-http.md)에서 소스·계약을 찾는다.

| 경계 | `src/voice_coach` 내부 위치 | 책임 |
|---|---|---|
| 연구 HTTP·유스케이스 | `api`, `application`, `domain` | oracle/closed loop와 공통 계약 |
| 내부 분석 | `backend_analysis` | 인증/DTO, canonical 실행·인계·복구, 외부 adapter |
| 익명 직접 접수 | `direct_analysis` | API·service·store·worker·비동기 delivery |
| 시도·음성 | `pronunciation_attempts`, `pronunciation_feedback` | 미디어 admission, 근거·정책, pipeline·ports/adapters |
| 영상 | `visual_feedback` | models/ports/services/adapters, QC·sync·landmark·MFA 결속 |
| 코칭·음운 | `coaching`, `phonology` | 근거 기반 선택·ontology·정규화 |
| 연구·학습 | `data`, `features`, `models`, `training`, `evaluation`, `experiments`, `references`, `human_research`, `reproducibility` | 데이터·학습·평가·승인 계약 |

배포는 `deploy`, 별도 검출 코드는 `seungun/src`에 있다. 기능 폴더별 책임과 기존 composition/factory를 따른다. `app/features/<feature>`, login/post/talk, ORM 계층을 템플릿에 맞추려고 만들지 않는다.

HTTP → service/application 조합 → ports/adapters·domain 방향으로 책임을 분리한다. domain은 FastAPI Request/Response를 알지 않게 한다. 모델·미디어·저장소·provider 호출은 명시된 adapter/client 또는 격리 프로세스로 연결한다.

Request/Response DTO, domain result, journal, private 원본 bytes와 공개 projection은 서로 다른 계약이다. 원본 provenance를 보존한다. 새 기능은 책임을 가진 기존 package에 먼저 배치하며, 일괄 이주·공개 경로 변경·동결 core/H5/schema 변경을 문서 정리에 섞지 않는다.

`src/patent_search`는 별도의 특허 검색·수집·PDF 입력 도구이며 loopback API와 SQLite job store를 가진다. 음성 분석 경로와 임의 결합하지 않는다.

음성 제품의 Backend/Frontend·PostgreSQL은 별도 저장소 책임이다. 실제 해당 revision을 확인한 경우에만 구현 사실로 기록한다. 구조 변경 시 흐름·상태·계약 정본을 함께 갱신한다.
