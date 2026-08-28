# P6·P8 시각 데이터 후보 근거 ledger

- 최종 갱신: 2026-08-28 (Asia/Seoul)
- 문서 상태: `public_metadata_audit`
- 조사 범위: AI Hub·Hugging Face·공식 공개 저장소의 카드/메타데이터
- 원본 데이터 접근·다운로드: `not_started`
- restricted data 또는 원본 영상의 Git 반입: 없음

```yaml
dataset_candidate_audit: dataset_candidates_audited
p6_source_assignment: selected_pending_admission
p8_source_assignment: selected_pending_admission_and_seal
data_admission: not_started
data_access_or_download: not_started
calibration_execution: not_started
independent_automated_evaluation: not_started
claim_policy_conformance: not_started
gate_promotion: none
user_visual_claims: disabled
```

위 상태에서 `dataset_candidates_audited`는 공개 페이지에서 후보와 예상 역할을
조사했다는 뜻뿐이다. 데이터 사용 적격성, P6 calibration 실행, P8 평가 실행,
사용자 피드백 정확성 또는 claim-policy conformance 완료를 뜻하지 않는다.

현재 runtime에는 decoded RGB+landmark video-QC candidate와 lip aperture+PCM energy
A/V anchor candidate extractor가 있으며 둘 다 lineage receipt와 합성 unit test를 가진다.
합성 H.264/AAC `--video-fd` CLI도 공식 landmark model과 CUDA Seungun에서 audio 완료·visual
unavailable, QC·content-sync candidate not calibrated, cleanup 완료로 실행됐다. 이는 AI Hub 자료 admission,
실제 사용자 영상 측정 또는 P6 calibration은 아니므로 위 `not_started` 상태와 gate를
변경하지 않는다.

## 1. 이 문서에서 P6와 P8이 뜻하는 것

이 구현 계획의 work package를 다음처럼 고정한다.

| Work package | 이 문서의 범위 | Track E 대응 | 데이터셋이 채울 수 없는 것 |
|---|---|---|---|
| P6 | lip tracker·직접 가시 2D cue의 development/calibration 자료를 정하고, reference/support artifact를 생성·재로드 검증 | VE3·VE4 | 발음 정오답, 행동 지침의 타당성, 사용자 효능 |
| P8 | P6 선택에 쓰지 않은 잠금 자료에서 QC·tracker·A/V 처리·selective policy를 평가하고 동결 catalog의 claim-policy conformance를 자동 검사 | VE5·VE6 | 사용자 효능, 공개 schema에 없는 발음 오류·action truth |

P8은 이 문서에서 **자동 잠금 평가와 claim-policy conformance**를 뜻한다. 공개
데이터 후보를 배정한 것만으로 VE5·VE6을 `passed`로 바꾸지 않는다. P8 partition을
처음 연 뒤에는 동결된 policy/catalog를 재조정할 수 없으며, 다른 연구 문서의
“Phase 8 사용자 학습효과 연구”와도 같은 단계가 아니다.

## 2. 선택 결과

```text
P6 primary candidate
  AI Hub 68 신체 말단 움직임 영상
  -> actor-disjoint development/calibration
  -> 24-point lip landmark와 pose별 2D geometry 검증

P8 provisional locked candidate
  AI Hub 538 립리딩(입모양) 음성인식 데이터
  -> P6 중 열람·선택에 사용하지 않음
  -> QC/ROI/tracker/A-V 처리의 한국어 외부 일반화 평가

P8 auxiliary sync regression candidate
  AI Hub 539 음성 및 모션 합성 데이터
  -> 알려진 offset/drift를 인위적으로 주입한 복원 시험만 수행

Engineering reference only
  HF EGCLLC, HF MultiVSR
  -> adapter/tracker/OOD fixture와 문헌 참고
  -> 한국어 calibration 또는 사용자 claim 정답으로 사용하지 않음
```

68과 538이 서로 다른 AI Hub 데이터셋이라는 사실만으로 참여자 교집합 0이
증명되지는 않는다. 제공기관 provenance 또는 허용된 식별자 기반 교집합 검사를
마치기 전에는 538을 `provisional_locked_candidate`로만 부른다.

## 3. 후보별 공개 메타데이터 감사

### 3.1 AI Hub 68 — P6 1차 후보

