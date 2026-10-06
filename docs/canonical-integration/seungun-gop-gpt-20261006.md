# Seungun GOP → 단일 채점표와 GPT

상태: 구현 및 PR 인계. 이 문서는 운영 배포 완료 기록이 아니다.

## 결정

사용자가 GOP 근거를 보고 GPT가 항목 단계를 판단하는 방식을 선택했다. 서버의 사전 점수 계산, RUBRIC_RESULT, exp(GOP) 백분율 변환, 95/80/60/40% 경계는 사용하지 않는다. 새 실행에서는 보정 승인, TOKEN_MAPPING_UNVERIFIED, FRAME_REGION_UNAVAILABLE, P1 교정 후보 허가를 GPT 호출 조건으로 사용하지 않는다.

단일 정본은 `configs/pronunciation_feedback/phone_rubric_gop_ctc_af_sd_v1.json`과 그 해석을 지시하는 `deploy/runpod/harnesses/seungun_gop_prompt.txt`다. 모음 25, 평음·경음·격음·마찰음·파찰음·비음 각각 10, 유음 5, coverage 10을 유지한다. GPT가 9개 level(0~4 또는 대상 없음 null), 가중 총점, 한국어 feedback을 한 번에 반환한다. 서버는 응답 형식과 유한값 범위만 검사하며 점수를 계산하거나 교체하지 않는다.

## 음향 근거

[Cao et al., Interspeech 2024](https://www.isca-archive.org/interspeech_2024/cao24b_interspeech.pdf)의 alignment-free CTC SD 정의를 사용한다.

`GOP_i = log P_CTC(L|X) - log Σ_{L′∈SD(i)} P_CTC(L′|X)`

SD(i)는 다른 위치를 고정하고 해당 음소를 유지·40개 Native 음소 중 하나로 대치·삭제한 서로 다른 음소열의 집합이다. 각 확률은 CTC blank와 반복 규칙을 포함한 모든 정렬 경로의 합이다. 다른 프레임에서 얻은 두 최대 logit을 확률로 환산하지 않는다. 삽입 허용 SDI나 별도 PER 점수를 혼합하지 않는다.

- 기존 Native forward의 `arr`를 재사용한다. 새 모델 실행이나 음성 재다운로드가 없다.
- `full_metadata`의 전체 기대 음소열과 원래 source/word/syllable index를 보존한다. 무음 onset ㅇ을 제외하며 detector용 분할 모음열을 쓰지 않는다.
- Native 어휘의 역할별 표지(G/k, D/t, B/p, R/L)를 사용한다. E(ㅐ/ㅔ), iE(ㅒ/ㅖ), oE(ㅙ/ㅚ/ㅞ)의 모델 내 병합을 명시한다. 그 차이를 판별했다고 주장하지 않는다. 이는 이 모델의 표기 대응이며 사람 평가로 검증된 보정표가 아니다.
- 기준열은 음소만으로 구성한다. word separator/special token은 기준열과 대치 후보에 넣지 않는다. 원래 log-softmax의 전체 어휘 분모는 유지한다.
- 전체 시퀀스 확률은 float64 로그 공간의 CTC loss로 계산한다. 최대 32개 후보씩 처리한다. 단순 정확 계산의 시간은 O(T·N²·V), 배치 메모리는 O(B·T·(N+V))이며 T는 프레임, N은 기대 음소 수, V=40, B≤32다. 지연 감소를 주장하지 않으며 운영 측정이 필요하다.
- 출력 `phonemeAssessment`는 각 GOP, 기준열·경쟁 집합 로그확률, 가장 강한 대치/삭제 가설, 원래 CTC 관측 프레임을 포함한다. 원래 Seungun P1 관측은 CORE에 보존하지만 별도 채점표나 허가 정책으로 GPT에 전달하지 않는다.
- coverage는 기대열과 greedy CTC 관측열의 단위 편집 정렬에서 삭제되지 않은 기대 위치 비율이다. 동률은 대각선→삭제→삽입 순서로 해소하고 모호성을 표시한다. 대치는 coverage에 포함한다. 이는 음소 정확도와 별개다.
- `gop_start`, `gop_done`, `phonemeAssessment.elapsedSeconds`로 추가 계산 시간을 구분한다.

논문은 GOP 음향 특징의 근거다. GPT의 0~4 판단 및 한국어 9항목 가중 총점이 논문에서 검증됐다는 뜻은 아니다. null/비유한 GOP는 만점이나 0점으로 대체하지 않는다. 실제 근거 부족 시 GPT가 UNSCORABLE과 사유 피드백을 반환한다. 공급자 오류 시 점수 없이 생성 실패를 알린다.

## 연결과 호환성

새 bridge는 과거 H5 후보 선택기를 상속하거나 로드하지 않는다. GPT 입력은 연습문, 전체 GOP 근거와 Native 관측, 단일 RUBRIC이다. 출력은 scoring과 feedback이다. 공개 coaching은 호환용 `items=[]`, `selection=null`, `feedback`을 사용한다. 직접 RunPod 결과와 AWS 학습 이력 모두 feedback을 보존하며 FE에서 표시한다. B2/AWS 저장은 기존 비동기 경로를 유지한다.

파일·시도 연결, 배포 패키지 hash, JSON 형식, 잘못된 음성의 입력 실패 처리는 유지한다. 이는 발음 점수의 별도 게이트가 아니다. 과거 결과의 schema/하네스는 읽기 전용 호환으로 남기며 `require_native_execution`은 새 GOP identity만 실행하도록 제한한다.

## 패키징과 인계

`deploy/runpod/build_seungun_gpt.py`에 아래 입력을 전달한다. 기존 디렉터리를 덮어쓰지 않는다.

- `--core-source`: 원본 `canonical_ai_pre_llm_20260928_100247Z_421fda`
- `--h5-source`: `canonical_h5_20261003` (원본 계약·요청 설정의 검증된 빌드 입력)
- `--destination`: 새 비어 있는 출력 디렉터리

생성 디렉터리는 `canonical_seungun_gop_core_20261006_v1`, `canonical_seungun_gop_gpt_20261006_v1`이다. 전자는 RunPod releases, 후자는 validation 아래에 설치한다. 새 core/GPT/lock/prompt identity는 `canonical_harness.SEUNGUN_GPT`와 FE·BE 인계 계약에 등록했다. `prepare_canonical.py`의 기본 pipeline revision도 `canonical-seungun-gop-gpt-20261006-v1`이다.

배포 순서는 FE 호환 코드, BE 계약 및 verifier bundle, RunPod 새 패키지·앱 순서다. Backend develop 병합은 자동 배포를 일으키므로 이 작업에서는 PR까지만 생성한다. 원래 v4/v5 이력의 읽기는 유지하되 신규 실행에 구형 경로를 사용하지 않는다. 새 schema digest와 runtime 설정을 함께 반영해야 한다.

확인 범위: 패키지 생성 시 부모 hash·Python 소스 컴파일, JSON/schema 정적 확인, FE TypeScript 및 BE Java 컴파일. AGENTS.md에 따라 자동 회귀 테스트·브라우저 QA·실제 녹음/GPT 호출은 수행하지 않는다. 운영 지연·점수 품질은 개발자 QA에서 별도로 확인해야 한다.
