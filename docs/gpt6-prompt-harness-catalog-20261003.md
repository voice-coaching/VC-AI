# GPT-6 시스템 프롬프트·하네스 선택 목록

후속: 사용자가 H5의 canonical v4 적용을 선택했다. [파생 하네스 구현과 적용 상태](canonical-integration/h5-application-20261003.md)를 따른다. 아래 현재 적용 관측은 전환 전 조사 시점이다.

2026-10-03 작성. 작업 유형은 외부 연동 조사·문서화다. `.codex` 전역·외부 연동·문서화 규칙에 따라 소스, Git 이력, RunPod 동결 원본을 대조했다. 아래 H1–H6은 사용자가 선택하기 위한 문서 식별자이며 API에 구현된 설정값이 아니다. 날짜는 **KST**이고 Git 기록·운영 관측·번들 이름의 시각을 구분한다.

## 날짜별 선택

| 선택 | 날짜·근거 | 프롬프트 동작 | 경로와 현재 상태 |
|---|---|---|---|
| H1 `legacy-20260918-initial` | 9/18 23:30 Git `c6c3872` | 근거 기반 최대 3개 연습, 설명·행동·연습·자가 확인 생성. 모델이 ACTIONABLE 후보를 선택하는 초기 지시 | legacy 초기 소스. OPENAI adapter는 있으나 당시 GPT-6 운영 적용은 이번 조사에서 미확인 |
| H2 `legacy-20260919-coverage` | 9/19 00:41 Git `3d89016` | 서버가 선정한 후보의 **개수·순서·ID를 모두 보존**. 모델이 임의로 한 후보만 남기지 못하도록 변경 | legacy 이전 버전. 당시 GPT-6 운영 적용은 이번 조사에서 미확인 |
| H3 `legacy-20260928-expert` | 9/28 13:40:07 운영 설정 로드 기록 | H2 계열에 **아나운서 사례 2개 전체와 사용 규칙** 추가. 관측 의미와 구체적 연습을 설명하며, 별도의 총평 5문장·140자 제한은 아직 없음 | GPT-6 legacy 과거 운영 버전. S7 감사 디렉터리에 원본 system 문자열 보존 |
| H4 `canonical-s7-20260929` | 번들명 `20260928T154608Z` = **9/29 00:46:08 KST** | H3 계열을 canonical 권한·G2P expectedRole·MFA 위치로 조정. PRONUNCIATION_REVIEW, scoped ID, 서버 판정 보존. 일반 연습 표현 허용, 영상·운율 개인 교정 금지 | **현재 canonical v4 연결 대상**. 동결 S7, `json_object` |
| H5 `legacy-20260929-concise` | 9/29 16:29 Git `93db6ad`; 후속 배포 관측 revision `5ea4712` | **총평 5문장 이하**, 실제로 최대 3개 action. action 한 문장·140자 이하. 내부 처리 설명을 총평에서 제외 | GPT-6 legacy 과거 운영 버전. `json_object`; 설명 문장 생성 가능 |
| H6 `legacy-20260930-visual-v2` | 9/30 01:30 Git `32d98e3`; 01:35 배포 | H5 + 영상 P0/P1. 네 텍스트 필드는 후보별 **allowedText에서 그대로 선택**. usedVisualStatementIds 참조, 관찰 문구·수치는 서버 렌더링 | **현재 legacy 연결 대상**. strict `json_schema`; 자유로운 상세 문장 생성 제한 |

H3은 설명을 생성하는 이전 legacy 동작, H5는 짧은 행동 총평, H6는 허용 문구 선택에 초점이 있다. H4는 canonical 입력·검증 계약에 맞춘 별도 계열이다. 날짜가 더 늦다고 H6가 H4를 대체한 것은 아니다.

## 현재 적용 관측

2026-10-03 **13:54:59 KST**에 기존 SSH 대상 `194.68.245.79:22132`의 파일을 읽었다. SSH 세션에는 RunPod ID 환경변수가 없어 관리 API상의 Pod 신원은 이번 조사에서 다시 확인하지 못했다. 다음 내용은 해당 SSH 호스트의 관측이다.

- 런타임 env: `AI_ANALYSIS_COACHING_PROVIDER=OPENAI`, `AI_ANALYSIS_COACHING_OPENAI_MODEL=gpt-6-astra`.
- canonical 설정: `AI_CANONICAL_LLM_ROOT=/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1`.
- 연결 core: `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda`.
- S7 원본 system 파일의 실제 SHA-256은 아래 H4와 일치했다.
- legacy의 실행 사본 `system.md`, `pronunciation-coaching-v1.md`, `visual-coaching-v2.md`는 각각 `e22b5cae…`, `d1cec994…`, `abc69b9a…`였다.
- 마지막 확인된 legacy 로드 로그는 10/3 **11:31:37 KST**, `coaching-visual-policy-v2`, `coaching_generation_v2`, 합성 digest `b424be80…`다.

이는 설정·파일·로드 로그 확인이다. 해당 요청의 GPT 호출 여부나 결과 품질 검증은 아니다. 이번 작업에서 생성 API 호출·추론·QA·설정 전환·재배포를 하지 않았다.

## 불변 식별자

`system SHA`는 완성된 system 메시지 문자열의 UTF-8 SHA-256이다. legacy의 `generation.promptSha256`은 전문가 사례 사용 시 `system + "\n" + 사례 파일 SHA-256`의 해시이므로 system SHA와 다르다. 공개 `promptRevision=pronunciation-coaching-v1`만으로는 버전을 식별할 수 없다.

