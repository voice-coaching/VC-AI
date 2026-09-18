# 계층형 발음 평가 v3 구현 및 검증 인계

상태: 구현, 배포 전. 모델 가중치와 동결 detector threshold 변경 없음.

## 재검토 결과와 구현 범위

- 카탈로그는 11개 대분류 / 49개 소분류다. 음성 기대 표지40개와 coverage1개는 기존 6상태 근거로 계산한다.
- 부모9개는 자식 원시 개수를 합산하고 기존 배점/5등급으로 계산한다. 자식 등급 평균이나 부모·자식 이중 합산을 하지 않는다. 기존 overallScore와 수치 호환을 유지한다.
- 입술8개는 미채점으로 고정한다. 현재 승인된 선택 구간의 inner/outer aperture/area 비율4개만 관측값으로 연결한다. 시간적 움직임4개는 새 계측·검증 전 UNAVAILABLE이다. 따라서 전체 입술49항목의 검증된 채점 기능이 완성됐다는 뜻은 아니다.
- 문장 전체의 영상 다구간 계측, 전문가 참조, 입술 적합성 점수 및 통합 총점은 아직 미구현이다. 기존 single-selected-phone 경로를 전체 문장 평가로 포장하지 않는다.

## CLOVA 책임과 용량

채점 모드 전용 v3 프롬프트는 `configs/pronunciation_feedback/prompts/scoring/active-hierarchy.md` → `pronunciation-hierarchy-v3.md` symlink를 신뢰 로더가 읽어 주입한다. v2의 active.md는 보존한다.

512토큰 안에 전체 항목 설명·점수를 복제하는 대신 CLOVA가 반환하는 값은 rubricRevision, overallScore, attentionCriterionIds(최대3개)다. 전체 점수는 검산기가 결정하고 CLOVA는 실제 채점된 level<4 음성 항목 중 우선 확인 대상을 고른다. eligible이 있으면 최소1개, 없으면 빈 배열만 허용한다. 이 선택은 기존 Seungun 선택 음소나 승인 피드백을 변경하지 않는다.

전체 음소 근거는 compact counts 배열로 전달한다. 정상적인 구조·총점·선택 항목만 검증하므로 자유로운 원인 설명을 생성하지 않는다. 사용자 항목별 설명은 저장된 개수로 백엔드가 구성하고 기존 grounded summaryFeedback 경로를 보존한다. LLM 실패는 기존 채점 fail-closed 그대로다.

출력 제한은512토큰을 유지했다. 실제 모델의 최대 입력 토큰/최악문장 tokenization 및 지연은 아직 실측하지 않았다. 서버의 기존 입력 토큰 제한을 우회하지 않으며, 개발자 검증 후 활성화한다.

## 설정과 호환

새 opt-in: `AI_ANALYSIS_CLOVA_RUBRIC_REVISION=clova-phone-rubric-v3`.
기본값은 `clova-phone-rubric-v2`이며, 기존 SCORING_ENABLED 설정은 별도로 필요하다. 설정만 변경하기 전에 반드시 v3 수신 계약을 가진 BE가 먼저 배포돼야 한다. 지원하지 않는 revision/깨진 prompt 참조는 시작 시 거절한다.

HTTP 결과 envelope는 `voice-coaching.runpod-analysis-result.v2`를 유지하고 scoringEvidence.rubricRevision에 v3를 추가한다. 기존 criteria9개에 phoneCriteria40개, visualObservation, attentionCriterionIds를 추가한다. 양쪽 JSON Schema 정본은 동일해야 한다. v1/v2 저장 결과는 유지하고 역산/backfill하지 않는다.

입술 status는 영상이 없으면 NOT_PROVIDED, 유효 관찰이 없으면 UNAVAILABLE, 관찰이 있으나 적합성 기준이 없으면 NOT_VALIDATED다. raw landmarks·영상·비밀값은 전송하지 않는다. 선택 인덱스·구간·supplement digest로 묶인 관측만 전송한다. public hierarchy에는 digest를 내보내지 않는다.

BE 공개 `scoreHierarchy`는 null 또는 rubricRevision/leafCount/groups 구조다. Group은 id,label,description,maxScore,score,items를 갖는다. Item은 id,label,description,status,sampleCount,level,normalizedScore,attention,observation을 갖는다. 관측은 value/unit/selectedExpectedIndex/videoStartMs/videoEndMs다. 음성 자식 normalizedScore는25×level이며 총점에 별도로 합산하지 않는다. 영상에는 sampleCount·level·score를 임의 생성하지 않는다.

프론트는 새 hierarchy가 있으면 대분류를 펼쳐 소분류와 근거를 표시하고, 없으면 기존9항목 화면을 사용한다. 실제0점, 기대 음소 없음, 영상 없음, 계측 부족, 기준 미검증을 구분한다.

## 개발자 검증 순서

AI 저장소 AGENTS에 따라 자동 회귀 테스트를 추가/실행하지 않는다. 다음은 개발자가 수행할 확인 항목이며 완료 기록이 아니다.

1. ㅏ 기대4개 중 matchedClear3/matchedFlagged1: 음소75/100, 모음18.75/25, coverage10/10, overall82.1. 기존 v2와 비교.
2. 기대 음소가 없는 자식은null. 기대4개가 모두deleted이면 해당 자식은0. 누락된 근거를 N/A로 바꿔 총점을 높이지 않는지 확인.
3. 자식 counts 합과 부모 불일치, 중복/미지원 ID, 잘못된 rubric digest, 잘못된 attention 선택, JSON 추가필드, 생성 잘림을 거절하는지 확인.
4. 영상없음, 영상있지만측정없음, 유효static4관측, mismatched anchor/digest를 각각 확인. 관측값이 총점에 영향을 주지 않아야 한다.
5. 가능한 표지40개를 모두 포함한 입력의 실제 tokenizer 길이, 모델 JSON 반환, end-to-end latency를 확인. 기존512토큰 출력/실행 deadline 안에서 동작해야 한다.
6. v2 기본경로, v3 opt-in, 기존 분석 조회, 캐시 직렬화, APPLIED/DUPLICATE, 승급 API 저장총점 호환, 프론트 키보드·마우스·터치 확인.

## 배포/롤백

BE 호환 reader와 스키마 → AI 코드 배포(기본v2) → 검증 환경에서v3 opt-in → 프론트 순으로 진행한다. 현 단계에서 운영 환경파일을 바꾸거나 재시작하지 않았다. v3 비활성화 시 신규 작업을v2로 복귀시키되 이미 저장된v3 결과를 읽는 BE는 유지해야 한다. v3 실행/재전송 중 구형BE로 바로 롤백하면 안 된다.

입술 적합성 점수 활성화는 다구간 계측과 전문가 기준 승격 이후 별도 작업이다.
