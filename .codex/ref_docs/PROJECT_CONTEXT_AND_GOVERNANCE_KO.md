# intelligentAI 프로젝트 맥락과 작업 거버넌스 참고

이 문서는 루트 `docs/`의 프로젝트 명세를 작업 시작 시 빠르게 해석하기 위한
참고 요약이다. 원본 명세와 상태의 정본은 계속 루트 `docs/`에 있으며, 이 문서는
그 내용을 대체하거나 독립적인 완료 근거가 되지 않는다.

## 1. 프로젝트 성격

`intelligentAI`는 한국어 L2 학습자의 발음을 진단하고, 근거가 있는 제한된
코칭과 기준 음성을 제공한 뒤 재녹음 전후를 비교하는 **연구용 폐루프 AI 보이스
코칭 시스템**이다. Python/FastAPI를 사용하며, 현재 공개 API와 Oracle은 실제
모델 또는 제품 배포를 대신하지 않는 연구 계약이다.

핵심 연구 흐름은 다음과 같다.

```text
제한 음성·라벨
  -> manifest/QC/화자 분리 split
  -> 명시적 특징·GOP·WavLM 진단(S1)
  -> 독립 감사(S2)·confidence gate
  -> 근거 제한 coaching
  -> 권리 확인된 reference bank
  -> 재녹음 비교와 사람 평가
```

현재의 최우선 목표는 TTS 학습이나 제품 배포가 아니라, 재현 가능하고 신뢰할 수
있는 진단·평가 근거를 먼저 확보하는 것이다.

## 2. 정본 문서와 상태 판정

- 전체 목표·완료 정의·현재 work package 상태:
  `docs/constraints/RESEARCH_COMPLETION_TRACKER_KO.md`
- 구현 순서와 제품 범위:
  `docs/IMPLEMENTATION_PLAN_KO.md`
- S104--S108 WavLM 후속 실행의 정본 gate:
  `docs/constraints/WP4_POST_S103_EXECUTION_CONTRACT_KO.md`
- 실패·오염·실행 편차의 보존과 판정:
  `docs/constraints/PROTOCOL_DEVIATION_LEDGER_KO.md`
- 데이터 구역·가명화·반출 검사:
  `docs/constraints/DATA_PRIVACY_BOUNDARY_KO.md`
- 데이터별 역할·라이선스·공개 제한:
  `docs/constraints/DATA_LICENSE_AND_ROLE_MATRIX_KO.md`

계획, static readiness, config-only 검증, dry-run, 개발 결과, 실제 실행,
독립 검증, 사람 평가 완료를 서로 바꾸어 말하지 않는다. 완료는 명령·checksum·
metric report·registry·사람 평가 원본 등 해당 gate가 요구한 재현 근거가 모두
있을 때만 선언한다.


## 3. 모델·평가 작업의 fail-closed 원칙

- 학습 전에는 label 적합성, speaker-disjoint split, QC, manual audit,
  smoke, reload, evaluation, human-listening 등 해당 단계의 선행 gate를
  확인한다. 미해결 gate를 환경변수·임시 flag·대체 metric으로 우회하지 않는다.
- JSON의 expected phone과 TextGrid의 realized phone은 의미가 다르다. 시간
  overlap 기반 결합만 사용하며, 불명확한 label을 휴리스틱으로 고치지 않는다.
- train/dev/calibration/locked-test의 접근 역할을 분리한다. label leakage,
  split overlap, prompt 예약 위반, 결과의 사후 선택을 발견하면 실행을 멈추고
  기록한다.
- interrupted/failed run, stale registry, partial output, SIGTERM 종료는
  성공이 아니다. 원본 증거를 보존하고 정해진 rollover·재실행 계약이 있을 때만
  복구한다.
- 모델 지표·교정·coaching 안전성·그룹별 격차·독립 평가를 통과하기 전에는
  production, 교육 효과, 임상 효과, 일반화 성능을 주장하지 않는다.

## 4. 코드와 계약 경계

- `domain`은 순수 계약·상태·정책만 둔다. FastAPI, 파일 시스템, 모델 runtime,
  네트워크를 import하지 않는다.
- `application`은 use case를 조합하지만 HTTP 객체를 알지 못한다.
- `api`는 schema 검증과 응답 변환만 수행한다. 진단 판단, gate, coaching
  selection을 route에 넣지 않는다.
- 파일·GPU·subprocess·모델 runtime·dataset portal·외부 HTTP는 typed
  adapter/client 경계 뒤에 둔다. service/application은 좁은 의도 기반
  interface를 통해서만 사용한다.
- Oracle endpoint는 연구 계약 검증용이며 실제 추론, 자동 교정 또는 closed-loop
  production 흐름의 대체물이 아니다.

## 5. 문서 갱신 기준

작업으로 모델/데이터 계약, gate, split, artifact schema, privacy·license,
외부 연동, API, 상태 전이 또는 디렉터리 경계가 바뀌면 관련 루트 `docs/`를
동시에 갱신한다. 반대로 실제 실행하지 않은 결과를 status 문서에 추가하지
않는다. 새 연구 실행·편차·실패는 필요한 경우 tracker와 protocol-deviation
ledger에 사실·영향·다음 조치를 분리해 기록한다.

## 6. 참고한 루트 문서 묶음

| 주제 | 정본 문서 |
| --- | --- |
| 전체 아키텍처와 API 경계 | `docs/architecture/`, `docs/api/` |
| 데이터·개인정보·라이선스 | `docs/DATA_*`, `docs/AIHUB71490_*`, `docs/NATIVE_KOREAN_CORPUS_PIPELINE_KO.md` |
| 모델·실험·평가 gate | `docs/WAVLM_*`, `docs/constraints/WP4_POST_S103_EXECUTION_CONTRACT_KO.md`, `docs/S2_*`, `docs/GOP_*` |
| coaching·reference·closed loop | `docs/C0*`, `docs/R0*`, `docs/E0*` |
| 인간 평가·논문·재현성 | `docs/H0*`, `docs/paper/`, `docs/constraints/PROTOCOL_DEVIATION_LEDGER_KO.md` |
