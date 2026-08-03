# 원어민 한국어 코퍼스 수집·검증 파이프라인

## 대상과 용도

| 데이터셋 | MVP 내 역할 | 라이선스 |
|---|---|---|
| KSS | 단일 화자 한국어 TTS 기준선 | CC-BY-NC-SA-4.0 |
| Seoul Corpus | 원어민 음소 정렬·자발화 운율 기준 | CC-BY-NC-2.0 |
| ZEROTH-Korean | 원어민 ASR·강제정렬 기준선 | CC-BY-4.0 |
| FLEURS ko-KR | 외부 원어민 ASR·정렬 평가 | CC-BY-4.0 |

KSS와 Seoul Corpus는 비상업 연구 경로에만 둔다. 이 네 데이터셋은
학습자 오류 라벨을 제공하지 않으므로 AI Hub L2 코퍼스를 대체하지 않고,
원어민 참조·정렬·TTS 보정용으로 병행한다.

## 단일 실행 진입점

카탈로그는
`configs/data/native_korean_corpora.psd1`, 실행기는
`scripts/sync_native_korean_corpora.ps1`이다. PowerShell 실행 정책이
제한된 Windows에서는 다음처럼 실행한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Status
```

실제 파일을 변경하지 않는 사전 점검:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Download -DryRun

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Extract -DryRun
```

다운로드 재개와 검증:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Download

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Verify
```

충분한 디스크 공간을 확인한 뒤 압축 해제:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Extract
```

특정 데이터셋만 처리하려면 `-Dataset kss`처럼 지정한다. 가능한 ID는
`kss`, `seoul-corpus`, `zeroth-korean`, `fleurs-ko-kr`이다.

## 완료 판정

단순히 최종 파일이 존재한다는 이유로 완료 처리하지 않는다.

1. 선언된 바이트 수를 확인한다.
2. Hugging Face 파일은 고정 리비전의 SHA-256과 비교한다.
3. OpenSLR 파일은 배포본 바이트 수와 gzip CRC32를 비교하고 tar 전체를
   순회한다. OpenSLR가 SHA-256을 게시하지 않으므로 검증 시 계산한
   SHA-256을 로컬 완료 마커에 기록한다.
4. Parquet은 앞뒤 `PAR1` 매직을 확인한다.
5. 안전한 tar 경로만 허용하며 경로 탈출, 링크, 장치 파일을 거부한다.
6. 검증된 결과에 대해서만 `.native-corpus-state/*.complete.json`과
   `download.complete.json`을 기록한다.

완료 마커에는 파일 크기, SHA-256, 수정 시각, 구조 검증 결과가 들어간다.
파일의 크기나 수정 시각이 달라지면 마커는 무효가 된다. `Verify`는
마커와 관계없이 필요한 전체 해시·구조 검증을 다시 수행한다.

다운로드 중 파일은 `<최종 파일>.part`로 보존하고 `curl --continue-at -`로
재개한다. 검증을 통과하기 전에는 최종 이름으로 바꾸지 않는다. 기존
BITS가 만든 `BIT*.tmp` ZEROTH 파일도 정확한 크기의 후보가 하나일 때만
전체 검증 후 안전하게 채택한다.

## FLEURS 메타데이터 검증

오디오 `train/dev/test.tar.gz`뿐 아니라 같은 고정 리비전의 다음 파일을
필수로 수집한다.

- `train.tsv`: 2,307행
- `dev.tsv`: 226행
- `test.tsv`: 382행
- `README.source.md`: `cc-by-4.0` 및 Creative Commons CC-BY 근거 보존

각 TSV는 7개 열, 정수 ID·샘플 수, 고유 WAV 파일명을 검사한다. 이어서
TSV의 WAV 파일명 집합과 대응 tar 안의 WAV 파일명 집합이 정확히 같은지
확인한다. 이 검증을 통과하지 않은 FLEURS 오디오는 완료 처리하지 않는다.

## 압축 해제 안전성

압축 해제는 기존 정상 크기 파일을 건너뛰므로 중단 후 재실행할 수 있다.
완료 후 모든 일반 파일의 개수와 바이트 수를 tar 메타데이터와 비교하고
각 출력 디렉터리에 `.native-corpus-extraction-complete.json`을 기록한다.

실행 전 남은 추출 바이트와 1 GiB 여유 공간을 요구한다. 원본 아카이브는
자동 삭제하지 않는다. 디스크가 부족하면 `Extract`를 시작하지 말고
AI Hub 정리·검증이 끝난 뒤 다시 `Extract -DryRun`으로 확인한다.

## 통합 manifest 생성

검증·추출된 네 코퍼스는 다음 명령으로 하나의 운영용 manifest와 공개
가능한 집계 report로 결합한다. `--limit`은 전체 limit가 아니라 source별
limit이므로 smoke에서도 네 loader를 모두 실행한다.

