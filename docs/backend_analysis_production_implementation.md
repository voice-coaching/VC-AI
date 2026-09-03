# VC-BE 연동 AI 분석 워커 구현 현황

- 측정일: 2026-09-03
- 구현 기준 브랜치: `agent/prepare-gated-ctc-training`
- 문서화 시점 HEAD: `740f6ba99e2937125750b25d3307ccfac71ac252`

아래 동의·배포 조합 보강은 아직 커밋되지 않은 현재 작업 트리를 기준으로 한다. 실제
release ID에는 이 변경을 검토·커밋한 뒤의 깨끗한 40자 commit SHA를 사용해야 한다.

## 문서 목적

이 문서는 VC-BE가 발행한 음성·영상 분석 요청을 처리하는 intelligentAI 비공개
워커의 현재 구현과 검증 범위를 요약한다. 필드별 정본은
[VC-BE Redis Stream worker integration](api/vc-be-redis-stream-worker.md)과 VC-BE의
`docs/api/ai-redis-stream-contract.md`다. 이 문서는 스키마를 재정의하지 않는다.

현재 통합 계약은 다음 두 버전이다.

- request: `voice-coaching.analysis-request.v4`
- result: `voice-coaching.analysis-result.v3`

## 책임 경계

intelligentAI 워커가 소유하는 책임은 다음과 같다.

- HMAC 처리 권한과 음성·얼굴 처리 동의를 media I/O 전에 검증한다.
- private S3-compatible 저장소에서 서명된 opaque key의 객체만 조건부로 읽는다.
- canonical WAV를 제한 작업공간에 materialize하고 digest·크기·MIME를 다시 검증한다.
- 고정 g2pK expected phones와 warm isolated Seungun detector를 사용한다.
- Seungun을 요청당 정확히 한 번 호출하고 그 결과의 selected phone을 보존한다.
- 같은 요청·같은 media digest·같은 selected index인 경우에만 visual supplement를 만든다.
- 결과를 Redis Stream에 기록한 뒤 요청을 ACK하고, 취소가 먼저 관찰되면 결과를 억제한다.
- 성공·실패와 관계없이 제한 media와 파생 파일을 정리한다.

VC-BE가 소유하는 사용자 인증, 세션/recording ownership, 동의 원장, canonical media
생성, durable job/outbox 상태, public result 응답은 이 저장소가 대신 구현하지 않는다.
Seungun은 발음 진단과 selected phone의 유일한 권위이며 visual/LLM은 새 진단을 만들 수
없다.

## 구현된 처리 흐름

```text
analysis:request:v1
  -> deployment release/key/policy/device/artifact attestation verification
  -> request-v4 strict parsing and payload bound
  -> cancellation tombstone check
  -> authorization and consent verification
  -> ETag/VersionId-bound S3 streaming GET
  -> canonical WAV integrity verification
  -> pinned offline g2pK
  -> one warm isolated Seungun invocation
  -> optional same-attempt visual supplement
  -> bounded result-v3 publication
  -> atomic request XACK + XDEL
```

오래 실행되는 요청은 PEL lease를 갱신한다. 실행 중 cancellation tombstone이 생기면
Seungun child를 중단하고 결과를 발행하지 않는다. 정상 결과가 상한을 넘거나 직렬화되지
않으면 learner-safe한 최소 terminal `FAILED` 결과를 발행한다. request/result Stream은
안전한 durable handoff 전에는 길이 기반으로 trim하지 않는다.

## 동의 발급과 배포 조합 보강

동의 발급은 외부 계약만 남은 항목이 아니다. VC-BE가 인증된 analyze/retry transaction
안에서 음성 처리 동의를 실제 PostgreSQL 원장에 발급하고, 선택된 canonical video에는
별도의 얼굴 처리 동의를 발급한다. VC-BE는 그 receipt와 request generation을 짧은 수명의
HMAC authorization에 묶는다. AI 워커는 raw 동의를 받지 않고 opaque receipt, 정확한 policy
revision, 서명과 모든 media binding만 검증한다.

이번 보강은 동의 issuer와 GPU artifact가 각각 올바르더라도 서로 다른 release/key/policy
조합이 잘못 활성화되는 문제를 막는다. 워커는 Redis나 object storage를 사용하기 전에
`voice-coaching.analysis-deployment.v1` manifest를 검사한다. manifest는 다음 항목을 정확히
묶는다.

