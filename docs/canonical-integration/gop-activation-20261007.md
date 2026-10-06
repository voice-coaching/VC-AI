# GOP 하네스 활성화 기록

2026-10-07 사용자 요청: 병합된 GOP 하네스를 RunPod에서 활성화한다. 신규 생성/품질 QA가 아닌 운영 배포·readiness 확인 범위다.

배포 준비 중 AI main `60091d824edc28b55227527b79643182f995f370`의 병합 오류를 발견했다. `canonical_verify.py`에 정의/등록되지 않은 `DLPC_CUDA`, `dlpc_cuda` 참조가 남아 설치 확인이 NameError로 실패한다. frozen exporter에도 ROOTS에 없는 DLPC 디렉터리 조회가 남아 있다. 두 미정의 참조를 제거하고 등록된 Native/GOP/historical 계약 검증 및 pair 완전성 검사는 유지한다. DLPC 환경을 전환하거나 새 identity를 추가하지 않는다.

신규 GOP 패키지를 새 디렉터리에 설치해 모든 manifest 파일 hash를 확인했고, AWS 검증기는 digest별 새 release로 준비한다. 실행 중인 작업 drain, AWS와 RunPod 계약 일치, 배포 후 direct/handoff readiness를 확인한 뒤 접수를 재개한다. 운영 관측 결과는 배포 완료 후 이 문서에 추가한다.

## 활성화 완료: 2026-10-07 00:25 KST

| 대상 | 적용값 |
|---|---|
| RunPod | `s8hb5b0l5k4qlx`, A40, RUNNING 관리 API 확인 |
| AI 소스 | `89b36234555918bcb728edad0102ba4649b5ae67` (병합 main + 위 검증기 수정) |
| 실행 하네스 | `canonical-seungun-gop-gpt-20261006-v1` |
| 코어 | `/workspace/intelligentai/releases/canonical_seungun_gop_core_20261006_v1` |
| GPT 패키지 | `/workspace/intelligentai/validation/canonical_seungun_gop_gpt_20261006_v1` |
| 코어 manifest | `818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e` |
| GPT manifest | `f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5` |
| GPT lock | `74784bf0c7ba99628ed2ff8db19157c63d22a2a1cd66c58000ad2e811dc93160` |
| Runtime bundle | `084cc2e70101dfbee36c2eb81d1c74279f007b96015f1e039c4f5d88220f0638` |
| 영구 overlay | `e8e996890cf054c43770598ef17c30c71e80b359ed1d6eee342a3514e0d8a4e7` |
| AWS Backend | 병합된 develop `5e2d46835b549486c5de644d85a0edcfbc53ff42` |
| AWS verifier | `d219a53c9dcb8719a936da979665f370fb42709d3da25a6c2b08e215522e1e21` |
| 운영 FE 관측 | 기존에 배포된 `bb5e6a1f4a5418922d3ed35fdafae1b96afad9b5`, Production deployment 성공 및 GOP 계약 JS 확인 |

RunPod 배포 helper로 direct 신규 접수를 막고 drain 후 boot→manager→supervisor를 정상 종료·재기동했다. 준비 단계와 모든 설치 파일 hash를 검증했다. AWS도 hold→drain→activate→resume 상태 전이를 마쳤고 `COMPLETE`, maintenance=false다. Direct 접수 차단 파일은 제거됐고 Backend/RunPod 활성 작업은 0건이다. AWS 작업은 새로운 schema와 검증 패키지 일치를 위한 연동 배포이며 DB migration은 추가하지 않았다. 기존 릴리스 파일은 과거 결과 검증용으로 보존하고 새 추론은 GOP 경로만 사용한다.

운영 FE가 PR #65/#66을 통해 `https://ai.voice-coaching.site/direct`로 이미 바뀌었으나 AWS에 직접 분석 location이 없어 403이었다. `/etc/nginx/conf.d/ai.voice-coaching.site.conf`에 `/direct/` 전용 location을 추가해 기존 RunPod의 **8080**으로 전달한다. 20MiB body 한도, TLS 검증, SSE buffering off를 적용했다. 분석 요청은 Spring Backend·DB를 기다리지 않는다. 기존 TTS 및 다른 경로는 유지했고 DLPC로 upstream을 전환하지 않았다. 이 작업에서 FE를 새로 배포한 것은 아니다.

00:25:52 KST 최종 확인:

- RunPod `/readyz`, `/direct/readyz`, `/health/services`, `/health/deployment`: HTTP 200. executorReady/handoffReady=true, OPENAI / gpt-6-astra.
- AWS 검증기 설치·schema/pin·실행 revision 대조 및 handoff readiness 통과.
- 공개 stable direct 주소와 Pod direct 주소 모두 ready=true / HTTP 200.
- FE Origin의 분석 POST CORS preflight HTTP 200, Allow-Origin=`https://vc-fe.vercel.app`.
- 공개 FE 파일 hash를 현재 배포에 맞춰 AWS 배포 프로필에 반영하고 재확인.

확인 범위는 Backend JAR 컴파일, 소스/산출물 hash·설치 검증, 운영 readiness 및 CORS다. 녹음 분석 제출, GPT 실제 생성, 브라우저 QA·자동 회귀 테스트는 실행하지 않았다. readiness는 점수·피드백 품질 검증을 대신하지 않는다.

복구 근거: RunPod `/workspace/intelligentai/deployment/canonical-runtime-backup-20261006T151952Z`, AWS `/etc/alpha-deploy/runtime.pre-gop-20261007.json`, nginx `/root/.config/intelligentai/gop-activation-20261007/nginx-ai.before.conf`. 전환 실패 시 구형 추론을 자동 활성화하는 경로는 사용하지 않았다. 공유 볼륨은 소유권/권한 변경을 지원하지 않으므로 배포 패키지는 기존 볼륨 방식으로 보관하고 파일별 hash로 검증했다.

검증기 병합 오류 수정은 AI PR #31로 인계했다. 배포 소스 SHA는 위 값이며 후속 운영 기록 전용 commit은 실행 소스 변경이 아니다.
