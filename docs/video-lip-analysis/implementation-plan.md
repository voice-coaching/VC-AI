# GPT 연결 수정·구현 계획

작성일: 2026-09-30. **P0/P1 구현·RunPod 배포 완료: `32d98e3`, 01:35 KST. [실행 기록](p0-p1-deployment.md)을 따른다. P2는 비활성이다.** 아래 계획의 과거 기준 SHA와 제안은 작성 당시 기록으로 유지한다. 실제 생성·품질 QA는 미실행이다.

[목차](README.md) · [목표 설계와 입력 예시](gpt6-integration-design.md)

## 1. 범위와 유지할 결정

우선 목표는 같은 후보에 연결된 영상 관찰을 GPT가 오해 없이 사용하도록 하는 것이다. 개인별 입모양 교정 활성화는 별도 검증 후의 후속 단계다.

- `gpt-6-astra`, 음성 분석 1회, 코칭 후보 최대 3개, 요청당 GPT 생성 최대 1회와 실패 시 deterministic fallback을 유지한다.
- Seungun 원본·선택 음소·digest·시각 출처, 음성 결과 우선 보존을 유지한다.
- Clova 재도입, 영상 실패 때 LLM에게 영상 판정을 대신 맡기기, 점수/STT 생성은 제외한다.
- 내부 HTTP에 동의 증빙·요청별 grant·서명·정책 revision을 추가하지 않는다. 서버 내부 근거 정책 revision과 혼동하지 않는다.
- 두 전문가 사례 전체 주입, 짧은 총평 정책을 유지한다. raw 미디어의 새 외부 전송은 제외한다.

현재 로컬 HEAD는 `76f6933a117e35e335ff9c0b3dfc418ec0d7f102`이며 배포본과 일부 provider/조립 파일이 다르다. 구현 시작 시 [배포 근거](deployment-evidence.md)의 파일과 다시 대조해야 한다. 이전 로컬 Clova 분기를 최신 RunPod에 덮어쓰는 방식으로 배포하면 안 된다. 이 문서 작성에서는 새 remote fetch·Pod 조사·Backend 소스 검토를 하지 않았다.

## 2. 파일별 변경안

아래 경로는 저장소 상대 경로다. 새 파일명은 제안이며 이번에 생성하지 않았다.

| 위치 | 필요한 수정 |
|---|---|
| `src/voice_coach/backend_analysis/coaching_evidence.py` | 후보와 영상 observation의 명시적 연결; `project_candidates`에서 영상·statement 참조도 함께 필터링; source 원본은 보존 |
| `src/voice_coach/backend_analysis/adapters/visual.py` | 대표 `evidenceId`와 candidate ID 전달; aggregate를 의미 손실 없이 내부 DTO로 변환; 공통 오류와 후보별 오류 구분 |
| `src/voice_coach/pronunciation_attempts/services/video_attempt_facade.py` | 후보별 관찰 수집 경계와 stage/reason 유지; 부분 결과 처리 계약 명시 |
| `src/voice_coach/pronunciation_attempts/adapters/production_visual_sidecar.py` | 중심 프레임 시각·구간 내부 여부, beta 집계의 실제 유효 표본 수·사용 시각을 내부 측정 결과에 보존 |
| `src/voice_coach/visual_feedback/services/p6_lip_trajectory.py` 및 관련 models | 변화율의 이전 프레임 의존성과 단위, 평균/최대 집계에 사용한 표본 출처를 명시; 임의 표본 보간 금지 |
| 새 `backend_analysis/coaching_visual_policy.py` 제안 | binding·품질·reference 적용 범위를 검사해 interpretation mode와 허용 statement/action을 결정하는 순수 정책 계층 |
| `src/voice_coach/backend_analysis/coaching.py` | 최종 view 원자적 packing, 내부 생성 schema의 statement 참조 검사, 서버 fact renderer와 fallback, 기존 짧은 action/총평 제한 유지 |
| `src/voice_coach/backend_analysis/coaching_generator.py` | strict Structured Outputs와 refusal/중단 처리; endpoint/model/timeout/크기 제한 유지 |
| `configs/pronunciation_feedback/prompts/coaching/` | [목표 설계의 prompt 규칙](gpt6-integration-design.md)을 새 revision에 반영; schema와 맞춰 활성화 |
| `src/voice_coach/backend_analysis/expert_examples.py` | 과거 사례와 현재 시도 scope 분리 유지; 새 정책 이름에 맞춘 사용 규칙 검토; 전체 사례를 임의 절삭하지 않음 |
| `docs/contracts/coaching_result_v1.schema.json` 및 외부 serializer | 기존 schema는 그대로 보존; 공개 필드 확장이 필요하면 새 계약·reader 도입을 별도 수행 |

