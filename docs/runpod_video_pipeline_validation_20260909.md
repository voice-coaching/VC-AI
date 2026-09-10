# RunPod 내부 영상 파이프라인 실행 결과

실행일: 2026-09-09. 이 문서는 최초 실패와 MediaPipe 보완 단계의 기록이다. **후속 Clova 런타임 수정 후 기본 전체 경로는 두 영상 모두 검증을 통과했다.** 최신 결과와 원인 분석은 [Clova 수정 실행 기록](runpod_clova_runtime_fix_20260909.md)을 따른다. 아래의 90초 timeout 및 catalog fallback 결과는 수정 전 기록이다.

사용자의 기존 Pod 삭제 및 테스트 영상 실행 요청에 따라 새 Pod `odqpp9eqly0v0x`에서 실행했다. RunPod API 목록에는 새 Pod만 남아 있었다. 브라우저, 전체 회귀 시험, 대규모 평가 데이터셋 실행은 하지 않았다.

## 입력과 환경

PC의 Downloads 폴더에서 기존 두 영상을 찾아 새 Pod의 소유자 전용 임시 디렉터리로 복사했다. 전송 전후 SHA-256이 일치했다.

| 파일 | 길이 | 영상 / 오디오 | SHA-256 |
|---|---:|---|---|
| `testvideo1.mp4` | 4.833초 | HEVC 1080×1920 / AAC | `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9` |
| `testvideo2.mp4` | 2.700초 | HEVC 1080×1920 / AAC | `1dd299f28ae9a0c2fcc2422e46ef3e162c62805a2267a19e1f7a277087a81379` |

- 고정 문장: `LOCAL-TEST-RICE-CLEAN-V1`, “쌀을 깨끗이 씻어요”.
- RTX A6000 1개, 실제 VRAM 46,068MiB. RAM cgroup 제한 49,999,998,976 bytes.
- HyperCLOVA X SEED Think 32B, revision `a6cdfd3464d1b767259cad23e164eaf39d3e3960`, bitsandbytes INT8, 양자화 선형 모듈 504개.
- 입력 상한 4,096토큰, 출력 상한 256토큰, 직렬 generate, HTTP 요청 제한 90초.
- `/opt/intelligentai/app/scripts/run_pronunciation_video_attempt.py`를 기존 scorer 환경에서 실행했다. Clova endpoint는 `http://127.0.0.1:8000/v1`이다.
- 기본 production visual release는 `production_visual_v2`. CLI의 소유자 전용 계약에 맞춰 별도 디렉터리 0700 / 파일 0400 복사본을 사용했다. 원본 release, 모델 가중치, 판정 임계값, source-pinned worker는 변경하지 않았다.

## 최종 기본 경로 결과

| 항목 | testvideo1 | testvideo2 |
|---|---|---|
| 음성 상태 | `coaching_ready` | `coaching_ready` |
| 안내 위치 / 음소 | ‘깨끗이’의 두 번째 음절 ‘끗’ / `ㅅ` | ‘씻어요’의 첫 번째 음절 ‘씻’ / `ㅆ` |
| 음성 구간 | 2652–2673ms | 1405–1425ms |
| 입술 분석 | `completed` | `completed` |
| 영상 대응 구간 | 2493–2514ms | 1465–1485ms |
| 입술 벌림 비율 | 0.07307788 | 0.00834461 |
| 시각 안내 | 입술 벌림을 조금 줄이기 | 입술을 조금 더 벌리기 |
| facade 상태 | `audio_completed_visual_supplemented` | `audio_completed_visual_supplemented` |
| Clova 실제 예외 | `TimeoutError: timed out` | `TimeoutError: timed out` |
| Clova 호출 시간 | 90.14초 | 90.13초 |
| 결합 안내 생성 상태 | `catalog_fallback` | `catalog_fallback` |
| 제한된 작업 사본 삭제 | `completed` | `completed` |

CLI의 종료 코드 0과 facade의 `audio_completed_visual_supplemented`만으로 LLM 성공을 판단하면 안 된다. `combined_feedback.generator_status`가 두 경우 모두 `catalog_fallback`이며 `validated_grounded_plan`은 한 번도 확인하지 못했다. 내부 audio 결과의 `generator_not_configured`는 음성 단계에서 별도 LLM을 사용하지 않는 구성에 해당하고, 실제 결합 LLM 실패는 `combined_feedback` 및 호출 예외에서 확인했다.

기본 경로의 실제 반환 문장:

- 영상 1: “‘깨끗이’의 두 번째 음절 ‘끗’에 해당하는 목표 음소 ‘ㅅ’ 소리를 천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 표시된 음소 구간에서 입술 벌림을 조금 줄여 다시 발음해 보세요.”
- 영상 2: “‘씻어요’의 첫 번째 음절 ‘씻’에 해당하는 목표 음소 ‘ㅆ’ 소리를 천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 표시된 음소 구간에서 입술을 조금 더 벌려 다시 발음해 보세요.”

