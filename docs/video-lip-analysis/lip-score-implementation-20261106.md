# 입술 채점 구현과 활성화 조건

기준일: 2026-11-06. 로컬 후보 브랜치 `feat/lip-scoring-20261106`의 구현 기록이다.
**운영 배포·실제 영상 품질 검증 완료 기록이 아니다.**
기존 [문헌 검토](lip-research-review-20261106.md)와 [설계안](lip-scoring-feedback-plan-20261106.md)의 후속 구현이다.

## 구현한 흐름

```text
원본 음성·영상과 미디어 결속 검증
  → 기존 음성 CORE 실행 + 영상 특징 추출
  → 원본 CORE JSON 저장 완료
  → 이미 계산한 MFA 위치만 별도 alignment sidecar로 내보냄
  → CORE 원본 SHA·MFA receipt·음성/영상 시간축을 검증
  → 같은 음소·역할·단어 문맥의 실제 참조와 입술 특징 비교
  → 입술 항목 점수 L 및 하위 4항목
  → 음성 A를 보존하고 V = HALF_UP(0.60 A + 0.40 L, 1)
  → 입술 대상 최대 2개 + 음성 대상으로 총 최대 3개 선정
  → 원본 CORE JSON + 별도 점수/시각 근거로 GPT 호출 1회
```

원본 CORE에 가상 `mfa_phone_alignment`를 주입하지 않는다. 기존 방식으로 실제 필드가 있는 과거 CORE도 읽을 수 있다.
새 sidecar가 없으면 입술은 ALIGNMENT_UNAVAILABLE로 남기고 음성 점수를 유지한다.
sidecar가 다른 CORE·음성·MFA receipt에 연결됐으면 무결성 오류로 거부한다. 이런 결속 검사를 일반적인 관측 실패와 혼동하지 않는다.

## 항목과 계산

| 하위 항목 | 현재 측정 | 적용 범위 |
| --- | --- | --- |
| `lip_aperture` 입술 벌림 | 구간의 눈 간격 기준 정규화 벌림 중앙값 | 정렬·참조가 있는 모음 |
| `lip_rounding` 입술 원순 형상 | 같은 기준의 입 폭 중앙값 | ㅗ·ㅜ·ㅛ·ㅠ. 실제 돌출 거리나 원순성 전체를 보증하지 않음 |
| `lip_closure` 입술 폐쇄 | 구간의 최소 벌림 | ㅁ·ㅂ·ㅃ·ㅍ의 onset/coda. 영상만으로 접촉·후두 특성을 확정하지 않음 |
| `lip_timing` 입술 동작 타이밍 | 벌림 최대/양순 최소 이벤트의 구간 내 상대 위상 | 실제 움직임이 충분한 해당 문맥. 정적인 구간은 null |

미구현 값이나 임의의 음소 평균을 채우지 않는다. 실제 참조 궤적에서 같은 측정을 얻고 참조 중앙값을 계산한다.
구간별 3개 이상의 실제 관측 프레임, 같은 MediaPipe mapping과 같은 음소·역할·단어 문맥을 요구한다.
참조와 사용자 원본이 같으면 자기 비교로 점수를 만들지 않는다. 보간 프레임이나 다른 문맥으로 표본을 채우지 않는다.
타이밍은 참조 이벤트 위상과 비교하며 음향에 대한 0ms 지연을 정답으로 사용하지 않는다.

각 관측값 x, 참조 중앙값 m, 허용폭 r, 비교 scale s에 대해:

```text
참조 scale = max(임시 scale 하한, 참조별 측정값의 표본 표준편차)
z = max(0, abs(x - m) - r) / 참조 scale
항목 점수 = 100 × exp(-0.5 × z²)
```

참조 1개면 분산을 추정하지 않고 명시적인 임시 scale 하한을 사용한다.
이 scale은 검증된 모집단 표준편차가 아니므로 측정 응답에도 `referenceStd` 대신 `referenceScale`로 기록한다.
각 발생 위치의 측정 가능한 하위 점수를 동일 비중으로 평균하고, 입술 총점은 평가 가능한 발생 위치의 평균이다.
하위 항목별 요약은 각 항목을 실제 측정한 발생 위치만의 평균이며 부모 점수에 다시 더하지 않는다.

[임시 설정](../../configs/visual_feedback/lip_score_policy_v1.json):

