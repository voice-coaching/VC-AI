# 운영 병목 원인과 비동기화 경계 — 2026-10-03

작업 유형: 운영 병목 분석·아키텍처 검토. 사용자 요청의 다섯 구간을 AWS/RunPod 로그, 배포 상태, 해당 revision의 소스와 대조했다. `.codex` 전역·아키텍처·API·외부 연동·운영 규칙을 적용했다. 구현·배포 작업은 이번 조사에 포함하지 않았다.

기준 측정은 [testvideo1 분석 69의 소요시간](testvideo1-pipeline-timing-20261003.md)이다. 이번 조사에서는 새 분석이나 GPT를 요청하지 않았다. 운영 소스를 변경하지 않는 별도 프로세스의 import-only 계측 1회와 모델 파일 SHA 읽기 계측, 읽기 전용 운영 조회를 추가했다.

## 현재 배포 대조

2026-10-03 23:17–23:23 KST 전후 조사. AWS Backend는 `6393d76d5ccc4896edd168d743d865577374843e`(PR #101 merge), AI는 `5a1fba59aefb3fca24df0215534eabf0c0568d5b`, bundle `d31b79fdb2a131d45e16de9adcd27831149e98d30229cbc9990d3a5afccf7b04`였다. core v2/H5 scored v3 pin과 worker ID는 앞 측정과 동일했다. 현재 readiness 통과, maintenance=false, 진행 작업 수 0을 확인했다.

Backend 원격에서 **배포 SHA를 직접 fetch**했다. 배포 SHA와 검토 checkout `149c3a5` 사이의 `src/main/java/org/example/voice/analysis` 차이는 없었다. 현재 Git main HEAD를 배포 revision으로 간주하지 않았다.

## 1. 요청 시작 → RunPod 실행 시작: 198.1초

AWS `alpha-backend.service`의 분석 69 requestId 로그:

| 시각(KST) | 실패 코드 |
|---|---|
| 22:52:20 | `runpod_http_429` |
| 22:52:53 | `runpod_http_429` |
| 22:53:56 | `runpod_http_503` |
| 22:55:30 | claim 성공(nginx 200) |
| 22:55:31.380 | outbox PUBLISHED |

**확인된 원인**:

- `HttpRuntime`은 단일 `self.active` 슬롯을 사용한다. canonical 작업이 활성 상태면 다른 요청에 429를 반환한다.
- `AnalysisRequestOutbox.recordDispatchFailure()`는 실패마다 `min(300, 30 × attemptCount)`초를 기다린다. 이번에는 **30+60+90=180초의 재시도 지연 설정**이 적용된다. `attempt_count=3`은 실패 누적이며 최종 성공 제출은 별도다.
- 앞 분석 67은 22:53:43.181 완료했다. 분석 68은 동일 시간대 429가 누적돼 5회 후 실패했다. 용량 부족이 작업 실패로도 이어지는 현재 정책의 문제다.
- `_run_canonical()`은 처리 전에 legacy executor를 종료한다. 종료 후 `_monitor()`가 legacy executor를 다시 생성한다. 그런데 `accept()`는 canonical 요청에도 legacy `ready()`를 요구한다. 필요 없는 legacy 재기동이 다음 canonical 접수 조건에 결합돼 있다.
- 슬롯은 코어 반환 때가 아니라 canonical executor 전체가 보존·검증·ACK까지 끝나고 정리된 뒤 해제된다. 앞 요청의 후처리가 다음 요청의 대기 원인이 된다.

22:53:56의 503 세부 원인이 legacy 준비인지 canonical Backend readiness인지 로그만으로 확정되지 않는다. Pod 의존성 전환 로그에는 원본 timestamp가 없어 해당 503에 직접 연결하지 않았다. **legacy readiness 결합은 소스로 확인된 구조적 문제**, 해당 503의 직접 원인은 추가 계측 대상으로 구분한다.

수정 방향: canonical/legacy readiness 분리, 추론 슬롯과 영속 후처리 소유권 분리, capacity busy를 작업 실패 횟수와 분리하고 deadline 범위 내 큐잉. 재시도는 짧고 제한된 backoff·jitter 또는 worker 가용 통지와 결합한다. 단순히 동시 실행 수만 늘리는 변경은 피한다.

## 2. 코어 준비·분석 전체: 191.3초

### 새 프로세스의 import 비용을 재현

운영 core v2의 `source/scripts/run_canonical_batch.py`를 같은 Python으로 `runpy.run_path(..., run_name='performance_import_only')` 실행했다. `main`/모델 추론/MFA/GPT는 실행하지 않았고 `-X importtime`, bytecode 쓰기 금지, HF offline 설정을 사용했다.

- 전체 import-only 경과: **83.191초**, exit 0.
- `native_infer` 누적 import: **76.442초**.
- `transformers` 누적: **20.774초**, `torch` 누적: **18.662초**.
- 누적 import는 서로 포함되므로 합산하지 않는다.
- 별도 Native 가중치 SHA-256 읽기: **4.631초**, 파일 **1,262,083,185 bytes**.

앞 측정의 설정 파일 생성 전 86.4초와 유사한 지연이 **추론 없이 재현**됐다. 요청마다 큰 Python 의존성을 새 프로세스에 올리는 것이 주요 준비 병목이다. import profile 자체의 계측 비용과 시점 차이가 있으므로 83.191+4.631을 기존 86.4초의 정확한 분해로 보지는 않는다.

Python 환경·소스·모델 경로는 `/workspace`에 있고 mount 종류는 `fuse`다. 많은 작은 파일의 import에 저장소 지연이 기여할 가능성이 있지만 로컬 디스크와의 비교를 하지 않아 기여량은 미확인이다.

### MFA도 요청마다 재준비

`scripts/run_release.py`는 요청별 MFA host를 만들고 ready까지 기다린 **다음에** AI 프로세스를 시작한다. 기존 측정에서 host 준비 **49.7초**, 그 안의 version RPC **27.852초**, worker startup RPC **19.090초**가 발생했다. worker의 CLI import만 **17.050초**였다.

개별 코어 처리 44.726초 안에서는 MFA 호출 **30.776초**가 가장 크다. alignment workflow **25.934초** 안의 feature 생성은 **18.824초**다. `resident_worker.py`가 요청별 `PretrainedAligner`, 새 corpus DB, feature 생성을 수행하는 것을 확인했다. feature 내부의 CPU/프로세스 시작/I/O 비중은 기존 타이머로 분리되지 않는다.

Native 모델 로딩 0.978초, 추론 1.045초였다. GPU 전환만으로 191초를 해소할 수 있다고 보기 어렵다. Pod cgroup 한도는 CPU **7.65 cores**, 메모리 약 **50GB**로 확인했다. 누적 throttle 카운트는 이번 요청의 CPU 병목 증거로 사용하지 않는다.

수정 방향: revision별 상주 canonical worker와 MFA worker를 준비하고 모델·import·버전 확인을 부팅 단계로 이동한다. immutable 배포 자산 hash 검증은 활성화 단계와 검증된 프로세스 생명주기에 묶어 재사용한다. 요청별 파일 digest·소유권 검증은 유지한다. 과도기에는 MFA 준비와 AI import를 병렬 시작하고 실제 MFA 호출 전 준비 완료를 합류시킬 수 있다. 기존 `parallel=false`를 단순 true로 바꾸는 것은 이 준비 병렬화와 다른 변경이다.

## 3. B2 근거 보존·AWS journal: 85.2초

분석 69의 실제 artifact는 5개, 총 **233,747 bytes(약 234KB)**다. CORE가 229,650 bytes이며 나머지 네 개는 각각 598–1,426 bytes다.

AWS nginx 로그와 소스에서 RETAIN 구간의 **journal API 33회 순차 호출**을 확인했다:

- prepare 1회 + stage 5회 + 최초 status 1회.
- artifact마다 reference status, begin용 status, begin, uploaded, readback: 5회 × 5개.
- manifest 1회.

`BackendCanonicalJournal._call()`은 매번 `asyncio.run()`과 새 `httpx.AsyncClient`를 만든다. 호출 간 연결 pool을 재사용하지 못한다. nginx 완료 시각은 대체로 2–3초 간격이다. 이 간격에는 Backend 처리, 네트워크, B2 작업이 섞여 있으므로 모두 TLS 시간으로 단정하지 않는다.

B2도 artifact마다 ACL 확인→PUT→ACL 확인→version GET을 수행한다. 정상 신규 보존 경로에서 5개에 **20회 B2 API 작업**이 직렬로 연결된다. 이 20회는 소스 경로로 산출한 횟수이며 B2 access log를 직접 집계한 값은 아니다.

원인은 큰 파일 전송보다는 **작은 상태 변경을 많은 순차 왕복으로 구현한 구조**다. 수정 방향은 persistent client, 필요한 metadata/bytes의 batch handoff, artifact 간 제한된 병렬 보존, 반복 snapshot/ACL 조회 재사용이다. 같은 artifact의 begin→PUT→version checkpoint 순서는 유지한다. DB에서 같은 journal 행을 잠그는 경계도 함께 검토해야 하며 무조건 5개 `gather()`만 적용하면 경합으로 이익이 줄 수 있다.

사용자 요구처럼 B2를 화면 대기에서 제외하려면, 완성 결과와 재시도 가능한 근거를 **Backend 영속 저장소/outbox에 한 번 인계한 뒤**, Backend 보존 worker가 B2를 처리하는 구조가 적합하다. RunPod 메모리에 task만 띄워 놓고 완료 처리하면 프로세스 정리·Pod 재시작·lease 종료로 작업이 사라질 수 있다.

## 4. 영수증 검증·확인 대기: 29.5초

receipt 등록→VERIFIED는 **17.065초**, 나머지는 등록·상태 확인과 왕복이다. verifier는 이미 별도 single-thread executor에서 돌아가며 폴링 간격은 1초다. 고정 30초 sleep이 아니다.

- Backend `CanonicalArtifactLoader`가 5개 근거를 순서대로 읽는다.
- `CanonicalEvidenceReader.readVerified()`는 매번 ACL 검사와 정확한 version GET을 한다. 한 검증에 10회 B2 읽기/ACL 작업이 예상된다.
- 이후 Python `CanonicalSemanticVerifier` 자식을 새로 시작하고 의미 검증한다.
- RunPod는 receipt 상태를 받을 때마다 `journal.received_receipt()`를 거쳐 journal GET을 다시 수행한다. 실제 로그에서 receipt POST 1회, GET 4회와 그에 수반한 journal 조회를 확인했다.
- VERIFIED DB 시각 23:00:39.861 이후에도 RunPod가 이를 확인하고 CALLBACK에 진입한 것은 23:00:49.060이었다. 이미 검증한 상태를 확인하는 왕복에도 시간이 든다.

수정 방향: receipt identity를 최초 인계 때 고정하고 매 polling의 추가 journal GET 제거, batch/제한 병렬 version 읽기, 검증한 immutable bytes와 revision-bound verification 결과의 재사용. B2를 사용자 응답 뒤로 옮기는 새 계약에서는 receipt도 보존 후처리로 이동한다.

## 5. 콜백·검증·서버 완료: 36.6초

callback inbox 등록→검증 완료 **19.806초**, 검증 완료→canonical result 적용 **11.294초**. 별도 apply worker의 최종 attempts는 **2**였다. 이 11.3초 전체를 scheduler sleep 또는 DB lock으로 단정할 span은 없다.

**확인된 중복 작업**:

- receipt에서 검증했던 같은 5개 근거를 callback verifier가 다시 B2에서 순차 다운로드하고 ACL을 확인한다. 의미 검증 Python도 다시 실행한다. callback과 근거의 결합 검증 자체는 필요하지만 원본 재다운로드·기초 검증 전체를 반복할 필요는 재설계 대상이다.
- `CanonicalCallbackService.ingest()`는 inbox에 영속 접수한 PENDING도 503으로 반환한다.
- 분석 69에서 `/result` POST는 **503 7회, 200 1회**였다. RunPod는 동일 body를 반복 제출한다.
- 검증이 완료되면 반복 POST 경로와 `CanonicalCallbackApplyWorker` 양쪽이 `ingest/commit`을 시도할 수 있다. 현재 멱등성·잠금이 중복 완료를 막지만 불필요한 재진입·경합 가능성이 있다. 이번 11.3초에서 경합이 차지한 비중은 미확인이다.

수정 방향: 영속 접수는 202 및 receipt/ack ID로 명확히 반환하고, 결과 적용은 단일 owner가 수행한다. worker는 접수 후 같은 body를 재POST하지 않고 상태 조회나 완료 이벤트를 사용한다. 불확실한 전송 결과만 동일 ID로 재조정한다. 공개 완료는 durable 결과 commit에 연결하고 cache/B2 후처리를 독립 상태로 관리한다.

## 사용자가 제안한 비동기화의 적용 경계

비동기 I/O는 대기 중 다른 일을 할 수 있게 하지만 의존 데이터 없이 다음 계산을 실행할 수 있게 하지는 않는다.

| 제안 대상 | 병렬/후처리로 옮길 부분 | 반드시 합류할 지점 |
|---|---|---|
| 초기화·예약·다운로드 | 모델/MFA 준비와 다운로드를 겹침; worker 부팅으로 초기화 이동 | 코어 실행 전에 예약/소유권과 다운로드·digest 검증 완료 |
| GPT 채점·검증 | 계산기가 rubric을 만든 뒤 독립 코칭 생성·근거 staging 등과 겹침 | 사용자에게 검증된 최종 점수를 제공하기 전 GPT 결과·검증 완료 |
| B2·journal | 결과/근거의 batch 영속 인계 후 보존 작업을 durable worker로 분리 | 사용자 응답 전 최소 영속 인계와 결과 유효성 검증; B2 완료는 기다리지 않음 |
| receipt | B2 보존 검증으로 후처리 | archive 상태 확정 전 |
| callback 적용 | 같은 결과 503 재POST를 없애고 접수와 적용 분리 | 분석 결과를 한 번만 확정하는 DB commit |

GPT를 기다리지 않고 점수를 먼저 표시하려면 서버 계산기 점수를 즉시 확정하고 GPT를 사후 작업으로 취급하는 **별도 제품·실패 계약 변경**이 된다. 현재 사용자가 정한 GPT 검산 실패 시 unscorable 정책을 이번 조사에서 임의로 바꾸지 않는다. GPT 구간은 이번 표본에서 5.3초이므로 먼저 수십~수백 초의 구조적 비용을 해결하는 편이 효과가 크다.

현재 v4 callback은 VERIFIED receipt가 있어야 직렬화·적용할 수 있다. 따라서 B2를 사용자 완료 뒤로 옮기는 것은 `await` 제거만의 변경이 아니다. AI–Backend의 결과 인계 계약, Backend의 완료 조건·저장소/outbox, FE의 완료·점수 표시 계약을 함께 맞춰야 한다. 최종 결과와 보존 상태를 분리하고 보존 실패가 점수를 소급 삭제하거나 AI를 재실행하지 않도록 한다. 실패 후처리는 같은 실행 ID·digest로 재시도하고 운영에서 관찰 가능하게 한다.

## 수정 순서와 검증 기준

1. canonical readiness를 legacy 재기동과 분리하고 busy 재시도/실패 정책 수정. 429로 음성 자체가 실패 처리되지 않고 idle 시 다음 canonical 요청이 legacy 로딩을 기다리지 않는지 확인.
2. 상주 core/MFA 프로세스, 배포 단위 import/자산 검증 준비. 같은 digest 입력의 기존 점수·근거·정렬 결과 보존과 재시작 복구 확인.
3. 빠른 결과 인계·공개와 보존을 분리하는 버전 있는 계약을 양쪽에 구현. 결과 commit과 보존 outbox를 함께 영속화하고, 완료 직후 RunPod 종료 상황에서도 근거 보존이 복구되는지 확인.
4. journal batch/연결 재사용, artifact 간 제한 병렬화, receipt/callback 중복 다운로드·검증 축소. GET/PUT 횟수 및 개별 span을 기록해 개선량 확인.
5. MFA feature 생성 18.8초 및 전체 30.8초의 세부 계측. 요청별 DB·모델 준비·feature 하위 작업을 분리한 다음 결과 동등성을 유지하는 범위에서 수정.

현재 수치에서 단순히 85.2+29.5+36.6초를 빼서 개선 후 시간을 약속하지 않는다. 새 durable 인계·최소 결과 검증 비용이 생기며 대기열과 프로세스 준비도 함께 변한다. 실제 개선 후 같은 testvideo1을 무부하와 운영 동시 요청 조건으로 각각 측정해야 한다.

## 주요 수정 후보 소스

- intelligentAI: `src/voice_coach/backend_analysis/http_runtime.py`, `canonical_lane.py`, `canonical_executor.py`, `canonical_backend_journal.py`, `canonical_retention.py`, `canonical_receipt_client.py`, `canonical_backend_delivery.py`, `canonical_evidence_store.py`.
- frozen core v2: `scripts/run_release.py`, `source/scripts/run_canonical_batch.py`, `runtime_bundle/resident_worker.py`. 현재 pin을 직접 덮어쓰기보다 새 revision으로 배포해야 한다.
- VC-BE: `AnalysisRequestOutbox`, `RunPodAnalysisRequestOutboxDispatcher`, `CanonicalArtifactLoader`, `CanonicalEvidenceReader`, `CanonicalEvidenceWorker`, `CanonicalCallbackVerifierWorker`, `CanonicalCallbackService`, `CanonicalCallbackApplyWorker`, `CanonicalCallbackCommitter`.

미확인: 개별 journal RTT의 TLS/DB/CPU 비중, MFA feature 하위 병목, callback 적용 11.3초의 lock/DB/worker 비중. 현재 로그에는 이 span이 없어 원인을 추측으로 확정하지 않았다. 이번 산출물은 조사 문서이며 운영 코드 변경·commit·push·PR·재배포는 하지 않았다.
