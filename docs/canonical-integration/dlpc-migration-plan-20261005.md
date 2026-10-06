# RunPod → DLPC 마이그레이션 계획 — B2 병용

## 2026-10-06 개정: 이번 이전의 실행 기준

### AWS 연결 재검토·실행 결과 (17:10–17:16 KST)

이 절은 아래 최초 개정 당시의 ‘계획만 수행’ 상태보다 최신이다. 사용자 후속 요청에 따라
구현·설치·운영 상태 확인을 진행했다. 기존 PR #25는 병합되어 `origin/main`의
`2abbbdc`에서 `feat/dlpc-aws-completion-20261006` 후속 브랜치를 만들었다.

1. **AWS 공개 경로:** Backend 분석 `RUNPOD_ENDPOINT_URL`과 TTS `EXAMPLE_TTS_BASEURL`은
   모두 `https://ai.voice-coaching.site`다. nginx는 아직 RunPod gateway 8082로 보낸다.
   전환 후보에서 일반 분석은 AWS loopback 18080, `/v1/tts/synthesize` 및
   `/health/live|ready`는 18083으로 분리한다. TLS와 전달 인증 헤더를 유지하고
   자동 upstream 재시도를 금지한다. 후보의 `nginx -t`는 성공했고 reload는 하지 않았다.
2. **양방향 인증:** AWS에서 DLPC 분석 liveness 200, 인증된 TTS readiness 200,
   무인증 분석/TTS 요청 각각 401을 확인했다. DLPC에서 AWS handoff readiness도
   기존 callback token으로 200이다. 분석 readiness는 503이며 접수는 여전히 닫혀 있다.
3. **TTS 전환 묶음:** Backend는 `melo-ko-practice-r1`, DLPC는
   `melo-ko-practice-dlpc-r1`이다. 주소만 교체하면 revision 불일치로 합성이 실패한다.
   개발자 QA 이후 새 합성 manifest의 fingerprint와 revision을 Backend 설정·캐시에
   함께 반영하고 Backend 담당자가 배포해야 한다. 기존 fingerprint를 새 revision에 복사하지 않는다.
4. **v5 journal 정정:** 실제 `canonical_executor.py`는 `BackendCanonicalJournal`을
   사용한다. `CanonicalEvidenceJournal`의 로컬 ext4/xfs 요건은 이 v5 운영 경로의
   필수 조건이 아니다. AWS에서 handoff worker/archive 및 delivery가 활성화돼 있다.
   DLPC의 transport scratch는 인계 전 재전송에 필요하며 root 재생성 시 사라진다.
   따라서 AWS가 수신 완료한 결과의 복구와 인계 전 미완료 실행의 실패 처리를 구분한다.
   미완료 producer의 불확실한 상태를 자동 재추론으로 해결하지 않는다. 로컬 영속 볼륨은
   인계 전 scratch까지 보존할 때의 개선안이다. DLPC 메모리·디스크만으로 무손실을 주장하지 않는다.
5. **비밀 복구:** 최초 전달은 자동 승인 검토가 명시적 승인 부족으로 거절했다.
   이후 사용자가 전용 보관을 명시적으로 승인해 AWS
   `/root/.config/intelligentai/dlpc-recovery-20261006/`에 분석/TTS 설정을 보관했다.
   디렉터리 root:root 0700, 두 파일 0600이며 값은 출력하지 않았다.
   복원 기본 접수는 비활성이다. DLPC 터널 키에는 이 디렉터리 접근 권한을 추가하지 않았다.
   현재 영속 홈에는 비밀 없는 복구 도구와 기존 SSH 키만 둔다.
6. **기동:** 플랫폼 entrypoint/startup을 읽었으나 영속 사용자 hook은 확인하지 못했다.
   root의 `/etc` supervisor 파일 추가만으로 재생성 복구를 보장하지 않는다.
   홈의 `~/.local/share/intelligentai/recovery/`에 고정 B2 복원·진단 bootstrap 도구를
   설치했다. 현재 설치에서 bootstrap 실행은 성공했지만 컨테이너 재생성 검증은 미수행이다.
7. **저장 공간:** root 디스크의 private `/var/lib/intelligentai/work`를 준비했다.
   DLPC 프로필에 여유 14GiB(운영 여유 8 + 초기 작업 여유 6)·가용 inode 10,000
   미만 또는 측정 실패 시 신규 접수를 막는 코드를 추가했다. 이는 admission 검사이며
   작업별 하드 quota나 캐시 LRU가 아니다. 활성 API 소스·동결 모델은 변경하지 않았으므로
   새 디스크 검사 코드는 아직 운영에 반영되지 않았다. 기존 workspace 설정은 자동 이동하지 않는다.

사용자가 추가 영속 로컬 볼륨 대신 B2를 선택했으므로 새 ext4/xfs 볼륨 연결을 요구하지 않는다.
현재 필요한 후속 순서는 AWS 비밀의 운영자 재주입 경로·플랫폼 hook 또는 수동 복구 절차 확정 →
CUDA 후보의 개발자 QA → AI/BE identity·schema/verifier 및 TTS fingerprint 등록 →
Backend 배포·전체 readiness → drain·nginx 전환 → 관찰 후 RunPod 중지다.
품질 QA는 `AGENTS.md`에 따라 개발자가 수행한다. 이전 준비 코드가 이 단계를 우회하지 않는다.

현재 디스크에는 자산이 수용되므로 분할 배포·LRU 구현은 이번 운영 전환의 필수 작업에서
제외한다. 현 복원은 빈 대상에 검증된 전체 archive를 설치하고 임시 압축본만 제거한다.
추후 업데이트가 실제 용량 예산을 넘으면 아래 분할 배포 설계를 적용한다.

현재 요청은 **계획 수립**이다. 아래 개정안을 이후 실행 기준으로 사용하고, 하단의 최초 계획은 역사적 판단 근거로 보존한다. 특히 최초 계획의 Seungun 재기동, NAS 대용량 백업, 분석 2건 동시 처리는 이번 전환의 필수 단계가 아니다. 운영 기준은 재확인한 **Native-only v4 → DLPC CUDA v2 후보**다.

