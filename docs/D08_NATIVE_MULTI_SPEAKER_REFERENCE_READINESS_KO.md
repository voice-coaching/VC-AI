# D08 원어민 다화자 기준음 production 정적 준비도

최초 작성: 2026-07-29 KST  
상태: **정적 preflight 구현 완료 / 실제 모집·동의·녹음·QC·완료 0**

## 1. 결론

D08 전용 strict config, R1 metadata/D1 QC/A1 completion schema, H00·개별
동의·녹음 권리 template, QC preregistration template, read-only contract/
CLI, 합성 fault test와 데이터·라이선스·개인정보 카드를 구현했다.

현재 canonical full preflight는 R00 actual completion을 심층 재검증한 뒤
`H00 institutional approval is TBD_EXTERNAL_ATTESTATION`에서 의도적으로
종료한다. H00 경로 자체를 해석하거나 읽지 않으며 그 뒤의 개별 동의,
녹음 권리, QC 사전등록, R0/R1/D1, 오디오, 마이크, 모델, 네트워크, GPU와
출력도 모두 0이다.

이 패키지는 녹음기나 QC executor가 아니다. 실제 모집, 동의 receipt,
recording-license grant, microphone profile, session/take, WAV, metadata,
QC report, completion, commit과 publication은 생성하지 않았다.

## 2. D08 전용 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/d08_native_multispeaker_reference_v1.yaml` | R00→H00→동의→권리→QC prereg 순서와 zero-runtime 경계 |
| `configs/reference/d08_recording_metadata_v1.schema.json` | R1 녹음·세션·take·format·receipt strict schema |
| `configs/reference/d08_qc_report_v1.schema.json` | D1 row-level QC strict schema |
| `configs/reference/d08_completion_v1.schema.json` | 식별자·row가 없는 A1 completion schema |
| `configs/reference/d08_h00_institutional_approval_v1.template.json` | 비승인 H00/기관 결정 양식 |
| `configs/reference/d08_individual_consent_attestation_v1.template.json` | 분리 동의 범위와 receipt 양식 |
| `configs/reference/d08_recording_rights_attestation_v1.template.json` | source/license·권리·녹음 protocol 양식 |
| `configs/reference/d08_qc_preregistration_v1.template.json` | speaker floor·QC metric·adjudication 미확정값 양식 |
| `src/voice_coach/references/d08_native_reference_contract.py` | strict parser, R00 deep validation, 미래 artifact 전수 validator, symlink/hash/TOCTOU/no-overwrite guard |
| `scripts/prepare_d08_native_multispeaker_reference.py` | config-only/full read-only CLI |
| `tests/unit/test_d08_native_reference_contract.py` | policy·TBD·schema·attestation·metadata·QC·completion fault test |
| `tests/integration/test_prepare_d08_native_multispeaker_reference.py` | 실제 workspace zero-runtime와 R00-first/H00 path-read-zero 검사 |
| `docs/D08_NATIVE_REFERENCE_DATA_MODEL_LICENSE_PRIVACY_CARD_KO.md` | 데이터·동의·source/license·R0/R1/D1/A1 경계 |
| `docs/D08_NATIVE_MULTI_SPEAKER_REFERENCE_READINESS_KO.md` | 본 준비 증거 |

기존 canonical tracker, R00/R01 artifact, 원고와 evidence map은 수정하지
않았다.

## 3. 근거를 넘지 않는 고정

다음은 고정했다.

- R00 actual completed 20문장
- 화자×문장별 자연속도 2 take + 명료 교육속도 1 take
- 48 kHz/24-bit mono PCM WAV
- 동일 장비·마이크·공간 profile과 15–20 cm metadata
- clipping ratio `<0.001`, decoder error 0
- ASR은 후보 triage-only, 사람 reviewer pool 2명 중 최소 1명 최종 확인
- 공개 corpus/TTS를 human으로 위장 금지와 explicit source/license
- R0 원음, R1 locator/identity/session, D1 QC/embedding, A1 aggregate

다음은 문서 근거 이상으로 고정하지 않았다.

- 문장별 4명은 planning target이며 binding completion floor가 아니다.
- binding speaker floor는 `TBD_PREREGISTRATION`이다.
- DC offset, content match, noise, reverb, interruption의 metric·단위·
  방향·threshold는 `TBD_PREREGISTRATION`이다.
