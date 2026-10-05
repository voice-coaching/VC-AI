# Native 전체 음소 채점 구현 현황

2026-10-04. 작업 브랜치: `refactor/native-only-core-20261004`, 기준 main: `bbaf5f43a91c925781bd6ac4c0ba75d56f8a298a`.

**소스 구현과 정적 확인을 완료한 DRAFT다. 운영 전환·추론 품질·속도 개선을 검증한 릴리스가 아니다.** 임계값과 모음 매핑의 보정 자료가 없어 신규 실행 승인 목록은 비어 있다. 이 작업본을 그대로 운영에 배포하면 신규 canonical 실행이 준비 상태를 통과하지 못한다. 현재 운영 환경은 변경하지 않았다.

설계 정본: [전체 음소 설계](native-full-phone-scoring-design-20261004.md), [초기화 지연 계획](native-only-latency-plan-20261004.md).

## 구현한 경로

1. 기존 Native CPU FP32 forward의 logits/argmax를 `native_core_adapter.py`에 전달한다. 추가 모델 추론은 없다. P1 코칭 정책과 threshold는 변경하지 않았다.
2. `native_phone_contract.py`는 full g2pK metadata를 사용한다. 초성 ㅇ 제외 사유와 원래 index를 보존한다. ㅙ·ㅞ를 분해하지 않고, 21개 모음 표지를 각각 한 표본으로 취급한다. source word 대응이 모호하면 UNKNOWN이다.
3. `native_phone_alignment.py`는 CTC blank/repeat 규칙과 모든 최적 edit 정렬의 대응 후보를 보존한다. 임의 tie-break를 하지 않는다. UNK/특수 토큰을 정상 음소로 바꾸지 않는다. O(N×M), 두 uint32 DP 배열, 최대 1,000,000 cells로 제한한다. 원본 단어 provenance DP도 같은 상한을 적용한다.
4. `native_phone_assessment.py`는 버전 고정 expected-class margin, frame window, category/role/operation별 정책을 적용한다. E/iE/oE 대응은 검증 문서 digest가 없으면 활성화하지 않는다. 임계값을 구형 검출기/P1에서 가져오거나 임의 숫자로 채우지 않았다.
5. `native_phone_evidence.py`는 요청·execution·오디오·대본·core/model/vocab/policy identity를 결합한다. 같은 단어의 연속된 좌우 MATCH anchor가 없는 삭제 후보는 확정하지 않는다. MFA word span은 위치 교차 확인에만 사용한다. 어느 기대 표본이든 UNKNOWN이면 전체 점수는 UNSCORABLE이다.
6. `native_gpt_rubric.py`가 9항목 서버 계산기를 사용한다. 배점, 4/2/3/1/0 근거 단위, coverage, 정수 단계 경계와 최종 HALF_UP을 유지한다. 새 revision은 `phone-rubric-native-v1`. GPT가 응답한 숫자를 검산표와 정확히 대조하며 누락·불일치를 점수로 대체하지 않는다.
7. 결과 공개 경로에 B2 저장이나 추가 모델 추론을 넣지 않았다. raw FP32 logits는 공개 DTO/GPT 입력이 아니라 기존 비공개 CORE artifact의 `nativeScoringLogits`에 넣어 비동기 인계한다. 기존 archive cleanup으로 근거가 사라지지 않도록 별도 임시 파일에만 남기지 않는다. Backend offline verifier는 이 bytes로 feature·정렬·산술을 재계산한다.

## 구형 실행 제거와 resident

- 구형 `build_canonical_scored.py`, `build_canonical_resident.py`, `harnesses/canonical_scored_bridge.py`를 삭제했다. 신규 빌드는 `build_canonical_native.py`만 사용한다.
- 새 core에서 `final_pronunciation_detector.py`, `final_pronunciation_artifacts.py`, `xlsr_pronunciation.py`, `expected_phone_parity.py`, `pronunciation_acceptance.py`를 제거했다. 한글 표지 테이블과 full metadata는 추론 의존성 없이 분리했다.
- pipeline의 구형 runner·예열·호출을 제거했다. 옛 설정은 enabled=False만 허용한다. 기존 DTO의 미사용 pronunciation 필드는 `not_requested`, detector는 `RETIRED_NOT_EXECUTED`, 옛 weights는 null로 기록한다. Native 근거를 옛 error_score로 위장하지 않는다.
- 이전 릴리스의 frozen 파일과 source identity는 **과거 결과 읽기·검증용**으로 유지한다. 새 producer와 readiness는 `require_native_execution()`으로 구형 identity를 거절한다.
- Native 전용 resident에서 모델/weights 확인, MFA version/start 결과를 세 요청 동안 재사용한다. Native 준비와 MFA bootstrap은 겹쳐 수행하며 둘 다 준비된 뒤 READY다. 세 요청 후 generation을 종료하고 새로 준비한다. watchdog/cleanup은 유지한다. one-shot fallback은 제거했다.
- 실제 Native/MFA 추론을 동시에 실행하는 기존 금지 조건은 유지한다. 최초 준비, 첫 요청, warm 요청, recycle 비용은 아직 실측하지 않았다.

