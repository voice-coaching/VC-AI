# testvideo1 운영 파이프라인 소요시간 — 2026-10-03

작업 유형: 운영 측정·성능 분석. `.codex/ai_rule_developer/GLOBAL_RULES.md`, `.codex/runpod.md`, `.codex/backend-http.md`를 적용했다. 사용자가 요청한 실제 음성 분석 1회를 실행했으며 서비스 코드·설정·배포는 변경하지 않았다.

## 결과와 측정 범위

- 2026-10-03 22:52:12.686 KST 분석 POST 시작 → 23:01:25.657 서버 완료: **552.972초(9분 13.0초)**.
- 완료 상태를 테스트 클라이언트가 받은 시각: 23:01:28.093, **555.407초(9분 15.4초)**.
- 세션 118 / 녹음 83 / 분석 69. `COMPLETED`, `ACCEPT`, 점수 **70.8**, `RUBRIC_COMPUTED`, `canComplete=true`.
- requestId: `7b7bfdae-d0c8-4b1b-9ed1-b5574002d670`.
- executionId: `5acbb1ce-68df-48ec-876f-faecd4b6417e`.
- callback eventId: `5a46a560-4d1b-438a-ae29-4cc3c2d7f6d6`.
- scoring `READY`. 코칭은 `NO_PERMITTED_COACHING_CONTENT` / `NOT_DISPATCHED`이므로 이번 GPT 구간에 상세 코칭 생성은 포함되지 않는다.

testvideo1.mp4에서 추출한 4.843초 PCM 16 kHz mono 음성을 새로 업로드했다. 원본 영상 SHA-256은 `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`, 추출 WAV는 `3ec858673030f563fc5deef5d0bed4aac507a998c72545ead5959fab2f62515a`다. Backend 정규화 파일 SHA-256은 `9eb81a5d38625566aead94150268c07df0f74550126a1ccbbcebb36abc9e6807`로 확인했다.

이번 측정은 **음성 canonical v4 경로**다. 영상 업로드·입술 분석·TTS는 실행하지 않았다. RunPod 안의 테스트 클라이언트가 `https://vc-fe.vercel.app/api/backend`를 통해 AWS API를 호출했다. 실제 사용자 PC의 네트워크, 브라우저 녹음·렌더링 지연은 측정하지 않았다.

## 분석 요청 이후 시간 분해

아래 구간은 중복 없이 POST 시작부터 클라이언트 완료 확인까지를 나눈 것이다. 서버 간 UTC 시각 비교와 로그 관찰 오차가 있어 소수 첫째 자리로 표시한다.

| 구간 | 시간 | 측정 근거 |
|---|---:|---|
| FE 프록시 요청 시작 → RunPod 실행 파일 생성 | **198.1초** | 클라이언트 POST 시작과 `request.json` mtime. 접수·dispatch 대기·실행 시작 포함 |
| executor 초기화·journal 예약·음성 다운로드 | **5.5초** | request mtime → CORE 로그가 마지막이던 시점의 executor.log mtime |
| CORE 전체 | **191.3초** | CORE 로그 mtime → EXPRESSION 실시간 관찰 |
| H5 채점 응답 작성·GPT 호출·검증 | **5.3초** | EXPRESSION → PREFLIGHT. GPT 서버 처리 시간만의 계측은 아님 |
| 결과 사전 검증 | **1.4초** | PREFLIGHT → RETAIN |
| 근거 보존·B2 업로드/readback·AWS journal | **85.2초** | RETAIN → RECEIPT |
| 영수증 등록·Backend 검증·확인 대기 | **29.5초** | RECEIPT → CALLBACK |
| 콜백 작성·전달·검증·서버 완료 반영 | **36.6초** | CALLBACK → DB/공개 상태의 완료 시각 |
| 서버 완료 → 클라이언트 완료 인지 | **2.4초** | 공개 완료 시각 → 상태 polling 응답 |
| 합계(반올림 전) | **555.407초** | 요청 시작 → 클라이언트 완료 인지 |

RunPod가 콜백 ACK까지 확인하고 `completed.json`을 작성한 시각은 23:01:28.559 KST다. CALLBACK부터 ACK 확인까지는 약 **39.5초**이며 위 36.6초와 별도로 더하지 않는다. 분석 결과 상세 조회 자체는 **3.239초**였다. 테스트 도구는 완료 확인 뒤 재로그인과 상태 재조회도 했으므로 상세 응답 수신 시각을 그대로 UI 지연으로 해석하지 않는다.

