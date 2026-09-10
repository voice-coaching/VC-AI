# RunPod → Backblaze B2 스토리지 비용 절감 계획

조사일: 2026-09-09. 아래는 최초 조사 시점의 계획이다. 후속 실행에서 지정된 불완전 archive를 삭제하고 한 차례 이전을 중단했다. 이후 사용자가 A6000 48GB + Clova 8bit를 선택하여 120GB Pod volume으로 이전을 재개했다. 현재 상태는 [실행 기록](runpod_b2_storage_migration_execution.md), 하드웨어 선정 근거는 [GPU 검토](runpod_gpu_capacity_assessment.md)를 따른다. 실행 시 양자화 방식이므로 원본 BF16 파일을 유지하며 별도 양자화 checkpoint 저장 공간은 할당하지 않는다.

## 판단

현재 RunPod에는 대규모 연구 데이터셋이나 여러 연구 모델이 남아 있지 않다. 대용량 항목은 배포용 HyperCLOVA 모델과 Python 실행 환경, 패키지 설치 캐시다. 따라서 **배포 파일은 유지하고 설치 캐시를 제외한 뒤, 할당 볼륨을 300GB에서 120GB 수준으로 재구성하는 것**이 주요 절감 방법이다.

기존 B2 백업을 재업로드할 필요는 없다. B2의 파일이 RunPod에도 있을 것이라는 가정은 실제 조사에서 배제했다. 특히 B2의 공개 데이터셋 약 25GB와 연구 모델 캐시 약 43GB는 현재 RunPod에서 발견되지 않았다.

## 실측 범위와 주의점

- 대상 Pod: `intelligentai-a6000` (`urgf7ov260m6to`), RTX A6000 1개, RAM 50GB.
- API 설정: Pod volume 300GB, container disk 40GB, `networkVolumeId=null`.
- 조사 루트: `/workspace/intelligentai`. `current`는 아래 release를 가리키는 symlink다.
- 현재 release: `releases/15534800bcc3f730d6fffeb9aa98d3ee91d3ab18563b0dc5f0fad4dff6eb2887`.
- `du -x -B1` 기준 프로젝트 사용량: **101,441,689,088 bytes ≈ 101.442GB / 94.475GiB**. 디렉터리 메타데이터를 포함한다.
- `/workspace`의 `df`는 공유 파일시스템 전체 용량을 표시한다. 이 값을 사용자 할당량이나 사용자 사용량으로 계산하지 않는다.
- 표의 GB는 십진 GB다. B2 객체 크기와 파일시스템 할당량은 서로 다른 측정이므로 완전 일치한다고 간주하지 않는다.
- SSH 파일·디렉터리 메타데이터, 배포 스크립트, 프로세스 이름, B2 목록과 일부 소형 파일 hash를 확인했다. 모델 실행이나 브라우저·회귀 QA는 수행하지 않았다.
- 별도 파일 순회에서는 오류가 없었다. 일반 파일의 논리 크기 합계는 `envs` 약 10.894GB, `uv-cache` 약 10.887GB였다. 공유 파일시스템의 큰 디렉터리 메타데이터 때문에 `du` 값보다 작다. B2 archive 비용을 디렉터리 할당량 17GB와 동일시하지 않는다.
- 실행 환경의 symlink는 Python 실행 파일을 가리켰고 `envs`에서 `uv-cache`로 향하는 symlink는 발견되지 않았다. `.pth` 및 hardlink 최종 검사는 실행 단계에 남긴다.

## 파일·폴더 분류

아래 RunPod 경로는 `/workspace/intelligentai/` 기준이다. 상위·하위 항목은 중복 합산하지 않는다.

