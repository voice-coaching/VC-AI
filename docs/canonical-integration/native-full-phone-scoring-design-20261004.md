# Native 전체 음소 채점 근거 설계

상태: [DRAFT 소스 구현과 정적 확인](native-full-phone-implementation-20261004.md) 완료. 보정·품질 검증·운영 배포는 미완료다.
사용자 결정(2026-10-04): 구버전 Seungun 실행 소스를 제거하고 최신 Native 모델로 전체 음소 채점 근거를 새로 설계한다. 점수 미제공을 정상 제품 동작으로 전환하는 선택은 하지 않았다.
관련 작업: [구버전 제거 및 지연 개선 계획](native-only-latency-plan-20261004.md).

## 1. 결론과 변경 경계

하나의 Native CPU FP32 forward에서 얻은 logits/CTC 관측을 기존 P1 코칭과 신규 전체 음소 채점이 공유한다. 별도 Seungun 모델, error detector, threshold, checkpoint, fallback을 실행하지 않는다.

역할은 네 단계로 분리한다.

1. Native adapter: 관측과 모델 identity를 생산한다. 점수나 오류 확률을 만들지 않는다.
2. 전체 음소 evidence service: 기대열·관측열·위치·모호성을 결속하고 검증된 정책으로 음소 상태를 결정한다.
3. 서버 rubric calculator: 상태 집계에서 9항목 level/overallScore를 계산한다.
4. GPT scoring adapter: RUBRIC_RESULT를 그대로 응답하도록 요청하고 서버가 정확히 검산한다. GPT가 음소 판정·보정·임계값 선택을 하지 않는다.

P1 코칭 정책과 점수 근거 정책은 별개다. P1에서 경고가 없다는 이유로 채점에서 정상으로 판정하지 않는다. 기존 H5 코칭의 후보 선택·금지 주장 규칙은 유지한다.

## 2. 확인된 소스와 한계

- Native 모델: slplab/wav2vec2-xls-r-300m_phone-mfa_korean, revision e26ff9dfb62169acf445d0060ef56863c018b20e.
- weights SHA-256: b2ec663558606d651a718fa1eaa20c6d504df173725ecbc66df74c22267e2d68. CPU/float32만 현재 authoritative다.
- 실제 vocab: 45개 ID 중 음소 0..39, 경계 `|`=40, UNK=41, blank/PAD=42, 특수 토큰 43/44. 40개 음소 토큰은 자음 23개와 모음 17개다.
- 기존 P1은 유일하게 위치가 정해진 자음 run의 raw margin을 판정한다. threshold 4.529027462005615는 이 조건 전용이다. 모음·삭제·전체 음소 점수의 threshold가 아니다.
- 기존 전체 음소 점수의 error_score와 threshold 0.8134703636169434는 구버전 검출기 출력이다. Native logits에 이 값이나 sigmoid 변환을 적용해 호환 필드를 만들지 않는다.
- 기존 g2pk_detector_phone_contract_v2는 초성 ㅇ을 제외하고 ㅙ를 ㅗ+ㅐ, ㅞ를 ㅜ+ㅔ로 분해한다. 신규 채점의 sampleCount를 여기에 종속시키지 않는다.
- 근거: RunPod의 모델 vocab.json/added_tokens.json, refined_p1_policy_contract.json, 9월 28일 frozen source의 native_infer.py/evaluate_smoke.py/pronunciation_phone_contract.py 및 현행 phone_rubric.py/gpt_rubric.py. 새 추론은 수행하지 않았다.

## 3. 버전과 계층

다음 이름은 새 계약의 제안 식별자이며 운영 등록값이 아니다.

| 계약 | 제안 revision | 책임 |
|---|---|---|
| 기대열 | native-expected-phones-v1 | 단어·음절·역할·원본 인덱스와 기대 표지 |
| 관측 토큰 매핑 | native-phone-inventory-v1 | token ID, 자음 역할, 모음 관측 동치 집합 |
| 관측 정렬 | native-phone-alignment-v1 | 기대열과 CTC run의 대응·삽입·삭제 후보·모호성 |
| 음향 판정 | native-phone-assessment-v1 | 검증된 feature·threshold·지원 범위 |
| 사적 근거 | native-phone-evidence-v1 | 동일 실행의 관측·판정·출처 |
| 채점 | phone-rubric-native-v1 | 기존 배점·산술, 신규 근거 의미 |

