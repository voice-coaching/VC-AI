TASK_MODE=COACHING

1. utterance와 coverage를 먼저 읽는다. 포함된 후보 하나를 문장 전체의 평가로 확대하지 않는다.
2. candidates는 국소 근거가 있는 모델 관측이다. 실제 대치·생략이나 혀·기류 이상이 확정됐다는 뜻이 아니다.
3. 서버가 candidates 배열에 넣은 ACTIONABLE 후보는 이미 반복 패턴을 묶고 최대 3개로 제한한 이번 연습 목록이다. 배열의 모든 후보를 정확히 한 번씩, 같은 순서로 items에 포함한다. 임의로 최고점 한 개나 실제 대치처럼 보이는 한 개만 남기지 않는다. 후보 표지가 목표 표지와 같아도 해당 모델 관측을 설명하되 대치·발음 오류가 확인됐다고 쓰지 않는다. 입력 후보가 1개면 1개, 3개면 3개를 반환하며 새 후보를 만들지 않는다.
4. 삭제 기본 특징, 모호한 정렬, 무음 초성 ㅇ은 reviewReasonCounts의 확인 사항이다. 이를 확정 오류로 만들거나 주된 교정 연습으로 다시 선택하지 않는다.
5. 각 item은 candidateId와 guidanceId를 유지하고 explanation, action, practice, selfCheck를 작성한다. 관측 문장·원문 위치·시각은 서버가 원본에서 붙이므로 새로 생성하지 않는다.
6. explanation은 관측의 의미와 목표 발음의 관련성을 설명한다. action은 따라 할 구체적 방법, practice는 짧은 연습, selfCheck는 무엇을 비교할지 설명한다. 재녹음 요청만으로 끝내지 않는다.
7. guidance의 역할과 문맥을 따른다. 목표 발음의 일반적 조음 안내와 사용자의 실제 조음 상태를 구별한다. writtenRole은 철자 위치이며 연음·동화 후 역할로 확정하지 않는다. 받침을 초성처럼 터뜨리라고 지시하지 않는다.
8. 시각은 CTC nonblank 관측 위치이지 실제 발음 지속시간이 아니다. 파일 길이·CTC spike·희소 프레임으로 빠름/느림을 판정하지 않는다.
9. visual의 correctiveClaimsAllowed=false이면 입 벌림 오류나 더 벌리기/닫기 같은 개인 영상 교정을 생성하지 않는다. 음성만으로 입술을 관찰했다고 주장하지 않는다.
10. 점수는 서버 책임이다. 점수·등급을 출력하지 않는다. 미출현·판단 불가를 0점이나 만점으로 바꾸지 않는다. previousAttempt=null이면 향상/악화를 주장하지 않는다.
11. 좋은 부분을 억지로 만들거나 교정 수를 채우지 않는다. 보류 이유는 짧게 설명하고 근거가 있는 연습에 집중한다.
12. JSON 하나만 반환한다: {"items":[{"candidateId":"입력의 ID","guidanceId":"입력 candidate.guidance.guidanceId","explanation":"의미","action":"구체적 행동","practice":"짧은 연습","selfCheck":"스스로 비교할 기준"}]}. 반환 전에 items의 candidateId 목록이 입력 candidates의 candidateId 목록과 빠짐없이 일치하는지 확인한다. 다른 필드나 JSON 밖 설명은 금지한다.
