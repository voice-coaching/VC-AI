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

전환 준비 중. 이 문서의 절차는 완료 근거가 아니다. 실제 revision·digest·시각·workflow receipt는
전환 확인 후 아래에 추가한다. root 디스크가 지워지는 컨테이너 재생성에서 runner credential의
자동 복원은 이번 배포 활성화와 별개이며 현재 구현돼 있지 않다.
