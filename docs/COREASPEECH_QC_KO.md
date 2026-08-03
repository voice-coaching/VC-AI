# CoreaSpeech 고정 스냅샷 QC

상태: **현재 공개 revision의 1개 shard 다운로드·표본 header 검증 완료 /
전체 shard header 감사 대기**

## 경계

CoreaSpeech는 현재 manifest에서 연구 전용·비상업·재배포 금지·2차 수정
금지로 고정한다. 공개 revision에서 삭제된 과거 shard를 복원하거나
우회 다운로드하지 않는다. 현재 파일은 `qc_only`이며 학습, checkpoint
승격, 생성 음성 공개의 허가가 아니다.

고정 계약은 `data/manifests/coreaspeech-smoke-v1.yaml`에 있다.

- revision: `950d6a50b2b3e338d141313d11440fe47b037745`
- 현재 공개 shard: 35개
- 내려받은 shard: `train-00001-of-00047.parquet`
- byte: `4,754,132,131`
- SHA-256:
  `7ab69f26a19ff017deb9b697c4c175a223ef231034ca14639ac31e437e076c3b`
- metadata: 3,592행, 20화자, 15.506219시간

## 감사기

`scripts/audit_coreaspeech_snapshot.py`는 다음만 수행한다.

1. manifest의 연구·권한 경계가 완화되지 않았는지 확인한다.
2. revision directory와 shard가 symlink나 경로 탈출이 아닌지 확인한다.
3. byte 수와 SHA-256을 다시 계산한다.
4. 모든 embedded audio의 header를 read-only로 열어 sample rate, channel,
   format, frame-duration 일치를 집계한다.
5. 원문 전사, 발음열, 화자 값, audio bytes와 로컬 경로를 report에
   기록하지 않는다.
6. audio를 sample tensor로 decode하거나 별도 파일로 materialize하지
   않는다.
7. 예상하지 못한 I/O·parser 예외도 원문 오류 문자열 대신 고정 오류
   코드만 출력해 임시 파일명과 로컬 경로를 노출하지 않는다.

Parquet 내부 `audio.path` 값은 audio bytes와 함께 존재하지만, 감사기는
그 값을 사용하거나 report로 내보내지 않고 존재 건수만 집계한다.

전체 shard를 검사하는 정본 명령은 다음과 같다. S104 GPU 실험이
종료된 뒤 낮은 I/O 우선순위로 실행한다.

```bash
CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  nice -n 10 ionice -c2 -n7 \
  .venv-scorer/bin/python scripts/audit_coreaspeech_snapshot.py \
  --output artifacts/data/coreaspeech/950d6a50/header_qc.json
```

`--row-limit`을 지정한 실행은 항상 sampled smoke로 남으며 full QC로
승격되지 않는다. Full QC가 통과해도 license/output-lineage 검토,
speaker-disjoint split, text·pronunciation conditioning 감사 전에는
학습에 사용하지 않는다.