| 선택 | system SHA-256 | legacy generation.promptSha256 |
|---|---|---|
| H1 | `eb3858c4e3f24c6e508fcbeffd5e6852415eff6b0a7fddae4c5173c92f863b76` | system SHA와 같음 |
| H2 | `5098a378cc63b78506d1a6fa8dbbbb55e1f07af20de10aef1e1d56963e1f6e07` | system SHA와 같음 |
| H3 | `701c042ebaf09e2ee3ccaa9af6d56fde7c1d24082265ed8714bb374f65185c19` | `29b079110f0d96a6b77b2e4070d38de7357d5e87bf4334b37c29340f2c656ebe` |
| H4 | `362ccc1f15d2e166110cca52d90fd3d6e81ee85646e96eb7018d8b8278898714` | canonical 계약으로 별도 관리 |
| H5 | `98332751799c13c53c30eba5afdd791aecd7cc0349f0118ccf8669c012c58145` | `2a23d1f776975227740a25cff6c1e48c6fc637fd9b5c6c62193407c3ac592cf6` |
| H6 | `bb31598cafeb8eed2385793bb6fb01104636ba34c6ab6b56f8e3090767618893` | `b424be80e08f7a1d929538e123a3f81b77cfd156ce581bb6778524db97522202` |

H1/H2/H5/H6는 해당 Git 객체를 읽어 문자열을 정적으로 조립·해시했다. H3/H4는 원격 보존 원본 바이트와 대조했다. H3/H6의 합성 digest는 기존 운영 기록과도 일치한다. H5 합성 digest는 정적 계산값이며 당시 로드 로그를 이번 조사에서 확보한 값은 아니다.

### 원문과 복원 기준

- H1/H2/H5/H6: 위 커밋의 `configs/pronunciation_feedback/prompts/system.md` + `coaching/pronunciation-coaching-v1.md`. H5/H6는 해당 커밋 `src/voice_coach/backend_analysis/expert_examples.py`의 `INSTRUCTIONS`도 포함한다. H6는 그 앞에 `coaching/visual-coaching-v2.md`를 추가한다. 조립 방식은 해당 커밋의 `coaching.py`가 기준이다.
- H3 원문: S7 루트의 `audit/original_combined_prompt.txt`. 9/28 운영과 9/29 Git 반영 날짜가 다르므로 Git 날짜를 운영 적용일로 대체하지 않는다.
- H4 원문: S7 루트의 `contracts/llm/llm_system_prompt.txt`.
- H4 manifest SHA: `1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73`.
- H4 dependency lock SHA: `d8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01`.
- H3–H6 전문가 사례 revision: `announcer-paired-examples-20260928-v1`, 사례 SHA `eb68e02e75bd1b816414c411dd97953c20cd08361a2f7da6b58b94341717f3f8`. 두 사례 전체를 제공하며 현재 녹음의 근거로 취급하지 않는다.

## 선택과 적용 범위

사용자는 `H3, 9월 28일 전문가 사례 버전`처럼 식별자를 선택하면 된다. **선택 시 legacy에 적용할지, canonical v4에 같은 표현 정책을 이식할지도 구분한다.** 현재 요청은 목록 작성이며 이 문서 작성으로 설정을 전환하지 않았다.

하네스는 system 문자열 외에도 입력 adapter, 후보 선정·guidance, 전문가 사례, 요청 설정, 출력 schema·validator, fallback을 포함한다. H3/H5 문자열만 H6에 넣어도 H6의 allowedText 검증 때문에 예전 자유 문장 동작으로 돌아가지 않는다. H4의 scoped ID/MFA/권한 계약에 legacy 문자열을 그대로 넣는 것도 일관된 하네스 전환이 아니다. 선택한 동작을 적용할 때는 이 묶음을 대조하고 기존 v4 처리 계약을 유지할 범위를 정해야 한다.

S7의 고정 요청은 `gpt-6-astra`, `reasoning_effort=low`, `store=false`, `json_object`, 출력 한도 1200, timeout 120초, 숨은 재시도 0이다. H6도 같은 모델·low·store=false를 사용하지만 출력 형식은 동적 strict JSON Schema다. 세부 입력 예산과 실행 설정은 각 revision의 loader가 기준이다.

STEP8 `canonical_llm_live_step8_20260928T163122Z_a1de23`은 후속 연결·검증 기록으로 분류했다. 이번 확인에서 별도의 시스템 프롬프트 원본 버전은 발견하지 않았다. 9/30 인계 감사와 10/2–10/3 통합·lease 수정도 확인된 프롬프트 변경 근거가 없어 새 버전으로 세지 않았다. 날짜만 있는 설계안과 Clova/Qwen용 prompt는 GPT-6 운영 선택 목록에서 제외했다. 목록은 현재 Git 이력과 확인된 RunPod 보존 자료 범위다.

## 근거 문서

- [전문가 사례와 9/28 운영 로드 기록](expert_feedback_rag_20260928.md)
- [짧은 총평 정책](concise_coaching_feedback_20260929.md)
- [짧은 총평 실제 배포 파일 확인](runpod_deployed_pipeline_20260930.md)
- [9/30 영상 P0/P1 배포와 제약](video-lip-analysis/p0-p1-deployment.md)
- [canonical 원본 위치·계약 차이](canonical-integration/README.md)
- [현재 프롬프트 조립 코드](../src/voice_coach/backend_analysis/coaching.py)
- [현재 GPT 요청 adapter](../src/voice_coach/backend_analysis/coaching_generator.py)
