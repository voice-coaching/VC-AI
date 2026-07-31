# 로컬 전용 정렬 감사 UI/CLI

기준일: 2026-07-29 KST  
현재 실제 전문가 판정: **0/240**  
G04 상태: **외부 전문가 판정 대기**

이 도구는 기존 240건 표본을 한국어 음성학 전문가가 검수하기 위한
로컬 실행 도구다. 자동화 에이전트가 전문가 판정을 대신 생성하는
도구가 아니며, 현재 worksheet와 review state에는 실제 판정이 없다.

## 보안·개인정보 경계

- 기본 bind는 `127.0.0.1:8765`다.
- 서버는 기관 SSO, 다중 사용자 권한 관리, TLS를 제공하지 않는다.
- 자동 생성된 임시 access token이 모든 화면/API/audio 요청에 필요하다.
- `0.0.0.0` 같은 non-loopback bind는 기본 거부한다. 별도 격리망과
  접근통제가 검토된 경우에만 `--allow-non-loopback`을 명시할 수 있다.
- worksheet의 원본 file stem과 `audio_path`는 서버 메모리 안의 locator로만
  사용한다. 브라우저 응답, state, scorer export에는 내보내지 않는다.
- 화면과 저장 결과의 항목 키는
  `SHA-256("voice-coach.aihub71490.alignment-review-item.v1:" + file_stem)`만
  사용한다.
- reviewer pseudonym도
  `SHA-256("voice-coach.alignment-reviewer.v1:" + reviewer_id)`로 즉시
  변환한다. 원문 reviewer ID는 저장하거나 브라우저로 보내지 않는다.
- audio endpoint는 브라우저가 선택한 item hash만 받는다. 서버가 내부
  locator를 다시 조회하며 절대경로, `..`, symlink root escape를 차단한다.
- notes에는 이름, 연락처, 원본 file stem 또는 경로를 쓰지 않는다. 원본
  item ID나 제한 경로가 포함된 notes는 저장을 거부한다.

이 token은 로컬 CSRF·우발 접근을 줄이는 장치이지 기관 인증을 대체하지
않는다. 제한 음성을 외부 네트워크에 공개하지 않는다.

## 고정 입력과 state

```text
worksheet:
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv
details:
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl
dataset root:
  data/restricted/aihub/dataset-71490
review state:
  data/restricted/aihub/dataset-71490/review/alignment_review_state.json
```

state에는 source 두 파일의 SHA-256과 hash item별 다음 필드만 저장한다.

- `reviewer_id_sha256`
- `alignment_correct`
- `maximum_boundary_error_ms`
- `notes`
- `updated_at_utc`

저장은 같은 디렉터리의 고유 `.part-*`에 먼저 쓴 뒤 `os.replace`로
원자 publish한다. 재실행 시 source hash와 모든 review를 검증해 resume한다.
알 수 없는 item, 중복 JSON key, 누락·추가 필드, 음수·비유한·발화 길이
초과 boundary error, 잘못된 hash가 있으면 시작하지 않는다.

## 서버 실행

reviewer pseudonym을 shell history에 남기지 않도록 환경 변수로 전달한다.

```bash
read -rsp "Reviewer pseudonym: " VOICE_COACH_REVIEWER_ID
export VOICE_COACH_REVIEWER_ID

.venv-scorer/bin/python scripts/review_alignment_audit.py serve \
  --worksheet data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv \
  --details data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --state data/restricted/aihub/dataset-71490/review/alignment_review_state.json
```

터미널에 출력된 `http://127.0.0.1:8765/#token=...` 주소를 같은 머신의
브라우저에서 연다. 화면에는 접근통제 경고, hash reviewer, 완료 수,
hash item 목록, 선택 항목의 audio와 target/realized/automatic alignment가
표시된다. raw file stem과 경로는 표시되지 않는다.

브라우저 UI는 첫 API 호출이 실패하면(잘못된 token 등) 항목 목록이 빈
화면으로 남는다. 이 경우 `#token=` 조각을 포함한 전체 주소를 다시
복사했는지, 서버 재시작으로 token이 바뀌지 않았는지 확인한다.

## tkinter GUI 클라이언트 (Windows 검수자 기본 경로)

브라우저 대신 검수자 PC에서 실행하는 Python 데스크톱 클라이언트를
제공한다. 음성은 항목 단위로만 수신해 검수자 PC의 오디오 장치로
재생하고, 임시 WAV는 종료 시 자동 삭제한다. 서버·state·export 계약은
위와 완전히 동일하며 서버는 그대로 띄워 둔다.

```text
scripts/review_alignment_audit_gui.py
```

- 의존성: Python 표준 라이브러리만 사용한다. Windows 공식 Python
  설치본에는 tkinter가 포함된다.
- 접속: loopback(`127.0.0.1`/`localhost`) URL만 허용한다. 원격 검수는
  SSH 터널을 전제로 한다.
- 재생: Windows는 `winsound`, macOS/Linux는
  `afplay`/`paplay`/`aplay`/`ffplay` 중 설치된 재생기를 사용한다.
- 판정 저장: 신규는 POST, 이미 저장된 항목을 다시 연 뒤 저장하면 PUT으로
  교체한다. 서버의 검증 오류(403/409/422)는 화면에 그대로 표시한다.
- raw utterance ID·제한 경로는 서버가 내보내지 않으므로 클라이언트에도
  나타나지 않는다.

Windows 검수자 실행 순서:

```powershell
# 1) 터널을 유지한 채 (별도 창)
ssh -L 8765:127.0.0.1:8765 <계정>@<서버주소>

# 2) 클론한 저장소에서 (서버 터미널에 출력된 주소를 그대로 붙여넣기)
python scripts\review_alignment_audit_gui.py --url "http://127.0.0.1:8765/#token=<토큰>"
```

`--url`에 token 조각이 없으면 시작 시 입력창에서 전체 주소 또는 token을
붙여넣을 수 있다. 왼쪽 목록에서 항목을 고르면 음성이 로드되고
`▶ 재생`으로 청취한다. 검수 도중 서버를 재시작했다면 token이 바뀌므로
클라이언트도 새 주소로 다시 실행한다.

상세 패널은 `automatic_phone_alignments`(자동 목표·실현 phone 대응)를
가장 위에, 그 아래 `target_phone_intervals`·`realized_phone_intervals`·
`metadata` 순서로 표시한다. 판정 대상인 자동 대응을 먼저 보게 한 순서다.

아직 저장된 판정이 없는 항목을 열면 "정렬 정확성"은 `correct`,
"최대 경계 오차(ms)"는 `0`으로 미리 선택돼 있다. 검수 속도를 위한
기본값이며, **음성을 듣지 않고 그대로 저장하면 "정상"으로 기록된다.**
다르게 판단한 항목만 값을 바꾸는 방식으로 진행하되, 반드시 240건 전부를
실제로 청취해야 한다. 이 기본값 때문에 미검수 항목과 실제로 correct로
판단한 항목을 자동으로 구분할 수 없으므로, 자동 채점 통과만으로 G04
완료를 주장하지 않고 연구책임자가 검수자의 실제 청취 이행을 별도
확인한다
([`AIHUB71490_EXPERT_AUDIT_WORKSHEET_GUIDE_KO.md`](AIHUB71490_EXPERT_AUDIT_WORKSHEET_GUIDE_KO.md)
1.2절 참고).

클라이언트의 네트워크 계층은 실서버(240항목)에 대해 status/item/audio
수신, 잘못된 token 403 거부, non-loopback URL 거부를 headless 통합
테스트로 검증했다. 이 검증은 판정을 저장하지 않았고 review state를
만들지 않았다.

token은 URL fragment에 있어 HTTP 요청·서버 access log로 전달되지 않는다.
UI는 API/audio를 요청할 때만 `X-Review-Token` header로 보낸다. 서버
access log도 비활성화한다.

신규 판정은 POST로 저장한다. 이미 저장된 항목의 중복 POST는 `409`로
거부하고, 화면에서 명시적으로 다시 연 뒤 저장할 때만 PUT으로 교체한다.
필수 필드는 다음과 같다.

- `alignment_correct`: strict boolean
- `maximum_boundary_error_ms`: 0 이상, 유한, 해당 발화 길이 이내
- `notes`: 최대 2,000자, 빈 문자열 허용, 개인정보·원본 ID/경로 금지

## 진행 상태 확인

서버를 띄우지 않고 source/state를 검증하고 진행 집계를 볼 수 있다.

```bash
.venv-scorer/bin/python scripts/review_alignment_audit.py status \
  --worksheet data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv \
  --details data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --state data/restricted/aihub/dataset-71490/review/alignment_review_state.json \
  --report-output data/processed/aihub71490/manifests/aihub71490_manual_alignment_review_progress.json
```

0/240 상태에서는 `status=incomplete`, `alignment_accuracy=null`이어야 한다.
진행 중 accuracy를 최종 결과처럼 해석하지 않는다.

## 완료 export와 기존 scorer

240건이 모두 유효할 때만 scorer TSV를 쓴다. 미완료 상태에서는 aggregate
report만 `incomplete`로 쓰고 scorer TSV를 생성하지 않는다.

```bash
.venv-scorer/bin/python scripts/review_alignment_audit.py export \
  --worksheet data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv \
  --details data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_details.jsonl \
  --dataset-root data/restricted/aihub/dataset-71490 \
  --state data/restricted/aihub/dataset-71490/review/alignment_review_state.json \
  --output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_scoring.tsv \
  --report-output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_review_report.json
```

export TSV는 기존 scorer가 요구하는 다음 열을 유지하되 두 식별자를
namespaced SHA-256으로 바꾼다.

```text
audit_index
utterance_id
reviewer_id
alignment_correct
maximum_boundary_error_ms
notes
```

완료 후 기존 scorer를 실행한다.

```bash
.venv-scorer/bin/python scripts/score_alignment_audit.py \
  data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_scoring.tsv \
  --output data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit_result.json
```

`score_alignment_audit.py`도 표본 수, item/audit index 중복, reviewer 누락,
판정값, boundary error 누락·범위와 95% gate를 검사하고 결과를 원자
저장한다.

## 완료 판정

도구 구현과 synthetic test 통과는 G04 또는 D07의 사람 평가 완료가
아니다. 다음 증거가 모두 있어야 G04를 완료로 바꾼다.

1. 240/240 유효 review state
2. privacy-safe scorer export와 aggregate report SHA-256
3. 기존 scorer `status=passed`
4. alignment accuracy 95% 이상, 즉 최소 228/240
5. 연구책임자가 실제 reviewer 자격·독립성을 확인

D07의 1,000–2,000건 독립 전문가 재평가는 이 UI/state/export 계약을
확장해 별도 표본과 reviewer 역할 분리로 수행하며, 현재 240건 G04
표본을 D07 완료로 재사용하지 않는다.