접수 응답의 requestedAt(22:52:14.759)부터 RunPod 작업 파일 생성(22:55:30.749)까지는 **196.0초**다. AWS outbox는 22:52:15.278 생성, 22:55:31.380 published, `attempt_count=3`, 최종 `last_error_code=null`이다. 측정 전부터 다른 운영 요청이 처리 중이었으며, 배포 코드의 RunPod admission은 활성 canonical 작업이 있으면 `429 CAPACITY_EXCEEDED`를 반환한다. 따라서 슬롯 경쟁과 dispatch 대기가 유력하다. 이번 실행의 과거 실패 HTTP 코드별 로그는 확보하지 못했으므로 196초 전체를 특정 429 횟수로 단정하지 않는다.

## CORE 191.3초 내부

| 구간 | 시간 | 해석 |
|---|---:|---|
| producer 예약·검증·MFA host 실행 준비 | 4.4초 | CORE 진입 → host PID 파일 |
| MFA host 준비 | **49.7초** | host PID → ready 검증 파일 |
| AI 프로세스 기동 → 실행 설정 파일 생성 | **86.4초** | ready 검증 → effective_config mtime |
| 설정 생성 → 개별 요청 처리 시작 | 1.1초 | 이 안의 Native 모델 로딩은 **0.978초** |
| 개별 음성 코어 처리 | **44.726초** | `core_json_done - request_start` |
| 결과 기록·프로세스 종료·producer 반환 | 5.1초 | 코어 계산 종료 → EXPRESSION |

MFA host 준비에는 버전 확인 RPC **27.852초**, worker 시작 RPC **19.090초**가 포함된다. worker 시작 안의 CLI import는 **17.050초**다. 이 값들은 중첩된 계측이므로 전부 합산하지 않는다.

86.4초에는 새 AI 프로세스의 Python import, 계약/모델 파일 SHA 검증, 설정 구성 등이 들어간다. 기존 계측으로는 각 비중을 분리할 수 없다. **86.4초를 모델 로딩 시간으로 부르면 틀리다.** 별도로 계측된 Native 모델 로딩은 0.978초다.

개별 코어 44.726초 중 확인 가능한 부분:

- 음성 디코딩·기초 검사: **0.227초**.
- Native Wav2Vec2 추론: **1.045초**.
- MFA 호출: **30.776초**. MFA 내부 작업 전체 30.475초, alignment workflow 25.934초이며 그 안의 feature 추출은 18.824초다.
- prosody 함수: **0.023초**.
- 나머지 약 **12.7초**는 G2P, Seungun 전체 음소 채점 근거 생성, 정렬 준비·후처리 등이다. Seungun만의 독립 타이머가 없어 이 값을 전부 Seungun 시간으로 표시하지 않는다.

`stage_durations_s`의 대부분은 `request_start` 기준 **누적 시각**이다. 표는 `timing_marks_monotonic` 차이를 사용했다. 특히 `prosody_done`은 전체 core 반환 시점이므로 prosody 단독 지연으로 해석하지 않았다.

Native와 Seungun final_pronunciation의 실제 설정은 모두 `device=cpu`이며 Native는 float32, 병렬 옵션은 false다. 22:57:01 이후 약 10초 간격의 GPU 관찰 27회는 모두 utilization 0%, 메모리 1203 MiB였다. 준비 초반은 관찰하지 못했으며 짧은 GPU 사용을 이 샘플링으로 완전히 배제할 수는 없다. CPU 설정만으로 GPU 전환의 효과나 정확도 동등성을 보장할 수 없다.

## 업로드와 공개 API

| 작업 | 클라이언트 왕복 시간 |
|---|---:|
| ffmpeg 음성 추출 | 0.094초 |
| 학습 세션 생성 | 1.683초 |
| 업로드 URL 발급 | 1.339초 |
| 음성 바이너리 업로드 | 1.843초 |
| 녹음 등록·서버 처리 응답 | 2.939초 |
| 녹음 선택 | 1.658초 |
| 분석 POST 접수 응답 | 4.452초 |
| 분석 상태 GET | 평균 1.296초, 중앙값 1.343초, 범위 1.150–1.479초(96회) |
| 최종 분석 결과 GET | 3.239초 |

업로드 URL 발급+업로드+등록은 **6.121초**다. 업로드 준비와 분석 제출 사이의 측정 준비 대기 약 3분은 분석 소요시간에서 제외했다. 상태 polling은 응답 후 2초 간격이며 처리와 겹치므로 전체 RTT를 분석 시간에 더하지 않는다. 위 왕복 시간에는 네트워크·FE 프록시·AWS 처리가 섞여 있고 각각의 독립 server span은 없다.

## 결과 보존·검증의 지연

AWS DB에서 확인한 세부 시각(KST):

