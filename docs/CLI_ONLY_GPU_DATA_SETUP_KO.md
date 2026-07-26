# CLI 전용 고성능 GPU 머신 데이터 준비 가이드

이 문서는 모니터·브라우저가 없는 Linux GPU 머신에서 저장소를 clone하고,
AI Hub와 공개 원어민 코퍼스, 모델 가중치를 다시 내려받아 실험 가능한
상태로 만드는 절차다. Ubuntu 22.04/24.04 계열과 Bash를 기준으로 한다.

## 1. 먼저 확인할 사항

### 데이터 이용 조건

- AI Hub 데이터는 해당 계정에서 이용 신청이 승인된 뒤에만 다운로드한다.
- API key만으로 승인 절차를 우회할 수 없다.
- GPU 머신이 해외 리전이나 제3자 관리 인프라에 있다면 AI Hub 약관과
  연구기관의 국외 이전·위탁처리 규정을 먼저 확인한다.
- KSS와 Seoul Corpus는 비상업 연구 경로로만 사용한다.
- API key, 원본 음성, 전사와 화자 메타데이터는 GitHub에 올리지 않는다.

AI Hub 공식 안내는 Linux 등 CLI 환경에서 `aihubshell`을 지원하며, 다운로드
승인과 API key가 모두 필요하다고 설명한다. 다운로드·압축 해제를 위해
다운로드 크기의 2–3배 공간을 권고한다.

- https://www.aihub.or.kr/devsport/apishell/list.do
- https://aihub.or.kr/static/pdf/aihubshell_가이드.pdf

### 권장 디스크

| 범위 | 최소 권장 persistent NVMe |
|---|---:|
| AI Hub 71490 발음 MVP만 | 500 GiB |
| 현재 로컬 71490 전체 상태 재현 | 1 TiB |
| 영어·기타 아시아어·유럽어 발음까지 확장 | 2 TiB 이상 |
| feature cache·checkpoint·TTS 후보까지 장기 보관 | 3–4 TiB |

임시 OS 디스크가 아니라 인스턴스가 재시작되어도 유지되는 volume을 사용한다.

```bash
df -hT
lsblk -f
```

## 2. 기존 검증 데이터를 옮길지 다시 받을지 결정

이미 검증된 71490 데이터가 있으므로, 이용 조건상 허용되는 동일 연구
인프라라면 재다운로드보다 `rsync` 이전이 빠르고 안정적이다.

```bash
rsync -aH --partial --info=progress2 --protect-args \
  /source/intelligentAI/data/restricted/aihub/dataset-71490/ \
  gpu-user@gpu-host:/data/intelligentAI/data/restricted/aihub/dataset-71490/
```

437,841개 작은 파일의 왕복 지연이 큰 환경에서는 연구실 NAS 또는 접근
제어 object storage에 검증된 archive를 한 번 만들고 checksum과 함께
전송하는 편이 낫다. 국외·제3자 storage에는 이용 조건 확인 없이 올리지
않는다.

새로 다운로드해야 하거나 기존 머신에 접근할 수 없으면 아래 절차를 따른다.

## 3. 머신 초기화

```bash
sudo apt-get update
sudo apt-get install -y \
  git curl ca-certificates tmux rsync unzip p7zip-full \
  jq ffmpeg libsndfile1 build-essential
```

Python 3.12 이상과 PowerShell 7(`pwsh`)을 설치한다. 배포판에 맞는 공식
패키지를 사용하고 설치 후 다음을 확인한다.

```bash
python3 --version
pwsh --version
curl --version
tar --version
nvidia-smi
```

프로젝트는 persistent volume에 clone한다.

```bash
sudo mkdir -p /data/intelligentAI
sudo chown "$USER":"$USER" /data/intelligentAI
git clone https://github.com/voice-coaching/intelligentAI.git /data/intelligentAI
cd /data/intelligentAI

python3 -m venv .venv-data
source .venv-data/bin/activate
python --version
```

## 4. AI Hub Shell 설치

공식 endpoint에서 사용자 계정의 `~/.local/bin`으로 설치한다. system
전역 `/usr/bin` 복사는 필요하지 않다.

```bash
install -d -m 700 "$HOME/.local/bin"
curl -fsSL https://api.aihub.or.kr/api/aihubshell.do \
  -o "$HOME/.local/bin/aihubshell"
chmod 700 "$HOME/.local/bin/aihubshell"
export PATH="$HOME/.local/bin:$PATH"

bash -n "$HOME/.local/bin/aihubshell"
aihubshell -help
```

공식 페이지가 aihubshell checksum을 별도로 게시하지 않으므로 다운로드
시점, tool version과 로컬 SHA-256을 실행 로그에 남긴다.

```bash
sha256sum "$HOME/.local/bin/aihubshell"
```

## 5. API key를 CLI 세션에 안전하게 주입

