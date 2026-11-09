# AI-Hub 입술 모델의 영상 채점·GPT 연결

관측일 2026-11-09, J406. 작업 브랜치 `feat/aihub-lip-distributions-20261109`.
구현·격리 후보 패키지·두 영상의 실제 GPT 생성 검증을 완료했다.
운영 프로세스의 설정 변경·재시작·배포·PR은 수행하지 않았다.

## 연결된 실행 경로

`CanonicalAudiovisualService`가 기존 CORE/MFA와 영상 관측을 연결하고,
[ReferenceCatalog](../../src/voice_coach/visual_feedback/reference_catalog.py)가 H5에 고정한 모델을 읽는다.
[ResearchReference](../../src/voice_coach/visual_feedback/research_reference.py)가 실제 음소/역할/위상의
참조 평균·분산으로 점수를 계산한다. 이후 기존의 서버 종합점수·입술 우선 대상 선정·단일 GPT 요청·
브라우저 summary 흐름으로 이어진다. 별도의 채점 GPT 호출은 추가하지 않았다.

이번 학습 모델은 `voice-coaching.lip-reference-catalog.v6`의 `researchModel`에 넣고,
`researchModelSha256`으로 모델 원본을 고정했다. 실행 정책은 `AIHUB_RESEARCH_30FPS_V1`이다.
학습 모델 SHA는 `346e4d075083c1e58c95c464356b64206ae94e160a011f0c68093dfb3d61f944`다.
기존 충분한 화자 분리 참조표 v5와 구분하며, 모델의 검증 상태를 `PASSED`로 바꾸지 않는다.
참조에 없는 음소·관측·타이밍은 그대로 null이다. 없는 화자 독립 검증 인원을 만들지 않고 0으로 기록한다.

서버는 관측된 위상별 점수의 평균을 성분→음소→영상 순서로 합산한다.
성분을 표시용으로 반올림한 값을 음소 평균에 다시 넣던 차이를 수정해 오프라인 모델과
동일한 계산 순서로 맞췄다. 기존 60% 평가 범위를 충족하면 음성 60%·입술 40%로 결합하고,
미충족 시 기존 부분 점수 정책을 유지한다. CORE의 원본 바이트·기존 음성 채점표는 수정하지 않는다.

## 사용자 피드백

[프롬프트 보충 지침](../../configs/coaching/aihub_lip_supplement_v1.txt)을 실제 후보 H5에 추가했다.
최대 두 개의 낮은 입술 대상과 나머지 음성 대상을 선택하며 전체 세 줄·최대 다섯 문장·300자 제한을 유지한다.

- 사용자용 `practice`에는 대상 소리와 바로 할 수 있는 연습을 작성한다.
- 검증 여부·점수 신뢰도·미평가 항목·분석 한계에 관한 내부 설명을 `practice`로 옮기지 않는다.
- 관측되지 않은 성분은 언급하지 않는다. 평균 차이만으로 오발음이나 교정 방향을 확정하지 않는다.
- 점수·validationStatus·null 사유·근거는 내부 결과에 보존한다. 정확성 보장 문구로 대체하지 않는다.

브라우저 응답은 기존 `score.overallScore`, `visual.score`, `visual.scoreKind`, `visual.scoreScope`,
`coaching.feedback`를 사용한다. 음소별 좌표·측정값·학습 통계를 summary에 추가하지 않았다.
Frontend/Backend 소스나 운영 배포는 변경하지 않았다. 내부 H5 identity 허용값만
기존 v4/v6 계약에 추가했으며, 필드 이름·자료형·외부 API 버전은 유지했다.

## 실제 검증 결과

지정된 testvideo1·testvideo2의 보존 CORE·MFA·최근 실제 landmark 근거를 사용했다.
모델 학습 때의 영상과 다른 SHA이며, 이번 연결 검증에서는 CORE·영상 분석을 재실행하지 않았다.
새 모델의 채점·근거 replay·GPT 입력·단일 생성·응답 재검증·브라우저 요약 투영을 실행했다.
브라우저 요약 투영은 실제 서버 함수를 사용한 로컬 계약 검사이며 운영 화면 E2E 검사는 아니다.

