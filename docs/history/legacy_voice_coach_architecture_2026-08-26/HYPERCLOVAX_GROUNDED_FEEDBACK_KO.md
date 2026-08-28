# HyperCLOVA X 근거 제한형 발음 피드백 통합

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

최종 갱신: 2026-08-23 (Asia/Seoul)

## 1. 범위와 현재 상태

이 문서는 기술적 발음 판정 뒤의 **표현 계층**만 다룬다. HyperCLOVA X는
음성을 입력받지 않고, 오류를 새로 판정하거나 threshold·위치·수치를 바꾸지
않는다.

현재 구현됨:

- gate를 통과한 issue와 기존 advice catalog만 받는 v2 입력 계약
- 모델이 자유 문장 대신 승인 claim ID와 표현 style만 고르는 v2 출력 계약
- strict JSON parser, issue/advice/claim identity validator, 개인정보 경계
- 검증된 plan과 원래 catalog 문구로만 만드는 결정론적 한국어 renderer
- loopback HTTP만 허용하는 HyperCLOVA X adapter와 timeout/오류 시 catalog 폴백
- pinned snapshot 다운로드·오프라인 설치 검증·Transformers sidecar·CLI wiring
- `seungun` frozen Stage-1/layer-18 detector의 온라인 phone evidence runtime과 bridge
- 합성 단위/회귀 테스트
- 고정 revision 전체 snapshot의 14개 BF16 shard와 `66,642,461,825` byte 검증
- 단일 GB10에서 오프라인 BF16 load와 sidecar→adapter→validator→renderer 실제 smoke

아직 검증되지 않음:

- 장시간·동시 요청에서의 latency/throughput 및 정확한 peak unified memory
- phone별 혀·입술·턱·기류 지식을 제공하는 전문가 검토 catalog
- 실제 학습자 음성을 사용한 end-to-end 품질과 교육 효과

따라서 현재 연결은 `engineering_provisional`이며, LLM 통합 성공이 발음 진단
품질이나 코칭 내용의 전문가 승인을 의미하지 않는다.

## 2. 실행 흐름

```text
사용자 WAV + 고정 prompt
        |
        v
음향 분석 + deterministic gate
        |
        |  DiagnosticIssueDraft
        |  (issue/location/severity/confidence/evidence)
        v
기존 advice catalog 조회
        |
        v
GroundedCoachingPayload v2
  - target text
  - gate 통과 issue와 위치
  - severity/confidence
  - 선택적 observed candidate(모델 후보라고 명시)
  - 승인된 claim ID와 원문
        |
        v
로컬 HyperCLOVA X SEED Think 32B
  - claim ID/style/candidate disclosure만 선택
        |
        v
strict validator
  | success                         | failure/timeout/abstain
  v                                 v
결정론적 renderer                 기존 catalog 원문
        \_____________________________/
                      |
                      v
                 LearnerFeedback
```

LLM은 최종 문장을 직접 쓰지 않는다. 이 구조는 의미 동등성을 생성 모델로 다시
판정하는 순환 검증을 피하고, 최종 문장의 모든 사실이 코드가 보유한 문자열에서
왔음을 기계적으로 확인한다.

## 3. 입력 계약

schema: `voice_coach.grounded_advice_input.v2`

예시:

```json
{
  "schemaVersion": "voice_coach.grounded_advice_input.v2",
  "task": "select_approved_pronunciation_feedback_plan",
  "targetText": "쌀과 콩을",
  "issue": {
    "issueId": "issue-1",
    "type": "final_consonant_deletion",
    "location": {
      "unitType": "syllable",
      "unitIndex": 0,
      "targetUnit": "쌀",
      "startMs": 120,
      "endMs": 320
    },
    "observedCandidate": "사",
    "observedCandidateState": "model_candidate_not_ground_truth",
    "evidenceState": "gate_passed",
    "technicalEvidence": {
      "severity": 0.7,
      "confidence": 0.85,
      "detectorScore": null,
      "operatingThreshold": null,
      "scoreSemantics": null
    }
  },
  "approvedGuidance": {
    "adviceId": "ARTICULATION_RULE_017",
    "claims": [
      {
        "claimId": "ARTICULATION_RULE_017:SOURCE_MESSAGE",
        "text": "받침 위치에서 공기를 더 내보내지 말고 혀와 입의 접촉을 유지해 보세요."
      }
    ]
  },
  "claimScope": "engineering_provisional",
  "constraints": {
    "mayAddIssue": false,
    "mayAddPhone": false,
    "mayAddLocation": false,
    "mayAddScore": false,
    "mayAddArticulationFact": false,
    "maximumIssues": 1
  }
}
```

