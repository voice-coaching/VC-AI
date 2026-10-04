# H5 짧은 총평을 canonical v4에 적용

2026-10-03 사용자 선택: H5를 현재 canonical v4에 적용한다. **후속 직접 배포 승인에 따라 Backend와 RunPod 운영 전환을 완료했고, 14:53 KST 신규 분석 접수를 복원했다.** 14:55 KST AWS의 실제 HTTP client와 같은 헤더로 외부 분석·canonical readiness 200을 확인했다. FE PR #39는 미배포다. 기존 S7 원본과 기존 결과의 identity는 보존했다.

## 변경된 동작

- `canonical-h5-20261003-v1`은 S7의 별도 파생 하네스다. 기존 `CANONICAL_FROZEN_20260928_V4` 요청 프로필은 core/공개 계약 식별자로 유지하고, 결과의 manifest/lock/prompt SHA 세 값으로 표현 하네스를 구분한다.
- 서버가 선택한 최대 3개 후보의 순서·scoped ID·guidance 객체·MFA 근거를 보존한다. ACCEPT와 허용 후보가 있을 때 GPT를 최대 1회 호출한다. non-ACCEPT와 후보 없음의 호출 0회 정책도 유지한다.
- H5 표현 규칙을 system 메시지 마지막에 추가한다. 총평은 action만 순서대로 연결한다. action은 한 문장, UTF-16 140 이하, 마지막 마침표 하나이며 내부 진단 문구를 제외한다. 설명·연습·자가 확인은 상세 필드로 보존한다.
- 모델 응답에서 위 규칙을 검증하고 실패 시 같은 후보에 연결된 짧은 기본 행동 문구로 대체한다. 재호출·문장 자르기·후보 수 축소로 길이를 맞추지 않는다. fallback에도 같은 action 검증을 적용한다.
- 개인 조음 오류·영상 교정·운율 평가·점수 생성 권한은 늘리지 않는다. 정규식 검사가 자연어의 모든 의미적 정확성을 보증하는 것은 아니다.
- model=`gpt-6-astra`, low reasoning, json_object, store=false, 출력 1200, timeout 120초, 입력 예산은 S7 그대로다. 확장된 prompt도 기존 입력 예산에 포함되며 초과하면 호출하지 않는다.

## 식별자와 원본 보존

| 항목 | SHA-256 |
|---|---|
| H5 manifest | `9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45` |
| H5 dependency lock | `93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5` |
| H5 실제 system 문자열 | `6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd` |
| 확장된 v4 결과 schema | `11f7c609421a0c80cb337cb23c1c203cef540f28eea7fce67b16f37577029d5b` |

원래 H5 legacy 프롬프트와 이 파생본의 해시는 다르다. 파생본은 canonical 역할·위치·권한 지시를 유지한 채 H5의 표현 정책을 이식했다. S7 manifest/lock/prompt는 [기존 목록](../gpt6-prompt-harness-catalog-20261003.md)에 그대로 남는다.

[패키저](../../deploy/runpod/build_canonical_h5.py)는 원본 S7 manifest/lock과 의존 파일 11개의 해시를 먼저 확인한다. [추가 프롬프트](../../deploy/runpod/harnesses/canonical_h5_prompt.txt)와 [표현 정책](../../deploy/runpod/harnesses/canonical_h5_policy.py)만 파생 파일에 반영한다. 기존 디렉터리나 원본 하위로의 덮어쓰기는 거절한다. 전문가 사례 원본은 기존 비공개 보관본을 사용하며 Git에 추가하지 않는다.

## 파일과 책임

| 파일 | 책임 |
|---|---|
| `src/voice_coach/backend_analysis/canonical_harness.py` | 허용된 S7/H5 식별자의 닫힌 목록 |
| `canonical_bridge.py` | 실제 설치 manifest에 맞는 pinned adapter 로드, 정확한 association 기록 |
| `canonical_result.py` | 하네스별 재검증·fallback 원문 검증·실제 prompt identity 직렬화 |
| `docs/contracts/runpod_result_v4.schema.json` | 기존·H5의 정확한 identity 조합만 허용; 혼합 manifest/lock/prompt 거절 |
| `deploy/runpod/canonical_verify.py` | S7와 H5를 모두 설치 검증하고 원본 association에 따라 선택; GPT 실행 없음 |
| `deploy/runpod/stage_backend_verifier_bundle.py` | 새 검증기 의존 파일·H5 묶음을 별도 AWS release로 설치 |
| `deploy/runpod/prepare_canonical.py` | 배포 시 H5 root와 pipeline revision 선택 |

