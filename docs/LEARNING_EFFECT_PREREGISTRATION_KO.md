# H01 학습 효과 검정력·사전등록 패키지

최종 갱신: 2026-07-29 KST  
상태: **계산·사전등록 초안 구현 완료, H01 자체는 미완료**

이 문서는 신규 학습자 모집·중재·RCT·pre/post/retention을 선택하는
참여자 연구 경로 전용이다. 현재 개인 연구의 성인 전문가·원어민·청취자
평가 gate를 정의하지 않는다.

## 1. 현재 증거와 금지된 해석

현재 저장소에는 다음 자료가 없다.

- IRB 또는 동등한 기관 승인
- 모집·동의된 외부 참여자
- 학습자 pre/post/retention 녹음
- empirical pilot SD, 반복측정 상관, cluster ICC, 탈락률
- 관측된 C0↔C3 또는 C3↔C5 교육 효과

따라서 이 문서와
`configs/experiments/learning_effect_power_v1.yaml`은 실행 가능한
**planning/preregistration draft**이지 등록 완료 protocol이나 H01 완료
증거가 아니다. config의 effect, SD, correlation, ICC, attrition grid는
민감도 분석을 위한 가정이며 결과·예측·권장 표본수가 아니다. 조건당
15–20명은 feasibility pilot의 기술적 시작 범위일 뿐 efficacy power의
근거가 아니다.

### 기존 문서 감사에서 해소한 모호성

| 기존 표현 | 이번 고정 |
|---|---|
| “무작위 배정 또는 crossover” | 학습 carryover 때문에 병렬 무작위 배정 |
| “4주 뒤 retention” | 2주 post 평가 후 4주 |
| 조건당 15–20명 | feasibility 기술 범위, powered 표본수 아님 |
| C0/C3/C5 상세 미정 | 조건 간 최소 incremental component를 아래와 같이 고정 |
| participant·sentence random intercept | blind 평가자 변이를 위해 rater intercept 추가 |
| pilot 분산으로 표본수 확정 | pilot nuisance estimate + 독립 target effect + simulation + 수동 승인 |
| 두 단계 go/no-go | 안전·실행가능성 gate만 허용, unblinded 효과 기반 조기 중단 금지 |

## 2. 연구 질문과 조건

기존 계획의 두 인과 질문을 별도 병렬 비교로 고정한다.

1. `C3 − C0`: 구조화된 근거 제한 진단·코칭의 추가 학습 효과
2. `C5 − C3`: 다중 참조음과 코칭 후 재시도 이중 루프의 추가 학습 효과

현재 arm의 최소 운영 정의는 다음과 같다.

| 조건 | 운영 정의 |
|---|---|
| C0 | 시간·연습 문장·노출량을 맞춘 active control, 구조화 진단 코칭 없음 |
| C3 | C0 구성에 근거 제한 구조화 진단 코칭 추가 |
| C5 | C3 구성에 다중 참조음과 coached reattempt loop 추가 |

이는 기존 문서에서 완전하게 정의되지 않았던 C0/C3/C5를 검정 가능한
최소 차이로 명시한 것이다. 참여자용 화면, 코칭 빈도, 문장 수, 노출 시간,
허용 문의와 오염 방지 절차는 모집 전에 별도 intervention manual과
TIDieR 표로 동결해야 한다.

학습과 retention에는 carryover가 있으므로 crossover는 사용하지 않는다.
각 단계는 participant 단위 1:1 무작위 배정 병렬 설계다. L1과 baseline
comprehensibility band로 층화하며, 무작위 seed·allocation concealment
절차는 IRB 제출본에서 고정한다.

2단계 시작 여부를 1단계의 공개되지 않은 효과 추정치로 결정하지 않는다.
2단계는 승인 범위, 안전·개인정보 pause 해소, C3/C5 delivery fidelity,
무작위화·blind rating pipeline 준비 여부로만 시작한다. 효과 기반
adaptive gate를 사용하려면 별도 protocol amendment와 alpha/power 재설계가
필요하다.

## 3. 시점과 outcome

| 시점 | 정의 |
|---|---|
| pre | 무작위 intervention 전 |
| post | 2주 intervention 종료 시점 |
| retention | post 4주 후 |

Primary outcome은 고정 transfer item에 대한 두 명 이상 독립 전문가의
blind comprehensibility 1–5 평정이다. primary estimand는 각 단계의
`pre→post` 변화량 차이다.

Key secondary는 다음과 같다.

- `pre→retention` blind comprehensibility 변화량 차이
- post/retention blind phone accuracy
- post/retention blind fluency
- 만족도와 adherence/exposure는 exploratory

