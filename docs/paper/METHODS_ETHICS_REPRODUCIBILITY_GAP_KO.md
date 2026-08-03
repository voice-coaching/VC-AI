# 방법·윤리·재현성 독립 갭 감사

- 최종 갱신: 2026-07-29 KST
- 대상: WP10/P04 첫 편집 묶음 결과
- 판정: **P04 미완료**
- 변경 범위: 원고·최소 evidence-map·이 감사 문서와 evidence validation
  report 갱신, tracker·모델·config·cache·head·registry 미수정

## 1. 목적과 감사 기준

이 문서는 [현재 실험 원고](EXPERIMENTAL_MANUSCRIPT_KO.md)의 방법, 통계,
실패 사례, 윤리, 재현성 서술을 다음 네 요소로 분해한다.

- **A — 이미 뒷받침됨**: 원고에 있고, 고정 artifact·registry 또는
  evidence validator가 뒷받침하는 문장
- **B — 지금 보강 가능**: 새 결과를 만들지 않고 현재 protocol·정책·고정
  artifact로 추가할 수 있는 문장
- **C — 아직 쓸 수 없음**: 외부 사람, 승인 또는 후속 실험 전에는 관측
  결과로 쓸 수 없는 문장
- **D — 해제 조건**: B를 안전하게 옮기거나 C를 결과로 바꾸기 위해 필요한
  정확한 증거

이 분류는 [P00 주장 맵](CLAIM_GAP_MAP_KO.md)의 O/G/R/X 경계를 따른다.
A는 대체로 O, protocol 시제의 B는 G, 외부·후속 결과인 C는 X에
대응한다. 관련연구 source 확인은 별도 P04/P05 gap으로 남는다.

> 후속 WP4 S104–S108의 계획·해제 순서는
> [WP4 S103 이후 실행 계약](../WP4_POST_S103_EXECUTION_CONTRACT_KO.md)을
> 따른다. 이 링크는 아래 A/B/C/D 판정, 완료 artifact와 당시 검증 수치를
> 소급 변경하지 않으며 실제 결과는 기존 증거 우선순위를 계속 따른다.

## 2. 읽은 정본과 우선순위

감사에 사용한 문서는 다음과 같다.

1. [현재 원고](EXPERIMENTAL_MANUSCRIPT_KO.md)
2. [P00 주장·증거 갭 맵](CLAIM_GAP_MAP_KO.md)
3. [연구 완성 추적기](../RESEARCH_COMPLETION_TRACKER_KO.md)
4. [개인정보·반출 경계](../DATA_PRIVACY_BOUNDARY_KO.md)
5. [데이터 라이선스·역할 매트릭스](../DATA_LICENSE_AND_ROLE_MATRIX_KO.md)
6. [논문 수치 evidence validation](../MANUSCRIPT_EVIDENCE_VALIDATION_KO.md)
7. [G04 수동 정렬 감사 절차](../AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md)
8. [H01 학습효과 사전등록 초안](../LEARNING_EFFECT_PREREGISTRATION_KO.md)
9. [외부평가 실행 패킷](../EXTERNAL_EVALUATION_EXECUTION_PACK_KO.md)
10. [WavLM frozen-head 학습 계약](../WAVLM_FROZEN_HEAD_TRAINING_KO.md)
11. [WavLM Base+–Large 비교 계약](../WAVLM_FROZEN_HEAD_COMPARISON_KO.md)
12. [WavLM cache 독립 검증 계약](../WAVLM_FEATURE_CACHE_VALIDATION_KO.md)
13. [관련연구 source ledger](RELATED_WORK_SOURCE_LEDGER_KO.md)
14. [자료 라이선스 원문·역할 ledger](../DATA_LICENSE_SOURCE_LEDGER_KO.md)

상태 충돌 시 고정 aggregate artifact와 registry, evidence validator,
tracker, 최신 세부 protocol, 오래된 실행계획 순으로 우선한다. 계획 문서의
문장을 실제 수행 문장으로 바꾸지 않는다.

