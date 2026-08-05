# intelligentAI — 한국어 AI 보이스 코칭 연구

한국어 학습자의 발음을 진단하고, 근거가 있는 코칭과 기준 음성을 제공한 뒤,
재녹음 결과를 비교하는 폐쇄루프 음성 코칭 시스템의 연구용 구현 저장소입니다.

## 현재 상태

- NVIDIA GB10/CUDA 13 고성능 GPU 환경과 WavLM Base+/Large smoke 검증 완료
- AI Hub 71490 발음 WAV–JSON–TextGrid 322,380건 추출·검증 완료
- 전체 raw/enriched manifest와 화자 비중첩 고정 split 생성 완료
- 자동 G0 gate 통과, 240건 전문가 수동 정렬 감사 진행 중
- KSS, Seoul Corpus, ZEROTH-Korean, FLEURS ko-KR 수집·검증 완료

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

- [docs 폴더 구조 안내](docs/README.md)
- [전체 구현 계획](docs/IMPLEMENTATION_PLAN_KO.md)
- [고성능 GPU 실행 계획](docs/HIGH_PERFORMANCE_GPU_EXECUTION_PLAN_KO.md)
- [현재 작업 상태와 인수인계](docs/constraints/PROJECT_STATUS_AND_HANDOFF_KO.md)
- [연구 완성 하위 작업 추적기](docs/constraints/RESEARCH_COMPLETION_TRACKER_KO.md)
- [2026-07-28 실행 로그와 다음 작업](docs/EXECUTION_LOG_2026-07-28_KO.md)
- [AI Hub 71490 데이터 카드](docs/constraints/DATA_CARD_AIHUB71490_KO.md)
- [연구 데이터 개인정보·반출 경계](docs/constraints/DATA_PRIVACY_BOUNDARY_KO.md)
- [데이터 라이선스·역할 매트릭스](docs/constraints/DATA_LICENSE_AND_ROLE_MATRIX_KO.md)
- [AI Hub 71490 수동 정렬 감사 절차](docs/constraints/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md)
- [실험 결과 반영용 편집 원고](docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md)
- [논문 수치 증거 검증](docs/MANUSCRIPT_EVIDENCE_VALIDATION_KO.md)
- [Frozen WavLM feature cache 독립 검증](docs/constraints/WAVLM_FEATURE_CACHE_VALIDATION_KO.md)
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
