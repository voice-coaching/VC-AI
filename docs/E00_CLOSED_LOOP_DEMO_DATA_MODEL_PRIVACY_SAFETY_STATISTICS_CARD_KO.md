# WP8 E00 폐루프 데모 데이터·프라이버시·안전·통계 카드

기준일: 2026-07-29 KST  
상태: **production 정적 준비 계약만 구현 / 실제 데모 실행 0**

## 1. 목적과 현재 비주장

E00의 미래 목적은 한 번의 발음 시도를 실제 production scorer로 진단하고,
안전 gate를 통과한 경우에만 catalog 코칭과 동의·허가된 reference를
제공한 뒤 재녹음 결과를 같은 scorer로 비교하는 폐루프를 보이는 것이다.

현재 package는 strict schema, source-pinned config, 순수 payload validator와
read-only preflight만 제공한다. 다음은 전혀 수행하지 않았다.

- learner/fixture 입력 또는 microphone 접근
- raw audio 읽기, 녹음, 재녹음
- S108 model/checkpoint/calibrator/GPU 접근 또는 inference
- API 호출이나 응답 전송
- C02 코칭 또는 R03/D08 reference 전달
- pre/post metric, effect 또는 CI 계산
- output, registry, failure artifact 또는 commit 작성
- E00 완료, 교육 효과, 임상 효과 또는 production 배포 주장

따라서 이 문서의 `ready`는 별도 승인 executor를 실행할 수 있는 정적 계약
준비를 뜻하며, 실제 E00 성공이나 사용자 효용을 뜻하지 않는다.

## 2. 고정 폐루프 순서

Eligible trace의 순서는 다음과 같이 고정된다.

```text
attempt 1
  -> actual S108 production diagnostic handoff
  -> E02 confidence/OOD/support/abstain guard
  -> C02 catalog-only top-1 coaching
  -> exact D08↔R03 lineage가 확인된 licensed reference
  -> attempt 2
  -> attempt 1과 동일 scorer의 pre/post comparison
```

S108 handoff는 각 attempt의 실제 production inference receipt여야 한다.
S108 locked test row, prediction 또는 result를 복사하거나 replay해 데모
진단처럼 보이게 할 수 없다. Attempt 2에도 같은 model identity,
checkpoint, calibrator와 threshold policy를 사용하며 결과를 본 뒤 scorer를
바꿀 수 없다.

기존 `/v1/oracle-attempts`는 연구·테스트용 endpoint다. Target text,
score와 caller threshold를 받는 그 endpoint를 E00 production scorer나
E02 gate로 간주하지 않는다. 이번 package는 API route를 추가하거나
변경하지 않았다.

## 3. 선행 증거와 fail-closed 순서

별도 executor가 session 또는 audio 경로를 해석하기 전에 다음 actual
artifact를 순서대로 deep validation해야 한다.

1. S108 final `research_candidate_passed` completion과 model lineage
2. E01 `passed` metamorphic completion
3. C02 actual deterministic bundle과 frozen E02 preregistration
4. C03 expert audit `passed`, production release allowed
5. R03 private 20문장×3–5 reference bank
6. D08 aggregate completion, consent·recording·QC·rights complete
7. frozen E00 preregistration
8. human 또는 learner-fixture 실행 authorization
9. exact D08 metadata row↔R03 candidate lineage receipt
10. licensed reference handoff

앞선 gate가 해결되지 않으면 뒤 artifact의 path도 resolve하지 않는다.
Canonical config의 최초 blocker는 S108 completion
`TBD_S108_COMPLETION`이다.

## 4. E00 artifact와 privacy zone

| artifact | zone | 허용 내용 | 금지 내용 |
|---|---|---|---|
| preregistration | C1/A1 | 표본·flow·blocked·comparison·claim 정책 SHA | unresolved TBD인 actual 승인 |
| execution authorization | A1/C1 summary | mode, 승인·fixture receipt SHA | audio, locator, 직접식별자, 자유문 |
| S108 diagnostic handoff | D1 | attempt/audio content/model lineage와 categorical receipt | score, threshold, source condition, raw path |
| D08↔R03 lineage receipt | D1 | exact candidate/audio/metadata-row/rights hashes | speaker identity, locator, 독립 R01=D08 추정 |
| reference handoff | D1 | score-free licensed candidate와 lineage receipt binding | locator, speaker, source condition, score |
| session manifest | D1 | opaque attempts와 scorer lineage | audio bytes/path, plaintext, numeric score |
| trace | D1 | categorical event/guard/delivery receipt | PII, audio locator, score/effect, free text |
| completion | A1 | exact trace·eligible·blocked count와 safety counts | row data, effect/CI, pedagogical·clinical claim |

Hash는 익명화가 아니라 가명·무결성 식별자다. D1 payload와 audio content
hash는 공개 자료가 아니며 별도 접근통제를 유지해야 한다.

## 5. D08와 R03의 exact lineage

R03 후보의 `source_kind=human_recording`만으로 그 후보가 D08 녹음이라고
볼 수 없다. 특히 독립 R01 human candidate를 D08로 간주하는 것은
명시적으로 금지된다.

`voice_coach.e00_d08_r03_lineage_receipt.v1`은 제한 영역의 lineage
서비스가 다음을 하나의 binding으로 교차 확인한 경우에만 통과한다.