S103·R00 관측값과 검증 cohort SVG·자료 license ledger까지 반영한 현재
evidence report는 다음 계약을 통과한다.

- artifact `36/36`, manuscript claim `475/475`
- PENDING marker `18개`, marker multiset hash 일치
- explicit dev, threshold ablation, WavLM cache, Base+/Large head와
  S103의 stack/calibration/locked-test 접근 모두 `false`
- registry metric split은 `dev`, `dev_exploratory`, `inner_val`
- report SHA-256:
  `026799ab9ceb3d93128cc7c4558dcbe8d5ed4f0be3afefe19db198bb5e48b7bd`

## 3. 현재 충족률

아래 충족률은 **P04 편집 증거 커버리지**일 뿐 tracker의 연구 진행률이
아니다. 각 영역의 핵심 항목 8개, 총 40개를 같은 가중치로 계산했다.

| 영역 | A: 현재 충족 | B: 즉시 보강 | C: 후속/외부 대기 | 현재 A 비율 | A+B 잠재 비율 |
|---|---:|---:|---:|---:|---:|
| 방법 | 7 | 0 | 1 | 87.5% | 87.5% |
| 통계 | 7 | 0 | 1 | 87.5% | 87.5% |
| 실패 사례 | 6 | 0 | 2 | 75.0% | 75.0% |
| 윤리 | 6 | 0 | 2 | 75.0% | 75.0% |
| 재현성 | 7 | 0 | 1 | 87.5% | 87.5% |
| **합계** | **33** | **0** | **7** | **82.5%** | **82.5%** |

첫 편집 묶음의 B 14개를 protocol·정책·교정 기록·고정 artifact 범위로
원고에 반영했고 evidence validation을 다시 통과했으므로 현재 편집 증거
커버리지는 S103·R00 증거 반영 뒤 `82.5%`다. B는 0개이며 나머지 C
7개는 새 실험·외부 승인과
사람 자료 또는 최종 부록 없이는 A로 옮기지 않는다.

## 4. 방법 갭

| ID | 분류 | 문장 또는 갭 | 현재 근거 | D — 정확한 해제 조건 |
|---|---|---|---|---|
| M01 | A | 322,380건 cohort, 5-way speaker-disjoint split, PCM 중복 0, prompt 예약과 train/dev filtering | data card·G0·prompt·cache artifact와 원고 2.1/2.3 | 수치 변경 시 source artifact hash와 evidence-map claim을 함께 갱신 |
| M02 | A | Gold target boundary는 입력 경계이고 오류 정답은 target/realized overlap에서 파생한 silver label이며 독립 gold가 아님 | explicit cache/report와 원고 2.3/2.4 | G04/D07 전에는 `gold error label` 또는 배포 성능으로 바꾸지 않음 |
| M03 | A | explicit phone/utterance 특징, label 역할, train median 결측 처리, dev model selection | explicit config/report와 원고 2.4 | locked-test 문장과 결합하지 않고 dev/oracle 범위를 유지 |
| M04 | A | threshold ablation은 train 내부 speaker-disjoint validation에서 C와 threshold를 고른 post-hoc/exploratory 분석 | ablation config/report/registry와 원고 2.4 | primary baseline을 덮어쓰거나 승격 근거로 쓰지 않음 |
| M05 | A | Base+ cache의 pinned revision, 16 kHz, 20초 chunk, pooling 차원, 실제 dtype과 streaming validator | cache report·validation report와 원고 2.4/3.3 | cache 완결성을 head 성능으로 바꾸지 않음 |
| M06 | A | Frozen head의 train-only inner validation, early stopping/threshold 고정과 dev task·seed당 1회 추론을 protocol 시제로 반영 | frozen-head 학습 계약, 원고 2.4, evidence validation | 실제 수행형 과거 시제와 성능은 completed report·registry 후에만 사용 |
| M07 | A | C0↔C3/C3↔C5 participant 1:1 병렬 비교, 2주 post와 post 4주 후 retention을 planning-only로 반영 | H01 사전등록 초안, 원고 2.4 | `draft/not registered`, 외부 입력 없음과 `final N=null`을 실제 승인·lock 전까지 유지 |
| M08 | C | predicted MFA/CTC 배포 성능, S2·coaching·reference/TTS·학습자 intervention을 실제 사용한 방법으로 서술 | R00 텍스트 패키지만 완료; predicted alignment·S2·coaching·TTS/audio·사람 intervention 결과 없음 | predicted-alignment report, S200–S203, C02/C03, R01–R04, H00–H04의 versioned 완료 artifact와 registry가 각각 필요 |

