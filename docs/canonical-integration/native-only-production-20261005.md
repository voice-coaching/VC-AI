# Native 전용 전환 준비 및 RunPod 보정 자료 조사

2026-10-05. 기준 Git main `bcfadc8`, 작업 브랜치 `fix/native-only-production-20261005`.

## 사용자 결정과 현재 상태

사용자는 `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda`를 기준으로 구형 실행 경로를 완전히 제거하도록 요청했다. 최신 지시는 **최신 파이프라인을 먼저 배포하고 실제 출력의 채점 근거 필드를 측정**하는 것이다. 이전의 보정 자료 확보 후 전환 조건을 대체한다.

Native v4 패키지와 AWS 검증기를 별도 경로에 배치하고 파일 무결성·설치 readiness를 확인했다. 활성화와 요청 측정은 진행 중이다. 분석 실행 승인은 정확한 core/H5 digest로 등록하며, 채점 정책은 DRAFT를 유지한다. 분석 실행과 수치 채점의 근거 조건을 분리한다.

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
- 추론·회귀 테스트·GPT 생성·브라우저 QA는 실행하지 않았다. 위 확인은 실제 동작·품질 검증이 아니다.

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
