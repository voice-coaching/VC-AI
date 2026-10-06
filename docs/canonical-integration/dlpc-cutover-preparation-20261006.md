# DLPC 전환 전 준비 — 2026-10-06

## 적용 상태

운영 구현·외부 연동 작업이다. AGENTS, `.codex/runpod.md`, `env.md`,
`backblaze.md`, `backend-http.md`를 적용했다. 아래 소스 등록은 실행 승인이나
개발자 품질 QA 통과를 의미하지 않는다. 운영 upstream은 RunPod를 유지한다.

| 항목 | 이번 작업 결과 | 적용 상태 |
|---|---|---|
| 재생성 시작 경로 | 영속 홈 Jupyter Python config → background recovery → B2 자산 → AWS 비밀 설정 → diagnostic bootstrap | 코드 준비, 설치 미수행 |
| 비밀 재주입 | AWS 전용 22132 리스너의 고정 명령, settings/B2 두 동작만 허용 | 자동 승인 검토 거절, 사용자 구체 승인 대기 |
| CUDA identity | AI harness·닫힌 schema 조합·offline verifier, Backend verifier 등록 | 소스 변경, 실행 allowlist 유지 |
| AWS verifier | 816개 소스/계약 파일의 불변 번들 설치, `--check-installation` READY | 비활성 |
| TTS identity | 실제 서버·보조 코드·encoder 식별값을 포함한 합성 fingerprint | 후보 생성 |
| Backend TTS env | revision/fingerprint 두 항목만 바꾼 root 전용 후보 파일 | 비활성 |
| CUDA/TTS QA | AGENTS의 개발자 직접 수행 규칙과 요청 범위 확인 질문 전달 | 실제 샘플 실행·품질 승인 미수행 |
| drain/upstream | 선행 조건 완료 후 수행 | 미수행 |

## 시작 hook과 비밀 설정 공급원

