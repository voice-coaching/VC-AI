# Clova INT8 시간 초과 분석 및 수정 결과

이 문서는 짧은 피드백 정책에서의 실행 기록이다. 이후의 상세 설명 변경과
분석 보완 계획은 [상세 피드백 문서](clova_detailed_feedback_20260909.md)를 따른다.
아래 생성 시간과 통과 결과는 새 상세 설명 정책을 재실행한 결과가 아니다.

2026-09-09, Pod `odqpp9eqly0v0x`. **두 영상의 기본 경로와 40점 입술 관찰 옵션, 총 4건 모두 음성·입술·Clova 결합 검증을 통과했다.** `combined_feedback.generator_status=validated_grounded_plan`, `fallback_reason=null`, 종료 토큰 생성과 작업 사본 삭제를 확인했다.

## 원인

bitsandbytes 0.50.2의 `MatMul8bitLt.forward`는 BF16 입력을 FP16으로 변환할 때마다 같은 `logger.warning`을 호출한다. 이 모델에는 해당 선형 모듈이 504개 있다. 실제 2토큰 프로파일에서도 1,008회 호출을 확인했다. 정상적인 형 변환 경고가 토큰마다 레이어 수만큼 반복됐다.

RunPod 시작 스크립트는 stderr를 영구 공유 볼륨의 `hyperclova-int8.log`에 append한다. 따라서 경고마다 동기 파일 쓰기와 추가 위치 확인 비용이 발생했다. 수정 전 로그는 약 36.7MB까지 증가했다. 동일한 경고 1,000회를 flush하는 비교에서 컨테이너 파일 append는 0.0021초, 공유 볼륨 append는 0.3247초였다. 일반 쓰기 모드로 측정한 값은 더 작아 실제 append 경로와 구분했다. 이 작은 I/O 실험만으로 전체 지연을 정확히 환산하지는 않는다.

진단용 컨테이너 로그에서 8토큰 생성은 원래 경고를 기록할 때 3.09초, 반복 경고 억제 시 2.18초였다. 초기 적재·캐시 영향이 있는 소규모 비교라 엄밀한 처리량 벤치마크는 아니다. 이후 실제 운영 로그 위치를 유지하고 반복 경고를 억제한 동일 모델에서, 이전에 90초를 넘기던 기본 요청이 약 33초에 완료됐다. 실행 후 해당 형 변환 경고는 프로세스 전체에서 **1회**였다.

기존 서버에는 별도 문제가 두 가지 더 있었다. 클라이언트가 90초에 연결을 끊어도 생성은 계속될 수 있었고, 다음 요청은 같은 잠금에서 기다렸다. 또한 출력 토큰 상한으로 잘린 응답에도 HTTP 응답의 `finish_reason`을 항상 `stop`으로 표시했다.

GPU 메모리 부족은 이번 실패 원인으로 확인되지 않았다. `memory.events`의 `oom`, `oom_kill`은 모두 0이었다. 기존 CPU 스레드 제한만으로는 시간 초과가 해결되지 않았다는 이전 기록도 유지한다.

## 수정

- 알려진 BF16→FP16 형 변환 경고만 프로세스 최초 1회 기록한다. 다른 경고는 그대로 남긴다.
- 동시 생성 잠금을 즉시 획득하지 못하면 HTTP 503과 `Retry-After: 1`을 반환한다. 내부 대기열과 숨은 재시도는 만들지 않는다.
- 토큰 생성 단계마다 서버 측 75초 제한을 확인하고 초과 시 HTTP 504로 실패 처리한다. 클라이언트 제한은 기존 90초다. 토큰 경계에서 검사하는 제한으로, 강제 프로세스 종료 방식은 아니다.
- 종료 토큰 없이 출력 상한에 도달한 응답은 HTTP 422로 반환해 불완전한 출력을 정상 완료로 표시하지 않는다.
- `/health.last_completion`에 입력·출력 토큰 수, 생성 시간과 종료 사유를 남긴다. 요청·생성 원문은 여기에 저장하지 않는다.
- tokenizer에는 `thinking=False`를 직접 전달한다. 고정된 현재 템플릿에서 이전 전달 방식과 새 방식의 생성 prompt가 동일함을 확인했으므로, thinking 전환이 이번 속도 개선 원인은 아니다.

모델 ID와 revision `a6cdfd3464d1b767259cad23e164eaf39d3e3960`, INT8 형식, BF16 연산 설정, threshold **6.0**, CPU offload 비활성화, 입력 4,096 / 출력 256토큰 상한은 유지했다. 음성·시각 판정 임계값과 피드백 검증기도 바꾸지 않았다.

