# H00 인간대상연구 공식 판단 준비도

최종 갱신: 2026-07-29 KST  
판정: **정적 fail-closed 준비 완료 / 실제 H00 승인·실행 미완료**

## 개인 연구 범위 정책

현재 저장소는 기관 소속 인간대상연구가 아닌 개인 연구를 기본 범위로 한다.
기존 라이선스·접근권한이 있는 데이터 분석과 성인 전문가·청취자의 자발적
평가에는 기관/IRB 승인을 선행 gate로 요구하지 않는다.
대신 데이터 라이선스, 개별 동의·철회·보존·폐기, 개인 연구 scope
attestation, R0/R1/D1/A1, 가명키 분리, blind와 preregistration을 요구한다.

H00은 신규 참여자 모집·중재·RCT·학습자 pre/post/retention 같은
participant-study를 선택할 때만 적용한다. 현재 H00 v1 CLI/config는
이전 기관 승인 계약을 검사하므로, 이 문서 변경만으로 실행 gate가 해제되지는
않으며 개인 연구 executor에는 별도 contract amendment가 필요하다.

## 1. 구현 산출물

| 역할 | 경로 |
|---|---|
| 정적 실행 계약 | `configs/experiments/h00_human_research_approval_v1.yaml` |
| 공개 승인 schema/template | `configs/human_research/h00_human_research_approval_public_v1.schema.json`, `configs/human_research/h00_human_research_approval_public_v1.template.json` |
| 공개 원본문서 검증 attestation schema/template | `configs/human_research/h00_official_document_attestation_v1.schema.json`, `configs/human_research/h00_official_document_attestation_v1.template.json` |
| validator | `src/voice_coach/human_research/h00_approval_contract.py` |
| read-only CLI | `scripts/prepare_h00_human_research_approval.py` |
| 윤리·개인정보 카드 | `docs/H00_HUMAN_RESEARCH_ETHICS_PRIVACY_CARD_KO.md` |
| 단위/통합 검증 | `tests/unit/test_h00_approval_contract.py`, `tests/integration/test_prepare_h00_human_research_approval.py` |

이 구현은 개인 연구 평가를 승인하는 executor가 아니다. H00 경로를
사용하는 participant-study에서는 공식 판단이 없을 때
`TBD_H00_APPROVAL`에서 fail closed한다.

## 2. 정적 계약이 고정한 범위

정본 문서와 H01 planning artifact에서 근거가 있는 항목만 고정했다.

- 두 개의 독립 비교: `C3−C0`, `C5−C3`
- participant 단위 1:1 병렬 무작위 배정
- L1·baseline comprehensibility band 층화와 allocation concealment
- 2주 intervention, pre/post/retention(post 4주 후)
- C0/C3/C5 manual version/hash와 모집 전 동결
- blind outcome rater, participant blinding 주장 금지
- 동의·철회·음성 녹음·AI 오피드백 위험·사람 escalation
- 모집·보상·미성년자 정책
- R0/R1/D1/A1 구역, 보존·접근·삭제와 backup
- 연구 namespace 가명키와 대응표 분리
- 외부/TTS/국외 이전과 별도 동의
- incident response, pause/stop/resume
- amendment·만료·정지·철회와 현재 유효 상태

공식 기관 양식, 판단 범주와 승인번호, protocol version/hash, 보존 기간,
보상 조건, 미성년자 정책, transfer 범위, 만료/amendment/current status는
관측 근거가 없으므로 `TBD_EXTERNAL` 또는 `TBD_PREREGISTRATION`으로
유지한다.

## 3. 공개/제한 증거 구조

### 공개 C1/A1 승인 요약

`h00_human_research_approval_public_v1`은 기관·판단·protocol·정책을
version과 SHA-256으로 결합한다. 실제 positive determination은 다음 중
기관이 명시한 하나만 허용한다.

- `approved`
- `exempt_or_waived_with_explicit_authorization`
- `equivalent_official_authorization`

어떤 범주도 연구팀이 추론할 수 없다. `active_official_determination`,
공식 source 확인, 실제 pilot 범위, 모집·동의·녹음·무작위 intervention
권한과 현재 유효 상태가 모두 명시돼야 한다.

### 공개 C1/A1 원본문서 attestation

`h00_official_document_attestation_v1`은 권한 있는 custodian이 제한
원문을 확인했다는 최소 메타데이터다. 공개 승인 요약과 institution,
protocol, determination ID/document, archive receipt를 hash로
교차 결합한다. PII, 참여자 row와 locator path/URL은 복사하지 않는다.

Approval artifact는 attestation SHA-256을 참조하는 단방향 hash chain을
사용한다. 두 artifact가 서로의 최종 파일 hash를 참조하는 순환 구조는
사용하지 않는다.

### 제한 R1 공식 원문

공식 판단서와 실제 locator는 승인된 기관 archive와 역할 기반 접근통제
안에만 둔다. 정적 package는 locator hash를 문자열로 검증할 뿐 locator를
resolve하거나 원문을 열지 않는다.

## 4. fail-closed 검증

Validator는 다음 오류를 거부한다.

