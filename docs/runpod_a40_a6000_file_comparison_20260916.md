# A40 검증 manifest와 현재 A6000 파일 대조

## 결과

2026-09-16 00:56:35 KST에 A6000 `urwnuesv7o4fv6`의 `/workspace/intelligentai` 전체를 읽어 기존 manifest와 비교했다. 719초 소요됐다.

**차이는 B2 보관을 확인하고 이번 세션에서 삭제한 과거 복구 archive 2개뿐이다. 나머지 기록은 일치한다.**

| 항목 | 기준 | 현재 A6000 |
|---|---:|---:|
| 전체 엔트리 | 63,011 | 63,009 |
| 일반 파일 | 56,682 | 56,680 |
| 파일 바이트 | 79,032,534,574 | 79,032,406,932 |
| 추가 엔트리 | — | 0 |
| 공통 경로의 내용·metadata 변경 | — | 0 |

누락 경로:

- `deployment/service-supervision-20260910-6618a39/current-overlay.tar.gz` (76,751 bytes)
- `deployment/service-supervision-20260910-6618a39/previous-overlay.tar.gz` (50,891 bytes)

합계 127,642 bytes로 [B2 중복 정리 기록](runpod_disk_cleanup_20260916.md)과 정확히 일치한다. 활성 overlay·모델·Python 환경·실행 자산의 차이는 발견되지 않았다.

## 기준과 한계

- 기준: `data/cache/runpod-migration-20260915/source-manifest.json`.
- SHA-256: `de82b8b6f6dc1195a8e2406050c8f03d938084d812839cb7cdc8ade4f8c56490`. 로컬 사본과 원격 비교 입력 모두 확인했다.
- 영수증: 같은 디렉터리의 `migration-verified.json`. 2026-09-15 10:13:14 UTC에 A6000 → A40 `s8hb5b0l5k4qlx` 검증 성공을 기록한다.
- 사용자 선택에 따라 A40는 기동하지 않았다. 따라서 **A40의 검증 당시 상태와 현재 A6000의 비교**이며, 검증 이후 A40 변경 여부와 현재 두 Pod 간 직접 일치는 확인하지 않았다.
- 비교 항목: 모든 일반 파일 SHA-256·크기, 엔트리 종류·mode·uid·gid, symlink target. 파일 해시를 읽는 동안 크기·mtime·inode 변경도 검사했다.
- 기존 manifest와 동일하게 `.ssh`, `.env`, `.env.local`, `analysis-http.env`, `analysis-worker.env`, `id_ed25519`, `id_rsa`, `.log` 접미사 파일을 제외했다. 루트 디렉터리 자체, ACL, hardlink 관계는 비교하지 않았다.
- `/opt`, `/run` 실행 사본 및 비공개 설정·GPU·서비스 readiness는 이번 파일 비교 범위가 아니다.
- 결과 JSON: `data/cache/runpod-migration-20260915/a6000-comparison-20260916.json` (Git 제외 관측 자료).

원격 비교용 manifest와 결과 JSON만 `/tmp`에 만들었다. 비교 대상 파일·Pod 설정은 변경하지 않았으며, 추론·자동 회귀 QA·commit·push·PR는 수행하지 않았다.