자동 모델 점수는 교육 효과 primary outcome이 아니다. 자동 점수만
개선되고 blind comprehensibility가 개선되지 않으면 교육 효과 성공으로
기록하지 않는다.

## 4. Blind 평가와 데이터 잠금

- 평가자는 condition, timepoint, participant identity를 알 수 없다.
- 파일명과 item 순서를 가리고 reviewer별로 독립 난수화한다.
- 최소 두 평가자가 서로의 점수를 보지 않고 평정한다.
- ICC(2,1), QWK, MAE와 점수 분포를 기록한다.
- confirmatory 분석 전 평가 신뢰도 기준은 0.70이다. 미달 시 결과를
  숨기거나 adjudication만으로 대체하지 않고 재교육·재평가 절차를
  protocol deviation과 함께 보고한다.
- 분석자는 QC, 제외 사유, missingness rule과 SAP가 잠길 때까지 arm
  label을 가린다.
- 참여자는 intervention 차이를 알 수 있으므로 participant blinding을
  주장하지 않는다.

## 5. Estimand와 분석

Primary population은 무작위 배정된 모든 참여자의 ITT다. 조건 불이행,
중단, protocol deviation, follow-up 결측만으로 post-randomization
참여자를 ITT에서 제거하지 않는다.

사전 고정 model은 다음 fixed/random 구조의 mixed model이다.

```text
score ~ condition * time + L1 + baseline
random intercepts: participant + sentence + rater
```

보고할 contrast는 다음 네 개다.

- C3−C0의 pre→post 변화량 차이
- C5−C3의 pre→post 변화량 차이
- C3−C0의 pre→retention 변화량 차이
- C5−C3의 pre→retention 변화량 차이

각각 raw 1–5 scale 차이, standardized difference, 95% CI를 보고한다.
실제 자료가 생긴 뒤 ordinal model과 linear mixed model의 적합성을
blind residual/QC 단계에서 비교하되, primary model 변경은 SAP amendment로
남긴다.

## 6. Missingness·ITT·per-protocol

Primary estimator는 모든 관측 ITT 자료를 사용하는 likelihood-based mixed
model이다. 결측 자체를 0 또는 “변화 없음”으로 바꾸지 않고 다음을
sensitivity로 사전 지정한다.

- MAR 가정의 multilevel multiple imputation
- delta-adjusted pattern-mixture MNAR 분석
- complete-case 분석

LOCF와 post-randomization exclusion은 금지한다. arm·시점별 결측 수,
비율, 사유, 철회, adverse feedback과 ceiling/floor를 모두 보고한다.
배정 노출의 80% 이상을 받은 집단의 per-protocol 분석은 sensitivity일
뿐 primary claim에 사용하지 않는다. 철회 후 기존 자료의 보존·삭제는
승인된 동의서 선택을 따른다.

## 7. Multiplicity

전체 양측 familywise alpha는 0.05이고 Holm 보정을 사용한다.

- primary family: 두 `pre→post` contrast
- key retention family: 두 `pre→retention` contrast
- secondary family: phone accuracy/fluency × post/retention

검정력 근사에서는 primary 두 contrast를 모두 보호하는 보수적
`alpha=0.05/2=0.025`를 각 contrast에 사용한다. 이는 Holm의 실제
step-down 순서를 미리 안다고 가정하지 않는 planning bound다.

## 8. Power 근사

`scripts/plan_learning_effect_power.py`는 primary continuous rating의
participant-level 평균에 대해 두 집단 변화량 차이의 normal approximation을
사용한다.

```text
d = assumed raw mean difference / participant-level outcome SD
n_complete_per_arm
  = ceil(4 * (1-r) * (z_(1-alpha/2) + z_power)^2 / d^2)
```

여기서 `r`은 pre와 해당 follow-up 간 상관이다. 이는 full mixed-model
simulation이 아니며 participant, sentence, rater random effect를 완전히
재현하지 않는다.

실제 delivery가 classroom/cohort 단위 cluster randomization으로 바뀌는
경우에만 다음 unequal-cluster design effect를 적용한다.

```text
DE = 1 + (((1 + CV^2) * mean_cluster_size) - 1) * cluster_ICC
```

participant 단위 무작위 배정이 유지되면 individual repeated-change 결과가
주 planning 근사다. cluster 결과는 protocol 변경에 대비한 sensitivity다.
최종 confirmatory 표본수는 pilot nuisance estimate로 full
mixed/cluster simulation을 수행한 뒤 통계 담당자가 잠가야 한다.

## 9. 가정 grid와 empirical pilot의 분리

