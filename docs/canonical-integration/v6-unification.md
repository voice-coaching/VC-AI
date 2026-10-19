# 내부 분석 v6 통합

## 현재 소스 계약

신규 내부 분석은 request `voice-coaching.runpod-analysis-request.v4`, result
`voice-coaching.runpod-analysis-result.v6`, profile `CANONICAL_AUDIOVISUAL_20261007_V6`,
handoff `voice-coaching.canonical-handoff.v2` 하나를 사용한다. profile 이름은 기존
wire 식별자이며 음성 전용도 포함한다. 버전 번호는 요청·결과·인계마다 독립적이다.
다른 request/result/profile 조합은 신규 접수하지 않는다.

| 입력 | 요청 | 결과와 실제 근거 |
|---|---|---|
| 음성 전용 | `video=null`, `mediaPreparation=null` 또는 실제 음성 preparation | `visual.status=NOT_PROVIDED`, 영상 SHA·영상 근거 SHA=null, 영상 근거 artifact 없음 |
| 음성·영상 | 등록된 video와 같은 원본의 `mediaPreparation` 필수 | 검증된 실제 영상 근거·receipt·association v2, 관찰/실패 상태를 보존 |

음성 입력에 가짜 영상·normalization receipt·시간축을 생성하지 않는다. 음성 전용의
visual은 `score=null`, `scoreKind=NONE`, `correctiveClaimsAllowed=false`, coverage
각 count=0/coverage=null, 상세·미확정 index 목록=[], 생략 count=0,
`reasonCodes=[VIDEO_NOT_PROVIDED]`다. 음성 preparation이 실제 제공되면 PCM digest와
receipt digest를 검증하고 `MEDIA_RECEIPT`로 인계한다. 없으면 receipt SHA도 null이다.

영상 입력은 기존 source/PCM/video digest·PTS/MFA·같은 시도 결속을 유지한다.
`NOT_PROVIDED`를 영상 실패·timeout·unscorable의 대용으로 쓰지 않는다. 실제 영상이
없는 요청은 영상 모델·reference catalog·영상 전용 readiness를 요구하지 않는다.
영상 요청은 공통 v6 readiness와 영상 실행 identity·자산·설정·Backend 영상 readiness가
모두 준비되어야 한다. 이 변경은 모델·frozen core/H5를 수정하거나 신규 실행 identity를
승인하지 않는다. 현재 음성용 허용 identity도 v6 음성 결과를 작성한다.

공개 익명 direct는 UUID job/audio 식별자와 `voice-coaching.direct-analysis.v1`
응답을 가진 별도 API다. Backend 정수 ID 기반 내부 결과로 위장하지 않는다.
내부 wire 버전 통합 때문에 direct wrapper·SSE·이력 동기화 버전을 올리지 않는다.

## 구현 경계

- [요청 검증](../../src/voice_coach/backend_analysis/http_models.py)은 request v4만 받고
  음성·영상 여부는 실제 video 유무로 구분한다. payload를 v5에서 v6로 변환하거나
  digest를 다시 매기지 않는다.
- [통합 결과 경계](../../src/voice_coach/backend_analysis/canonical_audiovisual_result.py)는
  원본 음성 검증을 재사용하며 nullable 영상 결속과 association v2를 검증한다.
  `runpod_result_v6.schema.json`의 `audioResult`, `audioAssociation`, `audioVisual`은
  같은 v6 검증 내부 구조다. 별도 과거 wire 계약이 아니다.
- 결과·association의 모든 기존 실행 tuple을 보존한다. 원본 core, selection, GPT
  expression, prepared WAV measurement를 검증하며 피드백·점수·index를 만들지 않는다.
- [인계](../../src/voice_coach/backend_analysis/canonical_handoff.py)는 항상 handoff v2다.
  결과 preview publish는 하지 않고 Backend가 원본을 검증한 인계만 공개한다.
  업로드·seal·재전송은 동일 bytes/worker/event를 사용하고 추론을 재실행하지 않는다.
