# WP5 S201 Whisper 내용 일치 hard gate production readiness

최초 작성: 2026-07-29 KST  
상태: **정적 preflight 구현 완료 / production runner·실제 실행·결과 0**

## 1. 결론

S201 전용 config, strict contract, read-only preflight, 데이터·모델 카드,
논문 amendment template과 bounded fault test를 구현했다. 현재 package는
S108 final passed commit과 S200 readiness lock을 가장 먼저 deep
validate하고, 둘 중 하나라도 없거나 변조됐으면 threshold, 외부자료,
S201 모델, manifest와 output 경로를 해석하기 전에 fail-closed한다.

Whisper의 역할은 한국어 transcript content consistency hard gate로만
제한한다. Phone·운율 판단, S1 prediction/label/feature/threshold,
oracle/predicted alignment, Whisper timestamp feature와 locked-test 접근을
금지한다.

현재 S108 final commit이 없으므로 실제 full preflight는 의도대로 첫
선행자에서 종료된다. 실제 데이터·모델·download·GPU·WER/CER·prediction·
result·output은 모두 0이다. 따라서 S201 완료나 WP5 배점 근거가 아니다.

## 2. S201 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/s2_s201_whisper_content_gate_v1.yaml` | exact checkpoint/processor, 정규화, decoding, threshold·자료 역할, 선행자 순서 정본 |
| `src/voice_coach/training/s2_s201_whisper_contract.py` | strict YAML/JSON, S108/S200 deep validation, attestation·local bundle·manifest hash preflight |
| `scripts/prepare_s2_s201_whisper_content.py` | config-only/full fail-closed CLI |
| `tests/unit/test_s2_s201_whisper_contract.py` | 합성 전 경로와 tamper/symlink/order/TOCTOU fault test |
| `tests/integration/test_prepare_s2_s201_whisper_content.py` | 실제 workspace config-only·현재 full failure 검사 |
| `docs/S2_S201_WHISPER_CONTENT_DATA_CARD_KO.md` | 역할·privacy·license·normalization·blocker 데이터/모델 카드 |
| `docs/paper/S2_S201_WHISPER_CONTENT_AMENDMENT_TEMPLATE_KO.md` | 실제 commit 전에는 승격할 수 없는 논문 PENDING 표·문장 |

기존 tracker, 정본 원고, S104–S200, C01 파일은 수정하지 않았다.
Production inference/training runner도 만들지 않았다.

## 3. 고정한 모델·decoding

- model: `openai/whisper-large-v3`
- exact revision:
  `06f233fe06e710322aca913c1bc4249a0d71fce1`
- official Whisper source revision:
  `90fdc51112dc59242201d038a48add97ffd24b5d`
- 같은 model ID/revision의 `WhisperProcessor`,
  `WhisperFeatureExtractor`, `WhisperTokenizerFast`
- 16 kHz, 128 mel, hop 160, FFT 400, 30초
- 한국어 `ko`, `<|ko|>`, `transcribe` 강제
- language detection·translation·sampling·timestamp feature 금지
- temperature 0, beam 1, one sequence, previous-token conditioning 금지
- local-files-only와 offline 환경 강제; network fallback·자동 download 금지
- safetensors만 허용; pickle-capable weight 금지
- 실제 local bundle은 모든 weight/config/processor/tokenizer hash와
  MIT license hash를 attestation해야 함

Revision은 ref metadata만 확인했으며 model/processor artifact는 받지
않았다. 현재 `TBD_LOCAL_BUNDLE`이므로 실행은 금지된다.

## 4. 한국어 정규화와 hard gate

Reference와 hypothesis에 strict UTF-8 → NFC → casefold → Unicode
punctuation을 space로 교체 → whitespace 정규화·축약·trim 순서를 동일하게
적용한다. 숫자 확장, 형태소 분석, 발음형 rewrite와 맞춤법 교정은 하지
않는다. WER는 space token, CER은 space 제외 Unicode codepoint 기준이고
빈 reference는 fail-closed invalid다.

정본 문서에는 WER/CER/content-mismatch 임계값의 수치 근거가 없다. 따라서
세 값과 결합 규칙은 모두 `TBD_PREREGISTRATION`이다. Native external
benchmark 결과를 본 뒤 threshold를 정하는 것도 금지했다. Frozen
preregistration은 S108 commit·S200 lock에 hash-binding되고, 결과 미열람과
data/model path 미해석을 증명해야 한다.

## 5. 자료 역할과 접근 순서

S2 expert content transcript는 별도 L2 content reference/evaluation이다.
S1의 다섯 split, speaker와 labeler cohort overlap이 모두 0이고
기관 승인·동의·철회·보존/폐기·제한 재배포가 완료된 aggregate
attestation만 허용한다.