- image에 build-time으로 내장된 intelligentAI 40자 revision과 동일한 release SHA
- VC-BE가 서명에 사용하는 active key ID와 consent policy revision
- `cpu` 또는 `cuda` device
- worker가 읽는 artifact group ID
- detector bundle, Seungun model/processor, NLTK data, visual release와 landmark model의
  결정적 content digest

manifest에는 secret, artifact 경로, 사용자·동의 원문을 넣지 않는다. 다만 model
fingerprint를 포함하므로 Git이 아닌 제한된 배포 경로에 mode `0640`으로 새 파일만 생성해
read-only mount한다. artifact는 전용 group 소유이고 directory `0750`, file `0640` 또는
owner 실행 파일 `0750`이어야 하며 group/world write와 world access는 거부된다. 로컬
owner-only visual release를 직접 완화하지 말고 승인된 배포 staging copy에만 전용 group
권한을 적용한다.

startup은 attestation 검증, S3 adapter 구성, 실제 frozen Seungun child의 model/GPU preload,
visual release load, Redis consumer group 확인 순서로 진행한다. readiness는 이 과정이 모두
완료되기 전에 기록되지 않는다.

## Task별 구현 기록

| Task | intelligentAI 구현 | 커밋 |
| --- | --- | --- |
| 1 | face-consented visual input과 same-attempt supplement 계약·adapter·service | `14073a6` |
| 2 | durable cancellation polling과 실행 중 Seungun child 중단 | `bdc6c82` |
| 3 | terminal handoff 뒤 원자적 request `XACK`+`XDEL` | `21d7d9a` |
| 4 | S3 ETag/VersionId 조건부 streaming과 객체 identity 재검증 | `ded3b4a` |
| 5 | request/result byte bound와 최소 terminal fallback | `9a7676d` |
| 6 | 기존 isolated Seungun/ffmpeg 경계와 cleanup을 재검증; 신규 AI 커밋 없음 | 해당 없음 |
| 7 | canonical video digest·selected-index lineage 회귀 테스트 | `31638c1` |
| 8 | 전체 worker image build, preload health 계약, 자동 rollback | `740f6ba` |

## 주요 구현 위치

| 경로 | 책임 |
| --- | --- |
| [`src/voice_coach/backend_analysis/domain.py`](../src/voice_coach/backend_analysis/domain.py) | request-v4/result-v3와 visual/evidence 불변식 |
| [`src/voice_coach/backend_analysis/service.py`](../src/voice_coach/backend_analysis/service.py) | 검증 순서, Seungun 단일 호출, visual fallback, cleanup |
| [`src/voice_coach/backend_analysis/adapters/redis_stream.py`](../src/voice_coach/backend_analysis/adapters/redis_stream.py) | PEL reclaim/heartbeat, cancellation, publish-before-ACK |
| [`src/voice_coach/backend_analysis/adapters/s3_object_store.py`](../src/voice_coach/backend_analysis/adapters/s3_object_store.py) | 제한 객체 조건부 다운로드와 무결성 검증 |
| [`src/voice_coach/backend_analysis/adapters/seungun.py`](../src/voice_coach/backend_analysis/adapters/seungun.py) | canonical audio decode와 Seungun mapping |
| [`src/voice_coach/backend_analysis/adapters/visual.py`](../src/voice_coach/backend_analysis/adapters/visual.py) | promoted visual release와 same-attempt supplement |
| [`src/voice_coach/backend_analysis/production.py`](../src/voice_coach/backend_analysis/production.py) | production adapter composition과 startup preload |
| [`src/voice_coach/backend_analysis/deployment_attestation.py`](../src/voice_coach/backend_analysis/deployment_attestation.py) | release/key/policy/device와 mounted artifact content의 fail-closed binding |
| [`deploy/backend-analysis-worker/Dockerfile`](../deploy/backend-analysis-worker/Dockerfile) | Python 3.12, Java 21, ffmpeg, scorer/visual runtime image |
| [`deploy/backend-analysis-worker/compose.yaml`](../deploy/backend-analysis-worker/compose.yaml) | read-only root, read-only artifacts, tmpfs, secret와 health 계약 |
| [`scripts/deploy_backend_analysis_worker.sh`](../scripts/deploy_backend_analysis_worker.sh) | commit-bound build, health wait, 자동/수동 rollback |
| [`scripts/prepare_backend_analysis_artifact_access.py`](../scripts/prepare_backend_analysis_artifact_access.py) | 전용 artifact group의 private read 권한 check/apply |
| [`scripts/create_backend_analysis_deployment_attestation.py`](../scripts/create_backend_analysis_deployment_attestation.py) | 승인된 조합의 private immutable manifest 생성 |

