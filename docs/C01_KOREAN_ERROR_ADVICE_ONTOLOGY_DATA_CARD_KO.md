# C01 한국어 오류 taxonomy·advice ontology 데이터 카드

작성일: 2026-07-29 KST  
상태: **engineering contract 검증 완료, C01 연구 완료 주장 보류**

## 1. 감사 결론

현재 허용된 개인정보 안전 집계만으로 다음 항목은 실제 값에 맞춰 고정했다.

- AI Hub 71490 공식 오류 tag 17종의 raw label↔stable ID 전단사
- tag별 실제 interval support와 총합 176,523건, unknown 0건
- 오류 interval과 target phone interval의 overlap·동률·multi-tag·미매칭
  처리 규칙
- gold error annotation의 label/slice 전용 사용과 feature 입력 금지
- top-1/top-3 우선순위, confidence gate, unsupported-claim abstention
- `기타`, unknown, phone-localizable하지 않은 tag의 target-phone 주장 금지

다만 현재 집계에는 `tag × target phone` 결합 빈도가 없다. 모든 tag의
`empirical_target_phone_support`를 `null`로 고정했고 실제 phone별 support를
만들지 않았다. 조언 문구도 `engineering_draft`이며 한국어 음성학 전문가
검수나 C03 200건 안전 감사를 대신하지 않는다. 따라서 이 문서는 tracker의
C01을 완료로 바꾸거나 배점을 부여할 근거가 아니다. C01은 아래 세
blocking evidence가 생길 때까지 `진행 중`으로 유지해야 한다.

1. 승인된 경로에서 만든 개인정보 안전 `tag × target phone` joint aggregate
2. S107에서 확정한 calibration 기반 confidence threshold
3. C03 전문가 200건 감사와 위치·유형 정확도 및 unsupported claim gate

## 2. 입력 증거와 접근 경계

유일한 데이터 입력은
`data/processed/aihub71490/manifests/aihub71490_data_card.json`이다.

- source schema: `aihub71490.data_card.v1`
- source SHA-256:
  `4ab53c6837b1a28fbf153dd3a351a195f6931a43694467114cea6c21b8a57b13`
- target phone inventory: 38종, occurrence 4,296,628건
- error interval: 176,523건
- normalized unknown error tag: 0건

이번 감사에서 읽은 범위는 privacy-safe aggregate 1개뿐이다. row-level
manifest·annotation, raw WAV/JSON/TextGrid, locator, `.env*`, 화자·발화
식별자는 읽지 않았다. target-phone joint support가 없는 이유를 원시
자료 접근으로 우회하지 않았다.

## 3. 17종 실제 support와 routing

| raw label | stable ID | interval | target scope | localization |
|---|---|---:|---|---|
| 초성 | `initial_consonant` | 35,198 | onset consonant | phone overlap 필수 |
| 종성 | `final_consonant` | 44,376 | coda consonant | phone overlap 필수 |
| 모음오류 | `vowel_error` | 22,033 | vowel | phone overlap 필수 |
| 경음화 | `tensification` | 13,424 | contextual sequence | phone overlap 필수 |
| 기타 | `other` | 14,451 | unknown | target phone 주장 금지 |
| 반복 | `repetition` | 12,162 | non-target fluency event | target phone 주장 금지 |
| 비음화 | `nasalization` | 10,006 | contextual sequence | phone overlap 필수 |
| 격음화 | `aspiration` | 6,438 | contextual sequence | phone overlap 필수 |
| 유음화 | `liquidization` | 4,156 | contextual sequence | phone overlap 필수 |
| 연음규칙 | `liaison_rule` | 3,861 | contextual sequence | phone overlap 필수 |
| 경계억양 오류 | `boundary_intonation_error` | 3,177 | prosodic region | phone 단정 금지 |
| ㄴ-첨가 | `n_insertion` | 2,673 | contextual sequence | phone overlap 필수 |
| 구개음화 | `palatalization` | 2,012 | contextual sequence | phone overlap 필수 |
| 강세 오류 | `stress_error` | 1,187 | prosodic region | phone 단정 금지 |
| 간투사 | `interjection` | 793 | non-target fluency event | target phone 주장 금지 |
| 모음삽입 | `vowel_insertion` | 300 | contextual sequence | phone overlap 필수 |
| 음운탈락 | `phoneme_deletion` | 276 | contextual sequence | phone overlap 필수 |
| **합계** | **17종** | **176,523** |  |  |

`other`는 원 데이터의 17종 안에 이미 포함된 공식 `기타`다. `unknown`은
새로운 18번째 관측 tag가 아니라 정규화 실패를 보존하는 sentinel이며 현재
관측 support는 0이다. 사람 평가의 `uncertain`도 별도 판정 상태로 취급하고
`기타`와 합치지 않는다.

## 4. target-phone mapping 규칙

구간은 `[start, end)` half-open으로 해석한다.

