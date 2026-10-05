# DLPC 실행 자산 B2 백업 — 2026-10-06

상태: **B2 백업·전체 재다운로드 무결성 검증 완료. 복원 기동·운영 전환은 별도 미완료.**

## 결정과 범위

사용자가 NAS 대신 Backblaze B2에 백업하도록 지시했다. Native API로 기존
`speakai2026` 버킷(`e5cc71977ef1880ca60b0c17`)이 `allPrivate`임을 확인했다.
기존 `intelligentai/recovery/`의 현재 객체 62개를 모두 조회했으며 이번 DLPC
설치본에 해당하는 복구 객체는 없었다. 기존 객체는 변경하거나 삭제하지 않는다.

- 대상: `J3990.2020067.notebook.dlpc`에 실제 설치된 자산.
- 원본: 약 26.5GB, 파일·디렉터리·링크 141,602개. 원래 38개 자산 경로에
  DLPC CPU/CUDA v2 core/H5와 비밀 없는 설치·무결성·기동 영수증을 추가한다.
- 포함: 분석/TTS 코드, Python 환경, 모델·공개 CA, 동결 release, 실행 설정 template.
- 제외: `/etc`, `/run`, 홈 디렉터리, SSH 개인키, API/B2 키, 토큰, env 파일,
  운영 journal과 사용자 녹음 저장소. OS 이미지·GPU driver는 포함하지 않는다.
- 위치: `b2://speakai2026/intelligentai/recovery/dlpc-20261006/`.

## 전송과 검증

`deploy/dlpc/b2_backup.py`는 설치 영수증과 허용 경로를 확인한 후 명시적인
파일 목록으로 tar/gzip 스트림을 생성한다. NAS나 로컬 디스크에 전체 압축본을
만들지 않는다. 64MiB part, 동시 전송 최대 2개를 사용하며 각 응답의 file ID,
part 번호·크기·SHA-1을 대조한다. 압축 프로세스는 단일 gzip과 낮은 CPU 우선순위다.

Master key는 PC의 일시적인 인증 요청에서만 사용했다. 발급받은 단기 account
token만 SSH stdin으로 백업 프로세스에 전달했다. 토큰을 DLPC 프로세스 argv에
넣지 않으며 키·토큰은 로그, 문서, Git, archive 또는 설정 파일에 저장하지 않는다.

완료 조건은 다음과 같다.

1. tar 정상 종료와 B2 large-file finish 응답의 이름·크기 대조.
2. 정확한 file ID로 전체 객체 재다운로드, 원본 스트림의 SHA-256·바이트 수 대조.
3. 다운로드와 함께 gzip/tar 전체 구조 확인.
4. 파일 목록·권한·symlink 대상, archive file ID·크기·hash·part 영수증을
   `source-manifest.json`으로 업로드하고 manifest도 재다운로드해 일치 확인.

백업 상태는 DLPC의 root 전용 `/run/intelligentai-b2-backup-20261006/state.json`에
기록한다. 이 로컬 경로는 영속 저장소가 아니다. 최종 복구 근거는 B2 객체와
이 문서에 인계하는 file ID/hash다. 업로드 이후 검증만 실패한 경우 새 단기 인증을
stdin으로 주입해 `--verify-only`로 전체 다운로드 검증을 다시 수행할 수 있다.
진행 중 작업에 중복 실행하지 않는다.

첫 시도는 업로드 URL 검사가 공식 `*.backblaze.com` 주소를 누락해 실패했다.
미완료 large file을 취소했고 실패 기록은 별도로 보존했다. B2가 인증된 API로
반환한 HTTPS `*.backblazeb2.com` 및 `*.backblaze.com` 주소만 허용하도록 수정했다.
TLS 검증은 유지한다.

## 복구 경계

이 백업은 설치된 실행 자산 사본이다. 원래 source-copy의 staging 영수증만으로
`install_staged.py`를 그대로 재실행하는 형식이 아니다. 복구 시에는 다음이 필요하다.

- 먼저 아래에 기록된 정확한 manifest/archive file ID로 받아 크기·SHA-256을 확인한다.
- 동일 계열의 Ubuntu 24.04 DLPC 플랫폼과 호환 NVIDIA driver, 충분한 빈 디스크를 준비한다.
- 기존 서비스가 없는 대상에서 archive 목록을 검토하고 역사적 절대 경로와
  root/서비스 UID·GID 10001 소유·권한을 복원한다. 실행 중인 환경 위에 덮어쓰지 않는다.
- `/run`의 private 작업 디렉터리·GPU lock을 다시 만들고 분석/TTS 설정과 SSH 연결을
  별도의 안전한 공급원에서 재주입한다. 백업에는 해당 비밀값이 없다.
- 플랫폼 기동 hook, 검토된 AI/Backend release identity, TTS revision/fingerprint를
  맞춘 뒤 liveness와 전체 readiness를 각각 확인한다.

전체 다운로드·압축 구조 검증은 복원 후 서비스 기동이나 음성·점수·피드백 품질
QA와 다르다. 실제 복원 기동은 아직 수행하지 않았다. 분석 접수, AWS upstream,
RunPod 실행 상태는 이 백업 작업으로 변경하지 않는다.

## 실제 영수증

완료 시각: **2026-10-06 01:29:45 KST** (2026-10-05 16:29:45 UTC).
DLPC 상태는 `verified`이며 백업 worker PID 85948의 종료를 확인했다.

| 항목 | runtime-assets.tar.gz | source-manifest.json |
|---|---|---|
| 크기 | 16,062,581,191 bytes (16.06GB / 14.96GiB) | 42,449,956 bytes |
| SHA-256 | `b30e4d258ce42f4d40188d4a295784748fcba94c31dad78e5cc51f24334c7eed` | `f806086d4196c1df6ff2f63e0ea3b8565197a0a390139aca0beacc8a20920bda` |
| B2 file ID | `4_ze5cc71977ef1880ca60b0c17_f20016754e80dd30c_d20261005_m154312_c005_v0501036_t0023_u01791214992290` | `4_ze5cc71977ef1880ca60b0c17_f116916fb8a29905c_d20261005_m162935_c005_v0501045_t0059_u01791217775463` |

두 객체는 `intelligentai/recovery/dlpc-20261006/` 아래에 있다. 원본 파일 크기 합은
26,489,472,405 bytes이며 파일·디렉터리·링크 목록은 141,602개다. archive는 240개
part로 업로드했다. 전체 재다운로드 16,062,581,191 bytes의 SHA-256이 원본 스트림과
일치했고 gzip/tar 구조 검사도 정상 종료했다. manifest도 업로드 후 재다운로드해
바이트 일치를 확인했다. 버킷은 비공개이며 조회 당시 lifecycleRules는 빈 목록이었다.

이로써 NAS quota 증설 없이 실행 자산의 영속 백업을 확보했다. 비밀 설정 재주입,
플랫폼 재시작 hook, 실제 복원 기동 확인과 개발자 품질 QA는 별도 완료 조건이다.

## 확인한 외부 계약

- [B2 large-file 시작](https://www.backblaze.com/apidocs/b2-start-large-file)
- [part 업로드 URL](https://www.backblaze.com/apidocs/b2-get-upload-part-url)
- [part 업로드](https://www.backblaze.com/apidocs/b2-upload-part)
- [large-file 완료](https://www.backblaze.com/apidocs/b2-finish-large-file)
- [file ID 다운로드](https://www.backblaze.com/apidocs/b2-download-file-by-id)

마이그레이션 전체 상태는 [실행 기록](dlpc-migration-execution-20261005.md)을 따른다.
