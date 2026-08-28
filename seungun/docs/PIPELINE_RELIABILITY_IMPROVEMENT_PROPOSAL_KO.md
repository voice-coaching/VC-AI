# seungun 오디오 발음 코칭 파이프라인 신뢰성 개선안

기준일: 2026-08-25 (Asia/Seoul)  
상태: **개선 제안서 / 구현·검증 완료 문서가 아님**

## 1. 목적과 결론

이 문서는 현재 `seungun` 오디오 파이프라인의 장점을 유지하면서 다음 다섯
원칙을 어디까지 적용할 수 있는지 정의한다.

1. `target-free + target-conditioned` 이중 음성 해석
2. 관찰 가능성과 책임에 따른 evidence source 권한 제한
3. 전체 evidence와 충돌의 보존
4. 보정·OOD·abstention을 포함한 발행 전 신뢰성 gate
5. 한 번에 한 가지 행동만 제공하는 grounded feedback

결론은 다음과 같다.

- 영상·2D·3D branch를 현재 audio v1에 추가하지 않는다.
- XLS-R, MFA, prosody backbone을 교체하지 않는다.
- 이중 음성 해석과 source 권한 제한은 현재 구조에 상당 부분 구현되어 있으므로
  새 모델보다 계약과 검증을 보강한다.
- phone별 evidence는 이미 보존되지만 append-only ledger와 명시적 conflict
  disposition은 없으므로 최소 schema를 확장한다.
- 가장 큰 기능 공백은 detector와 feedback 사이의 **Reliability Gate**다.
- 기존 single-action grounded feedback은 유지하되 Reliability Gate를 통과한
  경우에만 호출한다.

## 2. 범위와 비범위

### 범위

- 현재 문서와 실제 artifact·코드의 상태 정합성
- pronunciation과 prosody evidence의 책임 경계
- target-free surface hypothesis와 target-conditioned error verdict의 분리
- evidence provenance, 충돌, 최종 disposition 계약
- 품질·지원 범위·OOD·보정·abstention gate
- pronunciation과 prosody를 통틀어 하나의 행동만 고르는 결정론적 정책
- 검증 순서와 문서별 후속 갱신안

### 비범위

- 입술·얼굴·자세·gesture·2D·3D 입력 추가
- 현 detector architecture 또는 threshold 재선택
- locked-test 결과를 이용한 재튜닝
- 음향 evidence만으로 혀·입술·턱·기류의 신체 원인 확정
- LLM의 음향 판정, threshold 변경 또는 새 조음 사실 생성
- production readiness, 교육 효과 또는 사용자별 진단 타당성 주장

영상 branch는 [CURRENT_PIPELINE_KO.md.md](CURRENT_PIPELINE_KO.md.md)의 audio v1
범위와 [NEXT_STEPS_KO.md](NEXT_STEPS_KO.md)의 실행 순서상 별도 후속 연구다.
이 문서의 개선안을 이유로 현재 파이프라인에 혼합하지 않는다.

## 3. 현재 근거와 상태 정합성

### 3.1 우선하여 읽을 근거

| 근거 | 역할 | 이 문서의 해석 |
|---|---|---|
| [CURRENT_PIPELINE_KO.md.md](CURRENT_PIPELINE_KO.md.md) | 설계 정본 | 책임 경계와 audio-first 원칙은 유지하되 detector 상태는 최신 artifact와 대조 필요 |
| [WORK_LOG_AND_FINDINGS_KO.md](WORK_LOG_AND_FINDINGS_KO.md) | 실험 이력 | 과거 시점의 관측과 실패를 보존하며 최신 상태로 소급 수정하지 않음 |
| [NEXT_STEPS_KO.md](NEXT_STEPS_KO.md) | 당시 실행 계획 | 완료된 artifact가 확인되면 계획과 현재 상태를 분리해 갱신 필요 |
| [HYPERCLOVAX_INTEGRATION_STATUS_KO.md](HYPERCLOVAX_INTEGRATION_STATUS_KO.md) | 2026-08-23 온라인 경로 보충 | frozen detector, bridge, 표현 계층의 현재 구현 근거 |
| [detector_config.json](../artifacts/error_detector/final_pronunciation_detector_v1/detector_config.json) | 동결 artifact | architecture·threshold freeze와 hash 근거 |
| [locked_test_results.json](../artifacts/error_detector/locked_test_evaluation_stage1_v1/locked_test_results.json) | 로컬 locked 결과 artifact | one-shot 결과가 존재하나 정본 문서 승격과 독립 재검증은 별도 과제 |
| [frozen_detector.py](../src/korean_phone_ctc/frozen_detector.py) | 실제 detector runtime | 점수 의미와 출력 evidence 계약 확인 |
| [seungun_bridge.py](../../src/voice_coach/pronunciation_feedback/seungun_bridge.py) | 실제 feedback bridge | top-1 generic claim과 LLM fallback 동작 확인 |

