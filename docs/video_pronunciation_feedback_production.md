# 영상 기반 발음 피드백 프로덕션 경계와 상태

2026-09-09 새 RunPod A6000 INT8에서 두 실제 테스트 영상을 실행했다.
MediaPipe 의존성과 Clova의 반복 로그·요청 처리를 보완한 뒤 두 영상의
음성·입술·Clova 결합 결과가 모두 검증을 통과했다. Clova 생성은 약 33초였다.
위 결과는 짧은 피드백 정책의 [실행 기록](runpod_clova_runtime_fix_20260909.md)이다.
이후 적용한 관찰·연습 단계 보존과 분석 보완 계획은
[상세 피드백 변경](clova_detailed_feedback_20260909.md)을 따른다.

## 결론

P6은 고정 런타임과 보정 아티팩트를 비어 있지 않은 표본에서 확인했다는 최소
요약이고, P8은 지정한 평가 실행에서 claim 위반이 0건이었다는 최소 요약이다.
P8 표본을 P6 표본과 분리해야 한다는 계약은 삭제했으므로 같은 사용자 영상들을
보정과 평가에 함께 쓸 수 있다. 두 요약은 `runtime_bundle.json` 안에 포함하며
요청마다 생성하거나 원본 데이터셋을 다시 읽지 않는다. 별도 runtime
eligibility 계약은 존재하지 않는다.

요청마다 수행하는 것은 동영상 입수와 삭제, 오디오 추출, Seungun 판정,
랜드마크 측정, 영상 QC/A·V 동기화 **측정**, 승인된 정책 적용, 그리고
근거가 승인된 문구를 Clova가 표현 계획으로 선택하는 작업뿐이다.

## 권한과 데이터 흐름

1. 호스트가 명시적 얼굴 영상 처리 동의를 확인한다.
2. H.264/HEVC 영상과 AAC 오디오가 들어 있는 MP4를 제한된 임시 작업공간에
   복사한다.
3. 내장 오디오를 PCM으로 추출하고 고정된 Seungun 검출기로 분석한다.
4. Seungun이 선택한 단일 음소 구간만 시각 sidecar의 anchor로 전달한다.
5. 영상 전체의 QC와 A·V 동기화를 측정하고, 선택 음소 구간의 프레임에서
   입술 landmark를 측정한다.
6. production visual release가 `disabled`이거나 로딩에 실패하거나 해당 음소에
   승인된 시각 claim이 없으면 시각 피드백만 fail-closed 한다. 오디오 피드백은
   유지하고, 안전하게 정규화된 실패 이유를 Clova의 승인 claim에 추가한다.
7. Seungun의 승인 문구와, 존재한다면 sidecar의 승인된 시각 문구만 결합해
   loopback Clova 서버로 전달한다. 원본 음성·영상, 경로, landmark 배열,
   영수증 해시는 전달하지 않는다.
8. Clova 응답은 issue/advice/claim ID, 모델 ID, 모델 revision을 검증한다.
   승인된 claim을 하나라도 누락하거나 새 사실을 만들면 정확한 승인 문구를
   그대로 반환하는 카탈로그 폴백을 사용한다.
9. 성공 여부와 관계없이 입수한 영상 사본과 추출 PCM을 삭제한다.

시각 sidecar는 Seungun의 발음 판정을 대체하거나 새 오류 음소를 선택할 수
없다. 같은 시도의 같은 Seungun 음소 anchor를 보충할 수만 있다.

### Clova 학습자 설명 정책

호스트는 `voice_coach.grounded_advice_input.v4`를 만들 때 g2pK 기대 음소와
원문 자모를 정렬해 선택 음소의 단어·음절 위치를 먼저 확정한다. 예를 들어
`쌀을 깨끗이 씻어요`의 9번 기대 음소 `ㅅ`에는
`‘깨끗이’의 두 번째 음절 ‘끗’`이라는 `learnerAnchor`를 넣는다. Clova가
같은 문장의 다른 `ㅅ`과 혼동해 위치를 추측하도록 두지 않는다.

