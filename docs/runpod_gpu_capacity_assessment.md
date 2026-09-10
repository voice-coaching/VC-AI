# Clova·Seungun·입술 피드백 GPU 용량 및 비용 검토

조사일: 2026-09-09. 사용자 조건: 현재 모델 유지와 양자화 대안 모두 비교. 서비스 규모 미정. 신규 인프라 생성과 추론 벤치마크는 수행하지 않았다.

## 판단

**A6000 48GB 한 장은 현재 HyperCLOVA 32B BF16 전체를 GPU에 상주시킬 수 없다.** CPU offload를 통한 실행 가능성과 실용적인 응답 속도는 구분해야 한다. 프로젝트에서 메모리를 크게 요구하는 부분은 Clova이며, Seungun과 MediaPipe만으로 80GB GPU가 필요한 근거는 없다.

- 비용 우선: 기존 A6000에서 Clova 8bit를 첫 검토 후보로 삼는다. 4bit는 더 큰 메모리 여유가 필요할 때의 다음 후보다. 현재 활성화된 기능이 아니므로 구현·호환성 확인과 개발자의 품질 검증이 필요하다.
- 현재 BF16 유지: 단일 A100 **80GB**를 우선 후보로 삼는다. 40GB A100은 이 조건을 만족하지 않는다. 짧은 입력·직렬 처리부터 용량을 잡는다.
- 더 큰 메모리 여유: RTX PRO 6000 Blackwell Server **96GB**. A100 대비 시간당 약 31% 비싸며 실제 처리량 우위는 아직 확인하지 않았다.
- 처리량·지연 최우선: H100 PCIe 80GB. 현 단계에서는 높은 비용을 정당화할 서비스 규모나 실측 근거가 없다.

## 실제 파이프라인

Seungun은 XLS-R-300M 기반 발음 판정 모델을 사용한다. 영상 피드백은 MediaPipe landmark와 기하 분석으로 관찰 근거를 만들고, Clova에는 원본 영상 대신 승인된 claim이 전달된다. Clova의 영상 인코더가 입술 landmark 추출을 담당하는 구조가 아니다. [프로젝트 파이프라인](video_pronunciation_feedback_production.md)

`p6_mediapipe_geometry_worker.py`의 `BaseOptions(model_asset_buffer=...)`에는 GPU delegate 지정이 없고 FaceLandmarker가 프레임을 순회한다. 현재 영상 경로는 CPU 작업으로 산정한다. ffmpeg, OpenCV, g2pK 및 별도 프로세스의 RAM·CPU 여유도 필요하다. [구현](../src/voice_coach/visual_feedback/adapters/p6_mediapipe_geometry_worker.py), [MediaPipe 문서](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python)

현재 Redis production worker의 Seungun adapter는 `generator=None`으로 호출한다. Clova를 포함한 video CLI 경로와 구분해야 한다. 이번 비교는 사용자가 요청한 **Clova까지 함께 사용하는 구성** 기준이다. 현재 worker만 실행한 결과로 Clova 동시 운영을 검증할 수 없다. [worker 구현](../src/voice_coach/backend_analysis/adapters/seungun.py)

기존 Pod는 RUNNING이지만 이번 조사 시 모델 서비스는 실행 중이지 않았으며 GPU 사용량은 1MiB였다. Seungun·MediaPipe 등 일부 runtime 자산 복구와 `/opt/intelligentai` 구성이 남아 있다. B2 자산을 복원했던 신규 Pod는 사용자 요청으로 삭제했다. GPU 선택만으로 배포가 끝나지 않는다.

## 메모리 근거

기존 Pod의 revision `a6cdfd3464d1b767259cad23e164eaf39d3e3960`에서 safetensors **헤더만 읽어** 텐서 크기와 dtype을 합산했다. 모델을 로드하거나 추론하지 않았다.

| 항목 | 확인값 |
|---|---:|
| 전체 BF16 텐서 | 66,626,820,608 bytes = 약 62.05GiB |
| 언어 모델 텐서 | 65,196,697,600 bytes = 약 60.72GiB |
| vision 및 기타 텐서 | 1,430,123,008 bytes = 약 1.33GiB |
| 현재 A6000 VRAM 표시 | 49,140MiB = 약 47.99GiB |
| Pod RAM 제한 `memory.max` | 49,999,998,976 bytes = 약 46.57GiB |

