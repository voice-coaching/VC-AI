# G04 로컬 음성 AI 프록시 결과 — 2026-08-09

## 결론

고정 AI Hub 71490 표본 240건의 로컬 Qwen2-Audio 재청취는 완료됐다.
다만 161건에서 모델이 기권했고 이 실행은 전문가 검증을 대체하지 않으므로
**공학적 프록시 완료**이지 **canonical G04 통과**가 아니다. 계약 순서에
따라 D07과 그 이후 단계는 시작하지 않았다.

## 실행 조건

- 프로토콜: `docs/evaluation/G04_AUDIO_AI_PROXY_PROTOCOL_KO.md`
- 모델: 로컬 `Qwen2-Audio-7B-Instruct`
- revision: `0a095220c30b7b31434169c3086508ef3ea5bf0a`
- 장치·dtype: CPU, bfloat16
- G04 전용 fine-tuning: 없음
- 처리: 240/240, remaining 0
- 외부 음성·전사 전송: 없음

Claude는 이 실행에 사용하지 않았다. 제한 음성과 R1 주석을 외부 서비스에
전송할 수 없고, 현재 로컬 환경에 Claude가 음성을 직접 청취했다는 검증
가능한 인터페이스가 없기 때문이다. 대신 로컬 Qwen2-Audio 결과를 전문가
재청취 우선순위용 프록시로만 생성했다.

## 집계 결과

| 판정 | 건수 |
|---|---:|
| consistent | 53 |
| target_annotation_mismatch | 4 |
| realized_annotation_mismatch | 22 |
| both_annotations_mismatch | 0 |
| uncertain | 161 |
| 합계 | 240 |

기권 161건의 원인은 모델 토큰 평균 확률이 `0.45` 미만인 경우 78건,
목표·실현 주석 어느 쪽과도 phone 유사도가 `0.45`에 도달하지 못한 경우
83건이었다. 전체 모델 확률 중앙값은 `0.5586`, 기권 항목 중앙값은
`0.4585`였다. 따라서 단일 임계값만 사후 완화해서 해결할 문제가 아니라
모델 청취 신뢰도와 주석 대응 실패가 함께 존재한다.


현재 `align_intervals()` 구현으로 저장 정렬을 재계산한 결과는 221/240
(92.0833%) 일치였다. 19건의 차이는 현재 작업 트리에서 후보 우선순위가
실제 겹침을 먼저 보도록 변경된 뒤 고정 표본이 재생성되지 않은 영향이
포함되므로, 그 자체를 사람 주석 오류 19건으로 해석하지 않는다.

## 산출물과 무결성

- 공개 가능 집계: `artifacts/evaluation/g04_audio_ai_proxy_aggregate_v1.json`
- 집계 SHA-256:
  `7f371872ad191baa4ffb7bfa65f8dbcbed359faefeaa6f9ecd64ec0d4164df3b`
- 제한 상태: R1의 `g04_audio_ai_proxy_state_v1.json`
- 제한 상태 SHA-256:
  `a955b29cbd35f46e0e8d135374ba41e0af6a789de7d1bb8ec022a8a62cff0057`
- 집계가 가리키는 제한 상태 해시와 실제 파일 해시: 일치
- 공개 집계의 금지 키(전사문, 원시 ID, 음성 경로, prompt): 없음

제한 상태는 240개 행과 `completed_proxy` 상태를 갖는다. 공개 집계는
`substitutes_expert_review=false`,
`canonical_g04_completion_claim_allowed=false`를 명시한다.

## 실행 중 결함과 수정

첫 전체 실행은 8건 뒤 Qwen 출력이 구두점만 남은 항목에서 g2pK가
phone을 만들지 못해 fail-closed로 중단됐다. 구두점-only 출력을 빈 전사로
정규화해 `uncertain`으로 기권하도록 수정하고 회귀 테스트를 추가한 뒤,
저장된 8건부터 재개해 240건을 완료했다.

관련 정적 검사와 단위 테스트 23개는 통과했다. 테스트 환경의 FastAPI
호환성 경고 1건은 있었지만 실패는 없었다.

## 게이트 판정과 다음 조치

현재 G04는 다음 이유로 닫혀 있다.

1. AI 프록시 기권이 161/240이다.
2. 모델은 G04 전용으로 학습·보정되지 않았다.
3. 현 계약상 AI 프록시는 독립 전문가 재청취와 attestation을 대체하지
   않는다.
4. 현재 정렬 구현과 고정 표본 사이의 19건 drift를 별도 adjudication해야
   한다.

따라서 다음 허용 작업은 프록시가 표시한 기권·불일치 항목을 포함한 독립
전문가 재청취, 또는 평가자 대체에 관한 명시적 계약 변경과 그 대체 모델의
별도 검증이다. 둘 중 하나로 canonical G04가 통과하기 전에는 D07을
시작하지 않는다.
