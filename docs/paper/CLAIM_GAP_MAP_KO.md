# 논문 주장·증거 갭 맵

- 최종 갱신: 2026-07-29 KST
- 대상 원고: `docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md`
- 상태: WP10/P00 분류 감사 완료, S101–S103와 R00 관측값 반영,
  S104 이후 실험·외부 평가는 미완료

## 1. 목적과 판정 단위

이 문서는 실험 진행 중인 원고, 실행계획, 연구 완성 추적기에 흩어진 핵심
수치와 주장을 다음 네 범주로 분리한다.

| 코드 | 범주 | 논문에서 허용하는 표현 | 금지하는 해석 |
|---|---|---|---|
| O | `observed engineering result` | 해시가 고정된 artifact에서 관측한 데이터·dev 지표·cache/resource·검증 상태 | locked-test 성능, 독립 전문가 gold, 배포 성능, 학습 효과 |
| G | `pre-specified gate/target` | 사전 분석·승격 기준·연구 질문·출력 계약·일정 | 달성값 또는 관측 효과 |
| R | `related-work statement` | 원문 source와 인용을 확인해야 하는 외부 연구·도구·설계 근거 | 본 연구의 실험 결과 또는 검증된 사실 |
| X | `external/PENDING` | 아직 수행하지 않았거나 외부 사람·승인·후속 artifact가 필요한 항목 | 임의 수치, 완료 판정, 효과 주장 |

한 문장에 관측값과 gate가 함께 있으면 각각 O와 G로 분리해 읽는다. 예를
들어 dev phone macro F1 `0.4902`는 O이고, engineering gate `0.60`은 G다.
Cache `completed`, validator `passed`, CUDA preflight `passed`도 O이지만
그 자체는 모델 품질·S101 head 완료·S108 승격을 뜻하지 않는다. 별도
완료 report와 registry로 검증된 S101/S102 head와 S103 comparison은
O21–O24로 분류하되 development-only·oracle/silver·no-promotion 제한을
함께 둔다. R00 텍스트 패키지는 O25로 분류하지만 TTS/audio·사람 평가
완료로 확대하지 않는다.

## 2. 증거 우선순위와 감사 기준선

현재 사실의 우선순위는 다음과 같다.

1. 해시가 고정된 aggregate artifact와 experiment registry
2. `docs/paper/evidence-map.yaml` 및 evidence validator
3. `docs/RESEARCH_COMPLETION_TRACKER_KO.md`의 현재 task 상태
4. `docs/IMPLEMENTATION_PLAN_KO.md`의 gate·프로토콜

실행계획 2장은 문서 자체가 “최초 계획 당시의 스냅샷”이라고 경고한다.
따라서 그 장의 ZIP 미해제·코드 미구현 등의 문장은 현재 상태 증거로
사용하지 않는다. 실행계획의 API JSON에 적힌 `0.96`, `0.93`, `0.72` 등의
숫자도 실제 응답이 아니라 출력 계약 예시다. 추적기의 가중 진행률과
synthetic/unit test 개수는 프로젝트 관리·코드 검증 상태이지 논문 성능
지표가 아니다.

감사 기준선은 다음과 같다.

- hashed aggregate artifact: 36개
- evidence-map claim: 475개
- unresolved `[PENDING: ...]` marker: 18개
- marker multiset SHA-256:
  `1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673`
- explicit dev/threshold/WavLM cache/Base+/Large head와 S103
  stack/calibration/locked-test 접근: 모두 `false`
- registry metric split: `dev`, `inner_val`, `dev_exploratory`
- manuscript SHA-256:
  `dea994728ae8c73be098a29e9a70af2ab4730a651063e64687b40c07433b17eb`
- evidence-map SHA-256:
  `1a4d897f3872a3a685be73667ac1e5676cd085711420f790e950f1b7af9e28be`
- validation 문서 SHA-256:
  `c40075548b70eac32efed6fe012db1ae8435652532858a0c49829ab9c07ee051`
- validation report SHA-256:
  `026799ab9ceb3d93128cc7c4558dcbe8d5ed4f0be3afefe19db198bb5e48b7bd`
- evidence validation/locked-test guard status: `passed`/`passed`

475개 claim의 개별 JSON Pointer·Parquet aggregate·반올림 규칙은
중복해서 옮기지 않고 `evidence-map.yaml`을 정본으로 삼는다. 아래 표는
그 claim들을 의미와 허용 범위가 같은 묶음으로 완전 분류한다.

