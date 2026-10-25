# 저장소 전수 목록 조사와 .codex 개편 — 2026-10-19

## 기준과 한계

후속 구현으로 신규 내부 분석은 request v4/result v6/handoff v2로 통합했다. [현재 계약과 적용 범위](canonical-integration/v6-unification.md)를 우선한다. 아래 목록·수치는 조사 당시 snapshot이며 현재 접수 계약이나 운영 준비를 선언하지 않는다.

조사 기준은 로컬 `chore/repository-start-20261019`, HEAD `ee8eefb377226d42b59b05d1b06950b050ca1b5e`다. Git 추적 목록 전체, 주요 디렉토리·모듈 책임, 분석/HTTP/저장/배포 진입점, schema, 문서 및 개발 규칙을 정적으로 조사했다. 전체 파일의 동작을 줄 단위로 감사하거나 실제 실행을 검증한 결과는 아니다.

원격 Backend/Frontend checkout·서버·GPU·B2 계정을 조회하지 않았다. 비밀 설정·사용자 미디어·모델을 열거나 실행하지 않았다. QA·브라우저 자동화·자동 회귀·실제 녹음/GPT/TTS 생성은 수행하지 않았다. 아래 운영 설명은 **기존 기록의 관측**이며 10/19 현재 상태를 증명하지 않는다.

시작 시 `.codex` 안내 5개는 삭제 상태였다. 루트 `AGENTS.md`에는 충돌 표식과 없는 문서에 대한 placeholder가 남아 있었다. ignored 로컬 개발 규칙 8개·시작 프롬프트는 FastAPI 템플릿 구조를 전제했다. 요청에 따라 안내를 새 내용으로 작성하고 규칙을 수정했다. 기존 `deploy/dlpc/README.md` 변경, compact GOP 관련 신규 파일·테스트, notebook checkpoint는 편집하지 않았다.

## 전체 목록

조사 시작 기준 추적 파일은 **1,331개**다. 아래 수는 작업 중 새 문서를 Git index에 추가하기 전 `git ls-files` 기준이며 ignored/untracked 자료는 포함하지 않는다.

| 범위 | 파일 수 | 책임 |
|---|---:|---|
| `src/voice_coach` | 406 | 음성·영상·근거 기반 코칭, 실행·연구 계약 |
| `src/patent_search` | 7 | 별도 특허 검색·PDF 입력·수집/API 도구 |
| `seungun` | 44 | 별도 한국어 음소 검출 코드·bridge·추론 실행 |
| `scripts` | 157 | serving, 데이터 수집·준비, 실험·정적 확인 도구 |
| `deploy` | 144 | DLPC 68, RunPod 69, legacy HyperClova 5, Redis worker 2 |
| `configs` | 192 | 코칭·음성/영상·실험·reference·평가·사람 연구 계약/설정 |
| `data` | 8 | 공유 가능한 manifest·reference sentence card |
| `docs` | 114 | API·계약·운영 이력·설계·연구 |
| `tests` | 237 | 기존 unit/integration 자료. 읽기·목록 조사만 수행 |
| `requirements` | 9 | 역할·플랫폼별 의존성/lock |
| `.codex` | 5 | 기존 공유 안내. 새 규칙은 시작 시 ignored |
| `.github` | 2 | base CI, DLPC 배포 workflow |
| 루트 파일 | 6 | AGENTS, pyproject, env 예제, Git/Docker 설정 |

`pyproject.toml`의 배포 패키지명은 `voice-coach`, Python 요구는 `>=3.12,<3.13`이다. 기본 FastAPI/Pydantic/uvicorn/httpx/jsonschema/rfc8785와 선택 scorer/visual/test 의존성을 구분한다. 루트 요구 범위를 별도 core·TTS 환경의 Python 버전과 혼동하지 않는다. `src/patent_search`는 loopback API·SQLite job store를 가진 별도 도구로 음성 운영 경로에 결합하지 않았다.