- JSON/YAML duplicate key, extra/missing field, NaN/Inf
- workspace 밖 path, 절대경로, traversal, symlink chain
- canonical source·schema·template SHA-256 drift
- read 중 파일 변경 또는 identity drift(TOCTOU)
- template의 `TBD` 제거 또는 false-safe 기본값 변경
- 공식 source 없는 자체 승인, 비활성·정지·철회·만료된 판단
- 존재하지 않거나 미래인 lifecycle 시각, approval보다 오래된 attestation
  status 확인
- 승인 범위, C0/C3/C5 manual, allocation concealment의 누락
- 보존·접근·삭제·철회·backup·미성년자·보상 규칙 누락
- AI 위험·사람 escalation·사고/pause/stop/resume 누락
- 가명키 분리나 transfer/별도 동의 불일치
- 공개 artifact의 PII, 참여자 row, raw 경로/URL/승인번호/자유서술
- approval/attestation의 protocol·institution·document hash 불일치
- 미래 output 대상 중복, 기존 파일 overwrite 또는 atomic publish 정책
  이탈

정적 config는 12개 C1/A1 source를 exact hash로 읽는다. Full preflight는
이 source들의 snapshot을 다시 검증한 뒤 `approval_sha256`이
`TBD_H00_APPROVAL`이면 approval path resolution 전에 종료한다. 현재
config에서는 approval/attestation path/read가 모두 0이다.

## 5. 검증 명령

정적 schema/template/config만 검사한다.

```bash
.venv-scorer/bin/python scripts/prepare_h00_human_research_approval.py \
  --workspace-root . \
  --config configs/experiments/h00_human_research_approval_v1.yaml \
  --config-only
```

현재 full preflight의 기대 결과는 성공이 아니라 안전한 차단이다.

```bash
.venv-scorer/bin/python scripts/prepare_h00_human_research_approval.py \
  --workspace-root . \
  --config configs/experiments/h00_human_research_approval_v1.yaml
```

기대 stage/error:

```text
h00_approval
H00 approval is TBD_H00_APPROVAL
```

이 명령들은 portal을 열거나 제출하지 않고, 기관에 연락하지 않으며,
공식 판단을 생성하지 않는다. 참여자·오디오·개인정보·모델/GPU/TTS에
접근하지 않고 artifact도 쓰지 않는다.

## 6. 향후 경로

개인 연구의 D07/C03/R04 평가에는 기관 제출 대신 개인 연구 scope
attestation, 평가자·청취자 개별 동의와 라이선스 확인, preregistration,
blindness·보안 검토를 보관한다.

참여자 연구를 실제로 시작하려는 경우에만 다음 기관 경로를 사용한다.

participant-study의 연구책임자와 기관 담당자가 다음 순서로 처리해야 한다.

1. 기관이 지정한 공식 양식과 관할 심의 경로를 확인한다.
2. protocol, C0/C3/C5 manual, allocation, consent, PIA, DMP, 모집·보상,
   미성년자, AI 위험, transfer와 사고 대응 문서를 동결한다.
3. 승인된 수단으로 기관에 제출하고 질의·수정·amendment를 기록한다.
4. 기관의 공식 판단 원문을 R1 제한 archive에 저장한다.
5. 권한 있는 custodian이 source, 서명/인장, document hash, protocol
   version, scope, expiry/amendment/current status를 직접 검증한다.
6. PII와 locator를 제외한 attestation template을 먼저 완성하고 canonical
   JSON SHA-256을 계산한다.
7. 그 attestation SHA-256을 포함해 공개 approval summary를 완성하고
   canonical JSON SHA-256을 계산한다.
8. Config에 실제 approval/attestation path와 정확한 SHA-256을
   version-up한 뒤 read-only full preflight를 수행한다.
9. 독립 검토자가 공개 artifact의 개인정보 비포함과 restricted archive
   접근통제를 확인한다.
10. 별도 권한을 받은 인간 연구 executor만 모집·동의·녹음·무작위화와
    intervention을 시작한다.

공식 양식이나 판단 문구를 이 저장소가 임의로 만들 수 없다. Portal
제출·기관 연락·서명과 법률/윤리 판단은 participant-study를 선택한
경우에만 권한 있는 사람과 기관의 외부 작업으로 남는다.

## 7. 다음 차단 조건

현재 차단자는 다음과 같다.

- `approval_sha256 = TBD_H00_APPROVAL`
- `attestation_sha256 = TBD_H00_ATTESTATION`
- 공식 기관 양식/심의 기구/판단 범주/판단 ID: `TBD_EXTERNAL`
- protocol·동의서·PIA·DMP와 C0/C3/C5 manual: 미동결
- 보존 기간/trigger, 삭제·backup, 보상, 미성년자: `TBD_EXTERNAL`
- 외부/TTS/국외 이전 scope: `TBD_EXTERNAL`
- 만료·amendment·현재 유효 상태와 공식 receipt: `TBD_EXTERNAL`

따라서 개인 연구의 D07/C03/R04에는 H00 배점을 적용하지 않는다. H00의
정적 준비 완료는 participant-study의 승인, 개인정보 적법성 또는
논문용 학습효과 결과를 의미하지 않는다.
