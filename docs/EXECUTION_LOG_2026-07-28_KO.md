# 고성능 GPU 머신 실행 로그와 다음 작업

기준 시각: 2026-07-28 KST

이 문서는 NVIDIA GB10 머신에서 실제 수행한 작업, 검증 결과, 현재 실행
중인 작업과 다음 실행 순서를 기록한다. 비밀정보, 제한 데이터 원문과 원시
화자 식별자는 기록하지 않는다.

## 1. 완료한 작업

### 실행 환경

- NVIDIA GB10, compute capability 12.1, CUDA 13.0을 확인했다.
- Python 3.12 가상환경 `.venv-scorer`를 구성했다.
- PyTorch 2.12.1+cu130의 CUDA BF16 연산을 검증했다.
- PowerShell 7.6.3 ARM64와 공식 AI Hub shell 25.09.19 v0.6을 설치했다.
- `.env.local`은 권한 `600`으로 유지하고 Git에서 제외했다.
- GTX 1660 전용 실험 설정과 문서를 삭제하고 고성능 GPU 설정으로
  교체했다.

### GPU smoke benchmark

WavLM Base+와 Large를 8/12/20초 합성 16 kHz 입력으로 frozen BF16
추론했다.

| 모델 | revision | OOM | p95 RTF | peak CUDA allocation |
|---|---|---:|---:|---:|
| WavLM Base+ | `4c66d4806a428f2e922ccfa1a962776e232d487b` | 0 | 0.00222-0.00280 | 0.54-0.70 GiB |
| WavLM Large | `c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c` | 0 | 0.00522-0.00624 | 1.38-1.64 GiB |

이 결과는 처리량과 메모리 smoke test다. Large 채택 여부는 동일 locked
split의 phone macro F1이 Base+보다 0.03 이상 개선되는지로 결정한다.

Large 원본 JSON이 누락된 것을 2026-07-29 재현성 감사에서 발견해 동일
local revision·GB10·BF16·8/12/20초·10회 조건으로 다시 실행했다.
artifact는 `artifacts/hardware/nvidia-gb10/wavlm-large-bf16.json`,
SHA-256은
`11b0a157dc650f3f834d723d60a4967d2a118eb63a62bc633557f2fd09c402d0`이다.
Base+ artifact SHA-256은
`cb49f5e6ec6475a6925e8d46608f45472f7f800e85922022289287eeb3f32ad4`다.

### 원어민·공개 한국어 코퍼스

| 데이터 | 완료 결과 |
|---|---|
| FLEURS ko-KR | 고정 revision/checksum 검증, WAV 2,915개 추출 |
| KSS | Parquet 7개, 12,854행, embedded RIFF audio 12,854개 전수 확인 |
| Seoul Corpus | 이중압축 안전 해제, FLAC/TextGrid 240/240 stem 완전 일치 |
| ZEROTH-Korean | archive hash/gzip/tar 검증, FLAC/전사 22,720/22,720 ID 완전 일치 |

네 데이터는 `scripts/sync_native_korean_corpora.ps1 -Action Verify -Dataset all`
재검증을 통과했다. 샘플 디코더 검사에서 FLEURS와 ZEROTH는 16 kHz,
KSS는 44.1 kHz, Seoul Corpus는 22.05 kHz mono임을 확인했다.

### AI Hub 71490 발음 MVP

새 API key로 dataset 권한 확인에 성공한 뒤 다음 7개 묶음을 공식 CLI로
다운로드했다.

- Training pronunciation sound 1/2
- Training pronunciation JSON/TextGrid
- Validation pronunciation sound
- Validation pronunciation JSON/TextGrid

공식 CLI tar 전송량은 약 68.6 GiB였고 HTTP 200, 분할 ZIP 병합 완료,
잔여 `.part` 0, 잔여 `download.tar` 0을 확인했다.

Linux GNU tar가 ZIP을 열지 못하는 이식성 문제와 AI Hub ZIP entry가
선행 `/`를 사용하는 비표준 구조를 발견했다. 다음과 같이 수정했다.

- `scripts/extract_verified_zip.py`
  - 경로 탈출과 symbolic link 차단
  - AI Hub에만 선행 separator 정규화 허용
  - 파일별 CRC와 크기 검증
  - 대량 작은 파일용 선택적 per-file fsync 생략
  - 재개 가능한 `.part` 원자 교체
- `scripts/extract_aihub_labels.ps1`
  - GNU tar 대신 안전 ZIP 추출기 사용
- `scripts/extract_aihub_audio.ps1`
  - GNU tar 대신 안전 ZIP 추출기 사용
  - 파일 수·총 바이트 재검증 후에만 원본 ZIP 삭제

