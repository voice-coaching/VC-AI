# 아나운서 사례를 매 GPT 코칭에 제공

2026-09-28. 사용자 요청으로 testvideo1/testvideo2의 기존 Seungun·입술 분석 출력과 아나운서 피드백을 연결했다. GPT 가중치를 재학습하지 않는다. 두 사례 전체를 매 생성 입력에 제공하는 고정 사례 검색 방식이며, 의미 검색으로 일부만 선택하지 않는다. [OpenAI의 문맥 제공 안내](https://developers.openai.com/api/docs/guides/prompt-engineering)를 따른다.

## 자료와 연결

- [전문가 원문과 항목](../configs/coaching/announcer_feedback_20260928.json): 사용자가 2026-09-28 제공한 문구. testvideo1 4항목, testvideo2 5항목.
- [배포 manifest](../configs/coaching/expert_examples_v1.json): 전체 사례 파일 경로, revision, SHA-256.
- 사례 파일: Git 제외 `artifacts/expert_coaching/announcer-20260928/cases.json`, 393,116 bytes. SHA-256 `eb68e02e75bd1b816414c411dd97953c20cd08361a2f7da6b58b94341717f3f8`.
- 각 사례는 Seungun 음소 30행 전체, 위치 근거 전체, 입술 파이프라인 trace 전체와 최종 관측, 사용자 제공 전문가 원문, 항목별 위치 연결을 포함한다. 기존 LLM 생성 답변은 전문가 정답으로 포함하지 않는다.
- 원본은 Pod `/tmp/coaching-quality-20260918/visual-debug/`의 기존 분석 JSON과 `report.json`이다. 보고서의 영상·PCM·정규화한 Seungun 분석 digest를 확인했다. 영상은 `/root/.config/intelligentai/api-verification-20260917/testvideo{1,2}.mp4`와 대조했다. 새 추론은 수행하지 않았다.
- testvideo1 영상 SHA-256: `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`.
- testvideo2 영상 SHA-256: `1dd299f28ae9a0c2fcc2422e46ef3e162c62805a2267a19e1f7a277087a81379`.

분석 대본은 `쌀과 콩을 깨끗한 그릇에 담아요`다. 전문가의 짧은 연습 문장에는 `깨끗한`이 없다. 두 원문을 유지하며 단어 이름으로만 위치를 연결했다. `담아요`의 억양, 발화 속도, 첫 단락 전달 방식에 새로운 시간·pitch 정답을 만들지 않았다. 단어 연결은 검출기 판단의 전문가 검증을 의미하지 않는다. 입술 관측은 기존 후보 구간만 포함하며 전체 모음/단어를 분석한 것으로 확대하지 않는다.

## 생성 경로

[expert_examples.py](../src/voice_coach/backend_analysis/expert_examples.py)는 시작 시 고정 manifest와 사례 hash를 검사한다. OpenAI 코칭 설정에서 필수로 로드하며, 누락·손상·용량 초과 시 참고 자료 없이 조용히 실행하지 않는다.

[coaching.py](../src/voice_coach/backend_analysis/coaching.py)는 system 규칙, `expertReferenceCases` 전체, `currentAttempt` 순서로 메시지를 만든다. 과거 사례의 ID·시간·수치·전문가 진단을 현재 녹음의 사실로 옮기지 않는다. 현재 후보/근거 정책, 영상 교정 제한, JSON 출력 계약과 실패 시 기본 연습을 유지한다. 적격 후보가 없어 기존 로직이 GPT를 호출하지 않는 경우에는 새로 호출하지 않는다.

기존 현재 입력 예산에 전체 사례와 사용 규칙 예산을 더한다. 이번 운영 설정의 보수적인 UTF-8 기준 입력 예산은 410,821이며 실제 API 토큰 수가 아니다. 매 생성에 약 393KB의 참고 JSON이 추가되므로 입력 비용·지연은 증가할 수 있다. 사례 전체는 후보 수 축소 대상이 아니다. prompt digest에는 사례 SHA-256도 포함된다. 로그는 revision·hash·사례 ID만 기록하고 원문을 남기지 않는다.

## 보관·배포 관측

대상 Pod: `s8hb5b0l5k4qlx`. 2026-09-28 KST 운영 확인.

- Pod 지속 사본: `/workspace/intelligentai/deployment/int8-overlay/artifacts/expert_coaching/announcer-20260928/cases.json`.
- 실행 사본: `/opt/intelligentai/app/artifacts/expert_coaching/announcer-20260928/cases.json`.
- 코드·설정·사례 6개 파일을 overlay와 실행 사본에 배포하고 overlay manifest를 갱신했다. 운영의 OpenAI 전용 `gpt-6-astra` 설정을 보존했다. 로컬 generator에는 기존 provider 분기를 유지했다. 서로 다른 운영 코드를 로컬 소스로 통째로 덮어쓰지 않았다.
- 변경 전 백업: `/workspace/intelligentai/deployment/expert-examples-backup-20260928/`.
- API PID는 61556에서 252171로 재기동됐다. `/health` 및 `/readyz` 200, `executorReady=true`를 관측했다. 일시적인 Backend callback readiness 변경은 별도로 발생할 수 있다.
- 2026-09-28 04:40:07 UTC에 실제 UID/GID 10001·scorer Python에서 설정 로드를 확인했다. 두 사례와 hash가 일치했고, prompt SHA-256은 `29b079110f0d96a6b77b2e4070d38de7357d5e87bf4334b37c29340f2c656ebe`였다. 이 확인은 별도의 설정 로드이며 실제 생성 요청을 보낸 증거가 아니다.
- 최초 B2 업로드는 목적지 명시 승인 부족으로 자동 승인 검토가 거절했다. 이후 사용자가 비공개 B2 영구 저장을 명시적으로 승인했으며, 2026-09-28 05:07:09 UTC(14:07:09 KST)에 저장과 다운로드 무결성 확인을 완료했다. `speakai2026` 버킷의 `allPrivate` 설정을 확인했고 공개 접근은 설정하지 않았다. Pod 지속 볼륨과 로컬 사본도 유지한다.

### B2 영구 객체

- 버킷: `speakai2026` (`allPrivate`).
- 객체명: `coaching/expert-examples/announcer-20260928/cases-eb68e02e75bd1b816414c411dd97953c20cd08361a2f7da6b58b94341717f3f8.json`.
- file ID: `4_ze5cc71977ef1880ca60b0c17_f101d4699d8ba290b_d20260928_m050700_c005_v0501027_t0034_u01790572020047`.
- 크기: 393,116 bytes. 위 사례 SHA-256과 업로드 후 다운로드 SHA-256이 일치한다. B2가 반환한 크기와 SHA-1도 로컬 파일과 대조했다.
- 로컬 확인 기록: Git 제외 `artifacts/expert_coaching/announcer-20260928/b2-receipt.json`. 인증값은 포함하지 않는다.
- 저장 대상은 분석 출력과 전문가 피드백 묶음이며 원본 MP4나 비밀 설정을 함께 업로드하지 않았다. 이번 저장으로 GPT 생성 코드나 Pod 프로세스를 다시 변경하지 않았다.

## 확인 범위와 복원

Python 컴파일, JSON 형식, 영상/분석 binding digest, 사례 SHA-256, 배포 파일 목록 및 서비스 계정의 설정 로드를 확인했다. 실제 GPT 호출, 두 영상 재추론, 브라우저·자동 회귀·발음 품질 QA는 수행하지 않았다. 전문가 피드백 방식의 개선 효과는 개발자가 확인해야 한다. commit/push/PR은 수행하지 않았다. 기존 사용자 문서 삭제 변경은 보존했다.

복원은 위 B2 file ID로 사례 파일을 내려받아 manifest 경로에 놓고 SHA-256을 확인한다. [패키징 스크립트](../scripts/build_announcer_expert_examples.py)는 기존 보고서·분석 JSON·원본 영상과 전문가 원문에서 동일한 사례를 다시 묶으며 추론을 실행하지 않는다. Pod 재생성 때는 영구 overlay의 파일과 manifest를 함께 복원한다.