루트의
[C02 정적 준비도](../../docs/constraints/C02_DETERMINISTIC_COACHING_SELECTION_READINESS_KO.md)와
[E02 정적 준비도](../../docs/constraints/E02_CONFIDENCE_GUARD_READINESS_KO.md)는
상태명과 fail-closed 의미를 참고할 수 있다. 그러나 두 문서는 실제 C02/E02 실행
0과 미해결 predecessor를 명시하므로, 해당 threshold나 완료 상태를 `seungun`
runtime에 복사하거나 승격하지 않는다.

### 3.2 확인된 구현 및 artifact

- Stage-1 XLS-R, 9개 soft feature, layer-18 pooling, expected-phone embedding과
  MLP를 사용하는 `final_pronunciation_detector_v1` artifact가 존재한다.
- artifact는 `architecture_and_threshold_frozen_before_locked_test` 상태와
  threshold `0.8134703636169434`를 기록한다.
- runtime은 CTC hypothesis를 expected alignment 전에 만들고, 이후 expected
  phone을 조건으로 phone별 `error_score`를 계산한다.
- `PronunciationAnalysis`는 expected phones, CTC hypothesis, insertions,
  전체 phone evidence, detector/backbone provenance와 경고를 함께 보존한다.
- bridge는 threshold를 통과한 phone 중 최고 score 하나만 골라 generic
  재연습 claim에 연결한다.
- HyperCLOVA X는 승인 claim과 표현 방식만 선택하며 실패·timeout·모델
  `abstain`은 generic catalog 문구로 폴백한다.
- 로컬 artifact
  `locked_test_evaluation_stage1_v1/locked_test_results.json`은 frozen hash를
  결합한 `one_shot_locked_test`를 기록한다. 기록된 범위는 65,296개 발화,
  370명, 694,498 phone row이며 Error-F1은 약 `0.4568`, macro-F1은 약
  `0.7076`이다. 로컬 파일 SHA-256은
  `e6e0d110290b0f6170383e0dba59bb32c38b828c55f72e69f1604a217b370097`과
  일치한다.

마지막 locked 결과는 **로컬 artifact 존재와 hash 일치**의 근거다. 현재 정본
문서에는 승격되어 있지 않고 이 문서 작성 과정에서 독립 재실행하지 않았으므로,
독립 검증·publication·production readiness 근거로 확대하지 않는다.

### 3.3 해결해야 할 문서 충돌

- `CURRENT_PIPELINE_KO.md.md`와 `WORK_LOG_AND_FINDINGS_KO.md`는 final detector가
  아직 미확정이고 locked test가 미사용이라고 기록한다.
- 이후 frozen artifact와 locked evaluation artifact는 architecture·threshold
  freeze와 one-shot 결과를 기록한다.
- `NEXT_STEPS_KO.md`에는 이미 artifact가 존재하는 extraction·freeze·locked
  단계가 여전히 미래 작업으로 남아 있다.

따라서 현재 상태는 다음 세 층으로 분리해 기록해야 한다.

1. **역사적 사실**: 당시 문서 작성 시점의 실험 결과와 미완료 상태
2. **현재 로컬 artifact 사실**: freeze와 locked one-shot 결과 파일 존재 및 hash
3. **미검증 상태**: 독립 재현, 정본 승격, 실제 학습자 타당성, 전문가 안전 감사

과거 작업 로그를 고쳐 쓰지 말고, 날짜가 있는 후속 상태 절에서 충돌을 해소한다.

### 3.4 계속 유효한 실패와 제한

- manual alignment audit의 `alignment_accuracy=0.7417`은 기존 0.95 gate를
  통과하지 못했다.
- `error_score`는 ranking score이며 보정된 correctness confidence가 아니다.
- `observed_candidate`는 CTC alignment 후보이지 실제 발음의 정답 전사가 아니다.
- 단일 dev WAV 및 LLM smoke는 실행 가능성만 보이며 detector 정확도나 교육
  효과를 입증하지 않는다.
