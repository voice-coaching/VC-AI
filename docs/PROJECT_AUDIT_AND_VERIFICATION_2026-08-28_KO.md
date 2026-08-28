# 프로젝트 전수 감사 및 검증 기록 — 2026-08-28

- 기준 시각대: Asia/Seoul
- 기준 작업트리: 문서 정본, source pin, 패키징 경계, 학습 gate, visual P6/P8
  계약 이관을 반영한 현재 작업트리
- 판정 원칙: 구현 코드, 실제 실행 결과, 정적 계약과 미완료 gate를 구분한다.

## 1. 프로젝트 구조와 현재 상태

이 저장소는 한국어 L2 학습자의 발음을 진단하고 근거가 있는 코칭과 기준 음성을
제공한 뒤 재녹음 결과를 비교하는 연구 시스템이다. 상위 안내는
[`README.md`](../README.md), visual 정본은
[`architecture/README_KO.md`](architecture/README_KO.md), 오디오 경로는
[`seungun/README.md`](../seungun/README.md)가 소유한다.

| 영역 | 구현·실행 상태 | 아직 주장할 수 없는 범위 |
|---|---|---|
| FastAPI | `/health`, Oracle 진단, stateless closed-loop Oracle endpoint가 기본 dependency만으로 import·실행된다. | 사용자 WAV 업로드, 인증, 영속 저장과 실시간 자동 진단 product는 아니다. |
| 데이터 | AI Hub 71490 parser, QC, manifest와 speaker-disjoint split 계약이 구현돼 있다. | 제한 데이터는 clone에 포함되지 않는다. Seungun 수동 정렬 감사는 `178/240 = 0.7417`로 0.95 gate를 통과하지 못했다. |
| 오디오 진단·코칭 | expected-phone, CTC/GOP/WavLM 계열, S1/S2, evidence-constrained coaching과 재녹음 상태 기계의 코드·계약·테스트가 있다. | historical smoke나 local receipt는 production 승격 증거가 아니다. locked/human gate가 남아 있다. |
| 기준 음성·TTS | reference/TTS adapter와 평가 계약이 있고 scorer extra에서 Torch 계열 runtime을 사용할 수 있다. | 사람 평가를 통과한 reference bank와 production TTS 품질 주장은 없다. |
| 영상 sidecar | MP4 격리, FFmpeg A/V 분리, MediaPipe landmark 후보, QC·A/V sync 후보, P6/P8 validator와 audio-first facade가 구현돼 있다. | 사용자 runtime P6 calibration과 P8 claim catalog가 없어 visual 코칭은 계속 fail-closed다. |
| 평가·논문 | 정적 preflight, source pin, aggregate evidence와 paper validator가 구현돼 있다. | 퇴역한 license/tracker 정본을 대체하는 증거가 없으므로 P04/P05 출판·완료 주장은 차단된다. |

## 2. 문서 정본·링크·source pin 감사

- Git 추적 Markdown 64개 전체를 검사했으며 상대 링크 누락은 0건이다.
- 삭제된 구현계획, privacy, license ledger, tracker 문서를 현행 정본처럼 참조하던
  루트·API·architecture·paper·Seungun 문서를 이관했다.
- legacy snapshot은 역사 자료로만 표시하고 현행 상태 근거에서 제외했다.
- repository-current source pin 709개를 41개 config에서 현재 Git 추적 파일과 대조해
  모두 일치시켰다.
- S104 역사 snapshot 4개의 의도된 live drift 6개는 config 전체 SHA-256과
  `development_only=true`, `promotion_decision_allowed=false` 등 비승격 필드가 정확히
  고정된 경우에만 제외한다. config 한 바이트라도 바뀌면 CI가 실패한다.
- 명시적 TBD 10개와 runtime/external pair 154개는 성공 pin으로 계산하지 않는다.
- 링크와 pin 검사는 [CI workflow](../.github/workflows/ci.yml)에서 새 clone마다 실행한다.

## 3. 패키징·거버넌스 수정

- 기본 dependency의 API import가 Torch를 요구하지 않도록 scorer/TTS 모듈 import를
  실제 TTS 생성 시점까지 지연했다.
- 별도 기본 설치 환경에서 Torch 없이 `/health`, Oracle API와 closed-loop Oracle을
  검증했다. TTS 생성을 요청하면 `voice-coach[scorer]` 설치 안내와 함께 fail-closed한다.
- Seungun CLI의 `--allow-ungated-*` 우회 옵션을 제거했다. smoke와 internal-full을
  포함한 모든 학습 mode가 동일한 독립 수동 감사 0.95 gate를 요구한다.