- QC 불일치 trigger·decision·독립 adjudication은
  `TBD_PREREGISTRATION`이다.
- exact equipment/microphone/room profile은
  `TBD_EXTERNAL_ATTESTATION`이다.

Template는 승인 문서가 아니고 모든 외부 status/hash가 TBD다. 그대로는
future validator를 통과할 수 없다.

## 4. R00 actual completion deep validation

Config pin 일치만으로 R00을 통과시키지 않는다. Full preflight는 고정
R00 validator를 동적으로 다시 실행하고 public sentence card/schema/CC0
notice 및 public·aggregate evidence만 사용해 report를 재계산한다.

요구사항:

- 재계산 report와 immutable report canonical JSON exact 일치
- `status=valid`, 문장 20개
- `r00_completion_claim_allowed=true`
- `r01_completion_claim_allowed=false`
- R00 TTS/audio 생성과 human evaluation false
- raw audio·row-level read 0

R00 뒤에는 source snapshot 19개와 R00 report를 다시 확인해 외부 승인
gate 사이 TOCTOU를 막는다.

## 5. H00-first fail-closed 순서

외부 gate는 다음 순서다.

1. H00 또는 동등한 기관 승인
2. 활성 개별 동의와 분리 scope
3. recording-license grant와 human/TTS/source/license 정직성
4. QC·speaker floor·adjudication 사전등록
5. consent/prereg floor와 config/rights exact profile cross-binding
6. 별도 모집·녹음·QC executor

각 external SHA가 TBD이면 path를 normalize하거나 존재 여부를 probe하기
전에 중단한다. 현재 첫 SHA인 H00이 TBD이므로 H00 path resolution/read
attempt/read가 0이고 이후 모든 external/runtime counter도 0이다.

## 6. Future artifact deep validator

합성 fixture로 다음 계약을 검증했다.

- H00 approval ID/document/기간/scope/privacy plan과 수집 허용
- 개별 consent receipt의 성인·원어민·활성·비철회·20문장 범위
- 녹음 권리 receipt와 동의 speaker set exact 일치
- 공개 corpus/TTS 위장 금지와 explicit license basis
- R1 metadata의 20문장, prereg speaker floor, exact 세 take
- 동일 equipment/microphone/room, 48k/24-bit/mono PCM WAV, 15–20 cm
- clipping `<0.001`, decoder 0과 preregistered 다섯 metric
- ASR final-QC 금지, 사람 청취와 triggered adjudication receipt
- A1 aggregate에서 row/speaker/recording/audio/locator/receipt 제거
- count·lineage·completion claim 전수 재계산
- duplicate YAML/JSON, NaN/Inf, source hash drift, traversal/symlink,
  TOCTOU와 no-overwrite fail-closed

이 validator는 audio를 열거나 metric을 계산하지 않는다. 미래 production
executor가 생성한 metadata·QC·aggregate의 구조·lineage·결정을
독립적으로 검증하는 계약이다.

## 7. Canonical 실행 결과

### Config-only

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_d08_native_multispeaker_reference.py --config-only
```

결과: `valid_config_only`

- source pin read: 19
- R00 deep validation: 0
- H00/동의/권리/QC preregistration path·read: 0
- 모집·동의수집·R0/R1/D1 path, metadata/QC/completion read/write: 0
- audio/microphone/model/network/GPU/QC metric/listening/adjudication: 0
- publication/commit/output path/output: 0

### Full preflight

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_d08_native_multispeaker_reference.py
```

결과: 의도된 exit 1,
`stage=h00_institutional_approval`,
`error=H00 institutional approval is TBD_EXTERNAL_ATTESTATION`.

- source pin read/TOCTOU verify: 19/19
- R00 validator load/deep validation: 1/1
- R00 public·aggregate evidence access: 33
- R00 raw audio/row-level read: 0/0
- H00 path resolution/read attempt/read: 0/0/0
- 이후 external prerequisite path/read: 모두 0
- 모집·동의수집·recording/audio/microphone/model/network/GPU/QC: 모두 0
- metadata/QC/completion/publication/commit/output: 모두 0
- stdout: empty
- 선언 output 5개: 생성 0