기존 phone-rubric-20261003-v1 이름을 재사용하지 않는다. 산술이 같아도 모델·표본 단위·판정 의미가 바뀌므로 과거 점수와 동등하지 않다.

도메인 Evidence/Assessment와 HTTP DTO, B2 원본, DB persistence model, FE view model을 분리한다. 라우터는 채점하거나 모델을 호출하지 않는다. 공개 URL 추가는 필요하지 않으며 기존 v5 결과 DTO에 새 revision을 등록하는 방식으로 연동한다.

## 4. 기대 음소와 표본 수

- 동일 대본에 기존 offline g2pK를 한 번 적용하고, 원문 문자 위치·단어·음절·발음 표지의 대응을 보존한다. 이를 발음의 정답 판정이 아니라 버전 고정 기대열로 취급한다.
- 초성 ㅇ은 음향 기대 표본에서 제외하고 제외 이유를 보존한다. 종성 ㅇ은 비음 표본이다.
- 모음은 현대 한글 21개 표지를 유지하며 ㅙ·ㅞ도 각각 한 표본이다. 모델 대응을 위해 내부적으로 토큰 여러 개를 검토하더라도 분모에 두 번 넣지 않는다.
- 이미 g2pK에서 변환된 표지를 original grapheme과 혼동하지 않는다. 받침 중화·연음의 정확성을 별도 측정했다고 주장하거나 추가 가감점하지 않는다.
- expectedIndex는 채점 기대열의 0..N-1 고유 인덱스다. 기존 P1 위치에는 source position을 통한 명시적인 one-to-many/many-to-one map을 제공한다. 양쪽 인덱스가 같다고 가정하지 않는다.
- 각 기대 표본은 정확히 한 음소 category와 coverage에 한 번씩 속한다. 추가로 관측된 삽입 토큰은 감사 근거로 보존하되 별도 감점이나 분모 증가에 쓰지 않는다.
- 존재하는 기대 표본이 미지원·모호하다고 sampleCount에서 빼지 않는다. n=0 제외 규칙은 대본에 해당 category가 실제로 없는 경우에만 적용한다.

## 5. Native vocabulary 매핑

### 자음

기존 Native 토큰의 어두·종성 역할을 보존한다. 예를 들어 G/GG/Kh와 종성 k, D/DD/Th와 종성 t, B/BB/Ph와 종성 p를 표면 자모만 보고 무조건 같은 관측으로 처리하지 않는다. L/R의 역할과 NG의 종성 조건을 명시한다. 역할 불일치는 UNKNOWN 근거이며 자동 substitution이나 clear가 아니다.

### 모음

기존 소스에서 단일 표지 대응이 확인된 토큰은 다음 14개다.

| Native token | 기대 표지 |
|---|---|
| A, EO, EU, I, O, U | ㅏ, ㅓ, ㅡ, ㅣ, ㅗ, ㅜ |
| euI, iA, iEO, iO, iU | ㅢ, ㅑ, ㅕ, ㅛ, ㅠ |
| oA, uEO, uI | ㅘ, ㅝ, ㅟ |

남은 E/iE/oE는 vocab에 있지만, 기존 P1의 모음 매핑 표에 없다. 다음은 검증 대상 매핑 후보이지 확정된 모델 의미가 아니다.

| 후보 token | 검증할 기대 표지 집합 |
|---|---|
| E | {ㅐ, ㅔ} |
| iE | {ㅒ, ㅖ} |
| oE | {ㅙ, ㅚ, ㅞ} |

모델 학습 label inventory 및 독립 발음 annotation으로 의미를 확인하고 inventory revision에 고정한다. 확인되지 않으면 해당 기대 표본은 TOKEN_MAPPING_UNVERIFIED이며 전체 점수는 UNSCORABLE이다.

