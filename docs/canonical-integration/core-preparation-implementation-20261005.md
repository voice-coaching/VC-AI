# 코어 준비 지연 개선 구현 — 2026-10-05

작업 유형은 서비스 로직·외부 연동·리팩토링이다. 요청별 근거 보존, 계층 분리, 배포 revision 고정 규칙을 적용한다. 이 문서는 구현 인계이며 운영 성능 측정 결과가 아니다.

## 원인과 변경

기존 분석 79, execution `789733e4-137c-43d3-a36d-c039b1a1a1fb`의 코어는 153.706초였다. 10월 5일 17:21 KST에 조회한 보존 기록에서 MFA 준비 49.869초 중 별도 version CLI가 29.561초, 실제 worker 시작이 16.846초였다. MFA ready 이후 61.282초에는 AI import, 정책·1,262,083,185바이트 가중치 해시 확인, 설정 생성이 들어간다. Native 모델 로딩 0.868초는 그 이후다. 기타 10.034초에는 결과 이후 프로세스 종료 대기가 포함된다. 새 구현의 실측값으로 해석하지 않는다.

| 위치 | 구현 |
|---|---|
| `deploy/runpod/native_mfa_source.py` | 별도 version CLI를 제거하고 실제 MFA worker의 설치 버전·모듈 출처가 검증된 ready 응답을 재사용한다. 요청 정리 통과 후 결과 future를 완료하고 세대 종료는 이어서 처리한다. |
| `deploy/runpod/native_resident_source.py` | G2P 엔진을 세대당 한 번 생성한다. 요청별 CaptureG2P 관측 state는 따로 유지한다. 기존 모델·checked_contract 캐시는 유지한다. |
| `deploy/runpod/harnesses/canonical_resident_server.py` | MFA 준비와 AI 준비를 겹친다. runtime 준비·import·계약·가중치 해시·모델·G2P·결과 준비를 구분해 기록한다. 결과 DONE은 프로세스 종료 전에 보낸다. |
| `src/voice_coach/backend_analysis/canonical_generation.py` | 부모의 전체 소스 검증 결과를 읽기 전용 mount·inode·manifest에 묶어 private 세대 기록으로 전달한다. 쓰기 가능하거나 확인 불가하면 매 요청 전체 검증을 유지한다. |
| `src/voice_coach/backend_analysis/canonical_resident.py` | 3회 사용 한도를 유지한다. 선택적으로 예비 세대 한 개만 준비하고, 요청 정리·DONE 이후 교체한다. 정리 실패가 확인되면 새 배정을 중지한다. |
| `canonical_lane.py`, `direct_analysis/service.py`, 두 executor | 부모가 선택한 세대 기록과 socket을 같은 요청에 전달한다. 사용자 입력에서 세대 경로를 받지 않는다. |

입력 WAV·대본·실행 식별자·결과 연결 검증은 그대로 유지한다. FE 직접 분석 API의 사용자 인증 방식은 변경하지 않는다. 보관과 AWS 학습 이력은 기존 비동기 경로를 사용한다.

## 패키지와 호환성

새 소스 패키지는 `canonical_native_core_20261005_v2`, `canonical_native_h5_20261005_v2`다. 부모 소스는 변경하지 않고 별도 패키지로 만든다. 기존 Native v1과 과거 결과 식별자는 보관 자료 검증용으로 남는다. 구버전 추론 fallback은 추가하지 않는다.

| 식별자 | SHA256 |
|---|---|
| core manifest | 68cda53df72be9dace15f3829a840c27f40efbbfee1a561f8e58d314b4991aad |
| H5 manifest | 7dfc26fb088300165b4a564847d0a489092b0ab830ef591943a40f31ecb85747 |
| H5 dependency lock | 93ccb06cafb9128f4ae02f111ca4bfb599305f84ecce01e41971e69822ba1ec6 |

