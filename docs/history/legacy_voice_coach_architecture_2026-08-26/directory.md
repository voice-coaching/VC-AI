# 디렉토리 문서 - intelligentAI

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

이 파일은 현재 프로젝트 구조를 설명하는 기준 문서다.

## 필수 작성 항목
- 디렉토리 경로
- 주요 파일 경로
- 책임
- 담당 기능 및 계층
- 중요한 의존성 메모

기능/도메인 폴더를 바깥 경계로 두고, 각 기능 하위의 계층 폴더를 문서화한다.

## 현재 구조

목표 발음 feedback pipeline의 상세 target architecture는
[`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)에
있으며, 아래 표의 현재 구현 구조와 구분한다.

| Path | Type | Responsibility | Owner | Notes |
| --- | --- | --- | --- | --- |
| `src/voice_coach/domain` | directory | DiagnosticProfile과 enum 계약 | core | 외부 I/O 의존 금지 |
| `src/voice_coach/application` | directory | 진단·코칭 use case | core | HTTP 객체 의존 금지 |
| `src/voice_coach/coaching` | directory | 우선순위와 advice allowlist | coaching | evidence 없는 조언 금지 |
| `src/voice_coach/api` | directory | FastAPI route와 계약 연결 | api | 비즈니스 판단 금지 |
| `tests/unit` | directory | 순수 계약·정책 테스트 | core | 빠른 단위 테스트 |
| `tests/integration` | directory | HTTP 계약 테스트 | api | 현재 DB 없음 |
| `scripts` | directory | 데이터 준비와 benchmark 진입점 | data/ml | import 부수효과 금지 |
| `configs/data` | directory | 데이터 join·split·privacy 정책 | data | restricted 경로 사용 |
| `configs/experiments` | directory | 고성능 GPU 실험 gate | ml | GB10 기준 |
| `src/voice_coach/phonology` | directory | expected phone·오류 태그·g2pK 계약 | core | 관측 라벨 덮어쓰기 금지 |
| `data/reference/r00` | directory | 고정 연습 문장 카드(20문장, CC0) | reference | 목표 pipeline의 프롬프트 출처 |
| `configs/coaching` | directory | advice ontology와 selection/guard schema | coaching | 17-tag draft는 전문가 감사 미완 |

## 발음 feedback pipeline 구조

`prompt + audio -> feedback` 경로의 실제 구조다. HTTP route는 이 프로젝트
범위가 아니므로 존재하지 않는다.

| Path | Type | Responsibility | Owner | Notes |
| --- | --- | --- | --- | --- |
| `src/voice_coach/pronunciation_feedback/domain.py` | file | PracticePrompt/PromptSpec/LearnerFeedback/AttemptResult 등 계약과 상태 enum | core | 외부 I/O import 금지 |
| `src/voice_coach/pronunciation_feedback/language_scope.py` | file | `ko`/`en` 타깃 언어 fail-closed 검사 | core | audio/model 호출 전 수행 |
| `src/voice_coach/pronunciation_feedback/ports.py` | file | catalog/expected-phone/audio/S1/S2/threshold/advice port | core | Protocol만, 구현 세부 금지 |
| `src/voice_coach/pronunciation_feedback/pipeline.py` | file | Stage A0--H 조합, terminal 결과 생성 | core | 모델·파일 API 직접 호출 금지 |
| `src/voice_coach/pronunciation_feedback/gate.py` | file | Stage F veto 정책 | core | 모든 미충족은 상태로 종료, 점수 보정 금지 |
| `src/voice_coach/pronunciation_feedback/feedback.py` | file | Stage H 학습자 표현 구성 | coaching | 판정·문구 생성 금지, 변환만 |
| `src/voice_coach/pronunciation_feedback/grounded_advice.py` | file | LLM 입력/plan schema, strict validator, 결정론적 renderer | coaching | 승인 claim 밖 사실 생성 금지 |
| `src/voice_coach/pronunciation_feedback/thresholds.py` | file | ThresholdSet 계약과 provenance 검증 | ml | calibrated set은 dataset/manifest/frozen_at 필수 |
| `src/voice_coach/pronunciation_feedback/adapters/` | directory | R00 catalog, 음절 매핑, g2pK frontend, soundfile QC, threshold/advice 로더 | ml/data | dependency별 lazy import |
| `src/voice_coach/pronunciation_feedback/adapters/advice_generator_hyperclova.py` | file | loopback OpenAI-compatible grounded plan client | ml | audio/path 미전달, no retry, invalid 응답은 폴백 |
| `src/voice_coach/pronunciation_feedback/adapters/hyperclova_transformers_runtime.py` | file | pinned local snapshot의 lazy BF16 Transformers runtime | ml | local files only, 모델 1회 상주 |
| `scripts/run_pronunciation_feedback.py` | file | CLI entrypoint (`prompts`, `attempt`) | core | 인자 파싱·직렬화만 |
| `scripts/download_hyperclovax_seed_think_32b.py` | file | pinned model snapshot 다운로드·byte/shard 검증 | ml | floating revision 금지, `data/models`는 Git 제외 |
| `scripts/serve_hyperclovax_grounded_advice.py` | file | 내부 loopback chat-completions sidecar | ml | product API가 아니며 외부 bind 금지 |
| `scripts/run_seungun_grounded_feedback.py` | file | seungun WAV detector→grounded LLM feedback CLI | ml/coaching | expected phones는 호출자가 제공, score를 confidence로 승격 금지 |
| `configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json` | file | model commit·크기·runtime policy | ml | claim scope `engineering_provisional` |
| `seungun/src/korean_phone_ctc/frozen_detector.py` | file | frozen Stage-1/layer-18 phone detector 온라인 inference | ml | artifact hash·feature schema·threshold 고정, local files only |
| `configs/pronunciation_feedback/thresholds` | directory | profile별 calibration artifact 위치 | ml | 현재 artifact 없음 → `thresholds_unavailable` |
| `src/voice_coach/evaluation/g04_amended_contract.py` | file | T43/I30 선택·state·scorer·progress/completion 계약 | evaluation | 순수 계산과 원자 JSON export; T43 accuracy 분모 금지 |
| `src/voice_coach/evaluation/g04_amended_review_app.py` | file | amended 로컬 정적 서버와 versioned review API | evaluation | loopback/token, raw ID·경로 미노출 |
| `src/voice_coach/evaluation/g04_review_frontend/` | directory | T43/I30 HTML·CSS·JavaScript GUI | presentation | package data, own-origin API만 사용, 판정 기본값 금지 |
| `scripts/prepare_g04_amended_audit.py` | file | T43 준비·T43/I30 serve/status/export/attestation CLI | evaluation | immutable artifact no-overwrite |
| `configs/expert_evaluation/g04_*_v1.schema.json` | files | T43 manifest, amended progress/completion JSON schema | evaluation | D07/S108 소비자 계약 |
