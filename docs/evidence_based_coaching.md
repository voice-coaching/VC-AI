# 근거 기반 코칭 이행

2026-09-18. 구현 브랜치 `feat/evidence-based-coaching`. 기본 비활성, 운영 반영 전.

## 실행 경로

HTTP worker의 기존 S3 무결성/claim/deadline/lease 경로 뒤에 연결한다.
Seungun → `coaching_evidence.build_coaching_input` → 후보별 영상 관측 →
`EvidenceBasedCoaching.render` → 공통 generator 0~1회 → 구조 검증/기본 연습 → result v3.
유효 후보가 없거나 입력이 한도를 넘으면 LLM을 호출하지 않는다.
기존 scoring LLM과 single-selected grounded LLM은 새 모드에서 호출하지 않는다.

모델 가중치, vocabulary, 검출 threshold, 재학습은 변경하지 않았다.
새 정책은 검출 결과를 사용자 교정으로 사용할 수 있는지 판단하는 별도 정책이다.

## 근거 정책

- 기대열의 모든 행을 읽고 역할·단어·글자·CTC 위치를 연결한다. 원본은 요청 메모리에서 유지하며 디버깅용 영구 사본을 생성하지 않는다.
- 기대열↔후보열, 기대열↔철자열의 복수 최소 편집 정렬을 확인한다. 고정 deletion 특징, 모호한 위치, 철자 역할 미확인, 초성 ㅇ, 유효 국소 구간 없음은 REVIEW_ONLY.
- 대본 글자의 역할은 음운 변환 후 역할로 확정하지 않는다. 학습 TextGrid 규약을 재정의하거나 초성 ㅇ token을 제거하지 않는다.
- 적격 모델 후보는 기대 표지·후보 표지·철자 역할로 묶고 반복 수→첫 위치 순으로 최대 3개를 선택한다. 음소 간 미보정 detectorScore의 크기로 우선순위를 정하지 않는다.
- 최종 system+user 메시지를 세어 후보 묶음 단위로 줄인다. 제외 ID와 INPUT_BUDGET을 남긴다. 후보 세부 특징을 몰래 일부만 제거하지 않는다.
- 검출기는 모델 관측이며 정확한 전사·확정 조음 진단이 아니다. 자연어 사실 적합성은 별도 사람 QA가 필요하다.

## Prompt / provider

`configs/pronunciation_feedback/prompts/system.md`와 `coaching/active.md`가 가리키는 본문을 시작 시 한 번 읽어 SHA-256을 고정한다. Git symlink를 Windows가 일반 텍스트로 체크아웃한 경우 정확한 대상 이름만 허용한다. 대본에서 경로·모드를 선택할 수 없다.

항목은 observation(코드), explanation/action/practice/selfCheck(생성), candidate/evidence/guidance 참조, 위치·출처를 가진다. 생성된 ID, 완결성, 크기, 금지된 추가 필드, 모든 입력 후보의 포함 여부를 검사한다. JSON 형식 검증만으로 교육적 정확성을 인증하지 않는다.

기본 지도 문구는 범용 연습 초안이다. 전문가 승인 자료라고 표기하지 않는다. 향후 음소·문맥별 출처와 검토 이력을 갖춘 지식 자료로 대체해야 한다. 점수·확신도·해부학적 원인을 생성시키지 않는다.

| 설정 | 기본/조건 |
| --- | --- |
| `AI_ANALYSIS_COACHING_ENABLED` | `false`; `true`는 v3 reader 배포 후 사용 |
| `AI_ANALYSIS_COACHING_RESULT_CONTRACT` | 활성화하려면 명시적 `v3` 필요 |
| `AI_ANALYSIS_COACHING_PROVIDER` | CLOVA / OPENAI / TEMPLATE, 기본 CLOVA |
| `AI_ANALYSIS_COACHING_TIMEOUT_SECONDS` | 120; 0 초과 300 이하 |
| `AI_ANALYSIS_COACHING_INPUT_TOKENS` | CLOVA 3900 / OpenAI 16000 |
| `AI_ANALYSIS_COACHING_OUTPUT_TOKENS` | 1200; CLOVA 서버 상한 2048 이내로 설정 |
| `AI_ANALYSIS_CLOVA_TOKENIZER_PATH` | 기존 고정 revision tokenizer 필요 |
| `AI_ANALYSIS_CLOVA_ENDPOINT` | loopback `/v1`, 외부 redirect 거절 |
| `AI_ANALYSIS_COACHING_OPENAI_MODEL` | OpenAI 선택 시 필수. 모델을 자동 대체하지 않음 |
| `OPENAI_API_KEY` | 프로세스의 비밀 설정. 값 출력/로그 금지 |