실제 PID 1의 entrypoint/startup은 사용자 `2020067`로 `/opt/python/bin/jupyter lab`을
시작한다. 해당 실행 파일의 `jupyter --paths --json` 결과에
`/home/2020067/.jupyter`가 포함되며 기존 `jupyter*config.py`는 없었다.
Jupyter는 이 경로의 `jupyter_server_config.py`를 읽는다.
[공식 설정 문서](https://jupyter-server.readthedocs.io/en/stable/users/configuration.html).

준비된 `deploy/dlpc/install_startup_hook.py`는 기존 config가 있으면 중단하고,
`~/.local/share/intelligentai/recovery/startup-20261006/`에 작은 복구 도구만 설치한다.
Jupyter를 재시작하지 않는다. hook은 background 프로세스를 실행하며 복구 작업은
`/run/intelligentai-recovery.lock`으로 중복을 막는다. 부분 설치·불명확한 복원 상태는
자동 덮어쓰기 대신 운영자 확인 상태로 남긴다. 상태는 root 전용
`/run/intelligentai-recovery-state.json`에 기록한다.

AWS 공급원은 이전 승인으로 보관한
`/root/.config/intelligentai/dlpc-recovery-20261006/`이다. 준비된 exporter는 임의 경로와
셸 명령을 받지 않고 stdin의 `settings` 또는 `b2`만 처리한다. 설정과 토큰은 SSH
stdout에서 DLPC 프로세스 메모리/stdin으로 전달하고 로그·Git·일반 archive에 넣지 않는다.
B2 Master Key는 AWS나 DLPC로 복사하지 않는다. 별도 `readFiles` 키를 버킷
`speakai2026`의 `intelligentai/recovery/dlpc-20261006/` prefix에만 제한해 AWS root 전용
`b2-reader.json`으로 보관하는 설계다. DLPC에는 단기 Native 인증 토큰만 제공한다.
v4 응답의 `storageApi.allowed` 범위를 검사한다.
[B2 인증 계약](https://www.backblaze.com/apidocs/b2-authorize-account).

실행을 시도한 리스너 변경은 **실행 전 자동 승인 검토에서 거절**됐다. 기존 forward-only
키의 `ForceCommand`/`MaxSessions` 변경 및 민감 설정 반환에 구체적 승인이 없다는 이유다.
현재 리스너, authorized_keys, Jupyter config, B2 키는 이 변경을 적용하지 않았다.
고정 exporter 허용·복구 읽기 키 생성/보관 승인을 사용자에게 요청했다.

복원 후 canonical 접수는 비활성이다. 현재 B2 archive는 이전 진단 배포본이므로, 이후
승인된 운영 release의 코드/attestation은 별도 복구 공급원으로 고정해야 한다. 이 hook을
설치하는 것만으로 재생성 후 운영 readiness까지 자동 복구됐다고 보고하지 않는다.
실제 컨테이너 재생성 검증도 수행하지 않았다.

## CUDA 등록 및 Backend 호환성

| 식별값 | SHA-256 |
|---|---|
| CUDA core | `4d29ddf6da45d43de6ceb0b07b70cecaf291ae5bc92b700ece6b320a8e6c423d` |
| CUDA H5 | `de8f66aa7688404f2f59fc17e67dd2c250cf746d58f9697bebd8fee87095f5f9` |
| H5 lock | `fc48fc935df8c4fb90c0a9345651c047a320da220fe46c12e6e00d395a6c556d` |
| AWS 비활성 verifier bundle | `56a51e940337b41e5a9731f7105289a3e495a434fa06f59ea7dc45750753d2a3` |

비활성 번들 위치는 `/opt/alpha-canonical/releases/<bundle SHA>/`다. 816개 파일,
10,775,216 bytes를 검증·설치하고 비특권 `ec2-user`로 설치 검사를 수행했다.
검사는 CUDA tuple을 포함한 `READY`를 반환했다. 입력 녹음이나 LLM을 실행하지 않았다.
활성 verifier `8178f478…`의 설정은 변경하지 않았다.

Backend develop은 실제 AWS의 Native v4 등록보다 뒤처져 있었다. 기존 v1/v2를 보존하고
현재 RunPod v4 및 DLPC CUDA 조합을 추가한다. AI·Backend의 v4/v5 결과 schema는 같은
바이트로 맞춘다. 서로 다른 release의 core/H5/lock을 섞는 조합은 허용하지 않는다.
AI `APPROVED_NATIVE_EXECUTIONS`는 변경하지 않아 QA 미확인 CUDA 실행을 막는다.

## TTS fingerprint와 전환 후보

DLPC의 기존 상위 `manifest.json`에는 이전 RunPod 서버 해시가 남아 있었다.
새 합성 manifest는 부모의 모델 snapshot·Melo commit·의존성 식별값을 유지하고 실제
DLPC 서버/보조 코드 해시, CPU thread 설정, ffmpeg/ffprobe 바이너리 해시·버전을 추가한다.
모델·환경 자산 무결성은 기존 B2 복원 검증에 근거하며 이번 확인에서 모든 모델을 다시
hash하거나 음성을 생성하지 않았다. fingerprint 직렬화는 UTF-8의
`json.dumps(ensure_ascii=True, sort_keys=True, separators=(',', ':'))`다.

- revision: `melo-ko-practice-dlpc-r1`
- fingerprint: `ace97bc52df31f304513bc5f18177669723fe996f5cd19235691a5449d9f3766`
- 실제 server SHA: `8f25077eab50503b914e530866225d8984899dac2dcbf2791c622d90c36ea83b`
- AWS 후보: `/root/.config/intelligentai/dlpc-tts-candidate-20261006/backend.env`
- 원본 env SHA: `25be4fd99b671e5897c82109cf8e90890be6e5517f58137559e647f364ae3e99`
- 후보 env SHA: `f1fc6548dc99f870e1dcd048bffd1e58307e045cb83e554db2abd2fc3e8f2ed3`

실제 Backend revision은 계속 `melo-ko-practice-r1`이다. 후보는 서비스 재시작이나 DB
revision reconcile을 수행하지 않았다. 현재 TTS의 resource budget 복사본은 이전
배포본이다. 이를 갱신하면 새 합성 manifest/fingerprint와 revision 정책을 재검토해야 한다.

## 전환 실행 순서와 현재 중단점

1. 구체 승인 후 고정 SSH exporter와 prefix 전용 B2 읽기 키를 설치한다. 설정값을
   출력하지 않고 DLPC 수신·권한·토큰 일치 여부를 확인한 뒤 Jupyter hook을 설치한다.
2. 개발자가 CUDA 결과/수치·피드백과 TTS 실제 음성을 검토한다. 허용된 샘플 실행 범위,
   대상 release, 결과 및 승인자를 확정한다. 모델 접근 readiness와 품질 승인은 별개다.
3. AI 실행 승인 및 attested 배포, Backend PR 검토/배포와 verifier 활성화를 맞춘다.
   결과 schema digest와 실행 identity를 양쪽에서 확인한다. 승인 release 복구본도 고정한다.
4. 새 요청 접수를 닫고 기존 analysis lease, direct spool, handoff, callback 및 TTS 작업을
   drain한다. AWS가 아직 받지 못한 DLPC scratch 결과의 유실을 ACK 완료로 간주하지 않는다.
5. 원본 설정 digest를 다시 확인한 뒤 Backend TTS revision/fingerprint와 nginx 후보를
   함께 적용한다. 접수를 닫은 채 readiness, 인증 거부, 배포 identity, callback 경로를 확인한다.
   실패 시 접수를 닫아 둔 채 기존 env/verifier/upstream으로 복귀한다.
6. 검증 후 접수를 열고 기존 RunPod에 잔여 작업이 없는지 확인한다. RunPod 중지는 그 이후다.

승인과 QA가 미확인인 동안은 4번 drain을 시작하지 않는다. 전환 준비 때문에 현재 운영
요청 처리를 먼저 중단하지 않는다. 자동 회귀 테스트·브라우저 QA는 수행하지 않았다.

확인 결과: 변경 Python 12개 소스의 컴파일, JSON 파싱, 양쪽 `git diff --check` 통과.
Backend는 기존 JDK 21과 Gradle 캐시로 `compileJava --offline --no-daemon -x test -x check`
컴파일을 수행해 성공했다. 기본 PATH에는 Java가 없어 확인된 JDK 경로를 명시했다.
v4 schema SHA는 `00e5b8104efc1539248246a3235622574228f1363930767160b62e248049d90c`,
v5는 `b21618ba31147539d0783a780241bd696545250d146d1fea7caa3b57c81ce977`로 양쪽이 같다.
