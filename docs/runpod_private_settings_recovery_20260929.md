# RunPod 비밀 설정 위치와 복원 조사 — 2026-09-29

## 조사 대상과 결론

대상은 Pod `s8hb5b0l5k4qlx`다. 문서를 먼저 검색한 뒤 SSH로 파일 존재·설정 이름·권한과 현재 공급원의 일치를 확인했다. 키·토큰·버킷명·객체명·설정값 원문은 이 문서에 기록하지 않는다.

실제 저장소·readiness 설정이 있는 기존 파일은 **`/workspace/intelligentai/deployment/private/analysis-http.env`**다. 누락된 설정 6개와 AWS 읽기 자격증명이 모두 존재한다. 현재 공급원과 이 파일의 API 토큰, callback 토큰, callback base URL은 메모리 비교에서 일치했다. 사용자 승인으로 누락 항목을 지속 설정에 복원한 뒤 실제 서비스 readiness까지 확인했다.

## 문서에서 확인한 근거

- [HTTP 배포 기록](runpod_http_deployment_20260913.md): 9월 13일 저장소·readiness·AWS 설정이 미설정이었다.
- [A40 이전 기록](runpod_a40_migration_20260915.md): 9월 15일 Pod 환경의 HTTP JSON에는 callback URL과 양방향 토큰만 있었다.
- [9월 28일 운영 관측](runpod_deployed_pipeline_20260928.md): 당시 준비 상태는 정상이었으나 컨테이너 재생성 후 비밀 복원까지 검증한 기록은 아니었다.
- [현재 배포 안내](../deploy/runpod/README.md): `RUNPOD_ANALYSIS_HTTP_ENV_JSON`이 있으면 root private source를 갱신하고 `/run` 실행 설정을 생성한다. 오래된 JSON은 수동으로 보충한 임시 설정을 재기동 시 다시 덮어쓸 수 있다.

## RunPod에서 확인한 파일

| 경로 | 확인 결과 |
|---|---|
| `/workspace/intelligentai/deployment/private/analysis-http.env` | 저장소·readiness 6개, AWS access key/secret 및 default region 존재 |
| `/workspace/intelligentai/deployment/backup-clova-score-20260917T083818Z/root/.config/intelligentai/analysis-http.env` | 같은 필수 이름들이 있는 과거 private source 사본 |
| `/workspace/intelligentai/deployment/backup-clova-score-20260917T083818Z/run/intelligentai/analysis-http.env` | 과거 실행 설정; 현재 GPT 설정 대신 통째로 복원하지 않음 |
| `/workspace/intelligentai/deployment/backup-detailed-rubric-20260917T090418Z/run/intelligentai/analysis-http.env` | 과거 실행 설정; Clova 관련 항목 포함 |
| `/root/.config/intelligentai/analysis-http.env` | 현재 Pod JSON에서 복원된 source, 권한 `0600` |
| `/run/intelligentai/analysis-http.env` | 생성된 현재 실행 설정; 배포 입력 정본으로 사용하지 않음 |

기존 `/workspace` 사본들은 이번 조회에서 `0666`으로 표시됐다. 파일 경로를 찾았다는 사실을 안전한 장기 비밀 보관 검증으로 해석하지 않는다. 파일시스템의 권한 적용 방식은 별도 확인 대상이며 이번에는 해당 파일을 삭제하거나 권한을 변경하지 않았다. 새 비밀 사본을 Git·문서·overlay·일반 archive에 추가하지 않는다.

## 복원에 필요한 이름

| 역할 | 변수 이름 |
|---|---|
| 사용자 녹음 저장소 | `AI_ANALYSIS_OBJECT_STORAGE_BUCKET`, `AI_ANALYSIS_OBJECT_STORAGE_REGION`, `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX` |
| 비사용자 readiness 객체 | `AI_ANALYSIS_READINESS_OBJECT_KEY`, `AI_ANALYSIS_READINESS_OBJECT_SHA256`, `AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES` |
| 기존 AWS SDK 자격증명 | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |

