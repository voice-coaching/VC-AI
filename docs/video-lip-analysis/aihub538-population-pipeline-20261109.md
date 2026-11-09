# AI-Hub 538 기반 입술 참조·채점·피드백 파이프라인

## 구현 범위와 현재 상태

대상은 [AI-Hub 립리딩(입모양) 음성인식 데이터 538](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=538)와
[OLKAVS](https://github.com/IIP-Sogang/olkavs-avspeech)다. 음성 6항목·기존 발음 채점표는 보존한다.
이번 변경은 단일 시험 영상과 임시 허용오차 대신 실제 다화자 입술 분포를 사용할 **별도 경로**다.
Seungun CORE 원본 바이트를 수정하지 않으며, 학습용 원본·라벨·CORE·프레임을 GPT에 보내지 않는다.

구현된 단계는 원본 수집, 안전한 압축 해제, 실제 라벨 어댑터, 문장 선택, 별도 resident에서의
CORE/MFA/MediaPipe 추출, 근거 재검산, 화자 분리 통계, 서버 채점, 단일 GPT 요청용 근거·프롬프트,
해시가 고정된 후보 H5 패키징이다. **운영 기준표 생성·등록·배포 완료를 뜻하지 않는다.**
실제 기준표는 충분한 원본·화자를 확보하고 추출·검증을 통과한 뒤 생성한다.

2026-11-09 작업에서 API 키는 `/home/2020067/.config/intelligentai/aihub.env`에 0600으로
저장했다. 운영 분석 환경에 키를 추가하지 않았다. 키 값은 소스·문서·인자·수집 영수증에 없다.

실제 확보·검증한 자료:

| 자료 | 바이트 수 | SHA-256 | B2 검증 |
|---|---:|---|---|
| Training TL174 라벨 묶음 | 179827200 | `b0f5fcd90566cacdd6647191d9987107bd8c7e0f813241bb3713ba1122e86ab2` | 전체 재다운로드 일치 |
| Training TL34 라벨 묶음 | 708160000 | `bd3163e6c1cab08da727616e8f02f5e3144b6a4394f0f0094fe1141af4765e55` | 전체 재다운로드 일치 |
| Training TL50 라벨 묶음 | 588362240 | `b1628a1658dd42aa127e3a5a7706e6a0178e982fdd7968f59d0fe502039d700a` | 전체 재다운로드 일치 |
| TL50과 연결된 전문가 정면 MP4 1개 | 409317319 | `dd8773d4a343d96aec6cc7b2ec069d6a4538741e37e5c827acfca4d733aa0385` | 전체 재다운로드 일치 |
| TL50과 연결된 전문가 정면 MP4 2 | 437771875 | `fc3726f77e902ab6949ccd3e4b13673167aa6ca790ca6993baf445299753d0b8` | 전체 재다운로드 일치 |
| TL50과 연결된 전문가 정면 MP4 3 | 444380543 | `4e45acd5b270d98e89f0bd5f93dcd8b5200e566855c3b0e01009021f7b0aaf3f` | 전체 재다운로드 일치 |
| Training TS50 전체 원본 archive | 80605660160 | `5890dde5e8f9103e9eaa95fab02f184e02c2688f0852026283a1f6824fedd944` | NAS 로컬 전체 SHA 검증; 전체 archive는 B2 미보관 |

TL174·TL34는 실제 라벨 구조·코호트 확인용이다. 일반인·소음 자료를 전문가 무소음 참조로
바꾸지 않는다. 첫 MP4는 H.264 1920×1080·30FPS·음성 AAC·약 322.7초다. 실제 라벨에서
3화자·총 60개의 서로 다른 문장을 추출했다. 최초 48문장과 추가 12문장의 합이며,
미시작/CORE 시작 전 영상 추출 실패를 처리한 14건은 새 문장 수에 중복 합산하지 않는다.
현재 3화자도 운영 기준표 최소 조건에 부족하다. 이 자료만으로 운영 점수를 AI-Hub 검증
점수라고 표시하면 안 된다. 최종 모델·두 영상 검증은 [검증 보고서](aihub538-model-validation-20261109.md)를 따른다.

원본·비공개 manifest와 수집 영수증은
`/home/2020067/.local/share/intelligentai/data/olkavs-20261109/`에 있다.
B2 새 객체 prefix는 `speakai2026/datasets/aihub/538/lip-population-v1/`이며 각 파일의
정확한 fileId·이름·크기·SHA는 해당 `b2-receipt.json`에 보존한다.
TS50 전체 archive는 80.6GB를 로컬 NAS에서 다운로드·전체 SHA 검증했다. B2에는 선택한
정면 원본 3개와 라벨 3묶음이 보관돼 있으며, 전체 archive가 보관됐다는 의미는 아니다.
최종 모델·평가·근거 묶음의 B2 업로드는 최초 자동 검토에서 중단한 뒤 사용자 명시적 승인을
받아 완료했다. 최종 5개 산출물 모두 전체 재다운로드 SHA-256 일치를 확인했으며
로컬 `model-artifacts/`에 파일별 B2 영수증을 보관한다.

## 데이터 경로

1. 공식 API 이용 승인을 가진 키를 다운로드 프로세스에만 주입한다.
   기존 [보안 다운로더](../../scripts/download_aihub_secure.py)는 재개·완료 SHA 영수증과
   기존의 파일별 접근 조건을 유지한다. shell의 API 키 인자 전달 방식은 사용하지 않는다.
2. [수집기](../../scripts/collect_aihub538_frontals.py)는 실제 라벨이 가리키는 전문가별
   정면 원본 파일만 선택할 수 있다. 서버가 비영점 Range를 무시하면 실패하며, 명시적인
   `--stream` 방식은 순차 TAR을 읽어 선택 파일만 저장한다. 선택 수집 영수증의
   `wholeArchiveVerified=false`를 전체 다운로드 완료로 바꾸지 않는다.
   실제 TS50 응답은 part 접미사가 순번이 아닌 바이트 오프셋(`part1073741824` 등)이어서
   순번만 지원하던 초기 수집이 중단됐다. 실제 파일 구조를 확인해 두 형식을 모두 지원한다.
   따라서 전체 공식 archive의 완료 SHA를 확인한 뒤 `--local-archive`·`--download-receipt`로
   숫자 순서로 part를 연결하는 경로를 추가했다. 오프셋의 연속성·누락, 순번 형식 및
   문자열 순서 1,10,2를 혼동하지 않는 회귀 검사도 포함한다. 중단 전 확보한 파일은
   영수증·SHA로 확인해 보존한다.
3. [B2 보관기](../../scripts/aihub_corpus_b2.py)는 비공개 bucket에 원본/파생물을 별도로
   저장하고 **정확한 fileId로 전체 재다운로드 SHA-256**을 확인한다. 기존 객체·미완료 part를
   삭제하지 않는다. 응답 유실 등 모호한 업로드는 intent를 남기고 자동 재게시하지 않는다.
4. [corpus CLI](../../scripts/aihub_lip_corpus.py)의 `unpack --nested-tar`는 공식 완료
   영수증과 archive SHA를 먼저 검증한 뒤 `.tar.part0…`을 이어 읽는다. 절대경로·상위경로·링크·
   특수파일·중복·설정한 바이트 예산 초과를 거부하고 기존 폴더를 덮어쓰지 않는다.
5. 실제 라벨은 최상위 원소 1개의 배열이다. `speaker_info.Specificity=E`,
   `Audio_env.Noise=1`, `Video_env.Angle=A`만 참조 후보로 사용한다.
   `Video_info.video_Name`으로 원본을 연결하고 `Sentence_info`의 `sentence_text`,
   `start_time`, `end_time`을 사용한다. 임의 대본·화자 ID·시간을 생성하지 않는다.
   공식 `1.Training`만 사용하고 공식 Validation은 학습에 넣지 않는다.
6. 문장 clip의 영상과 embedded audio를 같은 시간축에서 자른다. 오디오 없는 입술 crop은
   현재 눈 사이 거리 정규화와 검증된 A/V 시간 연결을 제공할 수 없으므로 이 경로에 넣지 않는다.
   입술 bbox 크기를 MediaPipe의 눈 거리 정규화 값으로 바꾸어 해석하지 않는다.
7. [오프라인 추출기](../../src/voice_coach/visual_feedback/corpus_extract.py)는 운영 작업과
   분리된 새 workspace/resident에서 CORE·MFA sidecar·실제 프레임을 얻는다. GPT 호출은 0회다.
   기존 성공/실패/중단된 추출 폴더를 자동 재분석하지 않는다. 서버 접수·Backend callback도 없다.
8. [컴파일러](../../src/voice_coach/visual_feedback/corpus_dataset.py)는 원본 라벨·영상 SHA,
   추출 영수증, CORE 바이트, MFA·PCM 시간축, 근거 재연산을 확인한 뒤 통계를 다시 산출한다.
   CSV에 적힌 평균이나 모델이 만든 수치를 신뢰해 설치하지 않는다.

## 동일 측정 방식과 30FPS

참조 생성과 사용자 채점은 같은 `measure_phone()`을 사용한다. MediaPipe model SHA,
입술/눈 landmark mapping revision, 품질 설정 SHA가 다르면 비교하지 않는다.
얼굴이 여러 개인 구간은 화자를 임의 선택하지 않는다.

| 성분 | 실제 관측 | 참조 그룹 |
|---|---|---|
| 벌림 | 모음 구간을 4개 시간 위상으로 나누고, 관측된 위상 내부의 실제 프레임 중앙값 | 음소·중성·위상 |
| 원순 형상 | ㅗ·ㅜ·ㅛ·ㅠ의 정면 폭/눈 거리; 3D 돌출 아님 | 음소·중성·위상 |
| 폐쇄 | ㅁ·ㅂ·ㅃ·ㅍ의 최소 관측 벌림, 전체 구간 가시성·2프레임 이상 | 음소·초/종성 |
| 타이밍 | 실제 변화가 있고 3프레임 이상인 구간의 최대/최소 벌림 사건 위상 | 음소·초중종성·앞뒤 음소 |

한 프레임으로도 해당 위상의 정적 형상을 비교한다. 없는 위상을 보간하거나 프레임을
복제해 표본을 늘리지 않는다. 가림이 있으면 가시 프레임의 정적 형상만 남긴다.
2프레임 폐쇄 값도 완전한 접촉을 보증하지 않는다. 기존 0.01 motion cutoff는 사건 추출용이며
정상 발음 허용오차가 아니다. 복합모음은 현재 별도 전이 궤적/원순 성분을 추정하지 않는다.
정적 성분의 다른 단어 비교에는 공조음·발화 속도·얼굴 구조 차이가 남는다.

## 통계와 점수

화자의 dataset namespace SHA에서 결정한 버킷으로 TRAIN 70%, CALIBRATION 20%, TEST 10%를
분리한다. 같은 화자의 영상·문장·반복 프레임은 다른 분할로 이동하지 않는다.
공식 Training 안에서의 이 분리는 공식 Validation 사용과 별개다.

각 그룹의 TRAIN 화자 `s`에 대해 관측값들의 평균 `m_s`와 표본분산 `v_s`를 구한다.
화자 수를 `S`라고 할 때:

```text
μ = mean_s(m_s)
σ² = sample_variance_s(m_s) + mean_s(v_s)
z = (사용자 관측값 − μ) / sqrt(σ²)
score = 100 × exp(−0.5 × z²)
```

따라서 한 화자가 수천 번 녹음해도 화자 평균에 주는 가중치는 다른 화자와 같다.
고정 `0.04` 척도나 `0.01` 만점 허용구간을 쓰지 않는다. 평균에서 1 표준편차는 약 60.7점,
2 표준편차는 약 13.5점이다. `σ²=0`, 참조 없음, 실제 관측 없음은 null이며 0점으로 대체하지 않는다.
분산식은 화자 간·화자 내 변이를 보존하는 추정 규칙으로, 임상적 정상 분포를 입증하지 않는다.

기본 최소 조건은 **그룹마다** TRAIN 10화자·30관측, CALIBRATION 3화자, TEST 3화자다.
한 음소 출현은 위상당 한 번만 기여한다. holdout의 화자 평균 차이와 2SD 내 비율을 기록하고
평균·분산을 holdout으로 다시 맞추지 않는다. `PASSED`는 이 분리/표본/수치 조건 통과이며,
전문가 점수와 상관을 검증했다는 뜻이 아니다. 분할 후 최소 인원이 안 되면 화자를 옮겨 채우지 않는다.

실제로 측정·참조가 있는 위상→성분→음소 순서로 평균을 합산한다. 공개 점수는 기존처럼
소수 첫째 자리 HALF_UP이며, 부분 점수는 `ASSESSED_PHONES_ONLY` 범위로 유지한다.
기존 60% 커버리지를 충족한 입술 점수만 음성 60%·입술 40% 종합점수에 반영한다.
부족하면 음성 종합점수는 유지하고 부분 입술 점수/연습은 별도로 제공한다.
평균과 정확히 같거나 표시 반올림이 적용되면 100점이 가능하지만, 전체 발음 정확도를 보증하지 않는다.

## 피드백·Frontend 계약

서버는 공식 점수를 계산하고, 비공개 `corpusComparisons`에 관측값·평균·표준편차·z·방향·
화자/표본 수를 보존한다. 근거 재검산 때 이 비교값도 다시 확인한다.
GPT에는 사용자의 원본 Seungun JSON과 선택된 파생 근거가 **한 번의 요청**으로 들어간다.
코퍼스의 원본 영상·프레임·화자 ID·라벨 대본은 전달하지 않는다.

새 [프롬프트 보충 지침](../../configs/coaching/aihub_lip_supplement_v1.txt)은
`CORPUS_VISIBLE_SHAPE_PRACTICE`를 설명한다. 낮은 점수의 실제 입술 대상 최대 2개를 먼저
고르고 음성 항목을 포함해 최대 3개 연습을 작성한다. 전체 최대 5문장·300자를 유지한다.
평균보다 작거나 크다는 사실만으로 더 벌리기/닫기/오므리기를 명령하거나 오발음을 단정하지 않는다.
재생할 수 없는 특정 참조 영상을 보라고 하지 않는다. 모델·effort·Standard 설정은 보존한다.

브라우저 공개 API와 기존 필드명은 보존한다:

```text
GET /browser/v1/video-analysis-jobs/{jobId}/summary
score.overallScore
visual.score / visual.scoreKind / visual.scoreScope
coaching.feedback
```

평균·표준편차·각 음소의 좌표/측정값은 이 summary에 추가하지 않는다.
기존 내부 full result/감사용 원본의 보존 계약과 digest를 바꾸지 않는다.

## 실행 순서

Python 3.12와 기존 canonical/visual 환경을 사용한다. 아래 ROOT/DATA는 비밀값이 아닌
운영자가 정한 경로다. 파일은 새 경로로만 생성한다.

```bash
python scripts/aihub_lip_corpus.py unpack --archive DOWNLOAD/download.tar \
  --receipt DOWNLOAD/download.receipt.json --output DATA/source/1.Training/labels-TL50 \
  --maximum-bytes 900000000 --nested-tar
python scripts/collect_aihub538_frontals.py --key-file PRIVATE_AI_HUB_ENV \
  --file-key 55026 --labels DATA/source/1.Training/labels-TL50 \
  --output DATA/source/1.Training/videos-TS50 --maximum-videos 3 \
  --maximum-bytes 10000000000 --stream
python scripts/aihub_lip_corpus.py plan --source-root DATA/source --output DATA/plan.private.json
python scripts/aihub_lip_corpus.py index --plan DATA/plan.private.json \
  --source-root DATA/source --output DATA/manifest.private.json
python scripts/aihub_lip_corpus.py extract --manifest DATA/manifest.private.json \
  --source-root DATA/source --measurements-root PRIVATE_NEW_MEASUREMENTS \
  --configuration PRIVATE_EXTRACTOR_CONFIGURATION
python scripts/aihub_lip_corpus.py compile --manifest DATA/manifest.private.json \
  --source-root DATA/source --measurements-root PRIVATE_NEW_MEASUREMENTS \
  --configuration PRIVATE_EXTRACTOR_CONFIGURATION --revision aihub538-population-v1 \
  --output DATA/reference-catalog.json
```

추출 설정 schemaVersion은 `voice-coaching.aihub538-extractor.v1`이며 `coreRoot`, `h5Root`,
`nltkRoot`, `visualPython`, `landmarkModel`, `modelSha256`, `quality`, `isolatedResident=true`가
필수다. `manageResident=true`는 새 `residentWorkspaceRoot`, `resourceProfile`을 사용해
자체 resident를 준비·종료한다. 직접 socket 지정 방식은 별도 준비한 격리 socket만 사용한다.
운영 socket이나 운영 spool을 설정하지 않는다. `--limit`는 소규모 실검증용이며 완료하지 않은
manifest를 모두 처리한 것처럼 컴파일하지 않는다. `measurements-root`는 해당 workspace 안에 둔다.
실제 실행 scratch는 고정 resident가 요구하는 `workspace/executionId/media`와
`workspace/executionId/core/executionId` 구조를 사용한다. GPU lock의 기존 소유 서비스 계정으로
실행하며 lock의 권한·소유권을 바꾸지 않는다.

충분한 화자가 없으면 `compile`은 보고서만 쓰고 설치 가능한 catalog는 만들지 않는다.
정상 catalog가 확보된 뒤 [후보 H5 생성기](../../deploy/dlpc/build_aihub_lip_h5.py)를 사용한다.
실제 catalog digest를 고정한 direct/internal 실행 identity 등록, 같은 head의 실제 음성/영상·
단일 GPT 생성·서버 응답 회귀를 마친 뒤에만 PR/운영 반영을 진행한다. 현재 운영 identity를
임시 카탈로그나 합성 통계로 교체하지 않는다.

## 검증의 한계

소규모 데이터에서의 가능성 확인을 위해 별도
[음소 조건부 형상 모델](../../src/voice_coach/visual_feedback/corpus_research_model.py)과
[테스트 비디오 평가기](../../scripts/evaluate_aihub_lip_model.py)를 구현했다.
MediaPipe 관측기는 기존 가중치를 사용하고, 새로 학습하는 것은 실제 음소·시간 위상별
벌림·폭·최소 벌림·사건 위상의 평균과 분산이다. 영상 분류 신경망을 새로 학습했다고 표현하지 않는다.

`train-research-model`은 충분한 다화자 참조가 생기기 전에도 실제 완료된 측정으로 실험 모델을
만들 수 있다. 문장 전체를 해시 기준 80/20으로 나눠 한 문장의 프레임이 양쪽에 섞이지 않게 한다.
한 화자가 양쪽에 있을 수 있으므로 `heldOutSentenceSplitIsSpeakerIndependent=false`,
`validationStatus=RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION`을 명시한다. 이 모델은
운영용 화자 분리 catalog가 아니며 population 모드로 H5를 만들 수 없다. 분산이 0이거나 관측이
부족하면 해당 그룹은 없다. 이 경로로 운영용 최소 화자 조건을 통과한 것처럼 표시하지 않는다.
후속 사용자 요청에 따라 명시적 `--research-model`·`--allow-provisional-research` 모드로
같은 모델을 임시 참조 점수로 읽는 v6 catalog·H5 연결을 구현했다. 모델 상태를 변경하지 않으며
점수·피드백·실제 두 GPT 검증·배포 구분은 [연결 보고서](aihub538-runtime-integration-20261109.md)를 따른다.

초기 실제 실험은 다음과 같다. 아래 점수는 평가된 부분의 참조 유사도이며 정답률이 아니다.

| 실제 학습 문장 | 학습/보류 문장 | 학습 그룹 | testvideo2 평가 범위 | 부분 비교점수 |
|---:|---|---:|---|---:|
| 5 | 4 / 1 | 16 | 4개 음소 구간 | 90.4 |
| 12 | 9 / 3 | 38 | 시각 평가 대상 14개 중 9개 | 91.1 |

두 번째 모델에서 벌림 93.0, 폭 형상 66.2, 폐쇄 92.5이며 타이밍은 참조/관측이 부족해 null이다.
두 실행 모두 영상 landmark를 새로 추출했고, 보존 CORE 바이트와 기존 결과는 변경하지 않았다.
실제 GPT 호출은 0회다. 초기 모델의 B2 영수증과 모델·평가 원본은 데이터 폴더의
`model-artifacts/`에 보관한다.

최종 모델은 실제 3화자·60문장 중 48문장으로 학습하고 12문장을 보류했다.
15개 음소 표지에 대해 53개 음소/역할/성분/위상 그룹을 학습했다. 두 테스트 영상에서
landmark를 새로 추출했으며, 모두 시각 평가 대상 14개 중 9개를 채점했다.
부분 참조 유사도는 testvideo1 88.8점, testvideo2 92.4점이다. 타이밍은 둘 다 null이다.
3회 화자 분리 검증도 별도로 수행했으며, 이는 분포의 화자 간 일반화 검사이지
발음 정오 판별 정확도 검사가 아니다. 최종 수치·모델 SHA·미평가 사유는
[검증 보고서](aihub538-model-validation-20261109.md)에 기록했다.

```bash
python scripts/aihub_lip_corpus.py train-research-model --manifest DATA/manifest.private.json \
  --source-root DATA/source --measurements-root PRIVATE_NEW_MEASUREMENTS \
  --configuration PRIVATE_EXTRACTOR_CONFIGURATION --output DATA/research-model.json
python scripts/evaluate_aihub_lip_model.py --model DATA/research-model.json \
  --job RETAINED_DESIGNATED_TEST_JOB --core-root ORIGINAL_CORE --h5-root ORIGINAL_H5 \
  --output NEW_TEST_OUTPUT --visual-python VISUAL_PYTHON --landmark-model LANDMARK_MODEL
```

합성 회귀 검사는 계산, 화자 분리, 중복 배제, 최소 관측 조건, 근거 replay, GPT 입력 선택과
공개 계약을 검증한다. 실제 데이터셋 전체의 정확도, 전문가 임상 타당성, 새로운 프롬프트의
실제 GPT 피드백 품질 검증과 구분한다. 비공개 실제 추출 결과와 기준표가 있어야 후자를 진행한다.
프롬프트 변경은 [OpenAI 공식 지침](https://developers.openai.com/api/docs/guides/prompt-engineering)의
근거 전달·명확한 지시·변경 후 평가 원칙을 참조했다.