`observedCandidate`는 upstream evidence에 `heard_syllable=<한글>`이 정확히 하나
있을 때만 포함한다. 후보는 실제 발음의 확정 전사가 아니다. 후보가 없거나
서로 충돌하거나 안전한 한글 형태가 아니면 `null/not_available`이다.

audio bytes/path, SHA-256, speaker/utterance 식별자, raw transcript는 LLM 입력에
포함하지 않는다.

## 4. 출력 계약과 최종 렌더링

schema: `voice_coach.grounded_advice_output.v2`

```json
{
  "schemaVersion": "voice_coach.grounded_advice_output.v2",
  "status": "rendered",
  "issueId": "issue-1",
  "adviceId": "ARTICULATION_RULE_017",
  "selectedClaimIds": ["ARTICULATION_RULE_017:SOURCE_MESSAGE"],
  "style": "focus",
  "candidateDisclosure": "model_candidate"
}
```

허용 enum과 정확한 7개 field 이외의 값은 거부한다. markdown fence, 설명문,
미승인 claim, 바뀐 issue/advice ID, 입력에 없던 observed candidate도 거부한다.

위 plan의 결정론적 최종 문장 예시는 다음과 같다.

```text
분석에서는 ‘쌀’이 ‘사’ 쪽 후보로 나타났습니다. 이 후보는 실제 발음을 확정한
전사가 아닙니다. 다음 시도에서는 표시된 ‘쌀’에 집중해 보세요. 받침 위치에서
공기를 더 내보내지 말고 혀와 입의 접촉을 유지해 보세요.
```

마지막 문장의 조음 지시는 LLM이 만든 것이 아니라 기존 catalog의 정확한
원문이다. 현재 catalog는 legacy engineering 4개 항목뿐이므로 그 범위를 넘는
혀·입술·턱·기류 조언은 생성하지 않는다.

## 5. 로컬 모델 설치와 실행

고정 설정:

- model: `naver-hyperclovax/HyperCLOVAX-SEED-Think-32B`
- revision: `a6cdfd3464d1b767259cad23e164eaf39d3e3960`
- repository bytes: `66,642,461,825`
- BF16 weight shards: 14
- config: `configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json`

다운로드 또는 재개 후 byte/shard 검증:

```bash
.venv-scorer/bin/python scripts/download_hyperclovax_seed_think_32b.py
.venv-scorer/bin/python scripts/download_hyperclovax_seed_think_32b.py --verify-only
```

local sidecar 시작:

```bash
.venv-hyperclova/bin/python scripts/serve_hyperclovax_grounded_advice.py \
  --host 127.0.0.1 \
  --port 8000 \
  --gpu-memory-gib 110
```

`seungun` frozen detector부터 generic phone feedback까지 실행:

```bash
.venv-scorer/bin/python scripts/run_seungun_grounded_feedback.py \
  --audio /restricted/input.wav \
  --target-text "연습 문장" \
  --expected-phones-json '["ㅇ", "ㅕ", "ㄴ", "ㅅ", "ㅡ", "ㅂ"]' \
  --advice-endpoint http://127.0.0.1:8000/v1 \
  --advice-revision a6cdfd3464d1b767259cad23e164eaf39d3e3960
```

이 경로는 `seungun`의 Stage-1 CTC span, 9개 soft feature, layer-18 mean pooling,
동결 normalization/MLP/threshold를 온라인에서 재현한다. phone별 출력은
`korean_phone_ctc.pronunciation_analysis.v1`이며 `error_score`, threshold,
`is_error`, CTC observed candidate와 위치를 보존한다. `error_score`는 보정된
정답 confidence가 아니다.