| 설정 | 값 | 단위·의미 |
| --- | --- | --- |
| 벌림 scale 하한 | 0.04 | 눈 간격으로 정규화한 길이 |
| 입 폭 scale 하한 | 0.04 | 같은 기준 |
| 폐쇄 scale 하한 | 0.02 | 같은 기준 |
| 타이밍 scale 하한 | 0.20 | 음소 구간 내 위상 |
| 벌림·폭 허용폭 | 0.01 | 정규화 길이 |
| 폐쇄 허용폭 | 0.005 | 정규화 길이 |
| 타이밍 허용폭 | 0.10 | 구간 내 위상 |
| 타이밍 측정용 최소 움직임 | 0.01 | 구간의 최대 벌림−최소 벌림 |
| coverage 시작값 | 0.60 | 참조 catalog 컴파일 옵션, 수정 가능 |

위 값과 영상 60:40 비중은 전문가 검토 전의 임시 정책이다.
점수 종류는 **PROVISIONAL**, 검증 상태는 **EXPERT_REVIEW_PENDING**이다. 정확도·오류 확률·전문가 점수로 표현하지 않는다.
coverage 분모에는 정렬이 불확실한 평가 대상도 남는다. 평가 불가를 0점으로 만들거나 분모에서 숨겨 만점으로 만들지 않는다.

## 총점·피드백·호환성

- 기존 `sixDimension.compositeGrade`와 음성 6항목은 그대로 보존한다. 별도 `sixDimension.audiovisualGrade`가 A·L·가중치·V를 담는다.
- 외부 총점 이름은 기존 `score.overallScore`를 유지한다. 새 상세 공개 API `score.details`의 구현까지 이번 변경에 포함한 것은 아니다.
- L이 없으면 AUDIO_ONLY로 A를 보존한다. A가 없으면 전체 총점도 null이다. 입술만으로 음성 총점을 대체하거나 누락된 음성 비중을 재분배하지 않는다.
- GPT 입력·응답 검산·재구성 경로 모두 같은 시각 근거를 사용한다. 원본 CORE는 별도 user 메시지에서 바이트 그대로 보존한다.
- 개별 근거와 참조 비교 행동이 유효한 낮은 입술 발생 위치 최대 2개를 먼저 선정하고, 남는 자리는 기존 음성 하위 항목으로 채운다. 시각 근거가 없으면 기존 음성 피드백을 유지한다.
- 임시 점수에는 `COMPARE_REFERENCE` 연습만 허용한다. 단순 참조 차이만으로 오발음이나 더 벌리기·더 오므리기 같은 방향을 확정하지 않는다. `correctiveClaimsAllowed=false`를 유지한다.
- 피드백 최대 3줄·전체 5문장·300자, GPT 생성 최대 1회·자동 재시도 없음은 유지한다. GPT 실패가 이미 계산된 점수를 지우지 않는다.
- 새 visual enum PROVISIONAL과 선택적 `visual.subScores`/음소별 `subScores`를 consumer가 수용해야 한다. Backend/Frontend 배포·실제 저장 확인은 별도다. 기존 점수 필드 이름이나 인증·소유 관계를 바꾸지 않는다.

## 패키징과 실제 자료 준비

1. [sidecar CORE builder](../../deploy/dlpc/build_lip_sidecar_core.py)는 지정한 부모 manifest SHA를 검증하고 기존 runner의 CORE 저장 뒤에 sidecar export만 추가한다. 부모 원본 파일과 과거 frozen 패키지는 수정하지 않는다.
2. [기존 참조 capture/compile 도구](../../deploy/dlpc/prepare_lip_reference.py)의 capture는 선택적 `--alignment-sidecar`를 받는다. 실제 영상·PCM·CORE·미디어 receipt·정렬이 같은 시도여야 한다.
3. 실제 capture를 검수한 기존 review로 catalog를 컴파일할 때 `--lip-score-policy configs/visual_feedback/lip_score_policy_v1.json --minimum-coverage 0.6`을 추가하면 catalog v3에 임시 정책을 고정한다. 빈 catalog에는 점수를 만들어 넣지 않는다.
4. [H5 builder](../../deploy/dlpc/build_six_dimension_h5.py)는 `--visual-catalog`와 `--visual-runtime`을 함께 받으며, composite 정책을 요구한다. 시각 정책과 자료를 lock에 포함하고 새 `bridge/visual.py`도 패키징한다.
5. 생성한 실제 CORE/H5/lock SHA의 새 harness 등록, 영상 capability와 미디어 업로드 연결, Backend·Frontend reader 호환, 후보의 실제 분석 검사를 끝내고 활성화해야 한다. 기존 승인된 harness를 임의 값으로 덮어쓰거나 readiness 검사를 우회하지 않는다.