동치 매핑이 검증되더라도 같은 토큰으로 합쳐진 표지 사이의 오발음을 구별했다고 주장하지 않는다. MATCH는 '모델 관측 클래스가 기대 클래스와 대응'이라는 뜻이다. 원래 21개 표지는 provenance와 category 집계에 유지한다. 사용자에게 21개 모음을 모두 독립적으로 구별하는 정확도 점수라고 표시하지 않는다. 엄격한 21개 간 구별이 필수라면 현 모델의 식별 가능성을 별도로 입증하거나 모델 변경 작업이 필요하다.

## 6. 단일 추론과 정렬

1. 준비 WAV의 bytes/PCM identity, 16 kHz mono, 길이를 확인한다. Native logits를 CPU FP32에서 한 번 생성한다.
2. argmax 경로를 CTC 규칙에 따라 collapse한다. 같은 ID의 연속 frame은 한 run, blank로 분리된 같은 ID는 서로 다른 run이다. blank/special을 발음 음소로 세지 않는다. UNK를 삭제하거나 정상 음소로 치환하지 않는다.
3. 기대열과 관측 run을 역할·검증된 token mapping에 따라 단조 정렬한다. 초기 제안은 match cost=0, substitution/insertion/deletion cost=1의 edit graph다. 확정 정책에 비용·역할 제약을 고정한다. 기대 텍스트를 강제로 decode해 모든 음소가 관측된 것처럼 만들지 않는다.
4. DP forward/backward 최적 비용으로 각 expectedIndex에 가능한 모든 최적 대응의 operation/run 후보를 계산한다. 최적 경로 하나의 임의 tie-break로 모호성을 지우지 않는다. 모든 경로를 열거하지 않는다.
5. MFA word/phone 구간은 P1과 위치 결속 및 교차 점검에 재사용한다. forced alignment의 구간 존재만으로 발음이 맞음 또는 coverage가 충족됨을 판정하지 않는다. 음소 개수만 같다는 이유로 모든 위치를 유일 정렬로 인정하지 않는다.
6. 위치나 역할 조건이 상충하면 UNKNOWN으로 보존한다. 시간은 Native frame span과 MFA alignment span을 별도 필드로 기록한다. CTC run 길이를 발음 지속시간으로 설명하지 않는다.

계산 비용: 관측 생성 O(T·V), 정렬 O(N·M) 시간 및 forward/backward 표 O(N·M) 메모리. V는 고정 vocab, N은 기대 표본 수, M은 CTC run 수다. 입력 최대 길이에 맞춘 N·M 상한을 예산에 고정하고 초과 시 RESOURCE_LIMIT로 실패시킨다. 근거를 잘라 점수를 만들거나 검증되지 않은 근사 banding을 적용하지 않는다.

## 7. 위치별 판정: 구조와 음향을 분리

각 표본은 alignmentOperation=MATCH/SUBSTITUTION/DELETION_CANDIDATE/UNKNOWN과 acousticAssessment=CLEAR/FLAGGED/UNKNOWN을 별도로 갖는다. CLEAR는 발음의 무오류 보증이나 calibrated probability가 아니다.

### MATCH / SUBSTITUTION

- 가능한 모든 최적 경로에서 동일한 role-compatible 관측 run 및 operation으로 대응하고, 필요한 위치 근거가 유효해야 한다.
- 음향 feature 후보는 같은 위치의 frame에서 계산한 expected-class logit, competitor-class logit, 그 margin, entropy, blank 비중, run/frame 범위다. 전체 vocab의 blank/special과 음소 competitor 집합을 구분한다.
- 예시 후보 margin 정의: `mean_t(max_competitor_phone_logit(t) - max_expected_class_logit(t))`. frame 선택 창·토큰 클래스·float reduction을 계약에 고정한다. 기존 P1의 observed-run margin과 다른 feature이며 threshold를 이식하지 않는다.
- CLEAR/FLAGGED 판정은 기대 category/role 및 operation을 지원하는 보정 정책에서만 한다. 검증된 임계값이 없으면 UNKNOWN이다. token 일치만으로 matchedClear=4를 부여하지 않는다.
- 신규 정책은 기존 여섯 상태와 호환되도록 MATCH와 FLAGGED를 독립 축으로 유지한다. 데이터상 특정 조합이 나오지 않는다는 이유로 임의 상태를 만들어 채우지 않는다.