- 공식 페이지: [신체 말단 움직임 영상](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=68)
- 확인한 공개 버전: `1.3` (2023-03-31 원천데이터 수정)
- 공개 메타데이터: 200명 배우, 1,000개 단어 발화, 다섯 카메라 방향,
  24개 2D 입술 keypoint, `actor_id`·`word_id`·`camera_id`·`frame_idx`, 카메라
  intrinsic/extrinsic, 성별·연령
- 배정 역할: `ve2_qc_development_candidate`, `ve3_development_candidate`,
  `ve4_geometry_calibration_candidate`
- 현재 판정: `selected_pending_admission`

P6에서 검증할 수 있는 것은 tracker의 landmark 오차·coverage, pose별 반복성,
aperture/width/rounding 같은 직접 가시 geometry와 support 범위다. phone-level timing,
학습자 발음 오류, 발음 정오답 또는 피드백 action label은 공개 schema에서 확인되지
않았다. 따라서 이 자료를 “올바른 발음의 입 모양 정답”으로 해석하지 않는다.

공개 페이지는 입술 2D 영상을 한 표에서는 MP4로, 파일 구조에서는 frame JPG로
설명한다. 실제 manifest를 보기 전에는 파일 단위와 sample 수를 확정하지 않는다.
페이지에 실린 제공자 모델의 RMSE/WER도 이 프로젝트의 threshold로 재사용하지 않는다.

### 3.2 AI Hub 538 — P8 잠금 후보

- 공식 페이지: [립리딩(입모양) 음성인식 데이터](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=538)
- 확인한 공개 버전: `1.0` (2022-07-12 개방)
- 공개 메타데이터: 한국어 audio/video/text, 1,107명, 영상 5,750시간,
  음성 1,150시간, 아홉 촬영 각도, 소음 조건, 문장 전사·시간, speaker 정보,
  face/lip bounding box
- 배정 역할: `ve5_provisional_locked_candidate`
- 현재 판정: `selected_pending_admission_and_seal`

P8에서 처음 열어 tracker coverage, lip ROI 유지, 촬영 각도·소음 slice, A/V 처리 실패,
latency와 fail-closed 상태를 평가할 수 있다. 2D landmark gold, phone alignment,
발음 오류 또는 visual action truth는 공개 schema에서 확인되지 않았으므로 P6
threshold 선택이나 사용자 claim 정답으로 사용하지 않는다.

같은 페이지가 라벨 수를 `6,620`건과 `66,420`건으로 다르게 기재한다. 다운로드 후
manifest·schema·checksum 감사로 해소하기 전에는 규모를 확정하거나 평가 분모로
사용하지 않는다.

### 3.3 AI Hub 539 — P8 sync 보조 후보

- 공식 페이지: [음성 및 모션 합성 데이터](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=539)
- 확인한 공개 버전: `1.0` (2022-07-12 개방)
- 공개 메타데이터: 한국어 audio/video/text/motion, 동기화된 멀티모달 자료
  4,070시간, 음성 1,017시간, 486명, 여러 촬영 각도
- 배정 역할: `ve5_sync_perturbation_candidate`
- 현재 판정: `auxiliary_pending_admission`

공개 메타데이터에는 clip별 실제 sync offset ground truth와 lip landmark가 없다.
그러므로 “원본 sync 절대오차”의 정답 자료로 쓰지 않는다. 적격성 확인 후 원본에
알려진 offset/drift를 주입하고 backend가 이를 복원하거나 안전하게 거부하는지 보는
회귀시험에만 사용한다.

### 3.4 Hugging Face EGCLLC — 비한국어 engineering 참고