`production.py`, `prepare_http.py`, 배포 attestation은 새 정책/자산을 실제 로드하고 식별할 수 있게 함께 검토한다. 모델 자산을 바꾸지 않아도 새 policy·prompt·schema의 revision과 배포 파일 hash가 맞아야 한다.

## 3. 우선순위와 완료 조건

### P0: 연결 정확성과 출력 제약 — 기존 공개 계약 유지

1. 후보를 입력 예산으로 줄일 때 해당 후보의 영상 근거만 남긴다. 모든 참조의 존재·후보 소유 관계를 확인한다.
2. 새 내부 정책 계층을 도입하되 관찰/불가 모드만 사용한다. `NOT_VALIDATED`, 교정 금지를 유지한다.
3. 현재 6필드 GPT 출력에 strict schema를 적용한다. 문장 제한·coverage·JSON 중복 키·비유한 값·모델 identity 검사도 유지한다.
4. 영상 관련 생성 문장의 범위를 서버 template/allowlist로 제한하고, 위반 시 같은 후보의 일반 목표음 연습으로 fallback한다.
5. 기존 공개 `coaching.visual`은 현재 형태로 직렬화한다. 새 내부 provenance를 공개 schema에 몰래 추가하지 않는다.

완료 기준: 제외된 후보의 영상이 GPT view에 남지 않고, 영상이 없어도 음성 코칭이 유지되며, 영상 수치나 과거 전문가 문장만으로 개인 교정을 만들지 않는 구조가 코드에 명시되어 있다. 실제 품질 합격은 별도 개발자 확인이다.

### P1: 측정 의미와 실제 사용 추적 — 내부 계약 확장

1. cue별 실제 사용 시각·집계 종류·유효 표본 수를 측정기부터 전달한다. 값이 없는 단계는 unknown으로 남긴다.
2. `observationId`, `candidateId`, `audioEvidenceId`와 서버 provenance를 결합한 내부 DTO를 만든다.
3. GPT 내부 출력에 `usedVisualStatementIds`를 도입하고 allowlist와 대조한다. 사용되지 않은 영상 근거는 사용됐다고 표시하지 않는다.
4. 서버가 값·구간·중립적 관찰을 렌더링하고 GPT는 허용된 연습 설명만 담당하게 한다.
5. 요청 내 공통 decode 및 측정 재사용은 별도 최적화로 검토한다. 캐시 key는 같은 미디어·모델·측정 정책에 결속하고, 후보별 receipt는 재발급한다. 요청을 넘는 raw frame cache는 기본값으로 만들지 않는다.

완료 기준: 기본 중심 1프레임, beta 평균, 변화율 최대값의 출처를 서로 구분할 수 있고, decode 표본 수와 유효 표본 수를 혼용하지 않는다. 짧은 CTC 구간에 프레임이 없을 때 보류하며 구간을 임의로 늘려 관측했다고 쓰지 않는다.

### P2: 개인별 영상 교정 — 별도 승인 없이는 비활성

1. 음소/역할/문맥/촬영조건을 포함한 reference와 action 근거를 구축한다.
2. 개발자가 독립 평가 자료로 정렬·측정·교정의 유효 범위와 실패 조건을 확인하고 전문가가 교정 방향을 검토한다.
3. 제한된 claim별로만 `VALIDATED_CORRECTION`을 허용한다. 일치하는 reference가 없거나 기준이 불충족이면 관찰 전용으로 되돌린다.
4. 새 결과 계약과 Backend/FE reader가 준비되고 담당자가 승인한 후에만 writer를 활성화한다.

완료 기준: “asset에 calibrated라고 적혀 있다”가 아니라 어떤 대상·조건·교정에서 어떤 근거로 허용했는지 추적 가능하다. 현재 2개 참고 사례를 그대로 독립 평가 통과 근거로 사용하지 않는다.

## 4. 외부 계약과 호환성

현재 [coaching_result_v1 schema](../contracts/coaching_result_v1.schema.json)는 `additionalProperties:false`, `sampleCount:null`, `referenceStatus=NOT_VALIDATED`, `correctiveClaimsAllowed=false`를 요구한다. 따라서 실제 표본 수나 새 상태를 기존 공개 JSON에 넣는 것은 단순 필드 추가가 아니라 **계약 변경**이다.

권장 분리는 다음과 같다.

