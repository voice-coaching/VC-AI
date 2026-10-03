# H5 짧은 총평을 canonical v4에 적용

2026-10-03 사용자 선택: H5를 현재 canonical v4에 적용한다. 이 문서는 구현·배포 인계 기록이다. **구현과 로컬 패키징 완료, 운영 전환 전**이다. 기존 S7 원본을 수정하거나 기존 결과의 identity를 바꾸지 않는다.

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

Backend 변경은 저장소 AGENTS의 `develop PR로 인계하고 담당자가 검증·배포` 규칙을 따른다. 이번 선택은 H5 적용 요청이며 Backend 담당자 배포 규칙을 명시적으로 해제한 지시는 없으므로 PR을 준비하고 운영 전환은 별도로 인계한다. RunPod만 먼저 전환하면 현재 Backend가 새 identity를 거절하므로 단독 활성화하지 않는다.

원복은 진행 중 작업을 보호한 뒤 RunPod source/boot/schema를 기존 S7 묶음으로 돌린다. Backend의 S7/H5 양쪽 reader는 유지해 이미 저장된 H5 결과를 읽을 수 있게 한다. Backend 자체도 이전 버전으로 되돌리면 H5 이력 조회가 거절될 수 있으므로 단순 JAR 원복을 완전한 원복으로 보지 않는다.

## 확인 범위

원본 해시·정적 패키징·Python 구문·JSON 형식·공유 schema 동일성, Backend JAR 빌드, FE TypeScript 컴파일을 확인한다. 자동 회귀·브라우저·실제 녹음·GPT 생성·품질 QA는 수행하지 않는다. 설치 readiness는 실제 분석 성공과 구분한다. 아래 후속 기록에 최종 빌드·PR·배포 상태를 추가한다.

### 실제 준비·확인

- 변경 Python 8개 구문 컴파일 및 JSON 형식·양쪽 공유 schema 동일성 확인. 로컬 Python에는 jsonschema가 없어 로컬 metaschema 검사 시도는 실행되지 않았다.
- Backend Java 21 `bootJar -x test --no-daemon` 성공. FE는 동일 package-lock의 기존 의존 설치본을 사용해 `tsc --noEmit --incremental false` 성공.
- AWS 별도 release: `/opt/alpha-canonical/releases/021e17aa5e3bb51170b2cd58cd86d70739177ea82fef324d4cc310d3cd490ed4`, 128개 파일, 활성화하지 않음. 서비스 사용자로 `--check-installation`을 실행해 기존 core/S7와 새 H5 두 pin 모두 READY 확인. 작업 stdin·B2 조회·추론·GPT 호출은 없었다.
- RunPod: `/workspace/intelligentai/validation/canonical_h5_20261003`에 13개 파일 준비, 전부 SHA 일치. 기존 S7·서비스 env·프로세스는 변경하지 않았다. 해당 FUSE 볼륨은 chown을 거절하고 기존 S7처럼 고정 디렉터리 777/파일 666 권한을 보고한다. 최초 빈 디렉터리에서 중단된 뒤 그 경로가 비어 있음을 확인하고 완성했다. runtime의 내용 무결성 pin은 필수이며 POSIX 소유권으로 보호됐다고 주장하지 않는다.
- 운영 Backend JAR·검증기 활성 경로·RunPod H5 선택·FE 배포는 미수행이다. PR과 담당자 배포 또는 사용자의 명시적인 직접 배포 지시가 남아 있다.
