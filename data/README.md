# 연구 데이터 저장소

이 디렉터리에는 데이터셋의 원본 파일을 커밋하지 않습니다.

- `raw/open/`: 공개 라이선스 코퍼스(예: Zeroth Korean, Common Voice Korean)
- `restricted/aihub/`: AI Hub 승인 데이터. 약관상 제3자 제공·재배포 금지이며 접근 권한이 있는 연구원만 사용합니다.
- `manifests/`: 재현 가능한 다운로드 출처·버전·검증값만 보관합니다.

원본 음성·전사·화자 메타데이터는 외부 LLM이나 공개 저장소로 전송하지 않습니다.
`data/processed/`의 운영 manifest도 원천 상대경로나 file stem을 포함할
수 있으므로 자동으로 공개 가능한 산출물로 간주하지 않습니다. R0/R1/D1/A1
구역과 반출 검사는 `docs/DATA_PRIVACY_BOUNDARY_KO.md`를 따릅니다.

원어민 한국어 코퍼스는 `scripts/sync_native_korean_corpora.ps1`로
수집·검증·압축 해제합니다. 파일 존재 여부만으로 완료 처리하지 않으며
고정 체크섬과 `.native-corpus-state` 완료 마커를 함께 확인합니다. 자세한
운영 절차는 `docs/NATIVE_KOREAN_CORPUS_PIPELINE_KO.md`를 참고합니다.
전체 4-source 집계와 라이선스별 역할은
`docs/DATA_CARD_NATIVE_KOREAN_KO.md`에 기록합니다.

Linux CLI 전용 GPU 머신에서 AI Hub 데이터와 원어민 코퍼스를 다시 받는
절차, file key와 추가 수집 우선순위는
`docs/CLI_ONLY_GPU_DATA_SETUP_KO.md`에 정리되어 있습니다.