Clova는 `voice_coach.grounded_advice_output.v3`에서 승인 claim을 선택하는 것에
더해 `learnerMessage`를 작성한다. 메시지는 일반 사용자가 바로 이해할 수 있는
쉬운 한국어로 관찰·단어 연습·문장 재녹음·비교를 설명한다. 보통 4~8문장을
권하며 최대 1,000자를 허용한다. 음소 오류의 첫 문장은 `learnerAnchor`를
그대로 복사한 뒤 목표 음소를 명시해야 한다. 단어·음절 anchor가 빠지거나
바뀐 응답은 코드 검증에서 거부한다. claim마다 `requiredFragments`를 지정해
위치·핵심 관찰·단어 안에서의 연습·재녹음 비교·분석 한계가 본문에도 남도록
검증한다. 필수 상세를 생략한 응답은 전체 승인 문구로 폴백한다. 기존 v3
입력은 v4로 읽을 수 있으며 출력 계약은 v3를 유지한다.

Seungun의 `observedCandidate`는 실제 전사가 아니므로 현재 제품 메시지에는
노출하지 않는다. Clova는 detector, threshold, score, confidence, schema,
claim ID 같은 내부 용어를 사용자에게 보여줄 수 없고, 승인 claim에 없는
혀 위치·입술 모양·호흡법 등 새로운 조음 사실을 만들 수 없다. 출력은
issue/advice identity, 전체 claim ID와 필수 상세 보존, target 음소 포함, 길이,
개인정보와 내부 용어를 코드로 재검증한다. 이는 자유 문장 전체의 의미 검증을
대신하지 않는다. 위반하거나 클라이언트 기본 180초 안에 응답하지 않으면
승인 원문으로 폴백한다. 서버 생성 상한은 150초, 출력 상한은 512토큰이다.

## Production visual release

production visual release는 `enabled` 또는 `disabled` 상태다.

- `enabled`: 다음 네 파일로 구성된다.

- `release.json`
- `runtime_bundle.json`
- `video_qc_decision_policy.json`
- `av_sync_decision_policy.json`

- `disabled`: `release.json` 하나만 가지며 `reason_codes`에 시각 claim을
  비활성화한 이유를 기록한다. 근거가 없는 정책이나 P6/P8 통과 값을 만들지
  않으면서도 프로덕션이 의도적으로 audio-only 상태임을 명시한다.

`release.json`은 나머지 세 파일의 원시 바이트 SHA-256과 자체 canonical
digest를 고정한다. 릴리스 디렉터리는 소유자 전용이고 각 파일은 소유자 소유,
단일 hard link, mode `0400`인 일반 파일이어야 한다. 로더는 symlink,
중복 JSON key, non-finite 수, digest drift, 정책 종류 및 runtime bundle의
정책 identity 불일치를 모두 요청 영상에 접근하기 전에 거부한다.

현재 enabled release는 두 testvideo를 보정과 평가에 함께 사용하고, Seungun
고정 vocabulary의 한국어 음소 40개에 동일한 외부 입술 벌림 cue를 허용한다.
이는 모든 음소의 내부 조음이 입술로 식별된다는 뜻이 아니다. 혀·성문·기류를
추론하지 않고 화면에서 측정된 입술 벌림이 공통 기준보다 큰지 작은지만 말한다.

```bash
.venv/bin/python scripts/create_universal_lip_production_release.py \
  --output-directory artifacts/visual_feedback/releases/production_visual \
  --release-id production_visual_v2 \
  --calibration-video data/testvideo/testvideo1.mp4 \
  --calibration-video data/testvideo/testvideo2.mp4 \
  --calibration-lip-aperture 0.07285225122514805 \
  --calibration-lip-aperture 0.008025224045252148 \
  --evaluation-video data/testvideo/testvideo1.mp4 \
  --evaluation-video data/testvideo/testvideo2.mp4
```

생성기는 보정 2건·평가 2건의 digest를 compact receipt에 묶고 세 runtime
산출물을 원자적으로 패키징한 뒤 strict reload한다. 대용량 데이터셋은 읽지
않는다. 기존 `--visual-release` CLI와 production runtime bundle/QC/A·V
artifact의 개별 입력 방식은 제거했으며 호환 경로로 남기지 않는다.

실행 시에는 새 옵션만 사용한다.

```bash
exec 9<data/testvideo/testvideo1.mp4
.venv/bin/python scripts/run_pronunciation_video_attempt.py \
  --video-fd 9 \
  --prompt-id LOCAL-TEST-RICE-CLEAN-V1 \
  --consent-local-processing \
  --landmark-model models/mediapipe/face_landmarker.task \
  --production-visual-release artifacts/visual_feedback/releases/production_visual \
  --nltk-data-root /home/cmsl-dgx01/nltk_data \
  --device cuda
```

