# 지연 개선 구현 현황 — 2026-10-04

상태: 로컬 코드 구현·컴파일. 운영 배포·추론 QA·속도 재측정 전이다. [전체 계획](latency-improvement-implementation-plan-20261003.md)의 완료 기록이 아니다.

## 구현된 경계

- `http_runtime.py`: canonical readiness를 legacy executor 기동에서 분리한다. canonical 구성에서는 legacy 요청 전까지 legacy를 띄우지 않는다. 실행 중 새 요청은 준비 상태보다 capacity 429를 우선한다. 동일 요청의 멱등 조회는 capacity 검사 전에 처리한다.
- `canonical_executor.py`, `canonical_async.py`: journal 예약과 녹음 materialize를 병렬 실행하고 둘 다 성공·fence 확인 후 producer를 한 번 시작한다. 취소 시 blocking I/O가 끝나기 전에 client나 scratch를 회수하지 않는다.
- `journal_http.py`, `canonical_backend_journal.py`: execution당 전용 event loop와 HTTP pool을 재사용한다. start/PUT의 불확정 결과를 자동 재전송하지 않는다.
- `canonical_retention.py`: 운영 Backend journal 경로는 최대 2개 artifact를 병렬 처리한다. 각 artifact의 begin → PUT → version checkpoint → exact-version readback 순서는 유지하며 형제 작업 실패 시 새 업로드를 차단한다. 기존 다른 journal 호출자는 기본 동시성 1이다.
- `pipeline_timing.py`: UTC와 monotonic elapsed, analysis/request/execution ID, revision, stage, 결과 코드를 기록한다. 대본·음성·키·URL은 포함하지 않는다.
- Backend companion: 확인된 `CAPACITY_EXCEEDED`만 busy로 분리, endpoint DB gate와 FIFO 대기, 기한 만료·terminal 취소, 검증 read 2개 병렬화, VERIFIED manifest의 정확한 원본만 DB spool에서 재사용, callback 의미 검증 유지, 구간별 시간 기록.
- FE companion: status의 `deadlineAt`/`serverTime`으로 대기 시간을 맞추고, 대기·통신 오류 후 같은 analysisId를 조회한다. 기존 서버에는 10분 local fallback을 유지한다. local 대기 종료를 서버 FAILED로 바꾸지 않는다.

## 별도 resident 패키지

`build_canonical_resident.py`는 기존 scored core v2와 H5 v3를 SHA 검증하여 새 디렉터리에 파생 패키지를 만든다. 기존 패키지를 덮어쓰지 않는다. Native/Seungun 모델과 Python import를 준비하고 MFA 기동을 병렬 진행한다. 요청별 corpus·state는 기존 분석 경로에서 새로 만들며 CPU/dtype/모델·채점표·프롬프트를 바꾸지 않는다.

| 식별자 | SHA-256 |
|---|---|
| core `canonical_resident_core_20261004_v1` | `ce16f635da012237b5316b36161d5eeff9432279cd08dbbe7967f5ab7ace7f7e` |
| H5 `canonical_resident_h5_20261004_v1` | `885a2f0a67494dc3bebed28962222bfe95a0143292fe2de04194b84ea8beadc5` |
| H5 dependency lock | `1945dcf2fad7fc97621830abed34353c2d372d8224cc9ad11a9c422eabfdf9f4` |

`AI_CANONICAL_RESIDENT_ENABLED=true`를 명시해야 선택한다. 기본은 OFF다. 이 모드는 canonical 전용이며 legacy 요청은 NOT_READY다. OFF에서는 기존 legacy 지원을 유지한다. `prepare_canonical.py`가 이 flag에 맞춰 core/H5 root를 고정하고, 부모 `CanonicalResidentCore`가 비밀 없는 별도 guard/process subtree를 관리한다.

**현재 상한은 세대당 3개 요청, 전체 활성 execution 1개**다. 기존 MFA 수명 제한을 무제한으로 풀지 않았다. 3번째 요청 뒤에는 세대를 종료·회수하고 새로 준비하므로 다음 세대에서 cold 준비 비용이 발생한다. 초기 READY 또는 회수 확인 전에는 새 추론을 받지 않는다. inference/GPT의 불확정 결과는 재실행하지 않는다. 요청 취소·예외는 resident subtree를 회수하며 회수 불명확 시 admission을 닫는다.

Unix socket 입력은 execution/request ID, deadline, case/audio digest만 받는다. 입력·출력 경로는 서버 workspace에서 유도한다. generation 로그는 private `/tmp/vc-core-*`에 남고 자동 삭제 정책을 추가하지 않았다. 운영자는 저장 공간과 재기동 횟수를 감시해야 한다.

Backend offline verifier의 파생 pin, v4 schema의 허용 identity tuple, exporter/stager를 함께 갱신했다. schema의 결과·receipt 의미를 바꾼 것은 아니다. **새 verifier bundle 설치와 양쪽 schema digest 일치가 먼저 필요하다.** 기존 frozen 패키지들도 bundle에 유지한다. Backend verifier가 읽는 원본은 여전히 기존 v4 B2 검증 조건을 충족해야 한다.

## 후속 구현과 남은 범위

- P3/P4: [v5 영속 인계·archive worker](handoff-v5-20261004.md)에 후속 코드를 추가했다. 배포·개발자 수용은 별도다.
- P2 후속: 추론 1개 + GPT/인계 1개를 겹치는 execution별 context, 3회 제한 이후의 장기 상주 수명 검증.
- P5 후속: 새 profile capability/FE parser, MFA feature 내부의 추가 계측·실측에 따른 변경.

기존 v4는 **B2 보존·receipt·callback 완료를 기다린다**. 새 v5는 모든 원본의 영속 인계·의미 검증 후 결과를 확정하고 B2를 후처리한다. 별도 배포와 명시적 활성화가 필요하다.

## 확인과 인계

Python 구문 컴파일, 새 core/H5의 파일 SHA 대조·컴파일, AI/Backend schema bytes 일치, Backend `compileJava`, FE `tsc --noEmit --incremental false`를 수행했다. 자동 회귀 테스트·fixture·브라우저 QA·실제 추론은 수행하지 않았다. 90초 목표 달성 또는 기존 점수 70.8 재현을 확인한 기록이 아니다.

배포 순서는 새 frozen verifier bundle → Backend V38 및 JAR → 동일 schema의 AI overlay → resident 패키지 설치 → 개발자 수용 확인 후 resident flag ON이다. FE deadline 변경은 구 Backend에도 호환된다. 동작 중 profile/worker를 교체하지 말고 drain한다. rollback은 resident flag OFF와 기존 패키지 선택으로 수행하며 새 raw evidence나 DB row는 삭제하지 않는다.
