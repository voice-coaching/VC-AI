# FE–AWS–RunPod 연결 복구 구현

기준: [수정 계획](api-pipeline-recovery-plan-20261003.md),
[세션 107 조사](incident-20261003-session107-502.md). 2026-10-03 코드·PR 인계 기록이다.
16:20 KST의 운영 관측을 새로운 배포 성공 기록으로 바꾸지 않는다.

## 구현 범위

| 단계 | 코드 변경 | 운영 완료 여부 |
| --- | --- | --- |
| P1 호환 묶음 | 기존 scored builder/exporter/stager를 유지하고, overlay builder를 고정 Git SHA와 실제 beforeSha 관측 기반으로 수정 | AWS verifier/RunPod/Vercel의 동시 호환 활성화는 별도 배포 필요 |
| P2 배포 보호 | Backend release manifest·호스트 잠금·public 접수 차단·drain·호환 readiness·제한적 복구 도구, 서비스 접수 경계와 고정 진단 로그 | nginx/profile 최초 설치 및 운영 전환은 미수행 |
| P3 FE 복구 | 같은 분석 상태 GET의 90초 한도 재시도, 10분 전체 deadline, Retry-After, 취소, 연결 복구 안내 | Vercel 배포·수동 QA 미수행 |
| P4 업로드 | BE requiredHeaders에서 Content-Length 제외, FE 금지 헤더 방어 | 실제 브라우저 업로드 QA 미수행 |

Backend는 사용자 확인에 따라 기존 **develop**, Frontend는 **dev** 대상 PR로 인계한다.
AI 변경은 별도 작업 브랜치의 commit으로 참조한다. 일반 liveness나 컴파일을 실제
녹음·Seungun·GPT·점수 품질 검증으로 보고하지 않는다.

## AI 파일과 운영 계약

- `src/voice_coach/backend_analysis/deployment_observation.py`: 프로세스 시작 시
  앱 배포 metadata, 설정된 core/H5 manifest·lock, schema digest를 한 번 읽는다.
  파일만 덮어쓰고 프로세스를 재시작하지 않은 상태를 새 배포로 표시하지 않는다.
- `http_runtime.py`: 시작 시 identity와 현재 active slot/stopping 상태를 합쳐 제공한다.
- `http_api.py`: 인증된 `GET /health/deployment`; 일반 health의 공개 예외에 추가하지 않는다.
- `deploy/runpod/api-gateway/nginx.conf`, `enable_canonical_gateway.py`: 동일 경로를
  분석 API로 전달한다. 기존 canonical 경로가 있어도 필요한 deployment 경로만 추가한다.
- `build_canonical_overlay.py`: dirty checkout이 아니라 지정한 Git commit의 blob을
  읽으며 sourceGitRevision, baseRevision, baseOverlaySha256, 파일별 beforeSha256과
  SHA-256을 묶는다. 새 진단 모듈과 canonical 모듈의 전체 의존 경로를 포함한다.
- `inspect_canonical_overlay.py`: Pod 앱과 영구 overlay의 해당 파일 hash를 비교하고
  일치할 때만 읽기 전용 inventory를 출력한다. 비밀 파일과 근거 원문은 입력 대상이 아니다.

`/health/deployment`는 기존 `AI_ANALYSIS_API_TOKEN` Bearer 인증을 사용한다.
정상 응답은 HTTP 200, Cache-Control no-store다. 필드는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `protocol` | `canonical-deployment-v1` |
| `workerInstanceId` | 현재 프로세스의 worker ID |
| `activeJobs` | 현재 단일 실행 슬롯 0 또는 1; Backend DB drain도 별도로 필요 |
| `stopping` | 프로세스 종료 중 여부 |
| `sourceGitRevision`, `bundleSha256` | 시작 시 canonicalIntegration metadata |
| `coreManifestSha256` | 시작 시 설정한 core의 release_manifest.json hash |
| `llmManifestSha256`, `llmLockSha256` | 시작 시 설정한 LLM contracts/llm의 manifest/lock hash |
| `schemaDigests` | 기존 canonical readiness와 같은 9개 파일/sha256 목록 |

metadata나 파일을 읽을 수 없으면 기존 오류 envelope의 HTTP 503 NOT_READY다.
job 원문, 녹음 식별자, URL, 인증값은 노출하지 않는다. 이 endpoint는 GPT 접근성이나
실제 추론 품질의 증명이 아니며 기존 `/health/canonical`과 함께 검사한다.
공유 분석/result schema 9개와 동결 채점 규칙은 이번 변경에서 수정하지 않는다.

## artifact 준비 절차

새 진단 모듈까지 commit한 뒤 AI 40자리 SHA를 고정한다. builder의 `--list-paths`
출력을 SSH stdin으로 Pod의 inventory 도구에 전달하고, 응답을 로컬 inventory JSON에
저장한다. inventory 명령은 Pod를 시작하거나 프로세스를 멈추지 않는다.

```bash
python deploy/runpod/build_canonical_overlay.py --revision "$AI_SHA" --list-paths
# 위 JSON을 Pod의 inspect_canonical_overlay.py stdin으로 전달해 inventory를 받는다.
python deploy/runpod/build_canonical_overlay.py --revision "$AI_SHA" \
  --base-inventory "$INVENTORY_JSON" --output "$BUNDLE_JSON"
```

설치에는 기존 `deploy_canonical_runtime.py`의 beforeSha/overlay base 검증을 사용한다.
scored core/H5 모델·정책 경로와 오프라인 의존성은 별도 준비 사항이다. gateway의
실제 설정과 영구 설정을 함께 맞춘다. Backend 운영자는 새 verifier를 비활성 경로에
설치하고 실제 서비스 사용자로 8개 pin과 9개 schema를 먼저 확인한다.

구버전 Pod에는 진단 endpoint가 없으므로 일반 CI는 fail-closed한다. 최초 전환은
public 접수 차단, AWS DB 및 Pod 실행 슬롯의 별도 읽기 전용 확인, 새 verifier와
scored RunPod 묶음 전환, 양쪽 readiness 확인 순서로 운영자가 조정해야 한다.
구버전의 404/503을 빈 작업 큐로 간주하는 우회 옵션은 두지 않는다.

## 검증·남은 작업

Python 구문, Java compileJava, TypeScript noEmit, schema 9개 byte/hash 일치와
변경 문서/포맷을 정적으로 확인한다. 자동 회귀 테스트·fixture·브라우저 자동화는
저장소 지침에 따라 수행하지 않는다. PR 병합, 운영 설정 설치·배포, 실제 음성/GPT/
callback/점수 및 기존 null 점수 결과에 대한 개발자 수동 QA는 후속 작업이다.

Backend 기동 실패 때 DB 비밀번호/API 토큰을 새 복구 JSON에 저장하는 방식은
자동 승인 검토에서 거절되어 구현하지 않았다. 기존 비밀 설정을 이용하는 운영자
복구 절차와 접수 차단을 유지하며, 무조건 JAR만 되돌리고 접수를 여는 동작은 없다.