- [독립 verifier](../../deploy/runpod/canonical_verify.py)는 `VERIFY_HANDOFF`,
  `VERIFY_PRECORE`만 수용한다. v6 원본과 nullable media 결속을 검증하며 네트워크·
  추론·GPT dispatch를 하지 않는다.
- 1 MiB 결과 한도와 전체 원본 보관은 유지한다. visual projection은 최대 128개 및
  256 KiB이며, base 음성 envelope 예산은 실제 추가 wire bytes만큼 차감한다.
  `RETAINED_ONLY`의 초과 사유도 동일 예산으로 검증한다.

## 공통 readiness와 Backend 적용 계약

AI `/health/handoff`와 Backend `/api/internal/ai/worker-readiness/handoff`는
`contractVersion=voice-coaching.canonical-handoff.v2`를 사용한다. 양쪽이 비교할
공통 schema digest map은 아래 **11개 전체 파일의 정확한 SHA-256**이다.

1. `runpod_http_control_v1.schema.json`
2. `runpod_http_control_v1_2.schema.json`
3. `runpod_canonical_parent.schema.json`
4. `runpod_canonical_input.schema.json`
5. `runpod_canonical_output.schema.json`
6. `runpod_canonical_journal_v1.schema.json`
7. `runpod_analysis_request_v4.schema.json`
8. `runpod_result_v6.schema.json`
9. `runpod_canonical_handoff_v2.schema.json`
10. `runpod_media_preparation_v1.schema.json`
11. `runpod_visual_coaching_input_v1.schema.json`

AI 공통 readiness는 executor, Backend v2 지원·전체 digest, 기존 인증 callback·
저장소·모델 접근 의존성을 확인한다. 영상 readiness도 같은 map을 사용하며 실제
영상 자산·영상 지원을 추가로 확인한다. `supported`, executor 준비, 사용자 신규
admission, 실제 분석 성공은 서로 다르다. 설치 소스나 기본 설정만으로 준비를
보고하지 않는다.

별도 Backend 저장소에는 다음 변경이 필요하다. 이 checkout은 Backend 구현·DB
migration·FE 소비 또는 배포 완료를 주장하지 않는다.

- 음성·영상의 신규 writer/capability를 request v4/result v6/handoff v2로 단일화한다.
  영상 없는 요청의 video/preparation null을 수용하고 준비되지 않은 영상은 거절한다.
- `/worker-readiness/handoff`와 `/worker-readiness/audiovisual`에서 v2·공통 11개
  digest map을 반환한다. nullable media와 association v2를 저장·검증하며 결과의
  음성·영상 여부는 실제 media binding으로 판단한다.
- 새 verifier bundle과 일치하는 schema bytes를 설치한다. 신규 요청에 대해 삭제된
  계약 파일이나 과거 result-publish/VERIFIED receipt 경로를 요구하지 않는다.
- 사용자 로그인·등록 녹음 소유 관계·원본 무결성·서버 간 Bearer 인증을 유지한다.
  내부 API에 동의 증빙·정책 revision·grant·서명을 추가하지 않는다.

## 전환과 운영 반영 상태

Backend·AI의 admission을 닫고 기존 실행 및 미완료 handoff를 먼저 조정한다.
진행 중 요청·저장 결과·원본 파일의 버전이나 bytes를 사후 변경하지 않는다.
새 reader/verifier와 양쪽 schema를 설치하고 공통 v2 readiness가 일치한 뒤 음성
admission을 연다. 영상 admission은 영상용 identity·자산·지원까지 준비된 경우에만
연다. 배포·복구 launcher도 같은 공통 map을 요구한다.

새 recovery는 과거 요청의 transport journal을 변경·삭제·재실행하지 않고
`retired_handoff_requires_operator_reconciliation`을 기록한다. 따라서 과거 journal이
남은 상태로 통합 release를 적용하면 그 journal은 자동 인계되지 않는다. 전환 전에
기존 release/Backend로 미완료 인계를 조정해야 한다. 사용자 데이터의 마이그레이션은
이 소스 변경에 포함하지 않는다.

