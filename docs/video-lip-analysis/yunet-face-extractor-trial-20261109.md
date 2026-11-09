# YuNet 얼굴 검출기 교체 후보

## 범위

얼굴을 찾고 입술 좌표를 추출하는 구조를 유지한다. 얼굴 검출은 OpenCV YuNet
`face_detection_yunet_2023mar.onnx`로 바꾸고, 현재 고정된 기준점 가중치
`face_landmarks_detector.tflite`를 LiteRT로 직접 실행한다.
MediaPipe FaceLandmarker와 그 내부 BlazeFace 얼굴 검출기는 후보 경로에서 실행하지 않는다.
기준점 가중치는 기존 Google 모델을 재사용하므로 모든 모델이 새 모델이라는 의미는 아니다.

현재 운영 선택은 변경하지 않았다. 기존 음성 CORE·GPT·API 응답·저장 결과도 변경하지 않는다.
이 후보는 얼굴·입술 추출 시험이며 실제 음성 분석/GPT/종합점수 검증이나 배포 완료 증빙이 아니다.

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

## 점수 연결 전 필요한 작업

기존 AI-Hub 참조는 기존 task SHA에 결속돼 있다. 새 추출기는 다른 SHA를 반환하므로
기존 참조와 혼합해 점수를 계산하면 `CORPUS_FEATURE_PROTOCOL_MISMATCH`로 거부된다.
입술 좌표 topology가 같다는 이유로 기존 SHA를 가장하거나 이 검사를 제거하지 않는다.

운영 점수에 연결하려면 같은 AI-Hub 원본을 새 추출기로 재측정하고 동일 speaker/sentence split으로
참조 통계를 다시 만들어야 한다. 그 후 새 카탈로그·runtime·H5를 고정하고 실제 음성/영상·GPT
검증을 거쳐야 한다. 이번 비교의 100% 관측률을 입술 점수 100점으로 바꾸지 않는다.

## 출처

- [OpenCV YuNet 공식 모델·라이선스](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [Google FaceLandmarker 모델 구성](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)
- [Google 기준점 추정 graph](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/cc/vision/face_landmarker/face_landmarks_detector_graph.cc)
- [Google canonical 얼굴 모델](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/modules/face_geometry/data/canonical_face_model.obj)
- [LiteRT 설치 안내](https://developers.google.com/edge/litert/overview)
