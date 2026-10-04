# 자음 일치율 게이트 제거·9항목 채점 구현

2026-10-03 사용자 결정의 구현 기록. 이번 변경은 아직 운영에 배포하지 않았다.

## 계약과 책임

- 새 core에서 자음 일치율 REJECT/INCONCLUSIVE 경로를 제거한다. 오디오 손상·무음 검증과 기존 동결 릴리스는 유지한다.
- Seungun 전체 기대 음소를 채점에 사용하고 Native/P1은 기존 canonical 코칭 근거로 유지한다. 선택된 교정 후보만으로 전체 점수를 계산하지 않는다.
- 사용자 후속 승인: matchedClear=4, matchedFlagged=2, substitutedClear=3, substitutedFlagged=1, deletedClear=0, deletedFlagged=0.
- `phone-rubric-20261003-v1`: 모음25, 평음10, 경음10, 격음10, 마찰음10, 파찰음10, 비음10, 유음5, coverage10. coverage 단위는 `4*(sampleCount-deletedClear-deletedFlagged)`다.
- 서버가 정수 단위로 95/80/60/40% 경계를 높은 단계부터 비교한다. n=0은 null 및 배점 제외, n=1은 동일 산식이다. 최종 합계만 소수점 첫째 자리 HALF_UP으로 반올림한다.
- GPT SCORING은 서버 RUBRIC_RESULT를 그대로 반환해야 한다. 버전·항목 순서·단계·점수·JSON 형식을 검증하며 불일치, 근거 부족, API 실패는 UNSCORABLE이다. 대체 점수는 없다. GPT COACHING은 별도 요청이며 기존 H5 정책을 유지한다.
- 검출 점수는 확률이 아니다. 대본 간 난이도 동등성이나 100점의 완벽한 발음을 보장하지 않는다. 억양·속도·혀·입술·음색 등 검증되지 않은 항목을 가감점하지 않는다.

## 구현 파일

| 경로 | 책임 |
| --- | --- |
| `src/voice_coach/backend_analysis/phone_rubric.py` | 전체 음소 index/표지/정렬/검출 결정 검사, 정수 계산 및 응답 검산 |
| `configs/pronunciation_feedback/phone_rubric_20261003_v1.json` | 버전 고정 기준표 |
| `src/voice_coach/backend_analysis/gpt_rubric.py` | 채점 전용 transport 호출, 엄격한 JSON·공식 검산표 일치 확인 |
| `deploy/runpod/harnesses/phone_rubric_prompt.txt` | 채점용 시스템 프롬프트 |
| `deploy/runpod/harnesses/canonical_scored_bridge.py` | 바인딩 확인 → 채점 → 기존 H5 코칭; 채점 실패 시 FAIL_CLOSED |
| `deploy/runpod/build_canonical_scored.py` | 별도 core/H5 파생 릴리스 생성; 게이트 제거·Seungun 활성화 |
| `canonical_harness.py`, `canonical_bridge.py`, `canonical_result.py` | 새 identity, 모듈 무결성, 보관 근거 재계산, 공개 score/실패 연결 |
| `deploy/runpod/canonical_verify.py` | AWS 오프라인 검증; S7/H5/새 채점 하네스 모두 지원 |
| `deploy/runpod/export_canonical_verifier_frozen.py`, `stage_backend_verifier_bundle.py` | 다섯 동결 릴리스와 검증 앱 의존성의 완전한 소스 묶음, 비활성 설치 |
| `deploy/runpod/prepare_canonical.py`, `canonical_executor.py` | 신규 설정 기본 경로, 시작 시 채점 기준표 무결성 확인 |
| `docs/contracts/runpod_result_v4.schema.json` | 새 core/하네스 조합과 숫자 점수 계약; Backend와 동일 bytes |

파일명만 쓴 Python 파일은 `src/voice_coach/backend_analysis/` 아래다. 저장·HTTP·UI 경계에서 채점 산술을 중복 구현하지 않는다.

## 공개 API와 실패

성공 score는 overallScore, validity=RUBRIC_COMPUTED, reason=EVIDENCE_BOUND_PRACTICE_SCORE, rubricRevision, 순서 고정 9개 criteria[{criterionId,level}]를 제공한다. GPT 출력은 rubricRevision/criteria/overallScore 세 필드다.