AI/BE의 v5 및 과거 v4 결과 검증 스키마에 새 식별자 조합을 추가한다. 이는 v4 접수 재활성화가 아니다. Backend 설치 확인은 기존 v1 또는 새 v2의 정확한 세 값 조합만 허용하며 혼합 조합은 거절한다. 기존 설치가 Native 식별자 전체를 생략하는 동작도 유지한다. verifier export는 v2가 없으면 기존 묶음을 만들 수 있고, 있으면 core/H5가 모두 있어야 한다.

**현재 Native 정책은 DRAFT이고 실행 승인 allowlist는 비어 있다.** 이 변경은 보정 승인이나 실제 추론 QA를 대체하지 않으며 그 제한을 우회하지 않는다. 정책 승인 시 패키지 바이트와 해시가 다시 달라지므로 승인된 패키지 식별자로 양쪽 등록과 배포를 맞춰야 한다.

## 배포 설정

- [준비 설정 예제](../../deploy/runpod/core-preparation.env.example): 예비 세대는 기본 OFF다. 추가 세대의 실측 peak RSS를 `AI_CANONICAL_STANDBY_RESERVE_BYTES`, 나머지 서비스 여유를 `AI_CANONICAL_STANDBY_MIN_FREE_BYTES`에 지정한다. 유한한 cgroup 메모리 한도와 사용량을 읽을 수 없으면 두 번째 세대를 준비하지 않는다. 여유가 줄면 예비 세대를 종료한다. 메모리 한도 자체를 설정하는 기능은 아니다.
- [읽기 전용 설정 예제](../../deploy/runpod/core-readonly.conf.example): 승인된 패키지 경로를 service mount namespace에서 읽기 전용으로 고정한다. 읽기 전용 소스는 실행 중 다른 경로나 외부 프로세스로 덮어쓰지 않는다. 새 배포는 별도 경로·새 세대로 교체한다. systemd를 사용하지 않는 Pod에서는 동등한 mount 구성이 필요하며 예제 파일만 복사해선 적용되지 않는다.
- `AI_CANONICAL_VERIFIED_GENERATION`은 부모가 요청별로 주입한다. 운영 env에 수동 지정하지 않는다. 전체 가중치 검증은 별도로 세대 준비 시 수행한다. 재사용 기록은 패키지 소스 검증에만 적용한다.
- 예비 세대는 두 번째 추론 슬롯이 아니다. 실행은 하나씩 처리한다. 예비 준비가 늦거나 OFF면 세대 교체 시 readiness 공백은 남을 수 있다. 메모리·CPU·I/O 경합도 개발자 확인 대상이다.

## 검증과 성능 확인

소스 패키지 생성 시 모든 생성 Python을 컴파일했다. 변경 Python 컴파일, JSON 구조·등록 식별자 정적 대조, diff 공백 검사를 수행한다. 자동 회귀 테스트·브라우저 QA·실제 분석·배포·서비스 재시작은 수행하지 않는다.

추가 계측의 `elapsedSeconds`는 단조 시계 경과시간, `cpuSeconds`는 해당 프로세스 CPU 시간이다. 가중치의 `fileBytes`는 논리 파일 크기이며 OS 디스크 읽기량이 아니다. import와 가중치 해시 기록을 비교해 cold 준비의 CPU/I/O 원인을 더 좁힐 수 있지만 파일 크기만으로 스토리지 병목을 단정하지 않는다.

개발자는 승인 후 첫 준비·warm 요청·3회 이후 세대 교체를 나눠 확인한다. warm 요청에서 version CLI·모델/G2P 생성·가중치 전체 해시가 반복되지 않는지, 요청별 근거가 섞이지 않는지, 세대 교체·취소 시 잔류 프로세스가 없는지 확인한다. 결과 준비와 종료 완료를 별도로 측정한다. 과거 준비 시간 111.151초를 뺀 산술값을 개선 실측이나 보장값으로 제시하지 않는다.
