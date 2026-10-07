# 실제 참조 영상 등록 절차

2026-10-07. 추출·등록 도구 구현. 실제 영상 추출·전문가 구간 검수·보정 및 운영 등록은 미수행.

## 입력과 근거

사용자 지정 원본은 `tests/testvideo1.mp4`, SHA-256 `e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9`다. 사용자가 전문가 확인 영상으로 제공했지만, 프레임 품질과 각 MFA 구간의 적합성은 아직 확인하지 않았다. 대본 후보는 `쌀과 콩을 깨끗한 그릇에 담아요`이며 실제 영상과 일치하는지 담당자가 확인한다. 한 영상이 모든 한국어 음소·단어 문맥의 정상 기준이나 점수 보정을 제공하지 않는다.

Backend의 동일 preparation에서 얻은 canonical MP4, 16kHz mono PCM 및 `MEDIA_RECEIPT`가 필요하다. v2 core의 실제 실행 결과 JSON과 그 실행이 보존한 `prepared_cache/<requestId>.wav`도 필요하다. 이전 core의 자음 JSON, CTC frame 시간 또는 다른 녹음 결과로 대신 채우지 않는다. 이 절차는 운영 사용자 요청에 추출을 반복하거나 GPT를 추가 호출하지 않는다.

## 개발자 추출

`AGENTS.md`의 개발자 직접 QA 규칙에 따라 개발자가 Linux/DLPC에서 AI PR #35의 checkout을 기준으로 실행한다. 다음 인수의 경로는 실제 준비·실행 산출물 경로로 지정한다. 도구가 준비하거나 다운로드하는 모델은 없다. 기존 검증 Python, pinned FaceLandmarker 자산, ffmpeg/ffprobe와 seccomp가 필요하다.

```bash
python deploy/dlpc/prepare_lip_reference.py capture \
  --core-root /path/to/canonical_dlpc_audiovisual_core_20261007_v2 \
  --llm-root /path/to/canonical_dlpc_audiovisual_20261007_v2_h5 \
  --core-result /path/to/results/REQUEST_ID.json \
  --media-receipt /path/to/media-receipt.json \
  --source-pcm /path/to/canonical.wav \
  --prepared-pcm /path/to/prepared_cache/REQUEST_ID.wav \
  --video /path/to/canonical.mp4 \
  --source-sha256 e11d1d9ac6a04de67bdff5ddda68284d92d910d343bbf28dc764f2879744eac9 \
  --visual-python /path/to/visual-env/bin/python \
  --model /path/to/face_landmarker.task \
  --output /private/reference-capture.json \
  --review-output /private/reference-review.json
```

실제 core/H5 소스 identity, core JSON schema, receipt 및 파일 SHA를 확인한다. PCM sample hash/길이 일치와 연속 MP4 음성 PTS가 확인될 때만 출력한다. 출력은 입술 정규화 좌표·실측 PTS·MFA index/구간이며 RGB·전체 얼굴 mesh·비밀값·파일 경로를 포함하지 않는다.

## 전문가 검수 및 catalog 작성

생성된 review의 `expertReviewId=PENDING`, `approvedExpectedIndices=[]`는 승인 상태가 아니다. 전문가는 원본 영상과 capture의 실제 시간 구간을 대조해 보이는 발음 동작·동기화·MFA 위치가 참조로 적절한 음소만 선택한다. 검수 기록 ID와 해당 index를 직접 등록한다. 파일 digest가 달라지면 review를 재사용할 수 없다. 관측 불가·3프레임 미만·120프레임 초과 구간은 선택해도 등록되지 않는다.

```bash
python deploy/dlpc/prepare_lip_reference.py compile \
  --capture /private/reference-capture.json \
  --review /private/reference-review.json \
  --base-catalog deploy/dlpc/audiovisual/reference-catalog.json \
  --revision expert-reviewed-reference-v1 \
  --model-sha256 64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff \
  --output /private/reference-catalog.json
```

새 catalog는 승인된 실제 궤적만 추가한다. 원본 SHA, core SHA, 준비 WAV SHA, capture SHA, expected index, 음성·영상 시간을 보존한다. 같은 expected phone/role/정확한 단어 context끼리만 비교하며 같은 원본의 재인코딩을 포함한 자기 비교는 차단한다. calibration은 자동 생성하지 않는다. 거리 비교가 가능해도 `CALIBRATION_MISSING`, 점수 null이 정상이다.

이후 검수된 catalog를 배포 자산으로 반영해 별도 immutable H5를 만들고 AI·Backend의 tuple 및 공통 schema digest를 함께 등록한다. 이 도구는 Git, B2, DB, 설정, 실행 허용 목록, 서비스 및 admission을 변경하지 않는다. 실제 추출/검수와 별도 보정 데이터가 준비되기 전 현재 PR의 catalog는 비어 있다.