## 3. 원고 절별 분류 커버리지

| 원고 위치 | O | G | R | X |
|---|---|---|---|---|
| 초록 | AI Hub/QC, explicit dev, 사후 threshold, Base+/Large head와 S103 dev | 0.60/0.50 engineering gate | 없음 | B05/S108, S203, R04/H04 |
| 1. 서론 | 현재 관측 결과 없음 | 일반화 범위와 네 연구 질문 | 기존 설계 보고서의 문제 정의·관련연구 | 추가 L1·외부 평가 |
| 2.1 데이터 | AI Hub·native catalog 수·시간·split | 학습자 오류 정답으로 쓰지 않는 역할 제한 | 외부 corpus와 모델 이름 | 원어민 외부평가·license 원문 감사 |
| 2.2 감독 label | phone/tag/score coverage | 미평정 0 mask 정책 | 없음 | 독립 전문가 gold |
| 2.3 누수 방지 | 화자/PCM/prompt/filter 관측값 | oracle/predicted 분리와 locked-test 금지 | 없음 | G04 전 locked test |
| 2.4 모델과 통계 | 구현된 explicit/threshold/cache, Base+/Large 3-seed head와 S103 paired bootstrap 방법·dev 결과 | 후속 adapter/계층/fusion/calibration 프로토콜 | WavLM/HiPAMA 계열 설계 근거 | B05, S104–S108 성능, S200–S203 |
| 3.1 데이터 품질 | 자동 G0·filter·cache label 분포 | QC gate | 없음 | 수동 G04 |
| 3.2 명시적 기준선 | dev metric·failure slice·사후 ablation | B06 gate | 없음 | B05 locked test |
| 3.3 WavLM | Base+/Large cache·head·registry, S103 비교와 Large 채택 gate 실패 | 후속 S104–S108 계약 | WavLM/계층·fusion 설계 | adapter·fusion·calibration·promotion |
| 3.4–3.6 | R00 CC0 문장 텍스트 패키지 | 후속 평가 설계 | 없음 | S2/metamorphic, R01–R04 코칭/TTS, H04 |
| 4. 논의 | explicit·Base+/Large/S103 dev와 threshold/oracle/silver 제한 | 후속 GOP/SSL·adapter 비교 질문 | 기존 PDF 주장 철회 | 인과·배포·교육 효과 |
| 5. 윤리·재현성 | 현재 hash/registry, Base+/Large/S103 source chain, R00 라이선스와 비식별 처리 | 데이터·run 보존 정책 | 없음 | IRB·동의·외부 녹음/평정 |
| 부록 | 없음 | 없음 | 없음 | hyperparameter, ontology, 실패 사례, license |

## 4. O — 관측된 engineering 결과