### 40점 입술 형상 closed beta

기존 production release를 바꾸지 않고, 명시적으로 선택한 요청에만 40점 입술
분석 overlay를 켤 수 있다. 기존 4점 worker와 source-pinned artifact는 그대로
두며, 별도 격리 worker가 낸 얼굴 수와 공통 4점 좌표가 기존 worker 결과와
일치할 때만 40점 후보를 채택한다.

```bash
exec 9<data/testvideo/testvideo1.mp4
.venv/bin/python scripts/run_pronunciation_video_attempt.py \
  --video-fd 9 \
  --prompt-id LOCAL-TEST-RICE-CLEAN-V1 \
  --consent-local-processing \
  --landmark-model models/mediapipe/face_landmarker.task \
  --production-visual-release artifacts/visual_feedback/releases/production_visual \
  --closed-beta-lip-geometry-artifact \
    artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json \
  --nltk-data-root /home/cmsl-dgx01/nltk_data \
  --device cuda
```

성공하면 `closed_beta_lip_trajectory.status=enabled`,
`attempt_status=observed`이고 공개 supplement에
선택 음소 구간의 입 너비, 외곽/내곽 벌림, 외곽/내곽 면적, 입꼬리 높이 차이와
가능한 경우 형상 변화율이 추가된다. 40/24점 배열과 영상 프레임은 supplement나
LLM payload에 들어가지 않는다. 이 overlay는 관찰값을 설명할 뿐 새 음소,
발음 정답, 교정 행동을 결정하지 않으므로 출력의
`contains_pronunciation_truth`와 `contains_action_truth`는 모두 `false`다.

artifact가 잘못됐거나 production 의존성이 없으면 overlay만 `failed_closed`로
둔다. 설정은 유효하지만 해당 요청의 40점 worker 또는 기존 worker와의 parity가
실패하면 `attempt_status=unavailable`과 supplement notice를 남긴다. 두 경우 모두
기존 audio 및 검증된 4점 production 경로는 유지한다. 이 옵션은 로컬 production
entrypoint 연결이며 외부 API 배포·트래픽 전환을 뜻하지 않는다.

출력의 `production_visual_release.status`는 `enabled`, `disabled`,
`failed_closed`, `not_configured` 중 하나다. `disabled` 또는 `failed_closed`이면
`reason_codes`가 함께 나온다.

### Clova에 포함하는 실패 이유

production visual release 또는 시각 런타임이 실패해도 Seungun이
`coaching_ready`이면 오디오 피드백은 계속 생성한다. 실패 코드는 경로·해시·예외
원문을 제거한 학습자용 문장으로 바꾸고
`VISUAL_FEEDBACK_UNAVAILABLE_REASON` 승인 claim으로 Clova에 전달한다. Clova는
그 문장을 마지막 문장에 그대로 포함해야 하며, 누락하면 전체 응답을 승인
claim의 결정적 폴백 문장으로 교체한다.

주요 분류는 다음과 같다.

| 코드 | 사용자에게 설명하는 의미 |
|---|---|
| `production_evidence_unavailable` | 보정·평가 근거가 없는 disabled release |
| `production_visual_release_unavailable` | release 파일을 읽을 수 없음 |
| `production_visual_release_not_private` | release 파일 권한을 신뢰할 수 없음 |
| `production_visual_release_digest_mismatch` | release 무결성 검증 실패 |
| `production_visual_release_policy_mismatch` | bundle과 QC/A·V 정책 불일치 |
| `skipped_no_selected_phone` | 시각 분석할 음소 구간을 정하지 못함 |
| `video_decode_failed` | 필요한 영상 프레임을 읽지 못함 |
| `detector_failed` | 입술 위치를 안정적으로 찾지 못함 |
| `blocked_reliability` | 영상 품질 또는 A/V 동기화 기준 미통과 |

disabled 또는 로드 실패인 경우에도 실제 실패 이유가 Clova claim에 포함되는
audio-only 폴백은 유지된다. 현재 기본 release는 enabled이므로 두 testvideo의
검증 결과에서는 이 실패 claim이 아니라 실제 시각 claim이 전달됐다.

## Seungun과 Clova 런타임 분리

