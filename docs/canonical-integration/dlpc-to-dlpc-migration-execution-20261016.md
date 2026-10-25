# DLPC J3990 → J406 이전 실행 기록 — 2026-10-16

이 문서는 DLPC 서버 이전의 실제 수행 범위와 관측을 기록한다. 당시 별도 계획 파일은 현재 저장소에 없다.
자동 회귀·브라우저 QA는 수행하지 않았다. 실제 녹음·GPT 생성·TTS 합성·피드백 품질 QA는 개발자 확인 대상이다.

## 대상과 차이

| 항목 | 원본 J3990 | 대상 J406 |
|---|---|---|
| hostname | `J3990.2020067.notebook.dlpc` | `J406.2020067.notebook.dlpc` |
| userland / glibc | Ubuntu 24.04 / 2.39 | Ubuntu 22.04 / 2.35 |
| host kernel | 5.14 (el9) | **3.10 (el7)** |
| GPU / driver | RTX A5000 24 GiB / 615 (CUDA 13) | 3× A100 80 GiB / 550 (CUDA 12.4) |
| 홈 UID/GID | 120067 / 1001 | 120067 / 1000 |
| AWS 역방향 포트 | 18022 / 18080 / 18083 | 28022 / 28080 / 28083 (전용 키, permitlisten 분리) |

## 대상 환경 재구성
- `envs/scorer`, `envs/canonical`: python-build-standalone 3.12.3 + uv 잠금으로 재구성. scorer torch cu126.
  OpenCV는 커널 3.17 ABI가 필요한 Qt 의존을 피해 `opencv-contrib-python-headless`.
- core env `mac_prod311`(CORE_PYTHON): torch 2.13.0+cu126으로 교체. pinned cuDNN 9.24는 cu12 빌드라 그대로 사용.
- TTS synthesis identity는 host ffmpeg/ffprobe 바이너리 해시를 포함하므로 host별 pin(J406 ffmpeg 4.4.2, `e9080fe0…`).

## 코드 변경 (이 PR)
- `deploy/dlpc/host_identity.py`: host별 명시 identity(UID/GID/home, supervisor bin, tools Python, AWS 키·포트, 복구 profile, runner 이름).
  기존 J3990 hostname 고정 검사는 삭제하지 않고 명시 identity 검증으로 대체했다. 알 수 없는 host는 fail-closed.
- `src/.../canonical_process_guard.py`: Linux < 5.3(pidfd 없음)에서는 `waitid(P_PID, WNOWAIT)`로 미회수 직계 자식임을
  증명한 뒤 SIGKILL. 단일 스레드 guard만 회수하므로 PID 재사용 경쟁이 없다. pidfd 경로는 그대로.
- `src/.../canonical_producer.py`: cuDNN preload보다 core env의 `libstdc++`를 먼저 preload. 시스템 libstdc++(GCC 12)가
  `CXXABI_1.3.15`를 제공하지 않아 ICU→sqlite3→nltk→g2pK import가 실패했다.
- 복구: J406 전용 B2 prefix `intelligentai/recovery/dlpc-j406-20261016/`(readFiles 전용 reader, AWS exporter `b2-j406`),
  스트리밍 복원(`restore_assets_j406.py`, 256 MiB range·프록시), `restore_host_compat.py`(B2 스냅숏의 이전 파일에만 적용),
  production-owner 표식이 있을 때만 production 기동(재시작 시 원본과 동시 admission 방지).
- 배포: controller는 이미 approved target blob과 같은 파일을 drift로 보지 않는다(그 외는 base blob 일치 필수).
  성공 receipt에 bundle을 기록하고 `deployed_release.py`가 recovery에서 고정 baseline 또는 그 receipt의 release만 허용한다.
  → main 배포 후 재시작해도 배포된 release로 복귀한다. workflow에 30분 주기 catch-up을 추가했다.
- `start_production.py`: Backend admission을 900초 대기(공개 경로 전환 타이밍), 중단 재시작 시 supervisord reread/update 항상 수행.

## 운영 전환
- 1차(10:28–10:46 UTC): 원본 drain·정지 후 J406 canonical guard가 커널 3.10에서 실패하여 롤백. 원본 복구 확인.
- 2차(11:28–11:31 UTC): 원본 운영 중 J406을 hold 상태로 `/readyz` 200까지 사전 예열 후 전환.
  drain 0, 최종 증분 70 파일 sha256 일치, AWS nginx 18080/18083 → 28080/28083, 공개 readiness 전 항목 PASS,
  Backend admission true. 공개 중단 약 2분. 원본은 정지·hold·복구 진입점 보관 상태로 보존.
- runner `intelligentai-dlpc-j406`(label `intelligentai-dlpc-deploy`) 등록. J3990 runner는 정지(등록 삭제는 관찰 후).

## 남은 일
- 개발자 QA(ffmpeg 4.4.2, torch cu126 재구성 env의 실제 품질).
- 관찰 기간 후 J3990 runner 등록·B2 prefix `dlpc-20261006` 정리, AWS 22133 임시 리스너 및 J3990의 J406 접속 키 제거.
- 컨테이너 재생성 시 runner 계정·credential 자동 복원은 미구현(J3990과 동일). B2 복구 스냅숏은 전환 시점 release이며
  이후 배포는 catch-up workflow로 다시 적용된다.