명령행 history나 Git 저장소의 `.env.local`에 key를 직접 적지 않는다.
전용 `tmux` 세션 안에서 숨김 입력으로 환경변수를 만든다.

```bash
tmux new -s aihub
read -rsp 'AIHUB API key: ' AIHUB_API_KEY
echo
export AIHUB_API_KEY
set -o pipefail
```

공식 shell 인터페이스가 key를 인자로 전달하므로 같은 머신의 권한 없는
다른 사용자가 process argument를 볼 수 없도록 전용 OS 계정과 접근 제어를
사용한다. 작업 후에는 다음을 실행한다.

```bash
unset AIHUB_API_KEY
```

## 6. file key를 다시 조회

저장소의 key는 2026-07-25, aihubshell `25.09.19 v0.6` 목록을 기록한
snapshot이다. 데이터셋 revision 후 key가 달라질 수 있으므로 다운로드
직전에 이름과 key를 다시 조회한다.

```bash
cd /data/intelligentAI
bash scripts/download_aihub_cli.sh list 71490
less data/restricted/aihub/dataset-71490/dataset-71490-files.txt
```

목록에 기록된 파일명과
`data/manifests/aihub-l2-core.yaml`을 대조한다. key가 다르면 파일명을
기준으로 새 key를 사용하고 manifest도 갱신한다.

## 7. AI Hub 71490 다운로드

### 선택 A: 발음 진단 MVP에 필요한 최소 묶음

일반 대화 음성은 제외하고 발음 음성·목표 라벨·실현 TextGrid만 받는다.
공식 표시 기준 약 69.1 GB다.

```bash
bash scripts/download_aihub_cli.sh download 71490 \
  475431,475432,475434,475435,475437,475439,475440 \
  data/restricted/aihub/dataset-71490 \
  2>&1 | tee data/restricted/aihub/dataset-71490/download.log
```

### 선택 B: 현재 로컬 데이터 상태 전체 재현

일반 speech와 발음 데이터를 모두 포함한다.

```bash
bash scripts/download_aihub_cli.sh download 71490 \
  475429,475430,475431,475432,475433,475434,475435,475436,475437,475438,475439,475440 \
  data/restricted/aihub/dataset-71490 \
  2>&1 | tee data/restricted/aihub/dataset-71490/download-full.log
```

SSH가 끊겨도 `tmux attach -t aihub`로 돌아올 수 있다. 실패 시 같은 명령을
재실행하되, shell이 만든 `.part*`와 archive를 임의로 지우지 않는다.

## 8. 압축 해제와 검증

`aihubshell`은 분할 파일을 병합하고 AI Hub 디렉터리 구조를 복원한다.
내부 label/audio ZIP은 다음 순서로 해제한다.

```bash
cd /data/intelligentAI
source .venv-data/bin/activate

pwsh -NoProfile -File ./scripts/extract_aihub_labels.ps1 \
  -DatasetRoot ./data/restricted/aihub/dataset-71490

pwsh -NoProfile -File ./scripts/extract_aihub_audio.ps1 \
  -DatasetRoot ./data/restricted/aihub/dataset-71490 \
  -DeleteArchiveAfterVerify
```

음성 ZIP은 entry별 파일 크기와 총 바이트가 모두 일치한 뒤에만 완료 marker를
쓰고 `-DeleteArchiveAfterVerify`가 원본 ZIP을 제거한다. label ZIP은 보수적으로
남겨 둔다.

## 9. Manifest와 QC 생성

100건 smoke test:

```bash
python scripts/build_aihub71490_manifest.py \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --output-dir data/processed/aihub71490/smoke-manifests \
  --limit 100 \
  --with-speaker-splits
```

전체 manifest:

```bash
tmux new -s manifest
cd /data/intelligentAI
source .venv-data/bin/activate

python scripts/build_aihub71490_manifest.py \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --output-dir data/processed/aihub71490/manifests \
  --with-speaker-splits \
  --progress-every 5000 \
  2>&1 | tee data/processed/aihub71490/manifest-build.log
```

QC에서 WAV–JSON–TextGrid 결합률, 디코드 오류, 화자 교집합과 누락 파일을
확인하기 전에는 GPU 학습을 시작하지 않는다.

## 10. 공개 원어민 코퍼스

원어민 기준 발음·정렬·TTS 보정을 위해 다음 네 묶음을 받는다.

```bash
pwsh -NoProfile -File ./scripts/sync_native_korean_corpora.ps1 \
  -Action Download -Dataset all

pwsh -NoProfile -File ./scripts/sync_native_korean_corpora.ps1 \
  -Action Verify -Dataset all

pwsh -NoProfile -File ./scripts/sync_native_korean_corpora.ps1 \
  -Action Extract -DryRun -Dataset all
```

