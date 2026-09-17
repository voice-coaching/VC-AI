# A6000 → A40 이전 수행 기록

작성일: 2026-09-15 KST. 상태: **파일 이전·무결성 검증·A40 모델 적재·컨테이너 재생성 후 자동 서버 기동은 완료. 실제 중지 후 재가동은 호스트 GPU 부족으로 실패했으며 현재 두 Pod 모두 중지 상태다. 재가동 가용성 문제는 해결되지 않았다.**

## 대상과 선택 근거

| 항목 | 원본 | 새 대상 |
|---|---|---|
| Pod ID | `urwnuesv7o4fv6` | `s8hb5b0l5k4qlx` |
| GPU | RTX A6000 48GB | NVIDIA A40 48GB |
| GPU 실행 요율 | $0.53/시간 | 생성 응답 $0.49/시간 |
| API RAM / vCPU | 50GB / 9 | 50GB / 9 |
| 영구 볼륨 / container disk | 120GB / 40GB | 120GB / 40GB |
| HTTP Expose | `22/tcp,8080/http` | 동일 |
| 현재 상태 | 중지·원본 볼륨 보존 | 이전·기동 확인 후 중지, GPU 재할당 실패 |

새 A40의 실제 조회: VRAM 46,068MiB, NVIDIA driver 580.159.04, cgroup 메모리 상한 49,999,998,976 bytes, CPU quota 765000/100000(7.65 코어 상당). API의 vCPU 표기와 실제 quota를 구분한다. CUDA 13.0 이상·RAM 50GB 이상·vCPU 8 이상 조건으로 배정했다.

기존 Clova INT8 모델 적재는 과거 A6000에서 약 34,485MiB였으며 음성·영상 동시 처리 peak는 아니다. 같은 48GB급 A40을 선택했고 24/32GB 카드로 낮추거나 모델·양자화 정책을 바꾸지 않았다. 현재 조건의 다른 후보 요율은 L40S $1.09, RTX 6000 Ada $0.84였고 L40는 가용값이 없었다. 이 수치는 조회 시점·조건에 한정되며 가용성을 보장하지 않는다.

## 현재까지 수행

1. 원본 Pod GPU 재개 요청은 호스트 GPU 부족으로 거절됐다. 일반 `podResume`의 CPU 옵션도 메모리 부족 응답을 냈다.
2. RunPod 공식 콘솔의 공개 클라이언트 코드에서 확인한 `podResumeZeroGpu`로 원본을 GPU 없이 기동했다. `RUNNING`, `gpuCount=0`을 확인하고 기존 PC SSH 키로 원본 볼륨에 접근했다.
3. 같은 계정·EU-SE-1에 새 A40 Pod를 생성했다. 기존 이미지 `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`, 공개키·HTTP transport·비공개 HTTP 설정 공급원을 보존했다. 토큰 값은 출력·문서화하지 않았다.
4. 새 대상 `/workspace/intelligentai` 부재를 확인했다. 초기 시작 명령은 `/start.sh`이며, 복사 전 모델·분석 서버가 실행된 것으로 보고하지 않는다.
5. 새 대상 시스템 패키지 준비를 시작했고, 원본 파일 SHA-256·크기·종류·mode/uid/gid·symlink target 목록 생성기를 실행했다. [목록 생성기](../deploy/runpod/migration_manifest.py)의 구문을 정적으로 확인했다.

원본 디렉터리 관측 크기: releases 66,847,911,424 bytes, envs 17,190,718,464 bytes, deployment 213,731,328 bytes, runtime-assets 1,308,434,944 bytes. `du` 관측이며 전송 바이트·해시 검증 완료값이 아니다.

## 자동 승인 검토와 사용자 승인