FLEURS ko-KR와 ZEROTH-Korean은 CC-BY-4.0 attribution, license audit와
official partition name/hash 보존 뒤 외부 ASR 평가에만 사용한다. 두
자료를 학습자 오류 label, S2 expert transcript 대체물 또는 threshold
선택 자료로 사용할 수 없다. 자료마다 서로 다른 role-specific manifest
root를 고정해 attestation이 다른 data zone으로 read를 우회하지 못하게
했다.

Runtime 순서는 다음과 같다.

```text
C1 source pins
  -> S108 commit/registry + required artifact 8개 전수 hash
  -> S200 readiness lock + S108/config/source/data-card binding
  -> frozen threshold preregistration
  -> FLEURS/ZEROTH license·partition attestations
  -> S2 expert content attestation
  -> exact offline local model bundle
  -> manifest hash only
  -> 별도 production runner 구현 대기
```

## 6. 실제 검증 결과

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s201_whisper_content.py --config-only
```

결과: `valid_config_only`.

- C1 source pin read: 12
- predecessor/threshold/external/data/model/protected/S1/oracle/locked/GPU:
  모두 0
- metric/prediction/result/output path/output: 모두 0
- production runner/execution/completion claim: false

### 현재 full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_s2_s201_whisper_content.py
```

결과: 의도된 exit 1, `stage=predecessor_validation`.

- S200 C1/source contract 재검증 뒤 S108 final commit read attempt 1,
  successful S108 metadata read 0
- threshold/external attestation read attempt 0
- data/model path resolution과 model download 0
- row/audio/transcript/S1 prediction/oracle/locked/GPU/metric/prediction/
  result/output path/output 0

### Test·정적 품질

- S201 unit+integration targeted: **18 passed**
- S200→S201 관련 회귀: **36 passed**
- 전체 unit 회귀: **548 passed**, 기존 경고 7개만 발생
- Ruff check: passed
- Ruff format check: passed
- mypy: passed

합성 완전 경로는 S108 required artifact 8개와 모든 input을 commit 전후 두
번 hash 검증했다. 이 경로에서도 data row/audio/transcript, model
download, S1 prediction, oracle alignment, locked-test, GPU, metric,
prediction, result와 output은 0이었다. Fault test는 source/model symlink,
mutable revision, normalization 순서 변경, S1 누수·phone 역할 활성화,
external 역할 오용, S108 artifact hash와 registry redirect, S200 lock
변조, missing threshold/license/model, manifest TOCTOU를 모두 거부했다.

## 7. 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `865c3152f90dd38a2705e4fa0b58fd2a511ee20a098ef159e142b16ee2bd8c02` |
| contract | `88f0cd148129fd880b8af4322b30d21715e9aea26eea4d48bb2f64f130ad7667` |
| CLI | `0f10c1d9104e80152baba94e1764ffcc1af2b2c2f0147d20af656586ef22d2b2` |
| unit test | `ea0a21a9aa59540dc2153d305b871aa5b1744659b190c6ccd3aa4367d24c5463` |
| integration test | `6416d490b99450f57e00847e29beca34c0a073436f27c841821340f5f23cfa8e` |
| data/model card | `189e0eca2828bdd9fc53802e91f6dab0cd0df89d4e3f686b047c4cb5ac51b594` |
| paper amendment template | `dd6a11c6749876d9bae2f92e5f19725602794ff90514f9eada6f72de3ec5ce26` |

이 readiness 문서의 hash는 자기참조를 피하기 위해 상위 tracker 또는
handoff에서 기록한다.

## 8. C/H/M 위험 감사

현재 정적·합성 구현 범위의 open blocker:

- Critical: 0
- High: 0
- Medium: 0

검사 기준은 gate 이전 data/model/output 접근, S1/oracle/locked 누수,
threshold 우회, external 역할 혼용, mutable/unpinned model, symlink·
traversal·registry redirect, hash/binding·TOCTOU, 결과/완료 허위 생성이다.

다음은 결함을 숨긴 것이 아니라 의도적으로 실행을 막는 외부 선행
조건이다.

1. S108 final passed commit과 completed registry 없음
2. S200 readiness lock 없음
3. WER/CER/content-mismatch threshold 미등록
4. 세 자료 license/partition/expert governance attestation 없음
5. exact local Whisper bundle 없음
6. production runner·deep result validator 없음

실제 자료·모델·runner가 생기면 C/H/M을 다시 감사해야 한다.

## 9. 다음 실행

1. S108 final과 S200 lock을 실제 immutable evidence로 완료한다.
2. 실제 결과나 data/model path를 열기 전에 threshold preregistration을
   사람 승인으로 동결한다.
3. S2 expert content와 FLEURS/ZEROTH의 aggregate attestation을 만든다.
4. 승인된 별도 획득 절차로 exact model bundle을 준비하고 offline
   attestation을 만든다.
5. 이 package와 분리된 production runner·registry·commit·deep validator를
   구현·감사한다.
6. 실제 실행과 독립 재계산 뒤에만 amendment template 값을 채우고 정본
   원고/evidence map으로 승격한다.
