# 프로젝트 작업 상태와 인수인계

기준 시각: 2026-07-28 KST

> 2026-07-29 이후의 장기 실험 진행률과 현재 실행 큐는
> [연구 완성 하위 작업 추적기](./RESEARCH_COMPLETION_TRACKER_KO.md)를
> 기준으로 한다.

## 0. 고성능 GPU 머신 실행 현황

- 실행 머신은 NVIDIA GB10, CUDA 13.0, compute capability 12.1이다.
- Python 3.12 scorer 환경과 PyTorch 2.12.1+cu130을 분리 구성했고 CUDA
  BF16 연산을 확인했다.
- WavLM Base+와 Large의 8/12/20초 frozen BF16 smoke benchmark는 모두
  OOM 0으로 통과했다. Large의 p95 RTF는 0.00485-0.00610, peak CUDA
  allocation은 1.38-1.64 GiB였다.
- 새 API key로 AI Hub dataset 71490 권한 확인에 성공했다. 발음 MVP 7개
  묶음 약 68.6 GiB를 다운로드하고 WAV·JSON·TextGrid 322,380건을
  추출·검증했다. 전체 raw/enriched manifest 322,380건과 자동 G0 감사를
  완료했다. 자동 gate는 통과했으며 240건 전문가 수동 감사가 남았다.
- FLEURS ko-KR은 원본 검증과 2,915 WAV 추출을 완료했다.
- KSS는 Parquet 7개, 12,854행, embedded RIFF audio 12,854개를 전수
  확인했다.
- Seoul Corpus는 이중 압축을 안전하게 해제했으며 FLAC/TextGrid가 stem
  기준 240/240 완전 일치한다.
- ZEROTH-Korean은 9.1 GiB archive의 고정 hash·gzip·tar 검증과 안전 추출을
  완료했다. 16 kHz mono FLAC 22,720개와 transcript 22,720행이 ID 기준
  완전 일치한다.

## 1. 지금까지 완료한 작업

### 논문 분석과 실험 범위

논문의 폐쇄루프를 다음 구현 단위로 분리했다.

- 학습자 음성 진단기 S1
- 근거 제한 코칭 정책
- 원어민 또는 TTS 기준 음성 bank
- 재녹음 전후 비교
- 전문가 라벨 기반 calibration과 감사

외국인 학습자 한국어 데이터는 S1의 오류 감독학습에 사용하고, 한국인
원어민 데이터는 기준 발음·운율·강제정렬·TTS 보정에 사용한다. 두 데이터는
대체 관계가 아니라 서로 다른 모듈을 담당한다.

### AI Hub 71490

`교육용 중·일어 모국어 사용자의 한국어 음성 데이터`의 발음 MVP 7개
묶음을 공식 CLI로 받았다. 음성은 ZIP entry의 파일 수와 비압축 바이트를
실제 파일과 대조한 뒤 완료 marker를 만들었고, 검증된 음성 ZIP은 삭제해
디스크를 회수했다.

| 묶음 | 검증 파일 수 | 비압축 용량 |
|---|---:|---:|
| Training pronunciation 1 | 283,667 | 97.66 GiB |
| Training pronunciation 2 | 2,893 | 0.99 GiB |
| Validation pronunciation | 35,820 | 12.29 GiB |
| **합계** | **322,380** | **110.93 GiB** |

발음 WAV·JSON·TextGrid를 stem으로 결합한다. JSON의 목표 음소와
TextGrid의 실제 음소는 배열 인덱스가 아니라 시간 overlap 기반으로
정렬한다.

### Manifest와 개인정보 처리

`scripts/build_aihub71490_manifest.py`는 다음을 수행한다.

- 대소문자를 무시한 stem 기준 WAV–JSON–TextGrid 결합
- WAV payload 전체 디코드에 기반한 frame 수와 길이 계산
- 누락·중복·디코드 오류 QC
- raw split과 화자 비중첩 MVP split 생성
- 경로를 데이터셋 root 기준 상대경로로 저장
- 외부 `UserID`를 manifest에서 제거하고 SHA-256 가명 ID만 저장

실데이터 100건 smoke test에서는 완전 결합 100/100, 디코드 오류 0,
split 간 화자 교집합 0을 확인했다. 전체 manifest 생성은
`data/processed/aihub71490/manifests`에서 수행한다. 이 디렉터리는
Git에서 제외된다.

### 원어민 한국어 데이터

| 데이터셋 | 현재 로컬 상태 | 연구 역할 | 남은 일 |
|---|---|---|---|
| FLEURS ko-KR | 검증·추출 완료, WAV 2,915개 | 외부 ASR·정렬 평가 | loader smoke test |
| KSS | Parquet 7개·12,854행·embedded RIFF 전수 확인 | 비상업 TTS 기준선 | Parquet 직접 loader |
| Seoul Corpus | 안전한 이중압축 해제, FLAC/TextGrid 240쌍 | 음소 정렬·자발화 운율 | loader smoke test |
| ZEROTH-Korean | 검증·추출 완료, FLAC/전사 22,720쌍 | ASR·정렬 기준선 | loader smoke test |

통합 카탈로그는 `configs/data/native_korean_corpora.psd1`, 실행기는
`scripts/sync_native_korean_corpora.ps1`이다. Hugging Face 파일은 고정
revision과 SHA-256, OpenSLR 파일은 바이트·gzip CRC32·tar 전체 순회와
로컬 SHA-256으로 판정한다.

