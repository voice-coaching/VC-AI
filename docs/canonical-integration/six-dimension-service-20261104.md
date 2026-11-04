> 사용자 친화적 피드백 v3: 정량 정책과 선정 순서를 유지하면서, observation은 private BRIDGE_RESULT에 보관하고 practice만 기존 feedback 문자열로 전달한다. practice는 현재 원고의 대상/연습 주제와 화살표, 짧은 지시 하나를 포함하며 완성된 줄은 100자, 전체는 300자·최대 5문장 이하다. 정상 출력은 최대 세 줄·줄당 한 문장 또는 연습 구절이다. 특정 모범 대본은 프롬프트에 넣지 않는다. 아래 v2/direct-only 설명은 이전 배포 이력이다.

> 후속 사용자 결정: Backend와 AI 모두 배포하며 서버 간 GPT 릴리스 ID 및 공유 schema hash 일치 게이트를 제거한다. 내부 canonical 경로에도 canonical-six-dimension-internal-20261104-v2 H5를 적용한다. 내부 CORE f562484d…와 direct CORE 9e29fbc9…는 유지한다. 공개 feedback 문자열과 기존 총점 rubric은 같다. 아래 direct-only 설명은 #55의 앞선 배포 이력이다.

# AI 직접 분석의 여섯 평가 항목과 기존 feedback 연결

사용자 요청에 따라 Backend PR #192와 Frontend PR #104를 병합 없이 닫았다. 이미 병합된 AI PR #54의 공개 sixDimension 확장을 제거한다. 현재 변경은 AI 서버의 직접 분석(/direct)에 적용한다. Backend 내부 canonical 경로는 기존 실행 identity와 동작을 유지한다.

## 실행과 응답

기존 운영 direct CORE `9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba`를 그대로 사용한다. Seungun CORE의 GOP와 prosody v2 측정값 → 임시 정책에 따른 여섯 점수 → 유효 점수가 낮은 최대 세 항목 → GPT 설명과 구체적인 연습 → 기존 feedback 문자열 순서다. 계산식·임시 경계·평가 범위는 [평가 정책 설명](six-dimension-feedback-v1.md)을 따른다. 전문가 검토 상태는 EXPERT_REVIEW_PENDING이다.

GPT 요청은 한 번이다. 여섯 점수를 그대로 복사한 설명과 선정된 항목의 observation/practice를 검증한다. 기존 화면의 발음 총점은 기존 9개 음소 rubric 기준의 legacyGrade를 같은 응답에서 받아 유지한다. 여섯 임시 점수의 평균으로 기존 총점을 대체하지 않으며 legacyGrade는 여섯 항목의 선정에 사용하지 않는다.

여섯 점수·원본 연결·수치 근거·선정 ID·GPT 결과는 private BRIDGE_RESULT에 보관한다. 공개 응답의 coaching에는 기존 필드와 상태값만 전달한다. feedback은 문자열이며, 각 선정 항목을 '* 항목 이름 → 측정에 따른 설명과 연습' 한 줄로 연결한다. practiceResult.feedback과 직접 이력에도 같은 기존 필드를 사용한다. 공개 sixDimension 필드나 새 DB column은 없다.

미측정 값은 null이며 선정하지 않는다. 모두 높은 점수면 상대적인 보완 연습만 제안한다. 공급자 실패 시 임의 교정은 만들지 않고 기존 DETERMINISTIC_FALLBACK 상태와 안내문을 사용한다. 입력 예산 초과 시 호출하지 않으며 내부 점수는 보관한다. 비수락 입력은 기존 GLOBAL_* 상태와 dispatchAttempts=0을 유지한다.

## Backend와 분리하는 이유

Backend–AI 내부 경로는 공유 schema 해시와 canonical source identity를 검사한다. 따라서 공유 runpod_result_v4/v6 schema는 운영 기준 d8a7fc2와 바이트 단위로 동일하게 복구했다. 새 H5는 direct_only identity이며 canonical 내부 executor와 binding 경계에서 사용을 거부한다. direct는 원래 UUID 작업 계약을 사용하므로 Backend의 canonical identity enum에 추가하지 않는다. 고정 H5 manifest·lock·prompt·CORE hash 검증과 Native 실행 허용 목록은 유지한다.

