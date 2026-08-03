# R03 source-blind Pareto·diversity 선택 primitive

상태: **순수 알고리즘·합성 CPU 검증 완료 / 실제 R03 실행 0**

기준일: 2026-07-29 KST

## 범위

`src/voice_coach/references/r03_pareto_selector.py`는 R02가 미래에 제공할
수 있는 source-blind selector view를 대상으로만 동작하는 순수 함수다.
파일시스템, 후보 audio, 모델, GPU, network, human/TTS source label,
R04 label·MOS·CMOS, learner outcome을 읽거나 쓰지 않는다. 결과도 메모리에만
반환하며 R03 bank, commit 또는 공개 승인을 만들지 않는다.

허용 입력 field는 기존 R03 계약과 동일하다.

- opaque `candidate_id`, `sentence_id`
- target text와 R02 eligible row의 SHA-256
- objective vector
- speed·pitch bucket
- source를 드러내지 않는 blinded speaker-cluster SHA-256

그 외 field는 허용하지 않는다. 특히 source 종류·attestation·모델명,
R04 점수, MOS/CMOS, 학습·retention 결과와 raw identity/path/text field는
재귀적으로 거부한다.

## 명시적 정책 인자

논문과 현재 사전등록 template이 결정하지 않은 값을 기본값으로 발명하지
않았다. 호출자는 다음을 모두 `SelectorPolicy`에 명시해야 한다.

- objective ID와 maximize/minimize 방향
- identity/min-max/population-z-score, global/per-sentence fit scope,
  clipping 범위와 constant-axis 처리
- objective별 epsilon, epsilon tie 처리
- 첫 Pareto front 부족 시 fail 또는 다음 front 순위 채움
- 첫/boundary front 초과 시 fail 또는 diversity selection
- 선택 개수 3–5 중 정확한 값
- 입력을 문장·후보 ID로 정본화하는 candidate-order 정책
- 연속 objective distance feature와 weight
- weighted Euclidean/Manhattan distance
- blinded speaker·speed·pitch mismatch weight
- minimum separation 및 speaker·speed·pitch distinct floor
- exhaustive maximin algorithm, seed와 combination 상한

방향 변환 뒤 candidate A가 모든 축에서 B보다 `-epsilon` 이상이고 적어도
한 축에서 `+epsilon`보다 클 때만 A가 B를 지배한다. `keep_all`은
epsilon-equivalent 후보를 모두 남기고, `canonical_candidate_id`는 ID
오름차순 첫 후보만 남긴다.

## 선택과 재현성

문장별 모든 non-dominated front를 순서대로 계산한다. 첫 front가 부족하면
명시적으로 허용된 경우에만 더 낮은 front를 순서대로 채우며, 이미 포함된
상위 front 후보는 diversity를 이유로 제거하지 않는다. Boundary front가
초과하면 가능한 조합을 열거하고 다음 순서로 결정한다.

1. speaker·speed·pitch floor와 minimum separation 만족
2. 최소 pairwise distance 최대화
3. pairwise distance 합 최대화
4. 동률이면 명시적 seed에 묶인 SHA-256 순위

입력을 `sentence_id`, `candidate_id`로 정본화하므로 입력 행 순서에
무관하다. 반환값은 input, policy, normalized view, per-sentence fronts,
선택·diversity 진단을 SHA-256에 묶는다. `replay_source_blind_selection`은
동일 입력·정책을 다시 계산해 replay digest가 다르면 fail-closed한다.

## 검증과 연구 완료 경계

합성 CPU 테스트는 다음을 다룬다.

- maximize/minimize 혼합 방향과 문장별 front
- epsilon과 두 tie 정책
- objective 누락·NaN·무한대 거부
- front 부족/초과 정책
- speaker·speed·pitch 및 minimum-separation 불가능성
- source/R04/outcome denylist
- denylist에 없더라도 allowlist 밖인 추가 field와 중복 candidate ID 거부
- 입력 순서 불변성
- 다문장 3–5개 선택
- trace/replay 결정성 및 digest mismatch
- normalization 정책의 digest binding
- constant-axis normalization 정책과 exhaustive combination 상한

이 primitive는 승인된 R03 사전등록, R01/R02 실제 완료, objective 측정값,
source join 뒤 balance 검증, D1 bank/A1 aggregate 작성 또는 R04 blind
평가를 대체하지 않는다. 따라서 현재 실제 selected candidate, bank,
R04 결과와 논문 효과값은 모두 0이다.

독립 재검증에서는 신규 primitive 테스트 22개와 기존 R03
계약·integration 회귀 29개, 총 51개가 CPU에서 통과했다. Ruff,
format과 mypy도 통과했다.