공간 확인 후 마지막 명령의 `-DryRun`만 제거한다. KSS는 Parquet 안에 audio가
들어 있으므로 모델 데이터로더 또는 별도 materialize 단계에서 처리한다.

| 데이터셋 | 압축/컨테이너 크기 | 용도 |
|---|---:|---|
| FLEURS ko-KR | 약 1.64 GiB | 외부 원어민 ASR·정렬 평가 |
| KSS | 약 3.55 GiB | 비상업 단일화자 TTS 기준선 |
| Seoul Corpus | 약 2.49 GiB | 원어민 음소·자발화 운율 |
| ZEROTH-Korean | 약 9.63 GiB | 원어민 ASR·강제정렬 |

## 11. 고성능 GPU에서 추가할 학습자 데이터

71490만으로 기본 모델과 pipeline gate를 먼저 통과한 뒤 L1 범위를 확장한다.
처음부터 모든 일반 speech를 받지 말고 발음 묶음부터 받는다.

| 우선순위 | 데이터셋 | 발음 묶음 file keys | 약식 목적 |
|---:|---|---|---|
| P1 | 71469 영어 L1 | 475406,475407,475409,475410,475412,475414,475415 | 영어권 오류 일반화 |
| P1 | 71479 기타 아시아어 L1 | 475444,475445,475447,475448,475450,475452,475453 | 아시아 언어권 확대 |
| P1 | 71489 유럽어 L1 | 475419,475420,475422,475423,475425,475427,475428 | 유럽 언어권 확대 |
| P2 | 505 외국인 한국어 발화 | 목록 재조회 후 선택 | 읽기발화 robustness·사전학습 |

예:

```bash
bash scripts/download_aihub_cli.sh list 71469
bash scripts/download_aihub_cli.sh download 71469 \
  475406,475407,475409,475410,475412,475414,475415 \
  data/restricted/aihub/dataset-71469
```

P1 세 묶음은 공식 표시 기준 합계가 약 310 GiB이므로 71490 실험을 끝낸 뒤
순차적으로 받는다. 데이터셋 505는 71490 계열과 동일한 목표/실현 음소
감독을 보장하지 않으므로 우선적인 오류 classifier 학습 데이터로 합치지
않는다.

## 12. 다운로드가 아니라 직접 만들어야 하는 데이터

논문의 폐쇄루프를 완성하려면 공개 데이터만으로 충분하지 않다.

- 고정 연습문장 20개에 대한 원어민 전문가 기준음 2–3개
- 목표 음소·실현 음소 정렬을 전문가가 감사한 최소 240건
- 코칭 문장 안전성·근거 적합성을 평가한 최소 150건
- 자동 점수 calibration을 위한 전문가 이중평가 표본 약 800건
- 최소 30명, 1인 12문장 규모의 녹음→코칭→재녹음 feasibility pilot

이 데이터는 동의서, 보존기간, 철회 절차와 비식별화 정책을 승인받은 뒤
수집한다. 원본은 GitHub가 아니라 접근 제어 storage에 둔다.

## 13. 모델 가중치와 cache

모델 파일도 GitHub에 커밋하지 않는다. Hugging Face cache를 persistent
volume으로 지정한다.

```bash
export HF_HOME=/data/intelligentAI/data/cache/huggingface
export HF_HUB_DOWNLOAD_TIMEOUT=60
python -m pip install -U huggingface_hub

hf download microsoft/wavlm-base-plus --dry-run
hf download microsoft/wavlm-base-plus
hf download microsoft/wavlm-large --dry-run
hf download microsoft/wavlm-large
```

실험 config에는 모델 ID뿐 아니라 실제 snapshot revision을 기록한다.
TTS 가중치는 진단기 gate를 통과한 뒤 별도 환경에 받고 scorer와 같은
프로세스에 동시에 올리지 않는다.

Hugging Face의 현재 CLI는 `hf download`이며, `HF_HOME`으로 cache 위치를
고정할 수 있다.

- https://huggingface.co/docs/huggingface_hub/guides/cli
- https://huggingface.co/docs/huggingface_hub/guides/download

## 14. 최종 체크리스트

- [ ] GPU 머신의 리전·운영 주체가 데이터 이용 조건에 부합한다.
- [ ] persistent volume과 최소 여유 공간을 확인했다.
- [ ] `AIHUB_API_KEY`가 파일·history·Git에 남지 않는다.
- [ ] dataset approval이 완료되었다.
- [ ] list mode로 최신 file key를 다시 확인했다.
- [ ] archive와 추출 파일 검증 marker가 생성되었다.
- [ ] 전체 manifest QC gate를 통과했다.
- [ ] split 간 화자와 PCM hash 중복이 없다.
- [ ] `HF_HOME`, feature cache, checkpoint가 persistent volume을 가리킨다.
- [ ] 원시 데이터와 모델이 `.gitignore` 밖으로 유출되지 않는다.
