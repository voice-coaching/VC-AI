# S201 한국어 content metric 순수 함수

상태: **정규화·WER·CER primitive 구현 완료 / Whisper runner·threshold
사전등록·실제 평가 미완료**

`src/voice_coach/evaluation/korean_content_metrics.py`는 S201 정적 계약에
이미 고정된 다음 경로만 구현한다.

1. strict UTF-8
2. Unicode NFC
3. Unicode casefold
4. Unicode `P*` 문장부호를 ASCII space로 교체
5. 모든 Unicode whitespace를 ASCII space로 교체
6. 연속 space 축약과 trim
7. space token WER와 space 제외 codepoint CER

숫자 읽기, 형태소 분석, 발음 rewrite, 맞춤법 교정은 하지 않는다. 빈
reference는 fail-closed한다. 결과 객체에는 edit distance, unit 수와
rate만 있고 raw/normalized transcript는 없다.

`decide_content_mismatch`는 WER/CER threshold를 호출자가 명시적으로
전달할 때만 frozen OR 규칙을 적용한다. 현재 repository의 threshold가
`TBD_PREREGISTRATION`이므로 production caller는 이 함수를 호출할 수
없다. 이 구현은 다음을 뜻하지 않는다.

- Whisper model bundle 또는 inference runner 존재
- S201 데이터·audio·transcript 접근
- WER/CER 실제 결과
- content mismatch gate 통과
- S2 독립 감사 완료

검증:

```bash
.venv-scorer/bin/python -m pytest \
  tests/unit/test_korean_content_metrics.py
```
