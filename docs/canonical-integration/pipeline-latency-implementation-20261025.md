# 전체 분석 파이프라인 지연 단축 구현·운영 반영

후속 [실제 API 시간·Sol 6.1 모델 전환](sol61-light-timing-20261026.md)에서 이 조합의 Luna
관측과 같은 Core를 사용하는 Sol low 관측을 추가했다. 아래의 미측정 표기는 이 구현 작업 종료 시점의 범위다.

[추가 단축 계획](pipeline-latency-next-plan-20261025.md)의 P0–P4에서 요청별 반복 작업을
줄이는 변경을 구현해 DLPC J406에 반영했다. 관측 시각은 **2026-10-25 12:56:55 UTC**다.
local main `ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 새로 만든
`feat/pipeline-latency-implementation-20261025` 브랜치를 사용하며, 기존 미커밋 변경과
테스트·fixture 등 251개 파일의 삭제를 유지했다. 이 작업에서 commit·push·PR은 하지 않았다.

새 음성 API·GPT 생성·시간 측정 호출은 **0회**다. 아래 내용은 구현·정적 확인·운영 준비
상태이며, 실제 분석 성공이나 피드백 품질 동등성·지연 목표 달성을 의미하지 않는다.
대표 서버 14.217초·MFA 6.304초는 [직전 실측](seungun-server-optimization-20261025.md)의
변경 전 값이다. 이번 조합의 처리 시간·cache hit율·GPT 출력 길이·p95는 아직 미확인이다.

## 적용한 실행 구조

| 단계 | 구현 | 범위와 제한 |
|---|---|---|
| P0 | actual cgroup 메모리 조회, 내부 canonical 감시 상한, 오디오·provenance·provider 연결 관측 | 내부 canonical은 별도 파생 조합으로 교체. 수치 계산과 기존 메모리 실패 한도 유지 |
| P1 | 엄격한 PCM 복사 경로, READY 이전 ffmpeg/ffprobe 버전 준비 | 조건을 만족하지 않으면 원래 변환. G2P 결과 cache·추가 음향 task 병렬화는 추가하지 않음 |
| P2 | 준비된 실행 worker, 제한된 HTTPS 연결·H5 bridge·schema validator 재사용 | 단일 요청 실행, worker 최대 3회, 자동 재추론·GPT 재전송 없음 |
| P3 | 전체 compiler 입력을 key로 한 worker-local lexicon FST cache | 최대 1 entry·64MiB 추정 예산, 요청 소유 FST 복사. 첫 실제 compile 뒤에만 채움 |
| P4 | 고정 rubric prefix, compact 출력과 기존 공개 결과 확장 | Luna / none / default, Fast mode 제외. 최대 두 문장의 근거 기반 연습 안내 |

### 오디오·정적 도구 정보

[pipeline_prepared.py](../../deploy/dlpc/performance/pipeline_prepared.py)는 실제 파일
descriptor의 일반 파일 여부·크기·읽기 전후 inode/size/mtime을 확인한다. 전체 RIFF 길이,
chunk 경계·padding, 단일 data, mono PCM s16le 16kHz를 확인하고 전체 bytes의 SHA와
실제 frame 수를 계산한다. 알려진 작은 LIST/JUNK 외의 chunk나 확장 포맷은 기존
ffprobe/ffmpeg 경로를 사용한다. 기존 zero-input gate·최대 길이와 요청 binding은 유지한다.

새 prepared 파일은 요청별 private 경로에 만들며 실제 저장 bytes의 SHA를 사용한다.
기존 변환기의 header와 다른 경우를 숨기지 않고 `pcm-copy-and-tool-versions-v1`로
식별한다. 음성 결과를 cache하지 않는다. 도구 버전은 READY 전에 수집하고 동일 세대에서
재사용하되 executable 경로·inode·크기·mtime 및 PATH/LD 환경이 바뀌면 실패한다.
요청 시각·실제 파일 identity·동결 자산 검증은 계속 요청마다 유지한다.

### 실행 worker·연결 재사용과 실패 처리

[executor_pool.py](../../src/voice_coach/direct_analysis/executor_pool.py)와
[executor.py](../../src/voice_coach/direct_analysis/executor.py)는 기존 Native/MFA resident와
별개로 canonical Python 실행 프로세스를 READY 상태로 준비한다. 고정 자산·실행 identity·
동결 H5 import가 완료된 뒤에만 접수를 연다. UDS는 private control 디렉토리에 두고
SO_PEERCRED의 실제 부모 PID와 UID를 확인한다. 메시지는 job UUID·resident generation·
deadline으로 제한한다. API/Backend/AWS credential은 worker 환경에서 제외한다.

최대 3개 요청을 직렬 처리하며, 실제 cgroup 여유가 8GiB를 넘는 경우 두 번째 요청 뒤부터
다음 실행 worker 하나를 준비할 수 있다. Native/MFA standby의 운영 reserve는 별도이며
이번에 활성화하지 않았다. 실행 worker 준비는 GPU model 추가 적재나 합성 음성 호출이 아니다.

[service.py](../../src/voice_coach/direct_analysis/service.py)는 mutation lock 안에서
QUEUED→RUNNING을 전환해 이미 취소된 요청을 덮어쓰지 않는다. 취소·deadline·불확실한
dispatch·실패 결과에서는 실행 worker와 요청 소유 resident를 회수한다. durable completion의
requestId/executionId를 확인하며, UDS ACK가 유실돼도 같은 producer/GPT를 재실행하지
않고 durable 파일로 조정한다. 정리 확인이 실패하면 접수를 닫는다. 부모가 사라지면 실행
worker가 자기 process group을 종료해 provider thread가 남지 않도록 한다.

[canonical_transport.py](../../src/voice_coach/backend_analysis/canonical_transport.py)는
실제 런타임에 있는 표준 라이브러리 HTTPSConnection을 사용한다. endpoint·TLS 검증·
120초 정책·body 한도·redirect 거부·환경 proxy 미사용을 유지한다. 재사용 연결의 실패도
POST 재시도 사유가 아니다. `connectionReused`와 `connectSeconds`를 private 관측에
추가했다. 비스트리밍 `httpHeadersSeconds`에는 provider 처리·출력 대기가 포함되므로
첫 토큰 시간이나 순수 모델 추론 시간으로 해석하지 않는다.

고정 H5 bridge와 schema validator만 재사용한다. 요청 CORE·prepared SHA·association·
공개 결과 검증 및 원본 fsync 이후 결과 게시 순서는 유지한다. 새 direct 조합에서만
사용하지 않는 selection view 생성을 생략하며 기존 다른 조합의 의미는 바꾸지 않는다.

### MFA lexicon cache의 실제 범위

[mfa_lexicon_cache.py](../../deploy/dlpc/performance/mfa_lexicon_cache.py)는 설치된 MFA와
Kalpy 원본 SHA를 고정하고 DirectAligner의 compiler binding만 파생한다. 기존 ordered SQL,
dictionary 준비·disambiguation·normalize_text·파일 쓰기·clear는 그대로 실행한다.
발음 dataclass 전체, 옵션·확률·상태, 순서가 있는 word/phone symbol table로 key를 만든다.
규칙 FST가 주어지거나 compile이 symbol table을 바꾸면 cache를 사용하지 않는다.

hit에는 L/L_align FST를 각각 복사하고 필요한 compiler 상태를 요청 객체에 복사한다.
공유 mutable compiler·DB connection·음성·MFCC·정렬 결과는 재사용하지 않는다.
entry/byte 예산 외에도 기존 process RSS 감시를 유지하며, `lexicon_fst_cache_hit/miss`로
분리한다. 원래 SQL·key 구성 비용까지 없어졌다고 계산하지 않는다.

계획의 사전 생성 read-only FST 자산 대신 **첫 정상 요청의 실제 compile에서 cache를
채우는 방식**으로 구현했다. 가짜 corpus·음성 예열 없이 실제 compiler의 완전한 입력과
부수 상태를 결속하기 위한 선택이다. 첫 요청은 miss이며 3회 세대 수명을 늘리지 않았다.
따라서 계획의 절감 예산은 실제 hit가 관측되기 전까지 목표다.

### GPT 출력과 내부 canonical 감시

[파생 builder](../../deploy/dlpc/build_pipeline_latency_release.py)는 기존 rubric·가중치·
채점 조건과 전체 Native/GOP 근거를 유지한다. rubric을 변하는 user JSON 뒤에서 고정
system prefix로 옮기고 `levels[9]`, `score`, `feedback`를 받는다. 기존 compact 확장 코드를
사용해 원래 공개 결과로 펼친 뒤 원래 schema로 다시 검증한다. 서버가 점수를 새로 판정하지
않으며 null·UNSCORABLE과 실패 의미를 유지한다. 원래 3,000 completion token 한도도 유지한다.
출력은 근거가 있는 연습 최대 두 줄·두 문장으로 제한하며 input 축약·근거 삭제는 하지 않았다.
implicit prompt cache의 hit나 처리 시간은 보장하지 않는다.

[내부 감시 파생 builder](../../deploy/dlpc/build_internal_bounded_release.py)는 별도 내부
canonical의 감시 history를 deque 256·phase 8로 제한하고 raw 로그를 8MiB×2로 순환한다.
기존 숫자 계산·메모리 한도·모델·GPT 정책은 그대로다. 내부 canonical과 direct를 같은
새 H5로 합치지 않았다. 런타임의 main 버전 resident와 signature가 맞도록 main의
`canonical_producer.py`도 운영 overlay에 포함했다.

## 릴리스·복구 identity

설치 조합의 정본은 [restore_pipeline_latency.py](../../deploy/dlpc/restore_pipeline_latency.py)의
경로·고정 해시·runtime 17개 파일 allowlist다.

| 용도 | Core 경로 basename | Core manifest SHA256 | H5 manifest SHA256 |
|---|---|---|---|
| direct | `canonical_dlpc_pipeline_latency_20261025_v4` | `9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba` | `c3d78433cdb0d9ca999ab7b407464084a95cbf8f6d931a80ffd9610656ea3b68` |
| 내부 감시 | `canonical_dlpc_internal_bounded_20261025_v1` | `f562484d436e42626114e16afa2bcaa782c1abbfc3cc3cdbd5c3b3cf030b920a` | `25531c9e52c1088c9cdceee82a5e2d57d52c46da4b4cd93cac75eea87d6ade60` |

Direct dependency lock은 `2864be8b43b850136c7b41ffc9242265b5faa5890c48c59847f807051fc44114`,
prompt는 `fdd98e37b076c3b36058a09bc40667bcd2cd1d9d3a9d31a7106b5caa7df8ca7a`다.
내부 lock은 `570f92d23aa19f241e4ac73b35fccc406761bdfd2c097d1c83f3bb66c821d742`다.
실행 허용 조합과 private 결과 schema의 identity도 함께 등록했다.

새 selector와 [recover_on_start.py](../../deploy/dlpc/recover_on_start.py) hook을 설치했고
이전 복구 단계가 새 overlay를 거부하지 않도록 정확한 SHA를 허용했다. root 소유 private
payload에는 교체 전 파일도 고정해 두었다. 원복은 접수를 닫고 분석을 중지한 상태에서
`restore_pipeline_latency.py --rollback`으로 runtime·direct/internal 네 경로·selector를
함께 복구한 뒤 분석을 시작한다. 새 실행 모듈도 원복 시 제거한다. 이 절차 자체를 이번에
실행하지는 않았다. TTS는 원복 대상이 아니다.

## 실제 확인 결과

- 변경·신규 소스 전체 Python **43개** 구문 compile, JSON **2개** parse 통과. 직전 작업의
  미커밋 변경도 포함한 수다. bytecode·검증 파일을 저장하지 않았다.
- 운영 runtime **17개**는 저장소와 고정 SHA가 모두 일치했다. 복구 helper **4개**도
  저장소와 운영 SHA가 일치했다.
- Direct 자산 **98개** 전체 SHA, 그 안의 Python **69개** 구문·JSON **19개** parse 통과.
  내부 자산 **90개** 전체 SHA, Python **62개** 구문·JSON **18개** parse 통과.
- 실제 canonical Python·서비스 UID에서 두 조합의 Native 허용·schema·자산 SHA·동결 H5
  import 사전 확인 통과. 실제 Core Python에서 도구 정보 기동 준비, 실제 MFA 환경에서
  cache 설치·복사 기능 가용성 확인 통과. 음성·정렬·provider 요청은 하지 않았다.
- 기동 전 사전 확인에서 canonical 외 Python 사용, 제거된 과거 config 필드와 외부 경로,
  새 prompt의 sourceIdentity 등록 누락을 발견해 수정했다. 실패한 후보는 선택하지 않았고
  최종 v4와 내부 v1 조합으로 사전 확인을 통과한 뒤 배포했다.
- 새 실행 worker·service·worker, bridge·codec·transport·harness·Native allowlist와 두 성능
  helper **11개**에 `mypy --check-untyped-defs --follow-imports=skip --ignore-missing-imports
  --python-version 3.12 --cache-dir=/dev/null --no-incremental` 통과.
  resident까지 포함한 **12개** 검사에는 기존 nullable 처리 진단 **3건**이 남는다.
  HEAD/main의 resident 원문을 `--shadow-file ... /dev/stdin`으로 대조해 같은 세 진단이
  발생함을 확인했다. 경로·task의 None 가능성에 대한 기존 타입 진단이며 이번 변경에서
  새로 생긴 진단은 아니다. 저장 파일을 만들지 않았고 이 코드의 타입 정리는 남겨 두었다.
- 변경 문서 3개의 상대 링크 **61개**와 충돌 표식 확인, `git diff --check` 통과.
  새 테스트 관련 파일·fixture·mock은 없고, 외부 임시 검증 파일도 만들지 않았다.

활성 작업 0을 확인하고 소유 admission hold를 둔 상태에서 분석 서비스만 교체했다.
기동 후 direct Native/MFA의 새 Core SHA와 실행 worker의 Core/H5 SHA가 모두 일치했고,
MFA READY에는 1 entry·64MiB cache revision이 기록됐다. 내부 resident도 새 Core로 READY다.
관측 시각에 analysis PID **716835**가 RUNNING이고 TTS는 교체 전과 같은 PID **460835**다.
독립 TTS의 실제 합성 요청은 하지 않았다.

12:56:55 UTC local `http://127.0.0.1:8080/direct/readyz`와 공개
`https://ai.voice-coaching.site/direct/readyz`는 모두 **200 / ready=true**였다.
이번 작업 소유 hold는 제거했고 selector는 optimized다. 인증된 `/health/deployment`의
활성·direct 작업 수는 둘 다 0, local `/health/handoff`는 executorConfigured=true,
admissionEnabled=false였다. 저장된 env flag와 실제 admission 결과를 구분한다.
Backend 소스·AWS nginx는 이번에 변경하지 않았다.

## 남은 조건부 작업·인계

P1의 G2P cache·작은 음향 task 병렬화는 관측된 비중이 작아 이번 변경에서 제외했다.
P5 사전 DB template/normalize lookup은 **P3 이후 MFA가 목표를 넘는지** 정상 운영 자료로
확인한 뒤 진행한다. P6 corpus DB 없는 경로·Native/MFA standby, P7 다중 GPU·동시성 확대는
추가 근거가 필요한 후속 단계이며 활성화하지 않았다. 현재 max_active=1은 유지한다.

새 조합의 실제 분석·취소·세대 교체·cache miss/hit 의미와 GPT 피드백 품질, 남은 시간
병목은 아직 실제 요청으로 확인하지 않았다. 사용자 지시에 따라 모든 종류의 테스트와
화면 확인을 만들거나 실행하지 않았고, 실제 API 시간 측정도 이번에 추가하지 않았다.
9–11초·7–9초는 계획의 목표이며 달성값으로 인계하지 않는다.
