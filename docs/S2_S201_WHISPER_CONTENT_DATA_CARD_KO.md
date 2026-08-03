# S2 S201 Whisper 내용 일치 hard gate 데이터·모델 카드

최초 작성: 2026-07-29 KST  
상태: **정적 실행 준비 계약만 구현 / 실제 데이터·모델·다운로드·추론·지표 0**

## 1. 목적과 주장 한계

S201의 유일한 역할은 한국어 음성에서 생성한 transcript와 고정 prompt를
비교해 내용 불일치 가능성을 먼저 거르는 것이다. S201은 S2의 독립
음향 감사기 전체가 아니며, 세밀한 phone 오류, duration, pause, F0,
운율, 자연스러움 또는 이해가능성을 판정하지 않는다.

현재 구현은 config와 read-only preflight뿐이다. Whisper production
runner, checkpoint, audio/transcript 행, WER/CER, content mismatch
prediction과 결과 commit은 없다. 따라서 허용되는 주장은 “S201 정적
실행 준비 계약을 검증했다”뿐이다. S201 완료, WER gate 통과, S2 독립성의
경험적 입증 또는 배포 준비를 주장할 수 없다.

## 2. 선행자와 실행 순서

```text
S108 final research_candidate_passed commit + completed registry
  -> S108 등록 artifact 전수 hash 검증
  -> S200 readiness lock과 S108 binding 재검증
  -> WER/CER/content-mismatch threshold preregistration 동결
  -> 자료별 license·partition attestation
  -> S2 expert content transcript attestation
  -> local-only Whisper bundle·processor hash 검증
  -> manifest hash만 검증(행은 열지 않음)
  -> 별도 검토된 production runner 구현
```

현재 S108 final commit과 S200 readiness lock이 없으므로 full preflight는
첫 선행자 확인에서 실패해야 한다. 이때 threshold attestation, 외부자료,
모델, data manifest와 output 경로는 해석하지 않는다.

## 3. 모델 식별과 로컬 전용 정책

| 항목 | 고정값 |
|---|---|
| checkpoint | `openai/whisper-large-v3` |
| Hugging Face revision | `06f233fe06e710322aca913c1bc4249a0d71fce1` |
| 공식 Whisper source revision | `90fdc51112dc59242201d038a48add97ffd24b5d` |
| architecture | `WhisperForConditionalGeneration` |
| weight format | safetensors만 허용(단일/분할 여부는 local attestation에 기록) |
| processor | 같은 model ID와 같은 exact revision의 `WhisperProcessor` |
| feature extractor | `WhisperFeatureExtractor`, 16 kHz, 128 mel, hop 160, FFT 400, 30초 |
| tokenizer | `WhisperTokenizerFast` |
| license | MIT, 실제 bundle 확보 시 파일·license 재감사 |

`local_files_only=true`, `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`을 요구한다. 네트워크 fallback과 자동 download는
금지한다. 현재 로컬 bundle attestation은
`TBD_LOCAL_BUNDLE`이므로 실행은 금지된다. Revision은 원격 ref metadata만
확인했으며 model weight나 processor 파일을 다운로드하지 않았다.

## 4. 한국어 transcript 정규화

Reference와 hypothesis에 다음 순서를 똑같이 적용한다.

1. strict UTF-8 decode
2. Unicode NFC
3. Unicode casefold
4. Unicode General Category가 `P*`인 문장부호를 ASCII space로 교체
5. 모든 Unicode whitespace를 ASCII space로 교체
6. 연속 ASCII space 축약
7. 양끝 ASCII space 제거

추가 숫자 읽기 변환, 형태소 분석, 발음형 rewrite, 맞춤법 교정은 하지
않는다. 이 선택은 같은 surface text를 결정론적으로 비교하기 위한
engineering policy이며 한국어 내용 동일성의 언어학적 완전성을 뜻하지
않는다.

- WER 단위: ASCII space로 분리한 token
- CER 단위: space를 제외한 Unicode codepoint
- 빈 reference: 해당 item을 무조건 invalid로 처리
- 원문 transcript의 A1/C1 반출: 금지

## 5. 언어 고정·decoding·결정론

- 언어 `ko`, language token `<|ko|>`, task `transcribe`
- language detection과 translation 금지
- `do_sample=false`, `temperature=0`, beam 1, 반환 sequence 1
- fallback temperature는 `[0.0]` 하나뿐이며 sampling fallback 금지
- previous-token conditioning 금지
- timestamp 반환과 timestamp feature 사용 금지
- `max_new_tokens=448`
- seed `20260729`
- PyTorch deterministic algorithms, deterministic cuDNN, TF32 off
- `CUBLAS_WORKSPACE_CONFIG=:4096:8`
- transformers `5.14.1`, torch `2.12.1+cu130`, tokenizers `0.22.2`,
  safetensors `0.8.0`