### 방법 핵심 경계

- `oracle alignment`는 배포 가능 정렬이 아니다.
- silver 오류 label은 독립 전문가 gold가 아니다.
- train/dev 방법과 locked-test 방법을 같은 완료 시제로 합치지 않는다.
- Base+ cache `completed`와 validator `passed`는 frozen head 성능 완료가
  아니다.

## 5. 통계 갭

| ID | 분류 | 문장 또는 갭 | 현재 근거 | D — 정확한 해제 조건 |
|---|---|---|---|---|
| S01 | A | explicit dev의 phone macro F1/AUPRC와 utterance QWK/Spearman/MAE/ICC, dev 선택 순서 | explicit report와 evidence-map | metric 정의·반올림을 바꾸면 report와 claim regex를 동시 갱신 |
| S02 | A | threshold 탐색은 inner validation 전용이며 dev는 seed별 최종 1회, stack/calibration/locked-test 미접근 | ablation report·registry·locked guard | 사후 결과를 confirmatory p-value나 locked-test CI로 표현하지 않음 |
| S03 | A | seed 42/43/44의 동일 explicit 결과와 표준편차 0을 불확실성 부재로 해석하지 않음 | explicit report·원고 3.2 | 반복 seed를 독립 표본 수로 간주하지 않음 |
| S04 | A | B05가 대기이며 현재 locked-test metric은 없고 validator 접근 flag가 모두 false | evidence validation·tracker B05 | G04 통과와 전용 B05 run 전에는 locked-test 수치·CI·Holm 결과 금지 |
| S05 | A | explicit train/dev 통계, 완료된 S103 bootstrap과 아직 계획인 B05/H04 통계를 별도 상태로 분리 | 원고 2.4/3.3, S103 comparison/rendering artifact, evidence validation | B05/H04의 실제 CI·Holm 값은 각 completed report에서만 인용 |
| S06 | A | H01의 ITT, participant/sentence/rater random intercept, MAR/MI·MNAR·complete-case sensitivity와 family별 Holm을 planning-only SAP로 반영 | H01 사전등록 초안, 원고 2.4 | 실제 분석 전 승인 protocol version·SAP를 고정하고 H04 report 전 계획 시제를 유지 |
| S07 | A | Base+–Large 차이, paired speaker-bootstrap CI·p-value와 0.03 gate 결과 | Base+/Large 3-seed completed report·registry, 12 prediction artifact, S103 completed commit/report/receipt와 독립 renderer | dev-only·oracle/silver·no-promotion 제한을 유지하고 S104 이후 결과와 혼동하지 않음 |
| S08 | C | C0↔C3/C3↔C5 effect size·95% CI·결측 sensitivity·학습효과 결론 | pilot·참여자·rating 없음, final N null | IRB, 모집, pre/post/retention, blind rating, SAP lock, full simulation, final aggregate H04 report 필요 |

### bootstrap/Holm 계획과 실행의 구분

