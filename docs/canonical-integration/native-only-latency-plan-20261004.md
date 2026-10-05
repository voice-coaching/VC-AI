# Native/P1 전용 전환 및 요청 초기화 지연 제거

상태: Native 전용 소스와 bounded resident DRAFT 구현 및 정적 확인 완료. 보정·개발자 QA·배포·실측 미완료. [구현 현황](native-full-phone-implementation-20261004.md)을 따른다.
기준: intelligentAI main bbaf5f43a91c925781bd6ac4c0ba75d56f8a298a.
사용자 결정: 구버전 Seungun 실행 소스를 제거한다. 성능 수정은 우선 계획으로 구체화한다.

## 확인된 현재 상태

- 운영 코어는 canonical_scored_core_20261003_v2, H5는 canonical_scored_h5_20261003_v3다.
- 9월 28일 정본 manifest SHA는 95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2다. 현재 코어의 parent_manifest_sha256과 일치한다.
- Native/P1은 교정 근거다. 기존 final_pronunciation_detector_v1은 별도 전체 음소 채점 근거다. 분석 74에서 두 경로가 함께 실행됐으며, 구버전 검출기의 음소 행 28개가 존재했다.
- 9월 28일 정본의 final_pronunciation.enabled=False를 build_canonical_scored.py가 True로 변경한다. canonical_resident_server.py는 같은 검출기를 사전 로드한다.
- gpt_rubric.score_core는 core_result.pronunciation의 구버전 음소 행을 사용한다. 현재 계약은 근거가 없으면 SCORING_UNSCORABLE로 분석을 실패 처리한다. 검출기만 제거하고 현재 채점 계약을 유지하면 정상 입력도 실패한다.
- 9월 28일 Native/P1의 현재 공개 근거만으로 기존 9항목 검산표를 만들 수 있다고 검증된 바 없다. 모음·삭제 근거를 임의로 만들어서는 안 된다.

## 확정된 점수 방향

2026-10-04 사용자 결정: 최신 Native 모델 기반 전체 음소 채점 근거를 새로 설계한다. 점수 미제공을 정상 성공으로 허용하는 대안은 선택하지 않았다.

[Native 전체 음소 채점 근거 설계](native-full-phone-scoring-design-20261004.md)를 따른다. 단일 Native CPU FP32 추론을 P1 코칭과 전체 음소 채점이 공유하고, 기존 검출기 점수·임계값을 이식하지 않는다. 모음 token mapping, 삭제·모호성 처리, 신규 음향 판정의 보정과 held-out 검증을 거친 후 운영 전환한다. 기존 9항목 배점·서버 산술은 유지하되 근거 의미가 달라지므로 rubric revision은 새로 등록한다.

## P0: 구버전 실행 소스 제거

### 제거 범위

- 배포 빌더의 구버전 활성화 변환과 구버전 모델 warmup을 삭제한다.
- 새 Native 전용 release에서 final_pronunciation_detector.py, final_pronunciation_artifacts.py의 구버전 모델 로더·추론·검출기 자산 의존성을 제거한다. pipeline의 runner 주입·실행 분기도 제거한다.
- 최신 P1/MFA 코드가 공유하는 결과 타입·음소 계약은 모델 구현과 분리한다. 단순 파일 삭제로 ai_core/pipeline의 import가 깨지지 않도록 참조를 먼저 옮긴다. 빈 구현이나 항상 실패하는 stub으로 구버전 모듈을 남기지 않는다.
- 구버전 checkpoint·processor·detector를 새 실행 패키지와 배포 자산 목록에서 제외한다.
- 실행용 release 등록과 과거 결과 검증용 identity 목록을 분리한다. 과거 저장 결과를 읽는 것은 구버전 추론 재실행 권한이 아니다.
- 새 작업의 readiness, 일반 producer, resident producer 모두 Native 전용으로 승인된 manifest만 허용한다. 설정을 옛 디렉터리로 되돌려도 신규 추론이 시작되지 않아야 한다. fallback은 두지 않는다.
- bootstrap의 scored/resident 구버전 기본 경로를 제거하고 새 core/H5/schema tuple을 함께 등록한다.

### 주요 대상

| 파일/구성 | 변경 책임 |
|---|---|
| deploy/runpod/build_canonical_scored.py | 구버전 재활성화·패키징 경로 제거, Native 전용 빌더로 전환 |
| deploy/runpod/build_canonical_resident.py | 구버전 scored parent 의존 제거 |
| deploy/runpod/harnesses/canonical_resident_server.py | 구버전 모델 import 및 warmup 제거 |
| frozen source pipeline/ai_core 및 결과 타입 | 구버전 실행 구현 제거, 필요한 계약 타입 분리 |
| canonical_harness.py / canonical_bridge.py | 실행 허용 identity와 보존 결과 검증 identity 분리 |
| canonical_producer.py / canonical_executor.py | readiness 및 실행 시작 전 Native 전용 identity 확인 |
| deploy/runpod/prepare_canonical.py | 구버전 자동 선택 제거 |
| gpt_rubric.py / phone_rubric.py / H5 bridge | 선택된 신규 점수 계약에 맞게 근거 소비 변경 |
| BE verifier 및 FE 결과 파서 | 변경된 identity·점수 상태의 호환성 확보 |

