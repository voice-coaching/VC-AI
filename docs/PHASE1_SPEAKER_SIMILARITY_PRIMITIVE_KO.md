# Phase 1 m8 화자 임베딩 유사도 순수 primitive

작성: 2026-07-29 KST  
상태: **순수 수치 primitive 구현 / 실제 WavLM-SV·ECAPA·오디오 실행 0**

## 1. 범위

PDF의 Phase 1 m8은 `WavLM-SV cosine`을 화자/스타일 신호로 제안하고,
Phase 4 품질 평가는 별도로 `ECAPA-TDNN SIM`을 요구한다. 이번 구현은 두
모델 중 어느 것도 실행하지 않는 공통 수학 커널이다.

- 입력: 후보 embedding 1개와 상위 단계에서 동의가 확인된 reference
  embedding collection
- 검증: 비어 있지 않은 동일 차원, 모든 원소 finite, boolean 금지, 각
  embedding의 L2 norm이 0이 아님
- 계산: embedding별 float64 L2 정규화 후 cosine
- 출력: 최종 score, reference count, dimension, 정규화·집계 정책,
  입력·결과 SHA-256 digest만
- 부수효과: filesystem, audio, model, checkpoint, GPU, network, 환경변수
  접근 없음

구현은
`src/voice_coach/evaluation/speaker_similarity.py`에 있으며
`score_speaker_similarity(...)`의 `aggregation`은 필수 keyword다. 함수
이름의 `consented_reference_embeddings`는 필요한 상위 단계 계약을
표현할 뿐, 이 함수가 동의 receipt를 검증한다는 뜻이 아니다.

## 2. 명시적 집계 정책

논문과 현재 preregistration은 exact multi-reference SIM 집계를 고정하지
않았다. 따라서 기본값은 없으며 caller가 다음 enum 중 하나를 사전에
선택해야 한다.

| 정책 | 계산 | 가능한 해석 |
|---|---|---|
| `pairwise_mean` | 모든 candidate-reference cosine의 산술평균 | 모든 참조를 같은 가중치로 반영 |
| `pairwise_median` | pairwise cosine의 중앙값 | 극단 참조의 영향을 줄인 중심 경향 |
| `pairwise_minimum` | pairwise cosine의 최솟값 | 가장 불리한 참조에 대한 보수적 점수 |
| `pairwise_maximum` | pairwise cosine의 최댓값 | 가장 가까운 허용 exemplar와의 일치 |
| `normalized_centroid` | L2 정규화된 참조들의 산술 중심을 다시 L2 정규화한 뒤 cosine | 참조 집합의 중심 방향과 일치 |

`normalized_centroid`가 상쇄되어 zero vector가 되면 정의되지 않은 점수를
만들지 않고 실패한다. Reference 입력 순서는 canonical 정렬하므로 결과와
digest에 영향을 주지 않는다. 중복 reference는 숨겨서 제거하지 않고 각각
한 관측으로 계산하며 count에 포함한다.

정책은 점수를 본 뒤 선택하면 안 된다. 실제 실험 전 모델·전처리·reference
구성·집계·결측 처리·임계값을 함께 사전등록해야 한다. 이 primitive는
threshold나 pass/fail을 제공하지 않는다.

## 3. Digest와 개인정보 경계

`input_digest_sha256`은 L2 정규화된 유효 입력, reference multiset과 집계
정책의 canonical JSON을 결합한다. 따라서 reference 순서에는 불변이며,
동일한 정규화 float64 vector를 만드는 원시 크기 차이는 digest에 직접
포함되지 않는다. `result_digest_sha256`은 score와 공개 결과 필드를
결합한다.

결과에는 다음을 넣지 않는다.

- 원시 또는 정규화 embedding과 pairwise score
- speaker/candidate/reference ID
- audio/model/checkpoint 이름이나 경로
- 원문, source kind, human/TTS label

Digest도 익명화가 아니며 동일 입력의 연결 가능성이 있으므로 미래 실제
실험에서는 D1 제한 artifact로 취급하고 별도 승인 없이 공개하면 안 된다.

## 4. 실패 계약

다음 입력은 `SpeakerSimilarityError`로 fail closed한다.

- candidate 또는 reference collection/vector가 빈 경우
- NaN, `+Inf`, `-Inf`, boolean 또는 실수가 아닌 원소
- zero-norm embedding
- candidate-reference dimension 불일치
- enum이 아닌 집계 값
- zero-norm normalized centroid

집계 인자를 생략하면 Python 호출 계약 자체가 `TypeError`를 낸다.

## 5. 비주장과 남은 실험

이 primitive는 다음을 대체하거나 완료하지 않는다.

- WavLM-SV 또는 ECAPA-TDNN 모델·checkpoint 선택과 실행
- 모델 코드·가중치 라이선스와 exact preprocessing 검증
- reference 화자의 동의, 사용범위, identity/biometric 보호 승인
- 화자 검증 성능, style 유사성 타당도, spoof/OOD·성별·연령·억양 편향 감사
- R02 hard gate, R04 5조건 자동평가 또는 인간 MOS/CMOS
- m8 학습, S1 fusion, 논문 결과나 완료 주장

실제 m8을 완료하려면 승인된 reference cohort와 speaker-disjoint
protocol을 고정하고, exact WavLM-SV artifact로 embedding을 생성한 뒤 이
primitive에 넘겨야 한다. Phase 4의 ECAPA-TDNN SIM은 별도 artifact와 같은
평가 protocol로 실행해야 하며, WavLM-SV 결과로 대신할 수 없다.

## 6. 검증 명령

```bash
CUDA_VISIBLE_DEVICES='' PYTHONPATH=src .venv-scorer/bin/pytest -q \
  tests/unit/test_speaker_similarity.py

.venv-scorer/bin/ruff check \
  src/voice_coach/evaluation/speaker_similarity.py \
  tests/unit/test_speaker_similarity.py

.venv-scorer/bin/ruff format --check \
  src/voice_coach/evaluation/speaker_similarity.py \
  tests/unit/test_speaker_similarity.py

.venv-scorer/bin/mypy \
  src/voice_coach/evaluation/speaker_similarity.py
```

실행 결과:

- primitive unit: **21 passed**
- primitive + R02/R04 unit·integration 회귀: **62 passed**
- Ruff check: 통과
- Ruff format check: 통과
- mypy: 통과

모든 실행은 `CUDA_VISIBLE_DEVICES=''`, CPU affinity와 단일 thread 제한으로
수행했다. 실제 embedding, audio, model, GPU, network, consent receipt를
읽지 않았다.