| ID | 핵심 관측값·주장 | 증거와 범위 | 반드시 남길 제한 |
|---|---|---|---|
| O01 | AI Hub 71490 `322,380`건, `1,830`화자, `344.208`시간과 5-way split | `aihub_data_card` | 데이터 cohort 실측이며 모델 효과가 아님 |
| O02 | Chinese `198,502`, Japanese `123,172`, `Zhuang; Chuang` `706`, gender `O` `2,736`, 8–192 kHz | `aihub_data_card` | 소수 slice와 범위 밖 집단을 일반화 근거로 쓰지 않음 |
| O03 | pairing `99.9996898%`, decoder failure `0%`, time-bound error `0.3319064%`, 화자/PCM 중복 `0` | `aihub_g0_audit` | 자동 G0만 통과; 수동 G04는 X |
| O04 | native 4-source `38,729` object, `118.519`시간과 source별 `2,915/12,854/240/22,720` | `native_report` | 외부평가용 catalog 구축값이며 외부평가 결과가 아님 |
| O05 | 목표 phone 38종, free-form realized 표기 1,715종, tokenizer coverage `99.9682%`, 오류 tag 17종·176,523구간 | `aihub_data_card` | 독립 전문가 gold가 아닌 annotation inventory |
| O06 | 발음/이해도 유효 score 각 `322,337`, 유창성 `81,780` | `aihub_data_card` | 원본 0 sentinel을 관측 점수로 해석하지 않음 |
| O07 | prompt 229개, 예약 20개, challenge 5,522건, 다른 split 예약 prompt 21,240건 제외 | `prompt_challenge` | 예약 정책과 cohort disposition |
| O08 | source 322,380 → split 제외 185,751 → QC 제외 185,109 → 예약 prompt 제외 169,837; train/dev `137,759/32,078` | explicit cache reports와 P01 표·flow | train/dev cache cohort이며 locked test가 아님 |
| O09 | oracle phone cache 2,233,039 interval, silver 정상/오류 `2,088,825/144,214`(`93.542/6.458%`) | `phone_cache_report`, `phone_cache_parquet` | gold target boundary 입력과 silver 오류 정답을 구분 |
| O10 | explicit primary-dev phone macro F1/AUPRC `0.4902/0.1613`, all-dev F1 `0.4901` | `explicit_dev_report` | provisional dev engineering failure; B05가 아님 |
| O11 | utterance QWK/Spearman/MAE/ICC와 최고 fluency Spearman `0.4780` | `explicit_dev_report` | oracle alignment·dev 결과이며 배포 성능이 아님 |
| O12 | class metric, 오류 prevalence·예측률, L1/gender/proficiency/phone worst slice | `failure_analysis_json` | 최소 100건 aggregate, silver label 기준 |
| O13 | post-hoc threshold `0.68/0.67/0.68`, dev F1 `0.5588/0.5621/0.5588`, 평균 `0.5599±0.0019`, AUPRC `0.1613` | `threshold_ablation_report` | 탐색적 operating-point 조정; B06 `failed_unchanged` |
| O14 | Base+ pinned revision, 16 kHz·20초 chunk, 768/1,536차원 cache 계약 | `wavlm_base_cache_report` | feature 생성 방법이며 head 성능이 아님 |
| O15 | Base+ cache `169,837/2,233,039/408,790`행, 31 shards, 4,427,846,661 bytes, 2,289,794,304 scalar 전수검증 | cache report와 validation report | `completed/passed`는 cache 완결성만 뜻함 |
| O16 | non-finite/all-zero/forbidden/error `0`, decoded `178.998`시간, elapsed `3,927.819`초, OOM `0`과 peak resource | cache report와 validation report | 해당 머신·run의 resource 관측값 |
| O17 | synthetic tensor CUDA head preflight `passed` | `wavlm_head_cuda_smoke` | cache/dev/locked-test 미접근; 성능 결과 아님 |
| O18 | explicit·threshold·cache, Base+/Large head와 S103 config/source/report/registry/commit SHA 및 run ID | evidence-map의 artifact SHA claim | lineage 증거이며 품질 gate 통과 주장이 아님 |
| O19 | P01 데이터 QC 표, cohort flow source·검증 SVG와 자료 license ledger가 36-artifact evidence chain에 포함됨 | `paper_aihub71490_data_qc_table`, `paper_aihub71490_cohort_flow`, `paper_aihub71490_cohort_svg`, `data_license_source_ledger` | SVG 시각 감사는 통과했지만 최종 원고 PDF와 미해결 license 권리 판단은 별도 |
| O20 | H01 planning artifact가 assumed sensitivity 243건을 생성함 | tracker H01과 planning artifact | pilot 0, `final_sample_size=null`, 완료 주장 `false`; 학습 효과가 아님 |
| O21 | S101 Base+ frozen head seeds 42/43/44 완료; primary-dev phone macro F1 `0.669699±0.001629`, AUPRC `0.353243±0.002218` | `wavlm_base_head_report`, completed registry, 등록 artifact 13개 | oracle target boundary와 silver label의 development-only 결과; 표본 SD는 CI가 아니며 promotion·배포·우월성 근거가 아님 |
| O22 | Base+ utterance QWK/Spearman/MAE/ICC(2,1): pronunciation `0.098042/0.185194/0.837445/0.058316`, fluency `0.037078/0.199315/0.850812/0.043062`, comprehensibility `0.080252/0.155166/0.791802/0.052146` | `wavlm_base_head_report`; train-only selection 뒤 seed당 dev 1회, stack/calibration/locked-test 미접근 | oracle-alignment 발화 평정의 provisional engineering 값이며 독립 gold·locked-test·일반화·promotion 결과가 아님 |
| O23 | S102 Large frozen head seeds 42/43/44 완료; primary-dev phone macro F1 `0.671164±0.000443`, AUPRC `0.360830±0.000669`; 발화 지표도 같은 dev 범위에서 집계됨 | `wavlm_large_head_report`, completed registry, 등록 artifact와 S103 입력 hash chain | oracle target boundary와 silver label의 development-only 결과; Base+보다 큰 점추정만으로 승격하지 않음 |
| O24 | S103 Large−Base+ phone macro F1 `+0.001466`, 화자 bootstrap 95% CI `[0.000006, 0.002990]`, p=`0.0491902`; 사전 `+0.03` gate 실패 | S103 comparison commit/report/receipt와 strict results-table JSON/Markdown | dev-only model selection이며 실용적 gate 실패; stack/calibration/locked-test와 promotion 접근 0 |
| O25 | R00에서 project-original CC0-1.0 문장 20개를 고정했고 고유 hash 20, 초성/관측 모음/받침군/음운 규칙/운율 target `19/18/7/8/11`, 예약 prompt 교집합·PII·금칙 match `0/0/0`, validator `valid` | `r00_sentence_validation` | 텍스트·선정 규칙·라이선스 완료만 뜻하며 TTS/audio·원어민·MOS/CMOS·교육 효과는 미완료 |