1. overlap duration이 0보다 큰 target phone만 후보로 둔다.
2. overlap이 가장 큰 phone 하나를 primary target으로 정한다.
3. overlap 동률이면 `target_phone_index`가 작은 항목, 그다음 target label
   사전순으로 정한다.
4. 같은 target에 여러 error interval이 매핑돼도 source interval을
   합치거나 한 tag로 축약하지 않는다.
5. 후보가 없으면 tag는 보존하고 target은 `null`로 둔다. 이 경우
   target-phone 조언을 내지 않는다.
6. `other`, unknown, prosodic region, non-target fluency event는 구간이
   phone과 겹치더라도 exact target-phone claim을 만들지 않는다.
7. 중복 source/target index, 비유한 수, 역전·영 길이 interval은
   fail-closed한다.

이 규칙은 synthetic unit test로 고정했지만 실제 row에 적용해 joint
aggregate를 만들지는 않았다. 현재의 onset/coda/vowel/context scope는
ontology routing 범주이지 관측된 phone별 빈도나 전문가 정답이 아니다.

## 5. label과 feature의 강제 분리

Gold 오류 정보에 허용된 역할은 다음 세 가지뿐이다.

- supervision label
- evaluation slice
- human-audit stratum

`raw_error_tag`, `normalized_error_tag_id`, `error_interval`,
`error_tag_count`, `realized_phone`, `alignment_operation`은 model input
feature로 금지한다. catalog validator는 이 denylist와 역할 목록의 drift를
거부한다. 이 정책은 기존 explicit/WavLM의 label-only 경계와 같다.

## 6. advice ontology와 abstention

우선순위는 다음 고정식이다.

`severity × confidence × comprehensibility_impact × trainability`

- 허용 모드는 top-1 또는 top-3뿐이며 기본은 top-1이다.
- 동률은 start, end, issue type, target, observed, evidence 순으로 푼다.
- top-k window 안에서 confidence가 기준보다 낮으면 그 issue는
  `rerecord`, 지원되지 않은 claim이면 `expert_review`로 abstain한다.
- 막힌 top-k issue를 버리고 k 밖의 낮은 순위 issue로 채우지 않는다.
- 현 `0.75`는 S107 전의 provisional engineering 기본값이다.
- 16개 tag의 한국어 문구는 draft이고 `other`는 advice ID·문구가 없이
  항상 expert review로 보낸다.
- C03 전에는 catalog 전체의 production release가 금지된다.

기존 `src/voice_coach/coaching/catalog.py`의 4개 문구는 C00 oracle/API
회귀용 legacy engineering allowlist다. 이 파일도 C03 검수 완료 증거가
아님을 명시했고, 새 17종 catalog가 실제 coaching 결과를 생성했다는
주장은 하지 않는다. C02 runtime 연결과 coaching report는 별도 작업이다.

## 7. 산출물과 재현

| 산출물 | SHA-256 |
|---|---|
| machine catalog | `1ba0c0198533abd51a3caa4b176f17aae17aa48d08899f6e7ced634a1eef0d60` |
| JSON Schema | `aea001bca187af90f6aece0eebbfd8e2cd28304f054be11c7a170cac7efb6bb7` |
| validation report | `f81b31fe35ae7fc84d80ae1be2f965d35147066b985916f38f0d1e45638723cc` |
| mapping implementation | `291862fe406101909302284f7d6bd62418e23179c1425fb2233c7f38f5876f8e` |
| validator implementation | `7debfdb76e3e8c2f92b2c9b2aacb66b44a6dbd5f433d0276c78b1ae1b6550ff9` |
| prioritizer implementation | `b05f8ce48a5c2c8d86d77f476340cda24612d69bc2945cd74d50ce2381eccadf` |
| validator CLI | `144f83db469af5781f22421bcea8959f319ae8a5f5ce56978ba293c7b45018b9` |

검증 명령:

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/validate_korean_error_advice_ontology.py --validate-only
```

정본 report는
`artifacts/coaching/c01_korean_error_advice_ontology_v1.validation.json`에
있다. 동일 입력은 byte-identical report만 허용하며 다른 기존 report를
덮어쓰지 않는다.

검증 결과:

- C01 taxonomy/mapping/policy/validator targeted: 45 passed
- 전체 unit suite + API integration 회귀: 534 passed, 7 warnings
- Ruff check: passed
- Ruff format check: passed
- mypy(신규 mapping/validator/prioritizer/CLI): passed

warning 7개는 기존 Starlette/httpx deprecation 1개와 tiny WavLM
attention-mask 경고 6개이며 C01 실패는 아니다. canonical S104 GPU 학습과
자원 경합을 피하기 위해 별도의 전체 integration 장시간 suite 완료는 이
문서의 근거로 주장하지 않는다.

최종 자동 감사 결과는 C/H/M `0/0/0`이다. 이는 구현 bundle 안에서 발견한
미해결 결함 수이며, 위 세 blocking evidence가 충족됐다는 뜻이 아니다.
