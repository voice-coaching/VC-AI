# 설정과 비밀 주입 안내

설정 이름·필수 여부·기본값·허용값은 **실제 실행 프로세스의 parser와 launcher**가 정본이다. 루트 [.env.example](../.env.example)은 B2 계획값과 legacy Clova 설정을 포함하며 전체 운영 템플릿이 아니다. 예제나 env 추가만으로 기능이 활성화되지는 않는다.

| 역할 | 확인할 소스 | 구분 |
|---|---|---|
| 내부 HTTP 인증·저장소 probe | [http_settings.py](../src/voice_coach/backend_analysis/http_settings.py), [http_runtime.py](../src/voice_coach/backend_analysis/http_runtime.py) | `AI_ANALYSIS_API_TOKEN`, callback token/base, 제한 녹음 저장소·readiness 객체 |
| canonical 자식 프로세스 | [canonical_settings.py](../src/voice_coach/backend_analysis/canonical_settings.py), [canonical_executor.py](../src/voice_coach/backend_analysis/canonical_executor.py) | `AI_CANONICAL_*`, core/H5 경로·pin, handoff/영상 설정과 전달 allowlist |
| direct | [api.py](../src/voice_coach/direct_analysis/api.py), [service.py](../src/voice_coach/direct_analysis/service.py), [store.py](../src/voice_coach/direct_analysis/store.py) | `AI_DIRECT_*`, 명시 활성화·허용 origin·spool/work root·예산 |
| GPT | [coaching_generator.py](../src/voice_coach/backend_analysis/coaching_generator.py), [canonical_producer.py](../src/voice_coach/backend_analysis/canonical_producer.py), 실행 H5 manifest | `OPENAI_API_KEY`와 경로별 provider/model/prompt. legacy generator 설정을 canonical H5에 그대로 적용하지 않는다. |
| DLPC 전달·기동 | [receive_settings.py](../deploy/dlpc/receive_settings.py), [prepare_restored_runtime.py](../deploy/dlpc/prepare_restored_runtime.py), [launch.py](../deploy/dlpc/launch.py) | SSH stdin으로 역할별 전달, host identity와 자식 환경 확인 |
| 비밀 파일 읽기 | [secret_files.py](../src/voice_coach/backend_analysis/secret_files.py) | 이 helper를 쓰는 설정만 direct/file 방식의 배타성·파일 제한을 적용 |
| B2 | [B2 안내](backblaze.md), 대상 backup/restore 소스 | Native API 키, S3 credential, reader/writer 역할 분리 |
| 독립 TTS | [build_tts.py](../deploy/dlpc/build_tts.py), [restore_tts_runtime.py](../deploy/dlpc/restore_tts_runtime.py), [tts_synthesis_identity.py](../deploy/dlpc/tts_synthesis_identity.py) | 분석 토큰과 별도 token, 합성 revision·host별 fingerprint |

`receive_settings.py`는 root와 닫힌 입력 구조를 요구하고 `/etc/intelligentai/analysis.env`, `/etc/intelligentai/tts-token`을 0600으로 최초 생성한다. 기존 파일·symlink가 있으면 거부하므로 덮어쓰기 도구로 사용하지 않는다. 이것은 고정 구현 경로의 설명이며 문서 작업 중 실행할 명령이 아니다.

키는 필요한 프로세스에만 주입하고 env 전체·인증 응답·토큰·설정 파일 원문을 출력하지 않는다. 값은 argv, 문서, Git, 일반 archive에 넣지 않는다. 셸 간 환경이 공유된다고 가정하지 않으며 설정을 읽을 때는 필요한 key의 존재·형식만 확인한다.

Native B2 인증을 S3/AWS credential로 사용하지 않는다. 연구자료 B2 bucket과 사용자 녹음 저장소, canonical 보관 writer, 복구 reader를 같은 권한으로 합치지 않는다. 내부 HTTP에 Redis/HMAC용 grant·정책 revision을 가짜로 주입하지 않는다.

인계에는 비밀값 없이 설정 이름·소유 프로세스·필수 파일/권한·재시작 필요 여부·미설정 항목을 기록한다. 현재 원격 설정을 읽지 않았다면 준비 완료로 표현하지 않는다.
