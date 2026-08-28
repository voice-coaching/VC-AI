# intelligentAI — 한국어 AI 보이스 코칭 연구

한국어 학습자의 발음을 진단하고, 근거가 있는 코칭과 기준 음성을 제공한 뒤,
재녹음 결과를 비교하는 폐쇄루프 음성 코칭 시스템의 연구용 구현 저장소입니다.

## 현재 상태

- 연구·계약 검증용 Oracle API와 음성 분석·코칭·참조음·재녹음 비교
  구성 요소가 모듈별로 구현되어 있습니다.
- 오디오 `seungun` 경로의 실험 상태는 오디오 정본과 실험 이력을,
  시각 경로의 상태는 visual sidecar 정본을 각각 따로 따릅니다.
- visual sidecar는 `partial_implementation`이며 실제 사용자 영상 runtime은
  `not_ready`입니다. G04 오디오 AI 재청취는 engineering proxy이고
  canonical 전문가 평가를 대체하지 않으며, C03도 static readiness 범위입니다.
- 현재 tracked tree에는 저장소 전체를 대표하는 단일 completion tracker가
  없습니다. 삭제된 과거 문서명을 현재 정본으로 사용하지 않고,
  아래의 현행 단계별 문서와 실제 artifact/receipt를 함께 확인합니다.

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

- [프로젝트 맥락과 상태 판정 가이드](.codex/ref_docs/PROJECT_CONTEXT_AND_GOVERNANCE_KO.md)
- [API endpoint 목록](docs/api/endpoints.md) / [API 정확한 계약](docs/api/specification.md)
- [Seungun 오디오 경로 안내](seungun/README.md)
- [Seungun 현재 pipeline 설명](seungun/docs/CURRENT_PIPELINE_KO.md.md)
- [Seungun 실험 이력과 실측 결과](seungun/docs/WORK_LOG_AND_FINDINGS_KO.md)
- [visual sidecar 현행 정본](docs/architecture/README_KO.md)
- [G04 AI proxy protocol](docs/evaluation/G04_AUDIO_AI_PROXY_PROTOCOL_KO.md) /
  [결과와 한계](docs/evaluation/G04_AUDIO_AI_PROXY_RESULT_2026-08-09_KO.md)
- [C03 전문가 코칭 감사 static card](docs/evaluation/C03_EXPERT_COACHING_AUDIT_DATA_MODEL_PRIVACY_SAFETY_STATISTICS_CARD_KO.md)
- [실험 결과 반영용 편집 원고](docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md)
- [논문 주장·증거 갭 맵](docs/paper/CLAIM_GAP_MAP_KO.md) /
  [방법·윤리·재현성 갭 감사](docs/paper/METHODS_ETHICS_REPRODUCIBILITY_GAP_KO.md)
- [연구 데이터 저장 경계](data/README.md)
- [2026-08-28 프로젝트 전수 감사·테스트 기록](docs/PROJECT_AUDIT_AND_VERIFICATION_2026-08-28_KO.md)
- [새 clone 재현 및 공개 evidence 경계](artifacts/public/README.md)
- [2026-08-26 이전 아키텍처 snapshot](docs/history/legacy_voice_coach_architecture_2026-08-26/README_KO.md) —
  `historical_snapshot`이며 현재 정본이 아닙니다.

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
