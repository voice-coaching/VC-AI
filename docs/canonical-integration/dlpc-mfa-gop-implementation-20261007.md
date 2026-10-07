# DLPC MFA/GOP 최적화 구현 기록

2026-10-07. 작업 유형: 서비스 로직·성능 구현 및 운영 준비.

**상태: P0 계측 및 P1 후보 구현·패키징 완료. DLPC 설치·실측·운영 전환 미수행.**

적용 규칙: `.codex/ai_rule_developer/GLOBAL_RULES.md`, `ARCHITECTURE_RULES.md`, `SERVICE_LAYER_RULES.md`, `AGENTS.md`, `.codex/runpod.md`, `.codex/backblaze.md`.
사용자 원본 checkout의 기존 변경은 보존하고 별도 AI worktree에서 구현했다.

## 구현 경계

| 파일 | 구현 내용 |
| --- | --- |
| `deploy/dlpc/performance/gop_cpu.py` | float64 CPU CTC, blank=42, 기존 mapping/분모/후보 순서 유지. reference likelihood 재사용, 음소 간 제한된 microbatch, microbatch 안의 같은 transcript 계산 재사용 |
| `deploy/dlpc/performance/gop_task.py` | Native와 전체 기대 음소가 준비된 요청에서 작업 스레드 1개로 GOP 시작. MFA와 중첩하고 성공·예외 모두 join 후 재사용/반환 |
| `deploy/dlpc/performance/analysis_budget.py` | direct·Backend resident 사이 공통 코어 flock 1개. monotonic deadline을 대기에 포함. GPT/API 대기 중에는 점유하지 않음 |
| `deploy/dlpc/performance/mfa_timing.py` | 요청 준비, DB/corpus/사전/features/graph/alignment/export/cleanup 계측. 호출되지 않거나 없는 메서드를 추측하지 않음 |
| `deploy/dlpc/performance/collect_resources.py` | 운영자 실행용 읽기 전용 2초 CPU interval, affinity, PID 역할·PSS, 노출된 cgroup 제한, GPU 및 기존 worker timing 수집. 환경변수·명령행 전체·녹음·대본 미출력 |
| `deploy/dlpc/build_performance_release.py` | 현재 core/H5/lock을 hash로 검증하고 새 불변 패키지 생성. Python 컴파일 수행. prompt 보존. 등록·실행·배포는 수행하지 않음 |
| `deploy/dlpc/prepare_restored_runtime.py` | root 준비 단계에서 공통 analysis.lock 생성. 기존 lock inode를 교체하지 않아 활성 flock 유지 |

기본 후보는 Torch intra-op 2/inter-op 1, MFA job/BLAS 1이다. Torch 설정은 resident 시작 시 한 번 적용한다. 빌더는 비교용 immutable 후보의 thread 1/2/3/4, batch 32/64/128을 지정할 수 있다. 이 옵션은 측정 명령이 아니며 비교 실행을 자동화하지 않는다.

batch 상한 64에서 frames·target 길이를 이용한 임시 tensor/workspace 추정치 64 MiB에 맞춰 더 작게 제한한다. 추정식에 2배 여유를 사용하지만 allocator·Python 메모리·기존 모델 RSS를 포함한 hard cap은 아니다. 후보 하나만으로 추정 예산을 초과하면 batch=1로 유지한다. 전체 요청 transcript cache를 만들지 않아 긴 문장에서 cache가 제곱으로 커지는 것을 피한다. 기존 alignment DP 등 원래 메모리 사용은 별도다.

MFA span은 **inclusive**이며 중첩 span을 합산하지 않는다. process CPU·context switch·block I/O는 해당 MFA 프로세스 값이고 전체 자식 프로세스 합계나 I/O wait 시간은 아니다. peak RSS는 프로세스 수명 최대값이다. graph 등 실제 DirectAligner 경로에서 호출되지 않는 메서드는 빈 배열 또는 unavailable로 남는다. `request_preparation`에는 CLI callback/asset resolution/aligner 생성이 포함되며 아직 세부 자산별 cache를 추가하지 않았다.

`core-scheduling.json`에는 executionId와 queueWaitSeconds를 기록한다. GOP/MFA 진단은 근거 부속 timing이며 점수·prompt 입력을 변경하지 않는다. 요청 오류에서 스레드를 남긴 채 정리하지 않는다. CTC 실행 중에는 Python future 취소만으로 kernel을 멈출 수 없어 join을 기다리고, 외부 deadline/cancel 시 기존 소유 프로세스 트리 guard가 세대를 정리한다.

## 생성한 후보

이름: `canonical_dlpc_mfa_gop_20261007_t2_b64_v1` 및 같은 이름의 `_h5`.