실제 추출·검증 결과는 다음과 같다.

| 구분 | Training | Validation |
|---|---:|---:|
| 목표 JSON | 286,560 | 35,820 |
| 실현 TextGrid | 286,560 | 35,820 |
| WAV | 286,560 | 35,820 |
| WAV 해제 바이트 | 105,917,121,664 | 13,193,415,824 |

Training WAV는 주 묶음 283,667개와 보조 묶음 2,893개의 합이다. 검증이
끝난 음성 ZIP 3개는 삭제해 공간을 회수했고 라벨 ZIP은 보존했다.

### 100건 manifest smoke test

`scripts/build_aihub71490_manifest.py --limit 100
--with-speaker-splits`를 실행했다.

- WAV–JSON–TextGrid 결합: 100/100
- 모델 사용 가능 record: 100/100
- 디코드 오류: 0
- 누락 modality: 0
- 중복 stem: 0
- 원시 `UserID` 노출: 0
- 고유 화자: 95
- split 간 화자 교집합: 모든 조합 0
- 관측 sample rate: 16/24/44.1/48/96 kHz

원본 WAV header의 byte rate를 신뢰하지 않고 실제 decoder frame 수로
길이를 계산한다.

### 100건 enriched Parquet G0 smoke test

실데이터 JSON/TextGrid 스키마를 확인해
`scripts/enrich_aihub71490_manifest.py`를 구현하고 100건에 실행했다.

- Parquet record/column: 100/22
- 목표 phone과 실현 phone 시간 overlap 정렬: 완료
- phone alignment: match 1,692, substitution 1,289, deletion 3
- PCM hash 누락: 0
- split 간 PCM hash 중복: 0
- split 간 화자 중복: 0
- interval/parse QC flag: 0
- smoke G0 status: passed

### 구현된 연구 코드

- 엄격한 `DiagnosticProfile` Pydantic 계약
- 연구용 Oracle endpoint와 content/confidence/allowlist gate
- 근거 제한 코칭 catalog와 우선순위 계산
- duration, RMS, clipping, ZCR, pause, voiced ratio, F0, nPVI 특징
- scikit-learn 명시적 phone/utterance 기준선
- TextGrid interval parser
- 목표/실현 음소 시간 overlap 정렬
- WAV PCM SHA-256 중복 탐지
- 화자 단위 결정적 층화 split
- G0 manifest 감사
- frozen SSL interval/utterance pooling
- temperature scaling, ECE와 risk-coverage

현재 Python 테스트 25개가 통과한다. FastAPI TestClient에서 upstream
deprecation warning 1개가 있으나 테스트 실패는 아니다.

### 전체 raw manifest와 기본 QC

첫 전체 실행에서 숨김 추출 marker 두 개가 `.json`으로 색인되어 가짜
record 1건이 생기는 문제를 발견했다. scanner가 숨김 파일을 제외하도록
수정한 뒤 재실행했다.

- 실제 source file: 967,140개, 즉 322,380 × 3 modalities
- record: 322,380
- WAV/JSON/TextGrid: 각각 322,380
- 누락/중복/parse 오류: 0
- model complete: 322,380
- 고유 화자: 1,830
- raw `UserID` 노출: 0
- split 간 화자 교집합: 모든 조합 0
- raw manifest SHA-256:
  `b91920ccb4e12d3e611b62a14047c0f663f0dfe4d7572fc8049e59b95fea46ed`
- speaker split manifest SHA-256:
  `6d121591f98d3916617df9efe6b0f86371c92bdd2c6c951b003cdabde6ec7a1a`

관측 sample rate에는 8/16/24/44.1/48/96/192 kHz가 포함되므로 SSL
입력은 decoder 이후 16 kHz로 명시적으로 resample해야 한다.

### 전체 enriched Parquet와 자동 G0 감사

전체 322,380건에 목표·실현 음소 정렬, PCM hash, 고정 화자 split을
적용했다. 첫 전체 결과를 검토하는 과정에서 30ms 허용오차와 다대다 매핑이
결합되어 맞닿은 이웃 음소가 중복 치환으로 기록되는 문제를 발견했다.
pipeline을 일대일 최강 overlap 매핑으로 교정하고 전량 재생성했다.

- record/column: 322,380/22
- pairing rate: 99.9996898%
- decoder failure rate: 0%
- time-bound error rate: 0.3319064%
- annotation parse failure: 0
- split 간 화자 중복: 0
- split 간 PCM hash 중복: 0
- 고유 화자: 1,830
- 고유 PCM hash: 322,368
- phone operation:
  - match 3,991,194
  - substitution 267,049
  - deletion 38,385
  - insertion 25,783