현재 원고의 B05/H04 bootstrap·Holm 문장은 **분석 프로토콜**이다.
S103의 paired-speaker bootstrap 5,000회와 primary CI·p-value는 완료된
report와 source hash-chain이 있어 실행 결과로 쓸 수 있다. 다만 이
완료를 아직 실행하지 않은 B05/H04 통계로 확대하지 않는다. Synthetic
comparator test 통과만으로는 실제 통계 결과가 아니다.

## 6. 실패 사례 갭

| ID | 분류 | 문장 또는 갭 | 현재 근거 | D — 정확한 해제 조건 |
|---|---|---|---|---|
| F01 | A | `interval_out_of_bounds` 1,070건과 phone tier 누락 1건을 primary에서 제외하고 failure slice로 보존 | G0 artifact·원고 3.1 | G04 판정과 혼동하지 않고 자동 QC 실패로 유지 |
| F02 | A | 정상/오류 class 불균형, 과다 오류 발행, L1·gender·proficiency·phone worst slice | privacy-safe failure report·원고 3.2 | 독립 gold 오류율이나 배포 prevalence로 표현하지 않음 |
| F03 | A | threshold가 macro F1을 높였지만 AUPRC는 불변이고 0.60 gate에 실패해 B06 `failed_unchanged` | ablation report·registry·원고 3.2/4 | 성능 향상·승격 성공 제목으로 바꾸지 않음 |
| F04 | A | BF16 autocast 요청과 관측 float32 hidden output, float16 저장을 분리하고 CUDA preflight를 성능으로 해석하지 않음 | cache/smoke report·원고 2.4/3.3 | head metric이 생겨도 cache dtype 사실과 별도 문장으로 유지 |
| F05 | A | Stem prefix 재식별 위험의 발견→중단·폐기→namespaced SHA-256 수정→재검증→가명화 한계를 원고에 반영 | 개인정보 경계 문서, 원고 4.1 | 공개 전 canonical schema/hash와 old cache 부재를 계속 확인하고 익명화로 표현하지 않음 |
| F06 | A | failed/KeyboardInterrupt run, Large normalize 불일치와 registry 원자성 결함을 발견→차단→수정→재검증→성능 한계 순서로 반영 | tracker, frozen-head 학습/review 계약, 원고 4.1 | 구현 교정을 실제 Large/head metric 결과와 분리하고 각 완료 artifact hash를 후속 보고 |
| F07 | C | S1/S2 disagreement, metamorphic perturbation 실패율과 reward-hacking 사례 | S2/E01 결과 없음 | S1/S2 동일 item aggregate, 전문가 판정, perturbation별 report와 failure taxonomy 필요 |
| F08 | C | coaching 위해·unsupported claim 실제 사례, TTS hard-gate 실패, participant adverse feedback·탈락 사례 | 외부 자료 없음 | C03/R04/H03의 동의된 원자료와 비식별 aggregate failure/adverse report 필요 |

실패를 보고할 때 “발견→중단→수정→재검증→남은 한계” 순서를 사용한다.
실패한 gate를 삭제하거나 후속 탐색 결과로 덮어쓰지 않는다.

## 7. 윤리·개인정보 갭