| 항목 | SHA-256 |
| --- | --- |
| 부모 core | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` |
| 부모 H5 | `def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77` |
| 후보 core | `58a30aa79f233b37792a3f7f4c4ead036fbd809c8e721a1aa3b7a631f03373a1` |
| 후보 H5 | `13430a5b6bb39091897323b8569b7b775c1e6714039f5cd39cb335926862832c` |
| 후보 lock | `b8e7de7c1772b0b68762ee5dd1baa61f31a014b50b0e8eb4b1bdce5e5fc6e119` |
| 보존 prompt | `977c49744e7af9fc60fdc2eb13c23985692cb00dd71789d8b3c43c3813e3f1b5` |

63개 패키지 Python 파일 컴파일, 두 패키지 manifest의 파일 hash, runtime identity의 17개 파일 hash를 정적으로 확인했다. 변경한 빌더·준비 도구·helper도 컴파일했다. 이것은 CUDA/MFA/CTC 실제 동등성이나 성능 QA 통과가 아니다.

기본 후보 재생성 예시(원본 두 패키지가 있는 DLPC 또는 개발 환경에서 실행, destination은 새 디렉터리):

```bash
python3 deploy/dlpc/build_performance_release.py --core /workspace/intelligentai/validation/canonical_dlpc_gop_cuda_20261007_v2 --h5 /workspace/intelligentai/validation/canonical_dlpc_gop_announcer_20261007_v1_h5 --destination /workspace/intelligentai/performance-candidate-20261007 --threads 2 --batch 64
```

운영 env의 core/H5 경로를 이 후보로 먼저 바꾸면 안 된다. Native 실행 허용 목록·AI/Backend closed tuple·검증기·복구 bundle에는 아직 등록하지 않았다. 기존 allowlist를 우회해서 운영 접수를 여는 변경은 없다. 두 분석 진입점 모두 같은 새 core를 선택하고 lock을 먼저 준비해야 공통 실행 제한이 성립한다. 기존 core와 혼용하면 전역 1건을 보장하지 않는다.

## B2 보관

기존 전용 복구 키로 읽을 수 있는 `intelligentai/recovery/dlpc-20261006/performance-20261007/`에 보관했다. 인증 정보는 객체에 포함하지 않는다.

| 객체 | file ID | 크기 | SHA-256 |
| --- | --- | ---: | --- |
| 후보 core/H5 archive | `4_ze5cc71977ef1880ca60b0c17_f103d083744be27fc_d20261007_m031624_c005_v0501050_t0014_u01791342984117` | 204491 bytes | `44893068b70f15102df6cbc6934d61c5e583c6f9ddb42f7e580b3510f50a0234` |
| 읽기 전용 collector | `4_ze5cc71977ef1880ca60b0c17_f11658e1bca98058d_d20261007_m031056_c005_v0501048_t0030_u01791342656690` | 4865 bytes | `20f1026b3f678b35d968d4dc79b5f6ade8274f770d583df7816053fa992ed27d` |

둘 다 업로드 후 다운로드 SHA-256을 확인했다. archive는 소스 패키지이며 모델/실행 환경/서비스 app bundle 또는 자동 설치기가 아니다. 기존 모델 자산과 실행 환경이 필요하다. B2 보관을 DLPC 설치 완료로 해석하지 않는다.

## 남은 실행 조건과 다음 순서

1. **P0 현장 관측:** AWS 경로는 API 전용이며 DLPC 관리 셸은 없다. 기존 고정 SSH `b2` 권한으로 collector를 받아 hash 확인 후 실행하는 한 줄 명령을 사용자에게 전달했다. 서비스별 affinity/quota/PID 역할과 기존 worker 시간의 결과를 기다린다.
2. **개발자 후보 실행·동등성 비교:** `AGENTS.md`의 개발자 직접 QA 규칙에 따라 자동 회귀 테스트·관련 설정/산출물은 만들거나 실행하지 않았다. 검증 환경의 Native 실행 허용은 별도로 기록하고 운영 접수는 닫은 상태에서 진행한다. 이전 testaudio1 성공은 이번 후보 QA로 재사용할 수 없다.
3. 개발자가 같은 입력으로 현행/후보의 GOP 배열·finite 상태·후보 순위·coverage·TextGrid 경계를 비교한다. reference 재사용 및 batch 변경에서 수치 차이가 발생하면 원인과 허용 오차를 먼저 결정한다. cold/warm/recycle/긴 입력/오류·취소/TTS 병행을 구분하며 표본 수 없이 p95를 만들지 않는다. 실패 시 후보를 채택하지 않는다.
4. 관측으로 검증한 thread/batch와 중첩 이득을 선택한다. P2 자산 cache는 새 MFA span의 병목 확인 뒤 설계한다. P3 GPU GOP·P4 요청 동시성 확대는 앞 단계 통과 후 조건부 후보다. 이번 변경에는 포함하지 않았다.
5. 채택 후보만 AI/Backend tuple·verifier·native permit·새 배포/복구 bundle에 동시 등록한다. 개발자 승인 정보와 원복 묶음을 준비한 뒤 hold → drain → 양쪽 전환 → identity/readiness → resume → B2 복구 pin 순서로 진행한다. hold 중 Backend admission=false를 활성화 대기 조건으로 오해하지 않는다.

**현재 운영 source·Backend·복구 pin·서비스 설정은 이 후보로 전환하지 않았다.** 새 실제 추론, GPT 호출, TTS 품질 QA, 성능 향상 수치는 미확인이다.
