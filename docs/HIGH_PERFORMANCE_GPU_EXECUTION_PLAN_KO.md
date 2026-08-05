# 고성능 GPU 실행 계획

이 프로젝트의 데이터 준비, 모델 학습, 평가와 TTS 생성은 고성능 GPU
머신만을 기준으로 수행한다. 먼저 고정 manifest와 평가 split을 유지한 채
모델 용량과 미세조정 범위를 단계적으로 확장한다. GPU 모델과 VRAM이 아직
문서화되지 않았으므로 실측 전에는 batch size나 full fine-tuning을
확정하지 않는다.

## 1. 첫 실행에서 기록할 항목

```powershell
nvidia-smi --query-gpu=name,driver_version,memory.total,power.limit `
  --format=csv
```

추가로 CUDA runtime, PyTorch, Python, OS, CPU RAM, 저장장치 여유 공간과
1,000개 발화의 peak VRAM·처리속도·OOM 횟수를 기록한다. 이 결과는
`artifacts/hardware/<gpu-name>/profile.json`에 저장하되 Git에는 개인정보가
없는 요약 JSON만 선별해 올린다.

## 2. VRAM별 시작점

| 가용 VRAM | 권장 첫 모델 | 학습 범위 |
|---:|---|---|
| 12–16 GiB | WavLM Base+ | frozen 또는 adapter/LoRA |
| 20–24 GiB | WavLM Large | frozen 비교 후 adapter/상위 block |
| 32–48 GiB | WavLM Large | partial 또는 full fine-tuning 후보 |
| 80 GiB 이상 | WavLM Large 다중과제 | full fine-tuning과 더 긴 context 검토 |

표는 상한이 아니라 시작점이다. 실제 선택은 8·12·20초 입력 benchmark와
gradient checkpointing, mixed precision, optimizer state를 포함한 peak
VRAM으로 결정한다.

## 3. 환경 분리

- scorer: Python 3.11, PyTorch·torchaudio·Transformers 고정 버전
- TTS: 모델 요구사항에 맞는 별도 Python 3.10/3.11 환경
- 데이터 준비: 모델 환경과 분리된 표준 라이브러리 도구
- scorer와 TTS는 첫 통합 전까지 별도 프로세스로 실행

전역 Python 3.14 환경을 직접 모델 학습 환경으로 확장하지 않는다.

## 4. 실험 순서

> WP4 S103 이후 S104–S108의 순서·split·label·승격 gate는
> [WP4 S103 이후 실행 계약](constraints/WP4_POST_S103_EXECUTION_CONTRACT_KO.md)이
> 아래 G1–G3 요약보다 우선한다. 아래의 “동일 seed와 locked test” 중
> seed 고정만 유효하며, locked test는 G04 240건 수동 정렬과 후속
> 외부·사전등록 gate 전에는 열지 않는다.

### G0 데이터 gate

- WAV–JSON–TextGrid 결합률 99% 이상
- split 간 `UserID` 교집합 0
- split 간 PCM hash 중복 0
- 음소 시간 구간 오류 1% 미만
- 240건 수동 정렬 정확도 95% 이상

### G1 저비용 baseline

- 명시적 duration·pause·speech-rate·F0·energy 특징
- frozen WavLM Base+ 기준선 구축
- 동일 seed와 locked test를 사용

### G2 모델 크기 비교

- WavLM Base+ frozen
- WavLM Large frozen
- Base+ adapter
- Large adapter 또는 상위 block partial fine-tuning

WavLM Large가 Base+ 대비 phone macro F1을 0.03 이상 개선하지 못하면
full fine-tuning으로 바로 확대하지 않는다.

### G3 다중과제 진단기

- phone error classification
- utterance pronunciation·fluency ordinal regression
- pause·duration·prosody auxiliary task
- confidence calibration

학습 loss를 합치기 전에 각 head의 독립 baseline과 label coverage를 먼저
확인한다.

### G4 TTS 기준음

진단기와 독립적으로 20개 고정 연습 문장의 기준음 후보를 생성한다.
원어민 녹음과 TTS 후보를 전문가가 blind 평가하고, artifact·발음 오류가
없는 2–3개만 bank에 저장한다. TTS 모델 자체 학습은 진단기 gate를 통과한
뒤 별도 실험으로 진행한다.

### G5 폐쇄루프

`녹음 → 진단 → top-1 코칭 → 기준음 → 재녹음 → 전후 비교`를 하나의
재현 스크립트로 만든다. confidence가 낮거나 calibration 범위를 벗어나면
자동 교정 대신 재녹음 또는 전문가 검토를 요청한다.

## 5. 고성능 GPU에서도 유지할 중단 기준

- 데이터 gate 실패 시 모델 학습 금지
- OOM이 동일 설정에서 두 번 발생하면 batch/context를 고정적으로 축소
- ECE가 0.15를 넘으면 자동 코칭 비활성화
- 발행된 교정의 precision이 0.90 미만이면 template 범위를 축소
- unsupported coaching claim이 2% 이상이면 생성형 코칭 제거
- worst-group phone F1 격차가 0.10을 넘으면 전체 성능만으로 배포 판단 금지

GPU가 커져도 데이터 누수, calibration, 하위집단 평가와 근거 제한 정책은
완화하지 않는다.
