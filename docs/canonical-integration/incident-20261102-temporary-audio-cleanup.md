# 임시 음성·resident 작업영역 정리 — 2026-11-02 작업

사용자는 임시파일 정리 구현과 AI 서버에 누적된 음성파일 삭제를 승인했다.
기존 root checkout의 사용자 변경은 보존하고 최신 main `65a3661`에서
`fix/temporary-audio-lifecycle-20261102`를 분기했다.

## 계획과 정책

1. 진행 작업·Direct 대기열·미완료 handoff를 확인한다.
2. 분석 서비스만 정상 정지하고 소유·경로를 확인한 누적 음성 및 임시 generation을 삭제한다.
3. 기존 revision의 서비스를 재개하고 공개 readiness와 Backend admission을 조회한다.
4. 요청별 음성 및 resident generation의 종료 정리를 구현하고 PR로 인계한다.

200GB 증설이나 `/tmp` tmpfs 확대는 수행하지 않는다. 모델·코퍼스·정본 소스,
결과 JSON·digest·저널·재전송 artifact와 다른 사용자의 임시파일은 삭제 대상이 아니다.
외부 저장소의 녹음 원본과 사용자 기기의 파일도 이번 서버 로컬 삭제 대상이 아니다.

## 실행 관측

대상은 `J406.2020067.notebook.dlpc`다. 아래 UTC는 **호스트가 반환한 시각**이다.
작업 세션 날짜와 호스트 날짜가 다르므로 이후 기록에서 동일 시각으로 가정하지 않는다.

| 관측 | 결과 |
|---|---|
| 삭제 직전 AI 상태 | activeJobs=0, directJobs=0 |
| Direct 문서 | 65개 모두 RESULT_READY, archiveStatus=COMPLETE |
| canonical workspace | 17개, 미완료 handoff=0 |
| 분석 서비스 | Supervisor 정상 stop 후 삭제, 이후 원래 서비스 start |
| 삭제 영수증 시각 | 2026-11-01T15:16:49.989773+00:00 |
| 삭제한 임시 트리 | 서버 UID 10001 소유 `vc-core-*`, `canonical_ai_release_*` 합계 893개 |
| 위 트리 바깥의 삭제 음성 | 211개, 54,906,082 bytes |
| 삭제 직후 `/tmp` 여유 | 4,319,817,728 bytes |
| 재개 직전 inventory 시각 | 2026-11-01T15:17:07.127583+00:00 |
| 재개 직전 잔여 누적 파일 | 대상 임시 트리=0, 대상 운영 영역 음성=0 |
| 서비스 재개 후 | local services=200, executorReady=true, handoffReady=true |
| 공개 `/readyz` | 200, status=ready |
| Backend handoff | 200, admissionEnabled=true |
| 재개된 AI revision | `4fc844a35c4a748b1688006b6bf170d0d01f22da` |

음성파일 211개는 삭제한 임시 트리 내부 파일을 포함하지 않는 수치다.
서비스 재개 후에는 정상적인 새 generation과 작업파일이 다시 생성될 수 있다.
readiness 복구는 실제 녹음·추론·피드백 품질 검증이 아니다. 독립 TTS는 재시작하지 않았다.

## 소스 변경과 배포 경계

- `scratch_cleanup.py`: private 실행 트리의 음성만 삭제하고 JSON·handoff 근거를 유지한다.
  종료된 generation은 `mfa_runtime/preflight.json`에 기록된 정확한 별도 local scratch와
  함께 제거한다. 다른 generation을 glob으로 추측해 삭제하지 않는다.
- `canonical_resident.py`: 종료·실패·회수된 standby/retired generation을 재사용 전에 정리한다.
  하위 프로세스 회수 미확인 또는 정리 실패는 차단 상태로 남긴다.
- `canonical_lane.py`, `http_runtime.py`: request guard 회수 후 음성 중간파일을 삭제한다.
  GPT·결과 인계 중 generation verification 자료가 사라지지 않도록 active 작업 동안
  generation 전환·정리를 보류한다.
- Direct service/store/delivery: 종료된 실행의 음성 중간파일을 지우고,
  실패·취소 또는 기존 B2 archive 완료 후 로컬 입력 `audio.wav`를 삭제한다.
  재시작 시 terminal 작업도 정리하며 결과·history/idempotency 문서는 유지한다.
- 기존 Direct archive v2/B2 원본 보관 계약은 유지한다. 업로드 실패로 archive가 미완료인
  Direct 입력은 보관 재시도에 필요한 동안 남으며 기존 spool 용량 제한이 적용된다.
  B2 음성 영구 보관 자체를 폐기하는 작업은 외부 계약 변경·외부 객체 삭제와 구분한다.
- `cleanup_runtime_scratch.py`: root/J406/STOPPED·작업 상태·소유권을 검증하는 일회성 운영 도구.
  기본은 inventory이고 `--apply`에서만 고정된 대상 경로를 삭제한다.

frozen bundle·schema·native execution identity는 변경하거나 우회하지 않았다.
frozen prepare가 preflight 기록 전에 실패해 만든 별도 local 디렉터리는 parent가
정확한 귀속을 확인할 수 없어 자동 glob 삭제하지 않는다. 이번 STOPPED 정리에서는
해당 누적 디렉터리도 삭제했다. 이러한 불완전 준비 경로까지 root를 통합하려면
별도의 versioned frozen-release 변경과 검증이 필요하다.

## 확인과 인계

변경 Python의 AST 구문 확인과 Git diff 정적 확인만 수행한다. 자동 테스트·브라우저
자동화·실제 녹음·추론·GPT 생성·품질 QA를 실행하지 않는다.

운영 누적 파일 삭제와 기존 서비스 복구는 수행했다. 재발 방지 소스는 이 브랜치의
PR 인계 대상이며, 원래 revision을 재개한 것을 새 코드 배포 완료로 표현하지 않는다.
병합·배포 시 공식 controller의 revision/bundle 영수증을 확인해야 한다.
개발자는 성공·실패·취소·재시작·3회 generation 교체·archive 재시도 경로를 직접 QA한다.