현재 config의 grid는 전부 `assumed_sensitivity_without_empirical_pilot`로
표시된다.

| 변수 | 현재 sensitivity 값 | 증거 지위 |
|---|---|---|
| raw comprehensibility 차이 | 0.15, 0.30, 0.45점 | 가정 |
| participant-level SD | 0.60, 0.90, 1.20 | 가정 |
| pre/follow-up correlation | 0.30, 0.50, 0.70 | 가정 |
| delivery cluster ICC | 0.00, 0.05, 0.10 | 가정 |
| attrition | 0.10, 0.20, 0.30 | 가정 |
| mean cluster size/CV | 10/0.25 | 가정 |

계산기는 이 조합 243개를 모두 출력하며 하나를 “추천”하지 않는다.
`final_sample_size`는 항상 `null`, `final_sample_size_locked`는 항상
`false`다.

향후 `--pilot-estimates`에는 다음 aggregate nuisance parameter만 허용한다.

- participant 수
- participant-level outcome SD
- pre/follow-up correlation
- delivery cluster ICC와 cluster size/CV
- attrition
- blind rater ICC(2,1)
- aggregate source report SHA-256

관측 treatment effect는 pilot power input으로 허용하지 않는다. pilot
effect를 그대로 confirmatory target으로 쓰면 불안정성과 winner's curse를
키울 수 있으므로, 최소 관련 차이는 외부 교육적 판단과 사전 sensitivity
grid에서 선택한다. pilot 파일은 IRB·동의·aggregate-only·식별자 없음
flag와 config의 외부 상태가 모두 일치해야 받아들인다.

## 10. 제외와 중단

무작위 배정 전 제외는 동의 미획득, eligibility 실패, 배정 전 baseline
recording hard-QC 실패, 중복 등록으로 제한한다.

효과나 futility에 대한 interim test와 조기 중단은 없다. 다음 상황에서만
pause/stop한다.

- IRB·기관의 정지 명령
- 동의 또는 개인정보 침해
- 예상하지 못한 중대한 intervention 관련 위해
- 무작위화·데이터 무결성 실패
- 등록된 조건을 그대로 제공할 수 없음

재개에는 문서화된 IRB와 연구책임자 승인이 필요하다. 안전·개인정보 중단을
효과 없음과 혼동하지 않는다.

## 11. 실행

현재 가정 grid만 계산하는 명령은 다음과 같다.

```bash
.venv-scorer/bin/python scripts/plan_learning_effect_power.py \
  --config configs/experiments/learning_effect_power_v1.yaml \
  --output artifacts/planning/learning_effect_power_sensitivity_v1.json
```

이 명령은 참여자 자료를 읽지 않으며 planning JSON만 원자적으로 쓴다.
실제 pilot 뒤에는 aggregate nuisance JSON과 config의 외부 상태를 함께
version-up하고 `--pilot-estimates`로 전달한다. raw/restricted/.env 경로는
항상 거부한다.

최종 표본수를 잠그기 전에 다음이 모두 필요하다.

1. IRB/기관 승인과 protocol version
2. 실제 feasibility pilot과 blind rating 신뢰도
3. SD/correlation/ICC/attrition의 aggregate 추정 및 불확실성
4. 최소 관련 효과의 독립적 교육학적 근거
5. full mixed-model 또는 cluster simulation
6. 통계 담당자·연구책임자 서명
7. confirmatory 모집 전 공개 사전등록

## 12. 방법론 근거

이 패키지는 임상 규제 적용을 주장하지 않고, 무작위 학습자 연구의 투명한
protocol 작성 기준으로 다음 원칙을 차용한다.

- [SPIRIT 2025](https://www.bmj.com/content/389/bmj-2024-081477):
  outcome, 표본수 가정, 배정, blinding, 분석·공개 계획을 protocol에 명시
- [CONSORT pilot/feasibility extension](https://www.bmj.com/content/355/bmj.i5239):
  feasibility pilot을 확증적 efficacy trial처럼 해석하지 않음
- [Eldridge, Ashby, Kerry (2006)](https://doi.org/10.1093/ije/dyl129):
  unequal cluster size의 CV를 포함한 design-effect sensitivity
- [ICH E9(R1) addendum](https://www.ema.europa.eu/en/documents/scientific-guideline/ich-e9-r1-addendum-estimands-and-sensitivity-analysis-clinical-trials-guideline-statistical-principles-clinical-trials-step-5_en.pdf):
  estimand, intercurrent event, missing-data sensitivity를 분석 전에 정렬

실제 등록 시점에는 당시 최신 기관 서식과 연구책임자·통계 담당자의 검토를
우선한다.