- 내부 측정 DTO/코칭 입력/LLM 출력 schema를 먼저 버전 관리한다. provider 출력 schema와 최종 callback schema를 같은 것으로 취급하지 않는다.
- P0/P1의 새 내부 데이터는 현재 callback에 투영할 때 새 필드를 제거하고 기존 null/false 의미를 유지한다. 더 풍부한 내부 데이터가 있다는 이유로 구형 계약에 잘못된 의미를 넣지 않는다.
- 외부 공개가 필요하면 새 coaching/result 계약, serializer, Backend ingestion·저장·조회 DTO, FE 표시를 함께 검토한다. 정확한 새 version 문자열은 양쪽 소스 검토 후 확정한다.
- Backend는 대상 브랜치를 확인하여 `develop` PR로 인계하고 담당자가 검증·배포한다. 새 reader를 먼저 준비하고 RunPod writer를 나중에 활성화한다. RunPod 배포는 별도 승인 작업이다.
- 구형 reader가 지원하지 않는 의미는 숨은 downgrade하지 않는다. 관찰 전용 구형 응답 또는 명시적 비활성 상태로 제한하고, 새 교정 claim을 구형 필드에 끼워 넣지 않는다.
- FE는 `summary`만 총평으로 사용하고 items의 explanation/practice/selfCheck를 다시 붙여 5문장 제한을 깨지 않도록 한다. Backend·FE 현재 구현을 이번에 확인했다는 뜻은 아니다.

## 5. 실패·예산·운영 가시성

| 상황 | 권장 처리 |
|---|---|
| 특정 후보 영상만 관찰 불가 | 해당 후보의 영상 statement를 비우고 음성 guidance 유지 |
| request/execution·PCM·영상 digest 불일치 | 영상 결합 거절; 부분 결과를 무조건 재사용하지 않음 |
| 전체 입력 예산 초과 | 후보 묶음 단위로 축소; 현재 두 전문가 사례 전체 정책은 유지 |
| 필수 최소 묶음도 예산 초과 | 동일 후보의 deterministic fallback; 보호 규칙/전문가 사례를 조용히 삭제하지 않음 |
| GPT 거부·timeout·출력 중단·schema 거절 | 기존 생성 실패 처리 및 fallback; 숨은 추가 생성 없음 |
| 허용되지 않은 statement·교정·후보 참조 | 출력 거절 후 같은 근거의 template |
| 참고 자산 무결성 불일치 | startup/configuration 단계에서 명시적으로 실패; 정상 readiness로 위장하지 않음 |

상위 lease/deadline의 남은 시간을 기준으로 영상과 GPT에 쓸 수 있는 시간을 제한한다. 설정된 provider timeout이 callback 마감보다 길면 그대로 기다리지 않는다. 생성 1회의 실패가 음성 근거까지 버리거나 슬롯을 무기한 점유하지 않게 한다.

관측 로그는 policy/prompt/schema/reference revision, 제공된/참조된/표시된 관찰의 개수, 보류 사유, provider usage·단계 지연·fallback reason 정도로 제한한다. payload 전문, 사용자 대본, 원본 미디어, 좌표 배열, API 키를 일반 로그에 남기지 않는다. `store=false`만으로 모든 저장·보존 정책이 해결됐다고 문서화하지 않는다.

## 6. 개발자가 확인할 인계 기준

아래는 완료 판단 기준이며 자동 회귀 도구·스크립트·평가 산출물을 생성하거나 실행하라는 지시가 아니다. 프로젝트 정책대로 QA는 개발자가 직접 수행한다.

- 후보 0/1/3개 및 예산 축소에서 후보·영상·statement 참조가 맞고 원본 source가 변하지 않는가.
- 영상 없음/얼굴 없음/복수 얼굴/QC 실패/약한 sync/짧은 CTC 구간에서도 없는 관측을 만들지 않는가.
- nearest frame이 구간 밖에 있는 경우, 실제 시각과 불확실성을 보존하고 개인 교정을 막는가.
- decode 프레임 수를 유효 표본 수로 대신 쓰지 않고 실제 cue별 사용 표본을 기록하는가.
- 과거 전문가 사례의 “입모양 크게”가 현재 사용자 진단으로 복사되지 않는가.
- 모든 자유 텍스트 필드에서 금지된 영상 원인 추정이 차단되고, 일반 목표음 연습은 유지되는가.
- refusal/중단/잘못된 ID/미허용 claim에서는 같은 후보의 안전한 fallback이 나오는가.
- 총평은 5문장 이하, action은 한 문장·UTF-16 140 이하이며 상세 정보가 총평에 다시 붙지 않는가.
- 구형/new reader, 설정·자산 mismatch, 취소/deadline 처리와 실제 화면 표시가 각각 확인됐는가.

배포 인계 시 변경한 양쪽 SHA, 계약·prompt·자산 revision, 실제 수행한 확인과 미수행 항목, reader/writer 활성 상태, 되돌릴 이전 package를 기록한다. 문제가 생기면 신규 교정 기능부터 비활성화해 관찰/음성 코칭으로 복귀하고, 계약·코드·prompt·자산을 일관된 이전 묶음으로 되돌린다. 이 문서 작성에서는 코드 변경·QA·commit/push·PR·배포를 하지 않았다.