적용 규칙: `AGENTS.md`, `.codex/runpod.md`, `.codex/backblaze.md`, `.codex/env.md`. 사용자 변경을 보존하며 자동 회귀 테스트·브라우저 QA·실제 추론 품질 QA를 수행하지 않는다. 이번에는 대상 조회와 문서만 변경했다. 아래 구현 항목은 완료된 기능과 구분한다.

### 1. 판단과 최신 근거

**현재 실행 자산은 DLPC의 50GiB 디스크에 들어간다. 로컬 실행 디스크 + B2 영속 복구본 구성을 채택한다.** 주요 과제는 현재 용량 증설보다 컨테이너 재생성 복구, 배포 중 최대 점유량, 실행 중 journal의 영속성이다. B2가 RAM·VRAM 또는 로컬 실행 파일시스템을 대체하는 것으로 설계하지 않는다.

2026-10-06 16:45–16:47 KST, RunPod API 및 RunPod→DLPC SSH와 B2 Native API에서 읽기 전용으로 확인했다.

| 대상 | 관측 | 계획에 반영할 점 |
|---|---|---|
| RunPod | `s8hb5b0l5k4qlx`, RUNNING, 관리 SSH `194.68.245.79:22132` | 아직 중지하지 않았으며 이전 중 관리 경로로만 활용 |
| 실제 분석 기준 | `canonical_native_core_20261005_v4`, `canonical_native_h5_20261005_v4`, worker `32d98e39767b56697bc7e58fb50e3e9d61432349` | 폐기된 모델을 다시 활성화하지 않음 |
| DLPC CPU/RAM | affinity 4개 `[28,34,36,38]`, RAM 11GiB, 가용 약 8.00GiB, swap 없음 | CPU 번호 고정 금지. cgroup 한도 파일 미노출이므로 관리자 할당 확인 필요 |
| DLPC GPU | RTX A5000, 총 24,564MiB, 당시 사용 1,021MiB | 분석 준비 전 관측이며 최대 추론 점유량이 아님 |
| DLPC root | 53,687,091,200 bytes = 50GiB, 사용 25.96GiB, 여유 **24.04GiB** | `/opt`, `/workspace`는 같은 디스크이므로 용량을 합산하지 않음 |
| 실행 경로별 점유 | `/opt/intelligentai` 6.61GiB, `/workspace/intelligentai` 11.68GiB, `/workspace/voice-coaching-tts` 6.64GiB | `du` 기준. hardlink·파일시스템 회계 차이 때문에 단순 합계를 root 점유와 동일시하지 않음 |
| NAS 홈 | NFS 공유 볼륨, 사용자 quota 도구 없음 | 공유 볼륨의 TB 단위 여유는 개인 quota가 아님. 과거 quota 실패를 고려해 대용량 백업 대상에서 제외 |
| 임시 경로 | `/tmp` 5GiB, `/var/tmp` 1GiB, tmpfs | 모델 다운로드·추출 공간으로 사용하지 않음. RAM 예산에도 영향 |
| DLPC 서비스 | analysis/TTS supervisor RUNNING, 분석 health 200·readyz 503 | 분석 접수 비활성. 전체 readiness·품질 통과를 의미하지 않음 |
| B2 복구 객체 | archive 16,062,581,191 bytes, manifest 42,449,956 bytes, 고정 file ID 조회 성공 | 이번에는 존재·메타데이터만 확인. 전체 hash 검증과 수동 복원은 앞선 실행 기록의 근거 |

AWS 직접 터널과 인증된 TTS ready 200은 **16:24 KST 관측**이다. 이번 조회로 AWS 경유 응답 또는 TTS 인증 readiness를 다시 확인한 것은 아니다. 근거·고정 file ID·SHA-256은 [실행 기록](dlpc-migration-execution-20261005.md)과 [B2 복구 명세](dlpc-b2-backup-20261006.md)에 유지한다.

### 2. 목표 연결과 저장 역할

```text
프론트엔드 → 기존 AWS HTTPS / Backend
                         ↓ AWS loopback 18080 / 18083
                    DLPC 분석 8080 / TTS 8083
                         ↑ DLPC가 AWS:22132로 여는 역방향 SSH

DLPC ← HTTPS / 고정 manifest와 객체 → Backblaze B2
DLPC → 기존 녹음 저장소·Backend callback·GPT API
```

RunPod는 최종 서비스 경로·복구 자산·비밀 공급원에서 모두 제거한다. 상시 관리용 새 터널은 추가하지 않고, DLPC 플랫폼 터미널에서 시작·복구할 수 있게 한다. 기존 AWS 터널 키는 shell/subsystem 접속이 금지되어 있으므로 비밀파일 다운로드용 SSH 키로 재사용할 수 없다.

| 자료 | DLPC | 영속 위치와 원칙 |
|---|---|---|
| 현재 승인 release·Python 환경·모델·MFA/G2P 사전·TTS | 실행에 필요한 전체 의존성 상주, 다운로드 후 오프라인 사용 | B2에 고정 버전 복구본. 요청마다 모델을 받지 않음 |
| 비활성 release·복구 archive·대형 연구자료 | 기본 미보관, 필요할 때 제한된 staging 사용 | 기존 B2 객체 재사용, file ID·hash로 선택 |
| 요청 중 WAV·영상·MFA 임시 DB | root 디스크의 제한된 private 작업 영역 | 완료·취소·회수 조건에 따라 정리. 진행 요청의 파일은 삭제 금지 |
| 녹음 원본·제품 결과 | 필요한 사본만 일시 보유 | 기존 사용자 저장소와 Backend 계약 유지. 이번 이전에서 B2로 임의 전환하지 않음 |
| handoff·재전송 journal | 실행 중 로컬 처리 | 별도 영속 처리 계약 확정 필요. B2의 주기적 백업만으로 무손실 복구를 보장하지 않음 |
| 로그·운영 영수증 | 크기 제한·회전, 비밀값 제외 | 필요한 비식별 운영 기록만 별도 보관. 사용자 원문이 섞인 로그의 일괄 업로드 금지 |
| 토큰·SSH 키·비밀 설정 | 역할별 private 파일/프로세스 | 플랫폼의 영속 비밀 주입 기능 또는 별도 승인된 공급원. 일반 B2 archive에 넣지 않음 |