기존 비밀파일의 값을 프로세스 내부에서만 읽고 현재 Pod JSON에 누락된 이름만 병합한다. 현재 OpenAI 키와 GPT-only 설정, 양방향 토큰, 다른 Pod 환경을 보존한다. JSON 갱신은 컨테이너 재생성을 일으킬 수 있으므로 현재 주소·배포 SSH 공개키의 복원·새 호스트 키 고정을 함께 확인한다. 저장 후에는 값 일치를 메모리에서 비교하고, 실행 env 주입·인증 `/health/services`·실제 readiness를 각각 확인한다. 필수값을 임의로 만들거나 준비 검사를 우회하지 않는다.

## 자동 배포 설정과 확인 범위

GitHub Actions에 `RUNPOD_API_KEY`, `RUNPOD_SSH_PRIVATE_KEY`, `RUNPOD_SSH_HOST_KEY` Secrets와 `RUNPOD_POD_ID` 변수를 등록했다. 사용자 PC의 개인키 대신 새 배포 전용 키를 생성했으며 Pod에는 공개키만 추가했다. 기존 SSH 접근은 보존했다. 별도 승인 후 Pod의 `PUBLIC_KEY`에도 기존 공개키들과 병합했고, 컨테이너 재생성 후 배포 공개키가 복원됐음을 확인했다. 재생성된 SSH 호스트 키는 신뢰된 접속으로 확인해 GitHub Secret을 갱신했다. 향후 Pod 재생성으로 호스트 키가 바뀌면 동일한 갱신이 필요하며 자동으로 미확인 호스트 키를 신뢰하지 않는다.

[자동 배포 안내](../deploy/runpod/GITHUB_ACTIONS.md)의 수동 실행에서 `check_only=true`를 선택하면 API 대상 확인과 고정 호스트 키를 사용하는 root SSH 접속만 확인한다. 소스 적용·서비스 재시작·모델 추론·회귀 QA는 하지 않는다. 정상 배포는 `check_only=false`이고 `main` push도 이 경로를 사용한다.

OpenAI 키는 저장·실행 환경 주입, `bootstrap=true`까지 확인했다. 저장소 설정 복원 전에는 `503 / http_settings_incomplete_or_invalid`였다. AWS 자격증명의 지속 환경 복원은 자동 승인 심사의 별도 승인 요청 후 사용자에게 명시 승인을 받아 실행했고, API PATCH 200 및 저장 항목 존재를 확인했다. 파일 발견·Secrets 등록·소스 게시와 실제 서비스 readiness·코드 배포는 별도로 확인한다.

2026-09-29 [GitHub Actions 연결 확인 실행](https://github.com/voice-coaching/intelligentAI/actions/runs/36546717155)은 성공했다. 앞선 연결 확인에서 Python 기본 User-Agent로 RunPod API가 403을 반환하여 식별자 `intelligentai-deployment/1.0`을 명시했고, 수정 후 API·SSH 접근을 검증했다. 이 실행은 `check_only=true`이므로 소스 배포나 readiness 검증을 수행한 결과는 아니다.

## 최초 실제 배포 결과

2026-09-29 [실제 배포 실행](https://github.com/voice-coaching/intelligentAI/actions/runs/36547108212)이 성공했다. 대상 커밋은 `a734f626c3a301a85c2ece1d746603c3881cadce`이며 Pod의 배포 상태 파일과 인증 `/health/services`의 `workerRevision`이 일치했다. 응답은 `HTTP 200`, `status=ready`, `executorReady=true`, `reasonCode=null`, provider/model은 `OPENAI / gpt-6-astra`였다. 미완료 배포 marker는 제거됐고 정상 분석 supervisor가 다시 실행 중이었다.

실제 사용자 녹음 처리·GPT 생성·피드백 품질·자동 회귀 QA는 실행하지 않았다. 이번 최초 push에는 기존 회귀 CI를 실행하지 않도록 `[skip ci]`를 사용하고 배포 workflow만 수동 실행했다. 이후 일반 `main` push는 workflow의 자동 배포 trigger 대상이다. `[skip ci]`가 있는 커밋은 GitHub의 push workflow 생략 규칙을 따른다.

독립 TTS는 분석 배포와 별개다. 같은 점검에서 `/workspace/voice-coaching-tts` 원본은 존재하지만 컨테이너 재생성 후 `/opt/voice-coaching-tts/venv`와 `:8082` listener는 없었다. 이 문서의 분석 readiness 성공을 TTS 복원 완료로 해석하지 않는다. TTS 환경·기동 복원은 별도 운영 작업으로 남는다.
