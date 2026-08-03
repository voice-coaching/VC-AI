# 전문가·원어민·학습자 외부평가 실행 패킷

최초 작성: 2026-07-29 KST  
상태: 프로토콜 준비 완료, 모집·승인·수집은 외부 의존

## 1. 범위와 완료 판정

이 문서는 자동화만으로 끝낼 수 없는 D07/D08/C03/R04/H00–H04의
사전 고정 프로토콜이다. 실제 전문가·원어민·학습자의 판정이나 기관
승인이 없는 상태를 “통과”로 기록하지 않는다. 모든 사람 대상 자료는
승인된 동의서, 철회 절차, 접근권한, 보존·폐기 계획이 확정된 뒤 수집한다.

| Task | 준비 산출물 | 실제 완료 증거 | 현재 상태 |
|---|---|---|---|
| D07 | 독립 gold 표집·rubric·schema | 2인 독립 평정 원본, 합의도, adjudication | 외부 의존 |
| D08 | 20문장 녹음·QC·동의 protocol | 동의된 다화자 WAV와 metadata | 외부 의존 |
| C03 | coaching 정확도·안전 rubric | 200건 blind 전문가 판정 | 외부 의존 |
| R04 | 기준음 blind 평가 설계 | 무작위화 원본, MOS/CMOS와 통계 | 외부 의존 |
| H00 | IRB/PIPA 제출 체크리스트 | 기관 승인번호·승인본 | 외부 의존 |
| H01 | C0↔C3/C3↔C5 planning·power draft | `final_sample_size=null`인 assumed sensitivity artifact | 계획 구현 완료, 외부 완료 불가 |
| H02–H03 | 2주 훈련·4주 유지 protocol | 비식별 pre/post/retention 원자료 | 외부 의존 |
| H04 | ITT mixed-model·missingness·Holm SAP | effect size·CI·sensitivity aggregate report | H03 후 대기 |

## 2. D07 독립 전문가 gold

### 표집과 역할 분리

- 한국어 음성학·발음교육 또는 강제정렬 검수 경험을 확인한 전문가 2인이
  모든 항목을 서로의 판정과 모델 출력 없이 독립 평가한다.
- 제3 전문가는 불일치 adjudication만 수행하며 최초 두 판정을 덮어쓰지
  않는다.
- 목표 표본은 최대 2,000발화다. 800건은 기존 `calibration` split에서
  threshold·rubric 안정화용으로, 나머지 최대 1,200건은
  `locked_test`에서 최종 독립평가용으로 사전 고정한다.
- L1, 성별, 숙련도, 발화 길이, 목표 음소, 기존 오류 유무를 층화한다.
  작은 집단은 최소 수를 우선 배정하고 모집단 가중치와 비가중 slice
  결과를 함께 보고한다.
- calibration 평정으로 rubric을 수정할 수 있지만 locked-test 평정이
  시작된 뒤에는 label 정의와 primary metric을 바꾸지 않는다.
- 개발자는 locked-test 평정·모델 결과를 최종 G0 통과 전과 단일 최종
  평가 전에는 열지 않는다.

### 발화·음소 평정 schema

| 필드 | 형식 | 규칙 |
|---|---|---|
| `item_id_sha256` | 64 hex | 연구별 namespace 가명 ID |
| `reviewer_id_sha256` | 64 hex | 대응표와 분리 |
| `target_phone_index` | int | 원문 stem 대신 항목 내부 index |
| `phone_realization` | enum | correct/substitution/deletion/uncertain |
| `error_type` | 17종 ontology+other/uncertain | 모델 예측 없이 선택 |
| `boundary_acceptable` | yes/no/uncertain | 50 ms 기준과 청지각을 함께 기록 |
| `pronunciation_score` | 1–5 | 고정 anchor 사용 |
| `comprehensibility_score` | 1–5 | 이해 노력 anchor 사용 |
| `fluency_score` | 1–5 | 속도·휴지·끊김 anchor 사용 |
| `confidence` | 1–4 | 판정 확신, 모델 confidence와 분리 |
| `adjudication_reason` | 코드+짧은 note | 제3 전문가에게만 허용 |

자유서술 note에는 이름·연락처·출신기관 등 식별정보를 쓰지 않는다.
오디오 순서와 시스템 조건은 reviewer마다 독립 난수화한다.

### 합의도와 gate

- phone 이진/유형: macro F1, Cohen's kappa와 prevalence를 함께 보고한다.
- 1–5 점수: QWK, ICC(2,1), MAE와 score 분포를 보고한다.
- ontology: macro agreement와 Krippendorff alpha를 보고한다.
- calibration 800건에서 ICC/QWK 0.70 또는 taxonomy 합의 0.65에
  미달하면 rubric 재교육 후 별도 재표집한다. 낮은 합의를 adjudication
  결과만으로 숨기지 않는다.
