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
