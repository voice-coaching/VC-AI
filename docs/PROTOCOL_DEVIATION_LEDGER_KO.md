# 연구 실행 protocol-deviation 원장

최초 작성: 2026-07-29 KST  
상태: 실행 중

## 목적

계획·config·실행 lock과 실제 실행 사이에 발생한 편차를 숨기지 않고
기록한다. 편차가 발견되면 결과 지표가 좋아도 자동으로 완료·승격하지
않는다. 알려진 사실, 계산 영향 평가, 완료 판정과 후속 조치를 분리한다.

## 판정 상태

- `OPEN`: 사실관계 또는 영향 판정이 끝나지 않음
- `MINOR_ACCEPTED`: 계산 결과에 영향을 주지 않았다는 재현 가능한 근거와
  독립 판정이 있고, 사용 범위와 한계를 함께 공개함
- `INVALIDATING`: 사전 고정 계약을 위반했거나 무영향을 입증할 수 없어
  해당 실행을 정본 결과로 사용할 수 없음
- `CLOSED_BY_RERUN`: 오염되지 않은 새 실행과 독립 검증으로 대체함

`MINOR_ACCEPTED`는 단순한 사후 설명만으로 부여하지 않는다. 최소한 변경
대상·시간창·전후 바이트, 실행 중 사용 여부, 환경·데이터·모델·실행
코드의 불변성, terminal artifact의 deep validation을 확인해야 한다.

## DEV-S104-001 — 실행 중 project manifest 일시 변경

현재 판정: **OPEN**  
발견 시각: 2026-07-29 21:35 KST  
대상 실행: `aihub71490-wavlm-s104-adapter-v1-dev`

### 확인된 사실

1. S104 GPU 학습은 2026-07-29 08:40:44 KST에 시작됐고 registry의
   `started` 시각은 08:41:00 KST다.
2. PDF builder 개발 과정에서 21:19:20 KST에 `pyproject.toml`의
   production dependency 목록에
   `markdown-it-py==4.2.0` 한 줄이 일시 추가됐다.
3. 일시 변경본은 822 bytes,
   SHA-256 `1576ffb98e6de9999c725113ecca0a8970b70d9c631592c249790849b1c741c6`였다.
4. 21:35:27 KST에 해당 줄을 제거했다. 복구본은 795 bytes,
   SHA-256
   `c33af395f4f3a3c23b625219d4f0911de2c35b08d6ffecc2710a118766f17328`이며
   S104 config·execution lock·시작 registry fingerprint와 다시 일치한다.
5. 변경 시간창 동안 S104 PID `386918`은 seed 42의 epoch 8 이전
   fit/validation 구간을 실행하고 있었다. 프로세스를 재시작하거나
   신호를 보내지 않았다.
6. scorer lock은 변경되지 않았으며
   `requirements/scorer-lock-linux-aarch64-cu130.txt`의 SHA-256은
   `f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2`다.
   `markdown-it-py 4.2.0`은 학습 시작 전부터 이 lock과 scorer 환경에
   이미 존재했다. 설치된 package metadata의 mtime은
   2026-07-28 16:55:09 KST다.
7. S104 config가 고정한 implementation 14개 역할, S104 config,
   execution lock, data/model source는 복구 뒤 전수 hash 대조에서
   mismatch 0이었다.

### 현재 영향 평가

- 변경된 줄은 실행 중 Python module, 학습 config, 데이터, model
  checkpoint 또는 설치된 environment를 바꾸지 않았다.
- trainer는 시작 시 implementation을 검증하고 registry fingerprint를
  기록하며, 전체 seed 종료·registry 완료·commit 직전과 commit
  validator에서 현재 바이트를 다시 검증한다.
- trainer에는 중간 변경 이력을 지속 관찰하는 watcher가 없다. 따라서
  원복 뒤 terminal 검증이 통과하더라도 “학습 내내 implementation
  source가 한 번도 변경되지 않았다”는 연속 불변성은 입증할 수 없다.
- 계산 결과에 실제 영향이 있었다는 증거는 현재 없지만, 영향이 없었다는
  최종 판정도 아직 하지 않는다.

### 완료 차단과 다음 조치

1. 현재 S104 실행을 보존해 terminal 상태와 실패/commit 증거를 먼저
   확보한다.
2. terminal commit이 생겨도 즉시 S104 완료·배점·논문 반영·S105 실행을
   허용하지 않는다.
3. 독립 commit validation 뒤 다음 항목을 결합한 판정 receipt를 만든다.
   - 변경 시간창과 전후 파일 hash/size
   - 시작 registry의 원본 fingerprint
   - 설치 환경·lock, 실행 코드·config·data·model의 불변성
   - 변경 파일이 학습 계산 경로에서 소비되는지에 대한 코드 감사
   - terminal report/registry/generation/commit의 deep validation
4. 무영향을 재현 가능하게 입증하지 못하거나 사전 계약상 예외를 허용할
   수 없으면 실행을 `INVALIDATING`으로 판정하고 새 clean S104를
   실행한다.