전체 텐서 합은 `model.safetensors.index.json`의 `metadata.total_size`와 같으며 전부 BF16이다. 파일 용량을 메모리 사용량으로 단순 치환한 것이 아니라 텐서 payload를 합산했다. 실제 실행에는 추가 메모리가 든다. 언어 모델만 적재하도록 바꾸더라도 48GB에는 들어가지 않는다. [공식 모델](https://huggingface.co/naver-hyperclovax/HyperCLOVAX-SEED-Think-32B)

런타임은 BF16, `device_map="auto"`, SDPA를 사용한다. GPU 예산이 40GiB이면 단순 차이로도 약 22GiB 이상의 가중치를 GPU 밖에 두어야 하며 layer 단위 배치와 작업 공간 때문에 실제 배치는 달라진다. Accelerate는 CPU 가중치를 forward 때 GPU로 옮기므로 전송 지연이 추가된다. 초당 토큰 수나 timeout 내 완료는 측정하지 않았다. [런타임 코드](../src/voice_coach/pronunciation_feedback/adapters/hyperclova_transformers_runtime.py), [Accelerate offload](https://huggingface.co/docs/accelerate/main/en/concept_guides/big_model_inference)

문서의 GPU 40GiB / CPU 48GiB는 검토용 **가중치 배치 예산**이지 실제 RAM 예약이나 성공한 운영 설정이 아니다. CPU 48GiB는 현재 컨테이너의 총 46.57GiB 제한보다 크다. `/proc/meminfo`의 약 500GiB는 호스트 전체 값이므로 Pod 예산으로 사용하면 안 된다. 기본 GPU 110GiB / CPU 8GiB도 A6000에 맞지 않는다. 실제 cgroup 한도 안에서 실행 여유를 남겨야 한다. 이 프로젝트가 호스트 RAM 500GB를 필요로 한다는 근거는 없다.

실제 text config는 72 layers, 8 KV heads, head dimension 128이다. 일반 BF16 KV cache의 batch 1 이론값은 `2(K,V) × 72 × 8 × 128 × 2(bytes) = 294,912 bytes/token`이다.

| 입력+출력 합산 토큰 | KV cache 이론값 |
|---|---:|
| 4,096 | 1.125GiB |
| 8,192 | 2.25GiB |
| 16,384 | 4.5GiB |
| 32,768 | 9GiB |

prefill 작업 공간, CUDA context, allocator, Seungun 가중치와 음성 길이에 따른 activation은 별도다. 가중치 양자화가 BF16 KV cache까지 자동으로 줄이지는 않는다. 현재 입력 제한은 64Ki **문자**, 출력은 최대 256 **토큰**이므로 GPU 용량 확정 전에 입력 토큰 예산도 정해야 한다. A100 80GB도 무제한 입력이나 동시 처리를 보장하지 않는다.

Seungun runtime bundle의 모델 파일은 약 1.26GB이며 실행 중 최대 VRAM은 측정하지 않았다. 이를 최대 VRAM과 동일시하지 않는다. Clova는 프로세스 내부 lock으로 generate를 직렬화하므로 GPU 교체만으로 동시 배치 처리 구조가 되지 않는다. process worker를 늘리면 모델 복제가 발생할 수 있다.

## GPU별 비용과 용도

2026-09-09 RunPod GraphQL `gpuTypes.lowestPrice(input:{gpuCount:1,secureCloud:true})`의 `uninterruptablePrice` 조회값. 전역 최저 견적으로 특정 지역의 실제 생성 조건과 다를 수 있다. 월 금액은 GPU 720시간 실행의 단순 계산이며 스토리지·전송·세금은 제외한다. [RunPod 요금 안내](https://www.runpod.io/pricing)

| GPU | VRAM | 시간당 | 720시간 | 판단 |
|---|---:|---:|---:|---|
| A40 | 48GB | $0.49 | $352.80 | 양자화 비용 후보. A6000 대비 7.5% 저렴, 속도 우위는 미확인 |
| RTX A6000 | 48GB | $0.53 | $381.60 | 기존 자원 활용. 8bit/4bit 후보, BF16은 offload 필요 |
| RTX 4090 | 24GB | $0.74 | $532.80 | 4bit도 전체 파이프라인 여유가 작을 수 있고 A6000보다 비쌈 |
| RTX 5090 | 32GB | $0.99 | $712.80 | 4bit 성능 후보. 8bit는 부가 메모리까지 넣기 어려움 |
| L40S | 48GB | $1.09 | $784.80 | BF16 용량 문제 유지. 양자화 실측 속도가 추가 비용을 보상할 때 검토 |
| A100 PCIe / SXM 80GB | 80GB | $1.59 | $1,144.80 | BF16 단일 GPU 우선 후보 |
| RTX PRO 6000 Blackwell Server | 96GB | $2.09 | $1,504.80 | BF16·긴 입력·추가 작업의 메모리 여유 후보 |
| H100 PCIe | 80GB | $2.89 | $2,080.80 | 지연·처리량 요구가 커졌을 때 검토 |

조회 당시 A6000/A100 PCIe/H100 PCIe는 Low, A100 SXM은 Medium, A40/L40S/PRO 6000 Server는 High 재고였다. 재고는 변동한다. PRO 6000 **MIG 24GB/48GB**는 위 96GB 전체 GPU와 다르다.

A100의 메모리 대역폭은 PCIe 1,935GB/s, SXM 2,039GB/s이다. 같은 가격과 적절한 CPU·스토리지 조건이면 SXM도 우선 비교할 수 있다. PRO 6000 Server는 96GB와 1,597GB/s를 제공한다. 사양만으로 현재 custom model의 응답 속도 순위를 확정하지 않는다. [NVIDIA A100](https://images.nvidia.com/data-center/a100/a100-datasheet.pdf), [NVIDIA PRO 6000](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/)

**A6000 두 장**도 BF16 비용 대안이다. 단일 GPU 견적의 두 배를 가정하면 $1.06/h이지만 2장 구성의 확정 견적은 아니다. VRAM이 자동으로 하나의 96GB 공간이 되지 않으며 모델 분할, 장치별 예산, 실제 연결 및 전송 비용을 확인해야 한다. 현재 명시적 `max_memory`는 GPU 0과 CPU만 지정하므로 다중 GPU 배치를 추가 검토해야 한다. 단일 A100보다 구현·운영 조건이 많아 기본안으로 선정하지 않았다.

## 양자화 대안

전체 BF16 텐서에 균일하게 적용한다고 가정한 가중치 저장량의 산술 기준은 8bit 약 31.03GiB, 4bit 약 15.51GiB다. 일부 모듈의 BF16 유지와 scale·metadata로 실제 사용량은 더 커진다. 전체 추론 VRAM 측정값이나 적재 보장이 아니다. [Transformers 양자화](https://huggingface.co/docs/transformers/v4.52.3/quantization/bitsandbytes)

| 대안 | 선택 이유 | 필요한 확인 |
|---|---|---|
| A6000 + 8bit | 기존 GPU 유지, 큰 폭의 가중치 메모리 감소 | custom HCX와 고정 Transformers/PyTorch 호환, 최대 메모리, 지연, 문구·claim 출력 |
| A6000 + 4bit | KV cache와 Seungun 동시 실행 여유 확대 | 위 항목 및 표현 품질 변화와 검증 실패·fallback 빈도 |
| A40 + 양자화 | 시간당 $0.04 절감 | 월 720시간 $28.80 절감이 이전 비용과 속도 차이를 보상하는지 |
| 5090/L40S + 양자화 | 더 빠른 응답이 필요할 때 후보 | A6000 대비 각각 약 1.87배/2.06배 요금에 맞는 실측 성능 개선 |

현재 requirements에는 bitsandbytes가 없고 `from_pretrained`에 양자화 설정도 없다. 이번에는 모델이나 코드를 양자화로 변경하지 않았다. 우선 Clova만 양자화 대상으로 삼고 발음 판정 기준인 Seungun은 고정 모델을 유지하는 안을 제안한다. 양자화 방식/버전도 배포 기록에 남겨야 한다. fallback으로 오류를 처리한다고 품질 동등성이 입증되는 것은 아니다.

## 규모별 선택과 스토리지 반영

1. 규모 미정·비용 우선: 기존 A6000 + Clova 8bit를 개발자 검증 후보로 정한다. 짧은 입력, 단일 warm scorer, Clova 직렬 처리부터 시작한다. 결과가 나오기 전 새 자원을 만들 필요가 없다.
2. 양자화 품질·호환성이 불충분하고 BF16이 필수: A100 80GB, host RAM 64GB 이상을 초기 후보로 검토한다. 측정된 최소 사양이 아니므로 실제 cgroup 한도와 cold load 피크를 확인한다. 최초 입력 예산은 예를 들어 4K 토큰으로 잡고 확대한다.
3. 긴 입력·동시성 증가: 96GB PRO 6000 또는 별도 LLM 서버/분석 worker 구성을 비교한다. 모델 복제·큐 대기·CPU 영상 처리 병목도 포함한다.
4. 낮은 지연 필수: H100과 A100의 성공 응답당 비용을 비교한다. 높은 사용률의 단순 비용 기준은 `시간당 요금 × 성공 요청당 처리시간 / 3600`이다. H100 PCIe는 A100보다 약 1.82배 비싸므로 유사 조건에서 1.82배 이상의 처리량이 필요하다. 운영에서는 idle·재시도·cold start·큐 대기도 포함한다.

A6000→A100은 720시간 기준 월 GPU 비용을 $763.20 늘린다. 이는 300GB→120GB Pod volume의 월 $18~$36 저장비 절감보다 훨씬 크다. 비용 절감 목적에서는 GPU·양자화 결정을 먼저 해야 한다.

기존 B2 `speakai2026`를 계속 사용할 수 있다. GPU·지역 결정 후 RunPod에는 필수 모델·환경·runtime 자산을 유지하고 원본 백업·보관 자산은 기존 B2에 둔다. **실행 시 양자화만 적용하면 원본 BF16 파일은 여전히 로컬에 필요하므로 디스크가 자동으로 줄지 않는다.** 검증된 양자화 checkpoint를 저장·배포하는 경우에만 로컬 디스크 절감량을 다시 산정한다. 120GB는 확정값이 아니며 원본+변환본 또는 변환 중 임시 공간까지 포함하면 부족할 수 있다.

## 확인 범위

실제 Pod/API/B2 목록, VRAM 표시, cgroup 제한, 가중치 헤더 및 코드·문서를 확인했다. 신규 Pod 삭제와 임시 이전 키 정리를 완료했다. 모델 preload, 속도, 동시 추론 peak memory, 양자화 품질 및 서비스 QA는 실행하지 않았다. GPU 추천은 정적 용량 분석과 현재 요금에 따른 후보 선정이며 실제 배포 성능의 보증이 아니다.
