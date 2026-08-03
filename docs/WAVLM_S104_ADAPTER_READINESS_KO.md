# WavLM S104 adapter 구현·실행 준비 기록

최초 작성: 2026-07-29 KST  
최종 갱신: 2026-08-03 KST
상태: **v1 CUDA 시도 무효화 / 현재 코드 기준 v2 contract·full preflight
완료 / user-systemd GPU 3-seed 실행 중**

## 1. 현재 결론

S104의 정본 config, CPU preflight, 모든-layer residual adapter, online
raw-audio trainer, 3-seed inner selection/dev 평가, content-addressed 산출물
publisher, completed/failure registry, commit-only 독립 validator까지
구현했다. CPU synthetic 경로와 저장소 전체 회귀는 통과했다.

다만 다음 사실을 결과와 구분해야 한다.

- 첫 S104 CUDA 학습 PID `386918`은 seed 42 epoch 9 fit 중 외부
  SIGTERM과 일치하는 exit code 143으로 종료됐다.
- seed 42/43/44의 실제 checkpoint·dev prediction·metric은 아직 없다.
- 따라서 논문의 S104 실험 셀을 채울 수 없고 어떤 점수도 주장할 수 없다.
- 정본 S103 comparison은 gate 실패를 확정해 Base+를 선택했고, 실제
  full preflight는 immutable execution lock을 생성했다.
- v2 정본 trainer는 2026-08-03 19:18 KST user-systemd unit
  `intelligentai-s104-v2.service`에서 시작됐다. 연속 source-pin watcher가
  config·lock·implementation hash를 10초 간격으로 검사하고 있다.

즉, **v1 첫 실행 시도는 정본 결과로 사용할 수 없고, 현재 checkout을
기준으로 분리한 v2 실행 계약만 다음 실행에 사용한다**. v1의 stale
registry/partial은 source pin이 현재 checkout과 달라 기존 rollover
계약으로 archive할 수 없으므로 그대로 보존한다. v2의 completed commit과
독립 validator가 나오기 전에는 결과 완료나 metric을 주장하지 않는다.

## 1.1 현재 코드 기준 v2 실행 계약

2026-08-03 KST 사용자 승인으로 v1 실패 시도와 분리된 새 계약을 만들었다.
기존 v1 config·execution lock·registry·partial은 변경하지 않는다.

| 항목 | v2 고정값 |
|---|---|
| config | `configs/experiments/wavlm_s104_adapter_v2_current_code.yaml` |
| config SHA-256 | `7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434` |
| execution lock | `artifacts/experiments/aihub71490_wavlm_s104_adapter_v2_current_code/execution_lock.json` |
| execution lock SHA-256 | `c37ce4e3d0aa7b7e0c016c56342397b818d6316d6a0e7e22bab9867a63ab5a3e` |
| experiment ID | `aihub71490_wavlm_s104_adapter_v2_current_code` |
| planned run ID | `aihub71490-wavlm-s104-adapter-v2-current-code-dev` |
| selected candidate | WavLM Base+ |
| included utterances | 169,837 |
| current interval source pin | `0fc28d734951ca300bb208d2b76b89a9207093886853a0ce65adf30705c247cf` |
| preflight status | `ready_for_s104_gpu_training` |
| preflight GPU/raw-audio execution | 0 |

이 v2는 현재 checkout의 source hash를 고정한 development-only,
oracle-alignment, silver-label, no-promotion 실행이다. S104 실제 GPU
실행·checkpoint·dev metric·completed registry·commit은 아직 0이다.

## 2. 서브태스크 완성도

