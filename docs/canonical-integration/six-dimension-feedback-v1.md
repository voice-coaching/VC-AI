이 문서는 2026-11-04 사용자 요청으로 구현한 **전문가 검토 전 임시 평가 경로**를 설명한다. 기존 Seungun 음향 추론을 재사용하며, 고정 점수 계산 → 하위 3항목 선택 → GPT 설명/연습 생성 순서로 실행한다. 로컬 coach와 AI 직접 분석용 H5를 구현했다. 여섯 점수는 내부 자료이며 기존 feedback 문자열로 하위 세 항목의 연습을 전달한다. 서비스 적용 범위와 운영 기록은 [서비스 인계](six-dimension-service-20261104.md)를 따른다. 실제 녹음·GPT 생성·피드백 품질 QA는 개발자가 수행한다.

## 백업과 복구

구현 전 백업은 `systempromptbackup/20261104T074715Z/`다. 프롬프트·소스·설정·계약·실행 스크립트 981개와 최근 로컬 sol61-light H5의 lock에 포함된 파일을 보존했다. snapshot manifest에 원래 bytes의 SHA-256과 Git 상태를 남겼다. 비밀값·모델·녹음은 포함하지 않는다.

```sh
python3 systempromptbackup/20261104T074715Z/restore.py
python3 systempromptbackup/20261104T074715Z/restore.py --apply
```

첫 명령은 복구 계획만 출력한다. 두 번째 명령은 이번 작업의 파일만 복구한다. 작업 후 다른 수정이 생긴 파일은 덮어쓰지 않는다. 운영 파일이나 서비스에는 접근하지 않는다.

## 임시 정책

정본은 `configs/pronunciation_feedback/six_dimension_v1.json`이다. 아래 숫자는 사용자가 임시값 적용을 요청하여 정한 engineering 값이며 문헌·전문가가 검증한 한국어 규준이 아니다. 전문가 검토 후 이 파일의 수치와 revision을 수정한다. 매 결과에 실제 정책 SHA-256을 기록하며 실행 디렉터리에 정책·프롬프트 snapshot을 저장한다.

| 항목 | 계산 | 임시 100점 범위 | 0점 경계 |
|---|---|---|---|
| 발음 정확성 | 음소별 GOP를 선형 보간한 뒤 음소군별 평균·가중평균 | GOP=0일 때 음소 점수 100 | GOP≤−8일 때 음소 점수 0 |
| 발음 속도 | v2 `speech_rate` | 3.5~5.5 음절/초 | ≤1.0 또는 ≥9.0 |
| 조음 속도 | v2 `articulation_rate` | 4.0~6.5 음절/초 | ≤1.5 또는 ≥10.0 |
| 쉼 | 10음절당 횟수·시간의 두 점수 평균 | 횟수 0~1.5, 시간 0~0.8초 | 횟수 ≥5.0, 시간 ≥3.0초 |
| 억양·음높이 | v2 `f0_range_st` | 3~9 반음 | 0 또는 ≥18 반음 |
| 음량·강도 | v2 `intensity_range_db` | 4~12dB | 0 또는 ≥24dB |

범위 안은 100, 범위 밖은 가까운 0점 경계까지 선형 감점하고 0 미만은 0으로 둔다. 최종 항목 점수만 소수점 첫째 자리 HALF_UP 반올림한다. 순위는 이 표시 점수에 따라 정하고 동률은 정책의 여섯 항목 순서로 처리한다. 별도의 종합 총점은 만들지 않는다.

GOP의 보간점은 `(-8,0), (-4,40), (-2,70), (-0.5,90), (0,100)`이다. exp(GOP)를 정답률로 해석하지 않는다. 모음 25, 평음·경음·격음·마찰음·파찰음·비음 각각 10, 유음 5의 임시 배점을 적용한다. 관측된 음소군의 배점만 분모에 넣으며, coverage는 정확성 점수에 섞지 않고 원본 보조 근거로 보존한다. 기존 운영 GOP/GPT 점수와 같은 척도가 아니다.