- 카드: [SilentSpeak/EGCLLC](https://huggingface.co/datasets/SilentSpeak/EGCLLC)
- 카드 표기: English, CC BY 4.0, enhanced GRID corpus, lip frame,
  dlib 기반 2D lip coordinate와 word alignment
- 배정 역할: `external_tracker_fixture_candidate`
- 현재 판정: `reference_only_pending_upstream_rights_audit`

adapter의 coordinate parsing, frame-order, word-window join과 영어 입력 OOD 처리를
시험하는 데 참고할 수 있다. 한국어 phone/context calibration에는 사용하지 않는다.
HF 카드의 license tag만으로 원 GRID 영상의 동의 범위, 얼굴 파생물 처리와 재배포
권리가 모두 확인됐다고 간주하지 않는다.

### 3.5 Hugging Face MultiVSR — 비한국어 stress 참고

- 카드: [sindhuhegde/multivsr](https://huggingface.co/datasets/sindhuhegde/multivsr)
- 카드 표기: multilingual AVSR metadata/video, 13개 언어, MIT tag
- 배정 역할: `cross_language_ood_stress_candidate`
- 현재 판정: `reference_only_not_korean`

공개 언어 목록에는 한국어가 없다. 한국어 P6/P8 성능 자료가 아니라 비한국어
입력이 한국어 visual claim을 만들지 않는지 보는 OOD/fail-closed 참고 자료다.
원본이 제3자 web video에 의존하므로 카드의 MIT tag를 각 원영상의 얼굴·음성 사용
동의나 재배포 허가로 확대 해석하지 않는다.

### 3.6 추가로 확인했지만 채택하지 않은 항목

| 항목 | 판정 | 이유 |
|---|---|---|
| [westhnu/articulation-therapy-lipreading](https://huggingface.co/westhnu/articulation-therapy-lipreading) | `model_reference_only` | 한국어 117문장 lip-reading model 카드이며 독립 데이터 manifest·발음 오류·claim truth가 아님 |
| [KMSAV](https://github.com/etri/kmsav) | `future_external_robustness_candidate` | 한국어 spontaneous AV 참고 가치는 있으나 공개 split만으로 전역 speaker-disjoint를 증명하기 어려워 현재 P8 잠금 자료로 채택하지 않음 |

## 4. P6 실행 계약

후보 배정 뒤 다음을 모두 마쳐야 P6가 실제로 진척된다.

1. AI Hub 68의 접근 권한과 이 프로젝트 목적 사용·파생 artifact 조건을 확인한다.
2. portal manifest, 실제 schema, sample count와 file checksum을 보존한다.
3. `actor_id` 기준 development/calibration split을 결과 확인 전에 고정한다.
4. tracker·normalizer·cue extractor revision과 평가 기준을 고정한다.
5. 24-point annotation에 대한 normalized landmark error, missing/coverage,
   camera/age/sex slice와 동일 단어·화자 pose 반복성을 보고한다.
6. aperture·width·rounding·closure/timing 중 원자료로 검증 가능한 cue만 reference
   artifact에 넣는다.
7. 새 process에서 artifact reload와 hash/parity를 검증한다.

P6 산출물에는 최소한 dataset manifest/split, rights receipt, tracker/normalizer/cue
revision, reference/support/threshold hash, metric report, 실패 slice와 reload receipt가
있어야 한다. phone-level 발음 오류 label이 없으므로 `pronunciation_correct`,
`pronunciation_incorrect`, “입을 더 벌리세요” 같은 action truth는 만들지 않는다.

현재 `gate_evidence.py`의 P6 validator는 AI Hub 68 역할로 고정된 aggregate-only bundle에서
rights/admission assertion, actor+attempt disjoint split, pipeline revision/digest, 24-point
metric summary, calibrated artifact와 fresh-process reload summary의 exact digest/invariant를
검사한다. 성공해도 receipt는 항상 `evidence_validated_not_promoted`이며
`visual_reliability_authorized=false`다. rights와 reload는 제출된 assertion/digest 증거로,
validator가 실제 권리 상태나 별도 process 실행을 독립 생성·인증하지 않는다.

## 5. P8 잠금 자동평가 계약

P6의 모든 선택과 artifact freeze가 끝날 때까지 AI Hub 538 평가 partition은 열지
않는다. 실제 P8 실행 전 protocol에는 다음을 고정한다.

- 허용된 speaker/provenance 기준의 P6 자료와 교집합 검사
- 평가 전용 manifest·checksum과 read-zero/seal receipt
- angle/noise/capture slice, QC·ROI·tracker coverage와 failure reason
- A/V same-attempt binding, timestamp monotonicity와 sync failure
- `visual_feedback_ready`가 아닌 상태의 selective risk와 잘못된 claim 0 조건
- latency, peak memory, restricted-media deletion receipt
- AI Hub 539 또는 synthetic fixture에 알려진 offset/drift를 주입한 sync 회귀시험

538에는 landmark·발음 오류·action 정답이 없으므로 P8 결과는 자동 engineering
평가로만 보고한다. P8을 본 뒤 같은 protocol의 tracker, threshold, support 또는
catalog를 바꾸면 해당 결과는 더 이상 independent evaluation이 아니며 새 protocol과
새 잠금 자료가 필요하다.

현재 P8 validator는 AI Hub 538 역할로 고정된 summary replay에서 ready, missing,
low-confidence, OOD, conflict, unsupported의 6개 scenario와 exact binding/anchor/
calibration/catalog/policy digest, forbidden fixture, 모든 catalog claim 실행을 검사한다.
성공 receipt도 항상 `zero_violations_not_promoted`,
`user_visual_claims_authorized=false`다. read-zero seal은 supplied digest assertion일 뿐
validator가 잠금 상태를 생성하거나 인증하지 않는다. `validate_visual_gate_evidence.py`는
`p6|p8 --bundle`과 read-only `p6|p8 --bundle-fd`를 지원한다. 현재 test는 synthetic
privacy-safe fixture만 사용했으며 AI Hub 68/538 download/admission/metric과 P8 실실행
상태는 위의 `not_started` 그대로다.

## 6. data admission과 권리 gate

[AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105)은
신청 목적, 출처 표시, 제3자 제공, 국외 반출, 재식별과 이용 범위 조건을 둔다. 각
후보의 공개 페이지를 읽은 것만으로 다운로드 권한이나 개별 참여자의 얼굴·음성
파생처리 범위가 확인된 것은 아니다.

실제 원본을 열기 전 다음 항목을 fail-closed로 확인한다.

- 사용자 통제 AI Hub 승인 세션과 현재 dataset revision
- exact 이용약관, 연구/서비스 목적 적합성, 얼굴·음성·landmark 파생처리 범위
- 보존·삭제·접근자·국외 반출·제3자 제공·artifact 배포 조건
- manifest/checksum, annotation provenance와 알려진 결손
- development/calibration/locked 역할과 speaker/attempt 교집합 검사
- raw media, 경로, speaker identifier와 상세 landmark의 Git/LLM/public log 반입 금지

하나라도 미확인이면 후보 상태는 `candidate_blocked`이며 `admitted`로 승격하지 않는다.

## 7. 공개 데이터 근거와 claim-policy conformance의 분리

공개 데이터의 transcript, bbox 또는 keypoint만으로 발음 오류나 action truth를
새로 만들지 않는다. 대신 VE6는 다음 항목을 machine-checkable policy로 검사한다.

- 모든 candidate가 registered same-attempt binding, upstream phone/time anchor,
  observed cue와 calibration/support receipt를 정확히 참조하는가?
- catalog entry가 직접 보이는 lip cue allowlist와 동결 renderer key에만 매핑되는가?
- 혀·연구개·성문·기류·의학 원인처럼 지원하지 않는 target/claim이 0개인가?
- missing, low-confidence, OOD, conflict와 unsupported slice에서 candidate가 0개인가?
- `visual_feedback_ready`마다 candidate가 정확히 한 개이고 다른 상태에는 0개인가?

후보 배정은 이 검사를 완료했다는 뜻이 아니다. versioned policy, catalog digest,
금지 claim fixture, P8 locked replay report와 violation 0 receipt가 모두 있어야 VE6를
통과한다. 그 전에는 `claim_policy_conformance: not_started`와
`user_visual_claims: disabled`를 유지하며, 공개 가능한 결과는 QC·geometry·coverage와
fail-closed 측정에 한정한다. 이 자동 gate는 사용자 효능 연구를 대신하거나 그런
주장을 허용하지 않는다.

## 8. 다음 실행 순서

1. AI Hub 68/538/539의 사용자 통제 승인 세션에서 약관·파일 목록·revision을 확인한다.
2. 원본을 Git 밖 restricted storage에 내려받고 manifest/schema/consent-rights audit을
   수행한다.
3. blocker가 0인 자료만 `admitted`로 승격하고 68의 P6 split과 538의 P8 seal을
   결과 확인 전에 고정한다.
4. 작은 P6 tracker smoke와 reload를 먼저 통과시킨다.
5. calibration artifact를 freeze한 뒤에만 P8 잠금 평가를 한 번 실행한다.
6. 동결된 P8 결과를 claim policy/catalog에 재생해 VE6 conformance report를 만들고,
   violation 0과 receipt 무결성을 충족할 때만 허용 claim을 활성화한다.
