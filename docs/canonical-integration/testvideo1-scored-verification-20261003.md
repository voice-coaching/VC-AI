# testvideo1 scored API 재현과 provenance 복구

사용자가 실제 음성 추출·API 재현·수정·배포를 명시적으로 요청했다. 이 단일 입력의
운영 확인을 수행하며 기존 사용자 기록을 재실행하거나 DB 상태를 조작하지 않는다.

## 확인된 원인

운영 분석 62·63은 새 scored core의 CORE 단계에서 종료했다. traceback은
`run_canonical_batch.provenance` → `manifest.collect_provenance` → `_git_state`를 가리킨다.
해당 함수는 실제 `git rev-parse HEAD`와 `git status --porcelain`을 요구한다.
원래 core release의 Git HEAD는 `f27d6b31cec30b1174372e51c6bf5d19ee359247`이고,
새 scored core는 파일 manifest만 내보내면서 `.git`이 빠져 Git 조회가 exit 128이었다.
같은 서비스 UID 10001에서 원본은 성공하고 scored core는 실패함을 확인했다.
HTTP 부모는 실패 후 heartbeat를 중지하므로 Backend에는 나중에 execution timeout으로 보였다.

## 최초 임시 복구와 변경된 수정 방향

- `deploy/runpod/stage_canonical_provenance.py`: 부모·대상 manifest와 원본 파일 hash를
  확인한 뒤 원본 release의 실제 Git 객체·commit을 복제한다. 임의 commit이나 출처를
  만들지 않는다. 기존 scored 소스·하네스 pin·채점 기준을 바꾸지 않고 실제 dirty 상태를 보존한다.
- `src/voice_coach/backend_analysis/canonical_provenance.py`: 서비스 환경에서 release root,
  실제 parent commit 및 Git status 조회를 검사한다. 신뢰 디렉터리는 해당 release 하나로
  제한하고 사용자·시스템 Git 설정을 상속하지 않는다.
- `canonical_producer.py`, `canonical_executor.py`: 프로세스 시작 전과 요청 실행 전에
  provenance 의존성을 확인하여 모델 실행 후에야 이 배포 누락이 드러나는 문제를 막는다.

## 지정 파일 실제 요청

- 로컬 및 Pod 영상 SHA-256: `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`.
- ffmpeg로 새로 추출한 WAV: PCM s16le/16 kHz/mono, 155044 bytes, 4843 ms.
- WAV SHA-256: `3ec858673030f563fc5deef5d0bed4aac507a998c72545ead5959fab2f62515a`.
- 대본: `쌀과 콩을 깨끗한 그릇에 담아요`.
- 기존 전용 검증 사용자 40과 문장 49를 사용했다. 인증값·signed URL은 출력하지 않는다.
- FE 운영 `/api/backend` 프록시를 통해 로그인 → 세션 113 생성 → 실제 WAV 업로드 →
  녹음 77 등록(`PASS`) → 선택 → canonical v4 분석 64 접수까지 확인했다.
- 최초 접수 시각은 2026-10-03 21:07:24 KST이며 수정 전 재현 요청이다.

실행 결과와 최종 배포 근거는 아래 후속 관측에 기록한다. readiness 성공을 실제 채점
응답 성공으로 대신하지 않는다.

## Git 실행 의존성 제거 — scored v2

사용자가 매 요청의 Git 조회 필요성을 지적했다. 임시 메타데이터 복원은 중단 직전 v1에
적용됐으나, Git 의존성 자체를 유지하는 방식은 최종 수정으로 사용하지 않는다.
`stage_canonical_provenance.py`를 제거하고 새 v2 release는 `.git` 없이 패키징한다.

- `build_canonical_scored.py`는 원래 고정 identity의 `base_source_commit`과 파생 소스
  수정 여부를 `source/config/release_provenance.json`에 포함한다. 실제 원본 revision은
  `6856e9d357df2e51a5df2595205e77374f801cb2`이며 수정 여부는 true다.
- 원래 출력의 `git_commit`·`dirty_worktree` 필드는 이 원본 baseline과 수정 상태를
  뜻한다. 배포 패키지의 최종 바이트 식별자는 별도의 검증된 release manifest SHA다.
  결과에 가짜 commit이나 clean 상태를 넣지 않는다.
- 새 `manifest._git_state`는 위 고정 JSON을 읽으며 Git 프로세스를 실행하지 않는다.
  요청 producer와 시작 검사는 파일 hash·schema·원본 identity의 일치를 확인한다.
- core v1 → v2 바이트 변경은 manifest, release identity, 새 provenance JSON,
  provenance reader 4개 파일뿐이다. 모델·정렬·채점 코드와 규칙은 동일하다.
- 실제 core Python/UID 10001에서 `.git` 없음, `PATH=/nonexistent`, runner 호출 시 실패
  조건으로 provenance 함수를 실행했다. 정상 baseline/수정 여부를 반환했고 subprocess 0회다.
- Backend verifier는 S7/H5/scored v1을 보존하고 v2를 추가한다. 신규 설치 pin은 v2로
  이동하며 과거 결과 검증을 버리지 않는다.

| v2 artifact | SHA-256 |
| --- | --- |
| core manifest | `08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80` |
| H5 manifest | `4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf` |
| H5 lock | `b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989` |
| AWS verifier bundle | `39368e9d728e6bb276a882a7bd07424bc317b5092af270fb3583dac3f5e40fc1` |

