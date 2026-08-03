# S2 S202 별도 표현 pairwise·ordinal 감사기 데이터·모델 카드

최초 작성: 2026-07-29 KST  
상태: **정적 실행 준비 계약만 구현 / 실제 데이터·모델·다운로드·학습·지표 0**

## 1. 목적과 주장 한계

S202는 S1의 점수를 모사하는 보조 head가 아니라, S1과 다른 음성 표현과
별도 S2 전문가 감독으로 `approve` / `disagreement` / `uncertain`을 만드는
독립 감사 후보 단계다. S201의 내용 일치 hard gate를 통과한 표본만 다음
단계로 routing할 수 있지만, `content_gate_pass`는 S202의 학습 feature가
아니다.

현재 구현은 static config, read-only predecessor preflight, 데이터·모델
카드와 논문 amendment template뿐이다. Production trainer, S2 행 단위
자료, HuBERT checkpoint, feature, label, prediction, checkpoint, metric,
result commit은 없다. 따라서 허용되는 주장은 “S202 정적 fail-closed
준비 계약을 검증했다”뿐이며 S202 완료, 독립성의 경험적 입증, 모델 성능
또는 WP5 배점의 근거가 아니다.

## 2. 실행 선행 순서

```text
S108 final S1 research_candidate_passed commit + completed registry
  -> S200 independence readiness lock
  -> S201 completed content_hard_gate_passed commit + completed registry
  -> S202 preregistration
  -> S2 expert label/split attestation
  -> exact local-only separate-representation bundle
  -> 별도 production trainer 구현·감사
```

세 선행자는 public static contract와 artifact를 포함해 deep validate한다.
S108, S200, S201 순서를 바꿀 수 없다. 어느 단계든 없거나 변조되면
preregistration, data/model path와 output path를 해석하기 전에
fail-closed한다.

현재 S108 final commit이 없으므로 full preflight는 첫 선행자에서
종료해야 한다.

## 3. S1/S201과의 독립성

S202 raw input allowlist는 다음 네 필드뿐이다.

- `item_id_sha256`
- `speaker_id_sha256`
- `audio_waveform`
- routing-only `content_gate_pass`

모델 feature allowlist는 독립 SSL hidden state, 해당 attention mask,
S2 자체 불확실성으로 제한한다. 다음은 feature, target, label, loss,
selection 또는 threshold 입력으로 사용할 수 없다.

- S1 score/logit/probability/confidence/threshold/prediction
- S1 WavLM embedding과 explicit feature
- S1 label과 AI Hub target–realized silver phone label
- realized phone, alignment operation, error tag/count
- oracle/predicted phone·word boundary와 alignment confidence
- phone label을 합성한 word proxy
- Whisper transcript, WER, CER, content mismatch score, ASR score, timestamp

S201은 S202 진입 여부만 결정한다. Content gate를 통과했다는 사실을
연속 점수로 변환하거나 Whisper ASR score를 S202의 feature/target으로
사용하지 않는다. S202는 `alignment_free_utterance`이며 S1의 oracle 또는
predicted alignment를 사용하지 않는다.

## 4. 표현 모델의 현재 증거와 blocker

저장소가 직접 뒷받침하는 범위는 다음뿐이다.

- HuBERT가 masked hidden-unit prediction 기반의 별도 SSL 표현 후보라는
  family 수준 근거
- fairseq HuBERT source revision
  `3d262bb25690e4eb2e7d3c1309b1e9c406ca4b99`
- 해당 source/pretrained-model 문서 범위의 MIT license와 license hash

정확한 HuBERT checkpoint, provider/model ID, weight revision, preprocessor,
sampling/normalization 세부, local bundle 경로·manifest와 checksum은
저장소 근거로 확정되지 않았다. 따라서 모두 `TBD_PREREGISTRATION`이며
자동 download, network fallback과 실행을 금지한다.

다른 non-WavLM SSL을 선택하려면 단순 모델명 교체가 아니라 별도 표현을
선택한 이유, source/revision/license, preprocessor, weight manifest와
local-only attestation을 함께 사전 고정해야 한다. 이 증거가 없으면
HuBERT도 다른 후보도 실행할 수 없다.

## 5. 전문가 pairwise·ordinal label

허용 label family는 별도 S2 전문가의 `pairwise_preference`와
`ordinal_audit`뿐이다. 하지만 다음 항목은 정본 문서에 값 근거가 없어
모두 `TBD_PREREGISTRATION`이다.