Seungun은 저장소의 고정 환경인 `transformers==5.14.1`을 사용하고, 로컬
HyperCLOVA X는 모델 호환 환경인 `transformers==4.52.4`를 사용한다. 두 모델을
하나의 Python 환경에 설치하지 않는다. Seungun은 호스트의 `.venv`에서
실행하고 Clova는 `deploy/hyperclova/compose.yaml`의 GPU 컨테이너에서 실행한다.

Clova 모델은 이미지에 포함하지 않고 고정 revision 디렉터리를 read-only로
mount한다. 서비스는 loopback 포트에만 publish되고 Hugging Face와 Transformers는
offline 모드로 실행한다. root filesystem은 read-only이고 Linux capability를
모두 제거한다. 호스트 수준의 완전한 egress 차단이 필요한 배포에서는 별도
방화벽 정책을 적용해야 한다.

```bash
docker compose -f deploy/hyperclova/compose.yaml up --build -d
docker compose -f deploy/hyperclova/compose.yaml ps
curl --fail --silent http://127.0.0.1:8000/health
```

health가 `ready`이고 모델 revision이 설정과 일치한 뒤 호스트에서 실행한다.

```bash
exec 9<data/testvideo/testvideo1.mp4
.venv/bin/python scripts/run_pronunciation_video_attempt.py \
  --video-fd 9 \
  --prompt-id LOCAL-TEST-RICE-CLEAN-V1 \
  --consent-local-processing \
  --landmark-model models/mediapipe/face_landmarker.task \
  --production-visual-release artifacts/visual_feedback/releases/production_visual \
  --nltk-data-root /home/cmsl-dgx01/nltk_data \
  --device cuda \
  --advice-endpoint http://127.0.0.1:8000/v1 \
  --advice-revision a6cdfd3464d1b767259cad23e164eaf39d3e3960
```

종료는 `docker compose -f deploy/hyperclova/compose.yaml down`으로 수행한다.
동시 운영 시 GB10 통합 메모리를 Clova와 Seungun이 공유하므로 health뿐 아니라
실제 동일 시도 실행을 배포 검증에 포함한다.

### A6000의 메모리 설정

2026-09-09 RunPod 이전에서는 사용자가 A6000 + 8bit 구성을 선택했다.
`--quantization bnb-int8 --gpu-memory-gib 40 --cpu-memory-gib 8
--maximum-input-tokens 4096`을 사용하며 bitsandbytes 0.50.2를 고정한다.
Docker에서는 `compose.yaml` 뒤에 `deploy/hyperclova/compose.int8.yaml`을 적용한다.
기본 BF16 경로는 유지한다. 실행 시 INT8 변환이므로 원본 BF16 디스크 파일도 필요하다.
실제 이전·적재 상태는 [실행 기록](runpod_b2_storage_migration_execution.md),
재구성 방법은 [RunPod 배포 준비](../deploy/runpod/README.md)를 따른다.
최초 영상 실행의 Clova 시간 초과는 반복 경고 로그를 억제한 후 해결됐다.
원본 모델과 INT8 threshold 6.0은 유지했다. BF16 대비 양자화 품질과
동시 사용자 처리 성능은 검증하지 않았다.

48GB VRAM의 A6000에서 약 66.6GB의 BF16 HyperCLOVA 가중치를 사용하려면 CPU로
일부 가중치를 분산해야 한다. 서버 CLI는 `--gpu-memory-gib`와
`--cpu-memory-gib`를 받으며, Compose에서는 각각 `HYPERCLOVA_GPU_MEMORY_GIB`와
`HYPERCLOVA_CPU_MEMORY_GIB`로 설정한다. 기존 기본값은 GPU 110GiB, CPU 8GiB다.
CPU 예산을 늘리는 것은 양자화나 가중치 변경을 수행하지 않는다.

A6000의 초기 검증 후보는 GPU 40GiB, CPU 48GiB다. 이는 allocation의 상한이며
메모리 예약 또는 실제 적재 성공을 의미하지 않는다. CPU RAM, KV cache와 같은
동적 사용량, Seungun 동시 실행 여유, CPU offload에 따른 지연을 실제 호스트에서
검증해야 한다. 모델 preload, `ready` health 및 grounded 응답 검증 전에는
배포 완료로 판정하지 않는다.