| 이벤트 | 시각 |
|---|---|
| receipt 등록 | 23:00:22.796 |
| receipt 검증 완료 | 23:00:39.861 |
| callback inbox 등록 | 23:00:51.524 |
| callback 검증 완료 | 23:01:11.331 |
| canonical result 적용·ACK 기록 | 23:01:22.625 |
| analysis 완료 | 23:01:25.657 |
| result effects 완료 | 23:01:27.366 |

receipt 등록→검증은 **17.065초**, callback 등록→검증은 **19.806초**, callback 검증→결과 적용은 **11.294초**다. 모두 최상위 구간에 포함된 값이다. receipt와 callback 검증 시도는 각각 1회였다.

소스상 `canonical_backend_journal.py`는 각 요청마다 새 `httpx.AsyncClient`를 생성하며, artifact별 stage/status/begin/uploaded/readback 호출을 순차 수행한다. `canonical_retention.py`, `canonical_evidence_store.py`는 B2 저장과 정확한 버전의 readback을 포함한다. Backend의 receipt 및 callback 검증도 별도다. **85.2초 전체를 B2 전송 또는 GPT 시간으로 설명할 수 없다.** 저장·검증 안전성을 유지하면서 왕복 횟수와 연결 재사용을 개선할 후보가 있다.

## 개선 우선순위 제안

1. **요청마다 반복되는 CORE 준비부터 분리 계측하고 상주화 검토.** MFA 준비 49.7초, AI 초기 준비 86.4초가 실제 음성 코어 44.7초보다 크다. 버전·digest 검증 결과를 안전하게 재사용하고 모델/MFA worker를 준비된 상태로 유지하는 구조를 검토한다.
2. **보존·검증 왕복 비용 개선.** RETAIN 85.2초와 receipt 29.5초, callback/완료 36.6초에 HTTP 연결 재사용, 중복 상태 조회, scheduler 대기, 동일 immutable evidence 검증 재사용 가능성을 각각 계측한다. 무결성·영속성 검증을 단순 삭제하지 않는다.
3. **슬롯 점유와 dispatch 대기 개선.** 운영 중 196초의 접수 후 대기가 있었다. 상주화·후처리 분리 후 실제 CPU/메모리 한도를 확인해 동시성이나 worker 수를 결정한다. 현재 직렬 제한을 무조건 해제하지 않는다.
4. **CPU→GPU는 별도 비교 대상으로 취급.** Native 추론은 이번에 1.0초라서 이것만 GPU로 옮겨도 전체 지연을 크게 줄이기 어렵다. 먼저 준비 비용과 Seungun 독립 구간을 분리한다.

이번 요청에서는 최적화를 구현하거나 재배포하지 않았다.

## 재현성·계측 한계

- Backend: `6393d76d5ccc4896edd168d743d865577374843e` JAR, 배포 stage COMPLETE, maintenance=false.
- AI 런타임 소스: `5a1fba59aefb3fca24df0215534eabf0c0568d5b`.
- RunPod bundle: `d31b79fdb2a131d45e16de9adcd27831149e98d30229cbc9990d3a5afccf7b04`.
- Pod: `s8hb5b0l5k4qlx`, worker `bfdb81b6-6691-4fb3-95ad-f67bc1634178`.
- scored core v2 manifest: `08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80`.
- H5 scored v3 manifest: `e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3`.
- 실행 1회이며 다른 사용자 작업이 존재한 운영 환경이다. 무부하 평균·p95가 아니다.
- executor의 stage 로그에는 자체 timestamp가 없다. 첫 관찰기의 request 필드 오류 후 22:57:01에 재개했다. 초반 RESERVE/MATERIALIZE/CORE의 재수집 시각은 계산에 사용하지 않았다. request mtime, CORE까지만 기록됐을 때 읽은 executor.log mtime(Unix `1791035736.2177918`), journal DB 시각으로 초반을 보완했다. 예약과 다운로드 각각은 분리할 수 없다.
- EXPRESSION 이후는 0.2초 polling 관찰이다. 파일 읽기·GPU 관찰 명령 비용 때문에 0.2초가 엄밀한 최대 오차는 아니다. 서버 간 시계 오차도 별도 교정하지 않았다.
- 로컬 선택 자료: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/pipeline-prerequisites-20261003/testvideo1-performance-{observation,metrics,client}.json`. 인증정보를 제외한 관찰값이며 다른 clone에 필수인 자료는 아니다.
- 문서와 수치의 정적 대조를 수행했다. 실제 추론 1회 성공은 확인했지만 브라우저 QA, 피드백 품질 QA, 부하 테스트는 수행하지 않았다. 코드 수정·commit·push·PR·배포는 하지 않았다.