초기 관찰용 catalog는 비어 있었으나, 이후 사용자 지정 B2 `test/video/`에서 정상 원본 두 개를 확보했다.
두 영상의 대본은 사용자가 **쌀과 콩을 깨끗한 그릇에 담아요**로 확인했다.
첫 번째 영상에서 실제 관측 구간 13개를 capture해 `USER_DESIGNATED_EXEMPLAR` 참조로 구성했다.
이는 전문가 검수가 완료된 기준이 아니며 expertReviewId와 calibration을 만들지 않는다.
참조 원본과 동일한 첫 번째 영상을 자기 비교하여 점수화하지 않는다.

## 검증 기록과 남은 확인

분리된 Python 3.12.13 환경에서 기존 음성 회귀 및 새 입술 회귀 **38개를 통과했다**.
frozen bridge와 같은 jsonschema 4.25.1을 사용했다. 검사 명령은 `PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/analysis`다.
[검사 코드](../../tests/analysis/test_lip_scoring.py)는 합성 근거만 사용하며 실제 영상이나 GPT 호출을 대신하지 않는다.
검사 범위는 제곱 지수 감점, 관측 불가·자기 비교, 0점, coverage, 원본 보존, 정렬 sidecar 결속과 replay,
v2 catalog 호환·v3 읽기, 영상 종합점수·공개 필드명, 입술 우선 대상 선정, 단일 transport 시도와 provider 실패 시 점수 보존이다.

실제 영상 특징 추출과 두 영상의 로컬 CORE/MFA·수치 계산은 수행했다. 다음은 GPT 검사 및 운영 배포와 별개의 로컬 결과다.

| 입력 | 영상 프레임/관측 | CORE/MFA 시간 | 음성 종합점수 | 입술 결과 |
| --- | --- | --- | --- | --- |
| testvideo1.mp4 | 145/145 | 12.255초 | 72.6 | 참조 원본이므로 자기 비교 제외 |
| testvideo2.mp4 | 81/81 | 7.747초 | 33.6 | 평가 대상 14곳 중 1곳만 비교 가능; coverage 1/14 |

두 번째 영상의 평가 가능한 위치에서 벌림 38.0, 타이밍 1.8의 **임시 하위 점수**를 얻었다.
이 숫자를 영상 전체의 능력 점수나 오발음 판정으로 해석하지 않는다. 입술 총점은 기존 minimumCoverage=0.6에
미달해 null이며, 결합 모드는 AUDIO_ONLY로 음성 종합점수를 보존했다. 이 상태를 입술 종합 채점 성공으로 보고하지 않는다.
30fps에서 짧은 음소 구간에 실제 관측 프레임이 0~2개뿐인 경우가 많다. 평가를 통과시키려고 보간 프레임이나
가상 참조를 추가하거나 관측 범위 조건을 임의로 낮추지 않았다. 프레임 부재는 `NO_FRAME_IN_ALIGNED_SPAN`으로
표시하며 실제 얼굴 가림 `LOW_VISIBILITY`와 구별한다.

실제 검사에서 발견한 FFmpeg 4.4 옵션 호환성과 파이프 정리 문제를 수정했다.
MFA TextGrid의 6자리 반올림과 PCM 정확한 길이의 0.5마이크로초 차이는 새 CORE의 두 parser 경계에서만
최대 1마이크로초 범위로 처리하고 audit에 남긴다. 원본 TextGrid·CORE JSON과 운영의 과거 CORE는 수정하지 않는다.

현재 촬영 화면의 영상 Blob 전달, 전문가 기준의 품질 검증, 운영 저장·공개 조회는 아직 검증하지 않았다.
실제 분석/GPT·내부 request v4/result v6/handoff 테스트를 통과하기 전 PR 생성·merge·배포를 진행하지 않는다.
이번 작업에서는 PR·merge·운영 배포를 수행하지 않았다.

## GPT 검사 후속 기록

두 실제 CORE와 영상 근거 replay 후 현재 소스 프롬프트의 GPT 검사를 수행했다. 각각 POST 1회·HTTP 200,
STRUCTURALLY_VALID를 확인했다. 생성 시간은 16.679/15.637초, 피드백은 122/125자·각 3줄이었다.
원본 CORE SHA와 GPT 마지막 메시지 SHA가 일치했으며 영상 프레임·얼굴 좌표는 전송하지 않았다.
이 기록은 국소 입술 연습의 coverage 분리 수정 이전 검사이며 최종 후보의 head/tree 결속 배포 receipt가 아니다.
새 consumer 계약은 [입술 점수 소비 계약](../contracts/lip-provisional-score-v1.md)을 따른다.
