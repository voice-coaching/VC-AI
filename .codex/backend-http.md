# Backend–AI 계약 작업 안내

이름에 RunPod가 남아 있지만 최신 운영 이력은 DLPC다. [운영 안내](runpod.md)와 [조사 결과](../docs/project-survey-20261019.md)를 함께 사용한다. Backend/Frontend는 별도 저장소이며 이 checkout만으로 양쪽 구현·배포를 확정하지 않는다.

## 소스 대조 순서

| 경계 | 시작할 소스 | 확인할 사실 |
|---|---|---|
| 내부 HTTP | [http_api.py](../src/voice_coach/backend_analysis/http_api.py), [http_models.py](../src/voice_coach/backend_analysis/http_models.py), [http_runtime.py](../src/voice_coach/backend_analysis/http_runtime.py) | 인증, 신규 접수 버전, claim/heartbeat, 상태·취소, readiness |
| canonical 실행 | [canonical_executor.py](../src/voice_coach/backend_analysis/canonical_executor.py), [canonical_producer.py](../src/voice_coach/backend_analysis/canonical_producer.py), [canonical_resident.py](../src/voice_coach/backend_analysis/canonical_resident.py) | 동일 원본 연결, 실행·취소·프로세스 수명 |
| 실행 identity | [canonical_harness.py](../src/voice_coach/backend_analysis/canonical_harness.py), [native_execution.py](../src/voice_coach/backend_analysis/native_execution.py) | 과거 읽기 허용과 신규 실행 승인 구분, core/H5/lock pin |
| 영속 인계 | [canonical_handoff.py](../src/voice_coach/backend_analysis/canonical_handoff.py), [canonical_backend_journal.py](../src/voice_coach/backend_analysis/canonical_backend_journal.py), [canonical_recovery.py](../src/voice_coach/backend_analysis/canonical_recovery.py) | 동일 bytes 재전송, Backend 저장·검증, 복구·취소 fence |
| FE 직접 접수 | [direct_analysis](../src/voice_coach/direct_analysis) | 명시 활성화된 익명 `/direct` 경로, 로컬 journal, 결과와 후속 보관·이력 동기화 분리 |
| 영상 | [parallel_analysis.py](../src/voice_coach/visual_feedback/parallel_analysis.py), [canonical_service.py](../src/voice_coach/visual_feedback/canonical_service.py), [canonical_audiovisual_result.py](../src/voice_coach/backend_analysis/canonical_audiovisual_result.py) | 같은 시도·PCM/영상 digest·MFA/PTS 결속, 근거 한계 |
| 제한 파일 | [s3_object_store.py](../src/voice_coach/backend_analysis/adapters/s3_object_store.py) | 저장소 역할, 크기/MIME/SHA, 제한 workspace와 정리 |

## 버전 경계

`validate_analysis_request`는 request v4/result v6만 신규 접수한다. 음성 전용도 같은 계약을 사용하며 영상 부재를 명시한다. [통합 명세](../docs/canonical-integration/v6-unification.md)는 소스와 Backend 적용 조건을 구분한다.

| 경로 | 계약과 참고 문서 |
|---|---|
| 음성 canonical | request v4 → result v6, handoff v2. video=null, visual.status=NOT_PROVIDED. 가짜 영상·receipt를 만들지 않는다. |
| 음성·영상 | 같은 request v4 → result v6, handoff v2. [실제 영상 결속·활성화 조건](../docs/video-lip-analysis/audiovisual-v6-handoff-20261007.md). 실제 영상 identity·자산·설정·readiness를 추가 확인한다. |
| 공개 direct | UUID direct-analysis.v1. 내부 Backend 정수 ID를 만들거나 같은 버전 번호로 바꾸지 않는다. |

정본은 [contracts](../docs/contracts)와 검증·직렬화 소스다. [API 목록](../docs/api/endpoints.md)·[명세](../docs/api/specification.md)는 현재 checkout 기준이며 운영 배포 완료를 뜻하지 않는다. JSON schema 파일명, 내부 제어 버전, 요청 버전, 결과 버전, public view 버전은 서로 다르다.

## 변경과 인계

1. 평가·계획·구현·배포 중 범위를 구분한다. 양쪽 저장소가 필요하면 각 경로·브랜치·HEAD·미커밋 변경을 확인한다. sibling 이름이나 과거 snapshot으로 편집 대상을 추정하지 않는다.
2. 사용자 로그인·선택 녹음 소유 관계, 내부 양방향 서버 인증, 파일 무결성은 보존한다. 내부 HTTP에 동의 증빙·정책 revision·요청별 grant·서명을 추가하지 않는다. 별도 익명 direct 계약에 Backend 정수 ID나 권한을 가짜로 채우지 않는다.
3. 원본 음소·digest·index·시간 출처, 상태·세대·멱등성·마감·취소·재전송 의미를 유지한다. 근거 전달 재시도를 추론/GPT 재실행으로 바꾸지 않는다. 오류·점수·fallback은 해당 경로의 계약으로 판단한다.
4. Backend 변경은 대상 브랜치를 확인하고 `develop` PR로 인계한다. 담당자 검증·배포와 AI 배포는 별도다. 양쪽 SHA·schema digest·설정·migration·남은 연결·개발자 QA를 기록한다.

이 작업만으로 배포·실제 요청·GPT 생성·품질 QA를 승인하지 않는다. 새 기능이나 점수 정의는 별도 근거와 계약을 갖춰 설계한다.