5. 새 실행을 할 경우 시작·종료 hash뿐 아니라 inode/size/mtime/ctime
   변화 또는 외부 연속 감사 기록을 결박해 일시 변경 후 원복도
   탐지한다.

이 항목이 `OPEN`인 동안 S104 획득 점수는 0이고, 이 실행에서 나온
수치를 canonical paper result로 승격하지 않는다.

## DEV-S104-002 — 장기 실행의 외부 SIGTERM 종료와 비종료 registry

현재 판정: **INVALIDATING**  
발견 시각: 2026-07-29 23:23–23:29 KST  
대상 실행: `aihub71490-wavlm-s104-adapter-v1-dev`의 첫 실행 시도

### 확인된 사실

1. 첫 실행은 Codex unified-exec 세션 `91182`의 자식 PID `386918`로
   시작됐다. 장기 실행을 로그인 세션과 분리하는 supervisor에는
   연결되지 않았다.
2. 마지막 생존 관측은 23:23:17 KST이며 seed 42, epoch 9 fit
   구간이었다. 최초 `/proc/386918` 부재 관측은 23:23:47 KST이고,
   23:29:19 KST의 세션 회수 결과는 exit code `143`이었다. 이는
   일반적인 `128 + SIGTERM(15)` 종료와 일치한다.
3. root와 독립 감시 작업 모두 해당 PID/세션에 signal, kill,
   terminate, restart 또는 Ctrl-C를 보내지 않았다. 감시는 빈
   `write_stdin` poll과 `kill -0` 존재 검사만 수행했다.
4. signal 발신 주체와 직접 원인은 확인되지 않았다. 따라서 원인을
   Codex timeout, 운영체제, 사용자 또는 다른 주체 중 하나로 단정하지
   않는다. 다만 unified-exec 자식으로 약 14시간 43분 실행한 구조는
   장시간 학습의 단일 장애점이었다.
5. 종료 시간창의 kernel journal에는 NVRM/XID/OOM/segfault가 없었고,
   `user.slice`와 `user@1000.service`의 `memory.events`에서
   `oom`, `oom_kill`, `oom_group_kill`은 모두 0이었다. reboot,
   shutdown, session logout 증거도 없었으며 부모 Codex 프로세스와
   다른 세션은 생존했다.
6. 종료 뒤 canonical registry metadata의 SHA-256은
   `ff7fac6b7bb9104fda8c540794d5eec45c32de7f7d3458c27e9e392f6b56b7f2`다.
   상태는 `running`, `ended=null`, `failure=null`, metric 0,
   artifact 0이며 mtime은 시작 시각인 08:41:00 KST에서 변하지 않았다.
7. generation root에는 시작 시 만든
   `.part-1ee97821e55d4fa283dea77182283c8e` 디렉터리 하나가 남았고
   자식은 0개다. checkpoint, prediction, report, completed registry,
   content-addressed generation, commit은 모두 0이다.
8. 종료 뒤 config/execution lock/project manifest/scorer lock의
   SHA-256은 각각 `aa9a19fa…a8e9`, `8d877806…274ca`,
   `c33af395…17328`, `f6c5b10c…c8cf2`로 고정값과 일치한다.

### 판정과 복구 계약

- 이 시도는 terminal failure registry조차 만들지 못했고 seed 42도
  완료하지 못했다. 결과가 없으므로 정본 실험, S104 배점, 논문 수치,
  S105 선행자로 사용할 수 없다.
- 같은 명령을 즉시 재호출할 수 없다. 현재 trainer는 fixed run
  directory가 존재하면서 registry가 `completed`가 아니면
  fail-closed하며, partial checkpoint가 없어 resume도 불가능하다.
- 수동 `rm`, registry 본문 편집 또는 기존 경로 덮어쓰기를 금지한다.
  전용 one-shot rollover 도구가 PID 부재, exact registry 상태와 hash,
  빈 partial tree, commit 부재, 모든 immutable pin을 먼저 검증해야 한다.
- 검증된 도구는 첫 시도의 registry와 빈 partial을 바이트 그대로
  content-addressed incident archive로 원자 이동하고 fsync한 뒤,
  receipt와 postflight를 남겨야 한다. 중간 실패·동시 writer·symlink/
  hardlink·nonempty partial 공격은 모두 fail-closed해야 한다.
- 깨끗한 재실행은 계산·데이터·모델·config·execution lock을 바꾸지
  않고 처음부터 수행한다. 프로세스는 unified-exec 세션이 아니라
  persistent user-systemd service에 두고, 별도 연속 무결성 감시와
  종료 상태 seal을 붙인다.

DEV-S104-001은 첫 시도의 결과를 사후 수용하는 근거로 사용하지 않는다.
깨끗한 재실행과 독립 commit 검증이 끝난 뒤에만
`CLOSED_BY_RERUN`으로 닫을 수 있다.
