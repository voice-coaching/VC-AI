# YuNet 얼굴 검출기 교체 후보

## 범위

얼굴을 찾고 입술 좌표를 추출하는 구조를 유지한다. 얼굴 검출은 OpenCV YuNet
`face_detection_yunet_2023mar.onnx`로 바꾸고, 현재 고정된 기준점 가중치
`face_landmarks_detector.tflite`를 LiteRT로 직접 실행한다.
MediaPipe FaceLandmarker와 그 내부 BlazeFace 얼굴 검출기는 후보 경로에서 실행하지 않는다.
기준점 가중치는 기존 Google 모델을 재사용하므로 모든 모델이 새 모델이라는 의미는 아니다.

초기 추출 비교 단계에서는 운영 선택을 변경하지 않았다. 이후 사용자 승인에 따라 참조 재측정과
운영 연결을 추가했다. 기존 음성 CORE·GPT 모델·API 응답 구조·저장 결과는 유지한다.
운영 반영 완료 여부는 별도 배포 영수증으로 확인하며 이 문서 자체가 배포 완료 증빙은 아니다.

## 구현

- [격리 worker](../../src/voice_coach/visual_feedback/adapters/canonical_feature_worker.py)는
  새 번들의 명시적 manifest가 있을 때만 YuNet 경로를 실행한다. 기존 task는 기존 경로다.
- [새 adapter](../../src/voice_coach/visual_feedback/adapters/yunet_landmarks.py)는
  YuNet → 눈 기준으로 회전 보정한 얼굴 crop → LiteRT 기준점 → 원본 좌표 복원 →
  기존 눈 사이 거리 정규화·입술 벌림/폭/면적 계산을 수행한다.
- 머리 방향 QC는 고정 canonical 얼굴 모델을 사용하는 근사 pinhole PnP다.
  실제 카메라 보정이나 3-D 발음 측정을 의미하지 않는다. 이전 pose matrix 방식과 다르므로
  참조 재추출 때 같은 정책을 적용해야 한다.
- 네트워크·자식 프로세스 차단, CPU 1개 고정, 모델 SHA 검증과 기존 worker 자원 한도를 유지한다.
  다수 얼굴은 기존처럼 제외한다. 새 경로는 프레임마다 검출하며 추적 confidence는 사용하지 않는다.
- 새 번들에 검출기·기준점·자세 기준·라이선스와 구성 manifest를 함께 고정한다.
  전역 모델 파일을 바꾸지 않으며 임의 경로·잘못된 파일·다른 runtime 버전을 허용하지 않는다.

[번들 생성 도구](../../deploy/dlpc/build_yunet_model_bundle.py)와
[별도 환경의 의존성](../../requirements/visual-yunet-candidate.txt)을 제공한다.
LiteRT 2.3.0, OpenCV 4.12.0.88, NumPy 2.2.6을 사용한다. 운영 scorer 환경에는 설치하지 않았다.

후보 번들 SHA-256: `4fb24c5614d9a0bf9ac34b1dae46908e42e11a745e8e1a160515630a83f2fec8`.
원래 task SHA-256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
구성별 pin·전처리 revision은 adapter의 `PINS`, `REVISION`에 있다.

## 지정 시험 영상 비교

같은 파일·해상도·타임스탬프·quality 설정을 사용해 순차 실행했다. 검출·존재 confidence는
두 경로 모두 0.7, 얼굴 크기 최소 24px, yaw/pitch 제한은 각각 30/20도다.
다른 모델의 confidence가 같은 확률로 보정됐다는 의미는 아니다.

| 영상 | 기존 유효 입술 관측 | YuNet 후보 유효 입술 관측 | 기존 전체 추출 시간 | 후보 전체 추출 시간 |
| --- | --- | --- | --- | --- |
| testvideo1.mp4 | 145/145 | 145/145 | 10.2963초 | 10.3341초 |
| testvideo2.mp4 | 81/81 | 81/81 | 7.4355초 | 6.5807초 |

시간은 ffprobe·디코딩·모델 초기화·입술 추출·PCM 해시 검증을 포함한 전체 port wall time이다.
순수 모델 추론 시간이나 서버 종합 분석 시간으로 해석하지 않는다. 1회씩 비교한 관측이며
기존 검출기도 성공한 영상이므로 검출 정확도 우위나 운영 장애 해결을 입증하지 않는다.

눈 거리로 정규화한 벌림/폭/면적의 공통 프레임 평균 절대 차이는 각각
testvideo1에서 0.00676/0.01625/0.00204,
testvideo2에서 0.00533/0.00788/0.00145였다. 정답 오차가 아니라 두 추출기 간 차이다.