수정 전 분석 64는 21:11:19 KST에 FAILED/execution timeout으로 종료됐다.
v2 전환 중 신규 접수를 차단했으며 기존 실제 요청/lease를 초기화하지 않았다.

## v2 배포 후 추가 계약 누락

21:35:06 KST에 v2 readiness와 drain 확인 후 접수를 열고 21:35:56에 분석 64의
정상 재시도 API를 호출했다. core 및 MFA 종료 코드는 0이며 Git 장애는 재발하지 않았다.
이 실행은 PREFLIGHT에서 `CANONICAL_SCHEMA_INVALID`로 종료했고 21:40:52에 Backend
timeout이 됐다. core 원문은 parent schema를 통과했다.

원인은 v2를 Python harness registry에는 등록했지만 v4 schema의 binding, association,
sourceIdentity 허용 hash 묶음 및 scored 분기에는 추가하지 않은 것이다. 해당 폐쇄형
목록에 v2를 추가하고 기존 S7/H5/v1을 보존한다. Backend의 H5 요약 경로도 v2를 포함한다.
artifact 검증에는 실제 하네스 identity의 schema 등록 검사를 추가하여 이 종류의 배포
누락을 추론 전에 차단한다. 점수·음소·정렬 계산이나 임계값은 변경하지 않는다.

## 계약 보완 배포

- AWS JAR revision: `edb236275920a7c9cee75d88eaaccf92f0be379a`.
- JAR SHA-256: `6955393099e6acd405d58c7f1b32f12340066e86aaa4a10a957c8c50e9781ef4`.
- AI source: `f73c96fbe8fd42ccafb132dc1e1439f3cbe5585f`.
- RunPod bundle: `819748f468315be7502f922bdcb7490ee3fa06fd46f9d1dfd889c260e992cb6f`.
- AWS verifier bundle: `f878354d934651016dd40611068ec90bd47f11eb02a2444a1a5edd41efa1f2d9`.
- v4 schema: `b9180ff307c852d1fa898f33dbf89d879d85f22aef93a1f22db4d33bc1a3240c`.
- runtime profile: `c0244a57a699ebc186b4c96810c0039da0343cd8146b0cf19cb06404acf2729b`.

Java 21 `bootJar -x test` 성공. AWS의 고정 Python 환경에서 S7/H5/scored v1/v2
설치 검증을 통과했다. 양쪽 schema, 현재 artifact, FE 운영 자산 일치와 3회 연속
readiness/drain 확인 뒤 2026-10-03 21:55:07 KST에 접수를 재개했다.
21:55:33에 동일 세션 113/녹음 77/분석 64의 정상 재시도 API를 호출했다(retryCount 2).
request ID는 `5c572068-9b6e-4caf-b598-77bf2c7cd977`, execution ID는
`9a3ca6e7-2e08-4be7-a005-342dd9d6da18`다. 이 시도는 CORE/EXPRESSION/PREFLIGHT를
통과해 RETAIN 단계에 진입했다.

Backend 정규화 WAV의 SHA는 `9eb81a5d38625566aead94150268c07df0f74550126a1ccbbcebb36abc9e6807`이다.
업로드 전 ffmpeg 추출 WAV와 정규화 이후 파일의 hash를 혼동하지 않는다.

## 전체 음소 출처 계약 — H5 scored v3

두 번째 재시도는 receipt VERIFIED, callback APPLIED, 결과 효과 완료까지 통과했지만
22:01:14 KST에 `CANONICAL_ADAPTER_FAILED`/UNSCORABLE로 끝났다. 저장·연결 문제와
채점 준비 실패를 구분한다. 같은 core를 실제 frozen `Bridge.prepare`에 적용하면
`EXPECTED_PHONE_PROVENANCE`가 발생한다. 전체 Seungun을 활성화하면서 metadata의
출처가 `g2pk_detector_phone_contract_v2`가 됐지만 H5는 비활성화 시의 기본 표지
`g2pk_compat_jamo_v1`만 기대했다. 이 실행은 GPT 채점 호출 전 거절된 것이다.

새 하네스는 metadata와 pronunciation 양쪽에서 실제 detector phone contract v2 표지가
일치해야 한다. 출처 검사를 삭제하거나 core 원문을 이전 표지로 덮어쓰지 않는다.
Git 없는 core v2, 채점 기준, GPT 프롬프트는 그대로이며 H5 하네스만 v3로 추가한다.
동일 원문의 새 하네스 prepare는 통과했고 허용 코칭 후보는 0개였다. 이는 채점을
금지하는 조건이 아니다. 실제 28개 음소의 서버 검산은 70.8점이며 별도 GPT API 진단
2회(기본/운영 canonical Python) 모두 gpt-6-astra, finish_reason=stop, READY로 일치했다.
이 진단 값을 기존 실패 결과에 수동 저장하지 않았다.

- H5 v3 manifest: `e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3`.
- H5 v3 lock: `dc3db9cc03a3355ddeb5d5819cd6fd46726935c8ca9b830f513e37163c89f4d8`.
- v4 schema/registry/Backend는 v3를 추가하고 S7/H5/v1/v2의 과거 검증을 유지한다.