## 8. Test와 정적 품질

- D08 unit fault test: **14 passed**
- D08 integration test: **3 passed**
- D08 targeted 합계: **17 passed**
- R00+R01+D08 관련 회귀: **54 passed**
- R00–R05+D08 reference 전체 회귀: **162 passed**
- Ruff check: passed
- Ruff format check: passed
- mypy contract/CLI: passed

## 9. 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| config | `027b0c7b170e27b308a26ea9d0431d85ed4699894311e815c331770724d0742f` |
| R1 metadata schema | `6ae7bf0c6d30cf85e9f02fad37586d0dcb06baac7789e62f6310c4e20732364e` |
| D1 QC schema | `4064c2260d3df57b9f41977bcbe303f393ea4d7c582c295b31e39a80df646aa6` |
| A1 completion schema | `1ae8e5d37e0ce25b7d5831e321c44d7bff8d9805c7c24a3f2367b73dad6ac59c` |
| H00 approval template | `88321e78e556d53949f92cfdc8dadda6195fe8807ae235fe507544d4199693b1` |
| individual consent template | `ae8bee17dd8e94db3efd86b5dbe277c77521ab3f89730a370dea15a4df05c193` |
| recording rights template | `89d02baf5b9d5ad8ec415575aefeddbcdb89d096bc950026a00fdbdad637e7ed` |
| QC preregistration template | `e1d7b3a6b81614852f76c809299eae490c75d8e0ac2855f90b569da146537472` |
| contract | `1082147df808503dcf92be8466984171f9b2c5364afc0fcc78dd88cae7700549` |
| CLI | `f4a711e79f71fffab8b499151055423f6328e055b65f2ccaace97787350bbbf8` |
| unit test | `02a9f98e248d8b2e7c1825939f2413dacefafaac97d2f93ac8a3bc9bfe5a558d` |
| integration test | `888ba0e0fa64022bc0c081606635020e95a14803bf0b499e713fe5e580bd5313` |
| data/license/privacy card | `cd7d3d452480af17180aa6fab1f2c27112f87a5d40343a4db1ace0943c32d2c1` |

Readiness 문서 자체 hash는 자기참조를 피하기 위해 상위 handoff에서
기록한다.

## 10. C/H/M 감사

현재 정적·합성 패키지 범위의 open implementation finding:

- Critical: 0
- High: 0
- Medium: 0

감사 범위는 R00 완료 우회, H00/동의/권리/prereg 순서 우회, planning
target의 hard-floor 오표기, 임의 QC threshold, public corpus/TTS 위장,
동의 철회·만료, format/take/setup drift, R0/R1/D1/A1 누수, ASR final-QC
오용, A1 식별자 누수, symlink/hash/TOCTOU/no-overwrite와 허위 완료
주장이다.

다음은 구현 결함을 숨긴 것이 아니라 실행을 의도적으로 막는 외부
blocker다.

1. H00 또는 동등한 기관 승인 없음
2. 성인 원어민 모집과 활성 개별 동의 receipt 없음
3. recording-license grant와 exact 장비·마이크·공간 attestation 없음
4. binding speaker floor와 exact QC/adjudication 사전등록 없음
5. 별도 production recorder/QC executor와 독립 감사 없음
6. 실제 WAV/R1 metadata/D1 QC/A1 completion/commit 없음

## 11. 다음 실행

1. H00/기관 승인 범위와 승인 문서 hash를 확정한다.
2. Audio 접근 전에 speaker floor, 다섯 QC metric과 adjudication을
   독립 승인·사전등록한다.
3. 개별 동의와 녹음 권리 receipt를 수집하고 exact 장비·마이크·공간
   profile을 서명한다.
4. External SHA와 exact profile을 함께 새 config version으로 동결한다.
5. Full preflight 통과 뒤 별도 recorder/QC executor를 구현·감사한다.
6. R0 원음, R1 metadata, D1 QC를 no-overwrite·atomic publish하고
   실패/철회 자료를 격리한다.
7. A1 completion 전수 검증과 독립 receipt 뒤에만 D08 상태를 갱신한다.

그 전에는 모집, 녹음, audio/microphone 접근과 D08 완료 주장이
금지된다.