- 원본 root `authorized_keys`에 이전 전용 공개키를 등록하는 최초 작업은 자동 승인 검토에서 거절됐다. 이 거절 시점에는 원본 인증 설정이 변경되지 않았다. 이후 아래 명시적 승인을 받아 제한된 키를 등록·사용·제거했다.
- 대안으로 기존 PC SSH 인증을 사용한 두 SSH 연결의 메모리 중계를 준비했다. 전체 `/workspace/intelligentai`의 민감 자료 포함 범위가 충분히 특정되지 않았다는 이유로 이 파일 복사도 실행 전에 거절됐다.
- 사용자가 위 두 Pod 사이의 모델·소스·Python 환경·분석 자산 및 기존 테스트 자료 복사를 명시적으로 승인했다. `.env`, `.env.local`, 서버 인증 파일, SSH 키, `.log`는 제외한다.
- PC 중계는 약 490.8MB 전송 후 속도 문제로 종료했다. 이 전송의 exit code는 실패/중단이며 이전 완료로 판정하지 않는다. 부분 파일은 후속 rsync가 보정한다.
- 사용자가 두 번째로 제한된 임시 키 등록과 직접 전송을 승인했다. 원본 `/workspace/intelligentai`의 `rrsync -ro`만 허용하고 `restrict` 및 2026-09-15 21:00 KST 만료를 설정했다. 일반 셸·포워딩을 허용하지 않으며 전송 종료 시 키 등록과 대상 private key를 제거한다. 첫 번째 미등록 임시 키는 제거했다.
- 원본 목록: 일반 파일 56,682개, 전체 엔트리 63,011개, 파일 바이트 79,032,534,574. manifest SHA-256 `de82b8b6f6dc1195a8e2406050c8f03d938084d812839cb7cdc8ade4f8c56490`. 대상 전체 검증도 아래 기록처럼 통과했다.
- API 조회로 원본·대상의 `RUNPOD_ANALYSIS_HTTP_ENV_JSON`, `PUBLIC_KEY`, `AI_ANALYSIS_TRANSPORT` 값 일치를 메모리에서 비교했다. HTTP 비밀 공급원의 설정 이름은 callback base URL과 방향별 서버 토큰뿐이며 canonical 저장소 설정은 없다.

## 파일 이전과 기동 적용 결과

2026-09-15 19:03:27 KST 조회에서 네 병렬 rsync와 디렉터리 후처리가 모두 exit code 0으로 종료됐다. 병렬 단계는 1,599초가 걸렸으며 기존 부분 복사 파일을 재사용·보정했다.

19:12:43 KST 조회에서 manifest 검증도 exit code 0, `verified=true`로 끝났다(523초). 56,682개 일반 파일·63,011개 전체 엔트리·79,032,534,574 bytes의 SHA-256·크기·종류·mode/uid/gid·symlink target이 원본과 일치했다. 루트 디렉터리 자체, ACL, hardlink 관계는 검증 항목에 포함하지 않는다. 검증 영수증은 새 Pod `/workspace/migration-20260915/migration-verified.json` 및 로컬의 Git 제외 캐시에 보존했다.

19:09:21 KST 원본의 이전용 authorized_keys 한 줄을 제거하고 기존 4줄을 보존했다. 19:09:43 KST 새 Pod의 이전용 임시 개인키·공개키도 제거했다. 이어서 REST API로 시작 명령을 `bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh`로 적용했다. 기존 환경 설정 일치·120GB 볼륨·40GB container disk·22/tcp·8080/http 보존을 확인했다. 19:15:43 KST 새 컨테이너에서 boot PID 51과 prepare_runtime·rsync가 실행 중이었다.

## 자동 기동 확인과 재가동 실패

| 관측 시각(KST) | 대상·결과 |
|---|---|
| 19:15:14 | 시작 명령 변경으로 새 컨테이너 `4ddb2605299b` 생성. SSH 포트 22035, A40 1개 유지 |
| 19:18:17 | 자동 복원된 8080 `/health` 200 alive, `/readyz` 503 not_ready. Clova 모델 적재 중. HTTP 인증 파일 root 소유·0600 복원 |
| 19:18:34 | 새 Proxy HTTPS `/health` 200 alive, `/readyz` 503 not_ready. 고정 도메인은 두 경로 모두 503 preparing |
| 19:20:47 및 19:21:16 | Clova `/health` 200 ready, `bnb-int8`, 양자화 linear 504개. A40 VRAM 34,485/46,068MiB. manager/supervisor 아래 api·clova 모두 health ready |
| 19:17:57 이전 확인 | 원본 `urwnuesv7o4fv6` 중지 완료: `EXITED`, `runtime=null`. 볼륨 보존 |
| 19:21~19:22 | 새 Pod 실제 `podStop` 실행 후 `EXITED`, `runtime=null` 확인 |
| 19:22~19:25 | 새 Pod의 GraphQL `podResume(gpuCount:1)` 반복 요청과 공식 REST `/start` 모두 호스트 GPU 부족으로 거절. REST는 500과 같은 원인 메시지 |
| 19:26경 및 최종 19:30:10 | 두 Pod 모두 `EXITED`, `runtime=null`, 각 호스트 `gpuAvailable=0` 확인. 추가 재가동 시도도 같은 GPU 부족으로 거절 |

