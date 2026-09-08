# 입술 24점 시계열 shadow 개발 계획

## 상태와 결정

- `implemented`: P6가 고정한 40개 MediaPipe 후보 schema와 24점 mapping을 이용해
  같은 attempt의 프레임별 24점 형상을 입 중심·입 너비 기준으로 정규화한다.
- `implemented`: 24점 배열에서 입 너비, 외곽/내곽 벌림, 외곽/내곽 면적,
  양 입꼬리 높이 비대칭과 프레임 간 형상 변화율을 계산한다.
- `implemented`: landmark 배열은 메모리에만 유지하고 receipt에는 mapping/source
  digest, coverage, 시간 범위, trajectory digest만 남긴다.
- `implemented`: candidate 영상 digest를 기존 `SameAttemptBindingReceipt`의 영상
  digest와 대조해 다른 attempt의 궤적이 섞이면 거부한다.
- `implemented`: 별도 network-isolated worker가 같은 프레임에서 MediaPipe 입술
  후보점 40개를 추출하고, 기존 release worker의 얼굴 수와 공통 4개 좌표가
  일치할 때만 결과를 채택한다. 기존 source-hash 고정 worker는 수정하지 않았다.
- `implemented`: production CLI의 명시적 closed-beta 옵션으로 이 경로를
  sidecar와 grounded renderer에 연결했다. 기존 보정된 입술 벌림 action과 함께
  6개 형상 관찰값 및 가능한 경우 구간 변화율을 사용자 문장에 표시한다.
- `verified`: production visual release와 로컬 테스트 영상 한 건을 사용한 실제
  전체 시도에서 `audio_completed_visual_supplemented`, 6개 형상값 공개,
  grounded audio+visual 문장, 제한 작업공간 삭제 완료를 확인했다.
- `implemented`: P6 geometry artifact는 production worker의 read-only mount와
  deployment attestation에 포함되고, 선택 음소 구간의 aggregate 측정이 result v4로
  전달된다.
- `verified`: request-v5 Backend worker 경계에서 실제 CUDA Seungun v2와 testvideo1을
  실행해 7개 aggregate 측정, raw audio/video digest 일치, 완전한 result-v4 직렬화를
  확인했다. 이 한 표본은 일반화 성능 근거가 아니다.
- `blocked`: `contains_pronunciation_truth`와 `contains_action_truth`를 `true`로
  승격하는 변경은 근거 artifact가 없어 여전히 차단한다.

AI Hub 68 geometry artifact는 24점 위치 mapping과 tracker 오차를 검증한
artifact다. 발음 정답이나 특정 교정 행동이 적절하다는 정답을 수집·심사한
artifact가 아니다. 따라서 두 값을 직접 바꾸고 digest를 다시 계산하는 행위는
정답 근거를 추가하지 못하며 기존 fail-closed 계약만 우회한다.

## 재검토한 개발 순서

1. **Shadow geometry 계약** — 완료
   - 40개 후보점의 순서가 P6 calibration schema와 정확히 같은지 검사한다.
   - 고정 artifact의 24점 mapping 및 digest에 결과를 결속한다.
   - 평행 이동·크기 영향을 줄인 24점 시계열과 외부에서 관찰 가능한 형상값만
     만든다.
2. **별도 runtime v2 adapter** — 완료
   - 현재 production worker 파일은 P6 runtime parity artifact가 source hash로
     고정하므로 직접 변경하지 않는다.
   - 별도 network-isolated worker가 사용자 영상의 40개 후보점을 추출하게 하고,
     새 worker/model/protocol digest로 shadow trajectory에 연결한다.
   - 기존 production의 단일/다중 얼굴 coverage, 노출·입 주변 대비·tracking
     연속성 및 A/V sync gate 실패 시 trajectory를 피드백에 사용하지 않는다.
   - production release와 P6 geometry artifact가 모두 로드되고 CLI 옵션으로
     명시된 경우에만 활성화한다.
3. **정답 annotation artifact** — 차단
   - pronunciation truth는 자격을 갖춘 사람의 음소 단위 판정, annotation
     protocol, adjudication, split/lineage receipt가 필요하다.
   - action truth는 교정 행동의 적절성과 후속 시도 개선 여부를 사람이 검수한
     별도 label 및 agreement 근거가 필요하다.
   - 두 truth는 geometry artifact에 덧씌우지 않고 별도 versioned artifact로
     만든다.
4. **Offline 평가와 정답 승격** — 미실행
   - actor/speaker-disjoint development, calibration, locked evaluation을 분리한다.
   - 음소/viseme별 coverage와 오류, 카메라·조명·pose slice, calibration과 human
     usefulness를 평가한다.
   - gate를 통과한 cue/action만 allowlist에 추가하고 새 production release를
     발행한다.

## 제품 경계

- selected phone과 발음 판정은 계속 Seungun의 같은-attempt 결과만 사용한다.
- 24점 형상과 시간 변화는 그 판정을 설명하는 보충 근거이며 새 음소를 추론하지
  않는다.
- closed-beta renderer는 코드로 고정된 측정 설명만 생성하고 이 값이 발음 정답
  또는 교정 행동의 정답이 아니라고 명시한다. 새 교정 행동은 만들지 않는다.
- 정면 2D 입술 영상으로 혀, 성문, 기류 또는 실제 돌출 깊이를 판정하지 않는다.

## Closed-beta 실행 계약

`--closed-beta-lip-geometry-artifact`를 주면 production entrypoint가 다음 조건을
모두 확인한 뒤 40점 경로를 활성화한다.

- production visual release가 `enabled`이고 고정 landmark model이 설정됨
- geometry artifact가 단일 hard link의 비공개 일반 파일이며 strict JSON,
  자체 digest, 24점 mapping digest를 통과함
- geometry artifact가 별도 network-isolated fresh process에서 같은 digest로
  재로딩됨
- 새 worker와 기존 worker의 얼굴 수, timestamp, 공통 4점 좌표가 일치함
- Seungun이 선택한 같은 attempt의 음소 시간 구간 및 QC/A·V gate를 통과함

공개 supplement에는 40/24점 배열을 넣지 않고 입 너비, 외곽/내곽 벌림,
외곽/내곽 면적, 입꼬리 높이 차이, 가능한 경우 최대 형상 변화율과 lineage
digest만 남긴다. `contains_pronunciation_truth`와 `contains_action_truth`는 모두
`false`다.
