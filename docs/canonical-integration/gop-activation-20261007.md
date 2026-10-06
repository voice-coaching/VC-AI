# GOP 하네스 활성화 기록

2026-10-07 사용자 요청: 병합된 GOP 하네스를 RunPod에서 활성화한다. 신규 생성/품질 QA가 아닌 운영 배포·readiness 확인 범위다.

배포 준비 중 AI main `60091d824edc28b55227527b79643182f995f370`의 병합 오류를 발견했다. `canonical_verify.py`에 정의/등록되지 않은 `DLPC_CUDA`, `dlpc_cuda` 참조가 남아 설치 확인이 NameError로 실패한다. frozen exporter에도 ROOTS에 없는 DLPC 디렉터리 조회가 남아 있다. 두 미정의 참조를 제거하고 등록된 Native/GOP/historical 계약 검증 및 pair 완전성 검사는 유지한다. DLPC 환경을 전환하거나 새 identity를 추가하지 않는다.

신규 GOP 패키지를 새 디렉터리에 설치해 모든 manifest 파일 hash를 확인했고, AWS 검증기는 digest별 새 release로 준비한다. 실행 중인 작업 drain, AWS와 RunPod 계약 일치, 배포 후 direct/handoff readiness를 확인한 뒤 접수를 재개한다. 운영 관측 결과는 배포 완료 후 이 문서에 추가한다.