## 발견한 문제와 보완

1. 최초 두 실행은 음성만 완료되고 입술 검출이 실패했다. MediaPipe 공유 라이브러리를 직접 적재해 `libEGL.so.1`, 이어서 `libGLESv2.so.2` 부재를 확인했다. `libegl1`, `libgles2`를 설치하고 `deploy/runpod/boot.sh`에도 반영했다. 보완 후 기본 경로와 선택적 40점 경로에서 두 영상 모두 입술 피드백을 반환했다.
2. Clova 프로세스의 스레드가 처음 117개였고 Pod CPU quota는 약 7.65 CPU였다. RunPod 시작 스크립트에 `OMP_NUM_THREADS=4`, `MKL_NUM_THREADS=4`, `OPENBLAS_NUM_THREADS=1`을 적용해 모델을 재기동했다. 스레드는 9개로 줄었지만 **90초 timeout은 해결되지 않았다.** 이 변경의 지연 개선 효과를 입증한 것은 아니다.
3. 기본 CLI에 관찰용 wrapper를 씌워 LLM 호출의 예외와 경과 시간만 기록했다. 검증기, 90초 제한, 출력 토큰 수와 모델 정책은 변경하지 않았다. 두 경우 모두 실제 `TimeoutError`가 `CoachingPayloadError`로 변환되어 기본 안내가 선택되는 것을 확인했다. 제한을 늘려 통과 처리하지 않았다.

40점 추가 관찰을 켠 두 실행은 `closed_beta_lip_trajectory.status=enabled`, `attempt_status=observed`였다. 이 관찰값은 발음 정답이나 교정 행동의 정답을 뜻하지 않는다. 이 경로도 Clova는 fallback이었다.

## 자원과 시간의 해석

- 전체 기록의 1초 간격 GPU 관측 최고치는 **39,954MiB ≈ 39.02GiB / 46,068MiB**다. 순간 peak를 완전히 포착하는 계측은 아니며 동시 사용자 수용량을 입증하지 않는다.
- cgroup `oom=0`, `oom_kill=0`. RAM 사용량에 약 47.4GB 파일 캐시가 포함되어 있었고 별도 확인 시 익명 메모리는 약 1.34GB였다. cgroup 사용량이 50GB에 가깝다는 사실만으로 모델이 그만큼의 호스트 RAM을 요구한다고 판단하면 안 된다.
- 보완 후 40점 경로의 전체 경과 시간은 128.03초 / 124.44초였다. 기본 경로는 관찰용 tracing을 포함해 151.10초 / 148.91초였으므로 두 경로의 성능 비교 수치로 사용하지 않는다.
- 모델은 실행 전에 적재돼 있었으나 Seungun과 영상 worker의 요청별 시작 비용을 포함한다. 서버에는 클라이언트 timeout 후 generate를 별도로 취소하는 로직이 없고 직렬 잠금이 있어, 앞 요청의 잔여 생성과 대기 영향을 분리한 순수 생성 속도 측정도 아니다.
- 메모리 적재 가능성은 확인했지만 **현재 설정의 응답 시간 적합성은 실패했다.** BF16 대비 정확도 손실, 전체 INT8 생성 완료 시간, 완성된 LLM 응답의 품질은 이 결과로 판단할 수 없다.

## 기록, 정리, 남은 범위

새 Pod의 `/workspace/intelligentai/deployment/video-validation-20260909/`에 원본 미디어를 제외한 JSON 결과, 자원 표본, 오류 기록과 `summary.json`을 보관했다. `testvideo*`는 보완 전, `final-testvideo*`는 보완 후 40점 경로, `baseline-testvideo*`는 보완 후 기본 경로다. 로컬 증거 사본은 `%TEMP%\intelligentai-video-check-20260909`에 있다.

모든 완료 실행의 cleanup은 `completed`였고 `/tmp/voice-coach-video-attempt-*` 잔여 디렉터리는 없었다. Pod로 전송한 두 MP4 사본도 삭제했다. PC Downloads의 원본 영상은 보존했다. 종료 확인 시 Clova `/health`는 `ready`였고 새 Pod는 실행 상태를 유지했다.

시스템 의존성과 스레드 설정은 영구 overlay 및 재기동 스크립트에 반영했다. 수정 후 전체 Pod 재시작을 반복하지는 않았다. 수정된 배포 자산은 기존 B2 버킷에 백업하고 다운로드 SHA-256을 대조했다. 객체와 해시는 [이전 실행 기록](runpod_b2_storage_migration_execution.md)에 기록했다. Git commit/push는 하지 않았다.

이번 범위는 내부 CLI다. Redis/S3/authorization/attestation을 포함한 외부 서비스 연동은 미검증이다. 운영 전에는 Clova의 실제 생성 지연과 요청 대기·취소 동작을 해결하고, 같은 입력에서 제한 시간 내 `validated_grounded_plan` 반환을 다시 확인해야 한다.