삭제된 이전 request v3/result v5/handoff v1 schema와 v4/v5 계약 계획 문서는
아카이브하지 않는다. 현재 검증 코드가 실제 사용하는 frozen 원본·음성 검증 구조와
운영 관측은 신규 접수 계약과 구분한다.

작업 브랜치는 `feat/unify-analysis-v6-20261019`다. 이 작업은 저장소 소스·명세 변경이며
운영 애플리케이션 배포·Backend 수정·commit/push/PR은 수행하지 않았다. 앞선 터널
복구의 운영 적용과 별개다. 테스트·fixture·mock·화면 검증·실제 분석·GPT 생성은
생성하거나 실행하지 않는다. 검증 범위는 Python 구문, JSON/schema 정적 확인,
문서 링크·충돌 표식 및 Git diff다.

## 변경 파일과 실제 정적 확인

v6 구현 변경은 아래 파일에 한정한다. 작업 전부터 존재한 다른 수정·미추적 파일과
앞선 터널 복구 코드는 보존한다.

- `src/voice_coach/backend_analysis/`: `http_models.py`, `http_runtime.py`,
  `http_api.py`, `canonical_settings.py`, `canonical_bridge.py`, `canonical_result.py`,
  `canonical_audiovisual_result.py`, `canonical_codec.py`, `canonical_executor.py`,
  `canonical_handoff.py`, `canonical_recovery.py`.
- `deploy/dlpc/`: `start_production.py`, `activate_announcer_prompt.py`, `README.md`.
- `deploy/runpod/`: `canonical_verify.py`, `stage_backend_verifier_bundle.py`,
  `build_canonical_overlay.py`, `README.md`.
- `docs/contracts/`: request v4/result v6 schema와 README 수정, request v3/result v5/
  handoff v1 schema 삭제.
- `docs/api/`: `endpoints.md`, `specification.md`를 현재 계약으로 재작성.
- 현재 안내 갱신: `.codex/backend-http.md`, `.codex/backblaze.md`, `docs/README.md`,
  `docs/canonical-integration/README.md`, `docs/database/schema.md`,
  `docs/video-lip-analysis/audiovisual-v6-handoff-20261007.md`와 이 명세.
- 이전 계약 문서 삭제: `callback-v4-plan.md`, `handoff-v5-20261004.md`,
  `v5-only-migration-20261004.md`, `production-rollout-plan.md`.
- 삭제 문서의 링크만 갱신: `docs/canonical-integration/frontend-contract-review.md`,
  `latency-implementation-20261004.md`, `latency-improvement-implementation-plan-20261003.md`,
  `rollout-execution-20261001.md`, `docs/video-lip-analysis/implementation-plan.md`.

Python 3.12 내장 compile로 변경 Python 17개(이전 터널 수정 1개 포함)의 구문을
확인했다. 소스 실행·bytecode 파일 생성은 없었다. request v4/result v6/handoff v2의
JSON 구문과 Draft 2020-12 meta-schema 정적 확인을 통과했다. 예시 instance·fixture를
생성하거나 실행하지 않았다. 변경 문서의 상대 파일 링크 239개에서 새 누락·기존
누락이 없었다. heading anchor·외부 URL·변경하지 않은 문서 전체는 이 확인 범위가
아니다. 활성 Python 소스의 삭제된 v5 계약·상수 참조는 검색에서 발견되지 않았다.

종료 전 2026-10-19 운영 읽기 조회에서 공개 direct는 HTTP 200/ready=true였고,
Backend handoff는 HTTP 200/contractVersion=v1/supported=true/admissionEnabled=false였다.
J406 설치 source는 `791ca5496e04a10b6948c69dce97361ee4335f4c`, bundle은
`d5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443`다. 이 관측은
v6 소스의 운영 설치를 뜻하지 않는다. 요청 제출·추론·서비스 재시작은 하지 않았다.
