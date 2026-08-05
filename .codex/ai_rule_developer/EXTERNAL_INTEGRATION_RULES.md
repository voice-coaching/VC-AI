# 외부 연동 규칙 - intelligentAI

외부 시스템 연동은 반드시 명시적인 client 또는 adapter 경계 뒤에 둔다.

[대상]
- LLM API
- Lab, simulation, batch, worker server
- Third-party HTTP API 또는 SDK
- 현재 저장소 프로세스 밖의 모든 시스템

[구현 규칙]
- 확인된 인증 정보, 계약, timeout, 실패 매핑을 기준으로 외부 호출을 구현한다.
- Service는 비즈니스 의도를 드러내는 좁은 메서드로 외부 연동을 호출한다.
- Upstream DTO는 내부 DTO로 변환한 뒤 호출자에게 반환한다.
- Request/Response schema와 연동 문서는 실제 구현과 함께 갱신한다.

[금지]
- controller 또는 UI component에서 외부 HTTP/SDK 직접 호출

[Python 연동 메모]
- Filesystem, subprocess, network, environment 연동은 adapter module 뒤에 둔다.
- Adapter 경계에는 typed request/result object를 사용한다.
- import 시점에 외부 호출을 실행하지 않는다.

[연구 데이터·실행 연동 추가 규칙]
- dataset portal, object storage, model hub, GPU worker, subprocess, human-review export는 모두 외부 연동이다. 각 adapter에는 계약 확인 상태, timeout, retry 가능 여부, 실패 mapping, 민감 데이터 반출 제한을 명시한다.
- 인증은 사용자 제어 세션 또는 안전한 런타임 환경에서만 처리한다. token·API key·cookie를 출력, 소스, config, artifact, 문서에 기록하거나 대체하지 않는다.
- 외부 데이터·모델을 실제 실행에 사용하기 전에는 승인·라이선스·revision/checksum·역할 범위를 확인한다. 확인하지 못한 계약은 adapter가 fail-closed로 보고하며 추측한 fallback을 사용하지 않는다.
- 외부 worker의 exit code, registry 상태, checkpoint·report 존재와 완결성은 별도로 검증한다. 연결 성공이나 process 시작만으로 실행 성공을 반환하지 않는다.