| ID | 분류 | 문장 또는 갭 | 현재 근거 | D — 정확한 해제 조건 |
|---|---|---|---|---|
| E01 | A | 학습자 파일럿은 기관 승인·동의·보존/폐기 계획 뒤에만 진행하며 현재 완료 결과가 아님 | 원고 5절, tracker H00–H04, H01 외부 flag 모두 false | 승인번호가 생기기 전에는 “IRB 승인”, “participants enrolled” 금지 |
| E02 | A | R0/R1/D1/A1/C1 구역과 반출 경계를 원고 표에 반영 | 개인정보 경계 문서, 원고 5절 | 산출물별 실제 zone과 공개 가능 여부를 공개 전 재대조 |
| E03 | A | Namespaced SHA-256을 가명화로 한정하고 대응표 분리·자유서술 검토를 명시 | 개인정보 경계 문서, 원고 5절 | 익명화 표현을 금지하고 접근권한·보존정책을 승인 계획과 연결 |
| E04 | A | AI Hub 제한, FLEURS/ZEROTH attribution, KSS/Seoul 비상업 격리와 F5 code/weight 조건 분리를 반영 | 라이선스·역할 매트릭스, source ledger, 원고 5절 | 최종 P05 license 원문·revision·재배포 감사 전 완전 공개 가능 주장 금지 |
| E05 | A | 성인 사람 자료의 동의·철회, 목적 제한, 접근자, 보존·폐기와 note 검토를 수집 전 protocol로 반영 | 개인정보 경계·외부평가 패킷, 원고 5절 | 개인 연구 scope attestation과 consent/version/hash 전에는 수행 완료 시제 금지 |
| E06 | A | 참여자 연구를 선택할 때 미성년자·위해 중단을 포함한 IRB/기관 승인 전 수집 금지 조건을 반영 | 외부평가 패킷·H01 사전등록, 원고 5절 | 참여자 기관 검토·승인 전에는 계획으로만 유지하고 실제 adverse 결과로 표현하지 않음 |
| E07 | C | 참여자 연구의 IRB/PIPA 승인번호, 승인일, protocol version과 모집 허가 | H00 외부 의존 | 참여자 기관 승인본과 공개 가능한 승인 metadata hash 필요 |
| E08 | C | 실제 participant/reviewer 수·인구통계·철회·adverse event·보존/삭제 이행 | 사람 자료 미수집 | 동의된 자료, 접근통제 audit trail, 비식별 집계와 해당 경로의 승인 범위 확인 필요 |

### 개인정보 zone을 원고에 옮길 때의 제한

- R0/R1의 음성·전사·경로·worksheet는 공개 산출물이 아니다.
- D1의 SHA-256 ID도 연구 데이터이며 익명 자료로 단정하지 않는다.
- A1 집계라도 라이선스와 연구승인을 별도로 검토한다.
- Checksum은 재현 식별자이지 공개 허가가 아니다.
- 원고·표·그림에는 개별 utterance/speaker/reviewer/participant ID를
  넣지 않는다.

## 8. 재현성 갭

| ID | 분류 | 문장 또는 갭 | 현재 근거 | D — 정확한 해제 조건 |
|---|---|---|---|---|
| R01 | A | data/split/model/config checksum, seed, prediction, metric report를 run별 보존하고 explicit report SHA를 공개 | 원고 5절·evidence-map | artifact 변경 시 provenance review 없이 hash만 교체하지 않음 |
| R02 | A | explicit·threshold registry run ID, metadata SHA와 등록 artifact hash를 공개 | completed registry·원고 5절 | failed run과 completed run을 구분하고 registry split guard 유지 |
| R03 | A | WavLM cache report·독립 validation·CUDA smoke SHA와 전수검증 범위를 공개 | cache/validation/smoke artifact·원고 3.3/5 | cache 검증을 head reproducibility로 확대 해석하지 않음 |
| R04 | A | Exact lock 79개, 격리 clean recreation, 실제 CUDA 연산과 알려진 metadata 경고를 report SHA와 함께 반영 | clean recreation report, evidence-map, 원고 5절 | 환경 변경 시 새 clean report와 hardware/runtime aggregate를 다시 고정 |
| R05 | A | Task별 RNG 재초기화, `CUBLAS_WORKSPACE_CONFIG=:4096:8`, train-only threshold와 dev 1회 추론 계약을 반영 | frozen-head 학습 계약·테스트, 원고 2.4/5 | 실제 head run report의 deterministic flag·source hash와 일치 전에는 결과 재현성으로 승격 금지 |
| R06 | A | Atomic `.part` publish/cleanup, source hash-chain과 evidence validator의 artifact/claim/PENDING/locked guard를 반영 | registry·validator 계약, 원고 4.1/5, 최신 validation report | 원고 변경마다 validator 재실행, PENDING marker hash와 locked guard 유지 |
| R07 | A | Base+/Large 실제 head checkpoint·prediction·slice 13개씩과 S103 comparison hash-chain | 두 head completed registry, S103 commit/report/receipt와 strict renderer가 전 artifact를 재검산 | 후속 source drift 시 hash만 교체하지 않고 전체 lineage를 다시 검증 |
| R08 | C | 전체 모델 hyperparameter, seed별 결과, S1/S2 disagreement, 최종 license·environment appendix | 후속 모델·외부 결과 없음 | 각 completed run의 versioned config/report/registry와 최종 citation/license audit 필요 |