- locked-test 성능은 두 원평정과 adjudicated gold에 대해 sensitivity
  analysis를 수행한다.

## 3. D08 원어민 다화자 기준음

### 문장·화자

- 교육 목표와 38개 목표 음소 coverage를 기준으로 20문장을 사전 고정한다.
- 문장별 최소 4명의 성인 한국어 원어민을 목표로 하고, 특정 성별·지역
  억양만 “표준”으로 규정하지 않는다.
- 화자는 자연 속도 2 take와 명료한 교육용 속도 1 take를 녹음한다.
- 음성 사용 범위, 모델 입력 여부, 제3자 제공 여부, 철회 가능 시점이
  분리된 동의 항목이어야 한다.

### 녹음과 QC

- 원본은 48 kHz/24-bit mono PCM WAV, 동일 마이크·공간·약 15–20 cm
  거리로 수집하고 장비·세션·take metadata를 남긴다.
- clipping ratio 0.1% 미만, DC offset·decoder 오류 0, 문장 내용 일치,
  과도한 잡음·잔향·중단 없음이 hard gate다.
- 자동 ASR은 내용 일치 후보 선별에만 사용하고 최종 내용·발음 QC는
  두 명 중 한 명 이상이 청취 확인한다.
- 공개 corpus 음성을 사람 녹음으로 위장하지 않으며, 각 reference의
  human/TTS/source/license를 명시한다.
- 원음은 R0, locator manifest는 R1, 비식별 embedding과 집계 QC는
  D1/A1 경계를 따른다.

## 4. C03 근거 제한 coaching 감사

- S1의 최종 calibration이 고정된 뒤 200건을 L1·오류 유형·confidence
  구간으로 층화한다.
- 전문가는 모델 점수 없이 오디오, 목표문장, 생성된 top-1 조언만 본다.
- primary:
  - 오류 위치 정확도
  - 오류 유형 정확도
  - 조언의 실행 가능성
  - 근거 없는 주장 여부
- 위치·유형 정확도 각각 90% 이상, unsupported claim rate 2% 미만을
  gate로 한다.
- confidence gate 때문에 조언을 내지 않은 항목도 따로 표집해 과도한
  referral을 측정한다.
- 위험하거나 차별적인 표현, 화자 정체성 추론, 의학적 판단은 한 건이라도
  발견하면 원문과 규칙을 수정하고 재감사한다.

## 5. R04 다중 기준음 blind 평가

- 20문장 각각에 대해 human reference와 hard gate를 통과한 TTS 후보를
  포함한다. 시스템 이름, 파일명, 합성 여부를 숨기고 문장 내 순서를
  reviewer별 난수화한다.
- 최소 평가 단위는 `listener × sentence × candidate`이며 동일 listener가
  과도하게 많은 유사 후보를 연속 청취하지 않게 block을 나눈다.
- MOS: 자연스러움, 명료도, 목표 음소 정확도, 교육용 적합성 1–5.
- CMOS: 동일 문장의 쌍 비교에서 -3–+3.
- hard-gate 실패 후보는 MOS 평균으로 구제하지 않는다.
- listener와 sentence를 random effect로 둔 ordinal/mixed model을
  primary로 하고 bootstrap CI를 보조로 사용한다.
- 표본 수는 소규모 pilot의 분산과 intra-listener correlation을 얻은 뒤
  power 0.80, 양측 alpha 0.05 기준으로 고정한다. 분산을 임의로
  가정해 완료 수를 만들지 않는다.

## 6. H00–H04 학습자 파일럿과 유지 평가

### 현재 증거 지위와 설계

이 절의 정본은
[`LEARNING_EFFECT_PREREGISTRATION_KO.md`](LEARNING_EFFECT_PREREGISTRATION_KO.md)와
`configs/experiments/learning_effect_power_v1.yaml`이다. 둘은 실행 가능한
planning/preregistration draft이지 등록 완료 protocol이나 H01 외부 완료
증거가 아니다. 현재 IRB/기관 승인, 모집·동의된 참여자, 학습자
pre/post/retention 녹음, empirical pilot nuisance estimate와 관측 교육
효과는 모두 없다.

인과 질문은 다음 두 participant 단위 1:1 무작위 배정 병렬 비교로
분리한다.

1. `C3 − C0`: 시간·연습량을 맞춘 active control 대비 근거 제한 구조화
   진단·코칭의 추가 효과
2. `C5 − C3`: 구조화 코칭 대비 다중 참조음과 coached reattempt loop의
   추가 효과

