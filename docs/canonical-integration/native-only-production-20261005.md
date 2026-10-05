# Native 전용 전환 준비 및 RunPod 보정 자료 조사

2026-10-05. 기준 Git main `bcfadc8`, 작업 브랜치 `fix/native-only-production-20261005`.

## 사용자 결정과 현재 상태

사용자는 `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda`를 기준으로 구형 실행 경로를 완전히 제거하도록 요청했다. 최신 지시는 **최신 파이프라인을 먼저 배포하고 실제 출력의 채점 근거 필드를 측정**하는 것이다. 이전의 보정 자료 확보 후 전환 조건을 대체한다.

Native v4 패키지와 AWS 검증기를 배포하고 2026-10-05 21:38 KST 접수를 재개했다. 실제 API 요청 84·85를 측정했다. 분석 실행 승인은 정확한 core/H5 digest로 등록하며, 채점 정책은 DRAFT를 유지한다. 분석 실행과 수치 채점의 근거 조건을 분리한다. **Native 코어는 성공했지만 기존 채점 어댑터가 UNSCORABLE을 반환해 최종 작업 상태는 FAILED다. 정상 채점·코칭 완료로 보고하지 않는다.**

## 추가 제거한 구형 경로

기존 Native v2 빌더는 구형 검출 모듈 5개를 제거했으나 다음 경로가 남아 있었다.

- `runtime_bundle/demo_e2e.py`: `from korean_pronunciation_poc import final_pronunciation_detector` 방식으로 구형 검출기를 불러오는 실험 실행 진입점.
- `runtime_bundle/launch_experiment.py`: 위 진입점을 실행하는 launcher.
- `pronunciation_calibration.py`, `pronunciation_mvp_policy.py`: 구형 검출기 보정·판정 정책.
- `source/config/poc.toml` 및 config loader: 구형 checkpoint·artifact·reliability policy 설정과 경로 해석.
- 출력 schema의 구형 검출기 default metadata.

`deploy/runpod/native_source_pruning.py`에서 위 코드를 제거했다. runtime source identity의 제거 파일 항목도 함께 제외했다. 비활성 호환 설정은 `enabled=false`만 허용하고 구형 경로 설정은 거부한다. 검출기 metadata는 `RETIRED_NOT_EXECUTED`, 구형 모델 SHA는 null이다. AST 검사는 module 직접 import뿐 아니라 `from package import retired_module` 형태도 확인한다.

총 제거 목록은 기존 검출기 관련 5개 + 보정·정책 2개 + 실험 실행 진입점 2개로 **9개**다. MFA 자체는 지정 원본에 포함된 실행 자산이므로 제거하지 않는다. 과거 보고서의 파일 경로 문자열은 신규 실행 fallback이 아니다.

`deploy/runpod/build_canonical_native.py`는 원본을 덮어쓰지 않고 Native v4 파생 패키지를 만든다. 신규 실행은 이 버전만 허용한다. 구형 패키지는 과거 결과 검증용이며 추론 fallback이 아니다. 전환 실패 시 구형 추론으로 자동 복귀하지 않는다.

## 패키지와 확인 범위

