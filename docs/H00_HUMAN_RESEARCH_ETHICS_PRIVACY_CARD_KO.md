# H00 인간대상연구 윤리·개인정보 카드

최종 갱신: 2026-07-29 KST  
상태: **기관/IRB 또는 동등한 공식 판단을 기다리는 정적 준비 계약**

## 1. 현재 판정과 비주장

- 작업 단위: WP9 H00
- 구현 범위: `production_static_preflight_only`
- 현재 완성도: 정적 준비 100%, 실제 외부 승인·인간대상연구 0%
- 공식 승인서, 면제/비심의 판단서 또는 동등한 기관 판단: 없음
- 기관 portal 조회·제출, 기관 연락, 공식 판단 생성: 모두 0
- 참여자 모집·동의·무작위화·개입·녹음: 모두 0
- 개인정보·민감정보·참여자 row·오디오 접근: 모두 0
- 모델·GPU·TTS·외부 전송과 output/registry/commit 작성: 모두 0

이 패키지는 승인, 면제, 심의 불필요 또는 법률 적합성을 추론하지 않는다.
연구팀이 작성한 체크리스트, 계획서 또는 자체 서명은 기관의 공식 판단을
대신하지 않는다. 실제 공식 근거가 없으므로 H00 상태는
`TBD_H00_APPROVAL`이며, H00 완료나 참여자 작업 개시를 주장할 수 없다.

## 2. 예정 연구 범위

공식 판단이 명시적으로 포함해야 하는 학습자 연구는 다음 두 개의 독립
병렬 비교다.

1. `C3 − C0`: active control 대비 근거 제한 구조화 진단·코칭
2. `C5 − C3`: 구조화 코칭 대비 다중 참조음과 coached reattempt loop

두 비교 모두 participant 단위 1:1 무작위 배정, L1과 baseline
comprehensibility band 층화, allocation concealment를 사용한다. 개입은
2주이며 평가는 pre, 2주 종료 시 post, post 4주 후 retention 순서다.
평가자는 condition·timepoint·participant identity에 대해 blind여야 한다.
개입 차이를 참여자가 알 수 있으므로 participant blinding은 주장하지
않는다.

C0/C3/C5의 버전과 SHA-256이 있는 intervention manual은 모집 전에
동결해야 한다. 공식 판단 범위는 최소한 다음 실행을 각각 명시해야 한다.

- 참여자 모집과 eligibility 확인
- 설명·동의 및 명시적 음성 녹음 동의
- participant 단위 무작위 배정과 2주 개입
- pre/post/retention 녹음
- 독립 blind rating
- D1 가명처리 분석과 A1 집계 분석
- feasibility pilot과, 수행한다면 별도 범위의 confirmatory study

승인 범위가 하나라도 모호하면 해당 작업은 허가된 것으로 간주하지 않는다.

## 3. 동의·철회·참여자 권리

승인되는 설명문과 동의서는 연구 목적, 녹음, AI 피드백, 보존·접근·삭제,
철회, 외부 전송 가능성, 문의·신고 경로를 참여자가 이해할 수 있는 언어로
설명해야 한다.

- 동의하지 않거나 철회해도 불이익이 없어야 한다.
- 음성 녹음 동의는 명시적으로 받아야 한다.
- AI가 부정확하거나 부적절한 발음 피드백을 줄 수 있음을 알려야 한다.
- 사람이 응답하는 문의·이의제기·안전 escalation 경로가 있어야 한다.
- 철회 시 이미 수집된 자료를 보존, 삭제 또는 제한 처리하는 규칙을
  동의서와 protocol에서 일치시켜야 한다.
- 삭제 요청 접수, 본인 확인, 주 데이터와 백업의 처리 및 완료 기록을
  승인된 절차로 고정해야 한다.
- 자동 시스템은 신원 추론, 의료 판단 또는 근거 없는 단정에 사용하지
  않으며 불확실할 때 abstain해야 한다.

현재 동의서 version/hash, 철회 후 기존 자료 처리, 삭제 요청 경로와
사람 escalation 경로는 실제 기관 문서가 없으므로 `TBD_EXTERNAL`이다.

## 4. 모집·보상·미성년자

모집문과 eligibility protocol을 동결하고 과도한 영향이나 강요 가능성을
검토해야 한다. 보상 금액·지급 시점·중도 철회 시 처리 규칙은 현재
근거가 없으므로 추정하지 않고 `TBD_EXTERNAL`로 둔다.

미성년자는 다음 둘 중 기관이 승인한 한 가지 정책을 명시해야 한다.

- 미성년자를 제외한다.
- 포함한다면 법정대리인 동의와 연령에 맞는 본인 assent를 모두 포함한
  별도 보호 절차를 승인받는다.

미성년자 정책이 결정되지 않았거나, 포함하면서 guardian consent 또는
assent protocol이 없으면 모집할 수 없다.

## 5. 개인정보·보존·삭제

사람 음성은 직접식별자를 제거해도 재식별 위험이 있는 연구 데이터다.
H00은 다음 구역 경계를 적용한다.