## 실제 package 지도

경로는 `src/voice_coach` 기준이다. 파일 수는 하위 파일을 포함한다.

| package | 파일 수 | 역할·시작할 소스 |
|---|---:|---|
| `api` | 2 | 연구 FastAPI `app.py`: oracle/closed-loop API |
| `application` | 4 | oracle·closed loop·adversarial TTS 유스케이스 |
| `domain` | 2 | 공통 진단·코칭 모델 |
| `backend_analysis` | 75 | 내부 HTTP, canonical 실행·identity·journal·handoff·복구, legacy adapter |
| `direct_analysis` | 7 | 익명 API, service/store/worker, 독립 delivery |
| `pronunciation_attempts` | 25 | 같은 시도 미디어 admission·derivation, 격리 Seungun adapter |
| `pronunciation_feedback` | 48 | 음성 pipeline·근거 projection·gate·코칭·branch/threshold·adapters |
| `visual_feedback` | 127 | models/ports/services/adapters, QC·A/V sync·입술·MFA·reference 정책 |
| `coaching` | 6 | 후보 선택·ontology·전문가 audit 계약 |
| `phonology` | 6 | 음소/태그 정규화, expected phone·g2pK |
| `data` | 11 | dataset parsing·QC·split·TextGrid·audio |
| `features` | 7 | explicit/GOP/SSL·eGeMAPS·F0 도구 |
| `models` | 7 | baseline·WavLM·adversarial 모델 |
| `training` | 22 | 학습 실행과 단계별 승인/preflight 계약 |
| `evaluation` | 29 | 지표·calibration·locked/gold/closed-loop 계약·로컬 review |
| `experiments` | 5 | 실험 registry·특정 변환 worker receipt |
| `references` | 9 | reference candidate·gate·bank·선택·평가 계약 |
| `human_research` | 4 | 공식 승인·종단 수집·학습 효과 분석 계약 |
| `reproducibility` | 9 | 경로 보호·cache·protocol deviation·재현 근거 |
| 최상위 `__init__.py` | 1 | package 진입 |

연구·학습·사람 연구 모듈의 파일 존재는 운영 활성화나 사람 승인 완료를 뜻하지 않는다. 내부 HTTP의 grant 제외 결정으로 연구 데이터의 별도 승인 gate를 해제하지 않는다.

## 경로·계약·저장소 대조

| 경로 | 확인한 소스·계약 | 조사 판단 |
|---|---|---|
| 연구 oracle | [api/app.py](../src/voice_coach/api/app.py) | 연구용 `/v1/oracle-attempts`, closed-loop. 운영 분석 프로세스와 별도 |
| 내부 분석 | [http_api.py](../src/voice_coach/backend_analysis/http_api.py), [http_models.py](../src/voice_coach/backend_analysis/http_models.py), [http_runtime.py](../src/voice_coach/backend_analysis/http_runtime.py) | `/v1/analysis-jobs`와 인증 health. 신규 parser는 request v3/v4만 허용 |
| 음성 canonical | [후속 v6 통합 정본](canonical-integration/v6-unification.md) | request v3/result v5/handoff v1. 이전 버전 reader와 신규 admission 구분 |
| 영상 후보 | [v6 인계](video-lip-analysis/audiovisual-v6-handoff-20261007.md), [실행 승인](../src/voice_coach/backend_analysis/native_execution.py) | request v4/result v6/handoff v2 구현·배포/승인 gate를 구분. 이번 조사에서 활성화하지 않음 |
| direct | [API](../src/voice_coach/direct_analysis/api.py), [worker](../src/voice_coach/direct_analysis/worker.py), [store](../src/voice_coach/direct_analysis/store.py) | 명시 활성화된 익명 접수. Backend ID/lease/callback 없이 로컬 원본 영속화 후 결과 공개, 보관·이력은 별도 |
| 실행 승인 | [harness](../src/voice_coach/backend_analysis/canonical_harness.py), [native_execution](../src/voice_coach/backend_analysis/native_execution.py) | 과거 읽기 identity와 Native/GOP 신규 실행 허용 tuple 분리 |
| legacy | [production.py](../src/voice_coach/backend_analysis/production.py), [9월 배포](runpod_deployed_pipeline_20260928.md) | Clova/Redis 분기와 9월 코칭 정책이 남아 있음. 현재 경로를 파일명·docstring만으로 단정하지 않음 |

