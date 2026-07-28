# 다음 단계 실행 계획

최종 갱신: 2026-07-29 (Asia/Seoul)

## 현재 판정

현재 상태는 `LABEL_NOT_SUITABLE`이다. 이 판정은 JSON phones를 실제 관측
발음 CTC target으로 사용하는 기존 계획에 대한 것이다. TextGrid phone
tier를 정규화한 별도 target으로 사용할 수 있는지는 아직 검증되지 않았다.

## Phase 1: TextGrid 관측 target 적합성 감사

목표는 TextGrid의 모든 phone label이 일관된 규칙으로 실제 관측 음소열로
변환 가능한지 확인하는 것이다.

측정 항목:

- Training/Validation 전체 raw TextGrid label inventory와 빈도
- `-` 삭제 placeholder의 수와 발화 비율
- 공백으로 여러 음소가 묶인 label의 종류와 빈도
- 빈 interval, `spn`, 비한글 label 및 미지 label
- JSON 기대열과 정규화 TextGrid 관측열의 차이
- error tag별 차이 유형
- Training/Validation UserID 중복
- 정규화 후 빈 target 및 CTC에 사용할 수 없는 target 수

후보 정규화 정책은 감사 결과가 나온 뒤 확정한다.

```text
빈 TextGrid interval -> 시간 공백이므로 target에서 제외
"-" -> 기대 음소 삭제 placeholder이므로 관측 target에서 제외
"ㄱ ㅓ" 같은 공백 label -> 두 관측 음소로 분리할 후보
"spn" -> 명시적 unknown/noise phone으로 유지할 후보
```

정책을 확정하기 전에는 processor나 manifest를 생성하지 않는다.

### 2026-07-29 실행 상태

전체 322,380 matched 발화를 검사했다. 현재 결과는
`artifacts/textgrid_target_audit.json`에 저장돼 있다.

- 공식 Training/Validation 화자 교집합: 1,812
- raw TextGrid label: 1,715종
- 삭제 placeholder: 23,873 interval
- 공백 복합 label: 1,755 interval
- phone tier 누락: 1개
- 정규화 후 빈 target: 48개
- 현재 gate: `review_required`

보수적 41-phone 정책 초안 적용 결과:

- 안전한 자모 연결 interval: 12,873
- 미해결 raw label: 357종
- 미해결 interval: 1,785
- 미해결 label 포함 발화: 1,450
- 추가 검토 큐: `artifacts/textgrid_unresolved_labels.csv`

다음 즉시 작업은 `textgrid_unresolved_labels.csv`의 label을 규칙별로
분류하는 것이다. 허용할 수 없는 label이 있는 발화는 임의 교정하지 않고
훈련 후보에서 제외하며, 제외율을 화자·언어·숙련도별로 다시 측정한다.

## Phase 2: TextGrid target gate

다음 조건을 모두 충족해야 다음 단계로 간다.

1. 모든 raw label이 명시적 규칙으로 처리된다.
2. 임의 문자열 제거 또는 암묵적 phone mapping이 없다.
3. 정규화 후 빈 target 비율과 특수 label 비율이 보고된다.
4. 오류 사례에서 정규화 관측열이 error tag와 일치한다.
5. JSON 기대열과 TextGrid 관측열을 manifest에서 별도 필드로 보존한다.
6. dataset ID/제목 불일치에 대한 사용자의 데이터셋 확인이 기록된다.

통과하지 못하면 `FIX_REQUIRED` 또는 `LABEL_NOT_SUITABLE`을 유지한다.

## Phase 3: 화자 분할 manifest

TextGrid target gate 통과 후에만 수행한다.

- 공식 Training/Validation 간 UserID 중복 검사
- seed 42 화자 단위 train/dev/test 재분할
- 세 분할 UserID 교집합 0 검증
- 언어·숙련도·국적·error tag 분포 보존
- JSONL과 Parquet 생성
- `expected_phones`와 `observed_phones`를 명확히 분리
- raw label과 정규화 규칙 version을 함께 기록

## Phase 4: Processor와 vocabulary

- 정규화된 Training `observed_phones`만 vocabulary 원천으로 사용
- `<pad>`를 CTC blank로 사용하고 `<unk>` 포함
- 명시적 단어 경계가 없으면 `|`를 target에 추가하지 않음
- label ID encode/decode 왕복 테스트
- AI Hub canonical/MFA mapping은 별도 JSON으로 관리

## Phase 5: XLS-R smoke training

위 gate 통과와 target 정의 승인 후에만 시작한다.

- `facebook/wav2vec2-xls-r-300m` revision 고정
- 화자 단위 train 5천~2만, dev/test 각 1천~2천
- bf16, gradient checkpointing, feature encoder freeze
- 16kHz mono float32 동적 resampling/padding
- loss 감소, NaN/Inf, 빈 decode, peak memory 검사
- dev PER로 checkpoint 선택, test는 최종 1회
- 저장 후 재로딩 전후 출력 일치 검증

## 전체 학습 재개 조건

다음 조건이 모두 충족될 때만 `FULL_TRAINING_READY`를 선언한다.

- TextGrid target 의미와 정규화 정책 검증 완료
- 화자 누수 0
- smoke loss와 decode 정상
- dev/test PER 및 S/D/I 산출
- checkpoint 재로딩 일치
- pytest/Ruff/mypy 통과
- 데이터셋 ID와 실제 사용 범위 확정

그전까지 `xlsr300m_full.yaml`의 `enabled`는 `false`로 유지한다.
