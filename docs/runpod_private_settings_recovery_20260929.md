# RunPod 비밀 설정 위치와 복원 조사 — 2026-09-29

## 조사 대상과 결론

대상은 Pod `s8hb5b0l5k4qlx`다. 문서를 먼저 검색한 뒤 SSH로 파일 존재·설정 이름·권한과 현재 공급원의 일치를 확인했다. 키·토큰·버킷명·객체명·설정값 원문은 이 문서에 기록하지 않는다.

실제 저장소·readiness 설정이 있는 기존 파일은 **`/workspace/intelligentai/deployment/private/analysis-http.env`**다. 누락된 설정 6개와 AWS 읽기 자격증명이 모두 존재한다. 현재 공급원과 이 파일의 API 토큰, callback 토큰, callback base URL은 메모리 비교에서 일치했다. 자격증명 유효성 및 객체 읽기 성공은 아직 확인하지 않았다.

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

GitHub Actions에 `RUNPOD_API_KEY`, `RUNPOD_SSH_PRIVATE_KEY`, `RUNPOD_SSH_HOST_KEY` Secrets와 `RUNPOD_POD_ID` 변수를 등록했다. 사용자 PC의 개인키 대신 새 배포 전용 키를 생성했으며 Pod에는 공개키만 추가했다. 기존 SSH 접근은 보존했다. 현재 컨테이너의 공개키 등록과 향후 컨테이너 재생성 후 유지 여부는 별개다.

[자동 배포 안내](../deploy/runpod/GITHUB_ACTIONS.md)의 수동 실행에서 `check_only=true`를 선택하면 API 대상 확인과 고정 호스트 키를 사용하는 root SSH 접속만 확인한다. 소스 적용·서비스 재시작·모델 추론·회귀 QA는 하지 않는다. 정상 배포는 `check_only=false`이고 `main` push도 이 경로를 사용한다.

이번 조사에서 OpenAI 키는 저장·실행 환경 주입, `bootstrap=true`까지 확인했다. 분석 readiness는 여전히 `503 / http_settings_incomplete_or_invalid`이며 위 저장소·readiness 항목이 현재 실행 설정에 없다. AWS 자격증명의 지속 환경 복원은 자동 승인 심사에서 별도 승인을 요구하여 대기 중이다. 따라서 파일 발견·Secrets 등록·소스 게시를 실제 배포 완료나 서비스 정상화로 표현하지 않는다.