CLOVA는 checkpoint tokenizer, OpenAI는 UTF-8 바이트의 보수적 상한을 사용한다. 후자는 실제 tokenizer 토큰 수가 아니다. 비교 시 동일 입력/동일 정책 여부와 각 한도를 함께 기록한다. 응답 usage가 없으면 null이며 추정값으로 채우지 않는다.
OpenAI adapter는 기존 프로젝트 비교 방식인 Chat Completions JSON mode, low reasoning, store=false를 사용한다. [공식 문서](https://developers.openai.com/api/docs/guides/structured-outputs)가 구별하듯 JSON mode 자체는 스키마 보증이 아니므로 로컬 validator가 필수다.
CLOVA 서버의 출력 허용 상한은 512→2048이며 기존 호출의 max_tokens는 바꾸지 않는다. 새 모드의 부하·실제 지연은 배포 전 확인해야 한다.

로그에는 analysis/request ID, provider, HTTP 상태, 종료 이유, 제공된 token usage, 소요시간, 후보 수와 fallback 분류를 남긴다. 대본·미디어·응답 원문·비밀값은 남기지 않는다.

## 영상·점수 범위

최대 3개 후보별 실제 dense window 경로를 수행한다. 영상/오디오 admission, PCM 일치, Seungun 결과와 전체 희소 decode는 요청 내 재사용한다. 후보에 귀속된 QC/sync/receipt는 다시 연결한다. 겹치는 dense window의 프레임 캐시는 아직 구현하지 않았다.
승인 action의 존재와 독립적으로 유효 aggregate 관측을 전달한다. 실제 decode 시각과 aggregate 값은 별개다. `sampleCount=null`은 유효 landmark 표본 수가 공개 aggregate 계약에 없다는 뜻이다. 8프레임을 연속 궤적이라고 주장하지 않는다.
기존 release의 품질·sync 판정 경로를 사용하나 임계값의 타당성이 새로 검증된 것은 아니다. 따라서 항상 `referenceStatus=NOT_VALIDATED`, `correctiveClaimsAllowed=false`다. 음소 시간 미확인 시 영상 단어 수준 대체 측정은 아직 제공하지 않는다.

`create_universal_lip_production_release.py`는 NOT_EVALUATED 오류로 종료하고 산출물을 만들지 않는다. 같은 화자 자료·공통 입 벌림 값으로 모든 음소의 보정/평가 receipt와 `violation_count=0`을 만들던 기능을 폐기했다. 기존 release를 삭제하지 않는다. 대체 참조값이나 평가 통과 기록은 만들지 않는다.

새 코칭의 `score.overallScore`, callback `overallScore/scoringEvidence`는 null.
`INSUFFICIENT_EVIDENCE` 또는 `NOT_CALIBRATED`를 명시한다. 기존 결과/점수는 변경하지 않는다. 채점 기준의 타당성과 판단 보류 항목 처리를 검증하기 전에는 새 코칭을 승급 점수로 쓰지 않는다. 과거 62.5/45.8 등을 재계산하지 않는다.

## 검증·배포 순서

최초 구현에서는 Python 구문·문서·계약 파일만 정적으로 확인했다. 이후 사용자의 명시적 실제 QA 요청으로 두 영상의 CPU 추론, 실제 입술 추출, GPT 생성, VAD 및 쌍 비교를 수행했다. 결과와 한계는 [2026-09-19 QA 기록](coaching_quality_qa_20260919.md)을 따른다. 호출·계약 통과는 전문가 발음 품질 인증이 아니다.
Backend v1/v2/v3 reader+V29 → 실제 저장/조회 확인 → 개발자 녹음 QA → worker 제한적 활성화 → FE reader 순서다. callback v3는 BE 선배포 전 발행하면 안 된다. 비활성화는 신규 발행만 중지하며 저장된 코칭은 보존한다.

`backend_analysis.speech_timing`의 오프라인 QA 측정·비교기는 실제 PCM을 WebRTC VAD로 측정한다. 대본 길이/감지 시간 지표이며 실제 발화 전사 속도나 언어적 쉼을 확정하지 않는다. 운영 worker·콜백·공개 API 연결은 없다.

남은 품질 게이트: 사람의 위치별 정답/지도 검토, 화자별 독립 보정·평가, VAD 측정의 공개 계약·운영 연결과 정답 대조, 음운 역할 provenance, 음소별 참조/영상 sync 검증, 소유권·촬영 순서를 포함한 재녹음 API 비교, 실서비스 단계별 p50/p95. `strengths=[]`, `comparison=null`은 해당 검증된 자료가 아직 없다는 뜻이다. 이 단계들을 완료한 것으로 보고하지 않는다.