- 기준 원본 manifest SHA: `95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2`.
- 로컬 패키지: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/native-production-v4-20261005/`.
- core: `canonical_native_core_20261005_v4`, SHA `f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841`.
- H5: `canonical_native_h5_20261005_v4`, manifest SHA `efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428`.
- H5 dependency lock SHA: `1b8cb378b889817e14bf06be5065b04016589f6b74dcd5501776dde758f38259`.
- `calibrationStatus=DRAFT`. 실행 allowlist 등록은 사용자의 분석 배포 지시이며 채점 보정·품질 QA 승인과 다르다.
- Python 75개 소스 컴파일 및 Python 3.11 문법 확인, core/H5 manifest의 모든 파일 digest 대조, `git diff --check` 통과.
- 이후 사용자가 명시한 실제 음성 요청 2건을 수행했다. 첫 요청의 디렉터리 접근 오류를 수정하고 두 번째 요청의 결과 전달·DB 저장·B2 보관까지 확인했다. 자동 회귀 테스트·브라우저 QA는 하지 않았다. GPT 호출은 채점 어댑터에서 차단되어 0회다.

## RunPod에서 찾은 보정 관련 자료

아래 경로는 `/workspace/intelligentai/validation/` 기준이다. 대용량 baseline/H18 cache는 기존 검출기 자산으로 구분했고 Native 평가 보고서·정책·신규 녹음 검증·최신 인계 문서를 직접 확인했다.

| 자료 | 확인된 범위 | 9항목 전체 채점에 부족한 점 |
|---|---|---|
| `native_korean_phone_eval_20260928T1109KST/evaluation/evaluation_status.json` | 26개 녹음 확보, `MODEL_MAPPING_BLOCKED`, 전체 비교 추론 미실행 | 모음 매핑 및 전체 음소 평가 미완료 |
| 같은 디렉터리 `native_model/phone_mapping_audit.md` | E/iE/oE가 여러 한글 모음 대립을 구분하지 못함 | 개별 모음 정확도를 추정할 수 없음; 관측 동치 범주의 명시적 계약 필요 |
| `p1_native_demo_refinement_20260928T1310KST/contract/refined_p1_policy_contract.json` | CPU FP32, 유일한 자음 run margin `>= 4.529027462005615`, 삭제 경고 없음 | 자음 경고용 임계값이며 모음·삭제·전체 채점 기준이 아님 |
| 같은 디렉터리 `final_report_ko.md` | 개발 세트 26개 녹음, 의도 target 22/33, 정상 오경고 6단어 | target은 인간이 확인한 개별 발음 정답 라벨이 아님; 독립 전체 채점 검증 아님 |
| `refined_p1_new_recording_smoke_20260928T143000KST/final_report_ko.md` | 새 녹음 4개, target 14/16, 정상 참조 경고 0/73 | 자음 정책 smoke. 정상 참조도 사용자 청취 확인 라벨로 승격하지 않음 |
| `aihub71469_demo_recall_policy_20260928T123725KST/contract/demo_recall_policy_contract_v1.json` | 자음 비교 계약 | `full_phone_mapping=MODEL_MAPPING_BLOCKED_UNCHANGED` |
| `team_handoff_package_20260929T083659Z_WKE0R0/` | 코칭·LLM 인계 보고서 | 새 Native 전체 채점 보정 정책 미확인 |
| `final_handoff_revision_20260930T104815Z_276aba/DEPLOYMENT_HANDOFF_KO_V2.md` | S7 score null / NOT_CALIBRATED, 기존 scoring은 compatibility only | 현재 요구한 전체 음소 점수의 보정 근거가 아님 |

찾은 자료를 다른 목적의 보정 자료로 재해석하지 않았다. P1 threshold를 전체 모음·삭제에 복사하거나 구형 detector의 0.813470 임계값을 Native logits에 이식하지 않는다. 현재 검색 범위에서 승인 가능한 전체 채점 정책·독립 검증 보고서는 발견하지 못했다.

## 전환 및 측정 순서

1. 새 core/H5 identity 및 공유 결과 스키마·Backend verifier를 함께 등록한다.
2. 진행 중인 실행을 비운 뒤 AWS와 RunPod의 Native 전용 실행을 활성화한다.
3. `testaudio1.m4a`와 지정 연습 문장으로 실제 요청을 보내고 구간별 시간과 원본 근거 필드를 기록한다.
4. 관측 결과·채점 가능 여부를 구분해서 보고한다. 없는 필드와 점수는 만들어내지 않는다.

보정 자료 부족을 분석 배포의 선행조건으로 사용하지 않는다. 수치 채점이 가능하다는 보증으로도 해석하지 않는다.

## 운영 적용 결과

- RunPod 실행 코드: `ed73c19d8bd80507423be02b73e279b9f08580d8`, bundle `7d5e5206b7ff8e7c1d4b051d31c8614cf553800d55e7e9be596f3f92e23a0fb9`.
- 활성 core: `/workspace/intelligentai/releases/canonical_native_core_20261005_v4`. 지정한 September 28 원본의 Native 모델·MFA·P1 정책을 보존한 파생 릴리스다.
- 활성 H5: `/workspace/intelligentai/validation/canonical_native_h5_20261005_v4`.
- AWS JAR: `caf8c3384b1ed47eec5c9b29f1240641387876b3`, SHA `3c024f630db35dd33b487c05dc9ccfead03f07980d35d323fb727e023e69fdf3`.
- AWS verifier: `8178f478498057ea1decb898f5624035ea3c6bfed4d811adc3f17b87a5a54214`, 별도 Python 3.12/NumPy 2.4.6 환경. 이 번들의 구형 코드들은 과거 저장 결과 검증 전용이며 새 추론에 사용하지 않는다.
- FE: GitHub Production 배포 성공 revision `20429a40e3d61617d94e1edc6e3f34897b2c3dd5`, 운영 페이지의 공개 JS 18개를 배포 프로필에 고정. FE 코드는 이번에 변경하지 않았다.
- AWS 배포 상태 `COMPLETE`, 유지보수 hold 해제. RunPod services/handoff readiness 통과.
- 최초 요청 84의 `EXPRESSION` 오류: 새 `direct_analysis` 디렉터리가 root:root 0700으로 생성되어 서비스 계정이 접근하지 못했다. root:voice-coach 0750으로 수정하고 실제 서비스 UID의 import를 확인했다. 배포 도구도 새 source parent 권한을 명시하도록 수정했다. 구형 추론으로 롤백하지 않았다.

## 실제 측정: 요청 85

2026-10-05 21:49:54–21:51:08 KST. session 136, recording 101, request `be482733-e750-4fdc-aa12-0049f0619a58`, execution `ece02b7b-e682-4b55-b4ea-22a3f5b5af92`.

입력은 `tests/testaudio1.m4a`, 문장 **한국은행은 오늘 기준금리를 동결한다고 발표했습니다.**. 16 kHz mono WAV 5.1413125초. 측정 경로는 `FE의 /api/backend 프록시 → AWS v5 접수 → RunPod Native → v3 결과 조회`이며 브라우저 렌더링 시간은 측정하지 않았다. 로그인·업로드는 아래 분석 POST 시간에 포함하지 않는다.

| 구간 | 초 |
|---|---:|
| 분석 POST 시작 → RunPod 실행 단계 시작 | 10.030 |
| 예약·다운로드 병렬 준비 | 3.228 |
| Native 코어 전체 | 32.031 |
| 결과 어댑터 처리, GPT 호출 없음 | 2.146 |
| RunPod 결과 preflight | 1.903 |
| 결과 공개 | 2.908 |
| 공개 완료 → 측정 클라이언트 결과 수신 | 0.918 |
| **분석 POST → 첫 공개 결과 수신** | **53.165** |

코어 내부 Native forward는 **0.571초**, MFA 요청 구간 **27.326초**, 근거 생성 **0.019초**, 코어 내부 전체 **29.621초**다. 외부 core 전체와 내부 전체 차이 **2.411초**는 생산자 시작·검증·출력 처리 등에 해당한다. 내부 Native·기대 음소 준비는 병렬이므로 각 구간을 단순 합산하지 않는다. 최초 서비스 기동 Native cold prepare **80.709초**는 요청 전에 끝나 위 표에 포함되지 않는다.

AWS 비동기 handoff 전송 1.936초는 결과 공개 뒤 시작해 첫 결과 수신과 겹친다. 첫 결과 수신은 21:50:47.207, DB analyzed_at은 21:50:57.185, B2 ARCHIVED는 21:51:08.193이다. DB·보관 완료를 기다리지 않고 결과가 공개됐다. handoff `COMMITTED`, archive `ARCHIVED`, 각각 1회 처리했다.

이 수치는 **정상 GPT 코칭 완료 시간 비교에 사용하면 안 된다.** 채점이 차단되어 GPT 생성 시간이 없다. 최초 요청 84는 core 23.712초 후 접근 권한 오류로 실패했고, 위 표는 수정 후 요청 85다.

## 최신 출력의 채점 근거 필드

| 실제 존재하는 필드 | 요청 85 관측 |
|---|---|
| `nativeScoringLogits` | FP32 256 × 45, digest 포함 |
| `nativeScoringEvidence.positions` | 기대 음소 58개, 기대 표지·항목·역할·원본 index |
| `alignmentOperation` | MATCH 51, SUBSTITUTION 7; 발음 정확도 정답 판정은 아님 |
| `observedRuns`, `mfaWords` | 관측 run 58개, MFA 단어 5개 |
| `nativeFrameSpan`, `mfaWordSpan`, `features` | frame/margin 근거 54개; 프레임 구간 미확정 2개 |
| 음소별 항목 표지 | 모음 23, 평음 11, 격음 1, 마찰음 5, 파찰음 1, 비음 12, 유음 5, 경음 0 |
| 모델 및 실행 근거 | Native revision `e26ff9d…`, weights `b2ec6635…`, inferenceCount 1 |
| 구형 검출기 상태 | `RETIRED_NOT_EXECUTED`, stage1 weights null |

**현재 9항목 채점 입력으로 완성된 필드는 없다.** `acousticAssessment`는 58개 모두 UNKNOWN이며, Native 토큰 매핑 미확정 2개와 프레임 구간 미확정 2개가 관측됐다. 기존 계산기가 요구하는 명확/불명확 오류·삭제 분류 및 그에 따른 evidenceUnits/RUBRIC_RESULT가 만들어지지 않는다. 기준표 자체의 변경 여부와는 별개다.

실제 결과는 core `ok`, 입력 판정 `ACCEPT`, 점수 `{overallScore:null, validity:UNSCORABLE, reason:SCORING_FAILED}`, adapter `FAIL_CLOSED/SCORING_UNSCORABLE`, generation `NOT_DISPATCHED`, dispatchAttempts 0이다. **GPT 답변은 없다.** 최신 모델의 원시 관측 부재가 아니라 현재 Native→채점 어댑터가 수치 응답 전에 차단하는 상태다. 점수 처리 계약을 후속 변경할 때 이 관측 결과를 사용하며 정렬 MATCH를 정확한 발음으로 임의 승격하지 않는다.

측정 원본의 비밀값 없는 요약은 로컬 `C:/Users/Public/Documents/ESTsoft/CreatorTemp/native-production-v4-20261005/`의 `pod-metrics-b.json`, `aws-metrics-b.json`, `client-timing-b.jsonl`, `evidence-summary.json`이다. 서버의 인증 상태 파일은 문서·커밋에 포함하지 않는다.