```bash
.venv-scorer/bin/python scripts/build_native_korean_manifest.py \
  --output data/processed/native-korean/manifests/native_korean_smoke.parquet \
  --report data/processed/native-korean/manifests/native_korean_smoke.report.json \
  --limit 5
```

전체 고정본은 `--limit`을 제거해 생성한다.

```bash
.venv-scorer/bin/python scripts/build_native_korean_manifest.py \
  --output data/processed/native-korean/manifests/native_korean_v1.parquet \
  --report data/processed/native-korean/manifests/native_korean_v1.report.json
```

loader는 원본을 수정하지 않는다. FLEURS·Seoul·ZEROTH는 음성 header를
읽기 전용으로 검사하고 corpus root 기준 상대경로만 저장한다. KSS는
Parquet `audio.bytes`를 메모리 stream으로 열어 metadata만 검사하며 별도
음성 파일로 materialize하거나 embedded audio를 재작성하지 않는다.

manifest의 stable ID는 source ID와 canonical locator를 namespaced
SHA-256으로 결합한다. manifest에는 transcript 원문과 audio bytes를
저장하지 않고 transcript가 존재하는지와 접근 제한 reference만 저장한다.

## 2026-07-29 전체 생성 실측

전체 38,729개 audio object를 읽어 다음 결과를 얻었다. 코퍼스마다 기록
단위가 다르므로 이 합계를 동일한 길이의 “발화 수”로 해석하지 않는다.
특히 Seoul Corpus 한 행은 긴 세션 FLAC 한 개다.

| source | record | 시간 | format·sample rate | channel | split |
|---|---:|---:|---|---|---|
| FLEURS ko-KR | 2,915 | 10.040 h | WAV, 16 kHz | mono 2,915 | train 2,307 / dev 226 / test 382 |
| KSS | 12,854 | 12.857 h | embedded WAV, 44.1 kHz | mono 12,853 / stereo 1 | train 12,854 |
| Seoul Corpus | 240 | 42.752 h | FLAC, 22.05 kHz | mono 240 | unspecified 240 |
| ZEROTH-Korean | 22,720 | 52.871 h | FLAC, 16 kHz | mono 22,720 | train 22,263 / test 457 |
| **합계** | **38,729** | **118.519 h** | - | mono 38,728 / stereo 1 | - |

검증 결과는 다음과 같다.

- source별 record 수가 고정 기대값 `2915/12854/240/22720`과 일치
- libsndfile header metadata 유효 38,729건, 실패 0건
- transcript reference 존재 38,729건
- stable SHA-256 ID 38,729개, 중복 0개
- locator 계약 위반 0건, 절대 상대경로 0건
- transcript 원문·embedded audio bytes·direct identifier 열 0건
- 공개 집계 report에서 source audio path, transcript reference와 raw
  file-stem **값** 검출 0건

재현 식별자는 다음과 같다.

- `native_korean_v1.parquet`: 1,845,960 bytes
- manifest SHA-256:
  `2562b52c31de9bd1c85e8d9cc446c9ceaaebe53a3a7f8679834f8a39c251d5a6`
- 현재 집계 report SHA-256:
  `d50e6f788f6e631c37b54631325dfe840c0460ecf342af71f5e38b6f638037b4`

## 접근·공유 경계

두 출력은 같은 공유 등급이 아니다.

| artifact | 구역 | 포함 내용 | 외부 공유 |
|---|---|---|---|
| `native_korean_v1.parquet` | R1 운영 제한구역 | source 상대경로 또는 KSS embedded reference, transcript reference, 기술 metadata | 금지 |
| `native_korean_v1.report.json` | A1 집계 결과구역 | count, 시간·format·sample-rate 분포, license/role, checksum, 검증 결과 | 별도 검토 후 가능 |
| `DATA_CARD_NATIVE_KOREAN_KO.md` | A1/C1 | 집계 실측값과 사용 제한 | 가능 |

운영 manifest는 직접 식별자와 원문을 제거했지만 locator/reference를
포함하므로 “privacy safe”라고 부르지 않는다. schema metadata는
`privacy_reviewed=true`, `contains_direct_identifiers=false`,
`access_scope=restricted_internal`, `shareable=false`로 고정한다.

공개 report의 `report_contains_audio_locator`,
`report_contains_transcript_reference`, `report_contains_raw_file_stem`
같은 문자열은 검사 대상 **종류를 설명하는 key**다. 실제 locator,
reference 또는 stem 값은 report에 없으며 전수 값 비교 결과도 0건이다.

stable hash와 checksum은 재현 식별자일 뿐 재배포 허가가 아니다.

- KSS와 Seoul Corpus의 원본·locator·파생 feature/model은 비상업 연구
  경로에만 둔다. 두 source의 raw redistribution flag는 `false`다.
- FLEURS와 ZEROTH도 CC-BY-4.0 출처 표시를 유지하며 학습자 오류
  감독학습 label로 사용하지 않는다.
- 어떤 source도 transcript 원문, 음성, raw file 목록을 Git 또는 공개
  report에 포함하지 않는다.
