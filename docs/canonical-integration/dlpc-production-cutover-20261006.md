# DLPC 운영 전환 재확인 — 2026-10-06 23시 KST

## 현재 상태

사용자가 실제 운영 전환을 요청했다. 마이그레이션 준비 PR AI #28, Backend #111은
병합된 상태다. **운영 upstream 전환은 아직 실행하지 않았다.** 사용자 작업과 별도의
`feat/dlpc-production-cutover-20261006` 브랜치에서 후속 작업을 준비한다.

23:28 KST AWS 조회: DLPC 분석 liveness 200, readiness 503, TTS readiness 200,
양쪽 미인증 요청 401, Backend handoff 200. 실제 Backend TTS revision은 계속
`melo-ko-practice-r1`, DLPC는 `melo-ko-practice-dlpc-r1`이다.

## 이전 후보를 그대로 적용할 수 없는 이유

이전 준비 뒤 RunPod의 실제 운영 release가 바뀌었다.

| 대상 | 관측값 |
|---|---|
| 현재 RunPod app revision | `ef789373aac654ce40ac504104478aa3b8f64c24` |
| 현재 core | `20acb7275aa06d0be4aca1a567420ea603c0eec084e50c651c96501903034f1b` |
| 현재 GPT harness | `6a3bf7c872e718086cb9add4d48412c825188310cdc9eb394089301dd784ff57` |
| 현재 lock | `3f4ce0a44ae75192fe7fea3b03b879c21ab0b2be7cc0a620522c60c2155b674b` |
| 현재 AWS verifier | `7943d4ccc49247949019fef2dd2599b8be478b638fe46e641bc044c99e03c763` |
| 최신 AI main | `60091d8` — GOP 채점 PR #29 병합 |
| 최신 Backend develop | `5e2d468` — GOP 채점 PR #112 병합 |

기존 DLPC CUDA Native v4 후보는 현재 운영 Seungun/GPT와 다르다. 최신 main도 현재
RunPod보다 한 세대 새 GOP 방식이다. 사용자가 **현재 운영본 그대로 이전**할지,
**최신 GOP를 함께 배포**할지 확인하기 전에는 이들 중 하나를 임의로 활성화하지 않는다.
현재 운영 frozen 소스 94개를 manifest hash와 대조해 별도 로컬 준비 디렉터리에 받았다.
모델·녹음·비밀값은 그 소스 export에 포함하지 않았다.

## 추가로 확인한 전환 결함과 수정

1. `deploy/dlpc/launch.py`와 비밀 설정 수신/보관 allowlist가 `AI_DIRECT_*`를 제외하고
   있었다. 같은 분석 프로세스의 직접 분석 설정을 전달하도록 수정했다. 진단 기동과
   복구 보관본은 `AI_DIRECT_ENABLED=false`로 유지해 복구만으로 공개 접수를 열지 않는다.
2. 이전 nginx 후보의 일반 경로 제한은 64KiB여서 직접 분석 WAV 업로드를 막는다.
   `/direct/` 전용 20MiB 경로, metadata header 여유, 요청·SSE 응답 버퍼링 비활성화,
   자동 upstream 재시도 비활성화를 추가했다. FastAPI의 `maximum_bytes=20*1024*1024`와
   metadata header 24,000자 한도를 기준으로 작성했다.
   [nginx proxy 설정](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_request_buffering),
   [body 제한](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size).
3. `check_aws_connection.py`에 `/direct/readyz`를 추가했다. 내부 분석 API 준비만으로
   실제 프론트 경로까지 준비됐다고 판정하지 않는다.
4. FE의 직접 분석 기본 주소가 RunPod 개별 URL이다. 공개 배포
   `https://vc-fe.vercel.app/practice/1`의 정적 JS 18개를 읽었고
   `/_next/static/immutable/chunks/3zlsuhwtdnj9p.js`에서
   `https://s8hb5b0l5k4qlx-8080.proxy.runpod.net/direct`를 확인했다.
   별도 FE dev 브랜치에서 기본값과 env 예제를 `https://ai.voice-coaching.site/direct`로
   수정했다. 실제 Vercel 설정·재빌드·배포는 아직 수행하지 않았다.

새 AWS nginx 후보는 `/root/.config/intelligentai/dlpc-cutover-direct-20261006/ai.conf`다.
SHA-256 `df452223a34278a019416599d0d72e30b8d2faf9459d391564feba21f92e13fa`.
기존 활성 설정 SHA `e8b979412a57a17d3c0c52ef664a56c1286b37ac4bbb5f6d0dcc8a4626eb0cac`를
보존했다. 별도 syntax config의 `nginx -t` exit 0이며 reload는 하지 않았다.

직접 분석용 delivery 설정도 별도로 존재한다. RunPod의
`AI_DIRECT_DELIVERY_ENV_FILE=/run/intelligentai/direct-delivery.env`를 경로 문자열만
복사하면 DLPC에서 파일이 없어 실패한다. 대상 release 확정 후 role 설정을 적절한
권한으로 재주입하거나 기존 분석 환경에 필요한 값만 합쳐야 한다. 이 파일에는 B2 writer
설정과 history/recording prefix가 포함되며 값은 출력하지 않았다.

## drain 사전 관측

23:34 KST 읽기 전용 DB 조회에서 active analysis lease, pending handoff, running/pending
TTS, pending archive는 각각 0건이었다. 이것은 특정 시각의 관측이고 drain 완료가 아니다.
접수는 닫지 않았으므로 전환 직전 다시 확인해야 한다.

RunPod 직접 분석 spool은 RESULT_READY 4건, FAILED 1건이었다. 완료 결과 4건의
archive/history 전송은 모두 COMPLETE다. 기존 jobId/clientAttemptId 조회를 유지하려면
이 스풀도 drain 후 이전해야 한다. API hostname만 바꾸어 기존 결과 조회를 잃지 않는다.
RunPod의 직접 분석 CORS origin은 `https://vc-fe.vercel.app`이다.

## 실제 중단점

- 자동 승인 검토가 AWS 전용 sshd의 `MaxSessions 0→1`, 고정 `ForceCommand` 변경과
  비밀 반환을 다시 거절했다. 일반적인 운영 전환 지시는 구체적 SSH 권한 확대 승인으로
  인정되지 않는다는 이유다. 같은 변경을 우회하지 않고 명시적 승인을 요청했다.
- B2 복구 prefix 전용 readFiles 키 생성·AWS root 전용 보관도 그 승인 요청에 포함했다.
- AGENTS의 개발자 직접 QA 규칙 때문에 실제 CUDA/TTS 샘플 실행 허용 또는 개발자
  결과를 요청했다. 자동 회귀 테스트나 브라우저 QA는 수행하지 않는다.
- 운영본 유지와 최신 GOP 동시 배포 중 전환 대상을 확인하는 질문을 보냈다.

그 전까지 SSH 경계·운영 env·JAR·활성 verifier·nginx·프론트 배포와 RunPod 상태는
바꾸지 않는다. 새 코드 및 nginx 후보 준비를 실제 운영 전환 완료로 표현하지 않는다.