학습과 retention carryover 때문에 crossover는 사용하지 않는다. 2단계
시작 여부도 1단계의 unblinded 효과 추정치로 결정하지 않고 승인 범위,
안전·개인정보 pause 해소, delivery fidelity와 blind rating pipeline
준비만으로 판단한다.

### 승인 전 제출 항목

- 두 병렬 비교의 C0/C3/C5 intervention manual, participant 1:1 무작위
  배정, L1·baseline comprehensibility 층화와 allocation concealment
- 녹음되는 개인정보와 민감정보, 목적, 보존기간, 접근자, 폐기 방법
- 참여 중단·철회와 철회 시 기존 자료 처리
- AI가 부정확한 발음 피드백을 줄 위험과 인간 문의 경로
- 보상, 모집문, 미성년자 제외 또는 별도 보호절차
- 보안 사고 대응, 가명키 대응표 분리, 외부 서비스·TTS 전송 범위

승인번호가 생기기 전에는 실제 참여자를 모집하거나 녹음하지 않는다.

### 2주 feasibility pilot

- 조건당 15–20명은 feasibility의 기술적 시작 범위이며 powered efficacy
  표본수나 권장 모집 수가 아니다.
- 최종 confirmatory 표본수는 현재 `null`이고 자동 lock하지 않는다.
- pre에서 고정 문장과 미학습 transfer 문장을 모두 녹음한다.
- 2주 동안 조건별로 동일한 노출 시간과 연습 문장 수를 기록한다.
- post는 2주 intervention 종료 시점, retention은 post 4주 후 실시한다.
- 두 명 이상 독립 평가자는 condition·timepoint·participant identity를
  모르는 상태로 comprehensibility를 primary, phone accuracy·fluency를
  secondary로 평정한다.
- 자동 모델 점수는 교육 효과의 primary outcome이 아니다.

### 분석

- primary population은 무작위 배정된 모든 참여자의 ITT다. 조건 불이행,
  중단, protocol deviation과 follow-up 결측만으로 ITT에서 제거하지
  않는다.
- primary model: participant, sentence와 rater를 random intercept로 둔
  `score ~ condition * time + L1 + baseline`.
- C3−C0와 C5−C3 각각의 pre→post, pre→retention contrast에 대해 raw
  1–5 scale 차이, standardized difference와 95% CI를 보고한다.
- likelihood-based mixed model의 관측 ITT를 primary로 하고, MAR
  multilevel multiple imputation, delta-adjusted pattern-mixture MNAR,
  complete-case를 missingness sensitivity로 사전 지정한다. LOCF와
  post-randomization exclusion은 금지한다.
- 배정 노출 80% 이상 per-protocol은 sensitivity일 뿐 primary claim이
  아니다.
- 양측 familywise alpha 0.05에서 primary, retention, secondary family를
  나누고 family 안에서 Holm 보정한다.
- 결측·탈락 사유, 철회, condition별 adverse feedback, ceiling/floor를
  숨기지 않는다.
- pilot의 목적은 feasibility와 분산 추정이다. 확증 RCT의 교육 효과
  결론으로 과대해석하지 않는다.

### Planning-only power 상태

현재 planning artifact는
`artifacts/planning/learning_effect_power_sensitivity_v1.json`이며 SHA-256은
`d63dc95bb4df4967775781def0386beb7ec959c396d847ae6b6e8c982dbce66d`다.

- schema/status:
  `voice_coach.learning_effect_power_plan.v1` /
  `assumed_sensitivity_only`
- assumed sensitivity scenario: 243개
- pilot-informed scenario: 0개
- `final_sample_size=null`
- `final_sample_size_locked=false`
- `h01_completion_claim_allowed=false`
- IRB, 모집, 녹음, pilot 입력, confirmatory effect flag: 모두 false

최종 표본수는 IRB 범위 안의 실제 feasibility pilot에서 얻은 aggregate
SD·반복측정 상관·ICC·탈락률, 관측 효과와 독립적인 최소 관련 차이, full
mixed/cluster simulation, 통계 담당자·연구책임자 승인과 confirmatory
모집 전 공개 사전등록이 모두 갖춰진 뒤에만 수동으로 잠근다.

## 7. 인수인계 체크리스트

실제 외부 작업을 시작하는 담당자는 다음 증거를 저장한다.

1. 승인된 protocol/version/hash와 변경 이력
2. reviewer/participant 모집·자격·동의 원본의 접근제어 위치
3. 비식별 item manifest와 무작위화 seed/hash
4. 원평정, adjudication, 제외 사유와 QC report
5. 사전 고정된 분석 script와 실행환경 lock
6. aggregate 결과, CI, 실패 gate, adverse event
7. 철회·폐기 요청의 처리 audit trail

저장소에는 이 중 공개 가능한 schema, checksum, 집계 결과만 기록한다.