| ID | 서브태스크 | 완성도 | 현재 증거·다음 조건 |
|---|---|---:|---|
| S104-01 | 사전 등록 config와 S103 분기 계약 | 100% | `Large - Base+ phone macro F1 >= 0.03`이면 Large, 아니면 Base+로 고정 |
| S104-02 | CPU-only preflight와 immutable execution lock | 100% | 실제 S103·data·model·implementation 전이 hash, split/privacy, Base+ local snapshot 검증 및 lock 생성 완료 |
| S104-03 | residual adapter와 trainable allowlist | 100% | 모든 WavLM encoder layer에 bottleneck 64, adapter+2개 head 외 trainable 금지 |
| S104-04 | online raw-audio joint trainer | 100% 구현 | bounded streaming, label-aware chunk, 단일 WavLM forward 공유, BF16/checkpointing 구현 |
| S104-05 | inner selection과 dev 1회 평가 | 100% 구현 | train speaker-disjoint selection, train-only vocab/weight/threshold, seed별 dev 정확히 1회 |
| S104-06 | 산출물·registry·atomic publisher | 100% 구현 | seed당 5개+aggregate 1개, 정확히 16개, 실패/복구 fault injection 통과 |
| S104-07 | 독립 commit validator | 100% 구현 | source truth·metrics·slice·checkpoint·registry·S103 predecessor를 재구성해 검증 |
| S104-08 | 실제 고성능 GPU 3-seed 실행 | 진행 중; v1 시도 무효화 | v1 PID `386918`은 seed 42 epoch 9 중 exit 143. v2 unit `intelligentai-s104-v2.service`는 active, trainer PID `57618`에서 CUDA 사용이 관측됐다. 현재 checkpoint·metric·artifact·commit은 0이며 종료·성공 판정은 아직 없다 |
| S104-09 | 실제 결과 검증·논문 반영 | 0% | committed S104 generation과 독립 validator 통과 뒤 수행 |
| S104-10 | 승격 판단 | 0% | S104는 development-only이며 별도 G04/D07 수동 gate 없이는 승격 금지 |

### 첫 실행 종료 forensic

- 마지막 생존/최초 부재 관측: 2026-07-29 23:23:17 /
  23:23:47 KST
- 세션 회수 결과: exit code 143; signal 발신 주체와 직접 원인은 미확정
- 종료 시점: seed 42 epoch 9 fit, checkpoint·prediction·metric 0
- registry SHA-256:
  `ff7fac6b7bb9104fda8c540794d5eec45c32de7f7d3458c27e9e392f6b56b7f2`
- registry 상태: `running`, `ended=null`, `failure=null`,
  metric/artifact 0/0
- partial: `.part-1ee97821e55d4fa283dea77182283c8e`, 자식 0
- OS OOM/NVRM/XID/reboot/shutdown 증거: 0
- root/독립 감시자의 종료 signal 전송: 0

수동 삭제나 metadata 편집은 하지 않는다. 전용 one-shot rollover
도구의 공격 테스트와 postflight를 통과한 뒤에만 첫 시도를 보존
archive로 이동한다. 재실행은 기존 config와 execution lock을 그대로
사용하되 unified-exec 수명과 분리된 user-systemd service와 연속
pin watcher를 사용한다.

완성도 100%는 해당 코드·CPU 검증 범위의 완료를 뜻한다. 실제 GPU
실험 완료나 연구 결과 확보를 뜻하지 않는다.

## 3. 고정된 실험 계약

| 항목 | 고정값 |
|---|---|
| 후보 선택 | S103 point estimate가 0.03 이상이면 WavLM Large, 미만이면 Base+ |
| adapter | 모든 encoder layer residual bottleneck, 폭 64 |
| 학습 가능 parameter | encoder adapter, phone head, utterance head만 |
| full/top-block fine-tuning | 이 run에서 금지 |
| 입력 | R0 raw WAV를 R1 manifest locator로 bounded streaming |
| encoder gradient | pooled frozen cache 금지, online WavLM forward만 허용 |
| primary scope | L1 Chinese/Japanese, gender F/M |
| 학습·선택 | train의 speaker-disjoint inner fit/validation만 |
| phone loss | 발화 안 phone 평균 후 발화 단위 평균 |
| utterance loss | 발화 안 관측 outcome 평균 후 발화 단위 평균 |
| early stopping | phone macro F1, comprehensibility ICC(2,1), earliest epoch 순 |
| undefined ICC | 개선으로 인정하지 않음 |
| refit | inner selection 뒤 refit 금지 |
| dev | 고정 checkpoint로 seed당 정확히 1회, 선택에 사용 금지 |
| engineering 목표 | phone macro F1 0.75, comprehensibility ICC(2,1) 0.65 |
| word metric | 독립 word label이 없으므로 `not_evaluable` |
| OOM | 자동 재시도 없이 failed registry 1개를 남기고 중단 |
| 결과 지위 | development-only, oracle-alignment, silver-label, no-promotion |

