# Native 직접 접수와 지연 점검 — 2026-10-06

사용자 요청: 분석 요청·결과 표시에서 AWS를 제외하고 학습 이력과 보관은 결과 공개 후 처리한다. 사용자 인증 게이트를 추가하지 않는다.

## 관측 기준

2026-10-06 관리 API에서 Pod s8hb5b0l5k4qlx RUNNING, 공개 8080/8082와 SSH 22132를 재확인했다. 운영 core는 9월 28일 원본에서 파생한 Native-only v4이며 모델 추론을 구형 scorer로 되돌리지 않는다.

직전 testaudio1 분석 85(한국은행 문장, 5.141초)의 실제 기록:

| 구간 | 초 | 해석 |
|---|---:|---|
| 요청 → RunPod stage 시작 | 10.030 | AWS outbox 대기 2.729, dispatch 3.788 포함. warmup 시간이 아님 |
| 예약 및 다운로드 | 3.228 | 이미 서로 병렬이나 완료 뒤 코어 시작 |
| 코어 전체 | 32.031 | Native 추론 자체는 0.571 |
| MFA 호출 | 27.326 | 가장 큰 병목 |
| 표현 생성 경로 | 2.146 | GPT dispatch 0. GPT 네트워크 시간으로 해석 금지 |
| RunPod 사전 검증 | 1.903 | 근거/계약 검사 |
| AWS 결과 공개 | 2.908 | 직접 경로에서는 제거 |
| 공개 → 최초 조회 | 0.918 | 직접 SSE로 대체 |

MFA 보고서 `/tmp/vc-core-a8sha_4q/mfa_runtime/reports/g1_r0_645efee27f51_worker.json`:
worker 전체 27.132초, 정렬 workflow 21.515초, 그 안의 특징 추출 14.847초, 분석 0.709초, export 0.028초, cleanup 0.235초. 특징 추출과 workflow는 중첩하므로 합산하지 않는다. workflow 외 준비/정리 약 4.645초는 이 로그만으로 더 분해할 수 없다.

## 구현

- 기존 RunPod HTTP 앱에 `/direct` ASGI 앱을 연결한다. 이 경로만 공개 요청을 받으며 기존 서버 간 API 인증은 유지한다. 별도 AWS proxy·사용자 인증·예약을 호출하지 않는다.
- FE PCM 업로드를 지속 spool에 접수하고, 로컬 POSIX scratch에 복사해 resident에 전달한다. RunPod network volume은 FUSE이므로 UID/mode를 요구하는 core 작업 공간과 분리한다.
- 작업별 core 디렉터리 생성 누락을 수정한다. worker/guard는 고정 canonical Python을 사용한다.
- 분석 실행과 resident 유지 루프를 분리한다. 자원 예산이 설정된 경우 첫 요청 전부터 standby를 준비하며, 실행 중 generation 교체는 금지한다.
- producer의 파일 검증·오디오 검증·준비·resident 실행·출력 검증 시간을 기록한다. expression 구간과 실제 GPT dispatch 수를 구분한다.
- 근거 파일 쓰기는 병렬로 처리한다. 결과와 복구 가능한 근거를 저장한 후 RESULT_READY로 공개하고 SSE로 전달한다. B2/AWS history 작업은 이후 독립 lane에서 처리한다.
- journal 쓰기용 lock과 메모리 조회 lock을 분리해 저장 지연이 상태 조회를 막지 않게 한다. 보관 worker의 journal 쓰기는 이벤트 루프 밖에서 처리한다.

## MFA 개선 판단

Native/G2P와 MFA bootstrap은 이미 겹쳐 실행되고 있다. Native 0.571초를 GPU로 옮기는 것보다 MFA 특징 추출·workflow 준비를 먼저 조사해야 한다. 동일 모델·동일 정렬 설정을 유지한 채 corpus import, 특징 생성, 그래프 구성, GMM 정렬, DB 초기화별 wall/CPU/I/O 측정이 다음 비교 단위다. 전체 phoneme 정렬을 제거하거나 다른 입력의 정렬 결과를 재사용하지 않는다.

현재 frozen MFA runtime에는 queue 완료 알림과 단일 발화 DirectAligner가 이미 포함된다. 이를 새 개선처럼 중복 적용하지 않는다. NumPy/모델/정렬 알고리즘 변경은 이번 접수 경로 최적화와 분리한다.

## 검증 범위

자동 회귀 테스트와 브라우저 QA는 실행하지 않는다. 구문·컴파일·정적 확인, 요청된 운영 상태와 단일 음성 측정만 별도로 기록한다. 전체 단계 시간은 동일 executionId로 비교하고 병렬 구간은 합산하지 않는다. 운영 활성화 및 재측정 결과는 실제 수행 후 아래에 추가한다.
