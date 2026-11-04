# Seungun 원본 JSON 전달과 서버 채점 계약

사용자가 제공한 `intelligentAI/docs/announcer_ai_pre_llm_evidence_review.md`를 참고하여 입력과 프롬프트를 수정했다. 문서 자체는 읽기 전용 검토서이고, 원본 전체 전달·프롬프트 수정의 권한은 사용자 요청에서 나온다. 사용자는 서버 채점 규칙과 기존 사용자 피드백을 모두 유지하도록 확인했다.

## 요청의 구성

1. 시스템 메시지: 원본 경로별 해석 규칙, 근거의 한계, 짧은 코칭 규칙, 9항목 서버 채점 규칙.
2. 보조 자료 메시지: 원고, 기존 서버 6항목 점수·선정 결과, 출력 schema, 원본 digest·바이트 수·동일 실행 연결, 별도 서버 검산표.
3. 원본 메시지: producer가 만든 CORE bytes를 UTF-8로 읽은 문자열 전체. 공백·키 순서·배열·중복 객체·native·receipt를 선택하거나 요약하지 않는다.

HTTP JSON 인코딩 자체의 이스케이프는 발생하지만, API가 읽는 마지막 message content를 UTF-8로 인코딩하면 원본 bytes와 같다. 원본/스크립트/음원/준비 음성의 기존 digest 연결을 유지한다. byte 예산 또는 기존 1 MiB transport 제한 초과 시 호출 전에 실패하며 원본 축약으로 우회하지 않는다.

## 채점과 피드백

여섯 점수·하위 최대 세 항목 선정·사용자용 최대 5문장 규칙은 유지한다. 원본의 상세 관측은 피드백의 근거를 보완하며 점수·선정 순서를 GPT가 바꾸는 입력이 아니다. 특정 예문을 시스템 프롬프트에 고정하지 않는다. 발음 오류 채택과 위치 정확성을 구분하고, 속도의 분모·MFA/RMS 쉼·F0 범위/대표값/유효 비율·상대 강도·참조 집단의 한계를 설명한다. 원본에 없는 절대 SPL·억양 정답·전문가 평가 결과를 생성하지 않는다.

9항목 총점은 기존 순수 서버 계산기와 고정 기준표를 재사용한다. 배점은 25/10/10/10/10/10/10/5/10, 단계 경계는 95/80/60/40%, 표본 0은 null/배점 제외, coverage는 `4*(sampleCount-deletedClear-deletedFlagged)`, 최종 값만 HALF_UP 1자리 반올림이다. 정수 보조값·순서·revision·총점의 일치를 검증한다. `legacyGrade`는 내부 생성 결과 필드이며, 기존 levels/score 추정 대신 서버 `RUBRIC_RESULT` 전체를 담는다. 공개 서비스의 `scoring` 상태/결과와 `feedback` 문자열 형식은 유지한다. Frontend/Backend 저장소는 수정하지 않는다.

현재 CORE의 GOP·정렬·채택 경고에는 이 고정 기준표가 요구하는 모든 기대 음소의 matchedClear/matchedFlagged/substitutedClear/substitutedFlagged/deletedClear/deletedFlagged 판정이 없다. 퇴역한 `core_result.pronunciation` 경로도 다시 실행하지 않는다. 이런 입력에서는 `INPUT_DATA=null`, `RUBRIC_RESULT={"status":"unscorable"}`이며 공개 채점은 `UNSCORABLE`이다. 발음/GOP 전체가 측정되지 않았다는 뜻이 아니며, 유효한 여섯 항목의 피드백 생성은 계속한다. GOP를 임의 threshold로 이 여섯 상태에 변환하거나 선택 경고 몇 개로 총점을 만들지 않는다.

## 백업과 확인 범위

기존 운영 프롬프트·H5·CORE manifest·관련 소스 백업은 `/home/2020067/intelligentAI/systempromptbackup/raw-seungun-20261104T152656Z`다. 추가 영향 파일의 원래 Git bytes도 별도 manifest로 보관했다. 키·녹음은 백업하지 않았다.

기존 분석 #138의 308,144-byte CORE로 요청 직렬화만 확인했다. 마지막 메시지의 bytes·SHA-256·파싱된 전체 객체가 원본과 일치했고, 총 메시지 내용은 361,920 bytes, HTTP body는 394,851 bytes였다. 모델은 `gpt-6.1-terra`, reasoning effort는 `none`이다. 새 요청을 전송하거나 음향 추론·GPT 생성·자동 회귀 테스트를 실행하지 않았다.

direct/internal v5 H5 후보를 별도 디렉터리에 빌드하여 기존 운영 v4 파일을 보존한다. 후보 패키징은 운영 등록·배포·재시작을 뜻하지 않는다. 실제 전송 여부·토큰 사용·지연·피드백 품질은 미검증이다.