Backend 변경은 `develop` 기반 `feat/canonical-h5-20261003`이다. 공유 schema와 설치 readiness의 H5 pin을 추가하고, 독립 의미 검증을 통과한 H5의 action만 기존 summaryFeedback에 저장한다. 기존 S7 결과도 계속 검증·조회한다. DB migration은 없다.

FE 변경은 `dev` 기반 `feat/canonical-h5-summary-20261003`이다. 기존 화면과 API를 유지하고 canonical 총평을 action만 연결하도록 바꾼다. 기존 S7 이력도 이 표시 규칙을 사용하지만 과거 action의 길이까지 소급 수정하지는 않는다. 전체 원문은 canonical 객체에 보존한다.

## 운영 적용 순서

1. 원본 S7에서 파생 디렉터리 `/workspace/intelligentai/validation/canonical_h5_20261003`를 패키징하고 위 해시를 대조한다. 서비스 사용자의 읽기 권한을 확인한다.
2. AWS 검증기 release는 기존 `frozen/core`, `frozen/llm`에 **형제 경로 `frozen/canonical_h5_20261003`**와 새 앱 의존 파일을 함께 포함한다. `--check-installation`은 양쪽 하네스가 모두 있어야 READY다.
3. Backend 검증기·JAR·공유 schema를 먼저 배포한다. RunPod와 Backend의 schema digest가 달라지는 동안 신규 canonical 접수는 readiness에 의해 차단된다. 진행 중인 분석이 없는 시점에 전환한다.
4. RunPod의 새 source/schema/boot 설정을 기존 백업·원복 배포 절차로 적용하고 실제 H5 manifest/lock/prompt 및 양쪽 readiness를 대조한다. legacy GPT·TTS는 별도다.
5. FE의 action-only 총평을 배포한다. 실제 녹음·생성·표현 품질은 개발자가 확인한다.

최초 구현은 저장소 AGENTS의 `develop PR로 인계하고 담당자가 검증·배포` 규칙에 따라 PR까지 준비했다. 이후 사용자가 Backend 배포와 RunPod H5 활성화를 직접 지시하여 아래 운영 전환을 수행했다. Backend를 먼저 준비하고 RunPod를 전환한 뒤 양쪽 접수를 열었다.

원복은 진행 중 작업을 보호한 뒤 RunPod source/boot/schema를 기존 S7 묶음으로 돌린다. Backend의 S7/H5 양쪽 reader는 유지해 이미 저장된 H5 결과를 읽을 수 있게 한다. Backend 자체도 이전 버전으로 되돌리면 H5 이력 조회가 거절될 수 있으므로 단순 JAR 원복을 완전한 원복으로 보지 않는다.

## 확인 범위

원본 해시·정적 패키징·Python 구문·JSON 형식·공유 schema 동일성, Backend JAR 빌드, FE TypeScript 컴파일을 확인한다. 자동 회귀·브라우저·실제 녹음·GPT 생성·품질 QA는 수행하지 않는다. 설치 readiness는 실제 분석 성공과 구분한다. 아래 후속 기록에 최종 빌드·PR·배포 상태를 추가한다.

### 실제 준비·확인

