# GPT-6 Luna Standard 전환

사용자 선택은 `gpt-6-luna`, `reasoning_effort=none`, `service_tier=default`다.
`default`는 Standard 처리이며 Flex나 Fast를 사용하지 않는다.

## 적용 범위

- 현재 main의 음성·입술 분석 실행 계약을 부모로 Direct와 내부 요청의 H5를 각각 파생한다.
- Seungun CORE, 원본 JSON을 별도 사용자 메시지로 전달하는 방식, 시스템 프롬프트,
  공식 발음 검산표, 운율·입술 채점과 응답 필드는 보존한다.
- 요청당 GPT 생성은 최대 1회이고 자동 재시도는 추가하지 않는다.
- 이번 변경은 모델 교체다. 캐시 방식과 입력 축소는 변경하지 않는다.
- Backend·Frontend 소스와 배포는 변경 범위에 포함하지 않는다.

## 실행 계약

`deploy/dlpc/build_luna_standard_h5.py`는 기존 lip v4 H5의 manifest와 lock을 검증하고
새 디렉터리에 후보를 생성한다. 기존 H5는 덮어쓰지 않는다. 변경 허용 파일은 요청 설정,
그 설정을 담은 정책 JSON, Luna의 `none`을 허용하는 정책 검증 코드 세 개다.
CORE·프롬프트·채점 규칙·출력 스키마는 부모와 동일해야 한다.

| 경로 | H5 manifest SHA-256 |
| --- | --- |
| Direct | `c085724ea47290009f8d3243002cfe403552924f328e52757d21732c407d1c0b` |
| 내부 요청 | `9c34499f21f0f408f7f26fe45da309977494a2f1b0a6cdae5a7f9662c33e7a5f` |

새 identity는 AI의 자체 실행·직렬화 계약에 추가한다. 기존 identity는 과거 결과 읽기와
롤백을 위해 유지한다. 외부 점수·피드백 필드 이름과 구조는 바꾸지 않는다.

## 검증과 운영 반영

모델 접근 GET 성공은 실제 생성 성공과 다르다. 운영 선택값 변경 전 지정 음성 세 개를
두 실행 경로에서 분석하고, 기존 장애 입력의 실제 내부 request v4 → result v6 → handoff
직렬화를 검사한다. 각 요청은 GPT POST 1회, 수치 점수·피드백 생성, CORE 원본과 GPT 입력의
해시 일치를 충족해야 한다. Backend의 기존 Java 파서·공개 점수 projection도 읽기 검사한다.

검사 기록은 후보 Git head/tree와 CORE/H5 및 fixture SHA에 결속한다. 운영 반영 시에는
접수를 닫고 기존 작업을 완료시킨 뒤 소스·H5 선택을 함께 전환하고, 두 경로의 실제 선택값과
readiness를 다시 조회한다. 운영 완료 여부는 검사·배포 기록으로 별도 확인한다.

공식 호환성: [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).
