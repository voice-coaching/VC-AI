# MFA disambiguation 중복 연산 제거 계획·실행

브랜치 `feat/mfa-disambiguation-20261025`는 local main
`ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 생성했다.
기준은 준비 재사용 v2와 job `5bbcc2f3-add0-4ff8-9c5f-525b0577c647`의 MFA
10.595133초, disambiguation 2회 합계 2.602933초다.

## 구체적 계획과 완료 조건

| 순서 | 실행 | 완료 조건 |
|---|---|---|
| 1 | MFA 3.4.1 원본 class/function의 고정 SHA와 변환 문맥을 확인 | 원본 모듈·함수 SHA가 다르면 빌드/기동 실패 |
| 2 | 발음별 split을 한 번 수행하고 prefix 집합 구성·membership 확인 제거 | Word/Pronunciation 조회·순서·번호 부여·빈 발음 처리·max/symbol·bulk update/commit 순서 유지 |
| 3 | 원본 site-packages를 수정하지 않고 DirectAligner subclass에만 설치 | worker 기동 receipt에 원본/최적화 함수 hash와 revision 기록, 요청별 mutable cache 없음 |
| 4 | 현재 고정 core/H5에서 새 불변 쌍 생성·닫힌 등록·복구 연결 | GPT policy·thread·모델·사전/feature·두 번 계산 호출·cleanup gate 유지. 이전 prepared v2로 동시 롤백 가능 |
| 5 | 구문·SHA·실제 선택 identity/schema preflight와 운영 READY 확인 | inactive candidate를 자동 허용하지 않고 승인된 새 쌍만 선택 |
| 6 | 앞서 허용된 실제 API 측정 방식으로 동일 음성·문장 1회 실행 | 실제 MFA 단계 시간·전체 시간·원본 TextGrid/interval과 비교, 실패 시 재제출 없이 원인 확인 |

단위·회귀·화면 테스트 파일이나 fixture/mock은 생성하지 않는다. API 실측은 기존에
허용된 실제 녹음 방식으로 수행하고 임시 WAV/관측 JSON을 저장소 밖에 두었다가
작업 종료 전에 삭제한다. 단일 녹음의 성공을 전체 품질/회귀 통과로 표현하지 않는다.

이번 변경은 **함수 내부 연산만** 줄인다. 두 번째 호출 자체의 조건부 생략,
lexicon 재사용, 2/4 thread 전환, corpus DB 제거는 포함하지 않는다.
두 계산은 각각 필요한 사전 상태에서 계속 수행한다. 실제 절감량은 측정 전 미확인이다.

## 동일 의미를 유지하는 근거

고정 원본은 같은 발음 목록에서 전체 발음 자체를 포함한 모든 비어 있지 않은
prefix를 집합에 넣고, 다시 전체 발음의 membership을 검사한다. 따라서 비어 있지
않은 tuple은 항상 참이고 빈 tuple은 항상 거짓이다. tuple의 truth 값으로 이 조건을
대체할 수 있다. 첫 순회에서 발음을 모두 tokenization한 목록을 만든 뒤 기존 순서로
카운터를 갱신하여 원본의 split 검증 시점도 유지한다.

원본 함수 SHA-256:
`327efe0b16638b59d93bfd4fe8220e82be7686b4b051cbc02e510e75ff9cfa36`.
원본 module SHA-256:
`3cd456e52810efe9534bdaf30bb79ff2368ed6383ed3849d35b2fa2bb8fb4608`.
이 전제는 해당 고정 구현에만 적용한다. MFA 버전/원본 소스가 바뀌면 새 검토 없이
최적화 코드를 적용하지 않는다.

## 실행 결과

계획 1–6을 수행했다. 원본 함수의 고정 문맥에서 prefix 집합 생성과 두 번째 split을
제거하고, 첫 tokenization 순회에서 `(Pronunciation 객체, 음소 tuple)` 목록을 만든다.
같은 순서로 기존 카운터·DB update 목록을 구성한다. query·bulk update·Phone symbol
추가·commit 구문은 원본 그대로다. 두 계산 호출과 요청별 DB/aligner·정리 gate를 유지한다.

helper는 MFA 3.4.1, DirectAligner class, 원본 method의 module/qualname/소스 SHA/
code line을 확인하고 **DirectAligner subclass에만** 변환한 메서드를 설치한다.
MFA 원본 class와 site-packages 파일은 변경하지 않았다. 원본 namespace의 객체를
이용하지만 tokenized 목록·카운터는 함수 지역 상태이며 요청 간 cache가 아니다.

최적화 함수 SHA는
`52c2cd94c253cfac85d20f8c5d77badf84cf791327302f3d34c2fb9a64c2a224`다.
worker 기동과 실제 요청 receipt 모두 installed=true와 이 hash를 기록했다.

## 고정 릴리스와 운영 반영

| 항목 | 값 |
|---|---|
| core | `canonical_dlpc_mfa_disambiguation_20261025_v1` |
| core manifest SHA | `55cdf30b40dd463080c2aeab8832821a2f6874a9994cc30657d8c5ef3eb6b714` |
| H5 | 위 이름의 `_h5` |
| H5 manifest SHA | `0bc2d14c5229a932f56d0b984f357ccfcc3833775860a9857e986a36a55d2806` |
| H5 lock SHA | `eb73ac8878e3154b6fc4c5b0609adb30bb0361e49048ad9b4bfbf5bde92e5e00` |
| prompt SHA, 부모와 동일 | `ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d` |

자산은 `/workspace/intelligentai/validation/` 아래 새 디렉터리에 생성했다.
core 변경 파일은 resident_worker·mfa_disambiguation helper·historical_runtime_identity
세 개다. 나머지 모델·사전·DirectAligner·feature·Native/GOP·감시·thread·정리 소스는
부모와 byte가 같다. H5는 새 parent binding과 lock/manifest만 다르며 Luna none/default,
request config·prompt·rubric·입출력 schema는 유지한다. Fast는 사용하지 않는다.

실제 서비스 UID에서 identity·closed schema tuple·file SHA preflight를 통과했다.
활성 작업 0을 두 차례 확인하고 소유 admission hold를 둔 뒤 분석만 중지·적용했다.
처음 적용 helper의 90초 제한이 만료됐으며, 상태 조회에서 runtime과 direct 선택이
기존 prepared v2 그대로임을 확인했다. 선택적 단계 출력 추가·재개 작업은 자동 승인
검토 시간 초과로 실행되지 않아 그 편집을 되돌렸다. 이미 준비한 고정 helper를
직접 실행해 적용하고 분석을 기동했다. 사용자 분석을 중복 제출하지 않았다.

**09:09:18 UTC** 새 generation `vc-core-k76pjigi`의 READY, 최적화 설치 receipt,
local·공개 direct readiness 200/ready=true를 확인한 뒤 hold를 해제했다.
**09:12:37 UTC** 최종 조회에서도 local deployment health 200, activeJobs/directJobs=0,
local·공개 direct readiness 200/ready=true, hold 없음이었다.
TTS는 PID 460835로 8일 21시간 이상 계속 RUNNING이었고 재시작·합성하지 않았다.

내부 Backend의 AI_CANONICAL_CORE_ROOT/LLM_ROOT는 기존 GOP CUDA·announcer 쌍이다.
AI_DIRECT_CORE_ROOT/LLM_ROOT만 새 쌍을 선택한다. NUM_JOBS/BLAS/OMP=1, 단일 실행
슬롯과 3-request 세대 budget을 유지했다.

영속 payload/selector는
`/home/2020067/.local/share/intelligentai/recovery/startup-20261006/mfa-disambiguation-20261025`다.
selector는 optimized이며 복구 hook에서 prepared v2보다 먼저 선택한다.
[restore_mfa_disambiguation.py](../../deploy/dlpc/restore_mfa_disambiguation.py)의
`--rollback`은 분석 중지 상태에서 runtime·private schema·direct core/H5를 prepared
v2로 복원하고 selector도 함께 전환한다. env/key 사본은 생성하지 않는다.

## 실제 동일 음성 1회 측정

이전 정상 job과 같은 `testaudio.m4a`, 문장 **쌀과 콩을 깨끗한 그릇에 담아요**를
mono PCM s16le 16kHz WAV로 변환해 공개 direct API에 1회 제출했다.
원본 m4a·기존 mp3는 수정하지 않았다.

| 항목 | 관측 |
|---|---|
| POST 시작 | 2026-10-25 09:09:54.873358 UTC |
| clientAttemptId | `e378f8ac-6078-4b84-80f1-1b3fd45bb50b` |
| jobId | `57364a15-a9b7-437f-a06c-6a737a6c1aed` |
| executionId | `a3b4b85e-de40-4fa1-8917-acaec0fddc19` |
| requestId | `6de263ba-7e7b-4de9-b438-5c5e46fe65da` |
| WAV SHA-256 | `71dd0b1bd6929f0d921a630725c14f6f047cc27dcb978ac4c38b6d0a08770a1d` |
| script SHA-256 | `b9b4cab47fd1c9dafdd34167a478290a93e22b3fb2669e82907327aa45d9c3af` |
| 접수 | HTTP 202, POST RTT 0.082988초, 제출 1회 |
| 분석 | ACCEPT, generation 1 / ordinal 1, worker ok |
| GPT | gpt-6-luna / none / default, HTTP 200, dispatch 1회, finish stop |
| 생성 | SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID, score 97.2 / RUBRIC_COMPUTED |
| trace·정리 | complete=true, 55 spans, dropped=0, DB close/lifecycle gate PASS |
| 보관·이력 | archive/history sync 모두 COMPLETE |

이전 prepared v2 job `5bbcc2f3-add0-4ff8-9c5f-525b0577c647`와 retained 시각을
같은 정의로 계산했다. 비교는 서로 다른 시각의 각 1회이며 p50/p95·일반적 개선율이 아니다.

| 구간 | 이전 초 | 이번 초 | 관측 차이 |
|---|---:|---:|---:|
| 클라이언트 전체 | 20.590341 | 22.633384 | 증가 |
| 서버 접수→결과 준비 | 20.263268 | 20.662926 | +2.0% |
| core producer | 13.359206 | 13.086307 | −2.0% |
| **MFA** | **10.595133** | **9.895470** | **−6.6%** |
| **disambiguation 2회 합계** | **2.602933** | **2.517102** | **−3.3%, 약 0.086초** |
| workflow, worker timer | 8.922663 | 8.778999 | 감소 |
| lexicon compiler | 1.323211 | 1.333430 | 증가 |
| MFCC | 1.655692 | 1.513970 | 감소 |
| alignment 분석 | 0.735420 | 0.425604 | 감소 |
| cleanup_total | 0.472612 | 0.245855 | 감소 |
| GPT API | 4.955205 | 5.671092 | +14.4% |

**전체 MFA 약 0.700초 감소를 prefix 제거만의 효과로 볼 수 없다.** 해당 함수 관측
차이는 약 0.086초이며 feature·후처리·GC 등도 달라졌다. GPT 지연과 polling 관측 차이로
전체 체감 시간은 짧아지지 않았다. 큰 지연 개선이나 8초 목표 달성을 주장하지 않는다.
MFCC·부모 workflow/cleanup inclusive 시간은 중첩되므로 표를 합산하지 않는다.

기존 실제 산출물과 비교한 결과는 다음과 같다.

- MFA TextGrid SHA-256: 두 요청 모두
  `d0880a56b4d563381c29cb6d3128fbb55e5a00a5d67b8a65e36ee0de91f4780e`.
- phone_intervals·word_intervals: 값과 순서가 모두 동일.
- 원본 MFA module SHA: 배포 후에도 기존 `3cd456e5…b4608`과 동일.

이는 이 녹음에서 정렬 출력이 동일하다는 근거다. 모든 입력·예외·언어에 대한
동등성이나 전체 품질 통과를 입증하지는 않는다. GPT 점수는 이전 97.5와 이번
97.2로 달랐으며, MFA 산출물 동일을 공개 GPT 결과 전체 동일로 확대하지 않는다.

## 실제 확인·변경 파일과 남은 작업

| 확인 | 결과 |
|---|---|
| 변경 Python/JSON | Python 28개 compile, JSON 2개 parsing |
| 새 core/H5 | core Python 63개·H5 adapter compile, 전체 file/lock SHA 확인 |
| private schema | 구조 compile 및 실제 선택 identity의 서비스 UID preflight 통과 |
| 배포 runtime | 9개가 브랜치·고정 pin과 SHA 일치 |
| 영속 helper/hook | 2개가 브랜치와 byte 일치 |
| 실제 API | 정상 1회 완료·GPT 생성·정렬 출력 동일·보관 완료 |
| 문서 정적 확인 | local 링크 43개 누락 없음·충돌 표식·Git diff 확인 통과 |

이번 변경은 다음 파일이다. 이전 prepared/GPT/M0 prerequisite는 새 main 기반
worktree에 연결했으며 기존 worktree·사용자 변경은 보존했다.

- [mfa_disambiguation.py](../../deploy/dlpc/performance/mfa_disambiguation.py)
- [build_mfa_disambiguation_release.py](../../deploy/dlpc/build_mfa_disambiguation_release.py)
- [restore_mfa_disambiguation.py](../../deploy/dlpc/restore_mfa_disambiguation.py)
- [recover_on_start.py](../../deploy/dlpc/recover_on_start.py)
- [canonical_harness.py](../../src/voice_coach/backend_analysis/canonical_harness.py)
- [native_execution.py](../../src/voice_coach/backend_analysis/native_execution.py)
- [private v4 schema](../contracts/runpod_result_v4.schema.json)
- 이 문서와 [docs 목차](../README.md)

API 실측 외 단위·회귀·화면 테스트나 fixture/mock을 생성하지 않았다. 저장소 밖에
생성한 임시 WAV 1개와 관측 JSON 1개를 삭제하고 전용 디렉터리의 부재를 확인했다.
commit/push/PR은 수행하지 않았다.

남은 것은 disambiguation 안의 ORM 조회·관계 로딩·카운터·DB 쓰기 비용을 나눠
확인하고, 조건부 재계산 생략과 lexicon 재사용을 검토하는 일이다. prefix 생성만의
절감폭은 이번 1회에서 작았다. 8초 이하 목표와 더 넓은 품질 판단은 미완료다.