- DGX/local 다수 WAV parity, phone-to-text mapping, 전문가 조음 catalog,
  장시간·동시 요청 성능과 실제 학습자 blind review가 남아 있다.

locked 결과가 존재하더라도 위 실패와 제한은 자동으로 해소되지 않는다.

## 4. 개선 원칙별 판정

| 원칙 | 현재 상태 | 개선 판정 |
|---|---|---|
| target-free + target-conditioned | 단일 XLS-R backbone 위에서 두 역할이 존재 | 새 모델 추가 없이 역할·출력·불변성 계약을 명문화 |
| 관찰 가능성 기반 권한 제한 | g2pK/MFA/XLS-R/detector/policy/LLM 책임이 분리됨 | `source → 허용 claim → 금지 claim` 표를 정본에 추가 |
| evidence ledger와 충돌 보존 | 전체 phone evidence와 provenance를 보존 | evidence bundle을 유지하고 decision event와 conflict code를 별도 추가 |
| 보정·OOD·abstention | operating threshold만 있고 correctness calibration·통계적 OOD·detector abstention 없음 | 최우선 신규 기능으로 Reliability Gate 설계·검증 |
| 한 가지 grounded action | pronunciation bridge에서 구현 | gate 뒤에서 유지하고 prosody와의 전역 단일 선택을 검증 |

## 5. 목표 구조

```text
고정 script + 사용자 WAV
        |
        +--> g2pK expected phones -------------------------------+
        |                                                        |
        +--> MFA timing ----------------------+                  |
        |                                      |                  |
        +--> Prosody evidence -----------------+                  |
        |                                                         |
        +--> XLS-R target-free CTC surface hypothesis             |
                          |                                       |
                          +--> target-conditioned phone evidence <-+
                                           |
                                           v
                              Versioned Evidence Bundle
                                           |
                                           v
                                  Reliability Gate
                    quality -> support/OOD -> calibration -> conflict
                                           |
                    +----------------------+----------------------+
                    |                      |                      |
                 coach_one              no_issue        rerecord/abstain/
                    |                                      unsupported
                    v
          Deterministic global selector
          pronunciation 또는 prosody 중 한 행동
                    |
                    v
          승인 catalog + 제한형 표현 계층
```

이 구조에서 Reliability Gate는 detector를 다시 학습하거나 LLM을 호출하는 계층이
아니다. 이미 생성된 evidence가 자동 피드백 발행에 충분한지를 결정하는 순수하고
버전된 정책 계층이다.

## 6. 세부 개선안

### 6.1 target-free와 target-conditioned의 계약 분리

#### 정의

- `target_free_surface`: 추론 시 expected phone 또는 script를 입력하지 않고
  음향 logits에서 얻은 CTC hypothesis와 posterior 관련 evidence
- `target_conditioned_verdict`: expected phone, 정렬 결과, soft acoustic
  evidence와 hidden representation을 결합한 오류 판정

여기서 `target-free`는 **추론 시 prompt-free**라는 뜻이다. backbone 학습이
라벨과 무관했다거나 두 경로가 통계적으로 독립이라는 뜻이 아니다.

#### 필수 보존 항목

- target-free CTC hypothesis 전체
- expected phone sequence 전체
- expected/hypothesis alignment와 insertion/deletion/substitution
- phone별 soft feature, detector score, operating threshold와 score semantics
- detector/backbone/config hash와 schema version

#### 검증

- 같은 WAV에서 prompt를 바꿔도 target-free hypothesis가 변하지 않는지 검사
- target-conditioned 결과는 prompt 변경에 따라 바뀔 수 있음을 별도 검사
- target-free 후보와 conditioned verdict가 충돌해도 한쪽으로 덮어쓰지 않음
- reload 후 동일 hash와 허용오차 내 동일 결과 재현

### 6.2 evidence source 권한 표

| Source | 허용된 주장·결정 | 금지된 주장·결정 |
|---|---|---|
| Text normalization | 정규화된 script | 사용자의 실제 발음 판정 |
| g2pK | expected phone 후보 | 실제로 들린 phone 확정 |
| MFA | 단어·phone 시간 정렬과 coverage | 발음 오류 여부 단독 판정 |
| XLS-R target-free readout | heard-as acoustic candidate와 posterior evidence | 후보를 정답 전사로 표현 |
| Error Detector | expected phone에 대한 오류 ranking과 threshold 통과 여부 | score를 보정된 정답 확률로 표현 |
| Prosody Analyzer | 정의된 rate·pause·F0·intensity evidence | phone 조음 오류 또는 신체 원인 확정 |
| Reliability Gate | 자동 발행 가능 상태와 reason code | 새로운 acoustic fact 생성 |
| Deterministic Policy | 허용 issue 중 최종 행동 하나 선택 | 미지원 evidence를 임의 보완 |
| Advice catalog | 전문가가 승인한 행동 문구 | 승인 범위 밖의 혀·입술·턱·기류 지식 생성 |
| Qwen3/HyperCLOVA X | 승인 claim·style·candidate disclosure 선택 | 새 issue·phone·위치·score·조음 사실 생성 |