### 삭제와 보존의 구분

새 실행 패키지에는 구버전 실행 코드를 포함하지 않는다. 기존 frozen release·로그·기존 결과는 감사 자료이므로 실행 등록에서 제외하되 운영 스토리지에서 즉시 파괴하지 않는다. 보존 산출물 삭제는 신규 소스 정리와 별개의 작업이다. 실행 중 release를 제자리 수정하지 않는다.

## P1: 요청마다 반복되는 초기화 제거

분석 71의 CORE 197.492630초는 MFA host 준비 58.881557초, 이후 AI 설정 준비 90.739859초, 실제 코어 처리 37.667453초, 미분리 잔여 10.203761초다. 앞의 두 준비가 75.8%를 차지한다.

1. Native 전용 worker 한 세대를 서비스 시작 시 준비한다. 모델·P1 정책·MFA 버전·자산 무결성 검증을 해당 세대의 고정 identity에 연결한다. 요청별 원본 SHA·소유 관계·실행 ID 검증은 유지한다.
2. 독립적인 Native import/모델 준비와 MFA bootstrap을 병렬 시작한다. 둘이 모두 준비된 후에만 readiness를 연다. 모델 간 추론 병렬화는 이 단계에 포함하지 않는다.
3. 요청마다 run_release.py와 blackbox_host.py를 새로 시작하지 않고 준비된 worker에 요청한다. MFA version subprocess는 세대 준비에서 한 번만 실행한다. 분석 71의 version 실행은 32.065405초였다.
4. 처음에는 기존 max_requests=3과 RSS watchdog을 유지한다. 세대 종료 시 자식 프로세스 회수·socket 정리를 확인한 뒤 다음 세대를 준비한다. 무제한 상주로 바꾸지 않는다.
5. 세 번째 요청 뒤의 재기동 비용을 숨기지 않는다. 다음 세대 준비 중에는 명시적으로 admission을 닫거나 제한된 대기 상태를 제공한다. 두 세대 사전 준비는 메모리 예산 확인 후 별도 결정한다.
6. resident 장애 때 요청별 구버전 launcher로 fallback하지 않는다. 원인 코드를 반환하고 같은 execution을 몰래 재추론하지 않는다.

## P2: 잔여 비용의 근거 기반 분해

- 기존 계측에서 90.740초는 import·자산 hash·설정 준비가 섞인 구간이다. 비운영 환경에서 import 종료, 자산 검증 종료, Native load, G2P 준비, READY를 구분한다.
- MFA 준비·feature·alignment, 큐 입장/실행, H5/GPT, 결과 공개 ACK를 서로 중첩하지 않는 span으로 수집한다. 중첩 타이머에는 parent span을 명시한다.
- /workspace가 FUSE라는 사실만으로 지연 원인이라고 단정하지 않는다. 파일별 I/O 근거가 있을 때만 읽기 전용 자산의 로컬 사본을 검토한다. /tmp 요청 scratch는 유지한다.
- Native/MFA 추론 병렬화는 후순위다. 현재 runner에는 보존된 120초 실패 이후 parallel 금지 guard가 있다. 원인·격리·출력 동등성 확인 없이 guard를 삭제하지 않는다.
- B2 archive 및 DB 보존을 사용자 결과 대기 경로로 되돌리지 않는다. 관측된 core 초기화 문제를 해결하기 위해 HTTP timeout을 늘리지 않는다.

## 검증 및 인계 기준

- 저장소 규칙에 따라 에이전트는 소스 정적 점검과 필요한 컴파일만 수행한다. 실제 추론·회귀 QA는 개발자가 수행한다. 이 문서는 실행된 테스트 결과가 아니다.
- 정적 기준: 새 배포 패키지에 구버전 모델 loader/추론/자산이 없고, 신규 접수가 과거 scored/resident identity를 허용하지 않는다. 과거 결과 조회 검증은 유지한다.
- 개발자 QA: 동일 입력으로 최초 준비, 첫 요청, warm 2·3번째 요청, recycle 후 요청을 구분한다. Native/P1/MFA/prosody 출력 동등성 및 요청 간 오염·취소·중복·메모리 회수를 확인한다.
- 운영 인계 전: 선택된 점수 계약, BE/FE 호환성, 새 불변 manifest와 schema pin, 이전 요청의 완료 처리, 구버전을 다시 실행하지 않는 복구 경로를 확인한다.
- 성능 수용 기준은 먼저 'warm 요청에 version subprocess·모델 재로드·AI/MFA bootstrap이 발생하지 않는다'로 둔다. testvideo1 전체 15초 달성을 보장하지 않는다. 과거 14.25초는 다른 입력의 warm core 측정이며 HTTP/LLM을 포함하지 않는다.

## 현재 작업 상태

- 격리 브랜치 refactor/native-only-core-20261004 생성.
- 구버전 활성화·사전 로드·채점 소비·배포 기본 경로 확인.
- 사용자 선택 반영 및 Native 전체 음소 채점 설계 작성 완료. token mapping·판정 threshold의 검증과 구현은 남아 있다.
- 제품 코드 삭제, frozen release 생성, 테스트, commit/push/PR, 운영 변경·재시작·배포는 아직 수행하지 않았다.
