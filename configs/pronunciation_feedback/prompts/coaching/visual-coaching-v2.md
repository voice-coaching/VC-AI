INTERNAL_GENERATION_SCHEMA=coaching_generation_v2
VISUAL_POLICY=coaching-visual-policy-v2

이 절은 위 기존 출력 예시의 필드 목록을 확장한다. 각 item은 기존 여섯 필드와 usedVisualStatementIds 배열을 반드시 반환한다.
현재 candidates의 순서와 개수를 유지한다. candidateId와 guidanceId는 해당 후보의 값만 사용한다.
explanation/action/practice/selfCheck는 해당 후보의 allowedText에서 문자열을 그대로 선택한다. 자유롭게 바꾸거나 과거 전문가 문장을 덧붙이지 않는다.
전문가 사례는 과거 참고 자료이며 현재 사용자의 영상 진단·원인·교정 권한이 아니다. 현재 시도의 정책과 allowlist가 우선한다.
OBSERVATION_ONLY인 영상은 개인 교정이나 정상/비정상 판정에 사용하지 않는다. 같은 구간의 중립적 재확인 안내가 유용할 때만 해당 후보의 allowedStatementIds를 usedVisualStatementIds에 넣는다.
UNAVAILABLE이거나 영상 안내를 사용하지 않으면 usedVisualStatementIds는 []다. 다른 후보나 과거 사례의 ID는 사용하지 않는다.
영상 수치·구간·표본 수·관찰 문구는 서버가 렌더링한다. 자유 텍스트에 영상 관찰·수치·원인·교정 방향을 생성하지 않는다.
반환 형태: {"items":[{"candidateId":"입력 ID","guidanceId":"입력 guidance ID","explanation":"allowedText 중 선택","action":"allowedText 중 선택","practice":"allowedText 중 선택","selfCheck":"allowedText 중 선택","usedVisualStatementIds":[]}]}
