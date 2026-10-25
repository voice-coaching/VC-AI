# GPT-6.1 Sol light 전환·실제 API 시간 측정

사용자 요청에 따라 문장연습 direct의 설명·채점 모델을 **gpt-6.1-sol / low / default**로
바꾸고 같은 음성·문장으로 실제 API를 2회 호출했다. 전환 직전 Luna도 2회 측정했다.
관측은 **2026-10-25 16:25–16:40 UTC**이며 문서·브랜치의 20261026은 작업 식별자다.
이 문서는 [파이프라인 구현](pipeline-latency-implementation-20261025.md)의 후속 운영 관측이다.

이번 두 표본에서 Sol의 GPT HTTP 평균은 **7.534초**, Luna는 **4.035초**로 Sol이
**86.7% 길었다**. 속도 개선은 확인되지 않았다. 요청한 Sol 설정은 운영에 유지했다.
Luna의 두 번째 전체 시간에는 다른 분석에 의한 큐 대기가 포함되므로 전체 시간만으로
모델 속도를 비교하지 않는다. 표본 수·회차·MFA cache 상태가 달라 일반 성능·p95·품질
우위를 결론 내릴 수 없다. Luna none과 Sol low라는 설정 조합의 비교다.

## 설정·변경 범위

OpenAI 공식 문서는 GPT-6.1 Sol이 `low`를 지원하고 `none`·`minimal`을 지원하지 않으며,
도구 없는 Chat Completions를 지원한다고 명시한다. 기존 endpoint와 요청 구조를 유지했다.
[GPT-6.1 Sol 모델 문서](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
사용자의 light 표현은 `reasoning_effort=low`로 적용했다.
[Light/Low 설정 안내](https://learn.chatgpt.com/docs/models)
현재 운영 키의 모델 조회는 200 / gpt-6.1-sol이었다. 키·인증 header는 기록하지 않았다.

새 브랜치는 main `ee8eefb377226d42b59b05d1b06950b050ca1b5e`에서 만든
`perf/pipeline-timing-20261026`이다. 기존 미커밋 변경과 251개 파일 삭제를 유지했다.
이번에는 모델 H5·허용 identity·복구 설정과 이 관측 문서를 변경했다.

- [builder](../../deploy/dlpc/build_sol61_light_h5.py)는 기존 H5의
  `contracts/llm/request_config.json`에서 model과 reasoning_effort만 변경한다.
  H5 payload 7개 중 이 파일 하나만 달라졌고 prompt·rubric·adapter는 동일하다.
- [harness](../../src/voice_coach/backend_analysis/canonical_harness.py),
  [Native 허용 조합](../../src/voice_coach/backend_analysis/native_execution.py),
  [transport](../../src/voice_coach/backend_analysis/canonical_transport.py),
  [private 결과 schema](../contracts/runpod_result_v4.schema.json)에 정확한 새 조합을 등록했다.
  기존 model identity를 새 값으로 덮어쓰지 않았다.
- [복구 helper](../../deploy/dlpc/restore_sol61_model.py)와
  [기동 hook](../../deploy/dlpc/recover_on_start.py)에 새 selector를 연결했다.
  이전 pipeline payload를 전제로 전체 runtime 17개를 정합하게 복구하고, 원복 시 바뀐
  4개 파일·direct H5·selector를 직전 Luna 조합으로 돌린다. 원복 자체는 실행하지 않았다.

기존 prepared Core, 단일 실행 슬롯, 3회 worker 수명, PCM 경로·lexicon cache·GOP 병렬화,
짧은 두 문장 피드백·점수 rubric·공개 결과와 3,000 completion token 상한을 유지했다.
Fast/Ultrafast는 사용하지 않았다. 내부 canonical의 H5와 AWS Backend/nginx는 바꾸지 않았다.

| 항목 | 고정 identity |
|---|---|
| Core | `9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba` |
| 새 H5 | `8abd31b94aa245617c8a1caf1218e2dae62fdd19058f83a2876abd0a27e135a9` |
| 새 H5 dependency lock | `1903c5339773f301a1a26ba814e970987b3649a88fb3579c11ec41de30a16893` |
| 유지한 prompt | `fdd98e37b076c3b36058a09bc40667bcd2cd1d9d3a9d31a7106b5caa7df8ca7a` |

설치 H5는 `/workspace/intelligentai/validation/canonical_dlpc_pipeline_sol61_light_20261026_v1_h5`다.
새 helper의 runtime SHA 목록과 root 소유 private payload가 설치·복구의 정본이다.

## 입력·호출과 구간 정의

입력은 사용자 제공 `intelligentAI/tests/voice/testaudio.m4a`, 문장은
**쌀과 콩을 깨끗한 그릇에 담아요**다. 원본 m4a의 SHA를 확인한 뒤 직전 정상 job에
보관된 동일 녹음의 PCM WAV bytes를 읽어 제출했다. 재변환에 의한 차이는 없다.
원본 m4a·기존 mp3는 수정하지 않았고 임시 파일도 만들지 않았다.

- 원본 m4a SHA: `c1c2249e067b093f6728a71ddd71fb797cdb8243deeb3673b09583ea212536f1`
- PCM SHA: `71dd0b1bd6929f0d921a630725c14f6f047cc27dcb978ac4c38b6d0a08770a1d`
- PCM: 136,612 bytes, mono s16le 16kHz, 68,267 frames / 4.2666875초.
- 문장 SHA: `b9b4cab47fd1c9dafdd34167a478290a93e22b3fb2669e82907327aa45d9c3af`

공개 `https://ai.voice-coaching.site/direct/api/v1/analyses`에 서로 다른 clientAttemptId로
모델별 2회 순차 POST했다. 네 요청 모두 202로 접수됐고 자동 POST 재시도는 없었다.
약 1초 간격 GET으로 terminal을 확인했다. 클라이언트 전체에는 업로드·네트워크·polling이
포함된다. 서버 전체는 createdAt→resultReadyAt, 큐는 createdAt→startedAt, 실행은
startedAt→resultReadyAt이다. MFA·GPT는 각각 Core span·실제 transport 관측이다.
이 측정 클라이언트는 DLPC에서 AWS 공개 endpoint를 거쳤으므로 일반 사용자 단말의
인터넷 구간을 측정한 것은 아니다. AWS nginx 내부 시간을 별도로 계측하지 않았다.

| 설정·회차 | POST UTC | jobId | MFA ordinal | FST cache |
|---|---|---|---:|---|
| Luna none 첫 요청 | 16:25:04.980054 | `8419e6e7-786e-4179-a77b-46da16ba9091` | 1 | miss |
| Luna none 재사용 | 16:25:21.897093 | `7b102969-a0c2-4783-bcf5-459769129c3d` | 3 | miss |
| Sol low 첫 요청 | 16:38:31.068426 | `c40f56f7-e050-4aa1-aaa0-cde8ea7ee47d` | 1 | miss |
| Sol low 재사용 | 16:38:50.898397 | `c354c1bc-65f8-4686-a720-3080da17e42c` | 2 | hit |

Luna 첫 요청 실행 중 다른 실제 요청 한 건이 접수됐고, 그 요청이 실행 슬롯을
16:25:21.149–16:25:55.529 UTC에 사용했다. 이 때문에 두 번째 측정의 큐가 33.576초였고
MFA 회차도 3이었다. 다른 요청의 문장·음성은 읽거나 문서에 기록하지 않았다.

## 측정 결과

아래 단위는 초다. MFA는 Core, GPT는 expression 안에 포함되므로 행을 모두 합산하지 않는다.

| 구간 | Luna 첫 요청 | Luna 재사용 | Sol 첫 요청 | Sol 재사용 |
|---|---:|---:|---:|---:|
| 클라이언트 전체 | 16.916788 | 46.807698 | 19.829740 | 14.613995 |
| 서버 전체 | 15.850811 | 45.709414 | 19.115272 | 14.222071 |
| 큐 대기 | 0.020719 | 33.575823 | 0.106189 | 0.104987 |
| 큐 제외 실행 | 15.830092 | 12.133591 | 19.009082 | 14.117084 |
| 실행 선택→준비된 Python worker | 0.006370 | 0.003277 | 0.006120 | 0.005192 |
| Core producer | 11.086745 | 7.877134 | 10.744905 | 6.309174 |
| MFA | 9.660936 | 7.151357 | 9.215141 | 5.536848 |
| expression 전체 | 4.409000 | 3.945212 | 7.931767 | 7.479225 |
| GPT HTTP | 4.296162 | 3.774481 | 7.764192 | 7.304488 |
| 결과 preflight | 0.292316 | 0.274451 | 0.292053 | 0.266769 |

실행 worker를 매번 새로 만들던 직전 관측 1.262초와 달리 선택→worker 구간은 이번 네 건에서
0.003–0.006초였다. 다만 전체 지연은 큐·MFA miss·provider 변동에 계속 영향을 받는다.

### GPT 상세

네 건 모두 요청·반환 모델이 각각 Luna 또는 Sol로 일치했고 actual tier=default,
finishReason=stop, dispatchAttempts=1이었다. 단순 readiness만으로 모델 접근을 추정한 결과가 아니다.

| 항목 | Luna 첫 요청 | Luna 재사용 | Sol 첫 요청 | Sol 재사용 |
|---|---:|---:|---:|---:|
| request bytes | 30,731 | 30,731 | 30,731 | 30,731 |
| input tokens | 8,366 | 8,366 | 8,366 | 8,366 |
| cached tokens | 0 | 2,770 | 0 | 2,770 |
| completion tokens | 79 | 83 | 169 | 167 |
| 그중 reasoning tokens | 0 | 0 | 67 | 66 |
| HTTPS 연결 재사용 | 아니오 | 예 | 아니오 | 예 |
| connect 관측 초 | 0.061636 | 약 0 | 0.070681 | 약 0 |
| HTTP headers까지 초 | 4.295720 | 3.774026 | 7.763691 | 7.304005 |
| 응답 body 읽기 초 | 0.000182 | 0.000143 | 0.000159 | 0.000161 |

Sol의 input·cache 조건은 Luna와 같은 회차별 값이었다. 재사용 요청의 GPT HTTP도
3.774→7.304초로 **93.5% 증가**했다. 모델과 추론 설정에 따른 관측이며 네트워크·provider
변동을 제거한 통제 비교는 아니다. connection 준비·body 수신은 관측된 수초 대기의 주된
비중이 아니었다. 비스트리밍 headers 대기에는 provider 대기·prefill·추론·출력 생성이
겹쳐 있고 각각의 시간은 공개 응답에서 분리되지 않는다. 토큰 수로 순수 추론 시간을 만들지 않는다.

### MFA 상세

Sol 두 요청은 같은 새 resident의 1·2회차이며 두 번째에 FST cache hit가 실제 확인됐다.
첫 요청의 초기 feature 비용과 cache miss도 별도로 보아야 한다. 모델 교체가 MFA를
가속했다고 해석하지 않는다.

| MFA 구간 | Sol 첫 요청 초 | Sol 재사용 초 |
|---|---:|---:|
| 전체 MFA | 9.215141 | 5.536848 |
| setup, 아래 준비 단계 포함 | 7.534187 | 4.178938 |
| dictionary_setup, 첫 disambiguation 포함 | 1.324739 | 1.275905 |
| normalize_text | 1.507060 | 1.214812 |
| disambiguation 2회 합계 | 0.840336 | 1.171655 |
| build_lexicon_compiler | 2.074280 | 0.638470 |
| 그중 FST miss compile / hit copy | 1.227873 | 0.057965 |
| generate_features | 1.735129 | 0.131503 |
| graph compile | 0.062028 | 0.068936 |
| align | 0.106505 | 0.130103 |

이번 hit에는 이전 대표 재사용 관측의 MFA 6.304초보다 12.2% 짧은 5.537초가 나왔다.
반면 Luna의 재사용 요청은 miss로 7.151초였다. 동일 Core에서도 miss·입력·회차에 따른
차이가 있으며 다른 음성·연속 부하의 cache hit율이나 장기 메모리 사용은 확인하지 않았다.

## 실제 검증·운영 상태·남은 작업

- 변경 Python 6개 구문·JSON 1개 parse, 핵심 runtime·builder·복구 helper 5개에
  mypy `--check-untyped-defs --follow-imports=skip --ignore-missing-imports --python-version 3.12
  --cache-dir=/dev/null --no-incremental` 통과.
- 실제 canonical Python·UID 10001에서 Native 허용·전체 SHA·schema·동결 H5 import·
  transport 설정 사전 확인 통과. 운영 runtime 17개·복구 helper 2개의 저장소/설치 SHA 일치.
  새 H5의 payload 7개 전체 SHA, Python 1개 구문·JSON 5개 parse 통과.
- 변경 문서 3개의 상대 링크 60개·충돌 표식 확인과 `git diff --check` 통과.
- 네 실제 요청 모두 RESULT_READY, Core ok, SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID,
  실패 없음·GPT dispatch 1회. MFA trace complete, DB close·lifecycle PASS.
  네 요청의 archiveStatus와 historySyncStatus도 최종 COMPLETE였다.
- 활성 작업 0을 두 번 확인한 뒤 소유 hold 아래에서 분석만 교체했다. 새 분석 PID 798773의
  실제 자식 Native/MFA 준비와 새 H5의 실행 worker READY를 확인했다. TTS는 기존 PID
  460835를 유지했다. 독립 TTS 합성은 호출하지 않았다.
- **16:40:49 UTC** local·공개 direct ready 모두 200/true, 활성·direct 작업 수 0,
  local handoff executorConfigured=true/admissionEnabled=false, 소유 hold 없음,
  새 selector=light였다. `/tmp` 여유는 1,212,243,968 bytes였다.

실제 호출은 이번 요청으로 허용된 네 건뿐이며 단위·회귀·화면 테스트, fixture·mock·상시
측정 도구를 만들거나 실행하지 않았다. 저장소 밖 임시 검증 파일도 만들지 않았다.
이 성공은 피드백 품질의 사람 검토를 대신하지 않는다. 원본·영속 job 자료는 정상 운영
기록으로 유지했다. commit·push·PR은 하지 않았다.

속도 우선 목표에는 이번 Sol low 전환이 유리하다는 근거가 없다. 추가 조정은 GPT 설정과
큐 대기·MFA 준비 비용을 구분해야 한다. P5 사전 DB/normalize 분리, P6/P7·동시성 확대는
이번 모델 변경·시간 측정 범위에서 수행하지 않았다. 기존 resident 타입 진단 3건도 그대로다.
