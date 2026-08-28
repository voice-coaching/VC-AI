# 컴포넌트 및 모듈 문서 - intelligentAI

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

프로젝트 단위를 어떻게 분리하고 재사용하는지 기록한다.

## 분리 기준
- 공통 단위: `voice_coach.domain`의 엄격한 JSON 계약
- 기능 전용 단위: `coaching`, 향후 `features`, `models`, `references`
- Service/Application 단위: `application.oracle` 같은 use case 조합
- Repository/Adapter 단위: 데이터·모델 I/O 구현 시 추가
- UI 또는 Presentation 단위: `voice_coach.api`

## 목표 기능 경계: pronunciation_feedback

`prompt + audio -> feedback` 유스케이스는 Oracle 계약과 섞지 않고 별도 기능
경계로 둔다. 하위 단위와 소유 규칙은 다음과 같다.

| 단위 | 책임 | 소유 규칙 |
|---|---|---|
| prompt catalog | 연습 문장 선택과 표시 payload 발급 | 문장 텍스트·난이도·라이선스의 유일한 출처. 발음 판정 금지 |
| prompt frontend | expected phone contract와 offset 매핑 | g2pK 예측을 관측 라벨로 승격 금지 |
| audio intake | decode·QC·임시 저장 | raw audio를 기능 밖으로 전달 금지 |
| S1 scorer | branch별 점수·confidence·missing mask | 단일 총점으로 축약 금지 |
| S2 auditor | 독립 backend 감사와 independence receipt | S1 backend ID·alignment 재사용 금지 |
| gate/decision | coach/rerecord/uncertain/abstain 결정 | 결정 없이 문구 생성 금지 |
| feedback composer | 결정을 사용자 표현으로 변환 | 새 오류·새 위치·catalog 밖 문구 생성 금지 |
| grounded LLM selector | 승인 claim ID와 표현 style 선택 | 자유 문장·새 오류·음소·위치·수치·조음 사실 생성 금지 |
| deterministic advice renderer | 검증된 plan과 catalog 원문을 학습자 문장으로 조합 | LLM이 쓴 조음 문장을 직접 노출 금지 |
| model runtime | weight 상주와 timeout | attempt마다 checkpoint 재로딩 금지 |
| attempt 이력 | 재녹음 비교 (호출자 소유) | pipeline은 무상태, raw audio 미보존 |

각 단위는 port 하나에 대응한다. 현재 기본 wiring은 prompt/audio/gate/feedback뿐
아니라 XLS-R 음절 CTC content/alignment, GOP S1, Qwen2-Audio S2 adapter도
연결한다. S1은 hard decode만 사용하지 않고 음절별 CTC 전체 분포에서
`target_log_posterior`, `gop_margin`, `posterior_entropy`, `target_is_top1`을
`CtcSoftInformation`으로 보존한다. 이 evidence는 segmental advice에만 연결하며,
학습자 라벨로 보정되지 않은 entropy를 새 gate로 사용하지 않는다. `model runtime`은
별도 port가 아니라 각 scorer adapter가 자기 수명 안에서 책임진다. 상세 상태와
주장 한계는 [`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)를 따른다.

선택적 HyperCLOVA X adapter는 분석 모델과 분리된 표현 adapter다. 오직
loopback OpenAI-compatible endpoint를 호출하며, 실패하면 기존 catalog 원문으로
degrade한다. 구체 계약은
[`HYPERCLOVAX_GROUNDED_FEEDBACK_KO.md`](HYPERCLOVAX_GROUNDED_FEEDBACK_KO.md)에
기록한다.

## 계약 규칙
- 입력 모델 규칙: Pydantic extra field 금지, 점수 0–1, UTF-8
- 출력/result 모델 규칙: camelCase JSON, `schemaVersion` 필수
- 재사용 기준: HTTP와 무관한 정책만 domain/application에서 재사용
- 소유 경계: coaching은 허용된 advice ID와 우선순위 정책을 소유

## 프로필 메모

### Python
- import 시점에 파일 생성, 네트워크 호출, 프로세스 실행 같은 부수효과가 발생하지 않게 한다.
- 실행 가능한 모듈은 `main()` 진입점을 제공하고 직접 실행 코드는 `if __name__ == "__main__"` 아래에 둔다.
- 표준 라이브러리로 충분한 문제에 불필요한 dependency를 추가하지 않는다.
- 공개 함수와 클래스에는 의미 있는 타입 힌트를 유지하고, 입력/출력 모델은 명확한 자료 구조로 표현한다.
- 순수 계산 로직과 I/O 로직을 분리해 테스트와 재사용이 가능하게 한다.

### FastAPI
- APIRouter는 라우팅과 의존성 연결만 담당한다.
- Service가 FastAPI Response, Depends, Request에 직접 의존하지 않게 한다.
- Repository는 SQLAlchemy, SQLModel 등 실제 저장소 API를 감싸는 경계로 둔다.
- 예외는 도메인/서비스 예외로 먼저 표현하고 controller에서 HTTP 상태로 변환한다.
- 배경 작업, 외부 호출, 긴 작업은 service 하위의 명시적 adapter/client로 분리한다.

## G04 amended 평가 경계

| 단위 | 책임 | 금지 사항 |
|---|---|---|
| `g04_amended_contract` | T43 선택·state·집계, I30 phone scorer, progress/completion 검증 | T43을 accuracy 분모로 사용 |
| `g04_review_frontend` | profile별 검수 화면·입력 검증·진행 탐색 | 판정 기본값 생성, 원본 ID·경로 표시, 직접 state 접근 |
| `g04_amended_review_app` | 정적 자산과 token 보호 `/api/v1` audio/review API | raw ID·경로 응답, 인간 판정 자동 생성 |
| `G04AmendedReviewStore` | profile 계약 검증과 JSON state 원자 저장 | presentation·HTTP 의존, T43/I30 field 혼합 |
| D07/S108 소비자 | A1 progress와 completion attestation 검증 | R1 state·audio·row 접근, legacy 240건 gate 수용 |

T43은 알려진 문제 후보의 원인·조치 완결성을 판정하고 I30만 독립
phone-level accuracy를 제공한다. 두 역할은 하나의 총 정확도로 합치지 않는다.