- P04/P05와 paper publication v1/v2는 삭제된 정본을 임의 대체하지 않고
  `blocked_retired_canonical_sources`로 중단한다.

## 4. 전체 테스트 재실행

네트워크와 Hugging Face 접근을 끄고 이전 실패 cache를 삭제한 뒤 실행했다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DO_NOT_TRACK=1 \
  .venv/bin/python -m pytest --cache-clear
```

최종 결과는 다음과 같다.

| 검증 | 결과 |
|---|---:|
| 전체 root suite (detached clean commit worktree) | **3,595 passed, 69 skipped, 7 warnings, exit 0** |
| 실행 시간 | 192.41초 |
| Seungun 자체 suite | 38 passed |
| WavLM/S104/S105 확대 suite | 649 passed, 6 warnings |
| visual runtime/P6/P8/production sidecar 표적 suite | 116 passed |
| Markdown checker unit | 5 passed |
| source-pin checker unit | 8 passed |

초기 clean 실행의 `183 failed / 3,339 passed / 69 skipped`는 다음 이관 누락을 실제로
드러냈다: 삭제된 정본 뒤 stale source-count 기대값, S104→S108 및 하류 config hash
연쇄, visual measurement와 calibrated decision 분리 뒤 구 fixture, 퇴역 P04/P05의
구 성공 시나리오, PDF continuation-header fixture. 이를 수정한 후 같은 root suite를
cache-clear로 다시 실행해 위 exit 0을 얻었다.

warning 7건은 Starlette TestClient의 httpx 전환 경고 1건과 PyTorch attention-mask
타입 deprecation 6건이다. skip 69건과 warning은 pass로 승격하지 않는다.

## 5. 사용자 제공 영상 2건 재검증

민감한 locator가 process argv와 문서에 남지 않도록 두 입력을 read-only file
descriptor로 전달했다. 공식 pinned MediaPipe model, CUDA device, transport smoke용
고정 prompt를 사용했다. 경로, 파일명, media hash, 원문·전사와 행 단위 결과는 이
기록에 남기지 않는다.

| 관측 | 영상 A | 영상 B |
|---|---|---|
| CLI exit | 0 | 0 |
| audio | `coaching_ready` | `coaching_ready` |
| attempt | `audio_completed_visual_unavailable` | `audio_completed_visual_unavailable` |
| visual stage | `blocked_reliability` | `blocked_reliability` |
| 공통 blocker | uncalibrated video QC, P6 user-runtime calibration 없음, P8 user-runtime claim catalog 없음 | 동일 |
| A/V sync 후보 | `content_av_sync_not_observed` | `content_av_sync_candidate_not_calibrated` |
| visual supplement | 발급 안 됨 | 발급 안 됨 |
| cleanup | `completed`, deletion receipt 발급 | `completed`, deletion receipt 발급 |

실행 후 `/tmp/voice-coach-video-attempt-*` 작업 디렉터리는 0개였다. 이 결과는
audio-first fail-closed 동작과 cleanup을 확인하지만, visual 정확도·P6 calibration·P8
locked evaluation 또는 production readiness 증거는 아니다.

## 6. 새 clone 재현 경계

[`.gitignore`](../.gitignore)는 제한 데이터, 사용자 media, model weight, checkpoint,
상세 receipt와 local registry를 계속 제외한다. 대신 공개 가능한 aggregate evidence의
정확한 파일만 개별 allowlist할 수 있게 `artifacts/public/` 경계를 두었고, clone 설치와
검증 명령은 [`artifacts/public/README.md`](../artifacts/public/README.md)에 기록했다.

새 clone은 코드, config, source pin, 문서 링크, 기본 API 계약과 fail-closed 상태를
재현할 수 있다. AI Hub 제한 원본, 사용자 영상, model/checkpoint, human audit 원자료와
로컬 실행 receipt는 privacy·license상 commit만으로 재현되지 않으며, 그 부재를 성공한
실험으로 해석하지 않는다.

## 7. 남은 차단 항목

1. Seungun 수동 정렬 감사 정확도 0.95 이상을 독립 검토로 확보한다.
2. 대표 사용자/device slice에서 P6 calibration을 수행하고 aggregate-only receipt를
   검증한다.
3. 고정 P8 locked replay와 claim-policy conformance를 독립 실행한다.
4. reference/TTS human gate와 locked evaluation을 완료한다.
5. warning 7건의 upstream 전환을 별도 변경으로 추적한다.

이 항목들이 완료되기 전에는 전체 테스트 green을 연구 또는 production 성과의 완료로
해석하지 않는다.