[비교 도구](../../scripts/compare_face_extractors.py)는 원본 bytes와 타임스탬프 일치,
새 번들 SHA, 사용한 source 파일 digest를 기록하고 원본·기존 결과를 덮어쓰지 않는다.
실행 기록은 서버의 `/var/lib/intelligentai/face-detector-comparison/yunet-v2/comparison.json`에 있다.
이 비교에서 음성 CORE·GPT·외부 전송은 실행하지 않았다.

분석 회귀와 신규 좌표 복원·PnP·번들 무결성 검사 145개가 통과했다. 별도 검정·흰색
무얼굴 프레임 2개는 모두 `NO_FACE`로 처리돼 입술 좌표를 만들지 않았다.

## 참조 재측정과 운영 연결

기존 AI-Hub 참조는 기존 task SHA에 결속돼 있다. 새 추출기는 다른 SHA를 반환하므로
기존 참조와 혼합해 점수를 계산하면 `CORPUS_FEATURE_PROTOCOL_MISMATCH`로 거부된다.
입술 좌표 topology가 같다는 이유로 기존 SHA를 가장하거나 이 검사를 제거하지 않는다.

같은 AI-Hub 원본 60문장을 새 추출기로 재측정했다. 기존 3명·학습 48문장/보류 12문장 분할을
유지하고 53개 통계 그룹을 생성했다. 보존 근거 묶음의 303개 파일 SHA와 원본 영상·CORE·MFA를
검증한 뒤 얼굴 좌표만 새로 추출했다. 음성 CORE 재실행과 GPT 호출은 모두 0회다.
[재측정 도구](../../scripts/reextract_yunet_reference.py)는 새 폴더에 근거·검산 영수증을 저장한다.

참조 모델 SHA-256: `9a5ad565b19161d383e019d4beb07e66cf565484bdfb0e61b128ab953c117cc7`.
근거·참조 영수증은 `/var/lib/intelligentai/yunet-reference-20261109-v1/`에 있다.
신뢰 상태와 점수 의미는 종전 연구 참조 계약을 유지한다. 관측률 100%를 입술 점수 100점으로
바꾸지 않으며, 타이밍 등의 근거가 없는 성분은 null로 유지한다.

실제 미검출 사례의 동일한 정규화 영상 153프레임에서는 기존 추출기 관측 0개, 후보 관측 153개를
확인했다. 이후 보존 CORE와 새 참조를 별도 폴더에서 결합해 입술 점수 산출·근거 재검산도
확인했다. 기존 사용자 결과를 덮어쓰거나 해당 촬영본을 GPT에 보내지 않았다.

[H5 생성 도구](../../deploy/dlpc/build_yunet_lip_h5.py)는 기존 프롬프트·음성 채점·CORE를 유지하고
참조 카탈로그와 영상 runtime 설정만 바꾼 새 H5 두 개를 생성한다.
direct manifest는 `19802f16e8bcd8b8e9ada0148eb24e0aa2d6fb30b56769dae05c84794d350430`,
internal manifest는 `d3e13ad9f3b5ad25d209736e7fbd8642c2df2c081d66cf1587e6def8834aaae4`다.

운영 연결용 영상 Python은 `/home/2020067/.local/share/intelligentai-visual-yunet-20261109-v1/bin/python`,
모델은 `/workspace/intelligentai/validation/canonical_yunet_visual_20261109_v1/model.task`다.
기존 scorer·모델은 덮어쓰지 않는다. 새 영속 환경은 고정된 `/opt` CPython을 사용하며, 실제 서비스
UID에서 추출한 결과가 참조 재측정 환경과 동일함을 확인했다.

readiness는 선택된 모델에 따라 MediaPipe 또는 LiteRT/OpenCV 의존성을 구분해 검사한다.
배포 helper는 새 H5와 모델 SHA·실행 환경 영수증을 검증하고 소스·H5·모델·환경 선택을 함께
반영한다. 모델 bytes를 영속 번들에 포함해 cold 복구에서도 재설치하며, 영상 전용 Python은
복구 번들과 같은 영속 사용자 볼륨에 둔다. 설치 실패 시 기존 설정으로 롤백한다.

실제 음성/장애 회귀 7건·시험 영상 2건은 별도 승인된 최대 9회 생성으로 검증한다.
후보 HEAD/tree·CORE/H5·fixture SHA에 결속된 영수증과 배포 후 readiness·복구 선택을 별도로
기록한다. 새로운 요청부터 새 추출기를 사용하며 과거 사용자 작업을 자동 재분석하지 않는다.

## 출처

- [OpenCV YuNet 공식 모델·라이선스](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [Google FaceLandmarker 모델 구성](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)
- [Google 기준점 추정 graph](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/cc/vision/face_landmarker/face_landmarks_detector_graph.cc)
- [Google canonical 얼굴 모델](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/modules/face_geometry/data/canonical_face_model.obj)
- [LiteRT 설치 안내](https://developers.google.com/edge/litert/overview)