기존 PyTorch/CUDA 고정 버전도 별도로 확인한다. CUDA 12.8 기본 RunPod 이미지가
있다는 이유로 현재 CUDA 13 wheel의 호스트 드라이버 호환성을 가정하거나
PyTorch를 자동으로 하향하지 않는다. RunPod에서 컨테이너 중첩 실행과 read-only,
loopback, 임시 저장소 및 프로세스 격리 경계를 재현했는지도 별도 검증 대상이다.

## 현재 구현 상태 (기존 release 2026-09-02, closed beta 2026-09-05)

### 현재 작업 범위

현재 로컬 검증 범위는 `data/testvideo`의 두 MP4에 대한 단일 요청 실행으로
제한한다. 이 경로는 AI Hub 538 P6 준비 데이터, P8 locked partition 또는
대규모 보정 작업을 읽지 않는다. P6/P8은 두 영상의 digest와 compact 실행
요약만 release-time에 만들고 요청마다 다시 생성하지 않는다.

2026-09-01에 범위를 벗어나 시작했던 AI Hub 538 P6 v11 재실행은 중단했다.
해당 실행은 668GB의 준비 데이터셋을 대상으로 했으며 두 testvideo의 기능
검증에는 필요하지 않았다. 이후 로컬 영상 검증에서는 이 데이터셋 경로를
사용하지 않는다.

구현됨:

- MP4 admission/probe, AAC 추출, 제한 작업공간, 종료 시 삭제
- 고정 Seungun 검출기와 신뢰된 prompt/expected-phone 경계
- MediaPipe 입술 landmark, 영상 QC 후보 측정, 내용 기반 A·V 동기화 후보 측정
- 최소 P6/P8 요약이 포함된 runtime bundle이 없을 때 시각 claim fail-closed
- 40개 한국어 음소에 대한 외부 입술 벌림 claim과 enabled/disabled release 로더
- 명시적 closed-beta 옵션에서 별도 격리 40점 추출, P6 24점 mapping, 6개 공개
  형상 관찰값과 구간 변화율
- 삭제 정렬 음소의 인접 음소 기반 예상 시간 구간 복원
- Seungun + 승인된 시각 claim을 한 번의 grounded Clova 호출로 결합하는 계층
- Clova의 모델 ID/revision 및 모든 claim 보존 검증, 정확 문구 폴백

실제 `data/testvideo/testvideo1.mp4`로 확인됨:

- 전체 시도 상태: `audio_completed_visual_supplemented`
- Seungun: `coaching_ready`
- 선택된 음소: 목표 `ㅅ`, 모델 후보 `ㄱ`, 2652–2673 ms
- 시각 단계: `completed`; 영상 구간 2493–2514 ms
- 입술 벌림 비율 `0.072852...`, 두 영상 기준 중심보다 커
  `reduce_visible_lip_opening` claim 승인
- 제한 영상 사본/PCM cleanup: `completed`
- 고정 HyperCLOVA X SEED Think 32B revision
  `a6cdfd3464d1b767259cad23e164eaf39d3e3960`의 loopback health 확인
- 같은 영상의 Clova 결합 결과: audio+visual `validated_grounded_plan`,
  `fallback_reason=null`
- 최종 문장: `‘깨끗이’의 두 번째 음절 ‘끗’에 해당하는 ‘ㅅ’ 소리를 천천히
  분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 그리고 입술을 조금 덜
  벌리고 다시 발음해 보세요.`

2026-09-05에 같은 production CLI에 40점 closed-beta 옵션을 추가해 다시
확인함:

- 종료 코드 `0`, 전체 상태 `audio_completed_visual_supplemented`, production
  release `production_visual_v2/enabled`, closed beta `enabled/observed`
- 기존 입술 벌림 action의 supporting evidence는 기존 4점 cue 한 건만 유지
- 선택 음소 구간에서 화면 너비 대비 입 너비 `0.161`, 외곽 벌림 `0.305`,
  내곽 벌림 `0.074`, 외곽 면적 `0.226`, 내곽 면적 `0.042`, 입꼬리 높이 차이
  `0.002`, 최대 형상 변화율 `0.428/초`를 공개 aggregate로 생성
- combined feedback에 위 관찰값과 “발음 정답이나 교정 행동의 정답 판정은
  아님” 문구가 함께 포함됨
- 외부 Clova endpoint는 사용하지 않아 generator 상태는 `catalog_fallback`이며,
  제한 영상 사본과 PCM cleanup은 `completed`

