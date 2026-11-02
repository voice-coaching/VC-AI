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

## 후속 확인과 접수 전 계약 확인

2026-11-02 10:45~11:00 UTC의 J406 읽기 전용 조사에서도 보존된 마지막 실행은
analysisId=105, executionId=`cab43e0b-9cc3-49cf-b4f2-dee5928e8ee8`였다.
`executor.log`는 00:29:16~00:29:48 UTC에 CORE/EXPRESSION/PREFLIGHT 통과 후
RESULT_PUBLISH의 `CANONICAL_SCHEMA_INVALID`를 기록한다. Backend 관리 조회는
실패했으므로 서비스 전체의 가장 최근 접수 여부는 확정하지 않는다.

AI [PR #49](https://github.com/voice-coaching/intelligentAI/pull/49)는 01:01:16 UTC에
main으로 병합됐다. 후속 조회에서 원격 main은 `05b0907`이었지만 운영 v6 schema는
여전히 SHA-256 `e7a3d57bb3d8f2d7114a1f20c1339706f07e5b3377259bf6539bcb79dc0c6987`였다.
수정 schema와의 차이는 위 bounded H5의 네 분류 enum 추가뿐이다. 소스 병합을
운영 반영으로 보고하면 안 된다. 일반 source 배포 client는 `docs/contracts/` 변경에
`COORDINATED_IMMUTABLE_RELEASE_REQUIRED`를 반환하도록 구현돼 있다.
이번에 그 배포 workflow의 실제 실행 결과를 조회한 것은 아니다.

후속 수정은 `check_schema_registration`에서 v6 result/audioResult의 실제 scoring 및
GPT 분류 분기를 읽어 실행 H5의 `scoring`/`gpt_grading`과 대조한다. 기존 검사는
manifest 등록만 확인해, 등록은 있지만 분류가 빠진 위 상태를 놓쳤다.
이제 frozen artifact 준비 때 불일치를 감지하여 해당 resident가 ready가 되는 것을 막는다.
분기 누락·중복도 거부하며 가짜 녹음·점수·결과를 만들어 검증하지 않는다.
스키마 bytes와 기존 네 enum 보정은 이번 후속 commit에서 다시 변경하지 않는다.

이 후속 소스만 운영에 넣으면 기존 schema가 자동으로 고쳐지는 것이 아니다.
위 계약 배포 순서대로 PR #49의 schema와 이 후속 소스를 함께 반영해야 한다.
schema 보호·서버 인증·실행 identity 검증을 우회하거나 실패한 요청을 재실행하지 않는다.
확인은 소스/JSON/AST/문서 링크/Git diff 정적 확인에 한정하며 테스트·추론·배포는
수행하지 않는다. 개발자가 수정 계약을 설치한 뒤 readiness 및 실제 결과 수신을 QA한다.
