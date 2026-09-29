# 입술 관찰–GPT P0/P1 구현·배포 기록

2026-09-30. 사용자 요청 범위는 `implementation-plan.md`의 P0/P1 코드 구현과 RunPod 배포다. P2·새 공개 결과 계약·Backend/FE 수정·모델 변경은 포함하지 않는다.

## 구현

- 후보의 `candidateId`, 대표 `audioEvidenceId`, `observationId`를 명시적으로 연결한다. 입력 예산으로 후보를 줄일 때 해당 영상 관찰·허용 statement도 함께 제거한다. 원본 source와 영수증은 보존한다.
- 내부 `voice-coaching.coaching-input.v2`, `coaching-measurement-receipt.v2`, `coaching-visual-policy-v2`, 생성용 `coaching_generation_v2`를 사용한다. 정책은 `UNAVAILABLE`/`OBSERVATION_ONLY`만 허용한다.
- 측정 단계에서 기본 중심 프레임의 실제 시각·구간 내부 여부, beta 평균의 유효 표본 수·사용 시각, 변화율 최대 집계의 표본·직전 context 시각을 전달한다. decode 표본 수로 유효 표본 수를 대체하지 않는다. 구간 밖 중심 프레임은 관찰로 승격하지 않는다.
- 변화율 값 계산은 기존 정수 ms 시간축을 유지한다. 실제 decoder 시각과 `rateTimebase=INTEGER_MILLISECONDS`를 함께 보존해 정밀도가 개선됐다고 주장하지 않는다. 원시 좌표는 GPT나 callback에 보내지 않는다.
- 공통 저밀도 decode는 동일 요청·미디어 digest·decode 정책·표본 개수에서만 재사용한다. 후보별 dense 추출·측정·receipt는 별도로 수행하며 요청 간 raw cache는 없다. 추가 측정 캐시는 도입하지 않았다.
- GPT 요청은 strict JSON Schema를 사용하며 기존 여섯 필드에 `usedVisualStatementIds`를 추가한다. 해당 후보의 허용 statement만 참조할 수 있다. 중복 키·비유한 숫자·모델 identity·refusal·중단·coverage·action 길이 검사와 생성 최대 1회를 유지한다.
- **안전성 우선 제한:** 네 텍스트 필드 모두 후보별 `allowedText`에서 선택한다. 자유 생성의 의미적 교정 위반을 정규식만으로 차단했다고 주장하지 않는다. GPT는 허용된 일반 연습 설명과 영상 재확인 사용 여부를 선택하며 자유로운 상세 맞춤 문장 생성은 제한된다. 같은 후보의 deterministic fallback을 유지한다.
- 서버가 검증된 값·구간·중립적 재확인 문구를 `items[].observation`에 렌더링한다. 출력에서 영상 statement를 참조하지 않으면 문구도 붙이지 않는다. 입력 제공/출력 참조/표시 개수를 비밀·대본 없는 로그로 구분한다. 이것은 모델의 내부 추론 사용 여부를 증명하지 않는다.

## 유지한 계약과 자산

외부 coaching v1 / callback v3 schema 파일은 변경하지 않는다. `coaching.visual`은 명시적 allowlist로 기존 필드만 투영하며 `sampleCount=null`, `referenceStatus=NOT_VALIDATED`, `correctiveClaimsAllowed=false`를 유지한다. 새 내부 ID·영수증·표본 수·`usedVisualStatementIds`는 공개 응답에 추가하지 않는다.

공개 schema에 고정된 `promptRevision=pronunciation-coaching-v1`, `policyRevision=pronunciation-coaching-evidence-v1`은 외부 호환 라벨로 유지한다. 새 내부 규칙은 `visual-coaching-v2.md`와 별도 정책 revision으로 식별하며, 합성 prompt digest에는 새 내용이 포함된다. 새 prompt의 SHA-256을 코드에서 검증하고 불일치 시 기동에 실패한다.

현재 운영의 GPT-only provider를 로컬 코드로 가져와 수정했다. `gpt-6-astra`, Chat Completions endpoint, `store=false`, `reasoning_effort=low`, 두 전문가 사례 전체 주입과 기존 자산 digest를 유지한다. schema 크기도 UTF-8 기반 보수적 입력 예산에 포함한다. 이는 정확한 provider token 수가 아니다. [공식 Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs)의 strict schema·refusal 규칙을 적용했다.

`production.py`와 운영 `prepare_http.py`의 GPT-only 조립은 보존하고 변경된 모듈과 새 prompt만 영구 overlay에 포함한다. 기존 모델 attestation과 신규 코드/파일 manifest를 별개로 대조한다. 공개 계약이 같으므로 Backend reader 변경·DB migration은 없다. VC-BE의 사용자 작업은 건드리지 않는다.

## 확인·배포 상태

구현 기준 로컬 main: `dfa4be904b7ff3c8db42b015981a9145359792b7`. 배포 전 Pod `s8hb5b0l5k4qlx` RUNNING, 이전 runtime revision `5ea4712e55f231450b2120c7121dfaa3ade1ed6f`. 수정 대상 원본은 운영 파일 hash로 대조했다. provider 이외 수정 대상은 로컬 HEAD의 LF 정규화 내용과 일치했다.

로컬 Python 구문 검사·diff 정적 검토를 수행했다. 자동 회귀 테스트·합성 영상·실제 녹음·GPT 생성·TTS 합성·품질 QA는 실행하지 않는다. 새 strict 요청의 실제 생성 성공·입술 관찰 품질·FE 표현은 개발자가 확인해야 한다.

실제 배포 결과와 복구 위치는 작업 완료 후 이 절에 기록한다. 이 문장만으로 배포 완료를 뜻하지 않는다.