- exact R03 bank manifest와 selected candidate canonical row SHA
- target text SHA
- selected audio object SHA와 audio content SHA
- D08 aggregate completion과 metadata manifest SHA
- exact restricted D08 recording metadata row SHA
- candidate/source/D08 recording audio content SHA의 동일성
- D08 aggregate recording-rights attestation SHA
- candidate source-rights attestation과 D08 rights의 동일성
- exact D08 metadata-row per-recording license grant와 source grant의 동일성
- source kind가 실제 `human_recording`
- speaker identity와 audio locator 비포함

D08 metadata row 전체를 E00 receipt에 복사하지 않는다. Row의
speaker/locator field는 제한 영역에 남기고 canonical row SHA와 필요한
audio/rights projection만 결합한다. Reference handoff는 이 receipt의
고정 SHA, candidate row, audio object/content와 rights를 다시 exact
comparison한다.

## 6. Human mode와 nonhuman fixture mode

두 실행 mode를 혼합 집계하거나 한쪽 승인을 다른 쪽에 재사용하지 않는다.

### Human authorized demo

다음이 모두 필요하다.

- actual H00 public approval와 official-document attestation
- E00 recording·diagnostic·coaching·reference·rerecord scope 또는 amendment
- 개인별 active consent manifest
- withdrawal/deletion 정책
- human escalation

H00만으로 개인 동의나 E00 scope를 추론할 수 없다.

### Nonhuman test fixture

Fixture authorization의 `contains_human_voice=false`는 **learner attempt
input fixture에만** 적용된다. 이를 E00 session 전체에 인간 음성이 없다는
주장으로 해석하면 안 된다.

- scope는 `learner_attempt_input_fixture_only`
- learner attempt fixture에는 human voice와 participant/user data가 없음
- 실제 learner를 대표하지 않음
- human user data를 만들어 내지 않음
- synthetic/nonhuman label을 명시
- 교육·임상 효과 주장 금지

폐루프에서 들려주는 D08 reference는 이 fixture scope 밖의 **별도 동의되고
licensed된 인간 음성**이다. 그 reference는 앞 절의 D08↔R03 lineage와
rights gate를 별도로 통과해야 한다.

## 7. E02 차단과 safe exit

다음 중 하나라도 발생하면 C02 코칭, reference, 자동 corrective action,
attempt 2와 comparison으로 진행하지 않는다.

- `out_of_domain`
- `low_confidence`
- `unsupported`
- `abstain_all`

Blocked trace의 허용 결과는 `rerecord` 또는 `expert_review`라는
safe-next-action code뿐이다. 이는 E00 package가 실제 재녹음을 시작하거나
전문가에게 데이터를 전달했다는 뜻이 아니다. Capability override,
reference 우회와 free-generated 설명은 금지된다.

C02가 `no_issues`이면 `no_correction_safe_exit`이며 코칭·reference·attempt
2·comparison이 없다. E02가 별도 renderer eligibility를 반환하고 C02가
실제 `selected`일 때만 eligible flow로 진행할 수 있다.

## 8. 코칭과 reference 안전성

C02는 deep-validated actual bundle에서 deterministic하게 고른 catalog
item 정확히 1개만 사용한다.

- C01 catalog 밖 문구 금지
- top-k 밖 backfill 금지
- LLM/network/model 기반 free generation 금지
- prompt/completion 저장 금지
- C03 expert audit pass 전 production release 금지

Reference는 exact D08↔R03 receipt를 통과한 human recording 하나만
score-free handoff로 전달한다. Source identity/condition, speaker,
locator 또는 model score를 learner trace에 노출하지 않는다.

## 9. 비교와 통계 경계

E00은 noninferential closed-loop demo다. Eligible trace에서는 같은 scorer
정책으로 attempt 1과 attempt 2를 짝지어 비교할 수 있지만, 이 비교를 교육
효과나 일반화 성능으로 승격할 수 없다.

다음은 근거 없이 채우지 않고 actual preregistration에서 동결한다.

- exact demo trace count와 target selection
- minimum support
- paired metric definition과 categorical interpretation
- missing/failed attempt 처리
- exact effect estimand
- CI 방법과 multiplicity
- pedagogical claim threshold
- clinical claim
- deployment claim

현재 template은 exact sample size/effect/CI를
`TBD_PREREGISTRATION`으로 유지하고 pedagogical, clinical, production
claim을 false로 둔다. Completion payload에도 numeric effect, CI 또는
claim 필드가 들어갈 수 없다.

## 10. 무결성·출력 경계

- JSON/YAML duplicate key와 NaN/Infinity 거부
- strict root/nested schema와 exact key set
- 50개 C1/static source SHA-256 pin
- workspace-relative path만 허용
- traversal, symlink와 non-regular file 거부
- open 전·중 inode/size/mtime/content hash 검증
- static과 predecessor TOCTOU 재검증
- future output unique/no-overwrite 계약
- config-only/full preflight 모두 output write 0

Static package의 `ensure_output_targets_absent`는 미래 executor용 보조
검사일 뿐 현재 preflight가 호출하지 않는다.

## 11. 현재 actual

- actual input/audio/microphone: 0
- model/checkpoint/calibrator/GPU/inference: 0
- API/network: 0
- coaching/reference delivery: 0
- rerecord: 0
- metric/result/effect/CI: 0
- output/registry/commit/publication: 0
- completed eligible loop: 0

따라서 E00 actual 완료율과 논문 실험 점수는 0이다.