같은 시도·원본 bytes·음소 index·MFA/PTS·prepared media digest의 결속은 schema 형식 검증과 별도 의미 검증이다. legacy의 null 점수·최대 3개 후보·fallback을 Native/GOP 전 경로에 적용하면 안 된다. 정책 변경은 해당 core/H5·실행 identity·소비 계약을 함께 확인한다.

v5 Backend 인계·검증/commit·B2 archive, direct 결과 공개·archive·Backend history sync는 서로 다른 상태다. B2는 GPU scratch나 POSIX journal을 대체하지 않는다. 사용자 녹음 reader, canonical archive writer, 복구 reader와 Native/S3 credential을 구분한다. Backend PostgreSQL table/migration은 이 저장소의 ORM 모델이 아니다.

## 배포·운영 기록의 순서

1. [9/28](runpod_deployed_pipeline_20260928.md)·[9/30](runpod_deployed_pipeline_20260930.md): RunPod HTTP/GPT legacy 관측.
2. [10/7 DLPC GOP 전환](canonical-integration/dlpc-gop-cutover-20261007.md): 당시 실제 CUDA/GPT·TTS 표본과 RunPod 중지 관측. 광범위한 품질 QA 결과는 아님.
3. [10/8 Actions 활성화](canonical-integration/dlpc-actions-activation-20261008.md), [10/15 cold 복구](canonical-integration/incident-20261015-dlpc-cold-recovery.md): 배포·기동 복구의 후속 기록.
4. [10/16 J406 이전](canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md): J3990→J406 전환·당시 readiness와 남은 개발자 QA. 현재 환경과 가장 가까운 실행 기록.

host별 identity는 [host_identity.py](../deploy/dlpc/host_identity.py), 설치 release는 [deployed_release.py](../deploy/dlpc/deployed_release.py), 시작/복구는 [DLPC 안내](../deploy/dlpc/README.md)와 해당 launcher로 대조한다. source checkout·설치 bundle·복구 baseline·실행 프로세스는 같은 식별자가 아니다.

[실제 배포 workflow](../.github/workflows/deploy-dlpc.yml)는 main push, 수동 실행, **5분** catch-up을 정의한다. 10/16 기록의 30분 설명과 다르며 설정 근거는 현재 YAML이다. enrollment·self-hosted runner가 필요하고 문서 수정만으로 활성 상태를 확정하지 않는다. [base CI](../.github/workflows/ci.yml)는 pytest·Docker 작업도 포함하지만 이번 작업에서 실행하지 않았다. push는 이런 작업을 유발할 수 있으므로 수행하지 않았다.

## .codex 개편 결과