| 항목 | testvideo1 | testvideo2 |
|---|---:|---:|
| 입술 점수 | 88.8 | 92.4 |
| 입술 채점 / 시각 평가 대상 | 9 / 14 | 9 / 14 |
| 기존 서버 방식의 음성·입술 종합점수 | 79.1 | 57.1 |
| GPT POST / HTTP 상태 | 1 / 200 | 1 / 200 |
| 사용자 피드백 줄 수 | 3 | 3 |
| 메타 설명 문구 검사 | 없음 | 없음 |
| 보존 CORE 불변 / 근거 replay / 응답 재검증 | 통과 | 통과 |
| 브라우저 요약에 음소별 측정값 없음 | 통과 | 통과 |

두 결과 모두 `SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID`다. GPT 설정은
`gpt-6-luna`, effort `none`, Standard `default`이며 자동 재시도는 없다.
실제 생성 문장에는 입술 벌림 또는 닿는 모양을 살피는 연습과 문장 읽기 연습이 포함됐다.
이는 두 지정 영상의 실제 생성 관측이며 모든 후속 생성의 언어 품질을 보증한다는 뜻은 아니다.
근거/사용자 피드백 문자열은 비공개 검증 결과에 보관한다.

필요 회귀 검사 125개가 실패·오류 없이 통과했다. 새 identity 등록·내부 계약 enum·
임시 모델 상태 보존·점수 위변조 거부·단일 호출 설정·GPT 입력·공개 projection을 포함한다.
회귀 검사와 생성 검증 시점의 소스 diff SHA-256은
`c9b784f8c3175d551a0f88af2c33ea8b28405be53d3033698acf3ad37d6b1505`다.
이후 변경은 문서이며, 해당 hash를 최종 commit/head의 PR 전 실제 음성 회귀 영수증으로 대체하지 않는다.

## 패키지와 산출물

새 H5 root의 공통 부모:
`/home/2020067/.local/share/intelligentai/data/olkavs-20261109/runtime-candidate-20261109/`.

| 후보 | H5 manifest SHA-256 |
|---|---|
| `canonical_aihub_lip_direct_20261109_v1_h5` | `920570f14c74f7b8db4c72982fb3a5b6f992caa4f27401c37caaf2f9549aba6f` |
| `canonical_aihub_lip_internal_20261109_v1_h5` | `9a1d148b8c6f19a88e62b38f9eb0f8ae94bdee198f20fd3e997967383d4994c2` |

공통 prompt SHA는 `b8e4b50784c85c2dcf5deb530c7decdfb7083cca39eb9bac6b577d6c961db62d`다.
H5는 모델·프롬프트·bridge를 dependency lock으로 고정하고 기존 CORE를 참조한다.
새 direct/internal identity·스키마 분류·coordinated 배포 경로를 등록했다.
이는 운영 root를 새 후보로 선택하거나 서비스를 재시작한 것이 아니다.
실제 GPT 두 건은 direct 후보를 사용했으며 internal 후보는 등록·패키지·계약 검증 범위다.

실제 호출·검증 원본은 위 부모의 `connected-validation-v2/`에 있다.
각 영상의 `VISUAL_EVIDENCE.json`, `GPT_REQUEST.private.json`, `GPT_RESPONSE.private.json`,
`BRIDGE_RESULT.json`, `provider-observation.private.json`, `verification.private.json`과
전체 `verification.private.json`을 새로 생성했다. 이 후속 생성 자료는 B2에 업로드하지 않았다.

사용자가 별도로 승인한 이전 모델·두 평가·검증 요약·학습 근거 5개 파일은 비공개 B2
`speakai2026/datasets/aihub/538/lip-population-v1/derived/`에 총 36,944,869바이트를 보관했다.
5개 모두 전체 재다운로드 SHA-256이 일치하며, 파일별 정확한 B2 version/fileId·SHA·크기는
로컬 `model-artifacts/파일명.b2-receipt.json`에 보존했다. 비밀값은 포함하지 않았다.

운영 반영 시에는 후보 source와 두 H5를 함께 선택해야 한다. PR 생성/merge 단계의
지정 음성·장애 사례 실제 회귀는 [AGENTS.md](../../AGENTS.md)의 현재 요구를 별도로 따른다.
이번 두 건을 전체 음성 회귀·운영 브라우저 연결 테스트로 보고하지 않는다.
