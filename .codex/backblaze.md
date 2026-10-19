# B2 저장·수집·복원 안내

B2는 외부 원본·파생 자료와 복구 자산의 영구 보관 경계다. GPU 실행 디스크나 POSIX journal volume을 대신하지 않는다. 사용자 녹음 저장소는 별도 계약으로 확인한다.

## 역할별 진입점

| 작업 | 소스·기록 |
|---|---|
| 기반 자산 backup | [b2_backup.py](../deploy/dlpc/b2_backup.py), [10/6 백업 기록](../docs/canonical-integration/dlpc-b2-backup-20261006.md) |
| 기반 복원 | [b2_restore.py](../deploy/dlpc/b2_restore.py), [restore_assets.py](../deploy/dlpc/restore_assets.py) |
| J406 전용 backup/restore | [b2_backup_j406.py](../deploy/dlpc/b2_backup_j406.py), [restore_assets_j406.py](../deploy/dlpc/restore_assets_j406.py), [host_identity.py](../deploy/dlpc/host_identity.py) |
| 소스 overlay와 release 복원 | [restore_runtime_overlay.py](../deploy/dlpc/restore_runtime_overlay.py), [deployed_release.py](../deploy/dlpc/deployed_release.py), [10/16 이전 기록](../docs/canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md) |
| canonical 원본 보관 | [canonical_evidence_store.py](../src/voice_coach/backend_analysis/canonical_evidence_store.py), [현재 v6 인계·보관 경계](../docs/canonical-integration/v6-unification.md) |
| 연구 원자료의 이용·수집 | [data/manifests](../data/manifests), [데이터 모듈](../src/voice_coach/data), 필요한 `scripts/` downloader와 대상 manifest |

## 실행 전 확인

Native API와 S3 API를 구분하고 credential의 API 종류·bucket·prefix·capability·유효기간을 확인한다. 운영 reader는 필요한 범위 읽기 권한만 사용한다. Backend archive writer·녹음 reader·복구 reader를 서로 대체하지 않는다. 인증 응답은 통째로 출력하지 않는다.

대상 manifest의 file ID/version·바이트 수·digest·포함 범위와 설치 release를 먼저 확인한다. J3990과 J406의 CUDA·Python·host profile은 다르므로 이름이 비슷한 archive를 임의 선택하지 않는다. overlay만으로 모델·Python 환경·설정까지 복구된다고 보고하지 않는다. 비밀 설정은 별도 주입한다.

복원은 다운로드·압축 해제·설치에 필요한 여유 공간과 partial 상태를 확인한다. range 응답·크기·전체 SHA를 검증한 뒤 설치한다. 과거 완료 marker 대신 이번 작업의 terminal 상태와 실제 파일을 확인한다. 문서 정리 때문에 다운로드·업로드·목록 조회를 실행하지 않는다.

업로드 응답 유실은 실패 확정이 아니다. 저장 intent와 정확한 버전을 조회해 조정하고 임의 재PUT·추론 재실행으로 해결하지 않는다. canonical v5의 결과 commit과 B2 archive 완료는 다른 상태이며 direct 결과·이력 동기화도 별도다.

비용 조사는 현재 계정의 버전·unfinished large file/part·egress와 과금 조건을 필요한 요청에서 확인한다. 이 안내에 변동 가능한 가격을 고정하지 않는다. 과거 Pod 이름이 붙은 객체, 구 버전, 미완료 part를 자동 삭제하지 않는다. 삭제·수명 정책 변경은 별도 작업 범위로 판단한다.

원자료·gold 라벨·사용자 미디어를 Git·일반 archive·GPT 입력에 추가하지 않는다. [Git 제외 규칙](../.gitignore)과 각 데이터 이용 조건을 유지하고, 후속 기능에 쓸 작은 공개 카탈로그는 별도 검토한다. 완료 보고는 대상·정확한 버전·크기·hash·검증 범위와 미완료 복원을 구분한다.