### DELETION

- edit graph의 유일 삭제 후보만으로 발음 누락을 확정하지 않는다. CTC가 단순히 음소를 출력하지 못한 경우와 구분해야 한다.
- 같은 단어 내 좌우 기대 이웃의 유일하고 순서가 맞는 관측 anchor, 평가 가능한 사이 구간, UNK/역할 충돌 부재, 검증된 deletion 판정 정책이 모두 있어야 한다.
- blank 구간이나 MFA의 짧은 구간만으로 삭제 판정하지 않는다. 어두·어말의 한쪽 anchor만 있는 경우, 연속 삭제, 반복 음소로 인한 다중 대응은 초기 지원 범위 밖이며 UNKNOWN이다. 후속 검증 없이 자동 0점 처리하지 않는다.
- 검증된 삭제는 음소 evidenceUnits=0, coverage에서도 미대응으로 센다. deletedClear/deletedFlagged는 산술이 모두 0이므로 Native 전용 집계에서는 `deleted` 하나로 보존하고 adapter에서만 정해진 동일 산술로 변환한다. 검출기 오류 확률을 생성하지 않는다.

### UNKNOWN

예: MULTIPLE_OPTIMAL_ALIGNMENTS, ROLE_CONFLICT, UNK_IN_REQUIRED_REGION, FRAME_REGION_UNAVAILABLE, TOKEN_MAPPING_UNVERIFIED, ASSESSMENT_POLICY_UNCALIBRATED, DELETION_UNRESOLVED.

표본 하나라도 UNKNOWN이면 해당 요청의 정식 9항목 overallScore는 UNSCORABLE이다. 불확실한 표본을 clear/삭제로 몰거나 분모에서 제외해 점수를 높이지 않는다. 개발용 부분 통계는 사적으로 보존할 수 있지만 공식 점수로 공개하지 않는다.

## 8. 내부 Evidence 계약

사적 artifact `nativeScoringEvidence`를 core/H5 binding에 digest로 결속한다. 기존 `core_result.pronunciation`의 구버전 error_score 형식을 흉내 내지 않는다. 구체 schema 파일은 구현 단계에서 양쪽 verifier와 함께 추가한다.

| 필드 | 타입·필수 여부 | 의미 |
|---|---|---|
| schemaVersion, status | 필수 string / READY 또는 UNSCORABLE | 집계 가능한 근거 여부 |
| requestId, executionId | 필수 UUID | 동일 시도 |
| sourceAudioSha256, preparedWavSha256, scriptSha256 | 필수 SHA-256 | 입력 결속 |
| coreManifestSha256, modelRevision, modelWeightsSha256, vocabularySha256 | 필수 identity | 실제 source/model |
| expectedPhoneRevision, inventoryRevision, alignmentRevision, assessmentRevision | 필수 string | 해석 규칙 |
| assessmentPolicySha256, rubricRevision | 필수 identity | 판정·산술 버전 |
| device, dtype, inferenceCount | cpu, float32, 1 | 실행 사실; 전송값만 믿지 않고 producer receipt와 대조 |
| expectedCount, positions | 정수 / 배열 | 전체 기대열; 연속 expectedIndex, 정확히 N개 |
| observedRuns, insertions | 배열 | 원관측 run과 별도 삽입 근거 |
| failureReasons | 배열 | UNKNOWN 및 집계 실패 사유 |

위치 행은 expectedIndex, sourcePosition, expectedPhone, criterionId, role, permittedTokenClassIds, possibleRunIds, selectedRunId(nullable), alignmentOperation, acousticAssessment, reasonCodes, nativeFrameSpan(nullable), mfaSpan(nullable), featureDefinitionRevision, features(nullable), assessmentRuleId(nullable)를 갖는다. nullable 이유는 reasonCodes로 설명한다. 시간 범위·ID 참조·유한 수치·고유 대응을 검증한다.

