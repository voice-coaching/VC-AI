# Seungun 독립 실행 출력 계약 v1

입력은 사용자 로컬 음성과 1~500자 원고다. 호스트 launcher는 20 MiB 파일 한도를 확인하고 container 안에서 다시 제한된 복사본을 만든다. 원본·prepared WAV·원고 digest와 request/execution UUID를 연결한다. 음성 유효성·길이·정렬·음소 관측은 배포 core를 따른다.

| 파일 | 의미 |
|---|---|
| `evidence.json` | 배포 acoustic envelope의 원본 bytes. MFA 구간·Native runs·P1·GOP·운율/reference·실패/누락·stage timing 포함 |
| `prepared.wav` | 실제 생성된 mono 16kHz PCM. 원본 근거의 digest와 일치할 때만 export |
| `result.json` | `schemaVersion=seungun-standalone-result.v1`, 상태·UUID·원본/원고/근거 digest·부모와 이식판 identity·GOP projection |
| `runtime.log` | 본인 실행의 로컬 진단. 공유 전 원고·경로·원본 오류 내용을 검토 |

`result.json.status`는 유효 근거와 core `ok`이면 `COMPLETED`, 그 외 결과가 생성됐다면 `ANALYSIS_REJECTED_OR_FAILED`다. `COMPLETED`도 모든 음소 근거·GOP가 가용하거나 발음 품질이 좋다는 뜻은 아니다. `inputValidation`, 원본 core 상태와 `phonemeAssessment.status/errorCode`를 함께 확인한다. 시작·환경·timeout·association 실패는 종료 코드 1이며, 거절/분석 실패 결과는 2다. 정상 실행은 0이다.

`sourceIdentity`에는 parent core SHA, local core manifest SHA, CPU/CUDA profile, `productionIdentity=false`, `gptEnabled=false`, `apiAccessRequired=false`가 있다. `gptScoring`과 `coaching`은 모두 `NOT_INCLUDED`다. 이 패키지는 전체 점수나 GPT 텍스트를 생성하지 않는다. 원본 core의 LLM-facing projection 필드가 있더라도 GPT를 호출한 증거가 아니다.

missing/null·정렬 모호성·모델의 구별 불가능한 음소·시간 출처는 원본대로 남긴다. 로컬 wrapper는 임의 음소·점수·STT·판정을 채우지 않는다. source digest·index·시간을 바꾸거나 Backend ID·lease·검증 영수증·archive 완료를 가짜로 만들지 않는다.

출력 폴더는 새 경로만 허용한다. 실패가 남긴 로그도 덮어쓰지 않으며 새 실행은 다른 폴더를 지정한다. 취소/timeout 시 해당 컨테이너의 자식만 종료하고 inference/GPT replay를 하지 않는다. 플랫폼이 pidfd/subreaper를 제공하지 않으면 실행을 거부한다.
