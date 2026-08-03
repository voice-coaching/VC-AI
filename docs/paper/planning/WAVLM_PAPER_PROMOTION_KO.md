# WavLM Large/S103 논문 증거 승격 계약

최종 갱신: 2026-07-29 KST  
상태: **Large/S103 결과 승격·canonical 논문 반영·증거 재검증 완료**

## 감사 결론

현재 실행 자동화는 다음 경계까지 이미 구현돼 있다.

1. Large frozen-head 완료 report와 registry
2. Base+–Large S103 comparison report·execution receipt·commit
3. 검증된 Markdown·aggregate JSON·results-table commit

각 단계는 불변 generation과 SHA-256 chain을 사용한다. 다만 기존 논문
절차는 results-table commit 생성 뒤 “evidence map에 등록하고 PENDING
표를 교체한다”는 수동 지침만 있었고, 다음 조건을 한 번에 확인하는
downstream gate는 없었다.

- 새 Large report/registry가 실제 S103과 결과 표가 참조한 것과 같은가
- comparison commit의 report/receipt binding이 그대로인가
- results-table commit과 Markdown/JSON generation이 그대로인가
- 원고와 evidence map이 준비 manifest 작성 뒤 동시에 바뀌지 않았는가
- 삽입 뒤에도 development-only, oracle-alignment, no-promotion 및
  stack/calibration/locked-test 미접근 계약이 유지되는가
- 아직 남은 S104–S108을 포함하는 PENDING marker가 보존되는가

이를 위해 별도 준비 도구를 추가했다. 이 도구는 canonical 원고나 active
evidence map을 자동 수정하지 않는다.

- manifest template:
  `docs/paper/wavlm-s103-paper-promotion.template.yaml`
- 준비 CLI: `scripts/prepare_wavlm_paper_promotion.py`
- 검증 모듈:
  `src/voice_coach/reproducibility/wavlm_paper_promotion.py`
- 단위 검증: `tests/unit/test_wavlm_paper_promotion.py`

## 준비 단계의 잠금 상태

Template의 상태는 의도적으로 `pending`이고 모든 미생성 artifact
fingerprint는 `PENDING`이다. `status: ready`가 아니면 도구는 manifest가
가리키는 report, registry 또는 commit을 읽기 전에 즉시 실패한다.

따라서 실제 Large head, S103 comparison commit과 results-table commit이
없을 때는 후보 원고, 가짜 표, placeholder result JSON 또는 promotion
receipt가 생성되지 않는다. 이 문서와 template에는 실제 Large/S103
성능값을 예시로도 기록하지 않는다.

## 실제 결과가 나온 뒤의 준비 순서

1. Large validator를 통과한 완료 report와 완료 registry를 고정한다.
2. S103 comparator가 생성한 `commit.json`을 고정한다.
3. 결과 표 renderer가 생성한 `results_table.commit.json`을 고정한다.
4. Template을 별도 manifest로 복사하고 여섯 input과 현재 원고/evidence
   map의 정확한 workspace 상대경로, SHA-256, byte size를 기록한다.
5. 모든 값을 독립 검토한 뒤 복사본의 상태만 `ready`로 바꾼다.
6. 다음처럼 후보 bundle을 준비한다.

```bash
env PYTHONPATH=src \
  ./.venv-scorer/bin/python scripts/prepare_wavlm_paper_promotion.py \
  --manifest docs/paper/wavlm-s103-paper-promotion.ready.yaml \
  --output-root artifacts/reproducibility/wavlm_s103_paper_promotion
```

원본 template 자체를 `ready`로 바꾸지 않는다. 실행 manifest는 해당
generation의 검토 기록으로 보존한다.

## 준비 도구의 강제 검증

도구는 renderer가 사용하는 기존 strict gate를 다시 호출해 다음을
재검증한다.

- Base+/Large head report·registry와 등록 artifact의 현재 hash chain
- 정확한 세 seed, 완료 상태, development-only와 oracle-alignment
- S103 comparison report·receipt·commit binding
- comparison source report/registry/prediction 및 구현 source chain
- seed별·aggregate metric, paired bootstrap, Holm과 gate의 재계산 일치
- committed results Markdown/JSON의 generation과 comparison commit binding
- 결과 JSON과 재계산 metric, Markdown 재렌더 결과의 완전 일치
- 결과 범위의
  `locked_test_accessed=false`, `stack_accessed=false`,
  `calibration_accessed=false`, `promotion_decision_allowed=false`
- 준비 시점 active evidence validator의 전체 통과와 원고/evidence-map
  fingerprint 고정

하나라도 다르면 candidate generation commit을 만들지 않는다.

## 생성되는 review bundle

성공 시 content-addressed generation 아래에 다음 파일을 만든다.

- `manuscript.candidate.md`: committed Markdown의 표·제한 문구는
  유지하고 heading level만 원고 3.3 절 아래로 결정적으로 내린 뒤, 원래
  `[PENDING: S101–S108 — explicit/Base+/Large/adapter/HiPAMA/fusion 표]`
  바로 앞에 삽입한 후보 원고
- `evidence-map.candidate.yaml`: canonical 원고 경로를 유지한 전체 후보
  evidence map
