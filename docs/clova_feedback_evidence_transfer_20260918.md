# Seungun → CLOVA 근거 전달 확장

2026-09-18. 표현 정책 변경 `bea2930e` 이후의 전달 개선이다. 모델 가중치, detector threshold, 채점 공식, AWS 공개/콜백 계약은 변경하지 않는다.

## 구현

- `feedback_evidence.py::build_feedback_evidence`: 현재 시도의 전체 음소·후보·정렬·시각·검출 점수·9개 soft feature를 원래 정밀도로 보존한다. 기대열, CTC 후보열, 삽입, notices, 원문 위치도 연결한다. 원문 글자 위치는 음운변동 이후 음절 경계의 확정값이 아니다.
- `grounded_advice.py`: 입력 v6에 `feedbackEvidence`를 추가한다. v3/v4/v5 입력도 읽는다. 출력 스키마는 유지한다.
- `BackendGroundedFeedback.render`: 공식 선택 음소와 동일 시도의 근거를 연결한다. 다른 오류 후보는 공식 확정 진단과 구분한다. 영상은 기존 digest·선택 index·승인 claim 연결 검증을 통과해야 전달된다.
- `BackendAnalysisWorker.process_admitted`: 음성 → 영상 → 기존 채점 → 피드백 순서로 바꿔 검증된 점수를 피드백에 제공한다. 생성 횟수와 점수 계산은 그대로다. 채점 실패 정책도 유지한다.
- 영상 없음, 미설정, 추출 실패, 입력 처리 실패, 공개 가능한 근거 없음, 연결 검증 실패를 구분한다. extractor가 이유를 제공하지 않는 `None`은 `NO_PUBLISHABLE_EVIDENCE`다. 품질 실패나 정책 제외로 추측하지 않는다. 원본 계약에 없는 표본 수·표본 시각은 null이며 동작 속도·순서를 만들지 않는다.

## 입력 예산과 개인정보

서버 내부의 전체 근거와 LLM에 실제 전달한 자료를 구분한다. `fit_evidence`는 최종 system/user 메시지를 3,900 입력 토큰 안으로 맞춘다. 모델 서버 한도는 4,096이다. 전체 기대/후보열과 공식 선택 지점을 우선하고 오류 후보·인접 음소·나머지를 순서대로 포함한다. 선택 음소의 soft feature를 먼저 포함하고 공간이 남으면 추가한다.

49개 세부 채점표보다 음소 관측을 우선한다. 세부 점수를 제외해도 기존 총점·대분류 점수는 유지한다. 포함 index와 제외 범위, soft feature 제외 범위, 전체 열 포함 여부, 세부 점수 생략 여부를 메시지 및 진단 로그에 명시한다. 긴 발화 전체를 한 번에 전달한다고 주장하지 않는다.

Transformers 5의 AutoTokenizer는 모델 서버의 4.52와 GPT2 전처리가 달랐다. 고정된 모델의 `tokenizer.json` 그래프를 직접 사용하고 동일 chat template을 적용한다. 모델 가중치는 로드하지 않는다. 실제 모델 환경과 토큰 수를 대조한다.

원본 경로, 사용자/작업 식별자, 파일 hash, 모델·설정 digest는 LLM에 전달하지 않는다. 알 수 없는 원본 필드는 이름/내용 대신 제외 개수만 기록한다. JSON 개인정보 검증과 기존 fallback은 유지한다.

실제 생성에서 단일 JSON 코드블록 때문에 fallback되는 문제가 확인되어, 완결된 단일 코드블록만 정규화한 후 기존 필드·ID·enum·개인정보 검증을 수행한다. 앞뒤 설명문이나 여러 객체를 잘라서 통과시키지 않는다.

초기 생성은 받침 ㅇ을 무음으로 잘못 설명했다. prompt에 초성/종성 차이와 일반 조음 참고를 명시했다. 이는 검출 결과나 사용자 조음 측정값을 추가한 것이 아니다. 초성/종성 소릿값 차이의 참고는 [국립국어원 답변](https://www.korean.go.kr/front/onlineQna/onlineQnaView.do?mn_id=216&pageIndex=1&qna_seq=329332), 자음의 조음·비음 설명은 [국립국어원 새국어생활](https://www.korean.go.kr/nkview/nklife/1993_1/3_1.html)이다. 생성 모델의 모든 음성학적 설명이 정확하다고 보증할 수 없으므로 실제 생성 문구를 별도로 검토해야 한다.

## 활성화 및 원복

`AI_ANALYSIS_CLOVA_FULL_EVIDENCE_ENABLED=true`로 활성화한다. `prepare_http.py`가 기존 모델 pin과 runtime revision을 대조하고 `AI_ANALYSIS_CLOVA_TOKENIZER_PATH`를 파생한다. 모델 저장 경로는 LLM 입력에 넣지 않는다. false이면 기존 선택 음소 입력을 사용한다. 전체 원복은 배포 전 실행 사본·영구 overlay·manifest·환경·attestation 백업을 복구한다.

API 워커가 prompt를 전송하므로 이번 변경에는 CLOVA 모델 재적재가 필요 없다. 워커 재시작 전 진행 작업과 새 analyze/retry 접수를 보호해야 한다. 코드상 flag 기본값은 false이며 현재 HTTP 배포 설정에서 true로 명시한다.

## 검증 기록의 범위

기존 testvideo1의 저장된 실제 음성 분석 근거를 사용한다. 새 음성 추론이나 새 사용자 분석 DB 행을 만들지 않는다. 근거 보존, JSON 왕복, 입력 예산, 실제 CLOVA 생성과 fallback 여부를 분리해 기록한다. 서비스 readiness는 별도다. 기능·자동 회귀 QA는 수행했다고 주장하지 않는다.

격리 검증 결과:

- 원본 30개 음소의 지원 필드·정밀도 보존 및 v6 JSON 왕복 확인.
- 최종 LLM 입력은 24개 음소 행, 전체 기대열 30개와 CTC 후보열 28개, 선택 음소의 9개 특징값, 총점·대분류 결과. 제외 음소 범위는 22–23, 25, 27–29이며 세부 채점표는 생략된다. 선택 음소가 deletion이어서 실제로 없는 후보·시각·특징값은 그대로 null이다.
- 계획된 입력과 모델 서버가 보고한 입력 토큰 수가 모두 3,882로 일치했다. 출력 221토큰, 모델 생성 59.773초, 요청 처리 약 60.28초, finish_reason=stop.
- `validated_grounded_plan`, fallback 없음. 최초 두 호출은 단일 JSON 코드블록 때문에 거절됐으며 동일 응답의 재검증으로 원인을 확인했다. 이후 prompt의 초성/종성 참고 보완 후 실제 재생성도 통과했다.
- 최종 실제 문구는 받침 ㅇ의 연구개 비음 설명, 혀 뒤쪽·코로 흐르는 공기의 일반 조음, ‘강/방’ 및 ‘콩을’ 연습을 제공했다. 이전 무음 설명은 없어졌다. 이는 모델 후보의 실제 오발음 여부나 사용자 혀 위치를 검증한 결과가 아니다.
- Python AST·배포 JSON·diff 공백 검사 통과. 서버 구조·입력 자료·실제 생성 확인이며 새 미디어 업로드부터 AWS 저장까지의 E2E 재실행은 아니다.
