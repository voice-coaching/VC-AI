# 운영 선행조건 달성 기록

2026-10-03 20:02:32 KST(11:02:32 UTC) 관측. 대상은 AWS EC2
`13.209.184.226`과 RunPod `s8hb5b0l5k4qlx`, FE `https://vc-fe.vercel.app`다.
Backend PR #100의 운영 준비 요청과 FE 운영 반영 추가 승인을 근거로 수행했다.

## 설치·활성화

| 대상 | 실제 상태 |
| --- | --- |
| Backend | 기존 scoring JAR `1bdd87650192e21ec75e228d760b2008fed5ed61` 유지, verifier 설정 전환 후 재시작 |
| JAR SHA-256 | `c773ed80824895eeba86a2d8614a2332364badf81a0d58266ee95cd3fd4253d2` |
| AWS verifier | `/opt/alpha-canonical/releases/f4730d4d5e3b92a87df20271aa63903e66330ff502c49cef6220726b58d78954`, 원래 core/S7/H5와 scored core/H5, 계약 9개 포함 233개 파일 |
| RunPod 앱 | AI `4a63fd0e70986c6fc4a8f365c4f7e71791d90dbb`의 39개 파일을 기존 base `32d98e39767b56697bc7e58fb50e3e9d61432349`에 overlay 적용 |
| RunPod bundle | `3c6dc05deb50ce0c1eadfe6ebfd369bbc236f21bada33f434ed25d84dd139c00` |
| 최종 overlay manifest | `2f9b3f35e905866de4fabd0b8a16d2c1908dff373142423ee8f11ac64d707a3e` |
| scored core | `/workspace/intelligentai/releases/canonical_scored_core_20261003_v1` |
| scored H5 | `/workspace/intelligentai/validation/canonical_scored_h5_20261003_v1` |
| FE 운영 main | `36a49772c3ddc3df00cd44542064c502953469ac`, PR #43 → dev, PR #42 → main 병합 |
| Vercel | GitHub Production deployment `6826706906`, `success`, `vc-m5dbdfe92-youngwookes-projects.vercel.app` |

RunPod gateway에 인증된 `/health/deployment` 경로를 추가했다. TTS 설정은 변경하지 않았다.
AWS nginx의 API server에 유지보수 snippet을 설치·검사·reload했다. 신규 analyze/retry/
feedback regenerate만 차단하는 marker를 사용했으며 현재 marker는 제거됐다.

`/etc/alpha-deploy/runtime.json`은 root 소유 비밀값 없는 profile이며, verifier pin 8개와
schema 9개, RunPod revision/bundle 및 FE 실제 HTML의 JS 18개 digest를 고정한다.
profile SHA-256은 `ea0590a8be90019e8a727f3ad2f41fa52cb9443a32e6f8f916876ecdd5376e92`다.
`/var/lib/alpha-deploy/current.json`과 `bootstrap-20261003.json`에 현재 호환 기준을 기록했다.
Backend PR #100 자체는 병합·배포하지 않았다. root 운영 도구 설치본은 BE
`251124230c96fa97c26e9fa6ea2cc86b88445d4b`이며, 새 서비스 계층 admission은 PR 배포 후 적용된다.

## 실행 중 보완한 사항

- Python 기본 User-Agent가 AI ingress에서 403이었다. 배포 probe에 식별 가능한
  User-Agent를 지정했으며 같은 인증 경로가 정상 응답하는 것을 확인했다.
- 2026-08-09 legacy PENDING 1건은 request/execution ID와 dispatch 이력이 전혀 없었다.
  이런 미접수 기록만 drain 대상에서 제외하고 모든 실제 실행·전송·lease 검사는 유지했다.
  DB 행이나 lease를 변경하지 않았다.
- FE `main` → `dev` 병합본은 시험 세션 함수 두 개의 import가 누락돼 컴파일 실패했다.
  PR #43에서 기존 import를 복원하고 TypeScript noEmit 및 Vercel 빌드 성공을 확인했다.
- 기존 verifier 내보내기는 7개 계약만 포함했다. 배포 preflight가 요구하는 legacy result와
  canonical journal 계약도 필수 파일로 추가하고 새 digest 경로에 전체 묶음을 설치했다.
  기존 불변 release를 덮어쓰지 않았다.
- AWS 지원 준비와 접수 준비를 구분했다. 접수 준비는 새 RunPod 계약에도 의존하므로
  양쪽 전환 중 nginx 접수를 닫고, 최종 상호 readiness 확인 후에만 열었다.

## 확인 근거와 제한

최종 11:01:56, 11:02:14, 11:02:32 UTC 관측에서 AWS canonical supported/admission,
RunPod executor/admission이 모두 true이고 reasonCode는 null이었다. 앱 identity·하네스 pin·
9개 계약 digest가 일치했다. DB 분석/전송/취소/receipt/callback/apply/effects/upload/journal/
lease/트랜잭션/ACK와 Pod activeJobs는 모두 0이었다. 마지막 FE digest 확인 후 접수를 열었다.

접수 재개 후 기존 JAR에 대한 `release.py check`가 `PREFLIGHT_READY`를 반환했고,
current manifest는 `COMPLETE`, 유지보수 marker는 없음으로 확인했다. AWS 직접 경로와
FE 프록시의 세션 107 상태 GET은 비인증 요청에 모두 401을 반환했다. 이는 경로 연결과
인증 경계 확인이며 로그인 사용자의 세션 조회 성공을 검증한 것은 아니다.

FE 공개 `/practice/103` HTML과 점수 계약을 포함한 chunk
`/_next/static/immutable/chunks/2b1ovg4v87abo.js`를 읽어 확인했다. SHA-256은
`ad23364289502cca1623eac2d35b87263921612a7d2a809377fef31993207285`다.
FE revision은 GitHub의 Vercel production 상태로 연결했다. 연결된 Vercel 계정은 해당
팀에 권한이 없어 내부 build log와 보호된 개별 deployment URL은 조회하지 못했다.

수행 범위는 컴파일·정적 확인·운영 설치와 readiness/무결성/접수 상태 확인이다.
실제 녹음, GPT 채점·설명 생성, callback end-to-end, 브라우저 및 자동 회귀 QA는
실행하지 않았다. API 준비 성공을 실제 음성 분석이나 피드백 품질 QA 통과로 표현하지 않는다.
독립 TTS의 생성·품질 검증도 수행하지 않았다.