## 2. 구현된 주요 파일

| 경로 | 역할 |
|---|---|
| `docs/IMPLEMENTATION_PLAN_KO.md` | 논문 전체 구현·실현 계획 |
| `docs/HIGH_PERFORMANCE_GPU_EXECUTION_PLAN_KO.md` | 고성능 GPU 전용 실험·중단 기준 |
| `docs/CLI_ONLY_GPU_DATA_SETUP_KO.md` | Linux CLI 머신 재다운로드·추가 데이터 절차 |
| `docs/EXECUTION_LOG_2026-07-28_KO.md` | 실제 완료 내역·수치·다음 실행 순서 |
| `docs/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md` | 240건 G0 전문가 감사 절차 |
| `docs/DATA_CARD_AIHUB71490_KO.md` | 전체 데이터·label coverage·QC 집계 |
| `docs/RESEARCH_COMPLETION_TRACKER_KO.md` | 최종 논문까지 하위 작업·gate·진행률 |
| `configs/data/aihub71490_mvp.yaml` | AI Hub join·split·privacy 규칙 |
| `configs/experiments/high_performance_gpu.yaml` | 고성능 GPU 모델·평가 gate |
| `scripts/extract_aihub_audio.ps1` | 재개 가능한 음성 검증·추출·정리 |
| `scripts/build_aihub71490_manifest.py` | manifest와 QC 생성 |
| `scripts/enrich_aihub71490_manifest.py` | 음소 정렬·PCM hash·고정 split Parquet |
| `scripts/audit_aihub71490_enriched.py` | 전체 자동 G0 재감사 |
| `scripts/sample_alignment_audit.py` | 결정적 층화 수동 감사 표본 생성 |
| `scripts/score_alignment_audit.py` | 완성된 수동 감사 결과 판정 |
| `scripts/sync_native_korean_corpora.ps1` | 원어민 코퍼스 통합 실행기 |
| `scripts/native_corpus_archive.py` | 안전한 tar 검사·추출 |

## 3. 검증 완료 항목

- PowerShell 5.1 구문 검사
- Python 표준 라이브러리 manifest smoke test
- WAV/JSON/TextGrid 100건 결합
- raw `UserID` 비노출 확인
- 원어민 파이프라인 Download/Extract dry-run
- tar 경로 탈출·링크·장치 파일 차단 단위 테스트
- FLEURS dev.tsv 226행과 dev tar WAV 226개 이름 집합 일치
- Git raw/restricted/processed/artifact/checkpoint 제외 규칙
- GB10 CUDA BF16 matmul
- WavLM Base+/Large 8/12/20초 OOM 0
- TextGrid interval parser와 시간 overlap 음소 정렬
- 화자 단위 층화 split, PCM hash, G0 manifest 감사
- 인접 음소 중복 매핑 교정과 전체 enriched Parquet 재생성
- L1·성별·숙련도·오류 유무 층화 240건 수동 감사 worksheet
- Oracle 계약, 명시적 음향 특징, frozen SSL pooling
- 전체 Python 테스트 25개 통과

## 4. 남은 실행 순서

1. 진행 중: 고정된 240건 worksheet로 전문가 수동 정렬 감사
2. 228/240 이상 정확도를 확인하고 G0를 최종 고정
3. 명시적 음향 특징 baseline의 실제 phone F1 측정
4. 동일 split에서 frozen WavLM Base+와 Large phone F1 비교
5. 통과한 모델에만 adapter/partial fine-tuning 적용
6. calibration split에서 confidence 보정
7. 근거 제한 코칭 150건 전문가 감사
8. 원어민/TTS 기준음 20문장 bank 생성
9. 재녹음 전후 한 사이클 end-to-end demo

## 5. 데이터와 비밀정보 정책

- `.env.local`, AI Hub API key와 인증정보는 절대 커밋하지 않는다.
- AI Hub 원본과 파생 음성은 제3자에게 재배포하지 않는다.
- KSS와 Seoul Corpus는 비상업 연구 경로로 격리한다.
- GitHub에는 코드, 문서, URL, 고정 revision, 체크섬과 집계 QC만 올린다.
- 원시 음성·전사·화자 메타데이터·모델 체크포인트는 접근 제어된 외부
  저장소에 두고 Git에는 object key와 checksum만 기록한다.

## 6. GitHub에 200 GiB 데이터를 올리지 않는 이유

일반 GitHub 저장소는 파일 하나가 100 MiB를 넘으면 push를 차단하며,
저장소는 1 GiB 미만이 이상적이고 5 GiB 미만이 강하게 권장된다. Git LFS도
요금제별 개별 파일 한도와 저장공간·다운로드 대역폭 과금이 있다. 200 GiB
데이터는 기술적으로 유료 LFS 구성을 조합할 수 있더라도 비용, clone 시간,
버전별 전체 객체 재과금과 데이터 라이선스 때문에 이 프로젝트에는 적합하지
않다.

권장 구조:

```text
GitHub
  코드 / 설정 / 문서 / 작은 manifest / checksum

접근 제어 object storage 또는 연구실 NAS
  AI Hub 원본 / 공개 코퍼스 원본 / 처리 음성 / feature cache / checkpoint

DVC 또는 자체 catalog
  object key / 버전 / checksum / 생성 config / 접근 권한
```

참고:

- https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github
- https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage
- https://docs.github.com/en/billing/concepts/product-billing/git-lfs
