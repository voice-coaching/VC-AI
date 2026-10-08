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
실제 revision·digest·시각·workflow receipt는 전환 확인 후 추가한다. 이 기록은 추론·GPT 생성·
피드백 품질 QA 통과를 뜻하지 않는다. root 디스크가 지워지는 컨테이너 재생성에서 runner credential의
자동 복원은 현재 구현돼 있지 않다.