실제 local bundle attestation은 model config, generation config,
preprocessor config, tokenizer와 모든 weight shard의 경로·크기·SHA-256을
포함해야 한다. Preflight는 그것을 전수 hash 검증하되 추론하지 않는다.

## 6. Hard gate와 미해결 preregistration

WER와 CER의 정의는 고정했지만 통과 임계값과 두 지표를 content mismatch로
결합하는 규칙은 근거 문서에 없다. 따라서 다음 세 값은 모두
`TBD_PREREGISTRATION`이다.

| 항목 | 현재 상태 |
|---|---|
| item WER maximum | `TBD_PREREGISTRATION` |
| item CER maximum | `TBD_PREREGISTRATION` |
| content mismatch 결합 규칙·threshold | `TBD_PREREGISTRATION` |

수치를 임의로 채우거나 FLEURS/ZEROTH 결과를 본 뒤 선택할 수 없다.
별도 S2 content pilot/protocol 근거, 연구책임자 승인, 결과 미열람,
data/model path 미해석을 증명하는 immutable preregistration이 먼저
필요하다. 미해결 상태에서는 preflight와 production 실행 모두
fail-closed한다.

Gate는 S202보다 먼저 적용하고 bypass하지 않는다. Content gate 실패
표본을 S202의 acoustic score로 구제할 수 없다. 반대로 S201 통과가
phone·운율 품질 통과를 뜻하지도 않는다.

## 7. 자료 역할 분리

### S2 expert content transcript

별도 S2 전문가 자료는 한국어 L2 발화의 canonical prompt와 독립 content
reference/evaluation에만 사용한다. S1 labeler·split·예측·threshold와
독립이어야 한다. Phone·prosody label은 S201에 입력하지 않는다.
기관 승인, 동의·철회, 보존·폐기, item/speaker/rater 분리와 manifest
hash를 row-free attestation으로 먼저 고정해야 한다.

### FLEURS ko-KR와 ZEROTH-Korean

두 자료는 CC-BY-4.0 attribution과 원래 partition을 보존한 원어민 ASR
외부평가 전용이다.

- S2 expert transcript의 대체 자료로 사용 금지
- 학습자 오류 label 또는 S202 감독 label로 사용 금지
- S201 threshold 선택 자료로 사용 금지
- license·attribution·partition attestation 전 row/audio/transcript 접근 금지

## 8. 입력·누수·privacy

허용 입력은 다음 다섯 개뿐이다.

- `item_id_sha256`
- `speaker_id_sha256`
- `audio_waveform`
- `canonical_prompt_text`
- `language_code`

S1 score/logit/probability/confidence/threshold/prediction/label/embedding,
explicit feature, realized phone, 오류 tag/count, phone·word interval,
oracle/predicted alignment를 금지한다. Whisper timestamp도 model feature로
사용하지 않는다. Locked test와 S1의 train/dev/stack/calibration 자료는
S201 선택·threshold 설정에 사용할 수 없다.

원음과 transcript는 R0, locator는 R1, 가명 prediction은 D1, 집계 지표만
A1이다. 가명 SHA-256도 익명화가 아니며 raw transcript와 행 단위
prediction을 공개하지 않는다.

## 9. 미래 완료 산출물

S201 완료를 주장하려면 별도 production runner와 deep validator가 최소한
다음을 immutable commit으로 제공해야 한다.

1. exact S201 config
2. checkpoint source/model card와 local bundle hashes
3. transcript normalization policy
4. aggregate WER/CER/content mismatch report
5. privacy-safe content gate predictions
6. completed registry와 independent validation receipt

현재 산출물은 위 목록의 설계 template뿐이며 실제 artifact는 0개다.

## 10. 현재 blocker와 다음 행동

1. S108 final 단일 locked batch와 passed commit 완료
2. S200 full preflight와 readiness lock 발행
3. S2 content threshold preregistration을 사람 승인으로 동결
4. S2 expert content 자료의 승인·역할·분할 attestation 완료
5. FLEURS/ZEROTH license·attribution·partition attestation 완료
6. exact Whisper bundle을 승인된 방식으로 별도 확보하고 local manifest 감사
7. 이 readiness package와 별개로 production runner·deep validator 구현
8. 실제 실행 뒤에만 논문 amendment template의 PENDING 값을 채움