- [README](../.codex/README.md): 요청별 최소 읽기, 규칙/사실 우선순위, 공유·로컬 자료 경계를 정리했다.
- [backend-http](../.codex/backend-http.md): legacy·v5·v6, 내부 HTTP·direct·Backend 소유권을 구분하고 소스 대조 순서를 제공한다.
- [runpod](../.codex/runpod.md): 기존 파일명을 유지하며 DLPC/J406·과거 RunPod와 배포/복구·관측 보고를 연결한다.
- [env](../.codex/env.md): 역할별 parser/launcher와 비밀 전달 경계를 제공한다. `.env.example`을 전체 운영 설정으로 오인하지 않게 했다.
- [backblaze](../.codex/backblaze.md): 자산 backup/restore, J406 profile, canonical archive와 연구자료 역할을 나눴다. 불필요한 인증/업로드 명령 예제를 제거했다.
- 개발 규칙 8개와 시작 프롬프트: `app/features` 강제·가상의 CRUD/ORM 계층·임의 공백 규칙을 실제 package 책임으로 교체하고 QA 제한을 일관되게 반영했다.
- `AGENTS.md`: 충돌 표식·없는 문서 안내를 제거하고 legacy 정책과 현재 경로를 구분했다. 사용자 결정과 기존 원본 근거 보존 요구를 유지했다.
- `.gitignore`: 안내·시작 프롬프트·정확히 8개 규칙만 공유 허용하고 ref_docs/local/미검토 신규 파일은 계속 제외한다. Git index 추가·commit은 하지 않았다.

## 남은 불일치

| 항목 | 근거·영향 | 이번 처리 |
|---|---|---|
| 오래된 ‘현재’ 설명 | `docs/README.md`, `runpod-current-pipeline.md`, API/계약 목차에 서로 다른 날짜 상태 혼재 | 새 안내에서 날짜·소스와 대조하도록 명시. 이력 전체 재작성은 하지 않음 |
| ignored 아키텍처 template | `과거 구조 template 디렉토리`에 `app/features`, 인증/외부 API 미사용, 빈 상태 표 등 | 현재 구현의 정본으로 인용하지 않도록 규칙·조사에서 경고. 사용자 로컬 template는 보존 |
| DB 문서 범위 혼재 | 무상태 oracle 설명 뒤에 Backend PostgreSQL handoff 추가 | 소유 경계를 새 규칙에 명시. 실제 Backend DB 전수조사는 별도 |
| 사라진 RunPod workflow 링크 | `deploy/runpod/GITHUB_ACTIONS.md:3` → `../../.github/workflows/deploy-runpod.yml` | 기존 끊어진 링크로 기록. 폐기 workflow를 재생성하지 않음 |
| 없는 성능 기록 | `docs/canonical-integration/dlpc-mfa-gop-optimization-plan-20261007.md:22` → `testaudio1-announcer-timing-20261007.md` | 원본 미확인, 추정 문서 생성 안 함 |
| 없는 이전 계획 | `docs/canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md:3` → `dlpc-to-dlpc-migration-plan-20261016.md` | 실행 기록 자체는 존재. 누락 계획을 완료 사실로 복원하지 않음 |
| 환경별 실제 readiness/quality | 원격 조회·실제 생성·개발자 QA 미수행 | 현재 상태·품질은 미확인 |

## 정적 확인

- 전체 추적 Python **926개**는 `ast.parse`, JSON **159개**는 `json.loads`로 읽었으며 구문 오류는 없었다. 검사 환경 Python은 3.10이며 배포 환경의 import/compile·의존성·실행 호환성 검증은 아니다. 테스트 파일도 텍스트로 파싱했을 뿐 실행하지 않았다.
- 초기 `python scripts/check_markdown_links.py`에서 기존 끊어진 링크 **3개**를 확인했다. 최종에는 같은 검사기의 함수를 사용해 새 공유 파일까지 포함한 예정 파일 집합으로 확인했다. 수정·신규 Markdown **15개**의 끊어진 상대 링크는 **0개**, 전체 예정 Markdown **129개**에는 기존 결함 **3개**만 남았다. Git index는 수정하지 않았다. 외부 URL·heading fragment 검증은 포함하지 않는다.
- `git diff --check`를 통과했다. 공유 파일의 ignore 해제와 local/ref_docs/미검토 신규 규칙의 제외를 확인했다. 수정 문서에 충돌 표식과 깨진 checkout placeholder는 없다.

이 결과는 문서·소스 목록/구문 조사다. commit/push/PR/배포·원격 기동·추론·품질 QA를 수행한 결과로 해석하지 않는다.
