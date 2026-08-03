# R04 블라인드 다중 참조 평가 데이터·모델·개인정보·통계 카드

## 1. 현재 판정

- 작업 단위: WP7 R04, 블라인드 다중 참조 인간 평가
- 구현 범위: `production_static_preflight_only`
- 현재 완성도: `C=1, H=0, M=0`
- 실제 청취자 모집, 오디오 접근, 무작위화, 평정 수집, 모델/GPU 실행, MOS·CMOS·통계 계산, 우월성 판정, 결과/registry/commit 생성: 모두 `0`
- 논문 또는 연구 완료 주장: 불가

이 카드는 실행 결과 카드가 아니다. 미래 외부 인간 평가가 지켜야 할 데이터 구조와 개인정보·통계 경계를 정적으로 고정한다.

현재 성인 청취자 평가는 기관 승인이 필요한 참여자 연구가 아니라 개인 연구다.
기관/IRB 승인은 선행 gate가 아니며, 개인 연구 scope attestation, 청취자 개별
동의·자격 attestation, 데이터 라이선스, privacy, blind, preregistration,
pilot/SAP를 요구한다. 신규 참여자 모집·중재·RCT·학습자 효과 검증을 선택할
때만 H00 참여자 연구 경로를 적용한다.

## 2. 예정 입력 데이터

### 2.1 R03 참조 bank

- 예정 경로: `data/processed/references/r03/r03_reference_bank_manifest_v1.json`
- 등급: `D1_pseudonymized_restricted_experiment`
- 문장: R00의 고정 20문장
- 문장별 후보: 3–5개
- 필요한 source lineage: `human_recording`, `cosyvoice_tts`
- R02 hard gate 실패 후보: R04에서 복구하거나 MOS로 상쇄할 수 없음

현재 R03 completion commit, registry, bank는 없다. R03 completion SHA와 completion/registry schema version은 `TBD_R03_COMPLETION`이다. 따라서 R04는 R03 D1 경로를 열지 않는다.

### 2.2 개인 연구 평가 계층

다음 artifact가 순서대로 고정되어야 한다.

1. 개인 연구 scope attestation과 데이터 라이선스
2. R04 preregistration
3. pilot 분산·청취자 내 상관 aggregate attestation
4. 통계분석계획(SAP)
5. 모든 confirmatory listener의 개별 동의·pseudonymous attestation

승인 artifact에는 이름, 이메일, 전화번호, 주소, 자유서술을 저장하지 않는다. 서명 증거는 역할과 SHA-256 receipt로만 결합한다.

## 3. 데이터 모델

### 3.1 무작위화 manifest

스키마: `configs/reference/r04_randomization_manifest_v1.schema.json`

- zone: D1
- 자극 키: 불투명 `stimulus_blind_id`
- lineage: audio object/content SHA와 R03 candidate-lineage SHA
- listener 키: `listener_id_sha256`
- 무작위화 단위: 청취자별, 문장 내부
- 피로 통제: 최소 2개 block, 마지막 block 전 의무 휴식
- MOS assignment: 문장×불투명 후보×문장 내 위치
- CMOS assignment: 같은 문장의 서로 다른 좌·우 불투명 후보

스키마는 system name, filename/path, 합성 여부, source label, rating 필드를 허용하지 않는다. block 크기, 같은 문장 자극의 최대 연속 수, 휴식 정책은 preregistration에서 확정하기 전까지 `TBD_PREREGISTRATION`이다.

### 3.2 raw rating manifest

스키마: `configs/reference/r04_rating_manifest_v1.schema.json`

- zone: D1
- MOS unit: `listener_x_sentence_x_candidate`
- MOS 범위: 정수 1–5
- MOS 4차원:
  - `naturalness`
  - `clarity`
  - `target_phone_accuracy`
  - `pedagogical_suitability`
- CMOS unit: `listener_x_sentence_x_same_sentence_pair`
- CMOS 범위: 정수 -3–3
- 각 row는 승인된 listener, 사전 잠금된 assignment, 문장, 불투명 자극/쌍과 정확히 결합되어야 함
- 자유서술, 직접식별자, candidate ID, source/system/synthesis label, 오디오/소스 경로는 금지