## 9. 문서 링크·상태 정합성 감사

| 대상 | 확인 결과 | P04에서의 처리 |
|---|---|---|
| 원고 ↔ P00 주장 맵 | observed/gate/related/PENDING 경계가 일치 | 유지 |
| 원고 ↔ evidence validation | 36/36, 475/475, PENDING 18개, locked guard 통과 | 새 편집마다 재검증 |
| tracker P04 | `진행 중`; 첫 편집 묶음과 S103/R00 증거 반영은 완료했지만 C 7개와 최종 감사가 남음 | 이 편집으로 완료 변경 금지 |
| H01 사전등록 ↔ tracker | planning-only, IRB/participant/pilot/effect 없음으로 일치 | protocol 시제 유지 |
| 외부평가 패킷 ↔ H01 사전등록 | **해결(2026-07-29)**: participant 1:1 병렬 C0↔C3/C3↔C5, 2주 post·post 4주 후 retention, participant/sentence/rater random intercept, ITT·결측 sensitivity·family별 Holm으로 일치 | planning-only, `final N=null`, 외부 flag 전부 false를 유지 |
| WavLM cache/head 문서 ↔ tracker/원고 | **해결(2026-07-29)**: Base+/Large cache·head completed artifact와 S103 strict comparison을 실제 hash chain으로 재확인 | S104 이후 결과로 확대하지 않음 |
| Frozen-head 학습 문서 ↔ tracker | Base+/Large completed report·registry와 S103 판정이 현재 tracker·원고에 반영됨 | adapter 이후는 별도 completed commit 전에는 결과 완료로 쓰지 않음 |
| License 매트릭스 ↔ 원고 부록 | 역할 경계는 있으나 최종 license 표 marker는 PENDING | 원문 license audit 전 marker 유지 |

위 두 선행 정합성 결함은 이번 범위에서 수정했다. H01 power planning
artifact는 schema `voice_coach.learning_effect_power_plan.v1`,
`status=assumed_sensitivity_only`이며 SHA-256은
`d63dc95bb4df4967775781def0386beb7ec959c396d847ae6b6e8c982dbce66d`다.
외부 입력은 전부 없고 assumed 시나리오 243개, pilot-informed 시나리오
0개, `final_sample_size=null`, H01 완료 주장은 `false`다.

Base+ cache builder report SHA-256은
`34da4c3ec48088b88e1e44763868cd15a21b7baaa06a2cb253338866262ef41d`,
독립 validation report SHA-256은
`7abb93395af73460e4542ecc92b0edb3dd1e96b942551ad4759972da5fdd6c4b`다.
검증은 2,811,666행·벡터와 2,289,794,304개 embedding scalar를 전수
확인했고 `errors=[]`였다. 이 해결은 원고 편집 완료나 P04 완료를 뜻하지
않는다. Frozen-head 학습 문서의 live-state 차이는 completed report 전까지
계속 결과 승격 금지 조건으로 남는다.

## 10. 외부·후속 결과의 해제 순서

1. **G04/B05**: 정확히 240건을 완료하고 `228/240` 이상,
   `status=passed`인 aggregate G04 report를 고정한다. 그 뒤 사전 승인된
   locked-test 1회, speaker bootstrap/Holm, slice report와 registry를
   생성해야 B05 결과 문장을 쓸 수 있다.