| 경로 | 실측 할당량 | 분류 | 계획과 근거 |
|---|---:|---|---|
| `releases/<현재 release>/` | 66.848GB | RunPod 유지 | 현재 코드·설정·모델이다. `current` symlink 대상 전체를 보존한다. |
| `current/data/models/hyperclovax-seed-think-32b/a6cdfd3464d1b767259cad23e164eaf39d3e3960/` | 66.692GB | RunPod 유지 + 기존 B2 백업 | release 사용량에 포함된다. 배포용 가중치이므로 B2에 있다는 이유로 로컬에서 제거하지 않는다. |
| `envs/` | 17.045GB | RunPod 유지 | `api`, `g2pk`, `scorer`, `hyperclova` 환경. `prepare_container_runtime.py`가 이 경로에서 `/opt/intelligentai/envs`로 복사한다. 단순 캐시가 아니다. |
| `uv-cache/` | 17.025GB | 재생성 가능 캐시 | 가장 큰 제외 후보. `bootstrap.py`에 `UV_CACHE_DIR` 및 `UV_LINK_MODE=copy`가 명시되어 있다. 실행 환경과의 symlink·hardlink 관계를 확인한 뒤 새 볼륨 복사 대상에서 제외한다. 기본안은 B2 업로드도 생략한다. |
| `incoming/workingtree-…tar.gz` 및 대응 `.json` | 파일 바이트 합계 0.003751GB | B2 보관 / RunPod 중복 제외 | B2 기존 객체와 두 파일 각각 SHA-256 일치를 확인했다. 현재 release를 보존한 상태에서 staging 복사본을 새 볼륨에서 제외할 수 있다. |
| `incoming/runtime-assets.tar` | 파일 크기 0.505789GB | 불완전 archive, 제거 보류 | B2 원본은 1.271255GB. tar header 순회가 16개 항목 뒤 `ReadError`로 실패했다. B2 완본으로 필수 runtime을 복구하고 검증하기 전에는 이 파일을 정상 백업으로 취급하거나 기존 B2 객체를 덮어쓰지 않는다. |
| `incoming/` 전체 | 0.512GB | 위 두 정책 적용 | 복구·대조가 끝난 후 새 볼륨에서 staging 전체를 제외하는 것이 목표다. |
| `hf-download-cache/` | 0.007GB | 재생성 가능 캐시 | 현재 Xet download cache와 로그 수준이며 연구 모델 43GB가 아니다. 모델 파일 독립성을 확인하고 새 볼륨에서 제외한다. 비용 효과는 작다. |
| `deployment/` | 0.001GB | RunPod 유지 | bootstrap·환경 재구성 스크립트와 실행 기록. 용량이 작고 복구에 필요하므로 전체 보존한다. 기존 B2 배포 archive는 별도 백업으로 유지한다. |
| 현재 release의 `build/`, `tests/`, `docs/` | release에 포함 | 이번 정리에서 유지 | 절감 효과가 작다. release identity를 바꾸거나 배포 범위를 넓히지 않는다. |

`uv-cache`는 반복 offline 재설치가 실제로 필요할 때만 별도 B2 archive 후보로 바꾼다. 일반 파일 약 10.887GB에 archive overhead를 더한 크기이므로 압축 전 보관비는 대략 월 $0.08 수준이다. 매 Pod 시작 시 다시 받는 방식은 피한다. 현재 실행 환경 보존으로 충분하면 캐시 archive는 만들지 않는다.

## 반드시 보존하거나 복구할 배포 자산

저장소의 `deploy/backend-analysis-worker/compose.yaml`, `deploy/hyperclova/compose.yaml` 및 실제 Pod의 `deployment/bootstrap.py`, `deployment/prepare_container_runtime.py`를 기준으로 한다.

1. 현재 release 코드·설정과 `current` 연결, 4개 Python 환경.
2. 고정 revision의 HyperCLOVA 가중치·tokenizer·모델 코드.
3. Seungun의 `final_pronunciation_detector_v1`, `xlsr300m_full_internal_v1/best_model`, `seungun/processor`.
4. MediaPipe `face_landmarker.task`, production visual release, P6 geometry artifact, 고정 NLTK data.
5. 실제 운영 환경에서 사용하는 deployment attestation, secret mount, CA 및 startup 설정. 비밀값은 일반 archive에 넣지 않고 기존 secret 공급 방식으로 재주입한다.

조사 시점에는 `/opt/intelligentai`가 없었고, 현재 release의 위 Seungun·MediaPipe·visual·NLTK 표준 경로도 없었다. 프로세스 목록에는 모델/분석 워커가 없었다. 과거 `container-runtime-ready.json`은 2026-09-08 기록이므로 현재 준비 완료 근거로 사용하지 않는다.

따라서 작은 볼륨으로 전환할 때 단순히 현재 디렉터리를 복사하는 것만으로 배포 복구 완료를 선언하면 안 된다. B2의 `intelligentai/models/runtime/20260908/runtime-assets.tar`와 manifest로 필수 자산을 복구하고, 배포 로더가 요구하는 소유권·권한·digest 및 실제 mount 경로를 맞춰야 한다. 현재 요청은 계획이므로 이 복구는 수행하지 않았다.