## 5. G — 사전 gate·목표·가설·프로토콜

| ID | gate/target/protocol | 현재 판정 |
|---|---|---|
| G01 | 데이터 pairing ≥99%(목표 99.5%), parse 100%, time-bound <1%, ontology 미매핑 ≤1% | 자동 항목의 관측값은 O03; 수동 정렬 ≥95%는 X01 |
| G02 | 명시적 기준선 phone macro F1 ≥0.60, utterance Spearman ≥0.50 | dev 관측 O10/O11이 미달; gate를 낮추지 않음 |
| G03 | phone smoke 0.50 | 사후 threshold가 넘었어도 사전 engineering 0.60 실패와 승격 불가는 유지 |
| G04 | 후속 모델 seed 42/43/44, 화자 bootstrap 5,000회 95% CI, Holm, 지정 metric | Base+/Large 3-seed와 S103 primary paired-speaker bootstrap은 O21–O24; S104 이후는 X04 |
| G05 | S1 phone F1 ≥0.75(목표 0.80), utterance ICC ≥0.65(목표 0.70), word PCC ≥0.65(목표 0.70), ECE ≤0.10, L1 gap ≤0.10 | S101/S102 O21–O23은 phone/ICC gate 미달·promotion 불가; S104–S108은 X04 |
| G06 | Large 채택: ICC +0.03 또는 phone F1 +3%p, worst subgroup 하락 <0.05 | S103 primary phone gate는 O24에서 실패해 S104 backbone을 Base+로 고정; 다른 outcome 양상을 전체 우월성으로 일반화하지 않음 |
| G07 | S2 recall ≥0.80, false-high-score 30% 감소, 정상 referral ≤15%, 불일치 적중 ≥60%, metamorphic ≥95% | S200–S203/E01은 X05/X09 |
| G08 | coaching unsupported <2%, 위치·유형 정확도 ≥90%, 저신뢰 재녹음 recall ≥95% | C03 외부 감사 X06 |
| G09 | 문장당 후보 8–16개·exemplar 3–5개, transcript error ≤2%, 정상 오탐 20% 감소, blind CMOS 우위 | R00 텍스트 고정만 O25; R01–R04는 X07 |
| G10 | C0↔C3, C3↔C5 병렬 비교, 2주 post와 4주 retention, blind comprehensibility, ITT/mixed model/Holm | H01은 planning-only; IRB·pilot·H04는 X08 |
| G11 | 조건당 15–20명은 feasibility 범위 | powered final N이 아니며 현재 `final_sample_size=null` |
| G12 | GRPO 진입: S1 F1 ≥0.80, ICC ≥0.70, S2 recall ≥0.80, blind 우위, 학습 효과 신호 | 선행 X04/X05/X07/X08 전에는 진입 불가 |
| G13 | 실행계획의 API JSON 숫자와 13주/4–6개월/6–12개월 일정 | 예시 출력과 계획 추정치; 관측 응답·완료 기간이 아님 |
| G14 | 연구 질문 4개와 중국어·일본어 L1로 제한한 1차 일반화 범위 | 가설/범위 선언이며 채택·기각 결과가 아님 |

## 6. R — 관련연구·외부 도구·설계 근거