2. **S104–S108**: S101–S103의 Base+/Large 3-seed head와 paired
   comparison·renderer는 완료됐다. 이후에는 S104–S106을 건너뛰지
   않으며, 현재 실행은
   [후속 실행 계약](../WP4_POST_S103_EXECUTION_CONTRACT_KO.md)의
   S103→S104→S105→S106→S107→S108 순서를 따른다.
3. **S2/E01/C03/R04**: 독립 holdout, 전문가 판정, perturbation report,
   coaching 200건, blind MOS/CMOS 원본과 통계를 각각 완료해야 한다.
4. **H00–H04 참여자 연구 경로**: 기관 승인, consent, 모집, pre/post/retention, blind
   rating, SAP lock, pilot nuisance, final N simulation, ITT/missingness
   분석을 순서대로 완료해야 학습효과 문장을 쓸 수 있다.
5. **최종 부록**: 모든 완료 run의 config/report/registry, 실패 사례,
   license 원문과 citation audit가 모여야 hyperparameter·license marker를
   해제할 수 있다.

## 11. 첫 편집 묶음 완료 기록

새 실험 없이 가능한 첫 편집 묶음은 다음 다섯 항목으로 원고에 반영했다.

1. **방법 경계 문단 완료**: M06 frozen-head protocol과 M07 H01 draft를
   수행 결과가 아닌 사전 계획 시제로 추가했다.
2. **통계 상태 표 완료**: explicit dev에서 실제 실행한 통계와
   B05/S103/H04의 bootstrap·Holm·mixed-model 계획을 분리했다.
3. **실패·교정 문단 완료**: F05 privacy cache 교정과 F06
   run/config/atomicity 교정을
   “발견→중단→수정→재검증→한계” 형식으로 추가했다.
4. **윤리·privacy 표 완료**: R0/R1/D1/A1/C1, 가명화 한계, license 역할,
   IRB 미승인과 consent/withdrawal 계획을 한 표로 추가했다.
5. **재현성 문단 완료**: clean environment, deterministic runner,
   registry, atomic publish와 evidence validator를 연결했다.

외부평가 패킷과 최신 H01의 protocol 충돌 및 WavLM validation 문서의 stale
상태 머리말은 2026-07-29에 정리했다. 따라서 이 두 항목은 더 이상 첫 편집
묶음의 blocker가 아니었다. 편집 뒤 evidence validator는 artifact
`20/20`, claim `183/183`, PENDING `18개`와 locked-test guard를 통과했다.
이는 당시 첫 편집 묶음의 역사적 수치다. 이후 Base+/Large/S103와 R00을
반영한 현재 값은 2절의 `36/36`, `475/475`이며 PENDING 18개와
locked-test guard는 그대로 통과한다.

다음 결과 편집은 C 항목의 실제 artifact가 생긴 뒤에만 시작한다.
Placeholder를 보기 좋은 수치로 바꾸거나 synthetic test와 실행 중인 head를
실제 결과로 사용하지 않는다. 최종 P05에서는 출판본 중심 참고문헌,
license 원문, 표·그림과 PDF 렌더링을 다시 독립 감사한다.

## 12. P04 판정

현재 P04 편집 증거 충족률은 **82.5%**다. 첫 편집 묶음으로 B 14개를
원고에 반영했고, 이어 실제 Base+/Large/S103 비교와 R00 텍스트 증거를
검증해 S07/R07을 A로 옮겼다. 나머지 17.5%는 locked test, S104 이후
모델, S2/TTS/전문가 평가, IRB와 학습자 자료, 최종 부록에 의존한다.

따라서 이 문서는 P04를 완료로 바꾸는 증거가 아니다. P04는
`진행 중`이며, 외부 task 상태와 PENDING marker를 변경하지 않는다.
