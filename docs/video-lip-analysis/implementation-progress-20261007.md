# 음성·영상 통합 구현 진행 기록

2026-10-07. **일부 기반 구현 완료, 전체 계획 미완료. 운영 경로는 여전히 음성 전용이다.**

## 소스와 작업 위치

- AI: `origin/main` 조회 결과 `f336a80`에서 `feat/audiovisual-v6-20261007` 분리.
- Backend: `6edbd0f`에서 동명 브랜치 분리. origin/develop은 `5e2d468`; Backend 기반에는 아직 열린 PR #113의 DLPC 변경 6개 commit이 포함된다.
- FE: 최신 dev `ff3dec9`, 검토 checkout `2bce7c3`의 영상 연습·공통 client 소스를 확인했다. FE 제품 코드 변경은 아직 없다.
- 사용자 원래 checkout의 기존 수정은 유지했다. 제품 변경은 `CreatorTemp/ai-audiovisual-20261007`, `CreatorTemp/backend-audiovisual-20261007`에 있다.

## 구현한 범위

| 위치 | 실제 구현 |
| --- | --- |
| AI `visual_feedback/reference_distance.py` | 유효 PTS·프레임 수·feature 크기 검사, phase band와 연속 이동 제한 WDTW, 누적 비용 최소 경로/길이 정규화, 별도 reference similarity·단조 보정 적용. 보정 외삽 금지 |
| AI `visual_feedback/lip_geometry.py` | pixel aspect를 보존한 얼굴 기준 scale/roll 정규화와 입술 높이·폭·면적. 입 너비 자체로 전체 shape를 정규화하지 않음 |
| AI `visual_feedback/audiovisual_evidence.py` | 명시적 MFA anchor와 검증된 시간 map 기반 join, 동일 phone/role/context/mapping 참조, 참조 median, self-comparison 차단, 보정 누락 시 null, 적격 점수만 집계 |
| AI `visual_feedback/parallel_analysis.py` | 주입된 async audio/video port 병렬 실행, 영상 typed 오류/timeout과 무결성 오류 분리, 전체 deadline/cancel 시 task drain. 실제 worker port와 운영 executor에는 아직 미연결 |
| AI `backend_analysis/media_preparation.py` | 공유 schema와 JCS digest 기반 media receipt 검증, materialized PCM/video binding 검사 |
| 양쪽 `docs/contracts/runpod_media_preparation_v1.schema.json` | 같은 바이트의 media receipt schema. probe start/time base는 수집하되 syncStatus=UNVERIFIED, 검증된 시간 map으로 간주하지 않음 |
| 양쪽 `docs/contracts/runpod_analysis_request_v4.schema.json` | 새 입력 계약 파일. 기존 HTTP parser/admission은 아직 v4를 허용하지 않음 |
| Backend normalizer/model/writer/store | source/PCM/video 해시와 probe 메타데이터 생성 → 같은 recording 등록 transaction의 JCS receipt 저장 |
| Backend V41/V42 + execution store | 새 preparation 테이블과 immutable execution media snapshot. 과거 녹음의 근거를 추정 backfill하지 않음. migration 운영 적용 없음 |
| Backend request/publisher | retry 음질 PASS 재검사, 영상 v5 profile 불일치를 명시적인 422로 구분 |
| 참조 catalog | 사용자 지정 testvideo1 digest/크기/대본 출처 기록. 실제 MFA/landmark와 보정 자료를 만들었다고 표기하지 않음 |

계산 모듈의 호출자가 전달하는 reference/calibration/time map은 신뢰 경계 밖 API 입력이 아니다. 실제 모델·참조 loader와 pin 검증을 연결하기 전에는 운영 점수나 GPT 근거로 사용하면 안 된다. `observations`와 허용 교정 action 생성은 아직 연결하지 않았다.

## 아직 남은 구현 — QA만 남은 상태가 아님

1. Backend receipt에 검증 가능한 PCM→video 시간 변환/회전 근거를 추가하고 실제 feature decoder와 MFA anchor adapter를 연결.
2. 참조 영상의 모델 출력·검수 span·mapping/calibration loader와 고정 자산 패키지 준비. 단일 파일로 전 음소 보정 승인 금지.
3. v6 result/handoff v2 schema와 양쪽 parser·semantic verifier·frozen H5/GPT 입력 및 결과 association 구현.
4. Backend 새 profile admission/snapshot 전달, artifact 종류 확장, result-first/handoff/commit/B2 archive의 동일 검증·삭제 lifecycle, 공개 view v3와 result-contract 실제 profile 조회 구현.
5. FE `src/lib/api/analysis-capabilities.ts`, `canonical.ts`, `canonical-analysis.ts`, `src/routes/lip-practice.tsx`의 v6 요청·파싱·입술 결과 표시 구현. 현재 화면은 이미 영상 업로드·등록·선택을 수행하지만 VIDEO capability와 v5 client에 묶여 있다.
6. 위 코드가 연결된 뒤 개발자 실제 QA·DB migration 확인·DLPC 신규 bundle/복구 pin 생성과 전환. 현재 readiness나 점수 품질을 통과로 간주하지 않음.

## 확인한 범위

- Backend `gradlew compileJava --offline --no-daemon`: 최신 변경 포함 성공. Java 21 사용. 자동 회귀 테스트/DB migration 실행 없음.
- Python 추가 모듈: source compile/AST 정적 확인. 실제 media decode·MFA/GOP·GPU·GPT 실행 없음.
- 공유 JSON schema: JSON 구문·로컬 참조·양쪽 바이트 일치 정적 확인. 실요청 roundtrip 검증 아님.
- docs 링크/whitespace 정적 확인. 배포·서버 설정·admission·기존 frozen identity 변경 없음.

AGENTS의 개발자 직접 QA 규칙을 유지한다. 단, 미구현 1–5를 이 규칙 때문에 완료했다고 하거나 QA만으로 해결된다고 보고하지 않는다.