- 변경 Python 8개 구문 컴파일 및 JSON 형식·양쪽 공유 schema 동일성 확인. 로컬 Python에는 jsonschema가 없어 로컬 metaschema 검사 시도는 실행되지 않았다.
- Backend Java 21 `bootJar -x test --no-daemon` 성공. FE는 동일 package-lock의 기존 의존 설치본을 사용해 `tsc --noEmit --incremental false` 성공.
- AWS 별도 release: `/opt/alpha-canonical/releases/021e17aa5e3bb51170b2cd58cd86d70739177ea82fef324d4cc310d3cd490ed4`, 128개 파일, 활성화하지 않음. 서비스 사용자로 `--check-installation`을 실행해 기존 core/S7와 새 H5 두 pin 모두 READY 확인. 작업 stdin·B2 조회·추론·GPT 호출은 없었다.
- RunPod: `/workspace/intelligentai/validation/canonical_h5_20261003`에 13개 파일 준비, 전부 SHA 일치. 기존 S7·서비스 env·프로세스는 변경하지 않았다. 해당 FUSE 볼륨은 chown을 거절하고 기존 S7처럼 고정 디렉터리 777/파일 666 권한을 보고한다. 최초 빈 디렉터리에서 중단된 뒤 그 경로가 비어 있음을 확인하고 완성했다. runtime의 내용 무결성 pin은 필수이며 POSIX 소유권으로 보호됐다고 주장하지 않는다.
- 운영 Backend JAR·검증기 활성 경로·RunPod H5 선택·FE 배포는 미수행이다. PR과 담당자 배포 또는 사용자의 명시적인 직접 배포 지시가 남아 있다.

### 커밋·PR·배포 묶음

