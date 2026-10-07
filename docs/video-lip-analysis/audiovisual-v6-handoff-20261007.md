# 음성·영상 v6 인계

## 외부 계약

기존 `POST /api/training-sessions/{sessionId}/analyze`와 retry URL, 동의 body를 유지한다. 선택된 영상 recording과 서버 preparation을 사용한다.

- 영상 요청 헤더: `X-Analysis-Result-Schema: voice-coaching.runpod-analysis-result.v6`.
- 음성 전용은 기존 v5. v6에 영상이 없으면 접수하지 않는다.
- 기존 `GET /api/v3/analyses/{analysisId}`에서 영상은 public view v3/profile `CANONICAL_AUDIOVISUAL_20261007_V6`.
- private 전달은 request v4 → result v6 + handoff v2. `MEDIA_RECEIPT`, `VISUAL_EVIDENCE`, association v2를 추가한다.
- v6는 검증 없는 result-publish를 사용하지 않는다. 검증된 spool만 SAVING/RETRYING, 저장 완료는 SAVED로 구분한다.
- 입술 상세는 최대 128개와 `omittedPhoneAssessmentCount`. GPT 관찰은 최대 12개. private 근거에는 전체 평가 구간이 남는다.

## 설정

Backend 기본값은 다음과 같다.

```properties
analysis.canonical.audiovisual.worker-enabled=false
analysis.canonical.audiovisual.admission-enabled=false
```

AI는 기존 handoff 설정에 더해 다음이 필요하다. 아래는 자동 적용 명령이 아니다.

```text
AI_CANONICAL_AUDIOVISUAL_ENABLED=false
AI_CANONICAL_VISUAL_PYTHON=<MediaPipe 설치·검증된 Python 절대경로>
AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH=<face_landmarker.task 절대경로>
AI_CANONICAL_LLM_ROOT=<새 audiovisual H5 절대경로>
```

model SHA: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
Linux seccomp, `/usr/bin/ffprobe`, `/usr/bin/ffmpeg`를 사용한다. 영상 특징은 CPU 실행이다.

AI `/health/audiovisual`, Backend `/api/internal/ai/worker-readiness/audiovisual`은 기존 서버 인증을 요구한다. 공통 schema 5개 digest, H5 pin, 기존 handoff readiness, spool 예산을 확인한다. candidate identity는 아직 실행 승인 목록에 없다.

## 담당자 전환 순서

1. v6 admission을 닫은 채 Backend V41–V43 및 dual reader/verifier를 검증·배포한다. `stage_backend_verifier_bundle.py`의 확장 closure와 audiovisual H5를 함께 포함한다. 과거 결과에 필요한 frozen 패키지는 유지한다.
2. 개발자가 DLPC 실영상/자원/취소/저장 QA를 수행한다. 관찰 전용 출시는 보정 점수를 제공하지 않음을 확인한다. QA 근거에 따라 실행 identity를 별도 등록한다.
3. 새 app/H5/model을 기존 DLPC 재기동·복구 bundle에 포함하고 digest를 갱신한다. B2 복구와 AWS 비밀 반환은 기존 절차를 따른다. 이번 코드 작업은 운영 bundle·비밀값을 변경하지 않았다.
4. 양쪽 worker readiness 확인 후 Backend admission을 마지막으로 켠다. FE는 VIDEO capability와 v6 광고가 확인되어야 요청한다.
5. rollback은 v6 admission을 닫고 drain한다. v6 데이터, dual reader/verifier, migration은 유지한다. 파괴적인 migration rollback이나 원본 삭제를 수행하지 않는다.

실제 구현과 남은 참조 데이터·QA는 [진행 기록](implementation-progress-20261007.md)을 따른다.