## 현재 B2와 중복 처리

버킷 `speakai2026`은 비공개이고 endpoint는 `s3.us-east-005.backblazeb2.com`이다. 전체 최신 파일 목록 105개를 pagination 종료까지 조회했다.

| B2 prefix (`intelligentai/` 아래) | 객체 크기 합계 | 역할 |
|---|---:|---|
| `datasets/open/` | 25.055GB | FLEURS·Zeroth 보관. 현재 Pod에는 없음. |
| `models/huggingface-cache/` | 42.864GB | Qwen2-Audio, Phi-4, CosyVoice2, WavLM 등 연구 모델 보관. 현재 Pod에는 없음. |
| `models/hyperclovax-seed-think-32b/` | 66.642GB | 배포 모델 백업. 파일 전체 checksum 대조는 이번에 하지 않음. |
| `models/runtime/` | 1.271GB | Seungun·visual·NLTK 복원 archive 및 manifest. 현재 staging archive의 정상 원본 후보. |
| `recovery/full-20260908/` | 211.465GB | 암호화된 recovery snapshot. 해독·복원 검증은 하지 않았으므로 내용 중복을 추정해 삭제하지 않음. |
| `recovery/20260908/` | 0.007500GB | 소스와 working tree 백업. 현재 Pod working tree archive·manifest 두 파일은 checksum 일치 확인. |
| `recovery/deployment-20260909/` | 0.000004GB | 배포 스크립트 backup. |
| `recovery/speed-probe-20260908/` | 0.805GB | 과거 전송 속도 측정용으로 보이는 객체. 별도 정리 후보이나 이번 RunPod 이전 범위에서 삭제하지 않음. |

최신 객체 합계는 약 348.111GB다. 이는 RunPod 현재 사용량 101.442GB와 서로 다른 집합이다. B2 전체를 RunPod로 복원하지 않는다. 새 데이터와 구별되는 versioned prefix를 사용하고 기존 recovery 객체는 덮어쓰지 않는다.

working tree 대조 근거:

- `.tar.gz`: `6ddba061c0ada90428cac627bc129117d890dbbd43d62a1860ef43a6f0af6003`
- `.json`: `a059ea337c3fe94c63abe6ac65c3f8bad81022066544064e53c17d2958d09106`

## 비용과 권장 용량

2026-09-09 공식 공시 기준, USD/월. GPU 실행비·세금·일시적인 중복 저장·전송비는 아래 비교에서 제외한다.

| 저장 구성 | 실행 중 저장비 | 중지/컴퓨트 제거 후 저장비 |
|---|---:|---:|
| 현재 Pod volume 300GB | $30.00 | $60.00 |
| 새 Pod volume 120GB | $12.00 | $24.00 |
| Standard network volume 120GB | $8.40 | $8.40 |

RunPod container disk 40GB의 실행 중 저장비는 공시 단가로 월 환산 $4이며, 위 표와 별도다. 동일 크기를 유지하면 절감 차액에는 영향을 주지 않는다. Network volume을 남기고 컴퓨트를 종료하는 운영에서는 Pod 종료/재생성 시나리오를 별도로 고려해야 한다.

권장 후보는 **Standard network volume 120GB**다. 현재 약 101.442GB에서 캐시·staging 약 17.544GB를 제외하면 약 83.897GB이며, runtime 자산 복원 약 1.27GB와 운영 여유를 더해도 120GB를 검토할 수 있다. 최종 용량은 복구 후 실제 사용량과 빌드·임시 공간 요구를 기준으로 확정한다. High-Performance tier는 이 가격이 아니며 선택하지 않는다. 동일 datacenter의 Standard 가용성과 실제 새 Pod 생성 조건은 실행 직전에 확인한다.