실제 sourceRevision은 `b4057a008ad45c89f540e4cb6e19b6f29cd53aed`, overlay SHA는 `f2eac20f3f7a4f5543e19fa175d4d845f6478503c475179ea9cdc87c77c9c4ad`, 모델 revision은 `a6cdfd3464d1b767259cad23e164eaf39d3e3960`이다. 새 볼륨의 `first-boot-health.json`에 비밀값 없는 첫 기동 근거를 보존했다.

**컨테이너가 배정된 뒤 기존 boot가 실행 환경·설정·서버를 자동 복원하는 경로는 확인했다. GPU를 반환하는 실제 중지·재가동은 GPU 재할당 단계에서 실패했으므로 재가동 검증 통과로 표시하지 않는다.** 이전 전 A6000과 같은 VRAM의 A40에서 기존 Clova 모델이 적재됐지만, 실제 음성·영상 동시 처리 peak·처리 속도·피드백 품질은 개발자 QA가 필요하다.

AWS 인계 대상 후보는 `https://s8hb5b0l5k4qlx-8080.proxy.runpod.net`이다. 위 시각에는 실제 HTTPS 연결을 확인했지만 **현재 Pod가 중지돼 사용 가능한 upstream으로 전환하면 안 된다.** 재가동 성공과 readiness를 새 시각으로 확인한 뒤 Host·SNI를 `s8hb5b0l5k4qlx-8080.proxy.runpod.net`으로 전달한다. PC `.env`의 base URL은 이미 `https://ai.voice-coaching.site`이며 유지했다. AWS/Nginx 변경은 수행하지 않았다.

canonical 녹음 저장소·readiness 객체 등 기존 필수 설정은 여전히 미주입이며 일반 사용자 분석 접수를 열지 않는다. 이번 작업에서 AWS callback 인증을 재검증하지 않았으므로 과거 401을 현재 조회 결과로 보고하지 않는다.

## 재가동 가용성의 한계

이번 조건에서 A40 가용 지역인 EU-SE-1·CA-MTL-1은 API가 network storage 미지원으로 보고했다. 가격 조건을 지키기 위해 새 120GB Pod 볼륨을 사용했다. 중지 시 GPU가 반환되므로 다른 GPU 종류로 옮겨도 같은 호스트의 재확보 실패 가능성은 남는다. 자동 boot는 **GPU/컨테이너가 배정된 뒤 서버를 복원**하며 GPU 확보 자체를 보장하지 않는다.

지속적인 호스트 이동에는 네트워크 볼륨 지원 지역 또는 별도 검증된 복원 저장소가 필요하다. 기존 B2 원본·백업의 최신성·접근 자격을 이번 작업에서 확인하지 않았고, 일반 overlay만으로 전체 모델·환경 복원이 된다고 가정하지 않는다. [RunPod 가용성 설명](https://docs.runpod.io/pods/troubleshooting/zero-gpus), [스토리지 설명](https://docs.runpod.io/storage/network-volumes)

19:25~19:26 KST 추가 조사에서 GPU 1개·RAM 50GB 이상·vCPU 8 이상·CUDA 13.0/13.1 조건을 적용했다. 네트워크 볼륨 지원 18개 지역에는 A40·RTX 6000 Ada 가용 응답이 없었고, L40S는 US-TX-3에서 $1.09/시간·Low stock으로 조회됐다. 일반 network volume 120GB는 공식 $0.07/GB/월 기준 $8.40/월이며 기존 보존 Pod의 볼륨 비용은 별도다. 이는 견적·조회이며 자원 확보를 보장하지 않는다. 기존 가격을 유지하며 A40 재할당을 기다리는 방안과, 가격 조건을 변경하여 L40S·네트워크 볼륨을 새로 만들고 재이전하는 방안을 사용자에게 제시했다. 후자의 생성·이전은 아직 수행하지 않았다.

Pod·볼륨 삭제, AWS/Nginx 변경, Git commit/push/PR는 수행하지 않았다. 기존 분석 코드·모델 정책 변경이나 자동 회귀 테스트·추론 품질 QA도 수행하지 않았다. 두 Pod는 현재 중지 상태로 GPU 실행 요금은 중단되지만 보존 볼륨 요금은 계속 발생한다.
