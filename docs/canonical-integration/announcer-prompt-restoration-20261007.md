# 아나운서 음성 피드백 프롬프트 복구 — 2026-10-07

## GitHub 이력에서 확인한 원문과 누락 지점

- 원문 도입: [`93db6add0a80c434b943949da9dc6f801d3cb156`](https://github.com/voice-coaching/intelligentAI/commit/93db6add0a80c434b943949da9dc6f801d3cb156), 2026-09-29 16:29 KST. `configs/coaching/announcer_feedback_20260928.json`의 `testvideo2.expertFeedback`에 사용자가 인용한 다섯 항목과 마지막 단락이 있다. 제공일은 9/28이고 Git 반영일은 9/29다.
- 과거 구성은 시스템의 `expert_examples.INSTRUCTIONS`와 전문가 사례 user 메시지를 함께 전달했다. 인용문 전체가 독립적인 system 파일 하나에만 들어 있던 것은 아니다. [과거 생성 경로](../expert_feedback_rag_20260928.md), [H3–H6 목록](../gpt6-prompt-harness-catalog-20261003.md)을 함께 참조한다.
- [`513ea709d6be370b79fef304db0ec7934315ff0f`](https://github.com/voice-coaching/intelligentAI/commit/513ea709d6be370b79fef304db0ec7934315ff0f), 10/6 16:58 KST: `build_seungun_gpt.py`가 기존 frozen system을 `seungun_gpt_prompt.txt`로 교체했다. 새 요청 경로에 과거 전문가 사례 사용 정책이 연결되지 않았다.
- [`28e972564cea4171a50d507916319ee88b7e2e85`](https://github.com/voice-coaching/intelligentAI/commit/28e972564cea4171a50d507916319ee88b7e2e85), 10/6 22:38 KST: GOP 경로가 `seungun_gop_prompt.txt`와 새 bridge를 사용했다. 이 system과 요청 메시지에도 해당 전문가 원문이 없다. 자료 파일은 삭제되지 않았지만 현재 GPT 요청에서 빠진 상태였다.

## 복구 범위

사용자는 확인 질문에 **현재 채점 유지 + 피드백 복원**을 선택했고, DLPC 플랫폼 터미널에서 단발 설치 명령 실행이 가능하다고 답했다.

현재 GOP 채점과 JSON 계약을 유지하면서 전문가 피드백을 복구하는 방식이다. 과거의 `items/candidateId/guidanceId` 출력 계약이나 ‘점수는 서버 책임’ 지시까지 통째로 적용하면 현재 `scoring/feedback` 계약과 충돌한다. 따라서 전체 과거 system 바이트로 되돌린 것은 아니다.

`configs/coaching/announcer_feedback_system_20261007.txt`에 **과거 testvideo2 원문 전체를 그대로** 포함하고, 그 사례에 맞춘 단어별 실천 지도 지침을 복구했다. 인용문의 Git 원문 일치를 확인했다. 새로운 개인 오류를 만들어 내지 않고, 다른 대본에서 사례 단어를 복사하거나 원문 단어를 생략하지 않도록 했다. 운율은 선택적인 문장 표현 연습으로 안내하며 실제로 관측한 오류나 감점 근거로 만들지 않는다.

`deploy/dlpc/restore_announcer_prompt.py`는 배포된 DLPC H5의 manifest/lock/prompt와 역사 자료 hash를 확인한 뒤 feedback 절만 교체한다. GOP 정의·9항목 rubric·null 처리·총점 공식·JSON 출력 지시는 그대로 유지한다. 변경되는 동결 파일은 `llm_system_prompt.txt`, `dependency_lock.json`, `llm_contract_manifest.json` 세 개뿐이다. 기존 release 디렉터리는 덮어쓰지 않는다.

## 새 불변 식별자

| 항목 | 값 |
| --- | --- |
| Revision | `canonical-dlpc-gop-announcer-20261007-v1` |
| LLM 디렉터리 | `canonical_dlpc_gop_announcer_20261007_v1_h5` |
| Core (기존 유지) | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` |
| LLM manifest | `def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77` |
| Dependency lock | `b605c3b9ba06bbf18fc126770b18de3d2da0d3f70c08e04873f17bc735b948bc` |
| System prompt | `977c49744e7af9fc60fdc2eb13c23985692cb00dd71789d8b3c43c3813e3f1b5` |
| 역사 원문 JSON | `e6e4d6a005929def118f03881e356fddd7a55a95778e77ed37180d9c9ed8e25f` |

AI harness/execution registry, v4/v5의 association/sourceIdentity, verifier export/install, Backend SemanticVerifier에 새 tuple을 추가했다. 이전 tuple도 유지한다. 소스 경로에 남은 `runpod`는 기존 verifier·계약 이름이며 실제 RunPod 접속이 필요하지 않다.

## 확인과 배포 상태

- Git 원문과 복구된 인용문의 일치, 동결 파일별 hash와 lock, AI/Backend schema 바이트 일치를 정적으로 확인했다.
- Python 컴파일, JSON 파싱, `git diff --check` 통과.
- Backend `bootJar --offline --no-daemon -x test -x check` 성공.
- 자동 회귀 테스트·실제 GPT 생성·음성 품질 QA는 실행하지 않았다.
- **로컬 소스와 배포용 LLM 묶음 준비 완료, 운영 적용은 미완료.** 현재 운영은 기존 `5f3ec5aa…` prompt를 유지한다. 운영 프롬프트 복구 완료로 보고하지 않는다.
- RunPod는 중지돼 관리 경유지로 사용할 수 없고, 기존 AWS→DLPC 포워드는 API 전용이다. 운영 적용에는 DLPC 플랫폼 터미널 실행 또는 직접 관리 SSH 경로가 필요하며 사용자에게 요청했다.

## 적용 순서

1. 복구 코드 revision으로 새 LLM 묶음을 생성하고 위 digest를 대조한다.
2. 현재 기록을 보존한 채 AI source/schema, 새 LLM 디렉터리, AWS verifier, Backend JAR 및 runtime profile을 하나의 호환 묶음으로 준비한다.
3. 신규 접수 hold와 진행 작업 drain 후 양쪽 계약을 함께 전환한다. AI의 `AI_CANONICAL_LLM_ROOT`와 pipeline revision을 새 tuple에 맞춘다. 프롬프트 파일만 기존 디렉터리에 덮어쓰면 무결성 검증에 실패한다.
4. 실제 배포 source/bundle/schema, 분석·handoff·direct readiness를 각각 확인하고 접수를 재개한다. AWS 비밀 복구본과 B2 overlay pin도 활성 배포 묶음에 맞춰 갱신해야 재부팅 후 이전 프롬프트로 돌아가지 않는다.

이 문서의 적용 순서는 아직 실행 완료한 작업이 아니다.

## 단발 운영 적용 준비

AWS 고정 SSH exporter에 stage/resume 명령을 추가하는 패치는 자동 승인 검토가 거절했다. 그 확장은 적용하지 않았다. 현재 `activate_announcer_prompt.py`는 이미 허용된 `b2`와 `settings` 조회만 사용한다. AWS hold/activate/resume는 기존 관리자 SSH와 배포 도구로 운영자가 수행한다. SSH 키·리스너·허용 포트·고정 exporter 권한은 변경하지 않는다.

DLPC 명령은 bundle 검증 → direct 접수 hold/drain → `waiting_for_backend_activation` → Backend의 새 schema 확인 → 분석 정지 및 source/LLM 설치 → 분석 준비 확인 → `waiting_for_backend_resume` → AWS의 복구 설정이 새 source/LLM으로 갱신됨을 확인 → direct 접수 재개 순서다. 각 대기 단계는 최대 1시간이며 오류 시 hold를 남긴다. 진행 중인 작업은 강제 재실행하지 않는다. TTS는 재시작하지 않는다. 원복 자료와 상태는 DLPC `/opt/intelligentai/ops/announcer-20261007/`에 root 전용으로 남는다.

2026-10-07 10:58 KST까지 다음 준비를 확인했다. **이 값은 배포 후보 식별자이며 아직 활성화 근거가 아니다.**

| 후보 | 식별자 |
| --- | --- |
| AI source | `df90eb10be7165dd29b77454c368da76733eac6b` |
| Runtime bundle | `b7170dda1a4ec5a520f30dfa88a0f57f649f9c118060975957e7f68b27b94e78`, 14,869,274 bytes |
| AWS verifier bundle | `fecdeca814a707f32656e286df4edb22d16df6c0bc1020074df7c9c93317bd16`, 983 files |
| Backend source | `84260c05f2dde80ebec3f0d522726cca1ca8ff63` |
| Backend JAR | `3799174a245b9e9098d4474f06576cad8fd083ad63b274e77f1933ffdfaac16f` |
| Installer | `ee7539f971448bf3696c979c5f5165e2576bdac351e054b81909e381e078b48c`, 11,286 bytes |

AWS verifier를 비활성 디렉터리에 설치하고 설치 준비 상태, JAR의 9개 schema, 현재 frontend 자산 호환성을 확인했다. 비활성 runtime profile은 `/root/.config/intelligentai/announcer-20261007/runtime-profile.json`이다. 실행 중인 JAR·profile은 변경하지 않았다.

B2 `speakai2026`의 `intelligentai/recovery/dlpc-20261006/announcer-df90eb10-20261007/`에 두 파일을 저장하고 전체 다운로드 SHA-256을 대조했다. 비밀값·사용자 녹음은 포함하지 않았다.

- Runtime file ID: `4_ze5cc71977ef1880ca60b0c17_f11679934c7b4394b_d20261007_m015732_c005_v0501017_t0020_u01791338252241`
- Installer file ID: `4_ze5cc71977ef1880ca60b0c17_f107e77b17144395f_d20261007_m015757_c005_v0501042_t0031_u01791338277519`

운영 적용 시 persistent home의 진단 복구 도구도 이 B2 runtime과 새 LLM 디렉터리를 사용하도록 갱신한다. 이것은 별도로 승인 대기 중인 ‘재부팅 후 production 자동 접수 재개’ 구현과 구분한다.
