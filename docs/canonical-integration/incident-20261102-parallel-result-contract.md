# 두 문장 지연과 결과 직렬화 실패 — 2026-11-02

## 운영 관측

대상 `J406.2020067.notebook.dlpc`, 2026-11-02 00:35 UTC 이후 읽기 전용 조회.
실행 revision은 `4fc844a35c4a748b1688006b6bf170d0d01f22da`, bundle은
`23d978698b53ba1ff8cf24f3d99e7a251b5d1ab2e39491d247e5cac11c853663`였다.
PR #48이 main `d82609f`로 병합됐지만 이 호스트에는 배포되지 않은 상태였다.
서비스 readiness와 Backend admission은 정상이고 activeJobs/directJobs는 모두 0이었다.
`/tmp`는 약 4GiB 여유로, 이번 실패를 이전 디스크 고갈과 동일 원인으로 판단하지 않는다.

현재 affinity CPU 16개, MemAvailable 약 173GiB, A100 80GB GPU 3개가 관측됐다.
이는 host/container 관측이며 추론 성능 보장이나 다른 작업의 자원 사용권을 뜻하지 않는다.
배포 설정은 여전히 `dlpc-4cpu`이며 frozen GPU 배치나 모델을 임의 변경하지 않는다.

최근 두 실행의 executor 로그는 모두 CORE, EXPRESSION, PREFLIGHT를 통과하고
RESULT_PUBLISH에서 `CANONICAL_SCHEMA_INVALID`로 종료됐다. 해당 작업 폴더의
mtime은 00:27:30 및 00:29:46 UTC였다. 로그 단계별 시간은 다음과 같다.

| 단계 | 이전 실행 | 최근 실행 |
|---|---:|---:|
| 예약·음성 준비 | 2.62초 | 2.69초 |
| CORE | 18.76초 | 20.38초 |
| EXPRESSION | 9.09초 | 8.68초 |
| PREFLIGHT | 0.30초 | 0.32초 |
| RESULT_PUBLISH | 0.62초, 실패 | 0.62초, 실패 |

이 시간은 기존 로그 관측이며 이번 변경의 성능 비교 결과가 아니다. 두 폴더 mtime 차이를
순수 AI 대기 시간으로 해석하지 않는다. 실제 사용자 요청 시각과 Backend 대기 시간은 별도다.

## 원인과 변경

1. HttpRuntime resident 경로와 DLPC ResourceBudget이 활성 요청을 1개로 제한했다.
   전체 파이프라인이 한 슬롯을 점유하므로 두 번째 요청은 자원 여유와 무관하게 지연된다.
2. 운영 H5 `25531c9e52c1088c9cdceee82a5e2d57d52c46da4b4cd93cac75eea87d6ade60`은
   sourceIdentity enum 및 HarnessIdentity에 등록됐지만 v6 schema의 scoring/GPT 분류
   조건에는 누락됐다. 그 결과 legacy score 분기로 검증돼 정당한 scoring 필드가 거부된다.

수정은 다음과 같다.

- DLPC 활성 요청 상한은 자원 예산으로 계산한다(이번 관측의 짧은 음성 추정 36개). 각 요청에 독립된 CanonicalResidentCore/MFA
  generation·socket을 배정한다. 하나의 공유 resident에 동시 요청을 밀어 넣지 않는다.
- Backend claim 전에 event loop에서 worker를 원자적으로 예약한다. claim 실패,
  실행 완료·취소 때 예약을 반환하고 한 작업의 일반 실패는 해당 resident만 회수한다.
- worker 사용 중에는 generation 교체·삭제를 보류한다. 하위 프로세스 회수 미확인 시
  전체 admission은 닫힌 상태를 유지한다. 기존 3회 generation 수명과 lease 정책은 유지한다.
- 시작 시 첫 worker만 준비하고 요청에 따라 최대 32개 독립 worker를 활성화한다.
  cold worker 준비 중에도 Backend heartbeat·lease·deadline·취소를 유지한다.
  worker별 refresh lock으로 준비 작업의 중복 실행을 막는다. 기존 메모리·디스크·inode admission 제한은 유지한다.
- AI/Backend 양쪽 `runpod_result_v6.schema.json`의 result/audioResult에 있는 scoring과
  GPT grading 분류 enum, 총 네 곳에 위 운영 H5만 추가한다. 가짜 점수, schema 검증 생략,
  identity 치환이나 후보 모델의 일괄 승격은 하지 않는다.

이 병렬화는 인증형 Backend→AI canonical 경로 대상이다. 익명 Direct batch의 별도
dispatcher까지 병렬화됐다고 표현하지 않는다. GPU 1/2 전환 또는 frozen 실행 정책 변경도 아니다.
독립 요청이 자원을 공유하므로 병렬화만으로 개별 요청 시간이 일정하거나 2배 빨라진다고 보장하지 않는다.

## 계약 배포 순서

수정 v6 schema SHA-256:
`f17fd6ff70cd4fa3fcb1ee91d878cdf7fef0b2880159e4da17b5d19a46b9e40f`.
Backend `develop` PR의 계약 파일과 AI 계약 파일은 byte 단위로 동일하다.

1. Backend 담당자가 계약 사본과 semantic verifier가 사용하는 schema를 함께 갱신한다.
2. 신규 접수를 닫고 진행 실행·handoff를 drain/reconcile한다. 실패한 producer를 자동 재실행하지 않는다.
3. AI는 공식 coordinated 배포 경로로 schema와 소스를 함께 전환한다. protected schema를
   단순 source controller로 hot patch하거나 guard를 해제하지 않는다.
4. 양쪽 11개 schema digest와 verifier 설치 identity, local/public readiness 및 Backend admission을 확인한다.
5. 개발자가 동시 요청 증가(최대 32개), cold worker 준비 중 lease·취소, 독립 취소·실패, 3회 generation 교체, 결과 저장·표시를 직접 QA한다.

이 작업에서는 변경 소스 AST/JSON 구문, 양쪽 schema bytes/hash, Git diff만 정적으로 확인한다.
자동 테스트·브라우저 자동화·실제 녹음·추론·GPT 생성을 실행하지 않는다.
PR 수정은 아직 배포 완료나 실제 피드백 성공을 뜻하지 않는다.

## 이전 단계: 32개 상한 변경

사용자 후속 요청에 따라 MAX_CANONICAL_WORKERS=32를 단일 상한으로 사용한다.
DLPC ResourceBudget와 HttpRuntime, CanonicalLane 범위 검증을 함께 변경한다.
32개는 admission 상한이며 자원이 부족하면 기존 can_admit 제한으로 추가 접수를 거부한다.
시작 시 32개 모델/MFA를 동시에 기동하지 않는다. 사용된 worker는 기존 3회 generation
수명과 정리 정책을 유지하며, 실제 32개 동시 처리 성능·메모리 사용량은 미검증이다.
익명 Direct dispatcher와 Backend schema는 이번 상한 변경에서 추가 수정하지 않는다.

## 최신 자원 예산 변경

고정 32개는 [요청당 자원 비용·동시 처리 계획](capacity-budget-20261102.md)의
관측·가정·계산으로 대체했다. 절대 ceiling 128, 현재 짧은 음성 상한 추정 36개,
무거운 연산 예산 3개를 구분한다. 실제 부하·추론 검증이나 배포 완료를 뜻하지 않는다.