1차 자료 감사 결과는
[관련연구 source ledger](RELATED_WORK_SOURCE_LEDGER_KO.md)에 고정했다.
논문·공식 저장소·실제 LICENSE 36개 URL을 중복 없이 확인했지만, 외부
source는 구성요소와 선행 범위만 지지하며 본 연구의 한국어 L2 성능이나
완료 상태를 대신 증명하지 않는다. 아래 `unverified` 주장은 계속 사실
문장으로 승격하지 않는다.

| ID | 현재 등장하는 statement | 현재 증거 상태와 필요한 조치 |
|---|---|---|
| R01 | WavLM frozen/large와 layer-wise representation을 후속 후보로 두는 설계 판단 | `partially_verified`: WavLM·Large·layer weighted representation은 확인; 한국어 L2 개선과 결합 구조의 유효성은 미검증 |
| R02 | “HiPAMA식 phone→word→utterance attention” | `partially_verified`: 논문·공식 코드·BSD-3-Clause 확인; 정확한 표현은 계층 표현과 level별 multi-aspect attention이며 영어 SpeechOcean762 범위 |
| R03 | Whisper/HuBERT를 S2 독립 감사 후보로 쓰는 역할 분담 | `partially_verified`: 원 task/family는 확인; 현재 cohort WER·독립 auditor 성능은 미검증 |
| R04 | CosyVoice 2 이후 계열과 GRPO 지원이 공개됐다는 설명 | 공개 존재만 `verified`: official CosyVoice2 GRPO recipe 확인; TTS·학습 효과는 미검증 |
| R05 | pure minimum distance가 outlier reference를 선택할 수 있어 k-nearest median/trimmed soft-min을 쓴다는 근거 | `unverified`: 인접 선행 사용만 확인; 후보 집계의 우월성은 실제 ablation 필요 |
| R06 | F5 계열 코드와 가중치 license가 다를 수 있다는 설명 | 공식 SWivid artifact에 한해 `verified`: code MIT, pretrained weight 선언 CC-BY-NC-4.0; 제3자 artifact 일반화 금지 |
| R07 | UTMOS가 L2 억양을 과도하게 벌점할 수 있다는 위험 | `unverified`: OOD 위험은 확인했지만 한국어 L2 과벌점 직접 근거 없음 |
| R08 | 기존 PDF의 “정확도 상한 확장”, “온라인 루프 확보” | `unverified`: 외부 인용으로 복구할 수 없으며 후속 내부 artifact 전에는 X 유지 |

## 7. X — 외부 의존·미실시·PENDING

| ID | 미해결 주장 | tracker의 정확한 현재 상태 | 해제 조건 |
|---|---|---|---|
| X01 | G04 수동 정렬 정확도 ≥95% | 외부 의존, 0/240 | 한국어 음성학 전문가 판정 원본 |
| X02 | D07 독립 gold와 ICC/QWK | 외부 의존 | 전문가 2인, 1,000–2,000 발화 재평가 |
| X03 | B05 locked-test confusion/slice/bootstrap/Holm | 대기 | G04 후 locked test 1회와 전용 aggregate report |
| X04 | adapter/HiPAMA/fusion/calibration/S108 비교·승격 성능 | S101–S103은 O21–O24로 완료; S104 Base+ adapter 3-seed 정본 GPU 학습 실행 중, S105–S108 실제 결과 0 | S104 completed commit과 S105–S108 ablation·calibration·promotion report |
| X05 | S2 독립 감사 결과 | S200–S203 대기 | 독립 backbone/정렬/holdout/목표와 전문가 결과 |
| X06 | coaching 위치·유형·unsupported claim | C03 외부 의존 | 200건 전문가 감사 |
| X07 | reference/TTS MOS·CMOS와 단일 대비 우위 | R04 외부 의존 | 동의된 평가 원본과 blind 통계 |
| X08 | IRB, 모집, 학습자 녹음, post/retention, 학습 효과 | H00/H02/H03 외부 의존, H04 대기 | 승인·동의·실제 참여자·blind rating·mixed-model report |
| X09 | silence/gain/duration/F0/phone metamorphic 결과 | E01 대기 | 실행 artifact와 ≥95% 판정 |
| X10 | 전체 hyperparameter, ontology, 실패 사례, S1/S2 disagreement, license 부록 | 부록 marker 유지 | 각 고정 artifact와 citation/license 감사 |
| X11 | 중국어·일본어 L1 밖 일반화와 배포 성능 | 추가 L1·외부평가 없음 | 별도 데이터와 predicted-alignment 외부 평가 |