- `evidence-map.additions.yaml`: 기존 map의 주석과 편집 이력을 보존하며
  `apply_patch`로 병합할 artifact·claim·locked guard 추가분
- `candidate-validation.json`: candidate 원고를 대상으로 실행한 실제
  evidence validator 결과
- `promotion-receipt.json`: 모든 input, derived commit artifact, target,
  candidate와 PENDING 계약 hash를 고정한 receipt
- `commit.json`: 위 receipt를 가리키는 단일 원자 pointer

Review 시에는 같은 모듈의
`load_committed_wavlm_paper_promotion`이 commit/receipt binding, 모든
source와 candidate hash, generation 경로 및 candidate validation 상태를
다시 확인한다. Candidate가 한 byte라도 바뀌면 load가 실패한다.

추가 evidence는 Large report/registry, S103 commit/report/receipt,
results-table commit/Markdown/JSON 여덟 artifact를 등록한다. 생성된
phone·utterance·primary 표의 모든 metric cell과 bootstrap 반복 수,
gate 판정을 232개의 JSON Pointer claim으로 연결한다.

## PENDING과 locked-test 보존

후보 표는 기존 S101–S108 marker를 삭제하거나 문구를 바꾸지 않고 그
앞에 삽입한다. S103 개발 비교가 완료돼도 adapter, HiPAMA, fusion,
calibration과 최종 S108은 해결되지 않았기 때문이다. 준비 전후 marker
개수와 정렬 multiset hash가 같지 않으면 bundle 생성이 실패한다.

Candidate evidence map은 다음 guard를 추가한다.

- Large report의 stack/calibration/locked-test 접근 flag
- S103 comparison report의 같은 세 접근 flag
- results JSON scope의 같은 세 접근 flag와
  `promotion_decision_allowed=false`
- Large registry metric에서 stack/calibration/locked-test split 금지

따라서 S103의 개발용 gate status를 모델 승격, locked-test 결과 또는
배포 성능으로 바꾸어 서술할 수 없다.

## Canonical 반영

Bundle 성공은 자동 출판 승인이 아니다. 다음 순서로 사람이 반영한다.

1. Candidate의 표 행·제한 문구를 committed Markdown과 비교하고 heading
   level 외의 내용 변경이 없는지 확인한다.
2. `evidence-map.additions.yaml`의 path·hash와 claim regex를 검토한다.
3. canonical 원고와 evidence map만 `apply_patch`로 함께 수정한다.
4. 원고의 기존 PENDING marker와 제한 문구가 그대로인지 확인한다.
5. canonical evidence validator를 재실행한다.
6. 전체 test, Ruff, mypy와 `git diff --check`가 모두 통과한 뒤에만 논문
   변경으로 간주한다.

준비 manifest 이후 canonical 원고나 evidence map이 바뀌면 target hash
drift로 실패하므로, 새 상태를 검토해 manifest fingerprint부터 다시
작성해야 한다.

## 2026-07-29 실제 반영 결과

- Large completed head report/registry SHA-256:
  `ba869265c6e6c1381677fe922922b68da7df92cbb9cbaf5501fff5dafa3fdd23`,
  `85c90e95b954ec2b74081f44b4e64d311330985a0429d76f0cb67b7a86d5b565`
- S103 comparison commit/report/receipt SHA-256:
  `e45cf869ab54beff6bcf5a3c674c0bdff0d75460ab8daabe50c7477afca16c6e`,
  `90db58ee3af3bb47b88a8ae1700e6a0a9ce9f16feeaa06b8e1c5d7ffbe778213`,
  `342a9bd2b97de87a1bfd38c3ed0a22819b1151eb37836d90e941951781f620a6`
- results-table commit/Markdown/JSON SHA-256:
  `af0cb1d616e3c9377985b880bb19233647964b744ab23c98e6b69d257c273e38`,
  `89db29654f8c542154040e1626131fc2df29ea71ad0c99b36e531244cd255b0f`,
  `d8556c3a433e5d09b119ffaa567735c34bf10f19ba065a3173602d44ab5d7a20`
- ready manifest:
  `docs/paper/wavlm-s103-paper-promotion.ready.yaml`
- review bundle generation:
  `9aac308f22b03b6f7fd71fd9dae7ade92c7ab92fe1fa446979a4f2a86894f9c3`
- promotion commit SHA-256:
  `f01e41af8bdbeb08f599ab6d84ed1a8ee7fc1b0da6d10802711300a5d4d0a1c6`
- canonical 원고/evidence map SHA-256:
  `f0ed2301910eef608bfd7a7c5be879ab233ff29e7a080aecaabcd6e0e39b6d98`,
  `1614d1512604146f1773193a5a1919f2c2892ea16d48872e90c6f064ba31df88`
- canonical evidence validator: artifact **33/33**, claim **463/463**,
  PENDING marker **18개**, locked-test guard 통과
- validator report SHA-256:
  `2f33749d73bbdf59f18312db8c7d315594f92f91e4c9d402790afe7a6f15ba20`

S103 표의 canonical 반영은 완료됐지만 S104–S108과 독립
gold/locked-test/deployable alignment 근거는 계속 PENDING이다. S103
gate는 development-only 분기 판단이며 승격 또는 배포 근거가 아니다.
