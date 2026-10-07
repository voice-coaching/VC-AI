# GPT 음성·영상 피드백 간결화 — 2026-10-07

## 사용자 결정과 구현

피드백 전체를 최대 4문장으로 제한한다. JSON의 `feedback` 문자열 안에서 각 줄을
`* 대상 단어 → 구체적인 연습 방법`으로 작성하며, 한 줄에 한 문장만 쓴다.
현재 대본과 분석 근거에서 중요한 대상만 선택하고, 네 항목을 채우기 위해 교정을 만들지 않는다.
인사·제목·총평·마무리·별도 입술 관찰 단락은 추가하지 않는다.
구절 연결이나 입술 관찰이 필요하면 해당 단어의 문장 안에 통합하며 같은 4문장 한도를 적용한다.
어떤 단어에도 연습을 제안할 근거가 없으면 부족한 정보만 한 문장으로 설명한다.

정본은 [피드백 지침](../../configs/coaching/announcer_feedback_system_20261007.txt)이다.
과거 전문가 원문은 참고 사례로 보존하며, 그 사례의 길이보다 현재 4문장 지침이 우선한다.
[음성 H5 builder](../../deploy/dlpc/restore_announcer_prompt.py)와
[영상 H5 builder](../../deploy/dlpc/build_audiovisual_h5.py)는 같은 정본을 읽는다.
영상 builder도 frozen 부모에 들어 있던 옛 피드백 지침을 교체하므로 정본 수정이 실제 생성 프롬프트에 반영된다.
GOP 채점·배점·null 처리·JSON 응답 계약·모델·요청 토큰 설정은 유지한다.

## 소스 패키지 생성 결과

기존 immutable 패키지를 덮어쓰지 않고 새 이름으로 생성했다.
다음은 소스 생성물의 identity이며 운영 활성화 기록이 아니다.

| 항목 | 음성 `canonical_dlpc_gop_announcer_20261007_v2_h5` | 영상 `canonical_dlpc_audiovisual_20261007_v3_h5` |
|---|---|---|
| core manifest | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` | `4cc0cbacd41b5c6f2ac20e6e4840bc5406f4c7ab79ecf53702586eeb5874ecbc` |
| H5 manifest | `3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c` | `7eef5cfec091f3a886150296c23c558fc38a958b23d831dc83460a3aa135f091` |
| dependency lock | `58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4` | `b136a80b6fb7724a7fc6253bb886719db2b43b8154b9329e7c39bf5140262e70` |
| system prompt | `ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d` | `e0ad8c10033229fdeff4c0aa85aaef72af8245e4db728a5a0f5378f21f58eec6` |

음성 builder의 부모는 기존 GOP CUDA v2 H5, 영상 builder의 부모는 기존 announcer v1 H5와
AV core v2다. 각 builder의 기존 CLI로 새 destination에 생성할 수 있다.
기존 release 목록·실행 allowlist·Backend pin·운영 프로세스는 이번 변경에서 갱신하지 않았다.
배포 시 새 tuple의 검증/등록과 Backend revision 전환이 별도로 필요하다.

## 확인 범위

변경 Python 소스와 생성 패키지의 Python 소스를 컴파일하고 manifest/lock 파일 digest를 확인했다.
기존 생성 패키지와 비교해 피드백 지침 외 채점 프롬프트·JSON 지침 및 adapter·schema·요청 설정이
보존되는지 정적으로 확인했다. 실제 GPT 생성·음성/영상 추론·자동 회귀 테스트·운영 배포는 수행하지 않았다.
4문장 제한은 시스템 프롬프트 지침이며 서버에서 문장 수를 강제로 자르거나 JSON 채점 결과를 거절하지 않는다.
실제 응답 길이와 표현 품질 QA는 `AGENTS.md`에 따라 개발자가 수행한다.