소프트맥스 값이 있더라도 발음 정확도 확률이라고 명명하지 않는다. features는 모델 관측이다. 중앙 verifier가 저장된 근거에서 판정·집계·산술을 재계산할 수 있도록 feature window와 클래스별 충분 통계·정렬 후보를 보존한다. 원 logits는 비공개 artifact로 결속하고 보존 정책·용량을 따르며 API 응답에 넣지 않는다. 검증을 위해 Native 모델을 다시 실행하지 않는다.

## 9. 9항목 계산 계약

사용자 배점은 유지한다: 모음 25, 평음/경음/격음/마찰음/파찰음/비음 각 10, 유음 5, coverage 10.

기존 승인 산술도 유지한다.

| Native 판정 | 근거 단위 |
|---|---:|
| MATCH + CLEAR | 4 |
| MATCH + FLAGGED | 2 |
| SUBSTITUTION + CLEAR | 3 |
| SUBSTITUTION + FLAGGED | 1 |
| 검증된 DELETION | 0 |
| UNKNOWN | 계산 금지 |

- coverageUnits = 4 × (expectedCount − verifiedDeletedCount). MATCH/SUBSTITUTION만 대응으로 센다. 음향 CLEAR/FLAGGED와 무관하게 구조적 대응 범위다.
- category n=0이면 level=null, weight 제외. n=1도 동일 기준이다.
- level: 비율 95/80/60/40% 이상이면 4/3/2/1, 나머지 0. 정수 최소 단위는 ceil(percent×4n/100), 높은 단계부터 비교한다.
- overallScore = 100 × Σ(weight×level) / (4×Σ(applicableWeight)). 최종 값만 소수점 첫째 자리 HALF_UP이다.
- 계산기와 GPT가 같은 RUBRIC_RESULT를 사용한다. GPT의 독자 산술·누락 항목 추정·추가 필드는 거부한다. 원응답 실패·불일치 때 임의 점수로 대체하지 않는다.
- 9항목의 명칭은 유지하지만 신규 revision과 모델 관측 한계를 기록한다. 과거 Seungun 점수를 새 revision으로 재표시하거나 자동 재채점하지 않는다.

## 10. 호출·실패·보존 경계

- 신규 근거가 READY일 때만 GPT 채점을 호출한다. 정책 미검증·UNKNOWN이면 GPT를 불러 판정을 대신시키지 않는다.
- 입력 자체가 ACCEPT가 아니면 기존 NOT_REQUESTED/INPUT_NOT_ACCEPTED 규칙을 유지한다.
- ACCEPT이나 신규 근거가 UNSCORABLE이면 현재 fail-closed 계약에 따라 정식 점수/완료를 허용하지 않는다. 이 설계는 '점수 없이 성공'이라는 별도 제품 변경을 승인하지 않는다. 따라서 충분한 지원 범위를 검증하기 전에 운영으로 전환하지 않는다.
- GPT 코칭과 채점은 근거 완성 후 독립적으로 진행할 수 있지만, 현재 H5의 채점 실패 시 코칭 미실행 동작과 호출 비용이 달라질 수 있으므로 첫 전환에서는 기존 순서를 유지한다. 병렬화는 별도 변경이다.
- RunPod가 근거와 공식 점수를 검증한 후 기존 결과 우선 공개 경로를 사용한다. AWS의 모델 재실행·동기 B2 업로드를 추가하지 않는다. 인증·소유 관계·실행 바인딩·payload/schema 검사는 유지한다.
- 보존 및 독립 재계산은 비동기로 수행한다. 불일치를 발견하면 해당 revision의 신규 접수 중지·관측·운영 정책에 따른 결과 상태 처리를 한다. 이미 공개한 결과의 삭제/교정 정책은 별도 운영 결정이며 임의로 덮어쓰지 않는다.

## 11. 보정·검증 계획과 release gate

현 자료만으로 모음·삭제용 threshold 숫자를 정하지 않는다. 모든 새 threshold의 초깃값은 '미정/비활성'이며 0이나 P1 값으로 채우지 않는다.

