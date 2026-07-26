# intelligentAI — 한국어 AI 보이스 코칭 연구

한국어 학습자의 발음을 진단하고, 근거가 있는 코칭과 기준 음성을 제공한 뒤,
재녹음 결과를 비교하는 폐쇄루프 음성 코칭 시스템의 연구용 구현 저장소입니다.

## 현재 상태

- 논문 기반 전체 구현 계획과 GTX 1660 MVP 실험 설계 완료
- AI Hub 71490 학습자 음성 437,841개, 약 291.91 GiB 압축 해제·크기 검증 완료
- WAV–JSON–TextGrid 결합, 음성 디코드 QC, 화자 비중첩 split 생성기 구현
- KSS, Seoul Corpus, ZEROTH-Korean, FLEURS ko-KR 수집·검증 파이프라인 구현
- 고성능 GPU 전환을 위한 단계별 실험 계획 수립

AI Hub 원본, 공개 코퍼스 원본, 처리된 음성, 모델 체크포인트는 GitHub에
올리지 않습니다. 저장소에는 재현 가능한 코드, 설정, 체크섬 manifest와
연구 문서만 보관합니다.

## 시스템 구성

1. **S1 진단기**: 학습자 음성에서 음소 오류, 유창성, 휴지, 속도와 운율을 분석
2. **코칭 정책**: confidence와 근거가 충분한 최상위 오류만 교정 문장으로 변환
3. **기준 음성**: 원어민 녹음 또는 사전 생성 TTS 참조음을 제공
4. **폐쇄루프 비교**: 재녹음 전후의 동일 지표와 불확실성을 비교
5. **교정·감사**: 전문가 라벨과 독립 평가 세트로 자동 평가기의 편향을 점검

## 주요 문서

- [전체 구현 계획](docs/IMPLEMENTATION_PLAN_KO.md)
- [GTX 1660 MVP 실험 설계](docs/GTX1660_MVP_EXPERIMENT_DESIGN_KO.md)
- [고성능 GPU 전환 계획](docs/HIGH_PERFORMANCE_GPU_EXECUTION_PLAN_KO.md)
- [CLI 전용 GPU 머신 데이터 준비](docs/CLI_ONLY_GPU_DATA_SETUP_KO.md)
- [현재 작업 상태와 인수인계](docs/PROJECT_STATUS_AND_HANDOFF_KO.md)
- [원어민 코퍼스 파이프라인](docs/NATIVE_KOREAN_CORPUS_PIPELINE_KO.md)
- [연구의 기반 논문](docs/한국어%20AI%20보이스%20코칭을%20위한%20폐쇄루프%20TTS%20아키텍처.pdf)

## 데이터 준비

원어민 코퍼스 상태 확인:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Status
```

다운로드와 검증을 변경 없이 사전 점검:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\sync_native_korean_corpora.ps1 -Action Download -DryRun
```

AI Hub manifest의 소규모 smoke test:

```powershell
python .\scripts\build_aihub71490_manifest.py `
  --dataset-root .\data\restricted\aihub\dataset-71490 `
  --output-dir .\data\processed\aihub71490\smoke-manifests `
  --limit 100 `
  --with-speaker-splits
```

원본 데이터의 위치와 라이선스·체크섬은 [data/README.md](data/README.md)와
[data/manifests](data/manifests)에서 관리합니다.