음향 source의 권한 제한을 명문화하는 것이 목적이며, 권한 빈칸을 채우기 위해 영상
source를 추가하지 않는다.

### 6.3 evidence bundle과 decision ledger

현재 `PronunciationAnalysis`는 유용한 evidence bundle이므로 대체하지 않는다.
최종 선택과 발행 판단만 별도의 append-only decision event로 남긴다.

권고 필드:

```text
analysis_id              # 가명 attempt 내부 식별자
analysis_schema_version
evidence_source
evidence_source_version
artifact_hashes
unit_type / unit_index
start_ms / end_ms
score
score_semantics
support_state
conflict_codes[]
decision_state
reason_codes[]
policy_version
created_at
```

최소 conflict code 후보:

- `TARGET_FREE_CONDITIONED_DISAGREEMENT`
- `ALIGNMENT_UNAVAILABLE`
- `SPAN_EVIDENCE_CONFLICT`
- `OBSERVED_CANDIDATE_UNSUPPORTED`
- `CROSS_BRANCH_PRIORITY_UNRESOLVED`

conflict code는 실제 두 evidence가 존재할 때만 발행한다. 서로 다른 목적의 지표를
단순히 다르다는 이유로 충돌 처리하지 않는다. 최종 피드백이 한 issue만 선택해도
탈락 evidence와 conflict disposition은 감사용 bundle에 남긴다.

raw audio path, speaker ID, 제한 transcript, 비밀정보는 LLM payload와 공개 로그에
포함하지 않는다.

### 6.4 Reliability Gate

#### 위치

`Evidence Bundle → Reliability Gate → Deterministic Selector → Grounded Feedback`

#### 평가 순서

1. artifact·schema·hash와 필수 provenance 무결성
2. 오디오 품질, content, 길이와 유한값 검사
3. alignment coverage와 phone span 사용 가능성
4. 지원 phone·issue·claim 범위
5. 사전등록된 OOD population과 판정 정책
6. calibration support와 selective-risk 기준
7. evidence conflict와 금지 claim 여부
8. 발행 상태와 safe action 결정

#### 권고 상태

| 상태 | 의미 | 허용 동작 |
|---|---|---|
| `coach_one` | 지원 범위 안에서 한 issue가 gate 통과 | 한 가지 승인 행동 발행 |
| `no_issue` | 유효 입력이나 발행 가능한 오류 없음 | 교정 없음 |
| `rerecord` | 품질·content·정렬 문제로 판단 불가 | 재녹음 요청만 발행 |
| `abstain` | evidence 부족·충돌·calibration support 부족 | 자동 교정 보류 |
| `unsupported` | OOD 또는 지원하지 않는 phone/issue/claim | 자동 교정 금지, 가능할 때 전문가 검토 안내 |

#### 필수 제약

- 현재 `0.8134703636169434`는 detector operating threshold이지 correctness
  probability threshold가 아니다.
- Error-F1 최적 threshold 선택과 probability calibration을 같은 의미로 쓰지
  않는다.
- OOD population, detector, threshold와 mixed-reason precedence는 근거 없이
  작성하지 않고 preregistration에서 동결한다.
- 이미 사용한 locked 결과로 새 gate나 threshold를 조정하지 않는다. Reliability
  Gate의 수치 선택에는 새 speaker-disjoint calibration/prospective 자료를 사용한다.
- `abstain`을 모델 생성 실패와 혼동하지 않는다. 표현 모델의 `abstain`은 현재
  catalog fallback이고, 시스템 진단 `abstain`은 feedback 호출 자체를 막는다.
- blocked 상태에서는 top-2나 다른 branch로 backfill하지 않는다.

#### 평가 지표

- error precision/recall/F1과 average precision
- ECE, Brier score, NLL
- coverage-selective risk와 abstention rate
- `coach_one` issued precision과 false-alarm rate
- reason별 `rerecord`·`abstain`·`unsupported` 비율
- speaker·기기·환경별 worst-group 결과와 bootstrap confidence interval

