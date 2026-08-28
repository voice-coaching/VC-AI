# 흐름 문서 - intelligentAI

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

> **legacy/runtime 구분(2026-08-10):** 아래 G04 amended 흐름은 현재 구현을
> 설명하지만 새 정책에서는 T43/I30이 `diagnostic_only`다. D07/S108 개방은
> snapshot 당시 `PIPELINE_COMPLETION_PLAN_2026-08-10_KO.md`(현재 tracked tree에서 퇴역)의
> P0 migration 후 새 독립 gate를 따른다. P0 전 runtime은 legacy fail-closed다.

주요 기능 흐름을 단계별로 기록한다.

## Oracle 계약 검증

- Actor: 연구자 또는 통합 테스트
- Entry point: `POST /v1/oracle-attempts`
- Preconditions: 정답 오류와 0–1 범위의 점수
- Steps:
  1. 입력 schema를 검증한다.
  2. `severity × confidence × impact × trainability`로 오류를 정렬한다.
  3. 내용 불일치와 저신뢰 결과를 먼저 차단한다.
  4. allowlist에 있는 top-1 오류만 코칭한다.
  5. `DiagnosticProfile` version 1.0을 반환한다.
- Validation: 시간 구간 순서, evidence 최소 1개, 허용 enum
- Empty state: 오류가 없으면 coaching action은 `none`
- Error state: schema 위반은 HTTP 422
- Permission behavior: 현재 인증 없음, 연구 전용
- Retry or recovery: 입력 수정 후 재요청
- Side effects: 없음
- Related API: `POST /v1/oracle-attempts`
- Related DB tables: 없음


## 목표: prompt + audio 발음 피드백 pipeline

현재 구현된 Oracle 흐름과 실제 음성 분석 target을 혼동하지 않도록, 목표
pipeline의 단계·입출력·fail-closed 조건은
[`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)의
Stage A0--H와 검증 표를 따른다. 현재 `/v1/oracle-attempts`는 이미 계산된
content/aspect/gold issue를 받는 연구용 계약이며, 음성 파일 upload endpoint가
아니다.

목표 흐름은 다음 세 구간으로 나뉘며, 앞 구간은 뒤 구간의 모델 품질과 무관하게
독립적으로 성립·검증할 수 있다.

1. 프롬프트 제공 (Stage A0--A): 서버 카탈로그가 연습 문장을 선택해 사용자 표시
   텍스트와 모델용 expected phone contract를 같은 `promptId`에서 파생하고,
   `ko`/`en` 타깃 언어 gate를 audio/model 호출보다 먼저 적용한다. 출처는 검증된
   한국어 R00 sentence card 20문장이며 client 자유 입력은 기본 비허용이다.
   영어는 계약상 허용되지만 catalog/frontend/calibration은 아직 없다.
2. 낭독 수신·판정 (Stage B--F): audio QC → content/alignment → S1 → S2 독립 감사
   → gate. 모든 실패는 명시적 state로 종료하고 결손을 0점으로 대체하지 않는다.
3. 피드백 구성 (Stage G--H): 결정과 근거를 문장 위 span·조언 문구·재녹음 지시로
   변환한다. 이 구간은 새 판정을 만들지 않는다.

현재 기본 wiring에는 1구간과 3구간뿐 아니라 XLS-R 음절 CTC
content/alignment, GOP S1, Qwen2-Audio S2도 연결되어 있다. XLS-R의 hard decode는
내용 일치에 사용하고, 전체 log-posterior는 강제정렬·GOP와 음절별 soft evidence
(`target_log_posterior`, `gop_margin`, `posterior_entropy`, `target_is_top1`)에
사용한다. soft evidence는 segmental 조언의 근거로 전달되지만 새 threshold나
연구 검증을 만들지 않는다. 실제 품질 주장은 계속 `engineering_provisional`이며
상세 상태는 [`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)를 따른다.

### 선택적 grounded LLM 표현 흐름

gate가 허용한 top-1 issue가 있을 때만 advice catalog 원문을 claim으로 포장해
로컬 HyperCLOVA X에 전달한다. 모델은 claim ID와 `direct|focus` style만 선택하고,
코드가 plan을 재검증한 뒤 최종 문장을 결정론적으로 만든다. timeout, malformed
JSON, ID 변경, 미승인 claim, abstain은 attempt를 실패시키지 않고 catalog
원문으로 폴백한다. 음성·경로·hash·화자 ID는 이 경계를 넘지 않는다.

`seungun` bridge는 frozen detector의 threshold-passed phone 중 error score가 가장
큰 하나를 deterministic하게 선택한다. 동일 score이면 expected phone index가
앞선 항목을 고른다. 이 선택 뒤에만 generic 승인 claim과 optional HyperCLOVA
selector를 적용하며, detector score를 confidence나 정답 확률로 바꾸지 않는다.

## G04 amended 평가 흐름

1. 고정 240건에서 현재 정렬 재계산 drift 19건과 AI non-abstain 주석 불일치
   26건을 고르고 교집합 2건을 제거해 T43 43건 manifest를 동결한다.
2. loopback 서버가 정적 GUI를 제공하고 browser는 URL fragment의 실행별 token을
   session storage로 옮긴 뒤 `/api/v1` header에만 사용한다. item 목록에는 hash와
   비식별 metadata만 반환하고 audio path는 서버 내부 locator로 유지한다.
3. 인간 reviewer가 T43의 정렬·주석 fidelity·root cause·resolution을 판정한다.
   T43은 enriched sample이므로 accuracy를 계산하지 않는다.
4. 별도 I30 30건을 15/15 `error_presence` 층으로 청취하고 잘못 매핑된 phone
   interval 수를 기록한다.
5. API는 profile별 제출 계약을 재검증하고 전체 state를 원자 교체한다. POST는
   기존 판정을 덮어쓰지 않고 PUT만 명시적 교체를 허용한다.
6. I30 phone accuracy 0.95, T43 unresolved/uncertain 0, reviewer attestation,
   소비자 이관을 모두 검증해 aggregate completion을 만든다.
7. D07과 S108은 aggregate만 읽는다. 하나라도 미완료면 `blocked_g04`이며
   제한 state·audio·행을 열지 않는다.
