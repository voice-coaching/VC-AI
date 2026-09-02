# 아키텍처 규칙 - intelligentAI

## 실제 구조를 기준으로 한다

이 저장소는 `app/features/<feature>` 템플릿이 아니라 `src/voice_coach` 아래의
도메인·연구 단계별 모듈과 port/adapter 경계를 사용한다. 요청 없이 디렉터리를
일반 FastAPI CRUD 구조로 재편하지 않는다.

주요 경계는 다음과 같다.

| 경로 | 책임 |
| --- | --- |
| `src/voice_coach/api` | 공개 FastAPI 연구 API의 HTTP 경계 |
| `src/voice_coach/application` | Oracle, closed-loop 같은 상위 유스케이스 |
| `src/voice_coach/domain`, `coaching` | 공용 진단 계약, 코칭 ontology와 우선순위 |
| `src/voice_coach/pronunciation_feedback` | prompt→audio→근거 제한 피드백 파이프라인 |
| `src/voice_coach/pronunciation_attempts` | 동의·제한 media·Seungun·visual 시도 orchestration |
| `src/voice_coach/visual_feedback` | Seungun 종속 visual sidecar와 증거/gate 계약 |
| `src/voice_coach/data`, `features`, `models` | 데이터 해석, 특징, 모델 구현 |
| `src/voice_coach/training`, `evaluation` | 학습 및 평가 계약·실행 로직 |
| `src/voice_coach/experiments`, `reproducibility` | 실행 registry, receipt, 무결성·재현성 |
| `src/voice_coach/human_research`, `references` | 사람 대상 연구와 기준 음성 계약 |
| `scripts` | 얇은 CLI/composition root 및 운영 유틸리티 |
| `configs` | versioned JSON schema, template, 실험·runtime 계약 |
| `deploy/hyperclova` | 루트 환경과 분리된 로컬 모델 runtime |
| `seungun` | 별도 pyproject와 gate를 가진 한국어 phone CTC 하위 프로젝트 |

## 의존성 방향

기본 흐름은 다음과 같다.

`API/CLI -> application 또는 service -> Protocol port -> adapter -> 외부 효과`

- API/CLI는 framework 입력을 내부 command/schema로 변환하고 composition root를
  호출한다.
- application/service가 검증 순서, 상태 전이, gate, fallback, cleanup 정책을
  소유한다.
- domain/model 모듈은 값, enum, 불변식, 직렬화 계약을 소유한다. 가능한 한
  filesystem, network, subprocess, FastAPI, torch/transformers를 import하지 않는다.
- port는 서비스가 필요로 하는 좁은 `Protocol`을 정의한다.
- adapter는 외부 API, 모델, process, codec, 파일 저장소를 감싸고 upstream 값을
  내부 모델로 변환한다.
- composition 모듈만 구체 adapter를 조립한다. domain/service에서 임의로 concrete
  adapter를 생성하지 않는다.

기존 모듈에 이 이상형과 다른 legacy 경계가 있어도 요청 범위 밖 대규모 이동은
하지 않는다. 변경 대상의 책임이 더 섞이지 않게 하고, 필요한 최소 분리만 한다.

## 핵심 런타임 경계

### 발음 피드백

`PronunciationFeedbackPipeline`이 prompt 준비, audio QC, content/alignment, S1,
독립 S2 audit, 근거 제한 coaching을 순서대로 조합한다. optional backend가 없으면
명시적 unavailable 상태로 끝내며 가짜 점수를 만들지 않는다.

### 영상 시도

`VideoAttemptFacade`가 다음 순서를 소유한다.

1. 동의 확인
2. 제한 workspace에 media admission
3. MP4 probe와 내장 AAC→PCM 추출
4. 신뢰된 prompt 확인
5. Seungun을 정확히 한 번 호출
6. Seungun이 선택한 단일 phone anchor로만 visual handoff 생성
7. visual 측정·신뢰성·claim 적용
8. 성공/실패와 무관한 제한 사본과 PCM cleanup

visual 계층은 선택적 sidecar다. visual release/QC/sync/landmark/claim이 실패하면
승인되지 않은 visual claim을 만들지 않고 audio-only로 축소한다. cleanup 실패는
전체 시도의 fail-closed 조건이다.

### Visual feedback 소유권

- Seungun은 오디오 분석과 오류 phone 선택의 유일한 권위다.
- `visual_feedback`은 같은 attempt, prompt, media, selected phone의 binding을
  검증한 뒤 외부 입술 움직임을 보충한다.
- visual은 새 phone, 음운 오류, 혀·기류·성문 원인 또는 정확성 점수를 추론하지
  않는다.
- artifact 존재나 내부 fixture 통과만으로 production eligibility를 주장하지
  않는다. 현재 release/gate 코드와 실행 receipt를 함께 확인한다.

## 연구 파이프라인 경계

- `configs/experiments`와 `configs/**.schema.json`은 실행 전 계약이다.
- `scripts/prepare_*`는 준비/검증, `scripts/train_*`는 학습,
  `scripts/run_*`는 실행, `scripts/audit_*`는 감사라는 기존 역할을 유지한다.
- `training`, `evaluation`, `human_research`, `references` 간 gate와 artifact
  lineage를 우회하지 않는다.
- `seungun`은 루트 패키지의 단순 내부 폴더로 취급하지 않는다. 해당 작업은
  `seungun/pyproject.toml`, `seungun/README.md`, 자체 tests와 gate를 우선한다.

## FastAPI 경계

- `src/voice_coach/api/app.py`의 `/health`, `/v1/oracle-attempts`,
  `/v1/closed-loop/oracle-attempts`는 무상태 연구·계약 검증 API다.
- `evaluation/g04_amended_review_app.py`는 loopback 전문가 검수 앱이며 public
  production API가 아니다.
- route에서 모델 runtime, 파일시스템, 외부 HTTP, 학습 또는 복잡한 상태 전이를
  직접 수행하지 않는다.
- 현재 DB가 없으므로 repository 계층을 형식적으로 만들지 않는다. 영속화가
  요청되면 보존·삭제·동의 정책을 먼저 확정한 뒤 별도 설계한다.

## 환경 분리

- base API는 scorer/torch 없이 import 가능해야 한다.
- scorer·visual 의존성은 optional extra이며 base dependency에 끌어오지 않는다.
- Seungun과 HyperCLOVA는 transformers 버전이 다르므로 같은 Python 환경으로
  합치지 않는다.
- HyperCLOVA는 loopback, read-only model mount, offline mode, pinned revision
  경계를 유지한다.

## 아키텍처 변경 완료 조건

- import 방향과 optional-dependency 경계를 검사한다.
- 관련 unit/integration test와 최소 smoke를 실행한다.
- API, 상태, 외부 계약, 디렉터리 또는 데이터 흐름이 바뀌면 현재 허용된 `docs/`
  문서 갱신 필요성을 검토한다.
- 구현되지 않은 계층, DB, queue, auth, production API를 문서나 코드에 선행 추가하지
  않는다.