Backend 내부 경로에도 여섯 항목을 적용하려면 공개 feedback 형식을 유지하면서 Backend의 계약 메타데이터를 별도로 조정해야 한다. 이번 direct 배포에 그 변경을 포함하지 않는다.

## 패키징과 운영

서비스 패키지는 build_six_dimension_h5.py로 재현한다. 운영 direct CORE manifest와 검증된 기존 sol61-light H5를 입력하고 새 canonical_six_dimension_direct_20261104_v2_h5 디렉터리에 생성한다. 정책과 기본 프롬프트에 six_dimension_legacy_grade_v1.txt를 덧붙이고 기존 rubric/output schema와 compact 확장기를 보존한다. 기존 immutable 패키지는 덮어쓰지 않는다.

coordinated_release.py는 검증된 main 소스·새 H5 assets·선택 설정을 한 트랜잭션으로 설치한다. 기존 내부 CORE/H5와 direct CORE가 동일한지 확인하고 신규 direct H5만 전환한다. 신규 접수를 차단하고 진행 작업을 drain한 뒤 analysis를 재기동하며 현재 프로세스에 속한 CORE/executor READY receipt, 내부/full/direct readiness를 확인한다. 실패 시 이전 소스·설정·metadata로 복구하고 접수를 닫아 둔다. 성공한 소스/H5/선택값은 persistent recovery에 함께 고정한다.

최초 백업과 서비스 전 백업은 intelligentAI/systempromptbackup에 유지한다. 이번 배포 직전 deployment-20261104-existing-feedback에는 실제 운영 중인 두 H5의 시스템 프롬프트·의존 파일과 비밀값 없는 선택 설정을 추가 보관했다. 실제 private settings는 배포 트랜잭션의 root 전용 backup으로만 보관한다.

## 확인 범위

Python 3.12 컴파일, JSON/schema 구조, 등록 identity와 선택 경로의 분류, dependency lock, 공유 schema의 운영 bytes 일치를 정적으로 확인한다. 배포 후 readiness는 운영 상태 확인이다. 음성 추론·GPT 응답·피드백 품질·전문가 점수 일치·브라우저 QA·성능 측정은 실행하지 않으며 개발자 확인이 남는다. 배포 완료 여부와 실제 commit/H5/관측 시각은 별도 운영 receipt에 기록한다.

## v3 표현과 전달 검사

기본 프롬프트와 legacy rubric 부록의 공개 practice 규칙을 일치시켰다. 내부 observation에는 측정과 제한 설명을 남길 수 있으나 공개 조립에서는 제외한다. practice는 글머리표 없는 `대상 → 연습 지시` 형식이며, 서버가 `* `를 붙인다. 기존 assessments/feedback/legacyGrade JSON 키와 외부 feedback 문자열은 유지한다. 측정 수치, 분석 용어, 반복적인 확정 진단 부인 문구를 사용자 지시로 나열하지 않도록 한다.

pipeline의 provider 출력 검증 및 bridge의 최종 projection 모두 줄 수·글자 수·문장 종결 부호와 남은 구절 수를 확인한다. 문장 수 검사는 형식 검사이며 모든 한국어 문장의 의미 경계를 판정한다는 의미가 아니다. 흔한 복수 지시 종결형도 검사하며, 의미상 중복·모순·지도 적합성은 개발자 수동 QA로 확인한다. 제어 문자, 추가 줄바꿈, 잘못된 화살표 형식과 측정 단위 나열을 거부한다. 응답을 임의로 잘라내거나 추가 GPT 요청을 하지 않으며, 위반 시 기존 실패 상태와 짧은 안내를 사용하고 내부 여섯 점수는 보존한다.

운영 중이던 프롬프트·소스·두 H5·CORE manifest 백업은 intelligentAI/systempromptbackup/friendly-feedback-20261104T133945Z에 있다. v3는 새 immutable 패키지로 만들며 기존 v2 패키지와 읽기 identity를 보존한다. 두 음향 CORE, 임시 정량 정책 SHA, 모델과 요청 설정, 기존 총점 rubric은 유지한다. Backend/Frontend 변경은 없다.

확인은 Python 3.12 컴파일, JSON schema 구조, identity 분류, 패키지 lock과 정책/공개 schema의 동일성에 한정한다. 음성·GPT 생성·브라우저·자동 회귀 QA는 실행하지 않는다. 배포 후 readiness는 운영 상태 확인으로 기록하며 실제 생성 품질 검증과 구분한다.