Primary utterance outcome을 `comprehensibility_score`로 고정한 것은 첫 dev
결과를 보기 전 결정이다. 이를 바꾸려면 새 사전 등록과 새 config hash가
필요하다.

## 4. 데이터 준비와 보호 경계

실제 modeling manifest를 raw audio 없이 읽어 다음을 확인했다.

- QC·예약 prompt·primary scope 적용 뒤 S104 대상 발화: **167,506개**
- 대상 phone interval: **2,199,869개**
- 최소 target interval 길이: **30 ms**
- 20 ms 미만 및 25 ms 미만 interval: **각 0개**
- 시간 역전, frame overlap, 중복 frame-gradient blocker: **0개**
- config가 고정한 전체 train/dev preflight 포함 행: **169,837개**

Preflight와 metadata scan은 `train`, `dev`만 허용하며 `stack`,
`calibration`, `locked_test`를 읽지 않는다. QC/reserved/scope 검사를
통과하기 전에는 audio locator를 resolve하지 않는다. 학습 시에는 WAV별
PCM hash를 최초 1회 검증하고 stat identity를 끝까지 다시 확인한다.

Execution lock과 aggregate report에는 원본 ID, pseudonymous row ID,
speaker ID, prompt hash, raw locator를 쓰지 않는다. Row-level prediction은
D1 restricted-internal 범위다.

## 5. 구현된 학습 경로

주요 파일은 다음과 같다.

- `configs/experiments/wavlm_s104_adapter_v2_current_code.yaml`
  - 현재 checkout source pin, S103 branch, 두 model snapshot pin,
    optimization/evaluation/output 계약
- `src/voice_coach/models/wavlm_adapter.py`
  - residual adapter 주입, backbone freeze, canonical trainable allowlist
- `src/voice_coach/training/wavlm_adapter_contract.py`
  - CPU preflight, S103/data/model/implementation hash-chain, execution lock
- `src/voice_coach/training/wavlm_adapter_training.py`
  - streaming trainer, inner selection, dev writer, publisher와 validator
- `scripts/prepare_wavlm_s104_adapter.py`
  - torch/CUDA/raw audio를 사용하지 않는 config-only/full preflight
- `scripts/train_wavlm_s104_adapter.py`
  - 정본 3-seed 실행과 `--validate-commit-only`

학습기는 다음 불변 조건을 강제한다.

- 선택 snapshot을 `local_files_only`로 열고 model config/checkpoint hash를
  시작·commit 직전·validator 시점에 다시 검사한다.
- 짧은 tail은 WavLM receptive field 최소 400 sample까지 pad하고 사전
  등록된 정책에 따라 padded receptive field 전체를 valid로 표시한다.
  일반 chunk는 실제 sample만 valid로 표시한다.
- 각 chunk에서 phone pooling과 utterance sufficient statistics가 정확히
  하나의 WavLM forward를 공유한다.
- SpecAugment off, layerdrop 0, deterministic algorithms,
  `CUBLAS_WORKSPACE_CONFIG=:4096:8`, BF16과 gradient checkpointing을
  fail-closed로 적용한다.
- gradient clip은 non-finite를 오류로 처리하고 optimizer step마다 모든
  trainable tensor의 finite 여부를 한 번 동기 검사한다.
- seed 42/43/44 각각 train-only metadata와 inner selection을 사용하며,
  dev scanner는 완전한 1회 pass 외 재진입할 수 없다.

## 6. 산출물과 불변성

각 seed는 다음 5개 파일을 만든다.

1. `adapter_and_heads.safetensors`
2. `phone_dev_predictions.parquet`
3. `utterance_dev_predictions.parquet`
4. `dev_slice_metrics.json`
5. `resource_and_trainable_parameter_report.json`