## 신규 패키지와 계약

| 항목 | 값 |
|---|---|
| core directory | `canonical_native_core_20261004_v1` |
| H5 directory | `canonical_native_h5_20261004_v1` |
| core manifest SHA-256 | `9e2a2107db7d30fe8910c8984687974d85b95120760e2bcce53d57640a5d5a32` |
| H5 manifest SHA-256 | `358365399f7445ab0a77797ce46619979955503c4faac8185724e2225aa5205c` |
| H5 dependency lock SHA-256 | `798b47b6f5355b120141a7fa730350739298082549036233ea5804226dbad80c` |
| calibration status | `DRAFT` |
| approved execution identities | 없음 |

위 SHA는 소스 검토/읽기 계약에 등록된 DRAFT이며 실행 승인이 아니다. 정책이나 소스가 바뀌면 새 immutable directory/revision을 만들고 SHA를 양쪽에 등록해야 한다. 기존 디렉터리를 덮어쓰면 builder가 실패한다.

`native_phone_evidence.schema.json`, `native_logits.schema.json`과 parent/private 계약, v5 결과의 새 rubric revision을 반영했다. 공유 검증기가 과거 v4 artifact도 읽으므로 v4 스키마의 identity 등록도 갱신했지만 v4 신규 접수를 재도입하지 않았다.

RunPod의 실제 vocab/added_tokens를 읽어 45개 ID와 `[UNK]`=41, `[PAD]`=42를 대조했다. Native interpreter의 NumPy는 `2.4.6`이었다. feature reduction과 archive 재계산은 이 버전을 요구한다. `prepare_backend_verifier_env.py`는 기존 환경을 덮어쓰지 않는 `py312-jsonschema4251-rfc014-numpy246-native` 환경을 준비하도록 변경했다. 실제 AWS 설치/전환은 하지 않았다.

## 확인 범위

- 변경 Python 28개 컴파일 및 Python 3.12 문법 확인.
- 패키지 Python 79개 컴파일; core는 Python 3.11, H5는 3.12 문법 확인.
- JSON Schema 5개 정적 유효성, Native identity의 v4/v5 closed tuple 등록, 패키지 모든 source digest 확인.
- `git diff --check` 통과.
- Backend: 최신 develop `836e49a`에서 만든 `feat/native-score-contract-20261004`의 `compileJava --offline` 통과. FE: 최신 dev `aecddad`에서 만든 같은 이름의 별도 브랜치에서 TypeScript `--noEmit --incremental false` 통과.
- 모델 추론·GPT 호출·자동 회귀 테스트·브라우저 QA·속도 재측정은 하지 않았다. AGENTS.md의 개발자 QA 원칙을 따른다. 위 항목은 실제 동작/정확도 QA 통과를 뜻하지 않는다.

## 운영 전환까지 남은 조건

1. E/iE/oE label inventory와 독립 annotation을 확인한다. 모음·자음·삭제 category/role/operation별 calibration/held-out 자료, 허용 오류 기준, 검토 digest로 정책을 완성한다. 현재 `native_phone_assessment_v1.json`의 rules/mappingApprovals는 빈 배열이다.
2. 개발자가 Native/P1/MFA 출력 보존, UNKNOWN/삭제/반복 음소, feature 재계산, 채점 경계, cold/first/warm/recycle 및 강제 종료를 검증한다. 검증 뒤 새 릴리스 identity를 `APPROVED_NATIVE_EXECUTIONS`에 등록한다.
3. BE/FE 작업본에는 새 schema SHA·rubric revision·identity를 반영했고 기존 실패 상태를 유지했다. FE에는 이전 방식과 점수 비교 불가·일부 모음 관측 범주 통합 안내를 추가했다. 로컬 작업 위치는 `CreatorTemp/backend-native-score-20261004`, `CreatorTemp/frontend-native-score-20261004`다. 세 저장소의 커밋·푸시·PR·배포는 수행하지 않았다. 최종 보정 정책으로 identity가 바뀌면 세 작업본도 함께 갱신한다. verifier exporter/stager에는 새 패키지와 import closure를 추가했다.
4. 신규 접수를 끈 상태에서 새 verifier 환경/패키지와 RunPod를 배포하고 양쪽 readiness를 확인한다. FE 지원이 확인된 뒤 신규 요청을 전환한다. 원격의 구형 자료는 실행 허용 대상에 다시 넣지 않는다.

보정 자료 없이 점수를 만드는 우회나 구버전 fallback을 추가하지 않는다. 사용자가 승인한 전체 음소 채점으로 서비스하려면 위 자료와 검증이 필요하다.
