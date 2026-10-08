# DLPC 자동 배포 활성화 및 재배포 — 2026-10-08

## 사용자 결정과 전환 범위

사용자는 전용 runner 설치를 승인한 뒤 자동 배포 활성화와 실제 재배포를 요청했다.
기존 GOP CUDA core `f6d56e1d…`와 채점은 유지하고, 병합된 최대 4문장 피드백 정책의
음성 H5 `3e430635…`를 별도 불변 디렉터리에 등록한다. 기존 H5와 과거 결과의 검증은 유지한다.
영상 실행 identity는 이번 음성 전환으로 승인하지 않는다. 영상 admission 설정도 켜지 않는다.

AI harness·실행 registry·v4/v5 closed tuple, AWS offline verifier, Backend SemanticVerifier와
공통 schema를 함께 갱신한다. 새 H5는 문장 수의 서버 강제 절단이 아닌 시스템 지침이다.
사용자의 운영 전환 승인은 품질 QA 결과와 구분한다. 이번 작업은 자동 회귀 테스트나
음성/GPT 생성·품질 QA를 수행하지 않는다.

## 배포 절차

1. 현재 main, DLPC·Backend source/bundle/contract, 진행 작업, rollout 상태를 확인한다.
2. 새 AI runtime·AWS verifier·Backend JAR·runtime profile을 비활성 위치에 준비한다.
   B2 runtime은 전체 업로드 후 다운로드 SHA-256/size를 대조한다. 비밀 설정은 archive에 넣지 않는다.
3. Backend maintenance와 DLPC direct hold를 적용하고 진행 작업을 drain한다.
4. 기존 Backend controller의 hold/activate → AI overlay/H5 설치·기동 → 양쪽 readiness →
   Backend resume 순서를 따른다. 기존 분석 source/env, Backend profile/JAR/verifier는 복원 가능하게 보존한다.
5. B2 cold-recovery pin과 AWS 비밀 복구본의 비밀 아닌 runtime identity를 실제 운영본에 맞춘다.
6. workflow를 main에 병합하고 `DLPC_DEPLOYMENT_ENABLED=true`를 설정한다.
   수동 `force_restart=true`는 현재 main과 로컬 source blob을 검증한 뒤 실제 drain·분석 재시작을 수행한다.
   frozen core/H5/lock과 bundle identity는 그대로 유지한다. TTS는 재시작하지 않는다.

## 실행 기록

### 2026-10-08 08:39 UTC / 17:39 KST 완료 관측

사용자의 명시 승인으로 PR #37·#38을 main에 병합하고 실제 운영 전환을 완료했다.

