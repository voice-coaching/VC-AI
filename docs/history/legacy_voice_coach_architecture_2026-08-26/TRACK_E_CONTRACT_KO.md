# Track E 계약 동결 기록 (E1)

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

- 문서 상태: `frozen_contract_record`
- 동결: 2026-08-06 KST
- artifact: [`configs/pronunciation_feedback/track_e_contract_v1.json`](../../../configs/pronunciation_feedback/track_e_contract_v1.json)
- schema version: `voice_coach.track_e_contract.v1`
- canonical hash: `301155130ccfec6473289e41fecd5344478967d67452d26b4c319b4e00720154`
- 검증 명령: `python scripts/validate_track_e_contract.py`
- claim scope: `engineering_provisional` (연구 완성 배점 0, 검증된 성능 주장 불가)

계약은 **값이 아니라 규칙**을 먼저 고정한다. threshold 값은 E6에서
calibration split으로 산출하며, 그 산출 방법은 이 문서와 artifact로 이미 닫혀
있다. 결과를 본 뒤 규칙·metric 정의·gate 임계를 바꾸면 hash가 바뀌고, 이전
hash를 인용한 run은 전부 무효다.

## 1. split 역할표

| split | 역할 | threshold 산출 | 읽기 |
|---|---|---|---|
| train | 모델 파라미터 학습 | 금지 | 허용 |
| dev | 모델·variant 선택 | 금지 | 허용 |
| calibration | threshold 산출 전용 | **허용(유일)** | 허용 |
| locked | Track E 금지 | 금지 | **금지** |
| expert_holdout | Track E 금지 | 금지 | **금지** |

validator는 `threshold_derivation_allowed`가 참인 split이 `calibration`
하나뿐임을 강제하고, 금지 split이 읽기 가능으로 바뀌면 fail-closed한다.

## 2. metric 정의

| metric | 단위 | 집계 |
|---|---|---|
| `phone_macro_f1` | phone | class macro → seed 평균, seed 표본표준편차 |
| `utterance_icc` | utterance | ICC(2,1) seed별 1개 → seed 평균 |
| `content_wer` | word | corpus-level 총 edit / 총 reference token |
| `content_cer` | character | NFC 음절 문자 기준 corpus-level |
| `ece` | probability | equal-width 10 bin, 표본가중 절대격차 평균 |
| `issued_precision` | 발행된 코칭 항목 | 발행 항목 micro |

## 3. threshold 산출 규칙

산출 순서는 고정이다: `min_content_score` → `min_alignment_confidence` →
`min_s1_confidence` → `min_s2_confidence` → `min_issue_confidence` →
`min_issue_severity`. 반올림은 소수 4자리 half-up, 구간은 [0, 1],
동점은 **더 보수적인(높은) 임계**를 택한다.

| threshold | 규칙 | 모집단 |
|---|---|---|
| `min_content_score` | 5% 분위 | 참조 텍스트가 프롬프트와 같은 calibration 발화 |
| `min_alignment_confidence` | 10% 분위 | content 규칙을 통과한 발화 |
| `min_s1_confidence` | `issued_precision ≥ 0.90`을 만족하는 최소값 | alignment 규칙 통과 발화 |
| `min_s2_confidence` | 10% 분위 | 독립 audit이 approved인 발화 |
| `min_issue_confidence` | `issued_precision ≥ 0.90`을 만족하는 최소값 | allowlist 안의 issue draft |
| `min_issue_severity` | 고정 0.0 | severity를 독립 veto로 쓰지 않음 |

목표를 만족하는 값이 없으면 profile은 `uncalibrated`로 남는다. 값을 낮추어
통과시키지 않는다. 필수 branch는 `explicit`, `s104_adapter`,
`s105_selected_hierarchy`, `gop_acoustic_posterior` 4종이며, 하나라도 결손이면
`uncertain`으로 종료한다(0점 대체 금지).

## 4. gate 임계

| 그룹 | 조건 | 위반 시 |
|---|---|---|
| S107 calibration | `ece ≤ 0.10`, `issued_precision ≥ 0.90` | 단계 실패로 기록 |
| Stage C content backend | dev `content_cer ≤ 0.20`, `content_wer ≤ 0.35` | Stage C를 `backend_unavailable`로 두고 E5 진행 금지 |
| E01 metamorphic | 일치율 ≥ 0.95 | 코칭 발행 중단 |
| E8 e2e | `coaching_ready ≥ 1`건, 발행 항목의 span 3-tuple 완전 | 원인 단계로 되돌림 |

Stage C 상한(0.20 / 0.35)의 근거는
`engineering_acceptance_bound_frozen_before_execution`이다. 실행 전에 고정한
공학적 수용 한계이며, 검증된 성능 기준이 아니다.

## 5. 접근 계측 계약

다음 counter는 모두 0이어야 하고, **보고되지 않은 counter는 미접근 증거로
인정하지 않는다**(누락도 nonzero와 동일하게 fail-closed).

- `locked_test_path_resolution_count`, `locked_test_read_count`
- `expert_holdout_path_resolution_count`, `expert_holdout_read_count`
- `human_rating_read_count`
- `raw_audio_path_emitted_count`, `speaker_identifier_emitted_count`

`on_nonzero`는 `failed_closed` 고정이다. 모든 Track E run receipt는
`contract_sha256`를 인용하고 위 counter 전부를 보고해야 한다.

## 6. 중단 조건

artifact의 `stop_conditions`가 정본이다. locked/expert holdout 접근,
결과 확인 후 규칙 변경, S1/S2 backend·정렬·split 중복, hash 불일치,
raw audio·speaker·secret 유출, `engineering_provisional` 결과를 검증된
성능으로 기술하는 것 — 이 중 하나라도 관측되면 즉시 중단하고 원장에 기록한다.

## 7. `research_validated`로 넘어갈 수 없는 이유

`ThresholdSet`은 `claim_scope=research_validated`에 `human_gate_receipt`를
요구하고, `JsonThresholdStore`는 artifact에서 `claim_scope`와
`human_gate_receipt`를 읽지 않는다. 따라서 threshold artifact를 편집하는
방식으로는 Track R scope를 만들 수 없다. 이는 코드로 강제된다.