## 검증 결과

다음 결과는 2026-09-03 로컬 실행 근거다.

| 검증 | 결과 | 범위 |
| --- | --- | --- |
| 관련 worker/visual/media pytest | `29 passed, 1 skipped` | unit 및 로컬 사용 가능 adapter 계약 |
| 최종 deploy/rollback pytest | `4 passed` | activation, health 실패 복구, SHA 불일치 거부, 수동 rollback |
| `scripts/check_project_constraints.py` | exit 0 | 현재 repository constraints |
| `scripts/check_source_pins.py` | exit 0 | 40개 config의 repository-current pin 663개 |
| production Docker build | exit 0 | 현재 working-tree ARM64 smoke image, 모델·제한 media 미포함 |
| image revision binding | exit 0 | OCI revision과 image-baked `VOICE_COACH_BUILD_REVISION`이 합성 40자 SHA와 일치 |
| container runtime imports | exit 0 | boto3, Redis, MediaPipe, PyTorch, Transformers, Seungun package |
| pinned offline g2pK smoke | exit 0 | hash가 계약과 일치하는 read-only NLTK mount 사용 |
| production Compose expansion | exit 0 | 필수 mount/env/secret/health 구조 |
| VC-BE consent migration | exit 0 | PostgreSQL 16에서 V0~V14 적용, Hibernate validate와 consent constraint/index 확인 |
| attestation/authorization/composition pytest | `50 passed` | release/key/policy/device/artifact binding, group access, model preload 계약 |
| real CUDA frozen-model preload | exit 0 | 비특권 container, supplemental artifact group, `init`, read-only source/artifact 조합 |
| attested final-image composition smoke | exit 0 | 실제 local model/NLTK/visual artifact, synthetic key/policy, CUDA preload; Redis/S3 호출 없음 |

빌드된 smoke image는 Linux ARM64, UID/GID `10001:10001`, read-only 애플리케이션
파일과 OCI revision label을 확인했다. 실제 release 배포 스크립트는 전달된 40자 SHA와
checkout HEAD가 같고 worktree가 깨끗한 경우에만 빌드한다.

canonical codec 회귀 테스트는 H.264/AAC 및 HEVC/AAC MP4의 RGB frame과 PCM decode를
확인했다. 승인된 비공개 HEVC/AAC 기기 표본 1개도 opt-in smoke를 통과했지만 그 파일과
경로는 Git이나 이 문서에 기록하지 않았다. 한 표본의 성공은 전체 기기 호환성 근거가
아니다.

## 상태 판정

- `implemented`: request-v4/result-v3 처리, 동의/권한, S3 무결성, cancellation,
  Redis handoff, same-attempt visual, payload bound, health/rollback과 content-bound deployment
  attestation 코드가 존재한다. VC-BE의 동의 발급·철회 원장도 구현되어 있다.
- `verified`: unit/synthetic 계약, 실제 ARM64 이미지 빌드, 컨테이너 import, offline g2pK,
  Compose 확장, 제한된 codec smoke, PostgreSQL consent migration, 실제 local frozen model의
  CUDA preload와 attested composition smoke가 통과했다.
- `contract-only`: 동의 발급 자체와 GPU model/artifact 조합은 더 이상 contract-only가
  아니다. 실제 production S3/TLS Redis 자격 증명과 배포 target을 사용한 사용자 요청의
  end-to-end 처리는 아직 실행하지 않았다.
- `running`: 이 문서 작성 시 운영 worker가 실행 중이라고 주장하지 않는다.

## 운영 배포 전 남은 gate

다음 조합은 승인된 배포 환경에서 별도로 검증해야 한다.

1. 실제 private TLS Redis ACL과 request/result consumer group
2. ETag/VersionId 조건부 동작을 지원하는 실제 S3-compatible 저장소와 GET-only worker role
3. VC-BE active key ID/policy와 AI keyring/attestation의 일치 및 실제 secret-manager 주입
4. 배포 전용 group으로 준비한 read-only artifact와 새로 생성한 immutable attestation mount
5. GPU preload 완료 후 health state와 실제 result-v3의 Backend DB 반영
6. admission 중지, Stream/PEL drain, 양쪽 배포, 실패 시 양쪽 rollback, admission 재개

설정 또는 artifact가 불완전하면 worker는 Stream을 소비하기 전에 fail-closed해야 한다.
모델 weight, restricted media, secret, raw consent와 개인 식별자는 image, Git, 상태 파일에
포함하지 않는다.
