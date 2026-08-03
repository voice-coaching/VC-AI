# 논문 개정 템플릿 — S201 Whisper 내용 일치 hard gate

상태: **TEMPLATE_ONLY / 실제 S201 결과 없음 / 정본 원고에 미승격**

이 파일은 향후 S201 completed commit과 독립 검증 receipt가 생겼을 때
정본 `EXPERIMENTAL_MANUSCRIPT_KO.md`에 반영할 문장·표의 자리만 고정한다.
현재 값을 추정하거나 `PENDING`을 해제하지 않는다.

## Methods 삽입 후보

> [PENDING: S201_METHODS] S201은 S1과 독립된 내용 일치 hard gate로
> `openai/whisper-large-v3`의 exact revision과 동일 revision processor를
> local-only로 사용했다. 언어는 한국어 `ko`, task는 `transcribe`로
> 강제했고 sampling, language detection, translation과 timestamp feature를
> 금지했다. Reference와 hypothesis에는 사전 고정한 NFC 기반 literal
> normalization을 동일하게 적용했다. S201은 phone·운율 판정을 수행하지
> 않았고 S1 prediction·label·alignment를 입력받지 않았다.

검증 뒤 채울 provenance:

| 항목 | 값 |
|---|---|
| S108 final commit SHA-256 | `[PENDING: S108_COMMIT_SHA256]` |
| S200 readiness lock SHA-256 | `[PENDING: S200_LOCK_SHA256]` |
| S201 config SHA-256 | `[PENDING: S201_CONFIG_SHA256]` |
| Whisper model ID/revision | `[PENDING: S201_MODEL_REVISION]` |
| local bundle attestation SHA-256 | `[PENDING: S201_MODEL_ATTESTATION_SHA256]` |
| threshold preregistration SHA-256 | `[PENDING: S201_THRESHOLD_PREREG_SHA256]` |
| normalization policy SHA-256 | `[PENDING: S201_NORMALIZATION_SHA256]` |
| completed registry SHA-256 | `[PENDING: S201_REGISTRY_SHA256]` |
| independent receipt SHA-256 | `[PENDING: S201_RECEIPT_SHA256]` |

## Data role 삽입 후보

> [PENDING: S201_DATA_ROLE] S2 expert content transcript는 L2 content
> reference/evaluation에 사용했고, FLEURS ko-KR와 ZEROTH-Korean은
> CC-BY-4.0 attribution과 원 partition을 보존한 외부 ASR 평가에만
> 사용했다. 두 원어민 corpus를 학습자 오류 label, S2 expert transcript
> 대체물 또는 threshold 선택 자료로 사용하지 않았다.

검증 뒤 채울 자료 원장:

| 자료 | 역할 | partition | 항목 수 | 시간 | attestation SHA-256 |
|---|---|---|---:|---:|---|
| S2 expert content | L2 content reference/evaluation | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| FLEURS ko-KR | external ASR only | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| ZEROTH-Korean | external ASR only | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |

## Preregistered hard gate

현재 임계값 근거가 없으므로 아래 수치는 비워 둔다. Frozen
preregistration의 hash와 작성 시점이 data/model path access보다
앞섰음을 검증한 뒤에만 채운다.

| 지표·규칙 | 사전 고정 값 | 근거 |
|---|---:|---|
| item WER maximum | `[PENDING: TBD_PREREGISTRATION]` | `[PENDING]` |
| item CER maximum | `[PENDING: TBD_PREREGISTRATION]` | `[PENDING]` |
| content mismatch 결합 규칙 | `[PENDING: TBD_PREREGISTRATION]` | `[PENDING]` |

## Results 표 후보

### 외부 원어민 ASR 평가

| dataset/split | N | hours | WER | CER | 95% CI | license/partition audit |
|---|---:|---:|---:|---:|---|---|
| FLEURS ko-KR `[PENDING split]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| ZEROTH-Korean `[PENDING split]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |

### S2 expert content 평가

| cohort/slice | N | WER | CER | mismatch rate | gate pass rate | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| 전체 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| Chinese L1 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |
| Japanese L1 | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` | `[PENDING]` |

## 결과 해석 문장 후보

> [PENDING: S201_RESULTS] 사전 고정 threshold에서 S201의 content gate는
> `[PENDING]`의 결과를 보였다. 이 결과는 transcript 내용 일치에만
> 해당하며 phone·운율 평가, S2 acoustic auditor 전체 성능 또는 deployment
> readiness로 해석하지 않는다.

다음 근거가 없으면 위 문장을 원고로 승격하지 않는다.

- 실제 WER/CER/content report와 행 수·metric 재계산
- FLEURS/ZEROTH license·partition attestation
- S2 expert content 자료의 독립성·승인 attestation
- no S1 prediction/oracle alignment/locked leakage 감사
- completed registry·immutable S201 commit·독립 receipt
- source/config/model/data hash의 pre/post 실행 일치

## Failure·limitation 삽입 후보

> [PENDING: S201_LIMITATIONS] Literal normalization은 한국어 숫자 읽기,
> 띄어쓰기 변이, 조사·어미 또는 의미 동등 표현을 완전히 다루지 않는다.
> Whisper transcript 오류와 실제 학습자의 content 오류를 분리하기 위해
> 독립 전문가 검토와 mismatch failure taxonomy를 보고했다. S201 통과는
> 발음·유창성·운율 품질 통과가 아니다.

필수 failure slice:

- 숫자·영문·기호가 포함된 prompt
- 짧은 발화와 빈 hypothesis
- L1, 성별, 숙련도, 녹음조건
- WER와 CER 판정이 불일치한 표본
- ASR 오류와 실제 내용 누락·삽입의 전문가 구분