여기에 `experiment_report.json` 1개를 더해 총 **16개**다.

Validator는 다음을 독립적으로 확인한다.

- generation tree의 정확한 파일 목록·size·SHA-256
- safetensors의 정확한 key/shape/dtype/metadata와 finite 값
- 모든 encoder layer adapter 존재, full-backbone tensor 0
- manifest와 explicit label에서 재구성한 dev identity·phone truth·score
- prediction threshold, overall metric과 모든 slice metric 재계산
- train-only vocabulary/class weight/fit cardinality
- resource peak, trainable audit와 checkpoint 상호 결합
- report, registry, execution lock, generation, commit의 hash 결합
- 현재 S103 commit/report/receipt와 그 모든 transitive source hash를
  다시 읽어 execution lock의 branch evidence와 exact 비교

Generation은 content-addressed directory에 먼저 publish하고 completed
registry가 완성된 뒤 commit pointer를 쓴다. rename 전/후 fsync,
registry 초기화·등록·종료, precommit, commit publication 실패를 각각
fault-injection으로 검증했다. 유효한 generation+completed registry가
있고 commit만 없으면 재학습 없이 검증 후 commit을 복구한다.

## 7. 실행 순서

### 7.1 현재 가능한 read-only 검사

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s104_adapter.py \
  --config configs/experiments/wavlm_s104_adapter_v2_current_code.yaml \
  --config-only
```

이 명령은 S103, data, raw audio, CUDA를 읽지 않고 파일도 쓰지 않는다.

### 7.2 S103 commit 완료 직후

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/prepare_wavlm_s104_adapter.py \
  --config configs/experiments/wavlm_s104_adapter_v2_current_code.yaml
```

성공 시 다음 execution lock이 생긴다.

`artifacts/experiments/aihub71490_wavlm_s104_adapter_v2_current_code/execution_lock.json`

선택 후보, S103 gate, data cardinality, model/implementation hash를 검토한
뒤에만 GPU 실행으로 넘어간다.

### 7.3 정본 고성능 GPU 실행

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/train_wavlm_s104_adapter.py \
  --config configs/experiments/wavlm_s104_adapter_v2_current_code.yaml
```

2026-07-29 KST에 `CUDA_VISIBLE_DEVICES=0`,
`CUBLAS_WORKSPACE_CONFIG=:4096:8`을 고정해 PID `386918`로 이 명령을
시작했다. Base+ BF16 경로의 GB10 CUDA 연산은 관측됐지만 seed 42
epoch 9 fit에서 외부 SIGTERM과 일치하는 exit code 143으로 종료됐다.
Registry는 비종료 `running`, failure `null`, metric/artifact 0/0으로
남았고 seed별 정본 artifact나 commit은 없다.

같은 명령을 현재 바로 재호출하면 안 된다. 먼저 검증된 one-shot
rollover로 첫 registry와 빈 partial을 content-addressed incident
archive에 원자 보존한다. 그 다음 동일 명령을 persistent user-systemd
service 안에서 처음부터 실행하고 연속 pin watcher를 함께 둔다.

별도 축소 smoke config는 사전 등록되어 있지 않다. 임의로 seed,
epoch, batch/context를 바꿔 같은 정본 run으로 기록하면 안 된다. CUDA/BF16
admission이 실패하거나 OOM이 나면 실패 registry를 보존하고 새 protocol을
사전 등록한다.

### 7.4 완료 증거 read-only 검증

```bash
PYTHONPATH=src ./.venv-scorer/bin/python \
  scripts/train_wavlm_s104_adapter.py \
  --config configs/experiments/wavlm_s104_adapter_v2_current_code.yaml \
  --validate-commit-only