rating validator는 범위·중복·assignment·attestation·20문장 coverage만 검증한다. 평균 MOS, CMOS 효과, 신뢰구간 또는 우월성을 계산하지 않는다.

### 3.3 aggregate 분석

- 예정 경로: `artifacts/experiments/r04_blind_multireference_evaluation_v1/r04_aggregate_analysis_v1.json`
- zone: `A1_aggregate_only`
- 개별 rating, listener/stimulus ID, 오디오 hash/path를 포함할 수 없음
- 별도의 승격 검토 전 공개 불가

현재 aggregate 스키마와 실제 artifact는 없다. exact estimand와 분석계획이 승인되기 전 aggregate 결과 형식을 고정하면 사후적 결과 선택 위험이 있으므로 생성하지 않았다.

## 4. 모델 및 오디오 경계

- R04 패키지는 새 모델을 학습하거나 추론하지 않는다.
- CosyVoice TTS는 R01→R02→R03에서 lineage가 검증된 미래 입력일 뿐 R04 런타임 모델이 아니다.
- 실제 모델 접근 수: 0
- 실제 GPU 접근 수: 0
- 실제 오디오 read/write 수: 0/0
- 블라인드 manifest에는 재생 endpoint나 파일 경로를 쓰지 않는다.
- 오디오 object resolver와 rating collector는 개인 연구 scope attestation·preregistration·pilot·SAP·listener attestation을 모두 통과한 별도 등록 executor에서만 사용할 수 있다.

## 5. 개인정보 및 블라인드 정책

`listener_id_sha256`은 익명화가 아니라 가명처리 키다. raw rating과 attestation은 D1에 유지한다.

금지 항목:

- 이름, 연락처, 주소, 법적 이름, 원시 listener ID
- 자유서술, comment
- source/audio/recording path와 filename
- system/model name
- `source_kind`, human/TTS label, 합성 여부
- R01 candidate ID

청취자에게는 system 이름, filename, 합성 여부를 숨긴다. source kind는 R03 bank의 제한된 lineage 검증에만 사용하고 randomization/rating manifest로 전달하지 않는다.

## 6. 통계 계약

근거로 고정할 수 있는 항목:

- 목표 power: `0.80`
- 양측 alpha: `0.05`
- primary: ordinal mixed-effects 계열
- random effects: listener와 sentence 모두 필수
- bootstrap: secondary만 허용

승인 전 고정할 수 없는 항목:

- pilot variance와 intra-listener correlation
- listener 수 `N`
- exact primary estimand
- ordinal link와 fixed/random-effects 상세식
- CI 정의
- bootstrap resampling unit, 반복 수, seed, CI 방법
- multiplicity 전략
- superiority margin과 comparator
- rater 모집·제외·품질관리 정책
- adjudication 정책

이 값은 `TBD_PREREGISTRATION` 또는 `TBD_EXTERNAL`로 남으며, 하나라도 미해결이면 D1 오디오·rating 경로 전에 fail closed한다.

## 7. 품질·보안 검증

- JSON/YAML duplicate key 및 비유한 수 거부
- workspace-relative path만 허용
- symlink/path traversal 거부
- SHA-256 exact binding과 read 중 TOCTOU snapshot 검증
- 미래 output no-overwrite 및 atomic publish 정책 검증
- randomization에서 문장별 3–5개, 정확히 20문장, 모든 listener의 전 후보 1회 MOS 평가 검증
- 같은 문장 CMOS pair, 피로 block, 의무 휴식 검증
- raw rating의 MOS/CMOS 범위, 중복 unit, randomization 및 listener-attestation binding 검증

## 8. 비주장

현재 다음 값은 존재하지 않으며 0 또는 미정이다.

- 실제 listener 또는 평가 세션
- 실제 randomization
- 실제 MOS/CMOS rating
- MOS/CMOS 평균·효과
- CI, p-value, bootstrap 결과
- 우월성/열등성/동등성 판정
- completion, registry, commit
- 논문 삽입용 실험 결과