이상치 보정을 끄는 threshold 0은 진단 중 8토큰 비교에만 사용했고 즉시 복원했다. 이후 운영 서비스를 원래 6.0 설정으로 새로 시작했다. [Hugging Face 공식 문서](https://huggingface.co/docs/transformers/quantization/bitsandbytes#outlier-threshold)는 threshold 0의 속도 개선과 정확도 손실 가능성을 함께 설명한다. 이번 해결에는 이 대안을 채택하지 않았다.

## 실제 영상 재실행

PC Downloads의 기존 `testvideo1.mp4`, `testvideo2.mp4`를 사용했다. SHA-256은 [최초 실행 기록](runpod_video_pipeline_validation_20260909.md)의 값과 일치했다. 같은 CLI, 고정 문장, MediaPipe 모델, production visual release를 사용하고 Clova를 적재한 상태에서 요청을 순서대로 실행했다. 이 최종 실행에는 앞선 진단용 Python tracing wrapper를 사용하지 않았다.

| 경로 / 영상 | 입력 / 출력 토큰 | Clova 생성 | 전체 경과 | 결과 |
|---|---:|---:|---:|---|
| 기본 / 영상 1 | 1,271 / 127 | 32.828초 | 67.98초 | 검증 통과, fallback 없음 |
| 기본 / 영상 2 | 1,265 / 127 | 32.681초 | 61.64초 | 검증 통과, fallback 없음 |
| 40점 옵션 / 영상 1 | 1,407 / 161 | 40.805초 | 76.89초 | 검증 통과, fallback 없음 |
| 40점 옵션 / 영상 2 | 1,401 / 155 | 41.204초 | 75.25초 | 검증 통과, fallback 없음 |

전체 경과에는 요청별 Seungun·영상 worker 시작, 처리와 삭제가 포함된다. Clova 적재 시간은 포함하지 않는다. 이 수치를 동시 사용자 성능 또는 일반적인 서비스 지연 보장으로 확대 해석하지 않는다.

기본 경로의 실제 Clova 출력:

- 영상 1: “‘깨끗이’의 두 번째 음절 ‘끗’에 해당하는 ‘ㅅ’ 소리를 천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 그리고 입술을 조금 덜 벌리고 다시 발음해 보세요.”
- 영상 2: “‘씻어요’의 첫 번째 음절 ‘씻’에 해당하는 ‘ㅆ’ 소리를 천천히 분리해 발음한 뒤 원래 속도로 다시 녹음해 보세요. 그리고 입술을 조금 더 벌려 다시 발음해 보세요.”

40점 옵션도 두 건 모두 `enabled` / `observed`였고 Clova 메시지에 관찰값이 발음 정답이나 교정 행동의 정답 판정이 아니라는 설명이 포함됐다. 이 결과를 40점 관찰값 자체의 정확도 검증으로 간주하지 않는다.

## 추가 확인과 한계

- 실제 생성 도중 추가 HTTP 요청: **503, 0.039초**, `Retry-After: 1` 확인.
- 출력 상한 1토큰인 실제 HTTP 요청: **422, 0.476초**, `HyperCLOVA response reached the output token limit` 확인. 의도한 실패 사례이며 위 영상 결과와 구분한다.
- 4건의 생성 종료 사유는 모두 `stop`. 75초 deadline을 실제로 소진시키는 추가 요청은 하지 않았다.
- GPU 1초 표본의 최고치는 **40,638MiB ≈ 39.69GiB / 46,068MiB**였다. 순간 peak 전체를 포착하는 계측은 아니다.
- Python AST, shell syntax, overlay 9개 파일의 SHA-256 및 실행 사본 일치를 확인했다. 브라우저 및 자동 회귀 테스트는 실행하지 않았다.
- BF16 대비 양자화 정확도, 다중 사용자 부하, Redis/S3/authorization/attestation을 포함한 외부 서비스 연결은 미검증이다. 이번 성공 범위는 내부 영상 CLI다.

## 배포와 증거

수정은 로컬 코드, `/workspace/intelligentai/deployment/int8-overlay` 및 `/opt/intelligentai/app`에 반영했다. 정상 RunPod 시작 스크립트로 Clova 서비스를 재기동했으며 최종 운영 프로세스는 진단용 서버가 아니다. 전체 Pod 재생성은 반복하지 않았다. Git commit/push는 하지 않았다.

새 Pod의 `/workspace/intelligentai/deployment/clova-fix-20260909/`에 결과 JSON, 자원 표본, 프로파일과 `summary.json`을 보관했다. 로컬 사본은 `%TEMP%\intelligentai-clova-fix-20260909`다. 최종 health의 `last_completion`은 마지막에 실행한 의도적 1토큰 제한 검사이므로 `length`일 수 있다. 영상별 실제 완료 수치는 각 `resolved-*.metrics.json`에 보관했다.

모든 영상 실행의 cleanup은 `completed`이고, 파이프라인 임시 작업 디렉터리는 남지 않았다. Pod에 올린 두 영상 사본을 삭제했으며 PC 원본은 유지했다. 수정된 배포 자산은 기존 비공개 B2 버킷의 후속 복구 객체에 보관하고 다운로드 해시를 대조한다. 해당 객체와 완료된 검증 값은 [이전 실행 기록](runpod_b2_storage_migration_execution.md)을 따른다.