B2를 FUSE로 실행 환경에 마운트하는 방식은 초기안에서 채택하지 않는다. 작은 파일·랜덤 읽기·실행 중 네트워크 의존을 늘리지 않도록 검증된 자산을 로컬에 내려받아 사용한다. 다운로드는 Native API의 인증 헤더와 정확한 file ID를 사용하고 SHA-256을 별도로 확인한다. [공식 다운로드 계약](https://www.backblaze.com/apidocs/b2-download-file-by-id)

### 3. 로컬 50GiB 예산과 용량 부족 대응

아래는 **구현할 운영 상한 제안**이며 현재 강제되는 설정이 아니다. 파일 크기뿐 아니라 파일시스템 잔여량·inode도 검사한다. 공용 작업 영역과 캐시가 같은 파일을 중복 계산하지 않도록 소유 역할을 정한다.

| 항목 | 초기 예산 |
|---|---:|
| 플랫폼 기본 사용량 + 실행 중인 release/모델/환경 | 28GiB |
| 분석·TTS 요청 작업 공간 합계 | 6GiB |
| 다시 받을 수 있는 자료 캐시 | 최대 4GiB |
| 로그·작은 상태 파일·영수증 | 1GiB |
| 부분 배포/download staging | 3GiB |
| 최소 여유 | 8GiB |
| 합계 | 50GiB |

- 현재 root 점유 약 25.96GiB는 첫 예산 안에 있다. 이 측정만으로 최대 입력·추론 중 임시 파일 점유까지 보장하지 않는다.
- 매 접수·다운로드 전에 `현재 여유 - 예약된 작업 증가분 - 새 작업 최대 증가분 >= 8GiB`를 확인한다. 10GiB 아래에서는 재사용 캐시 축소·로그 회전을 우선 수행하고, 예약 후 8GiB를 확보하지 못하면 새 접수/복원을 보류한다. 이미 수락한 요청은 보유 예약 안에서 완료하거나 계약된 실패로 정리한다.
- 캐시는 사용 중 pin/lease를 가진 파일을 제외한 LRU만 제거한다. 재시도 횟수·부분 다운로드 TTL·동시 전송 수를 제한하고 저장 실패를 숨기지 않는다.
- `launch.py`의 TMPDIR/HF_HOME, 분석 workspace 설정과 MFA/TTS의 실제 임시 경로를 함께 대조해 대형 파일을 root 디스크로 모은다. 작은 socket/PID/lock은 `/run`에 유지한다. 이름만 바꾼 env로 모든 경로가 바뀌었다고 판단하지 않는다.
- 활성 의존성 자체가 28GiB를 넘거나 6GiB 작업 예산이 최대 입력을 수용하지 못하면, 먼저 B2에 검증된 비활성 자산·중복 다운로드 캐시를 옮긴다. 그래도 부족하면 플랫폼 로컬 디스크 증설 또는 입력/동시성 조정을 별도 판단한다. 필요한 실행 모델을 누락시키거나 VRAM을 디스크 대용으로 쓰지 않는다.

**복구 시 최대 점유량은 운영 중 점유량과 따로 계산한다.** 기존 압축본 14.96GiB + 압축 해제 원본 약 24.67GiB + 도구의 여유 검사 5GiB는 약 44.63GiB다. 여기에는 기존 OS 사용량·파일시스템 오버헤드가 추가된다. 빈 root에서의 복원은 앞서 실제 성공했지만, 현재 25.96GiB 설치 위에 또 받으면 이 수치가 합쳐져 50GiB를 넘는다. 기존 복원 도구도 대상 경로가 존재하면 설치를 거절한다.

따라서 기존 단일 archive는 **빈 컨테이너 재해 복구용**으로 유지하고, 이후 업데이트에는 의존성 단위 분할 패키지를 추가한다. 패키지는 압축 크기·최대 해제 크기·개별 파일 hash·symlink/hardlink 관계를 manifest에 기록하고, 공유 자산을 중복 보관하지 않는다. 압축본과 해당 해제 공간이 staging 3GiB 안에 드는 단위로 받아 검증·설치한 뒤 임시 압축본을 제거한다. 단일 파일이 이 예산을 넘으면 배포 전 예산을 다시 계산한다.

분할이 총 활성+후보 용량을 없애지는 않는다. 전체 사본 두 개가 필요하면 증설하거나 drain 후 점검 시간을 확보해 교체한다. 실행 중 환경 위에 덮어쓰지 않는다. 현재 복원 도구에는 범용 분할·캐시 제거·중단 재개가 구현되어 있다고 가정하지 않는다.

### 4. RunPod 없이 복구되는 기동 체계

현재 홈의 터널 키/실행기는 남지만 `/opt`, `/workspace`, `/etc/intelligentai`, `/run`은 재생성 때 사라졌다. 홈 NFS도 개인 quota와 mount 옵션에 의존하므로 작은 bootstrap 자료 외의 보관을 당연시하지 않는다.

1. **플랫폼 시작 진입점 확정:** DLPC 관리자가 제공하는 시작 hook·권한·재생성 정책을 확인한다. 지원되는 hook에 작은 버전 고정 bootstrap을 연결한다. shell 로그인 파일이나 일회성 `nohup`을 자동 복구 완료로 취급하지 않는다.
2. **영속 비밀 공급원 확정:** 플랫폼 비밀 주입을 우선 검토한다. 지원되지 않으면 운영자 PC의 보호된 공급원에서 DLPC 플랫폼 터미널로 재주입하는 수동 복구 절차를 확정한다. 이 경우 무인 복구 미지원과 복구 시간 영향을 명시한다. RunPod의 `/run`은 최종 공급원이 될 수 없다.
3. **복원/기동 상태 관리:** manifest 조회 → 용량 예약 → 다운로드/hash 검사 → 비공개 staging → 계정/권한·의존성 확인 → 설치 → 설정 주입 → runtime 생성 → AWS 터널 → 분석 준비 → TTS 준비 순서를 단일 lock과 단계별 상태로 관리한다. 기존 건강한 설치는 재추출하지 않으며 부분 실패에서 완료 marker를 만들지 않는다.
4. **접수 조건:** liveness·모델/MFA 준비·승인 identity·GPT 접근·callback/저장소·TTS를 각각 확인한다. 복구 중에는 신규 분석을 받지 않고 구체적인 준비 실패 상태를 제공한다. 접수 설정 하나로 검증 실패를 덮지 않는다.
5. **실행 상태 영속성:** 운영 전에 journal·재전송 산출물의 크기/쓰기/원자성 요구를 확인한다. DLPC 영속 볼륨이 없으면 AWS/기존 Backend에 실행 소유와 완료 산출물을 내구성 있게 인계하는 adapter 계약을 설계한다. 완료 응답과 삭제 시점을 영속 ACK 뒤로 두고 재연결 시 execution ID로 대조한다. 현재 홈은 `soft,nocto` NFS 관측이 있어 로컬 SQLite/fsync 의미와 동일하다고 가정하지 않는다.
6. **재생성 후 처리:** 미완료 execution은 Backend 소유권·lease 만료·idempotency로 조정한다. 옛 journal을 복사해 다른 호스트가 동시에 재실행하지 않는다. B2에는 완결된 복구 snapshot을 보관하며 실시간 lease/lock 저장소로 사용하지 않는다.

B2 복원 전용 키는 필요한 버킷/prefix의 읽기 권한, 백업 업로더 키는 쓰기 용도로 분리하는 안을 적용한다. Master key를 DLPC의 상시 runtime 설정으로 배치하지 않는다. Native API를 유지하며 S3 adapter를 쓰려면 호환 application key가 별도로 필요하다. [공식 키 계약](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)

### 5. 4 CPU·24GB GPU 운영 원칙

- 초기 분석 활성 1건, MFA worker 1개, 분석/TTS 공통 GPU 실행 슬롯 1개를 유지한다. API·heartbeat·I/O와 계산을 분리하되 4개 CPU만큼 모델 프로세스를 복제하지 않는다.
- 기존 DLPC thread 제한 1과 CUDA FP32 후보를 사용한다. MFA/Native 준비·TTS 로딩 순서를 조절해 RAM 최고치를 제한하고, 가용 RAM 2GiB 하한과 실제 자식 프로세스 메모리 감시를 확인한다.
- 현재 VRAM 점유는 분석 모델의 전체 준비 상태가 아니다. 사용자 녹음의 최대 입력·warm/cold·TTS 병행에서 RAM/VRAM peak와 지연을 개발자가 확인한다. 2건 동시 처리·양자화·FP16은 마이그레이션 후 별도 최적화다.

### 6. 실행 순서와 완료 조건

| 단계 | 작업 | 종료 조건 / 현 상태 |
|---|---|---|
| 0 | 최신 자산·리비전·저장 공간·백업 조사 | 이번 조회 완료. GPU 최대 부하와 cgroup 실제 할당은 미확인 |
| 1 | 복구 가능한 B2 기준본·AWS 직접 터널·진단 기동 | 앞선 수동 작업 완료. 자동 기동을 뜻하지 않음 |
| 2 | bootstrap·비밀 공급원·영속 journal·용량 제한 구현 | RunPod가 없어도 복구·요청 상태 정합성이 유지되고 공간 초과를 접수 전에 차단 |
| 3 | AI/BE release 승인·접점 정리 | core/H5/schema hash·장치 근거·TTS revision/fingerprint 일치. RunPod proxy/직접 URL의 실제 활성 설정 확인 |
| 4 | 운영 준비와 개발자 QA | liveness, 전체 readiness, 실제 녹음·GPT·결과 저장·TTS·품질, 단절/재생성 복구를 각각 확인 |
| 5 | drain 후 제한 전환 | RunPod 신규 접수 닫기 → 기존 요청/lease/handoff 정리 → AWS upstream DLPC로 전환 → DLPC 접수. 같은 execution 이중 처리 없음 |
| 6 | 관찰·RunPod 중지 | 아래 완료 기준 충족 후 중지. Pod 삭제는 별도 결정 |

운영 관찰은 초기 **24시간을 제안**하되 시간 경과만으로 합격시키지 않는다. 대표 입력·최대 입력·TTS 병행·재시도·개발자의 재생성 복구 확인이 포함돼야 한다. DLPC 실행 자산이 준비된 상태에서의 B2 일시 장애는 추론을 막지 않게 하며, cold 복원 중 B2 장애는 접수를 닫고 재시도한다. 실제 RTO는 네트워크 속도·전체 검증·모델 준비 시간을 포함해 운영자가 측정하고 확정한다. 자산은 불변 manifest, 완료 요청은 영속 ACK를 기준으로 무손실 목표를 검증하고, 미완료 요청의 재시도/실패 계약을 별도로 둔다.

**전환 완료 기준:** RunPod 중계·비밀 공급 없이 재시작 가능, 승인된 전체 readiness와 개발자 품질 확인, 녹음/결과/재전송 보존, 디스크 예산 준수, 활성 요청 중복 없음. 필수 조건 실패 시 DLPC 접수를 닫는다. RunPod 중지 전에는 기존 승인 release로 신규 요청을 되돌릴 수 있고, 중지 후에는 DLPC의 B2 기반 복원이 기본 복구 경로다. RunPod 재시작은 GPU 확보를 보장하지 않으므로 최종 복구 전제로 삼지 않는다.

### 7. 후속 코드 변경 위치와 인계

- **AI `deploy/dlpc/`:** 기존 `b2_backup.py`, `b2_restore.py`, `restore_downloaded.py`의 공통 manifest 처리·분할/재개·용량 예약을 분리한다. `launch.py`, `prepare_restored_runtime.py`, `supervisord.conf`, `start_aws_tunnel.py`에 bootstrap·작업 경로·기동 순서를 연결한다. 현재 도구의 고정 20261006 경로/file ID는 기존 복원 계약으로 보존하고 새 버전 선택을 명시적으로 추가한다.
- **AI 서비스/adapter:** 실제 배포 revision의 접수 경로에 디스크 예약·정리 정책을 연결한다. B2 통신·journal 영속 전송은 전용 adapter로 두며 route/UI에 정책을 넣지 않는다. root checkout과 운영 frozen release를 혼동하지 않는다.
- **Backend:** verifier 승인 identity·TTS fingerprint·실행 소유권/영속 ACK와 필요한 연결 설정만 검토한다. 변경은 `develop` PR 대상이다. AI 변경은 `main` PR 대상이며 기존 후속 PR의 변경과 중복하지 않도록 병합 상태를 먼저 확인한다.
- **Frontend:** 기존 AWS 공개 계약을 유지한다. 실제 활성 코드가 RunPod 주소를 직접 사용하는 경우에만 연결 설정을 수정한다.
- **문서:** 이 계획, 실행 기록, B2 manifest, 배포 README를 함께 갱신한다. 범용 캐시·자동 복구·개발자 QA가 미완료인 상태를 구현 완료로 표시하지 않는다.

이번 계획 작업에서는 서비스 설정·코드·B2 객체·운영 upstream을 바꾸지 않았고 commit/push/PR/배포도 수행하지 않았다.

---

## 최초 계획 보존 — 2026-10-05 (아래 내용보다 위 개정안을 우선 적용)

작성일: 2026-10-05. 갱신: 2026-10-06. 상태: **재시작 후 B2 수동 복원·분석/TTS 기동·AWS 22132 직접 터널 확인 완료. 자동 복구·품질 QA·운영 전환 미완료.** 실제 관측과 남은 조건은 [마이그레이션 실행 기록](dlpc-migration-execution-20261005.md)을 따른다.

아래 Seungun/Scored v2 사양은 최초 계획의 역사적 기준이다. 실행 시 확인한 실제 기준은 **Native-only v4**이며 폐기된 Seungun을 다시 기동하지 않는다. CUDA v2 후보에서 Native/MFA와 TTS 준비를 확인했으며 [B2 자산 백업 검증](dlpc-b2-backup-20261006.md)을 완료했다. AWS 직접 SSH 터널과 B2 수동 복원·비밀 재주입·재기동도 완료했다. 플랫폼 자동 복구와 개발자 품질 QA·운영 전환은 남아 있다.

사용자 결정은 기존 Seungun의 단일 CPU 전제를 유지하면서 DLPC의 CPU 4개와 VRAM 24GB를 효율적으로 사용하는 것이다. 목표는 모델을 GPU에 한 번 적재하고 CPU 단계를 제한된 프로세스로 재사용하는 구조다. 초기에는 분석 1건, 검증 후에는 요청 간 단계 중첩으로 최대 2건을 처리한다. CPU 개수만큼 전체 파이프라인과 모델을 복제하지 않는다. 실제 최적 동시성은 개발자의 메모리·지연·처리량 측정으로 확정한다.

## 확인된 사양과 현재 구현

2026-10-05 21:48 KST 이후 RunPod에서 `ssh dlpc-migration`으로 조회했다. 후속 소스·토폴로지 조회도 같은 날 수행했다.

| 항목 | 관측 및 해석 |
|---|---|
| DLPC CPU | Xeon Silver 4210R. affinity 10,16,26,28의 논리 CPU 4개. 네 CPU는 서로 다른 물리 코어이며 두 소켓에 걸쳐 있다. 호스트 전체 40개를 사용 가능 자원으로 계산하지 않음 |
| DLPC 메모리 | `/proc/meminfo` 총 11GiB, 당시 가용 약 9.3GiB. swap 없음. 별도 cgroup `memory.max`, `cpu.max`는 노출되지 않아 한도·회계 범위의 관리자 확인 필요 |
| DLPC GPU | RTX A5000 24GB, `nvidia-smi` 총 24,564MiB, 가용 24,164MiB, 사용 1MiB. 실행 중인 CUDA 계산 프로세스 없음 |
| 환경 | Ubuntu 24.04.4 컨테이너, Python 3.12.3, Conda 있음. `docker`는 Podman remote 실행 파일. 명령 존재가 새 컨테이너·GPU 할당 권한을 뜻하지 않음 |
| 저장소 | root overlay 50GiB이나 현재 사용자는 `/`, `/opt`에 쓰기 불가. 홈은 쓰기 가능한 NFS이며 개인 quota·보존 정책 미확인. `/tmp` 약 5GiB, `/dev/shm` 약 7GiB이며 모두 용량을 추가 RAM으로 합산하지 않음 |
| 기존 점유 | Jupyter, MLflow, MinIO, Ollama 등이 실행 중. 소유자 승인 없이 종료하지 않음. 관측한 8080/8082는 미점유이나 배포 직전 재확인 필요 |
| 현재 연결 | RunPod의 SSH 별칭은 `127.0.0.1:23990`에 연결. 이 경유 SSH는 이전 중에만 사용하고 완료 후 유지하지 않음. 실제 분석·TTS 요청은 AWS와 DLPC 사이에서 전달 |

RunPod에서 확인한 운영 worker revision은 `32d98e39767b56697bc7e58fb50e3e9d61432349`, core는 `canonical_scored_core_20261003_v2`, H5는 `canonical_scored_h5_20261003_v3`다. 실제 실행기·동결 release를 기준선으로 포장하며 로컬 checkout 전체를 운영 소스로 간주하지 않는다.

- Native runner에 `torch.set_num_threads(8)`와 CPU FP32 모델/입력 처리가 명시돼 있다. 주변 OpenMP/MKL/OpenBLAS 환경의 2스레드 제한만으로 이 8스레드 설정이 없어지지는 않는다.
- Native 계약 검사에는 `device`가 `CPU float32`로 시작해야 한다는 조건이 있다. GPU 전환에는 코드뿐 아니라 새 파생 release와 장치·계산 근거 계약이 필요하다.
- Native/MFA `parallel=True`는 `PARALLEL_NATIVE_MFA_DISABLED_AFTER_PRESERVED_120S_SYSTEM_FAILURE`로 차단된다. 과거 장애를 해소했다는 검증 없이 이 flag를 켜지 않는다.
- 운영 MFA 설정은 `active_workers=1`, `pending_queue=1`, 요청 timeout 120초, worker당 요청 3회 후 교체다. 현 파이프라인은 요청별 `active/state`를 closure로 공유하므로 같은 객체를 여러 요청에서 동시에 호출하면 안 된다.
- 현재 Native·최종 발음 분석의 장치는 CPU이며 TTS는 CUDA다. TTS는 GPU를 사용하면서도 RAM RSS 약 2.04GiB를 사용한다. 보존된 분석 79의 MFA worker tree 최고 RSS는 약 784MiB였고 이는 전체 분석 메모리가 아니다.
- 최초 계획 작성자의 로컬 선택 기록 `testaudio1-timing-20261005.md`에서 전체 응답은 197.855초, 준비 두 구간은 134.451초, 실제 코어 분석은 30.300초였다. Native forward 1.080초만 가속해서 전체 준비 지연이 해결되지는 않는다. 관련 로컬 선택 기록은 `core-preparation-bottleneck-20261005.md`다. 두 파일은 이 공유 checkout에 포함되지 않으므로 필수 배포 근거나 현재 성능으로 사용하지 않는다.

## 목표 프로세스와 CPU 배분

다음 표는 **초기 실행 예산 제안**이다. 프로세스 개수를 정확히 4개로 제한한다는 뜻은 아니다. 서비스와 감시기의 가벼운 스레드를 포함해 동시에 CPU를 많이 사용하는 작업을 통제한다.

| 논리 역할 | 담당 | 계산 예산 |
|---|---|---|
| CPU A | 음성 decode·정규화, G2P, 요청별 후처리·근거 조립 | 무거운 CPU 작업 1개, 기본 계산 스레드 1 |
| CPU B | 단일 MFA worker와 그 순차 작업 | 기존 단일 CPU 전제 유지, 내부 job/thread 1, 정렬 1건 |
| CPU C | Native·Seungun 공용 GPU worker의 입력 준비·결과 회수 | CPU 계산 스레드 1, GPU 분석 실행 1건 |
| CPU D | API·lease/heartbeat·취소·handoff, TTS CPU 처리·인코딩 | API는 비동기 I/O, 무거운 TTS CPU 작업은 1개 |

CPU 번호는 시작 시 `sched_getaffinity()`에서 가져온다. 0~3이나 이번 관측 번호를 코드에 고정하지 않는다. 초기에는 OS 스케줄링과 작업 수 제한을 사용하고, affinity 고정은 GPU의 NUMA 위치와 실측 효과를 확인한 경우에만 적용한다. GPU 실행 대기 중의 CPU를 유휴로 묶지 않으며 백그라운드 notebook 서비스와의 경쟁도 관측한다.

모든 새 계산 worker는 라이브러리 import 전에 `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`을 적용하고 Torch 초기화 전에 intra-op/inter-op를 각각 1로 설정한다. Native의 명시적 8스레드 설정과 TTS의 기존 2스레드 설정도 대상별로 조정한다. ffmpeg, MFA/Kaldi, tokenizer 등 별도 thread/job 옵션은 실제 parser를 확인해 1개로 제한한다. 환경변수만으로 모든 라이브러리 제한이 적용됐다고 보고하지 않는다.

두 번째 CPU를 같은 Seungun 호출에 주는 방안은 후속 비교 후보다. 먼저 단일 CPU worker를 유지하고 독립 단계의 중첩에서 이득을 얻는다. [PyTorch multiprocessing 지침](https://github.com/pytorch/pytorch/blob/main/docs/source/notes/multiprocessing.md)은 다중 프로세스의 과도한 CPU 스레드 생성과 CUDA 초기화 이후 fork의 위험을 설명한다. CUDA worker는 spawn 또는 독립 실행으로 시작하며 각 자식 내부에서 CUDA를 초기화한다.

## GPU 상주와 요청 처리

### 모델을 보유하는 프로세스

- 분석 GPU worker 하나가 Native와 Seungun 모델을 각 한 사본씩 보유한다. 두 모델의 패키지·버전 동거가 불가능하면 격리 프로세스 두 개를 사용하되 모델 복제 없이 공통 GPU 실행 슬롯을 공유한다. 같은 의존성 환경으로 억지로 합치지 않는다.
- TTS는 기존 별도 서비스와 모델 child를 유지한다. 상태·장애·오디오 계약을 분석과 분리한다.
- GPU 분석과 TTS의 동시 커널 실행은 초기에는 합계 1개로 제한한다. 프로세스마다 독립 lock을 두지 않고 공통 coordinator에 실행 슬롯을 요청한다. 두 모델의 동시 실행은 메모리와 TTS/분석 지연 검증 후 확대한다.
- 모델은 시작 시 순서대로 로딩해 CPU staging 메모리 최고치를 제한한다. CPU state dict·불필요한 사본은 실제 참조를 해제하고 RSS/PSS 감소를 확인한다. MFA 준비와 겹치는 로딩은 cold peak가 메모리 예산 안에 들어온 뒤 적용한다.
- 처음에는 FP32와 기존 전처리·모델·임계값을 유지한다. FP16/BF16/양자화·TF32 허용은 별도 품질 변경으로 다룬다. GPU FP32도 CPU와 비트 단위 동일성을 보장하지 않으므로 경계 음소 판정과 최종 점수를 비교해야 한다.
- 상주 모델과 G2P 엔진은 재사용하지만 요청별 waveform·대본·관측 상태·출력 경로는 분리한다. GPU tensor를 요청 간 공유 메모리에 보관하지 않는다. 짧은 RPC에는 실행 ID, 검증된 파일 참조·digest·deadline과 작은 결과를 사용한다.

### 초기 단계는 전체 분석 1건

기존 요청 안의 순서인 준비 → Native → G2P/MFA 및 후속 분석 → 근거 조립을 유지한다. 실제 의존성이 있는 Seungun·MFA 출력 결합을 앞당기지 않는다. 기존 API 접수 상한 3은 DLPC용 검증된 설정으로 1까지 낮출 수 있게 한다. 환경변수 이름만 추가하고 parser와 runtime이 여전히 3을 쓰는 상태를 남기지 않는다.

### 검증 후 전체 활성 요청 2건

다음 요청의 Native/GPU 처리를 앞 요청의 MFA/CPU 처리와 겹치게 한다. **각 요청 내부의 단계 순서는 유지**한다. 이를 위해 현재 공유 `active/state`를 요청 context로 분리하고 GPU adapter·MFA adapter를 통해 자원을 예약한다. 동결 batch 객체에 스레드 두 개를 붙이는 방식은 사용하지 않는다.

| 자원 | 목표 상한 |
|---|---:|
| 활성 분석 전체 | 2건, 검증 전에는 1건 |
| MFA 실제 실행 | 1건 |
| MFA 대기 | 최대 1건, 이미 접수한 두 분석 안에서만 존재 |
| 분석 GPU 실행 | 1건 |
| TTS 합성 실행 | 1건 |
| 분석과 TTS의 GPU 실행 합계 | 초기 1건, 검증 후에만 2건 검토 |

예를 들어 A의 MFA가 실행되는 동안 B의 Native를 처리하고, A가 외부 GPT 응답을 기다리는 동안 B의 코어를 처리한다. GPT는 외부 API이므로 CPU 또는 GPU 슬롯을 점유한 채 기다리지 않는다. GPU 결과를 CPU의 최소 근거·파일로 확정한 뒤 GPU 실행 슬롯을 반환한다. 최종 handoff가 완료될 때까지 전체 활성 분석 수에는 포함한다. 이 구조의 처리량은 가장 느린 단일 MFA 단계와 원격 대기에도 제한되며 4배 개선을 가정하지 않는다.

대기열은 bounded이며 기존 capacity 오류·claim·lease·deadline을 유지한다. 대기 시간도 deadline에 포함하고 남은 시간이 부족하거나 자원 부족이면 접수 전에 거절한다. TTS 요청이 분석에 무한히 밀리지 않도록 대기 시간과 deadline을 고려해 교차 배정한다. 만료·취소 요청은 모든 대기열에서 제거한다.

## 메모리와 worker 수명

아래 수치는 **DLPC 11GiB 환경의 초기 운영 예산 제안이며 실측 요구량이 아니다**.

- RAM: 기존 notebook 서비스까지 포함한 사용량 목표를 8.5GiB 이하로 두고 약 2GiB 이상 여유를 확보한다. 새 서비스들의 동시 최고 증가분은 기존 점유를 뺀 범위에서 결정한다. 시작·모델 로딩·최대 길이 음성 처리 모두 이 예산을 적용한다.
- `MemAvailable`이 2GiB 미만이면 신규 분석과 새 TTS 합성을 보류한다. 기존 요청은 deadline·개별 worker 메모리 감시 아래 종료·인계를 진행한다. 표시 meminfo가 실제 할당량과 일치하는지 관리자에게 확인하고, 확인된 cgroup 한도가 있으면 둘을 함께 사용한다.
- GPU: 전체 점유 목표를 20GiB 이하로 두고 드라이버·일시 workspace·다른 사용자 작업을 위한 여유를 둔다. 실제 모델 적재량과 최대 입력의 peak를 측정하기 전 남은 VRAM만 보고 동시 실행을 확대하지 않는다.
- `/dev/shm` 7GiB와 `/tmp` 5GiB를 추가 RAM으로 취급하지 않는다. 대용량 PCM/tensor 큐와 pinned memory를 제한한다. swap 증설을 처리 용량의 근거로 삼지 않는다.
- MFA의 3회 recycle과 기존 resident의 3회 제한을 초기에는 보존한다. worker 상태 격리·RSS 증가 추세가 확인된 뒤 길이를 늘리는 별도 revision을 만든다. 두 세대를 동시에 올리는 교체 방식은 11GiB의 cold peak 예산을 먼저 통과해야 한다.
- GPU worker 종료·CUDA 오류가 발생하면 해당 세대를 unhealthy로 표시하고 신규 접수를 닫는다. 실행 종료 확인 없이 슬롯을 반환하지 않는다. GPU 작업을 안전하게 취소할 수 없으면 완료 후 결과를 폐기하거나 세대를 정리하고, 다른 대기 요청에는 명시적 실패를 전달한다. 조용한 CPU fallback·자동 재채점은 하지 않는다.

## 자산과 배포 계약

1. 현재 실행 revision·모델 SHA·Python/패키지·MFA/G2P 사전·ffmpeg·core/H5/verifier manifest를 고정하고 의존 파일 closure를 목록화한다. 모델 환경뿐 아니라 hard-coded validation 경로와 사전·정책 파일도 포함한다.
2. GPU 장치 및 단일 thread 변경은 별도 불변 core/H5 파생 release로 만든다. CPU 장치 assertion을 단순 삭제하지 않고 CUDA 장치·dtype·런타임 identity를 기록한다. Backend의 승인 identity와 verifier 패키지도 새 release에 맞춰 준비한다. 기존 receipt를 새 SHA로 다시 쓰지 않는다.
3. 홈 NFS에는 검증된 배포 원본·복구 자료를 보존하고, 실행 환경·자주 읽는 모델은 관리자 승인된 쓰기 가능한 로컬 디스크에 staging하는 안을 우선 검토한다. root 50GiB의 writable bind mount 또는 별도 quota 확보가 필요하다. `/tmp` 5GiB에 전체 모델·환경을 설치하지 않는다. 로컬 staging이 불가하면 NFS cold 시작·fsync·rename·quota 조건을 측정해 대안을 판단한다.
4. 임시 WAV·MFA DB·Unix socket은 짧은 로컬 작업 경로에 둔다. handoff journal과 재전송 원본은 컨테이너 재시작 보존이 확인된 private 저장소에 유지한다. 현재 UID로 0700/0600을 적용하고 기존 `/root`, UID 10001, `/workspace`, `/opt` 가정을 실제 경로/권한으로 정리한다. 키는 모델 archive에 넣지 않는다.
5. 새 사용자 서비스 supervisor를 사용해 logout 이후 유지·재기동·로그 회전·정리를 제공한다. pidfd, subreaper, child 회수, `/proc` 접근은 실제 계정에서 확인한다. Python에 `pidfd_open` 심볼이 있다는 것만으로 전체 guard 지원을 판정하지 않는다.
6. UI·공개 분석 DTO·점수 산식·B2 보관 책임·외부 GPT 모델을 유지한다. GPU 전환으로 수치 판정이 달라지면 원인을 해결하거나 별도 정책 결정을 받는다. 마이그레이션에 미지원 코스/영상/시험 기능을 추가하지 않는다.

## 이전 접속과 실제 서비스 연결

사용자 결정에 따라 `ssh dlpc-migration` 경유 접속은 이전 중에만 사용한다. RunPod를 접속 중계기로 계속 유지하거나 별도의 상시 관리 접속을 구축하지 않는다. 다만 AWS에서 DLPC의 분석·TTS API를 호출하는 실제 서비스 연결은 이전 대상에 포함된다. 기존 `ai.voice-coaching.site` HTTPS 도메인을 유지하고 내부 upstream을 변경한다.

- DLPC에서 AWS로 연결하는 역방향 SSH 또는 조직이 허용하는 private VPN 중 접근 정책에 맞는 경로를 구축한다. 초기 제안은 AWS loopback에만 연결되는 별도 분석/TTS 터널이며 SSH 키에는 필요한 포워딩만 허용한다.
- 외부 HTTPS 도메인은 AWS에서 계속 종료하고 내부 upstream만 DLPC로 전환한다. RunPod proxy URL이 코드·환경에 직접 남아 있는 분석, TTS, 준비 상태 조회, FE 직접 접수 계획의 실제 활성 여부를 대조한다. DLPC로의 새 익명 공개 포트를 만들지 않는다.
- 분석·TTS 서버 인증, callback HTTPS, 녹음 읽기, GPT API 연결을 각각 유지한다. SSH 연결 성공만으로 전체 서비스 연결 성공이라고 판단하지 않는다.
- 세션별 실행 소유자를 고정하고 전환 중 기존 RunPod 요청은 RunPod에서 마친다. 같은 request/execution을 두 호스트에 동시에 보내지 않는다. deadline·callback·journal이 정리된 뒤 신규 접수를 DLPC로 옮긴다.

## 단계별 실행과 완료 조건

| 순서 | 작업 | 완료 조건 |
|---|---|---|
| 0 | DLPC 할당·서비스 수명·디스크 권한·NAS quota·AWS API 연결 확정 | AWS의 분석·TTS 요청이 DLPC에 도달하고 실행/영속 저장 공간과 자원 한도가 확인됨 |
| 1 | 자산·의존성·기준 요청/결과 보존, DLPC 파생 release 설계 | 모델/근거 hash·버전·원복 release 목록이 완전함 |
| 2 | 단일 CPU thread와 단일 활성 요청의 CPU 호환 경로 준비 | 기존 근거·점수·상태 처리 의미가 유지되고 guard/메모리 문제가 없음 |
| 3 | 상주 Native/Seungun GPU worker·TTS·MFA, 시작 로딩 예산 적용 | GPU 장치가 실제 사용되고 CPU 기준과 음소 판정/점수/출처 호환성이 개발자 검증됨 |
| 4 | 요청 context 분리 및 분석 2건 단계 중첩 | 1건 대비 완료 처리량 증가, p95 지연/실패율 악화 없음, RAM/VRAM 예산 유지 |
| 5 | DLPC 신규 요청으로 제한 전환 | 분석 저장·TTS 실제 생성·오디오 품질·이력·취소/재시도·재시작 복구가 각각 확인됨 |
| 6 | 확대와 RunPod 정리 | 진행 중 RunPod 작업·미인계 journal이 없고 사용자 승인 범위에서 중지. 삭제는 별도 결정 |

단계 2는 호환성 확인용 기준 경로이며 최종 CPU-only 배포 결정을 뜻하지 않는다. 2건 단계 중첩이 메모리나 지연 기준을 통과하지 못하면 상주 GPU 모델을 유지한 1건 구성으로 인계한다. 이 계획만으로 3~4건 동시 실행을 허용하지 않는다.

검증은 개발자가 수행한다. 비교는 동일 입력·최대 허용 길이·모델 warm/cold·TTS 병행·반복 요청에서 CPU 시간/사용률, 전체 RAM/PSS와 cold peak, GPU 사용량/peak, 대기·단계별 p50/p95, 완료 건수, 오류와 timeout을 기록한다. 짧은 요청 한 번을 throughput으로 일반화하지 않는다. score와 음소 index/시간 구간·원본 digest·request/execution 연결, 경계 점수 사례, 생성/저장 분리도 확인한다. 자동 회귀·브라우저 도구·새 테스트 산출물을 이번 계획 작업에서 실행하거나 생성하지 않는다.

서비스가 준비되었다는 판단은 API liveness, 모델·MFA·GPU 준비, Backend claim/callback, GPT 실제 생성, TTS 합성, 결과 품질로 나눠 인계한다. 시간 목표는 기존 197.855초 관측만으로 새 초 수를 약속하지 않고 준비 반복 제거와 요청 간 중첩 효과를 계측해 정한다.

## 변경 위치와 원복

- AI: `canonical_producer.py`의 thread 환경, `http_runtime.py`/`canonical_lane.py`의 검증된 접수 상한·자원 예약, `canonical_resident.py`와 실제 파생 release builder/host의 GPU 모델 소유·요청 상태 격리·재기동 정책. 현재 CPU 8스레드를 유지하는 옛 resident builder를 그대로 배포하지 않는다.
- AI frozen core: `source/scripts/run_canonical_batch.py`, MFA adapter/설정 및 장치·model manifest. 운영 release를 현장에서 덮어쓰지 않고 새 release를 생성한다.
- gateway/TTS: `deploy/runpod/api-gateway/supervisor.py`와 DLPC용 서비스 실행 구성에서 CPU budget·모델 장치·공통 GPU 예약·private 경로 반영.
- Backend: 새 producer identity/verifier 승인, 분석/TTS upstream과 readiness/capacity 설정을 확인하고 필요 변경만 `develop` PR로 인계한다. 변경한 AI 코드는 `main` PR 대상으로 인계한다. FE는 실제 활성 endpoint 계약 변경이 있는 경우에만 수정한다.
- 실패 시 DLPC 신규 접수를 닫고 해당 호스트의 진행 요청을 drain하거나 명시적으로 종료한다. 기존 RunPod release로 신규 요청을 돌리며 승인된 실행 소유·idempotency 규칙을 유지한다. 저장된 결과·녹음·B2 객체와 복구 journal을 삭제하지 않는다.

2026-10-05 실행 조사에서 운영 배포가 Native-only v4로 변경된 사실을 추가 확인했다. 위 Seungun/Scored v2 내용은 최초 계획 당시 관측이며, 실제 이전 기준은 현재 Native v4다. 중단된 옛 모델을 다시 활성화하지 않는다. 코드·복사·운영 전환의 현재 상태는 실행 기록으로 구분한다.