- 300GB Pod volume → 120GB Pod volume: 월 $18~$36 절감.
- 300GB Pod volume → 120GB Standard network volume: 월 $21.60~$51.60 절감.
- 실행 비율을 `f`(0~1)라 하면 현재 volume 저장비는 `60 - 30f`, Standard 120GB 대비 절감액은 `51.6 - 30f` 달러/월이다.
- B2는 $6.95/TB/30일, 즉 $0.00695/GB 수준이다. 현재 백업은 이미 존재하므로 재사용하면 신규 B2 보관비가 발생하지 않는다. 중복 archive를 새로 만들면 그만큼 추가 청구된다.
- B2 다운로드는 월평균 저장량의 3배까지 무료이고 초과분은 $0.01/GB다. RunPod 방향이 무제한 무료 제휴 대상이라고 가정하지 않는다.
- **현재 300GB 볼륨에서 파일만 삭제하면 할당 용량은 그대로이므로 저장비가 줄지 않는다.** Pod volume은 축소할 수 없어 작은 새 볼륨으로 전환하고 기존 할당을 최종 해제해야 한다.

## 실행 순서

1. **보존 목록 고정:** 위 배포 자산과 실제 startup 의존성을 manifest로 만든다. 상대 경로·파일 크기·SHA-256·소유권·권한·symlink 대상과 release identity를 포함한다. `current`를 따라가서 release를 이중 복사하지 않는다.
2. **불완전 runtime 복구 준비:** B2 완본의 manifest와 hash를 대조하고, 잘린 `incoming/runtime-assets.tar`와 구분한다. 모델·데이터는 필요한 배포 자산만 선택해 복원한다.
3. **캐시 제외 확인:** 설치/다운로드가 진행 중이지 않은지 확인한다. `envs`가 `uv-cache`를 symlink 또는 `.pth`로 참조하지 않는지 확인하고, hardlink는 실제 회수 바이트를 중복 계산하지 않는다. 필수 artifact 디렉터리는 캐시 이름 규칙으로 일괄 제외하지 않는다.
4. **작은 대상 볼륨 준비:** 실행 승인을 받은 뒤 120GB Standard network volume 또는 120GB Pod volume을 준비한다. 기존 Pod와 볼륨은 보존한다. 필요한 디렉터리만 allowlist로 복사하고, 캐시와 검증된 staging 중복은 제외한다.
5. **필요한 B2 보관만 수행:** 이미 checksum 일치한 working tree는 재업로드하지 않는다. 새 고유 archive가 필요한 경우에만 별도 prefix에 올리고 파일 수·크기·checksum 및 복원 가능성을 확인한다. B2 master key를 모델 시작 환경이나 일반 archive에 넣지 않는다. 후속 사용자 요청으로 Git 제외 `.env`와 RunPod 수집 전용 비밀파일을 작성했으며, 이 보관 범위와 외부 원본 수집은 [B2 수집·활용 계획](backblaze_external_data_plan_20260909.md)을 따른다.
6. **복구·배포 확인:** 새 환경에 Python/Java/ffmpeg 등 시스템 의존성과 `/opt/intelligentai`를 재구성한다. MediaPipe·Seungun·NLTK·visual release, 권한과 attestation을 확인한다. 새 GPU나 ARM/x86 환경을 임의 변경하지 않는다. 모델 preload·결과·재시작 후 유지 등 서비스 QA는 개발자가 수행한다.
7. **최종 전환:** 개발자의 복구 확인 후 트래픽/worker 전환을 완료한다. 기존 데이터 삭제·기존 Pod 종료로 볼륨을 없애는 작업은 별도 최종 승인 뒤 수행한다. 원본을 없애기 전까지 이전은 완료가 아니다.
8. **청구 확인:** API에서 기존 300GB 할당이 해제되었는지 확인하고 새 볼륨 크기·tier·B2 증가량을 기록한다. 절감 예상치와 다음 청구 내역을 비교한다.

이번 조사에서는 user가 실행한 Pod 상태를 유지한다. 승인 없이 Pod 생성·종료, 파일 업로드·삭제, B2 lifecycle 변경을 실행하지 않는다.

## 근거

- [RunPod 스토리지 종류·요금·볼륨 축소 제한](https://docs.runpod.io/pods/storage/types)
- [RunPod Standard/High-Performance 공시 요금](https://www.runpod.io/pricing)
- [Backblaze B2 공시 요금·egress](https://www.backblaze.com/cloud-storage/pricing)
- [프로젝트 worker 계약](api/vc-be-redis-stream-worker.md)
- [영상·HyperCLOVA 실행 경계](video_pronunciation_feedback_production.md)

운영 실제 연결 상태나 모델 품질을 검증한 문서가 아니다. 위 분류는 조사 시점 파일 목록 및 코드 의존성에 근거한 저장소 이전 계획이다.