CLI 연결:

```bash
.venv/bin/python scripts/run_pronunciation_feedback.py attempt \
  --prompt-id R00-01 \
  --audio /restricted/input.wav \
  --advice-endpoint http://127.0.0.1:8000/v1 \
  --advice-revision a6cdfd3464d1b767259cad23e164eaf39d3e3960
```

endpoint와 revision은 함께 지정해야 한다. endpoint는 `http` loopback만
허용하고 credential/query를 거부한다. inference는 local snapshot만 읽으며
외부 network fallback을 사용하지 않는다. 한 issue당 1회 호출하고 재시도하지
않는다.

모델 custom code의 기준 버전인 Transformers 4.52.4는 `.venv-hyperclova`에
분리했다. `.venv-scorer`의 Transformers 5.14.1은 구성 smoke에서
`no_init_weights` import 불일치가 확인되어 32B runtime으로 사용하지 않는다.

공식 모델 카드는 production OmniServe에 4×A100 80GB를 요구한다. 이 장비는
단일 NVIDIA GB10 119GiB unified memory로 공식 production 구성과 다르다.
2026-08-23에 Transformers 단일 장비 경로의 실제 smoke는 통과했지만, 이는
공식 production 지원·처리량 또는 여유 메모리를 보증하지 않는다.

실측 smoke:

- cold model load: 14 shard, 7분 2초
- 동일 grounded 입력 2회 생성: 19.415초, 19.759초
- 두 응답 모두 strict validator 통과, 최종 문장 동일
- prompt 보강 전 Markdown fence 응답은 validator가 거부하고 catalog로 폴백
- 로드·생성 후 system-wide snapshot: 119GiB 중 72GiB 사용, 47GiB available
- 서버 프로세스 `VmHWM`: 5,802,176kB

GB10 unified allocation은 이 측정에서 `nvidia-smi` per-process memory로 노출되지
않았다. 따라서 system-wide snapshot이나 `VmHWM`을 모델의 정확한 peak unified
memory로 해석하지 않으며, 그 값은 별도 계측 게이트로 남긴다.

## 6. 폴백과 운영 판정

다음은 모두 attempt 실패가 아니라 기존 catalog 메시지 폴백이다.

- endpoint timeout/unavailable
- HTTP/JSON/schema 오류
- 모델 abstain
- 미승인 claim 또는 ID 변경
- 없는 후보·사실 생성 시도
- 렌더링 길이/개인정보 검사 실패

`StageReceipt(stage="advice_generator")`에는 성공 시 model ID/revision과
`validated_grounded_plan`, 실패 시 예외 본문 없이
`fallback=advice_catalog reason=<ExceptionType>`만 기록한다.

모델이 없을 때는 기존 pipeline 동작과 receipt가 바뀌지 않는다. LLM은 선택적
표현 계층이며 acoustic/gate 경로의 가용성 조건이 아니다.

## 7. 검증 게이트

완료:

1. pinned snapshot 14개 shard와 byte count 검증
2. 모델 오프라인 BF16 load 및 strict-JSON 생성 smoke
3. sidecar→adapter→validator→renderer 통합 smoke
4. fenced JSON 거부 시 catalog 폴백 실측, invalid JSON/timeout/abstain 단위 테스트
5. 동일 입력 2회 결과 결정성과 단건 latency 기록

남음:

1. 장시간·동시 요청의 latency/throughput과 정확한 peak unified memory 계측
2. `seungun` frozen phone detector의 다수 WAV DGX/local parity 검증
3. phone→조음 claim catalog 작성 후 C03 전문가 감사
4. 실제 학습자 표본에서 end-to-end human review

현재 단일 비-locked dev WAV smoke는 통과했지만 다수 WAV parity는 미검증이다.
전문가 phone catalog 감사 완료 전에는 generic phone 재연습 문구 밖의 구체적
조음 조언을 발행하지 않는다.
