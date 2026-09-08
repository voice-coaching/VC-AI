# seungun → HyperCLOVA X 통합 상태

최종 갱신: 2026-08-23 (Asia/Seoul)

이 문서는 기존 `CURRENT_PIPELINE_KO.md.md`와 `NEXT_STEPS_KO.md`의 실험 이력을
재작성하지 않고, 그 뒤에 추가된 온라인 inference·표현 계층만 기록한다.

## 현재 구현

1. `seungun/src/korean_phone_ctc/frozen_detector.py`
   - XLS-R Stage-1 local checkpoint를 offline으로 로드한다.
   - CTC run span과 expected/hypothesis Levenshtein alignment를 만든다.
   - 동결 학습과 같은 9개 soft feature를 계산한다.
   - layer 18 predicted span mean pooling을 사용한다.
   - frozen normalization, expected-phone embedding, MLP state를 적용한다.
   - threshold `0.8134703636169434`로 phone별 `is_error`를 결정한다.
   - config/state/normalization의 고정 SHA-256과 freeze receipt를 검사한다.

2. `src/voice_coach/pronunciation_feedback/seungun_bridge.py`
   - threshold를 통과한 phone 중 error score가 가장 큰 하나를 선택한다.
   - 동률이면 expected phone index가 앞선 항목을 선택한다.
   - score는 `detector_ranking_score_not_calibrated_correctness_confidence`로
     표시하며 confidence로 바꾸지 않는다.
   - generic phone 재연습 claim만 승인한다.
   - HyperCLOVA X에는 audio/path/hash/speaker ID를 보내지 않는다.

3. grounded HyperCLOVA X 표현 계층
   - 모델은 최종 코칭 문장을 자유 생성하지 않는다.
   - 승인 claim ID, `direct|focus`, candidate 표시 여부만 고른다.
   - strict validator 통과 후 코드가 최종 문장을 만든다.
   - timeout, invalid JSON, abstain, 미승인 claim은 generic claim 원문으로
     폴백한다.

이 문서가 `seungun` 오디오 분석에서 HyperCLOVA X 표현 계층까지의 상태와
계약을 자체적으로 기록한다. 고정 모델과 실행 경로는 다음 파일로 확인한다.

- `configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json`
- `scripts/download_hyperclovax_seed_think_32b.py`
- `scripts/serve_hyperclovax_grounded_advice.py`
- `scripts/run_seungun_grounded_feedback.py`

`docs/architecture`의 시각 sidecar는 이 오디오 표현 경로를 소유하지 않는다.
아래 smoke에는 visual evidence 또는 visual feedback handoff가 포함되지 않았다.

## 실제 seungun 출력 계약

schema: `korean_phone_ctc.pronunciation_analysis.v1`

phone 한 건의 구조:

```json
{
  "expected_index": 6,
  "expected_phone": "ㄷ",
  "observed_candidate": "ㄸ",
  "alignment_operation": "substitution",
  "start_ms": 1382,
  "end_ms": 1402,
  "error_score": 0.9763295650482178,
  "operating_threshold": 0.8134703636169434,
  "is_error": true,
  "score_semantics": "detector_ranking_score_not_calibrated_correctness_confidence",
  "soft_features": {
    "hard_error": 1.0,
    "has_predicted_span": 1.0,
    "expected_prob": 0.07463296502828598,
    "competitor_prob": 0.9213643670082092,
    "expected_margin": -0.846731424331665,
    "predicted_prob": 0.9213643670082092,
    "entropy": 0.3001660406589508,
    "blank_prob": 0.0002867298317141831,
    "span_seconds": 0.020034782588481903
  }
}
```

위 값은 아래 비-locked dev smoke에서 실제 생성된 top error phone이다.
`observed_candidate`는 CTC alignment 후보이지 실제 발음의 정답 전사가 아니다.

## 확인된 실행

비-locked dev WAV 1건에 대해 CUDA smoke를 수행했다.

```text
expected phones: 25
CTC hypothesis phones: 25
phone evidence rows: 25
threshold-passed phones: 2
selected phone index: 6
selected target phone: ㄷ
selected error score: 0.9763295650482178
generic feedback: 표시된 구간에서 목표 음소 ‘ㄷ’를 천천히 분리해 발음한 뒤
                  원래 속도로 다시 녹음해 보세요.
```

이 결과는 온라인 코드 경로가 실행됐다는 smoke 근거다. 한 표본의 detector
정확도, 사용자별 타당성, DGX/local 전체 parity 또는 교육 효과를 입증하지 않는다.

## 확인된 HyperCLOVA X 실행

고정 revision `a6cdfd3464d1b767259cad23e164eaf39d3e3960`의 전체
`66,642,461,825` byte와 14개 BF16 shard를 검증했다. 단일 NVIDIA GB10에서
offline BF16 load 후 위 phone 한 건을 bridge에 넣은 실제 결과는 다음과 같다.

```text
cold model load: 7분 2초
strict grounded generation #1: 19.415초, validator 통과
strict grounded generation #2: 19.759초, validator 통과
final message identical: yes
final feedback: 분석에서는 ‘ㄷ’이 ‘ㄸ’ 쪽 후보로 나타났습니다. 이 후보는 실제
                발음을 확정한 전사가 아닙니다. 표시된 구간에서 목표 음소 ‘ㄷ’를
                천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요.
```

첫 prompt에서는 의미상 올바른 JSON을 Markdown fence로 감싸 반환했으며 strict
parser가 이를 거부하고 generic claim으로 폴백했다. parser를 완화하지 않고
prompt에 fence 금지를 추가한 뒤 위 2회가 통과했다. 이는 실행 경로 smoke이지
발음 진단의 정확도나 조언의 교육 효과 검증은 아니다.

## 남은 게이트

- 여러 WAV에서 DGX extraction과 local feature/error score parity
- phone index를 원문 문자·음절에 연결하는 g2pK/MFA mapping
- phone별 혀·입술·턱·기류 claim의 전문가 감사
- 장시간·동시 요청 latency/throughput과 정확한 peak unified memory 계측
- 실제 학습자 blind review

전문가 phone catalog 전에는 generic 재연습 문구만 제공하며, LLM이 구체적
조음 사실을 만들어 빈 영역을 채우게 하지 않는다.