위 수치의 목표값은 이 문서에서 임의로 정하지 않는다. calibration 자료와
서비스 위험 허용범위를 먼저 승인한 뒤 preregistration에 기록한다.

### 6.5 한 가지 grounded action

현재 pronunciation bridge의 top-1 generic claim 정책은 유지한다. 단 다음 조건을
추가한다.

- 입력은 `decision_state=coach_one`인 issue만 허용한다.
- pronunciation과 prosody 후보가 함께 있을 때도 최종 행동은 하나다.
- pronunciation raw detector score와 prosody deviation을 같은 척도처럼 직접
  비교하지 않는다.
- cross-branch priority는 승인된 categorical band 또는 별도 deterministic
  policy로 결정한다.
- 우선순위를 결정할 근거가 없으면 `CROSS_BRANCH_PRIORITY_UNRESOLVED`로 보류한다.
- LLM은 선택된 action, 위치, 수치, score semantics를 변경하지 못한다.
- 전문가 phone catalog 전에는 현재 generic 재연습 claim 밖의 신체 조언을
  발행하지 않는다.

## 7. 문서별 반영 계획

| 대상 문서 | 문제 | 제안 변경 | 우선순위 |
|---|---|---|---|
| `CURRENT_PIPELINE_KO.md.md` | detector 미확정·locked 미사용 상태가 최신 artifact와 충돌 | 날짜가 있는 현재 상태 절, dual readout, source 권한 표, Reliability Gate 위치 추가 | P0 |
| `NEXT_STEPS_KO.md` | extraction·freeze·locked 평가가 미래 단계로 남음 | 완료 artifact와 미검증 후속을 분리하고 locked 결과 재튜닝 금지 명시 | P0 |
| `WORK_LOG_AND_FINDINGS_KO.md` | 역사 기록 이후 artifact가 반영되지 않음 | 기존 내용을 고치지 않고 freeze·locked 결과와 hash를 날짜가 있는 후속 절로 append | P0 |
| `HYPERCLOVAX_INTEGRATION_STATUS_KO.md` | 표현 모델 abstain과 시스템 abstention이 혼동될 수 있음 | upstream `decision_state` 요구, catalog fallback과 진단 보류의 차이 명시 | P1 |
| [seungun/README.md](../README.md) | 읽는 순서가 최신 상태를 반영하지 않을 수 있음 | 정본·실험 이력·보충 상태·본 개선안의 읽는 순서 추가 | P1 |
| 루트 C02/E02 문서 | 유사 상태명이 있으나 실제 실행 0인 별도 계약 | 의미·reason code만 호환 검토하고 completion·threshold는 복제 금지 | P1 |

이 제안서 작성 작업에서는 위 기존 문서를 수정하지 않는다. 실제 반영 시에는 한
문서만 고쳐 새로운 충돌을 만들지 말고 P0 문서를 같은 변경 단위에서 정합화한다.

## 8. 실행 우선순위와 완료 조건

### P0. 상태 정합성 복구

작업:

- frozen config, state, normalization, locked result hash 재검증
- 정본·계획·작업 로그에 동일한 lifecycle 상태 사용
- historical status와 current status 분리

완료 조건:

- `architecture_frozen`, `locked_evaluated_local_artifact`,
  `independent_validation_pending`을 혼용하지 않음
- locked metric과 hash가 artifact에서 기계적으로 재계산됨
- manual audit 실패와 미검증 gate가 유지됨

### P1. 계약과 정적 검증

작업:

- dual-readout schema와 authority matrix 동결
- evidence decision-event schema와 conflict code 동결
- Reliability Gate의 상태·reason·precedence preregistration 초안 작성
- C02/E02와 의미 충돌 여부 검토

완료 조건:

- schema validation과 unknown enum fail-closed 테스트
- blocked top-k no-backfill 테스트
- 개인정보·raw path·free text 주입 거부 테스트

### P2. 작은 smoke 구현

작업:

- Reliability Gate 순수 정책 구현
- bridge가 `coach_one`만 받도록 연결
- `no_issue`, `rerecord`, `abstain`, `unsupported` 출력 경로 구현

검증:

- prompt swap 시 target-free hypothesis 불변
- clipping·낮은 SNR 등 오디오 품질 실패 fixture에서 `rerecord`
- 지원 밖 phone/issue fixture에서 `unsupported`
- conflicting evidence fixture에서 `abstain`
- threshold 통과 phone이 없을 때 실패가 아니라 `no_issue`
- 표현 모델 timeout·invalid JSON·모델 abstain은 승인 catalog fallback
- 시스템 abstain에서는 표현 모델을 호출하지 않음

이 단계는 합성·소수 비-locked 자료의 smoke이며 성능 완료를 의미하지 않는다.

### P3. 새 calibration·OOD 검증

작업:

- locked test와 겹치지 않는 speaker-disjoint calibration/prospective cohort 확보
- OOD population과 지원 범위 사전등록
- probability calibration과 selective-risk 비교
- reason별 safe action 및 precedence 동결

완료 조건:

- 데이터 역할과 speaker 교집합 0 검증
- reload·artifact hash·prediction 재현
- calibration과 prospective evaluation 결과를 분리
- 기준 미달 시 자동 coaching 비활성화 유지

### P4. 전문가·사용자 검증

작업:

- phone-to-text mapping과 표시 구간 검증
- phone별 조음 claim 전문가 감사
- 실제 학습자 blind review
- 장시간·동시 요청 latency/throughput과 실패율 측정

완료 조건:

- 전문가 승인되지 않은 신체 claim 0
- 한 번에 행동 하나와 evidence fidelity 검증
- 실제 학습자 결과 전에는 교육 효과·일반화 성능을 주장하지 않음

## 9. 검증 매트릭스

| 검증 대상 | 최소 검증 | 실패 시 처리 |
|---|---|---|
| artifact 무결성 | config/state/normalization/result hash와 schema | 전체 자동 피드백 차단 |
| target-free 불변성 | 동일 WAV의 prompt drop/swap/sham | dual-readout 계약 실패 |
| expected conditioning | prompt 변경에 따른 conditioned 결과와 provenance | 결과 발행 차단 |
| evidence 보존 | 선택되지 않은 phone·충돌·reason 잔존 | publish 거부 |
| 오디오 품질 | clipping/SNR/VAD/content fixture | `rerecord` |
| OOD·unsupported | 사전등록 population과 rule | `unsupported` 또는 `abstain` |
| calibration | ECE/Brier/NLL/coverage-risk | `coach_one` 비활성화 |
| single action | pronunciation+prosody 동시 후보 | 한 행동 또는 보류 |
| LLM grounding | ID tamper·새 claim·timeout·abstain | catalog fallback |
| 시스템 abstention | blocked 상태에서 LLM 호출 0 | 통합 실패 |
| 사용자 타당성 | 독립 전문가·학습자 blind review | production·교육 효과 주장 금지 |

## 10. 금지되는 해석과 변경

- locked Error-F1 `0.4568`을 사용자별 진단 정확도라고 부르지 않는다.
- average precision, macro-F1, Error-F1을 서로 바꾸어 쓰지 않는다.
- operating threshold를 보정된 confidence라고 부르지 않는다.
- CTC observed candidate를 확정 전사로 제시하지 않는다.
- LLM smoke 성공을 feedback 안전성·교육 효과로 승격하지 않는다.
- manual audit 실패를 locked 결과로 소급 해소하지 않는다.
- root C02/E02의 static readiness를 실제 `seungun` runtime 완료로 해석하지 않는다.
- 영상 연구 결과를 근거로 현 audio MVP의 backbone·policy를 교체하지 않는다.
- locked 결과를 본 뒤 architecture·threshold를 변경하고 동일 결과를 다시
  locked test라고 부르지 않는다.

## 11. 최종 권고

현 `seungun` 파이프라인의 우위는 오디오 모델의 수를 늘리는 데 있지 않고,
expected/acoustic evidence의 역할 분리, 동결 artifact, 결정론적 선택과 제한형
표현 계층에 있다. 따라서 다음 변경만 추진한다.

1. 현재 문서와 artifact 상태를 먼저 정합화한다.
2. 현 dual readout을 명시적 계약으로 승격한다.
3. 전체 evidence와 충돌을 선택 결과와 별도로 보존한다.
4. 새 prospective 자료로 검증한 Reliability Gate를 feedback 앞에 둔다.
5. gate를 통과한 경우에만 한 가지 grounded action을 제공한다.

영상·2D·3D는 audio v1의 위 문제를 해결하는 수단이 아니며, 현재 개선 범위에서
제외한다. 향후에도 별도 branch와 paired ablation에서 audio 기준선을 유의미하게
개선한다는 근거가 생긴 경우에만 다시 검토한다.