## 8. PENDING marker 계약

정규 marker 18개 중 1개는 편집 규칙을 설명하는
`[PENDING: ...]` 예시이고, 실제 미해결 내용은 17개다.

| 원고 위치 | marker 대상 | 분류 |
|---|---|---|
| 초록 | B05/S108, S203, R04/H04 | X03/X04, X05, X07/X08 |
| 방법 | B05 절차, S101–S108 정의(S101–S103 실행값은 O21–O24, 남은 실행은 S104–S108), S200–S203 정의 | X03, X04, X05 |
| 데이터 품질 | G04 | X01 |
| 명시적 기준선 | B05 confusion/slice/bootstrap/Holm | X03 |
| WavLM | S101–S108 최종 표(S101–S103 관측값은 O21–O24), S107 reliability/risk-coverage | X04의 S104–S108 |
| 독립 감사 | S203/E01 | X05/X09 |
| 코칭·참조 | C03/R04 | X06/X07 |
| 학습 효과 | H04 | X08 |
| 부록 | hyperparameter, ontology, 실패 사례/disagreement, license | X10 |

이번 S101–S103/R00 관측값 분류 갱신은 marker의 문구·개수·정렬된
multiset hash를 바꾸지 않는다.
실제 artifact가 생기기 전 marker를 수치로 치환하지 않는다.

## 9. 표현 혼동 감사와 최소 교정

다음 경계를 원고에 명시적으로 보강했다.

1. 연구 전체를 이미 검증했다는 인상을 피하도록 초록의 첫 문장을 연구
   목표로 표현했다.
2. Gold target boundary는 입력 경계이고 오류 정답은 silver label임을
   초록에서도 분리했다.
3. seed/bootstrap/Holm을 완료 결과가 아니라 후속 비교의 사전 분석
   프로토콜로 명시했다.
4. 데이터 품질 표의 `결과` 열을 `관측/상태`로 바꿔 G04의 `PENDING`과
   gate를 구분했다.
5. “최종 cache”를 “완결된 train/dev cache”로 바꿔 final 성능과 분리했다.
6. phone `0.50/0.60`을 사전 지정 gate로, dev metric을 관측값으로
   분리했다.
7. “실제 오류 비율”을 “silver 오류 label 비율”로 고쳐 독립 gold 또는
   배포환경 prevalence로 읽히지 않게 했다.
8. “사전 기준선”을 “사전 실행한 기준선 관측값”으로 고쳐 target과
   observed baseline을 분리했다.
9. 제한 특징이 실패 원인이라는 단정을 후속 ablation이 필요한 해석으로
   낮췄다.
10. IRB·학습자·외부 평가 절차는 현재 완료 결과가 아님을 명시했다.
11. S101 Base+ 3-seed head를 O로 추가하되 development-only,
    oracle/silver, 표본 SD 비-CI, no-promotion 제한을 결합하고
    당시 후속 비교·승격 결과는 X에 남겼다.
12. S102 Large와 S103 paired comparison을 O23/O24로 옮기고 실제
    `+0.03` gate 실패와 Base+ adapter 분기를 기록했으며 S104–S108은
    X04에 유지했다.
13. R00 문장 텍스트 패키지를 O25로 연결하되 TTS/audio·사람 평가·
    MOS/CMOS 결과와 분리했다.

## 10. P00 판정과 남은 일

P00의 완료 기준인 “기존 목표값·주장을 결과/가설/관련연구로 구분하고 paper
gap map을 남긴다”는 이 문서와 S101 분류 갱신으로 계속 충족한다. Source
ledger의 직접 지원 범위 원고 반영과 P04 첫 evidence 편집 묶음은 이미
완료했으며 남은 일로 다시 세지 않는다. 다만 이는 논문 전체 완료가
아니다. 특히 다음은 계속 남는다.

- X01–X03·X05–X11의 실제 artifact·외부 평가와 X04의 S104–S108 결과
- S104 이후 후속 수치를 evidence-map에 연결하고 최종 표를 만드는 P02/P03
- 후속·외부 artifact가 필요한 P04 C 등급 7개 갱신
- 표·그림 렌더링, 인용, 최종 PDF의 독립 P05 감사

이 판정은 WP10 전체 완료나 B05/G04/S108/H04 완료의 근거가 아니다.
Tracker의 P00 완료와 가중 진행률 반영은 별도 관리 절차에서 수행한다.