채점 실패는 overallScore=null, validity=UNSCORABLE, reason=SCORING_FAILED, 분석 FAILED/완료 불가다. 입력이 ACCEPT가 아니면 NOT_AVAILABLE/INPUT_NOT_ACCEPTED다. 전달 크기 제한 실패는 NOT_AVAILABLE/RESULT_NOT_DELIVERED다. 과거 S7/H5의 null 점수 계약은 계속 허용하며 자동 재채점하지 않는다.

Backend는 검증된 callback만 기존 overall_score 컬럼에 저장한다. 항목·revision은 원본 callback 및 공개 canonical DTO로 보존한다. DB migration은 없다. FE는 서버 점수를 소수점 첫째 자리까지 표시하고 단계 null은 “평가 대상 없음”으로 표시한다. 칭호 시험의 canonical 제외 정책은 유지되며 이번 점수는 연습 점수다.

## 고정 identity

| 대상 | SHA-256 |
| --- | --- |
| 원본 core manifest | `95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2` |
| 새 core manifest | `6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776` |
| 게이트 제거 정책 | `c89f1ad8320105849fecbce8b969f5f48e4d2e4b7fa66bd0c546595902f00a73` |
| 새 H5 manifest | `820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6` |
| 새 dependency lock | `17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d` |

새 운영 경로는 `/workspace/intelligentai/releases/canonical_scored_core_20261003_v1`, `/workspace/intelligentai/validation/canonical_scored_h5_20261003_v1`이다. 기존 H5 코칭 프롬프트 SHA는 유지한다. 채점 프롬프트·기준표·계산기 SHA는 새 manifest/lock에 고정한다.

## 배포 순서와 검증 범위

1. Backend develop 및 FE dev 변경을 검토한다. 이전 H5 변경이 아직 미병합이면 이를 포함한 최종 브랜치를 기준으로 검토한다.
2. `build_canonical_scored.py --core-source <기존-core> --core-target <새-core> --h5-source <기존-H5> --h5-target <새-H5> --rubric configs/pronunciation_feedback/phone_rubric_20261003_v1.json`으로 포장하고 위 SHA를 대조한다. 새 경로만 생성한다.
3. `export_canonical_verifier_frozen.py --frozen-root <다섯-릴리스-상위폴더> --app-root <AI-repo>`로 완전한 AWS 검증 묶음을 출력한다. 폴더 이름은 core, llm, canonical_h5_20261003, canonical_scored_core_20261003_v1, canonical_scored_h5_20261003_v1이다. 모델·미디어·env는 제외한다. 비활성 stage 후 Linux Python3.12에서 `canonical_verify.py --check-installation`으로 identity를 확인한다.
4. 신규 접수를 중지하고 진행 중 실행을 확인한다. AWS 검증 묶음과 Backend를 먼저 교체한다. 새/과거 계약을 읽는 FE를 배포한다. RunPod 앱과 파생 core/H5를 별도 설치하고 새 경로·pipeline revision을 설정한 뒤 재시작 및 접수를 활성화한다. 실패 시 이전 경로와 앱 revision으로 복구한다.
5. 시작 검사는 소스/기준표/설정·private ACL 확인이다. Seungun 모델 및 reliability policy는 기존 runtime-assets를 참조하며 실제 추론 시 검증된다. 시작 검사만으로 전체 음소 추론이나 GPT readiness를 보장하지 않는다. 추가 Seungun CPU 실행과 최대 120초 GPT 채점 호출의 실제 시간·메모리는 개발자가 확인해야 한다. Backend 15분/FE 10분 제한을 임의로 늘리지 않았다.
6. 개발자 QA에서 정상·오음·삭제·누락 항목·채점 실패·재시도·callback/이력 표시를 확인한다. dispatchAttempts는 기존 코칭 호출 수이며 별도 채점 호출 수를 포함하지 않는다.

로컬 Python 구문 컴파일, JSON Schema 메타 검증, 양쪽 schema 일치, 동결 소스 hash 포장을 수행했다. 231개 파일의 검증 묶음을 생성했다. Java compileJava와 TypeScript noEmit도 확인했다. 저장소 지침에 따라 자동 테스트·fixture·브라우저 QA·실제 추론은 수행하지 않았다. 이 결과를 품질 QA 또는 운영 배포 성공으로 해석하지 않는다.