실제 `data/testvideo/testvideo2.mp4`로도 확인됨:

- 전체 시도 상태: `audio_completed_visual_supplemented`
- Seungun: `coaching_ready`, 선택된 목표 음소 `ㅆ`
- 삭제 정렬이라 관측 CTC 구간은 없지만 인접 음소 기반 예상 구간
  1405–1425 ms를 복원; 삭제 상태와 `observedCandidate=null`은 유지
- 원문 위치 anchor: `‘씻어요’의 첫 번째 음절 ‘씻’`
- 시각 구간 1465–1485 ms, 입술 벌림 비율 `0.008025...`로
  `increase_visible_lip_opening` claim 승인
- Clova 결합 결과: audio+visual `validated_grounded_plan`,
  `fallback_reason=null`
- 최종 문장: `‘씻어요’의 첫 번째 음절 ‘씻’에 해당하는 ‘ㅆ’ 소리를 연습해
  보세요. 천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 그리고
  입술을 조금 더 벌려 다시 발음해 보세요.`
- 제한 영상 사본/PCM cleanup: `completed`

주의 및 아직 충족되지 않음:

- 모든 음소 coverage는 “모든 음소의 입술 벌림을 측정할 수 있다”는 제품
  정책이다. `ㅅ/ㅆ`을 입술만으로 식별하거나 그 내부 조음 원인을 판정한다는
  과학적 claim은 아니다.
- 현재 reference center/scale과 QC/A·V 임계값은 두 testvideo에 맞춘 소규모
  same-cohort 기준이다. 다양한 기기·조명·화자에 일반화됐다는 근거는 없다.
- closed-beta의 6개 형상값과 변화율은 아직 음소별 pronunciation/action truth 및
  사람 평가에 연결되지 않았다. 따라서 측정 설명은 가능하지만 해당 값만으로
  “정확하다/틀렸다” 또는 새로운 교정 행동을 제시할 수 없다.
- CLI는 내부 실행 경계다. 외부 프로덕션 API의 인증, 작업 큐, 동시성 제한,
  timeout/cancellation, 지표·감사 로그, 배포 rollback은 별도 서비스 계층에서
  구현하고 검증해야 한다.

## 모든 음소 claim의 경계

40개 음소 모두 같은 `lip_aperture_ratio` 측정과 두 방향 문구만 사용한다.
허용 문구는 “표시된 음소 구간에서 입술을 조금 더 벌리기/벌림을 조금 줄이기”다.
음소별 혀 위치, 기류, 성문, 정확성, 진단 claim은 runtime allowlist에서 금지된다.
따라서 `ㅅ/ㅆ`도 시간 구간의 외부 입술 움직임 피드백은 받을 수 있지만,
입술 영상만으로 `ㅅ`과 `ㅆ`을 구분했다고 해석하면 안 된다.

## 프로덕션 승격 조건

다음 조건을 모두 만족하기 전에는 `audio_completed_visual_supplemented`를
프로덕션 준비 상태로 간주하지 않는다.

1. P6 보정 표본에서 고정 모델·보정 아티팩트·QC/A·V 정책을 확인하고 최소
   P6 요약을 발급한다.
2. 지정한 평가 실행에서 claim 위반 0건을 확인하고 최소 P8 요약을 발급한다.
   P6과 다른 표본, locked partition 또는 특정 데이터셋은 요구하지 않는다.
3. 두 요약을 runtime bundle에 포함한다.
4. 세 산출물을 enabled production visual release로 패키징하고 프로세스 시작 시 strict reload를
   통과한다.
5. 실제 `testvideo1.mp4`에서 Seungun 오디오 피드백, 같은 음소 anchor의
   시각 보충, `validated_grounded_plan` Clova 결과, cleanup 완료를 함께
   확인한다. 현재 이 검증은 testvideo1과 testvideo2 모두 통과했다.
6. 장애·timeout·동시 요청·재시작·rollback 시험에서 오디오는 안전하게
   폴백하고, 시각 claim은 신뢰 자료가 조금이라도 불일치하면 fail-closed함을
   확인한다.

위 승격 조건은 향후 검증된 입술 교정 claim을 배포하기 위한 조건이다. 두
testvideo의 audio-only 제품 경로를 실행하거나 검증하기 위해 AI Hub 데이터셋
규모의 P6/P8 작업을 요청 루프에서 수행해서는 안 된다.