```

이 검증이 성공하기 전에는 metric을 논문이나 결과표에 옮기지 않는다.

## 8. 다음 실행 체크리스트

1. S102 Large frozen-head의 completed registry와 report를 독립 검증한다.
2. S103 comparison generation/receipt/commit을 완성하고 gate 결과를
   독립 검증한다.
3. S104 full preflight를 실행해 immutable execution lock을 만든다.
4. 선택 model과 execution lock hash를 검토한다.
5. v1 source pin mismatch와 stale state를 보존 기록한다. 기존 v1
   rollover는 실행하지 않는다. **완료(archive 불가 사유 기록)**
6. persistent user-systemd service와 연속 pin watcher에서 v2
   immutable config/lock의 정본 3-seed S104를 처음부터 실행한다.
   **대기**
7. `--validate-commit-only`로 16개 산출물과 predecessor chain을 검증한다.
8. aggregate report에서 실제 metric·slice·VRAM·failure 상태를 추출한다.
8. development-only 한계를 유지해 논문 표와 실험 절을 갱신한다.
9. 승격이 필요하면 별도 G04 수동 alignment와 D07 전문가 검증을 완료한다.

검증된 S103 commit과 v2 full preflight blocker는 해소됐다. v2 lock은
Base+를 선택했고 정본 S104 GPU 3-seed 실행이 user-systemd에서 진행 중이다.
다음 판정 지점은 terminal commit 생성과 read-only validator 통과다.

## 9. 검증 기록

2026-07-29 KST 기준:

- S104 core unit/integration: **59 passed**
- S104 preflight integration: **36 passed**
- 합친 S104 묶음: **95 passed**
- 공용 registry·explicit·Base+/Large comparison 회귀: **96 passed**
- `PYTHONPATH=src` 저장소 전체 collection: **557 tests**
- `PYTHONPATH=src` 저장소 전체 회귀: **557 passed**
- Ruff check와 format check: 통과
- mypy: S104 source 오류 0
- 실제 config-only: `status=config_valid`, S103/data/raw-audio/GPU read 0,
  output write 0
- 실제 full preflight: `status=ready_for_s104_gpu_training`, Base+ 선택,
  169,837개 포함, raw-audio/GPU 실행 0, execution lock SHA-256
  `8d8778061ec8e366208618071a806e2b04c78e22612ffdabdd6a46f047d274ca`
- 독립 논리 감사: **Critical 0 / High 0 / Medium 0**

경고는 기존 Starlette/httpx deprecation 1개와 tiny/actual WavLM의
attention-mask deprecation뿐이며 현재까지 실패는 아니다. 위 정적·회귀
검증은 CPU-only였고, 실제 GPU 학습 상태와 결과 완료 판정은 별도로
추적한다.

## 10. 최종 고정 SHA-256

| 파일 | SHA-256 |
|---|---|
| S104 v1 historical config | `aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9` |
| S104 v2 current-code config | `7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434` |
| preflight CLI | `58642383dbab13db4a00ada5188be701a2aefab5a3475d1c953bc85f53610e77` |
| trainer CLI | `68048e553fbb50f291588076897c9a7372c6bb557b8b8b952d23c1c60917cda5` |
| contract module | `a5c3c933b4fb55cbe0f8c33deb70a98bf47b53539304f6299b0728188b9ea06c` |
| training module | `a7b41d507385c7f45b102f2d1603ad4214f0247ad9e0e08c6a3640f2df6bed80` |
| adapter module | `6b5a89bbf26593c9aa8b2720b305cf448fba4c10510e890ff7339e5f4f6121f5` |
| registry module | `6929389771711b9d104b299ee540c93777f32e0be3fa7d720228f549296b998b` |
| WAV QC module | `654d7b6c258801a02f2361c3d547eafb6dbb08d847bb1863b0aa24c84af57aef` |
| preflight integration test | `ce97c67a3e1605ad5b12cdb62df67c3518a515181d6800e35f50347b400cba08` |
| trainer integration test | `e43ccd4c0f6f35c448a9d8fc693ce53c898b69765e11f4b3f01bbe62e7daaf46` |

Config의 `implementation` section은 모든 production 역할의 이 해시를
고정한다. Production source가 한 byte라도 바뀌면 config-only와 full
preflight가 실패해야 하며, 변경 시 새 hash와 재검증 기록이 필요하다.