| 구역 | 허용 내용 | 공개 가능성 |
|---|---|---|
| R0 | 원음성·동의 원본·직접식별정보 | 금지 |
| R1 | 운영 metadata·원천 경로·가명키 대응표·공식 문서 archive | 금지 |
| D1 | 연구 namespace SHA-256 가명키, 승인된 실험 자료 | 별도 승인 전 금지 |
| A1 | 최소 셀 크기와 disclosure review를 통과한 집계 결과 | 승격 검토 후 |
| C1 | 코드·schema·template·개인정보 없는 정책 문서 | 가능 |

가명키 대응표는 연구 자료와 분리한
`R1_restricted_separate_key_store`에 두고 별도 custodian 역할과 접근
기록을 둔다. 직접식별자, 참여자 row, 원음성 경로와 자유서술은 C1/A1
공개 승인 요약에 포함할 수 없다.

기관 제출본에는 다음을 구체적으로 적어야 한다.

- 수집할 개인정보·민감정보 범주와 승인 목적
- 역할별 최소권한과 접근 로그
- 보존 기간 또는 종료 trigger
- 주 데이터와 백업의 삭제 방법
- 철회·삭제 요청 처리와 audit trail
- 보안 사고 탐지, 통지, 격리와 복구 절차

현재 보존 기간/trigger, 역할표, 삭제 방법과 backup 삭제 범위는
`TBD_EXTERNAL`이다. 저장소의 template은 이 미정값을 임의로 채우지
않는다.

## 6. 외부 서비스·TTS·국외 이전

참여자 음성 또는 개인정보를 외부 모델/TTS/API로 보내는 행위는 기본적으로
허가되지 않는다. 향후 사용하려면 제공자, 처리 위치, 데이터 항목, 목적,
보존, 재처리, 삭제, 국외 이전, 최소 필요 전송 범위를 공식 판단과
동의서가 모두 포함해야 한다. 별도 전송 동의가 필요하다면 승인된 동의가
실제로 존재해야 한다.

현재 external service, TTS와 cross-border transfer 범위는
`TBD_EXTERNAL`이며 실제 전송 수는 0이다.

## 7. 사고·pause·stop·재개

다음 사건은 즉시 pause/stop trigger다.

- 기관/IRB 또는 동등한 공식 기구의 중단·정지 명령
- 동의 또는 개인정보 침해
- 예상하지 못한 중대한 개입 관련 위해
- 무작위화 또는 데이터 무결성 실패
- 등록된 intervention을 그대로 제공할 수 없음

재개에는 문서화된 공식 기구와 연구책임자의 승인이 모두 필요하다.
승인 만료, 정지 또는 철회 시에는 모집·개입·녹음을 중단한다. 범위,
manual, 전송, 보존 또는 분석이 바뀌면 먼저 amendment를 승인받고 현재
version/hash/status를 갱신한다.

## 8. 공개 승인 요약과 제한 원본 분리

공개 가능한 H00 승인 요약은
`configs/human_research/h00_human_research_approval_public_v1.schema.json`
을 따르는 C1/A1 artifact다. 실제 승인 상태에서 최소한 다음 항목을
`true`로 확인해야 한다.

- `actual_pilot_covered`
- `consent_approved`
- `privacy_approved`
- `retention_approved`
- `deletion_approved`

공개 artifact에는 기관과 판단 ID의 SHA-256, protocol·동의서·정책의
version/hash, 승인 범위와 boolean assurance만 둔다. 다음은 금지한다.

- 이름, 이메일, 전화번호, 주소, 법적 이름
- 참여자 ID·row, 동의 원본, 원음성/녹음 경로
- 승인번호 원문, 기관 portal URL, 공식 문서 경로·파일명·내용
- 자유서술, comment, 서명 이미지

공식 판단 원문은 R1의 제한 archive에 둔다. 공개 attestation에는
제한 locator 자체가 아니라 locator의 SHA-256만 기록한다. 권한 있는
custodian이 원문을 직접 열어 institution source, 서명/인장, document
hash, protocol version, scope, 만료·amendment와 현재 상태를 검증한
사실만 attest한다. 이 패키지는 제한 locator를 resolve하거나 원문을
읽지 않는다.

## 9. 실행 전 필수 증거

실제 참여자 작업 전에 다음이 모두 필요하다.

1. 기관/IRB 또는 동등한 기구의 현재 유효한 공식 긍정 판단
2. protocol·application bundle·동의서·PIA·DMP의 version/hash
3. 승인된 C0/C3/C5 manual과 allocation 절차/역할/receipt
4. 개인정보 범주, 보존·접근·삭제·backup·철회 정책
5. 모집·보상·미성년자 정책
6. AI feedback 위험, 사람 escalation과 adverse feedback monitor
7. 가명키 분리와 외부/TTS/국외 이전 정책
8. incident·pause·stop·resume 절차
9. 만료·정지·철회·amendment가 없는 현재 상태
10. 권한 있는 custodian의 제한 원본 검증 attestation

하나라도 누락되거나 `TBD`이면 모집·동의·녹음·무작위화·개입·분석을
시작하지 않는다.