1. 모델 학습 label inventory와 기대열 mapping을 확정한다. 17개 모음 관측 클래스의 의미·식별 한계를 검증한다.
2. 허용된 평가 음성에 전문가의 위치별 match/substitution/deletion/불확실 label을 확보한다. 단순 '일부러 틀리게 읽음' 지시만으로 모든 해당 위치의 gold를 확정하지 않는다.
3. 화자·대본·원본 녹음 단위로 development/calibration/held-out을 분리한다. 같은 원본의 파생물·정상/오류 쌍이 분할을 누수하지 않게 한다. 기존 26개 demo와 testvideo1은 독립 최종 성능 근거로 재사용하지 않는다.
4. category/role/operation별 false flag, false clear, 삭제 정밀도·재현율, UNKNOWN 비율, 전체 요청 UNSCORABLE 비율과 신뢰구간을 보고한다. 희소 category에 임의 threshold를 배포하지 않는다.
5. 보정 데이터에서 feature·threshold·경계 연산자·지원 범위를 고정하고 held-out을 한 번 평가한다. 승인할 품질 한도와 최소 표본 수는 제품 품질 책임자가 평가 전에 선언한다. 아직 숫자가 정해지지 않았다는 상태를 숨기지 않는다.
6. 모델 관측 클래스 내 구별 불가능한 모음은 검증 범위에 명시한다. 이를 정확하게 구별했다고 평가하지 않는다.
7. Native/P1 기존 코칭, MFA, prosody 동등성 및 신규 증거 재계산의 결정성을 개발자가 확인한다. 동등성은 기존 Seungun 점수와 숫자가 같다는 조건이 아니다.

Release gate: mapping 검증 완료, 모든 활성 assessment rule의 보정·held-out 승인, 품질 한도 충족, 신규 identity/schema 양쪽 등록, 구버전 실행·fallback 0, 단일 Native forward, bounded worker 생명주기 검증. 하나라도 미완료면 readiness를 열지 않는다.

본 작업에서는 자동 회귀·실제 추론·모델 학습·새 계측을 실행하거나 생성하지 않았다. 위 내용은 개발자 QA 계획이다.

## 12. 구현 순서와 성능 연결

1. 순수 DTO/음소 mapping/집계 산술을 모델 구현에서 분리하고 Native 증거 계약을 구현한다. 구버전 inference 모듈을 import하지 않게 한다.
2. 기존 Native forward 결과를 P1 및 신규 evidence adapter에 전달한다. P1 전용 logits 재계산, 두 번째 모델, forced target decode를 추가하지 않는다.
3. 위 gate를 위한 개발자 검증과 보정을 수행한다. 검증 전에는 개발 산출물만 생성하고 운영 채점 경로를 바꾸지 않는다.
4. 신규 core/H5를 별도 immutable release로 포장한다. 구버전 source/loader/assets를 패키지에서 제거하고 bootstrap·일반 producer·resident producer의 실행 allowlist를 신규 identity로 제한한다. 과거 artifact는 읽기 검증만 허용한다.
5. BE/FE가 새 revision과 실패 상태를 읽도록 반영한다. 기존 데이터의 provenance는 보존한다. 모델 교체 때문에 DB에 logits를 직접 넣는 schema를 추가하지 않는다.
6. Native 전용 bounded resident에 연결한다. Native/MFA 준비를 병렬 수행하고 readiness 이전에 합류한다. version·model load·immutable digest 검증은 세대별로 한 번, 입력 검증은 요청별로 수행한다. 최대 3회와 watchdog은 유지한다.
7. cold/first/warm/recycle 구간을 나누어 재측정한다. 149.621초 준비 비용의 반복 제거를 확인하며 전체 15초를 선험적으로 보장하지 않는다. 신규 정렬·판정 비용도 별도 span으로 보고한다.

구버전 제거 요청은 유지된다. 현재 문서 완료는 소스 삭제나 배포 완료가 아니며, 미결정 점수 선택은 해소됐지만 검증되지 않은 신규 판정 규칙을 운영에 넣을 권한으로 해석하지 않는다.