- split assignment SHA-256:
  `d3f0324d61afb9cebe7c002de0f39428584124771f8469703e60fc62e121ed84`
- enriched Parquet SHA-256:
  `291097f4c1a62f8c1c86ce7cbedfcb04bc1e1b2f0a0b8958d8cdcd23ee0f25e5`

자동 G0 gate는 모두 통과했다. 전체 G0는 독립 전문가의 240건 수동 정렬
감사가 끝나기 전까지 `pending_manual_alignment_audit` 상태다.

## 2. 현재 시작한 작업

L1·성별·숙련도·오류 유무 50개 층을 모두 포함하는 240건 수동 감사
표본을 고정 seed로 생성했다. 각 층에 최소 2건을 배정하고 나머지는
모집단 크기에 비례해 배정했다.

- 검수 worksheet:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv`
- 자동 매핑 상세:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl`
- 표본 재현 메타데이터:
  `data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_metadata.json`
- worksheet SHA-256:
  `7e70e1d4d5742688bd1e6544f97527f8362712efdc28827df8f0e7503c1a237c`

검수 방법과 판정 규칙은 `docs/constraints/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md`에
기록했다. 실제 청취·경계 판정은 한국어 음성학/강제정렬 검수 역량이 있는
사람이 수행해야 한다.

## 3. 다음 실행 순서

### 1. 전체 manifest와 기본 QC 완료

완료 기준:

- 완료: record 322,380개
- 완료: WAV–JSON–TextGrid 결합률 100%
- 완료: JSON/WAV parse 오류 0
- 완료: split 간 화자 교집합 0
- 완료: 원시 `UserID` 비노출
- 완료: manifest와 QC SHA-256 기록

### 2. G0용 enriched Parquet 고정

- 완료: 시간 overlap 기반 일대일 목표·실현 phone 결합
- 완료: PCM hash와 화자 단위 층화 split 고정
- 완료: train/dev/stack/calibration/locked_test와 hash 기록
- 완료: 자동 interval/decoder/pairing/누수 gate 통과
- 진행 필요: 독립 전문가 240건 수동 정렬 감사

완료 기준:

- 완료: pairing rate 99% 이상
- 완료: decoder failure rate 0.1% 이하
- 완료: time-bound error rate 1% 이하
- 완료: split 간 PCM hash 중복 0
- 완료: split 간 화자 중복 0
- 수동 정렬 감사 240건 정확도 95% 이상

### 3. 실제 명시적 기준선

- 고정 split에서 명시적 특징을 추출한다.
- phone error macro F1, AUPRC, event F1을 측정한다.
- utterance QWK, Spearman, MAE, ICC를 측정한다.
- L1·성별·숙련도·길이·오류 유형 slice를 기록한다.

### 4. frozen WavLM Base+/Large 비교

- 동일 manifest와 split을 사용한다.
- phone/word/utterance pooling feature cache를 만든다.
- 동일 head와 seed 42/43/44로 비교한다.
- speaker bootstrap 5,000회와 Holm 보정을 적용한다.
- Large가 Base+보다 phone macro F1 0.03 이상 개선되지 않으면 Large를
  중단한다.

### 5. 통과 모델만 fine-tuning·fusion·calibration

- adapter 또는 partial fine-tuning
- 명시적 특징과 SSL embedding confidence-aware fusion
- calibration split만 사용한 temperature scaling
- ECE 0.10 이하, issued correction precision 0.90 이상 확인

### 6. 코칭·reference bank·end-to-end

- 근거 제한 코칭 150건 전문가 감사
- unsupported claim rate 2% 이하
- 원어민/TTS 기준음 20문장, 문장당 후보 4개 생성 후 3개 선정
- 녹음 → 진단 → 코칭 → 재녹음 한 사이클 demo
- silence/gain 등 metamorphic pass rate 95% 이상

### 7. 파일럿과 GRPO 판단

- 전문가·학습자 파일럿과 worst-group gap을 평가한다.
- 전체 phone F1 0.80, utterance ICC 0.70과 안전 gate를 통과한 뒤에만
  정책 최적화 또는 GRPO 확장을 검토한다.

## 4. 재개 지점

중단 후에는 다음 순서로 상태를 확인한다.

```bash
ls -lh data/processed/aihub71490/manifests
python scripts/audit_aihub71490_enriched.py \
  data/processed/aihub71490/manifests/aihub71490_enriched.parquet \
  --output data/processed/aihub71490/manifests/aihub71490_g0_audit.json
.venv-scorer/bin/pytest -q
```

제한 데이터와 `.env.local`은 Git에 추가하지 않는다. Git에 남기는 것은
코드, 설정, 문서, 집계 QC와 checksum뿐이다.
