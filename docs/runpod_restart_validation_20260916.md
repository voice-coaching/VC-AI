# RunPod 재시작 및 testvideo 검증 시도

2026-09-16 01:03~01:05 KST, 대상 `urwnuesv7o4fv6`.

사용자가 재시작 자동 배포 및 testvideo의 음성·입술·Clova 실제 실행 검증과 답변 확인을 요청했다.

## 확인한 범위

- 앞선 40GB PATCH로 컨테이너가 `9558d7e35e69`로 재생성되고 SSH 포트가 22112로 바뀌었다.
- 실제 `/` 파일시스템 40G, 사용 8.7G, 여유 32G를 확인했다.
- boot 아래 prepare Python 및 rsync가 실행되어 자동 환경 복원이 진행 중이었다. 이 시점에는 deployment.json, services.json 및 8000/8080 리스너가 없었다. 전체 배포 완료로 판정하지 않는다.
- GPU 할당은 0개, 호스트 가용 GPU는 0개였다. RUNNING 상태에서 GPU 1개 resume 요청은 GPU 0개를 유지했다.
- 명시적 재시작 요청 범위에서 podStop을 수행하고 EXITED 응답을 확인했다. 이어 gpuCount=1로 podResume을 요청했으나 `There are not enough free GPUs on the host machine to start this pod.` 오류로 거절됐다.

## 미완료

GPU 재할당 실패로 모델 적재, Clova health, 전체 서비스 readiness, testvideo 음성·입술·Clova 실행은 완료하지 못했다. 이번 실행에서 새 Clova 답변은 생성되지 않았다. 과거 영상 결과를 이번 결과로 재사용하지 않았다.

새 Pod 생성·이전, A40 기동, 모델 정책 변경 또는 비밀 설정 변경은 수행하지 않았다. 이후 GPU 재시작이 성공하면 자동 복원 완료·revision/hash·각 서비스 상태를 확인한 후 실제 영상을 처리해야 한다.

## 후속 요청: A40에서 실행 시도

사용자가 A40에서 같은 재시작·testvideo·Clova 검증을 요청하여 대상 `s8hb5b0l5k4qlx`를 조회했다. A40, EXITED, runtime=null, container disk 40GB, volume 120GB, 호스트 가용 GPU 0개였다.

`podResume(gpuCount:1)`을 실제 요청했으나 `There are not enough free GPUs on the host machine to start this pod.`로 거절됐다. 최종 재조회에서도 EXITED, runtime=null, 가용 GPU 0개를 확인했다. 이 후속 요청에서는 A40 기동을 시도했지만 성공하지 못했다.

A40의 새 자동 배포·testvideo 처리·Clova 생성은 실행되지 않았다. 새 답변은 없으며 과거 답변을 현재 결과로 제시하지 않았다. 다른 Pod 생성이나 이전은 수행하지 않았다.
