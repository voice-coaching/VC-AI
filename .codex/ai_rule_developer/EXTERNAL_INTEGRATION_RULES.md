# 외부 연동 규칙 - intelligentAI

## 적용 대상

다음은 모두 외부 경계다.

- AI Hub portal/CLI/download API와 제한 데이터 archive
- Hugging Face 또는 로컬 model cache
- FFmpeg/ffprobe, MediaPipe worker, g2pK subprocess
- `seungun`의 고정 detector runtime
- HyperCLOVA/Qwen/CosyVoice/WavLM 등 model runtime
- Docker Compose의 loopback HyperCLOVA server
- 현재 Python process 밖의 worker, shell, PowerShell, HTTP/SDK 호출

외부 경계는 `adapters`, 전용 client 또는 좁은 worker module 뒤에 둔다. API route,
GUI, domain model에서 직접 호출하지 않는다.

## 계약 우선

연동 전 다음을 확인한다.

- 허용된 source/origin과 권리·동의
- 입력/출력 schema와 schema version
- model/tool ID와 immutable revision 또는 digest
- timeout, 최대 입력/출력 크기, retry 정책
- 인증·secret 전달 방식
- 실패 예외와 public reason code 매핑
- offline/loopback/network isolation 조건
- 임시 파일, process, GPU memory의 cleanup 방법

계약이 확인되지 않은 필드는 추측하지 않는다. SDK/CLI의 현재 동작이 중요하면 공식
문서나 `--help`와 작은 read-only probe로 확인한다.

## Process·worker 경계

- command는 argument list로 실행하고 `shell=True` 또는 문자열 조합 shell을 사용하지
  않는다.
- stdin/stdout JSON worker는 입력 schema, output byte limit, timeout, UTF-8, JSON
  finite-number 조건을 검증한다.
- timeout 시 process group과 child process를 정리하고 종료 여부를 확인한다.
- stderr나 예외 원문에 민감 경로·token이 포함될 수 있으므로 public result에 그대로
  복사하지 않는다.
- worker의 성공 exit code만으로 의미적 성공을 주장하지 않고 반환 receipt/status를
  검증한다.

## Seungun 경계

- Seungun은 오디오 발음 분석과 selected phone의 권위다. visual 또는 LLM adapter가
  이 결정을 다시 계산하지 않는다.
- frozen detector, expected-phone sequence, prompt revision과 반환 schema를 검증한다.
- detector score는 현재 계약에 명시된 ranking score이며 calibrated correctness
  confidence로 바꾸어 표현하지 않는다.
- 한 영상 시도에서 Seungun 재호출로 결과를 골라내거나 visual 결과에 맞춰 phone을
  바꾸지 않는다.

## HyperCLOVA·LLM 경계

- 로컬 HyperCLOVA endpoint는 HTTP loopback 주소만 허용한다. URL credential, query,
  fragment 또는 외부 host를 허용하지 않는다.
- 원본 audio/video, 파일 경로, hash, speaker/participant identifier, landmark 배열을
  전송하지 않는다.
- model ID와 revision, 응답 크기, JSON schema, issue/advice/claim identity와 모든 승인
  claim의 보존을 검증한다.
- LLM은 승인된 내용을 쉬운 한국어로 표현할 뿐 새 진단, 원인, 조음 사실을 생성할
  권한이 없다.
- timeout, transport, parse, schema 또는 grounding 실패 시 숨은 retry 없이 정확한
  승인 catalog 문구로 fallback한다.
- 루트 scorer 환경의 `transformers==5.14.1`과 HyperCLOVA container의
  `transformers==4.52.4`를 합치지 않는다.

## Model·artifact 로딩

- `from_pretrained`나 runtime loader에는 가능한 한 고정 revision과 local/offline
  경계를 사용한다.
- weight는 Git에 넣지 않고 허용된 read-only/local cache에서 로드한다.
- symlink, non-regular file, mode/owner/link count, duplicate JSON key, non-finite 수,
  digest drift를 검사하는 기존 strict loader를 약화하지 않는다.
- artifact 파일이 존재한다는 이유만으로 calibration, promotion 또는 모델 품질이
  확인됐다고 보지 않는다.

## AI Hub·다운로드

- dataset ID, file ID, 승인 상태, 약관과 목적을 현재 portal/CLI에서 확인한다.
- 대용량 또는 제한 데이터 다운로드는 사용자가 명시한 범위와 권한이 있을 때만
  시작한다.
- destination은 `data/restricted` 등 Git 제외 보호 경로로 제한하고 archive traversal,
  symlink, 크기, digest, member type을 검사한다.
- 다운로드 완료·압축 무결성과 label 의미·학습 적합성·gate 통과를 구분한다.
- token, cookie, API key를 명령 출력, 로그, 문서, shell history에 노출하지 않는다.

## 실패와 문서

- upstream 예외는 stable service exception/reason code로 변환한다.
- fallback이 제품 동작이면 성공 경로와 동일하게 테스트하고 문서화한다.
- timeout/retry/fallback/schema/revision 또는 전송 데이터가 바뀌면 관련 config,
  테스트, 허용된 프로젝트 문서를 함께 검토한다.
- 실제 외부 실행을 하지 않았다면 `contract-only` 또는 `not verified`로 보고한다.