관측되지 않은 음소군은 적용하지 않는다. 존재하는 음소군에서 유효 GOP 비율이 80% 미만이면 전체 정확성 점수를 null로 둔다. NaN·무한값·양수 GOP·알 수 없는 음소는 정상 점수로 바꾸지 않는다. 초성 무음 ㅇ은 제외한다. 모음 병합·역할·원래 음소/단어/음절 index는 유지한다.

나머지 다섯 항목은 `core_result.prosody.aihub71557_compatible_metrics_v2.metrics`의 `status=ok`, 올바른 metric revision, 원고 한글 5음절 이상, 분석 구간 1초 이상을 요구한다. F0 점수는 추가로 유효 비율 0.3 이상을 요구한다. 이 문턱도 임시값이며 유효 비율 자체를 능력 점수로 채점하지 않는다. null·누락·잘못된 값은 UNSCORABLE이며 하위 3개 선정에서 제외한다. legacy MFA timing과 v2 RMS timing을 임의로 혼합하지 않는다.

## 평가 범위

- 정확성은 모델의 GOP 지지를 임시 점수로 변환한다. 사람 정답 확률이나 임상·교육 규준 점수가 아니다.
- 속도는 원고 음절 수를 사용하므로 생략·반복을 직접 평가하지 않는다.
- 쉼은 묵음 빈도·길이만 평가한다. RMS 기준은 폐쇄음을 쉼으로 오인할 수 있다. 의미 경계·유성 휴지의 적절성을 평가했다고 말하지 않는다.
- 억양은 F0 변화 폭만 평가한다. 한국어 경계성조·문장 종결 억양·강조 위치를 채점하지 않는다. 구절별 목표와 전문가 레이블이 확보되면 새 policy revision으로 확장한다.
- 강도는 녹음 내 변화 폭만 평가한다. 보정된 절대 dB SPL·지각적 음량·녹음기의 자동 게인 영향을 평가한 것이 아니다.
- 속도·조음 속도·쉼의 공통 원인을 `dependencies`로 남긴다. 점수의 항목 간 비교와 하위 3개 우선순위 역시 전문가 검증 전이다.

## 실행

전체 저장소에서 Python 3.12와 프로젝트 의존성, 준비된 Seungun Docker 이미지·자산을 사용한다. `OPENAI_API_KEY` 또는 `OPENAI_API_KEY_FILE` 중 하나를 해당 프로세스에만 제공한다. 키를 명령 인자·결과 파일·정책에 넣지 않는다.

음성부터 처리:

```sh
python standalone/seungun/run.py coach --audio recording.wav --text "쌀과 콩을 그릇에 담아요." --output results/six-dimension-01
```

원고를 파일에서 읽고 정책을 명시:

```sh
python scripts/analyze_six_dimension.py --audio recording.wav --script-file script.txt --policy configs/pronunciation_feedback/six_dimension_v1.json --output results/six-dimension-02
```

기존 독립 실행 결과를 재사용하여 음향 추론을 반복하지 않기:

```sh
python scripts/analyze_six_dimension.py --analysis-dir existing-seungun-result --script-file script.txt --output results/six-dimension-03
```

같은 명령에 `--measurements-only`를 추가하면 GPT 호출과 키 요구 없이 기존 근거로 점수만 계산한다. 이 옵션도 `--audio` 사용 시에는 실제 음향 추론을 수행한다. 결과 경로는 항상 새 디렉터리여야 한다. 원본 evidence·script·prepared WAV의 digest 연결이 맞지 않으면 실행을 중단한다.

`coach`는 호스트에서 GPT 호출을 수행하고 기존 `analyze`를 자식 프로세스로 실행한다. 분석 컨테이너의 network=none 정책과 기존 `analyze`의 GPT 없는 출력은 유지한다. API 키 환경변수는 음향 분석 자식에 전달하지 않는다. 신규 경로에서 기존 `run_seungun_grounded_feedback.py`의 legacy Clova 경로를 사용하지 않는다.

## GPT 입출력