- AI 구현 `b301e14`, 원격 `feat/canonical-h5-20261003`에 push. main·기존 통합 브랜치를 변경하지 않았다.
- Backend `68ecde1`, [develop 대상 draft PR #98](https://github.com/voice-coaching/VC-BE/pull/98).
- FE `380b700`, [dev 대상 draft PR #39](https://github.com/voice-coaching/VC-FE/pull/39).
- Backend 빌드 JAR SHA: `a66bcaca14769c2d081fb243b50d6e884a8dbe19c653fa33a4ee197884848b5e`.
- 로컬 운영 인계 폴더: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/canonical-h5-package-20261003` (Git 제외).
- RunPod 런타임 5개 파일의 변경 전 실행/overlay SHA를 대조해 `runpod-runtime-bundle.json`을 준비했다. bundle SHA `764aeaaf36d00b53da1e7954e94c78bf92e59e8f2cf96c209213fab6393ce30f`, 기준 overlay SHA `e49443deec905def50d6eada451e9310a20f3c45c22e6ef9f93c66154ee2fffe`. 적용 시 drift 검증을 다시 통과해야 한다.
- 이 묶음은 준비만 했으며 `deploy_canonical_runtime.py`를 실행하지 않았다. PR 병합·운영 활성화도 하지 않았다.

## 직접 배포 실행 결과 — 2026-10-03 14:55 KST

위 준비 시점의 미활성화 기록에 대한 후속이다. 사용자 명시 승인 범위는 Backend 배포와 RunPod H5 활성화다. PR 병합과 FE 운영 배포는 수행하지 않았다.

### 기준선과 전환

- RunPod `/proc/1/environ`의 선택한 연결 필드로 Pod `s8hb5b0l5k4qlx`, `194.68.245.79:22132`를 재확인했다. 관리 API를 새로 조회한 것은 아니다.
- AWS 시작 JAR은 `b99a14f0ed75bc3fb5473d157f2723c8e0346f7e`, SHA `d54e6b582b3693488126041ead50209a46ae78a4e258ac5c00a3c882fc2b7efb`였다. H5 브랜치의 직전 기반과 일치하여 추가 기능·migration을 섞지 않았다. DB 적용 버전은 37이었다.
- 읽기 전용 DB 조회에서 활성 lease 0건. PENDING 1건은 8/9 생성된 analysis 1, lease 없음이었다. 해당 기록을 수정하지 않았다.
- RunPod gateway에서 `/v1/analysis-jobs`의 신규 접수만 잠시 503으로 보호했다. TTS와 조회·취소 경로는 유지했다.
- [Backend 전환 스크립트](../../deploy/runpod/activate_backend_h5.py)가 JAR·기존 env·검증기 무결성을 확인하고 백업 후 JAR와 verifier 경로 3개를 변경했다. secret 값·접수 설정은 재발급하거나 공개하지 않았다.
- 첫 실행은 schema가 아직 다른 RunPod의 접수 허용까지 먼저 기다려 timeout 후 기존 JAR/env로 자동 복구됐다. 배포 단계의 확인 조건을 `canonicalSupported=true`와 새 schema digest로 수정하고 재실행했다. 최종 admission은 RunPod 전환 후 별도로 확인했다.
- Backend H5 준비 후 RunPod 5파일 배포를 수행했다. 부팅 준비·재기동·파일 대조가 성공했다. R과 S7 원본, 기존 legacy GPT 하네스, 독립 TTS 설정은 변경하지 않았다.

### 최종 배포 식별자

| 항목 | 값 |
|---|---|
| Backend 적용 source | `68ecde1b609ee8677c15b3f09ed8c14248797589` |
| Backend JAR | `/opt/alpha/releases/68ecde1b609ee8677c15b3f09ed8c14248797589.jar` |
| Backend JAR SHA | `a66bcaca14769c2d081fb243b50d6e884a8dbe19c653fa33a4ee197884848b5e` |
| AWS verifier release | `021e17aa5e3bb51170b2cd58cd86d70739177ea82fef324d4cc310d3cd490ed4` |
| RunPod 적용 source / bundle | `b301e14` / `764aeaaf36d00b53da1e7954e94c78bf92e59e8f2cf96c209213fab6393ce30f` |
| RunPod 최종 overlay SHA | `e9c1dbd3cf01069289e85d7a99fbbda6f1c6de496809d50dc538fdaf5ad4a636` |
| 실행 LLM root | `/workspace/intelligentai/validation/canonical_h5_20261003` |
| pipeline revision | `canonical-R-421fda-H5-20261003-v1` |
| 실제 prompt SHA | `6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd` |

RunPod의 기존 `workerRevision=32d98e3…`은 기본 배포 revision으로 유지된다. 이번 부분 배포 identity는 deployment/overlay의 canonicalIntegration source와 bundle, 위 pipeline revision을 함께 사용한다. 기존 workerRevision만으로 H5 미적용이라고 판단하지 않는다.

### 확인과 복구 자료

- 14:52:53~58 KST Backend canonical readiness 200, `canonicalSupported=true`, `canonicalAdmissionEnabled=true`; RunPod analysis/GPT readiness 200, `OPENAI / gpt-6-astra`; canonical readiness 200, executor/admission true. 공유 schema 9개 SHA가 모두 일치했다.
- H5 manifest/lock/prompt는 위 pin과 같고 lock의 의존 파일 전체가 일치했다. 독립 TTS live/ready도 각각 200이었다.
- 14:53:35 KST gateway 원본을 복원·설정 검사·reload했다. SHA는 `45084a18fd5427600328e6c5eca128b8bbf40259467488b94aa22ec0862ddc11`로 복귀했다. 신규 분석 접수 경로의 임시 503은 제거됐다.
- 14:55:04 KST AWS에서 `https://ai.voice-coaching.site/health/services`와 `/health/canonical`을 실제 Backend와 같은 User-Agent/identity 헤더로 조회하여 각각 200, executorReady/admission true를 확인했다. 기본 Python urllib 헤더는 403을 받아 실제 client 헤더로 다시 확인한 것이며, generic urllib 요청도 성공한다고 보고하지 않는다.
- Backend 최종 백업: `/var/backups/alpha-canonical/h5-20261003T054920Z`. 첫 자동 복구 기록은 `h5-20261003T054501Z`다. env 백업은 root 전용이며 문서·Git에 포함하지 않는다.
- RunPod 백업: `/workspace/intelligentai/deployment/canonical-runtime-backup-20261003T055041Z`. gateway 백업: `/run/intelligentai/gateway-before-h5-20261003.conf`.
- 실제 녹음·GPT 생성·callback 전체 흐름·품질 QA는 미수행이며 `executionQaPerformed=false`다. 이 배포를 음성 분석 E2E 성공으로 표현하지 않는다. FE의 짧은 총평 표시는 별도 PR #39 배포가 남아 있다.
