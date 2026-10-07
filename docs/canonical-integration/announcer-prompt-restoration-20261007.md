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
- **운영 전환 완료.** 2026-10-07 11:30 KST DLPC 복원본과 AWS Backend 계약을 확인하고 신규 접수를 재개했다. 실제 생성·청취 품질 QA 통과를 의미하지 않는다.
- RunPod 관리 경유 없이 사용자의 DLPC 플랫폼 터미널 실행과 기존 AWS 관리자 SSH로 적용했다. AWS→DLPC 포워드는 기존 API 전용 구성을 유지한다.

## 적용 순서

1. 복구 코드 revision으로 새 LLM 묶음을 생성하고 위 digest를 대조한다.
2. 현재 기록을 보존한 채 AI source/schema, 새 LLM 디렉터리, AWS verifier, Backend JAR 및 runtime profile을 하나의 호환 묶음으로 준비한다.
3. 신규 접수 hold와 진행 작업 drain 후 양쪽 계약을 함께 전환한다. AI의 `AI_CANONICAL_LLM_ROOT`와 pipeline revision을 새 tuple에 맞춘다. 프롬프트 파일만 기존 디렉터리에 덮어쓰면 무결성 검증에 실패한다.
4. 실제 배포 source/bundle/schema, 분석·handoff·direct readiness를 각각 확인하고 접수를 재개한다. AWS 비밀 복구본과 B2 overlay pin도 활성 배포 묶음에 맞춰 갱신해야 재부팅 후 이전 프롬프트로 돌아가지 않는다.

아래 운영 관측을 기준으로 완료된 단계와 남은 단계를 구분한다.

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

## 11:11–11:16 KST 전환 관측과 설치기 수정

- 사용자가 DLPC 설치기를 실행해 `waiting_for_backend_activation`에 도달했다. AWS에서 direct readiness 503 및 active/direct 작업 0을 확인했다.
- 기존 runtime profile을 root 전용 `runtime-profile.before.json`으로 보존하고 준비된 profile을 적용했다. 기존 배포 도구의 `hold`에서 모든 drain 항목 0을 두 번 확인했다.
- Backend `84260c05f2dde80ebec3f0d522726cca1ca8ff63`를 활성화했다. 11:11:46 KST Spring 기동 완료, 재시작 횟수 0, handoff HTTP 200과 새 9개 schema를 확인했다.
- 설치기의 대기 조건이 Backend hold 중에도 `admissionEnabled=true`를 요구해 진행이 막혔다. Backend 응답은 정상적으로 `supported=true`, `admissionEnabled=false`였다. 접수 차단을 해제해 우회하지 않았다.
- 설치기는 새 schema의 정확한 일치와 `supported=true`를 활성화 신호로 삼도록 수정했다. `--resume-waiting`은 root 전용 상태·백업·기존 bundle·hold 소유권·변경되지 않은 설정을 확인한 후, 파일 교체 이전 대기 단계만 이어간다. 설치 전 active/direct 작업도 다시 확인한다.
- 수정 설치기는 Python 컴파일 및 diff 정적 확인을 수행했다. B2에 업로드한 전체 바이트를 다시 받아 hash를 확인했다. 실제 생성·품질 QA는 수행하지 않았다.
- 수정 설치기: 12,911 bytes, SHA-256 `999736994f6bed9f17047f2c6a5e66329804de4bf5811fda1d4885019e355dae`, B2 file ID `4_ze5cc71977ef1880ca60b0c17_f10720d922a8a0d9f_d20261007_m021450_c005_v0501000_t0027_u01791339290423`.
- 사용자에게 기존 대기 명령을 Ctrl+C로 중단한 뒤 `deploy/dlpc/resume_announcer_activation.sh` 실행을 요청했다. Backend 단계는 `ACTIVATED`이며 `resume`, AWS 비밀 복구본 identity 갱신, DLPC direct 접수 재개는 아직 수행하지 않았다. runtime bundle과 AI 실행 source 후보는 위 표의 값 그대로다.

## 11:30 KST 운영 전환 완료

- 사용자가 수정 설치기를 실행했고 `backend_activated` → `installing`을 보고했다. AWS 경유 authenticated deployment에서 새 source `df90eb10…`, runtime `b7170dda…`, LLM `def65f68…` 일치를 직접 확인했다.
- 새 분석 readiness 확인 후 기존 AWS 배포 도구 `resume`이 연속 readiness 3회와 계약·frontend·drain 검증을 완료했다. Backend release `84260c05…`는 `VERIFIED` → `COMPLETE`로 전환됐으며 maintenance hold가 제거됐다.
- root 전용 복구 설정 원본을 보존하고 AWS `dlpc-recovery-20261006/analysis.env`의 `AI_CANONICAL_LLM_ROOT`, `VOICE_COACH_BUILD_REVISION`, `AI_CANONICAL_PIPELINE_REVISION`만 새 identity로 갱신했다. 비밀값과 진단 복구의 admission 설정은 유지했다.
- 11:30:50 KST 공개 `/readyz`, `/direct/readyz`, 인증된 `/health/services`, `/health/deployment` 모두 HTTP 200. executor/handoff ready, GPT provider `OPENAI`, model `gpt-6-astra`, active/direct jobs 0을 확인했다. Backend handoff는 HTTP 200, supported/admission true, 9개 schema가 활성 profile과 일치했다. 독립 TTS `/health/ready`도 HTTP 200, status ready였다.
- DLPC direct readiness 정상화는 설치기가 AWS 새 복구 identity를 수신한 뒤 소유한 direct hold를 해제한 동작과 일치한다. DLPC 내부 최종 `state.json`은 관리 경로가 없어 직접 읽지 않았다. 설치 코드에서 이 단계 전 persistent home의 B2 runtime pin 및 새 LLM-aware 복구 도구를 갱신하며, 전체 재생성 복구 실험을 수행한 것은 아니다.
- 활성 AI runtime은 `df90eb10…`, Backend JAR는 `84260c05…`다. 후속 installer 수정 `917f656…`와 문서 commit은 운영 애플리케이션 source revision과 구분한다. 현재 채점은 유지됐으며 실제 녹음/GPT 생성·품질 QA는 이번 복구에서 실행하지 않았다.