| 대상 | 확인된 운영본 |
|---|---|
| AI source | `791ca5496e04a10b6948c69dce97361ee4335f4c` |
| Runtime | 17,308,951 bytes, SHA-256 `d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443` |
| B2 복구본 | file ID `4_ze5cc71977ef1880ca60b0c17_f114952e6b08b9921_d20261008_m083112_c005_v0501033_t0033_u01791448272034`, 전체 다운로드 일치 |
| GOP core | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`, 변경 없음 |
| 음성 H5 / lock | `3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c` / `58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4` |
| Backend | `a35f9e90e63e1c740d345eec4daa616447fbf381`, 배포 controller 상태 COMPLETE |
| AWS verifier | `e5d6418f3625813e3d7551f14771b9424cc246b9ddb20b539c78137950f91d47`, JAR와 schema 9개 일치 |
| Root controller | `69f657d33b2ebf55d1410a0a3f9530284cdd71677cd9047d3d5949ea1bc3373a` |
| 자동 배포 | `DLPC_DEPLOYMENT_ENABLED=true`, 08:37:31 UTC 설정 |
| 실제 Actions 재배포 | [run 37751075955](https://github.com/voice-coaching/intelligentAI/actions/runs/37751075955), workflow_dispatch `force_restart=true`, success |

Actions 배포 step은 08:37:50–08:38:37 UTC에 실행됐다. DLPC root transaction
`791ca5496e04a10b6948c69dce97361ee4335f4c-restart-db5381b6007e4ab08f132215c5c83683`
영수증은 `status=succeeded`, `restarted=true`와 같은 source/core/H5를 보고했다.
분석 PID는 `1012945`, TTS PID는 기존 `95166`이었다. 첫 두 push workflow의 실패는
활성화 변수 설정 전 preflight 기록이며 최종 재배포 실패가 아니다.

08:39:23–27 UTC 조회에서 분석·direct·TTS readiness는 200, 보호 API 비인증 요청은 401,
Backend/AI handoff는 ready, 진행 작업·drain 항목은 모두 0이었다. 양쪽 접수 차단은 해제됐다.
GPT 설정은 OPENAI / `gpt-6-astra`, executorReady/handoffReady는 true였다.
TTS는 `melo-ko-practice-dlpc-r1`이며 재시작하지 않았다. 이는 운영 readiness·모델 접근 확인이며
새 녹음·GPT 생성·피드백 품질 QA 결과가 아니다. 영상 실행 identity/admission도 활성화하지 않았다.

영속 홈의 복구 file ID/size/SHA와 overlay installer를 갱신했다. AWS 비밀 복구본은
LLM root·pipeline revision·build revision 3개만 변경하고 나머지 비밀·복구 admission 정책을 보존했다.

#### 설치 중단과 복구

첫 설치는 Backend 기동 직후 handoff 조회에서 중단됐고 supported/schema 확인 후 재개했다.
이어 기존 prompt 링크 3개가 symlink 사전 검증에서 거절됐다. 두 시도 모두 앱 파일 쓰기 전 중단이다.
metadata/env/config 및 앱 원본 바이트를 백업과 대조하고 첫 시도 기록을 별도로 보존했다.
최종 대조 결과 해당 세 active pointer는 Git mode **100644**, 내용은 참조 파일명이었다.
운영본만 symlink였고 로더는 두 형태를 지원한다. symlink 백업을 보존한 뒤 링크 leaf를 정확한
Git pointer bytes의 일반 파일로 원자 교체했다. 아래 기록의 “해당 세 파일이 Git mode 120000”이라는
초기 판단은 정정한다. PR #38의 실제 mode 120000 지원은 별도 호환 처리다.

이 pre-write 실패 후 수정 main을 반영할 때 동일 JAR/schema/verifier/native pin,
기존 AI source/bundle·진행 작업 0·maintenance 소유를 확인했다. 이전 state/profile을 root 전용으로
백업하고 `operatorReplan`에 사유·시각·이전 profile digest를 기록하여 AI revision/runtime hash만 재계획했다.
이 감사된 운영자 복구는 일반 CI의 profile 불변성 검사 우회 기능으로 제공하지 않는다.
이후 Backend 3회 readiness 확인·resume, DLPC direct resume 및 Actions 실제 재시작을 완료했다.

#### 자동 배포 범위

main push는 전용 runner·root controller로 호환 application source를 배포하며 문서만 바뀌면 skip한다.
frozen prompt/core/H5·계약·의존성 변경은 조정 배포가 필요하다. 향후 source delta 배포는 이번 고정
B2 full runtime/profile을 자동 승격하지 않으므로 cold recovery 기준이나 Backend 배포 profile을
바꿔야 하면 별도 정합성 갱신이 필요하다. 기존 root 설치가 남는 재시작용 runner hook은 설치됐지만,
root 디스크가 지워지는 컨테이너 재생성에서 runner 계정·credential/controller 자동 재등록은 미구현이다.
재부팅/재생성 시험은 수행하지 않았다.

### 명시 승인 후 전환 중 발견한 링크 처리 결함

사용자의 명시 승인으로 PR #37을 main `2bca3ba91091d40711b3956b6100373ec370800a`에 병합했다.
Backend `a35f9e90…`를 drain 후 활성화했으며 양쪽 접수는 전환 완료까지 닫혀 있다.
DLPC overlay 사전 검증이 기존 active prompt 링크 3개를 발견해 파일 쓰기 전에 중단했다.
Git mode `120000`의 blob은 링크 목적지이므로 regular source bytes로 설치하지 않는다.
runtime의 `appSymlinks` inventory로 목적지와 참조 파일을 검증하고 기존 링크를 보존한다.
cold restore의 누락 링크만 생성하며, force restart는 실제 readlink의 Git blob digest를 대조한다.
앱 백업도 symlink를 보존한다. 이는 정적 컴파일·운영 경로 보정이며 품질 QA가 아니다.

### 승인 전 준비 기록

2026-10-08 UTC 관측 기준으로 전환 준비만 완료했다. AI PR #37은 Draft/open이며 main에
workflow가 병합되지 않았고 `DLPC_DEPLOYMENT_ENABLED`도 아직 설정하지 않았다. 자동 승인 검토가
PR Ready 전환·main 병합을 별도 명시 승인 대상으로 거절하여 사용자 답변을 기다린다.
운영 hold·JAR 교체·AI 설치·workflow 재배포는 아직 수행하지 않았다.

| 준비된 후보 | 확인 근거 |
|---|---|
| AI source | `518c31cdeaf6fc4ae74bd3a52a24823f2b3ef65f` — 최종 main 병합 뒤 revision을 다시 고정해야 함 |
| Runtime | 17,306,274 bytes, SHA-256 `3a4efe244a8e3c47ce2fb8cae39389d1c893fedfed67f916e43c7017198d1b3a` |
| B2 | file ID `4_ze5cc71977ef1880ca60b0c17_f10192a30f48b7e92_d20261008_m040102_c005_v0501051_t0019_u01791432062558`, 업로드 후 전체 다운로드 일치 |
| AWS verifier | `e5d6418f3625813e3d7551f14771b9424cc246b9ddb20b539c78137950f91d47`, 비활성 설치·offline installation 확인 |
| Backend JAR | 최소 운영 후보 `a35f9e90e63e1c740d345eec4daa616447fbf381`, SHA-256 `a57595a752b0f8be2646611485ca2ac22b6202c2987f82c11ee2f8aef86a08c2`, compile/bootJar 성공·9개 schema 일치 |
| Backend 인계 | develop PR #116, head `91eeb23757517bd0709d7914bf2fe34a423c52e7`, mergeable. develop 병합 소스와 위 최소 운영 JAR는 서로 다른 revision |
| Frontend | public alias `https://vc-fe.vercel.app`의 JS 18개 digest 확인. GitHub Production 성공 revision `f927cfa65094a29a4eb06e15754e17ca916103c6`은 별도 관측이며 보호된 고유 배포 URL과 alias의 동일성은 확인하지 못함 |

AWS 운영 JAR는 `8ce2a0385dae3524559dfc36e14a997f1307a5d1`이며 배포 상태 COMPLETE,
maintenance marker 없음으로 확인했다. 후보 runtime profile은 root 전용 비활성 경로에만 준비했다.
DLPC는 2026-10-08 04:05:03 UTC에 운영 source `df90eb10…`, bundle `b7170dda…`,
기존 음성 H5 `def65f68…`를 보고했다. `/readyz`·`/direct/readyz`는 각각 200,
active/direct job은 각각 0, direct hold 없음, Actions runner는 RUNNING이었다.
root controller도 기존 `8ba96c45…`이며 새 force-restart controller는 아직 설치하지 않았다.
실제 revision·digest·시각·workflow receipt는 전환 확인 후 추가한다. 이 기록은 추론·GPT 생성·
피드백 품질 QA 통과를 뜻하지 않는다. root 디스크가 지워지는 컨테이너 재생성에서 runner credential의
자동 복원은 현재 구현돼 있지 않다.