| 구분 | 미고정 항목 |
|---|---|
| pairwise | 비교 단위, 선호 enum, tie/abstain 규칙, 방향 randomization, 쌍 표집 |
| ordinal | 척도 최소·최대, category 의미, missing/abstain 규칙 |
| 평가자 | 최소 평가자 수, 자격, training/calibration, blind protocol |
| adjudication | disagreement trigger, adjudicator 수·자격, 원평정 보존 |
| 품질 | agreement metric, 제외 규칙, rater drift gate |

평가자 ID는 연구별 namespace의 `reviewer_id_sha256`만 D1으로 이동할 수
있다. 자유서술과 대응표는 각각 R0/R1 경계를 유지한다. 위 schema와
governance를 사람 승인 preregistration과 aggregate attestation으로
고정하기 전에는 label row를 읽지 않는다.

## 6. 분할, seed, 선택, holdout

예정 split은 `s2_auditor_train`, `s2_auditor_validation`,
`s2_expert_holdout`이다. 세 split은 item·speaker·prompt가 서로 겹치지
않고 S1의 train/dev/stack/calibration/locked-test와도 재사용하지 않는다.
각 차원의 overlap count는 0이어야 하지만 현재 원장 기반 attestation은
없으므로 상태는 `TBD_PREREGISTRATION`이다.

문서로 고정된 seed는 42, 43, 44이며 seed별 checkpoint와 prediction을
분리 보존한다. 그러나 train 내부 분할, inner-selection metric/direction,
epoch·early stopping, dev 평가 시점과 ensemble 선택 정책은 근거가 없어
모두 `TBD_PREREGISTRATION`이다. 결과를 본 뒤 seed나 정책을 고를 수 없다.

`s2_expert_holdout`은 S203이 모든 S1/S2 모델·정책을 동결한 뒤 단 한 번
여는 최종 평가 자료다. S202 trainer와 preflight는 holdout을 읽지 않는다.

## 7. 목적함수, 지표, CI와 gate

목표 family가 pairwise preference와 ordinal audit라는 것만 고정됐다.
Pairwise loss, ordinal loss, 두 loss의 weighting, intrinsic uncertainty
정의와 3상태 decision rule은 근거가 없어 `TBD_PREREGISTRATION`이다.

S202 development primary/secondary metric, CI 방법·수준·resampling unit·
반복 수, 다중비교 보정, dev gate와 S203로 넘길 promotion gate도 모두
`TBD_PREREGISTRATION`이다. Phase 3의 최종 S1+S2 gate는 S203/E01 수준
목표이며 이를 임의로 S202 development gate로 복사하지 않는다.

## 8. 개인정보·라이선스·반출

원음과 전문가 자유서술은 R0, locator·평가자 대응표는 R1, 가명
feature·label·prediction은 D1, 집계 report만 A1이다. 행 단위 자료,
가명 prediction, 원 transcript와 원평정의 공개 반출을 금지한다.

실제 checkpoint 획득 전에는 정확한 weight와 code license를 다시
감사한다. Checksum은 재현 식별자이지 자료나 model weight의 재배포
허가가 아니다. S2 전문가 자료는 별도 동의·기관 정책·철회·보존·폐기
계획과 제한 재배포 계약이 필요하다.

## 9. 완료에 필요한 미래 산출물

S202 완료를 주장하려면 별도 production package가 최소한 다음을
immutable generation/registry/commit으로 제공해야 한다.

1. 사람 승인된 exact S202 preregistration
2. pairwise·ordinal label schema와 rater/adjudication attestation
3. item·speaker·prompt disjoint split attestation
4. exact model/revision/preprocessor/local bundle attestation
5. seed 42/43/44 checkpoint와 seed별 dev prediction
6. 사전 고정 metric·CI·gate의 aggregate dev report
7. lineage/no-S1/no-oracle/no-silver/no-Whisper-score 독립 감사
8. completed registry, immutable commit, 독립 validation receipt

현재 위 실제 산출물은 0개다.

## 10. 다음 행동

1. S108 final, S200 lock, S201 completed hard-gate commit을 순서대로
   실제 immutable evidence로 완료한다.
2. 결과·data/model path를 열기 전에 미해결 모델·label·objective·metric·
   CI·gate를 사람 승인 preregistration으로 동결한다.
3. S2 expert 모집·동의·평가자·adjudication·split 원장을 aggregate
   attestation으로 고정한다.
4. 승인된 별도 획득 절차로 exact local-only model bundle을 준비한다.
5. 이 정적 readiness package와 분리해 production trainer, registry,
   commit과 deep validator를 구현·감사한다.
6. 실제 3-seed dev 결과와 독립 재계산 뒤에만 논문 amendment 값을 채운다.
