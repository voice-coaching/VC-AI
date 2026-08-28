# 연구 데이터 저장소

이 디렉터리에는 데이터셋의 원본 파일을 커밋하지 않습니다.

- `raw/open/`: 공개 라이선스 코퍼스(예: Zeroth Korean, Common Voice Korean)
- `restricted/aihub/`: AI Hub 승인 데이터. 약관상 제3자 제공·재배포 금지이며 접근 권한이 있는 연구원만 사용합니다.
- [`manifests/`](manifests/): 검토한 다운로드 출처·버전·검증값을
  커밋하는 카탈로그입니다. 각 source의 실제 이용·재배포 조건은 해당
  source 약관과 manifest를 다시 확인합니다.

원본 음성·전사·화자 메타데이터는 외부 LLM이나 공개 저장소로 전송하지 않습니다.
`data/processed/`의 운영 manifest도 원천 상대경로, file stem,
파생 화자 식별자나 세부 annotation을 포함할 수 있으므로 자동으로
공개 가능한 산출물로 간주하지 않습니다. 현재 tracked tree에는
데이터 전체에 대한 독립 privacy/license matrix가 없으므로, 새 정본이
검토되기 전까지 [연구·데이터 거버넌스 전역 규칙](../.codex/ai_rule_developer/GLOBAL_RULES.md)의
fail-closed 반출 경계를 적용합니다.

원어민 한국어 코퍼스는
[`scripts/sync_native_korean_corpora.ps1`](../scripts/sync_native_korean_corpora.ps1)로
수집·검증·압축 해제합니다. 파일 존재 여부만으로 완료 처리하지 않으며
고정 체크섬과 `.native-corpus-state` 완료 마커를 함께 확인합니다.
AI Hub manifest는 [`scripts/build_aihub71490_manifest.py`](../scripts/build_aihub71490_manifest.py)의
CLI 계약으로 다시 생성합니다. 두 script가 생성하는 raw/processed state는
새 clone에 포함되지 않으며, 실행 전에 각 `--help`/`-Action Status`와
승인된 원천 접근 권한을 확인합니다.
