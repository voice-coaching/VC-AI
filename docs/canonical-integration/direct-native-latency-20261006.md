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

## 직접 경로 재측정 — 2026-10-06 13:51 KST

동일 testaudio1 PCM 164,600 bytes, job `0ac6f28f-80d3-414a-be30-4a6a2e36164a`, execution `1091523b-3547-40ec-b994-9dc692144c00`. 클라이언트가 공개 RunPod HTTPS에 직접 POST하고 SSE를 수신했다. 브라우저 렌더링 시간을 측정한 것은 아니다.

| 구간 | 초 |
|---|---:|
| POST 시작 → 접수 응답 | 1.283 |
| POST 시작 → RUNNING SSE 관측 | 1.725 |
| RunPod 접수 기록 → 실행 시작 (동일 서버 시계) | 0.104 |
| worker 프로세스 시작 | 0.336 |
| 코어 전체 | 20.202 |
| 표현 경로 | 0.966 |
| RunPod 결과 검증 | 0.823 |
| 근거 병렬 쓰기 | 0.024 |
| 결과 쓰기 | 0.005 |
| 결과 기록 → 공개 상태 전환 | 0.083 |
| 서버 접수 → 공개 | 22.553 |
| **클라이언트 POST → 최초 RESULT_READY SSE** | **23.947** |

코어 세부: artifact 검증 0.472초, audio 검증 0.001초, 준비 0.006초, resident 호출 19.543초, 출력 검증 0.173초. Native 모델 0.858초, decode 0.193초, MFA 호출 17.169초. 기대 음소 준비와 Native 실행은 겹치므로 합산하지 않는다.

MFA worker 17.024초: 정렬 workflow 12.272초(특징 추출 6.408초 포함), 분석 0.538초, export 0.034초, cleanup 0.198초. 나머지 준비 약 3.982초. 그래프 구성과 GMM 정렬 개별 시간은 현재 로그에 없어 미측정으로 남긴다.

이전 53.165초 대비 결과 수신은 약 55% 감소했다. **MFA 알고리즘은 바꾸지 않았으므로 MFA 27.326→17.169초 차이를 이번 코드의 인과 효과로 단정하지 않는다.** warm cache·동시 부하에 따른 변동을 포함한 단일 관측이다. 코어 외 대기는 약 21.134→3.745초로 줄었다.

결과 전달은 RESULT_READY지만 내용은 `UNSCORABLE/SCORING_FAILED`, adapter FAIL_CLOSED, GPT dispatch 0회다. 기존 Native DRAFT 채점 정책 상태이며 성공적인 점수 생성·코칭 품질 검증으로 보고하지 않는다. 초기 첫 직접 시도는 FUSE journal 소유권 검사에서 실패했으며 이 관측에서 제외했다. 실패 요청은 자동 재실행하지 않았다.

## 배포 및 남은 범위

- RunPod 공개 8080 포트의 `/direct`에 연결한다. 별도 Pod 포트 변경이나 AWS proxy가 필요 없다.
- 지속 볼륨의 flock/fsync를 실제 계정으로 확인했다. FUSE 소유권과 POSIX scratch 검사를 분리했다.
- 사용자 인증 게이트는 없으며, 이력 계정 연결과 서버 간 history 전달만 기존 인증을 사용한다.
- 대기 worker 예산: 추가 준비 reserve 6 GiB, 최소 여유 6 GiB. container limit 약 50 GB, 관측 사용량 약 28 GB. 메모리 여유가 없으면 추가 worker를 강제로 시작하지 않는다.
- 첫 코드 활성화 과정에서 설정 namespace 검증 및 복호화 Python 불일치로 13:30~13:39 KST 분석 기동이 보류됐다. 수정 후 Native로 복구했다. TTS 프로세스는 재기동하지 않았다.
- AWS V40 적용을 확인하고 direct-history API를 활성화했다. 사용자 결과 공개와 history/archive 완료 상태는 분리된다.
- 직접 음성은 B2 전용 writer의 versioned AUDIO 객체로 보관한다. archive v2는 `storage=B2`, `recording={kind,objectKey,versionId,sha256,byteSize}`와 5개 근거 artifact 참조를 AWS에 전달한다. 기존 녹음 다운로드용 S3 client로 직접 음성을 PUT하지 않는다.
- FE dev PR #60: 기본 공개 origin과 `/direct` base path 보존. 운영 PR #61. 독립 연습만 해당하며, 코스·시험·기존 세션은 정수형 분석 ID를 요구하는 완료 계약의 비동기 projection이 아직 필요하다. 전체 화면 전환 완료로 보고하지 않는다.

최종 운영 확인:

- RunPod code revision `1125c06`, bundle `b4fa7ed92d7f7f2f4c396cfc4eeba3d0f955cc545a418724cbdd207c31f8e51c`; `/direct/readyz` ready. Native 모델과 H5 pin은 기존 v4와 동일하다.
- 위 측정 job의 `archiveStatus=COMPLETE`, `historySyncStatus=COMPLETE`, AUDIO 포함 6개 B2 versioned 객체 보관을 확인했다. 전용 테스트 사용자 40의 이력 연결 응답은 `SAVED`다. 초기 보관 클라이언트 수정 전 재시도 8회가 있었으므로 이 시도의 보관 소요시간을 정상 archive 성능으로 사용하지 않는다.
- FE #60(dev), #61(main) 병합. main `bc2f39126457705460463c50623ac45dca49e41a`의 Vercel 상태 SUCCESS. 공개 JS에서 RunPod `/direct` 기본 주소와 기본 활성화 분기를 확인했다. 공개 자산 18개 hash 및 새 RunPod source/bundle을 AWS 운영 배포 profile에 갱신했다. Vercel 빌드 로그·브라우저 QA는 수행하지 않았다.

전체 전환의 다음 구현 단위는 course/exam/session의 `directJobId → 이력·세션·성적` 비동기 projection이다. 기존 세션 소유권·시험 콘텐츠 일치·완료 적격성은 결과 표시 후 처리하되 유지해야 한다. 분석 ID를 임의 정수로 만들거나 AWS 분석을 다시 호출하는 fallback은 사용하지 않는다. MFA 추가 세분화는 정렬 그래프 구성과 GMM 정렬을 각각 계측하는 동일 모델 파생 릴리스에서 진행한다.