요청 모델의 기본값은 백업한 최근 로컬 릴리스와 같은 `gpt-6.1-sol`, reasoning=low, service_tier=default, 3000 출력 토큰, timeout=120초다. 설정 변경은 정책 revision과 digest로 추적한다. 공급자 endpoint는 OpenAI chat completions로 고정하며 환경 proxy·redirect·자동 재시도를 사용하지 않는다. 시스템 프롬프트는 `configs/coaching/six_dimension_system_v1.txt`다.

GPT 입력은 원고, 확정된 6항목 점수, 단위·구간·근거 ID, 전체 GOP 근거, 임시 정책, 하위 3개 ID와 출력 schema다. 원본 음성·private logits·API 키는 전달하지 않는다. 참조 분포는 보조 자료로 남기며 백분위를 품질 점수로 변환하지 않는다. 입력 예산을 넘으면 조용히 자르지 않고 호출 전에 실패한다.

GPT는 여섯 항목에 대해 점수를 그대로 복사한 설명과 하위 3개에 대한 observation/practice를 반환한다. 서버는 점수 변경·항목 순서 변경·선정 개수 불일치·다른 항목 근거 인용을 거절한다. 모든 점수가 높아도 상대적으로 낮은 세 항목에 대해 보완 연습만 안내한다. 점수가 세 개 미만이면 가능한 항목만 안내한다.

두 번 GPT를 호출해 첫 응답으로 점수를 정하는 초기 계획은 재평가에 따라 변경했다. 전문가 검토 전에는 임시 수치 정책을 서버가 적용하고 GPT는 결과 설명·연습 생성만 담당한다. 한 요청으로 여섯 설명과 세 피드백을 반환하므로 GPT 결과에 따라 순위를 다시 바꾸지 않는다.

출력은 `voice-coaching.six-dimension-result.v1`이며 `docs/contracts/six_dimension_result_v1.schema.json`을 따른다. `assessment.criteria`에 여섯 점수가 있고 `feedback`에 선정 항목의 연습이 있다. GPT 장애 시 측정·점수·선정 결과는 보존하고 `generationStatus=FAILED`, feedback=[]로 둔다. 임의 fallback 교정을 만들지 않는다. 자연어 내용의 사실성 전체를 자동 검증했다고 표시하지 않는다.

## 패키징과 서비스 인계

```sh
python deploy/dlpc/build_six_dimension.py --destination /tmp/six-dimension-bundle-v1
```

새 evaluator bundle은 소스·정책·프롬프트·schema와 SHA-256 manifest를 포함한다. bundle에서는 기존 Seungun 결과를 `--analysis-dir`로 입력한다. 모델·키·녹음은 포함하지 않는다. 서비스는 별도의 새 direct 전용 H5 identity를 사용하며 기존 H5 hash를 재사용하지 않는다. 공유 Backend schema와 공개 feedback 필드는 변경하지 않는다.

서비스용 bridge는 `voice_coach.six_dimension.pipeline.evaluate`와 같은 모듈 bytes를 hash-pinned package로 로드한다. 호출자가 신뢰할 수 있는 producer의 원본 CORE bytes와 requestId/coreSha256/scriptSha256/sourceAudioSha256/preparedWavSha256 binding을 제공해야 한다. binding과 bytes의 일치는 파일 무결성 확인이며 외부 입력의 인증을 대신하지 않는다. 상세 six-dimension 결과는 private BRIDGE_RESULT에 보관하고 공개 응답에서는 제거한다. 기존 총점용 9개 음소군 rubric 채점은 같은 GPT 요청의 legacyGrade로 유지하며 하위 세 항목 선정에 사용하지 않는다. Backend 내부 canonical 경로는 기존 H5와 공유 schema를 유지한다.

정적 확인 범위는 Python 컴파일, JSON/schema·정책 구조, 백업 hash, source diff다. 저장소 AGENTS.md에 따라 자동 회귀 테스트·관련 테스트 산출물·브라우저 자동화·실제 녹음/GPT 호출은 실행하거나 생성하지 않는다. 후속 개발자 QA에서는 여섯 점수/동점/누락/GPT 실패/원고 불일치와 실제 하위 3개 피드백의 근거 적합성을 확인해야 한다. 실제 녹음 품질·전문가 점수 일치·성능과 서비스 배포는 미확인이다.
