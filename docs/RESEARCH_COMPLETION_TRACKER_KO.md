# 연구 완성 하위 작업 추적기

최초 작성: 2026-07-29 KST
최종 갱신: 2026-08-03 KST (현재 코드 기준 S104 v2 contract·full
preflight와 v1 source-pin 불일치 판정 반영)
상태: 실행 중

## 현재 개인 연구 범위 정책 (2026-08-03)

현재 저장소의 성인 전문가·원어민·청취자 평가는 기관 소속 참여자 연구가
아닌 개인 연구다. 따라서 D07/D08/C03/R01/R04의 선행 gate로 기관/IRB 승인을
요구하지 않는다. 대신 개인 연구 scope attestation, AI Hub 및 recording
license/약관, 평가자·화자의 개별 동의·철회·보존·폐기, 가명키 분리, R0/R1/D1/A1
privacy, blind, preregistration, QC와 독립 검증을 유지한다.

H00–H04의 신규 참여자 모집·중재·RCT·학습자 pre/post/retention 경로를
선택할 때만 기관/IRB 및 참여자 동의 gate를 적용한다. 아래 기존 표와
과거 실행 로그에 남은 H00·기관 승인 표현은 해당 참여자 연구 경로 또는
레거시 v1 executor의 상태를 기록한 것이다. 현재 개인 연구 평가를 실제로
열었다는 뜻이 아니다. D07/R04/C03/D08 v1 CLI/config가 여전히 H00 승인
검사를 수행하므로 개인 연구 executor로 전환하는 별도 contract amendment
전에는 사람 평가·음성 수집을 시작하지 않는다.

## 1. 최종 목표

`한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처`의 설계를 실제
데이터와 재현 가능한 실험으로 검증한다. 필요한 데이터를 수집·검증하고,
진단 모델과 후속 모듈을 학습·평가한 뒤, 실제 결과 표·그림·통계·한계를
논문에 반영한다.

계획값이나 목표값을 실험값으로 바꾸어 쓰지 않는다. 사람의 판단이 필요한
전문가 감사, blind 평가, 학습자 파일럿/RCT는 실제 참여자와 평가자가
확보된 뒤에만 완료로 판정한다.

## 2. 진행률 계산 규칙

- 각 work package의 가중치 합은 100이다.
- `완료`는 명령, checksum, metric report 또는 사람 평가 원본 등 재현
  증거가 있고 완료 기준을 모두 만족할 때만 부여한다.
- `진행 중`은 실행 가능한 산출물이 생성됐으나 gate가 남은 상태다.
- `외부 의존`은 IRB, 참여자, 한국어 음성학 전문가, 원어민 녹음처럼
  저장소 안의 자동 실행만으로 끝낼 수 없는 상태다.
- 실패한 gate도 삭제하지 않고 결과와 수정 이력을 기록한다.
- `진행 중`, `실패(개발)`, `대기`, `외부 의존`이라는 상태 자체에는
  점수를 주지 않는다. 아래 원장에 분리해 적은 milestone 중 checksum,
  완료 report, registry 또는 사람 평가 원본으로 완료가 입증된 것만
  획득 점수에 더한다. 개발 지표의 높고 낮음도 artifact 완료 점수를
  바꾸지 않는다.
- `T01`은 이 추적기를 계속 최신으로 유지하는 maintenance task이며
  별도 점수가 없다. 묶음 milestone은 표에 적힌 모든 항목이 완료돼야
  해당 점수를 한 번 부여한다.

### 고정 배점·획득 원장

| WP | 고정 milestone 배점 | 현재 완료 근거 | 획득 |
|---|---|---|---:|
| WP0 (5) | T00 1; T02 2; T03 2; T01 유지보수 0 | T00 1 + T02 2 + T03 2 | 5 |
| WP1 (12) | D01+D06 5; D02–D05+D10 5; D07–D09 2 | D01+D06 5 + D02–D05+D10 5 | 10 |
| WP2 (10) | G00–G01 4; G02–G03 3; G05–G06 2; G04 1 | G00–G01 4 + G02–G03 3 + G05–G06 2 | 9 |
| WP3 (12) | B00–B02 3; B03–B04 3; GOP/SSL 2; B05 2; 최종 gate 2 | B00–B02 3 + B03–B04 3 + B07 GOP/SSL 2 | 8 |
| WP4 (18) | S100 1; S101 cache 2; S101 head 2; S102 4; S103 2; S104 2; S105 2; S106 1; S107 1; S108 1 | S100 1 + S101 cache 2 + S101 head 2 + S102 4 + S103 2 | 11 |
| WP5 (8) | S200 1; S201 2; S202 3; S203 2 | 없음 | 0 |
| WP6 (7) | C00 1; C01 1; C02 3; C03 2 | C00 1 | 1 |
| WP7 (10) | R00 1; R01 2; R02–R03 3; R04 3; R05 1 | R00 1 | 1 |
| WP8 (5) | E00 2; E01 2; E02 1 | 없음 | 0 |
| WP9 (8) | H00 2; H01 실제 pilot+수동 lock 2; H02 1; H03 2; H04 1 | 없음; H01 planning-only artifact는 실제 pilot+lock이 아님 | 0 |
| WP10 (5) | P00 1; P01 1; P02+P03 최종 표 1; P04 첫 evidence 개정 1; P05 최종 감사 1 | P00 1 + P01 1 + P04 첫 evidence 개정 1 | 3 |
| **합계** | **100** | **완료 milestone만 합산** | **48** |

현재 가중 진행률: **48/100**
현재 최우선 경로: **진행 중인 v2 S104 GPU 3-seed를 terminal commit까지
완료하고 독립 검증 → v2 commit에 맞춘 S105 re-pin/계층 ablation**과
**검증 완료한 B07을 S106 4-component 계약에 편입하는 amendment**를 병행한
뒤 S106 정본 실행

## 3. 단계별 현황

| ID | Work package | 가중치 | 획득 | 상태 | 핵심 남은 조건 |
|---|---|---:|---:|---|---|
| WP0 | 재현 환경·계약·추적 | 5 | 5 | 완료 | 후속 trainer registry 연결 상태를 계속 감사 |
| WP1 | 데이터 수집·라이선스·카탈로그 | 12 | 10 | 진행 중 | 전문가 holdout, 원어민 reference, 파일럿 데이터 |
| WP2 | 정규 manifest·split·G0 | 10 | 9 | 진행 중 | G04 1차 240건 감사 완료(74.17%, gate 미달); 원인 분석·phone-level accuracy 도입·30건 재감사 진행 중 |
| WP3 | 명시적 특징·GOP 기준선 | 12 | 8 | 진행 중 | B05 joint one-shot evaluator, G0·D07·S108 뒤 최종 locked gate |
| WP4 | S1 WavLM·계층 모델·fusion | 18 | 11 | 진행 중 | Base+ adapter·계층 ablation·fusion·보정 |
| WP5 | S2 독립 감사기 | 8 | 0 | 대기(실제 S2) | S200–S203 정적 preflight 준비는 완료; S108 final gate와 독립 holdout·사전등록 뒤 실제 S201–S203 실행 |
| WP6 | 근거 제한 코칭 | 7 | 1 | 진행 중 | 200건 전문가 정확도/안전 감사 |
| WP7 | 다중 참조음·TTS Loop A | 10 | 1 | 진행 중 | R01 외부 human/CosyVoice 승인·실제 후보 생성, R02–R03 gate/Pareto, R04 blind 평가 |
| WP8 | 폐쇄루프·metamorphic·API | 5 | 0 | 대기(정적 준비 완료) | S108/C02/C03/R03/D08 actual 선행 뒤 한 사이클 demo·metamorphic 95%·API guard 실행 |
| WP9 | 전문가 평가·파일럿/RCT | 8 | 0 | 외부 의존 | 개인 연구 전문가 평가는 scope attestation·동의·license; 참여자 pilot/RCT는 IRB/동의/모집/사전-사후-유지 평가 |
| WP10 | 통계·표·그림·논문 개정 | 5 | 3 | 진행 중 | 모델·후속/외부 결과 표와 최종 PDF |

## 4. 실행 가능한 하위 작업

### WP0. 재현 환경·계약·추적

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| T00 | 완료 | GB10/CUDA/Python/PyTorch 실측 | `artifacts/hardware/nvidia-gb10/` | 실험 report에 자동 포함 |
| T01 | 진행 중 | 논문 요구사항과 실행 DAG 단일화 | 이 문서 | 감사 결과 반영 |
| T02 | 완료 | exact lock 일치 + 별도 clean 환경 재생성 검증 | `artifacts/reproducibility/scorer_clean_recreation_20260729.json` (SHA-256 `76ffff9b…`), 외부 lock 79/79 일치, CUDA 13.0 실제 연산과 최신 source 재설치 후 당시 clean 272-test suite 통과 | 후속 테스트 추가 시 같은 clean 환경에서 회귀 재검증 |
| T03 | 완료 | run ID, config/data/model hash, seed, metric을 한 report에 저장 | `src/voice_coach/experiments/registry.py`, 단위 테스트 | 모든 trainer에 연결 |

### WP1. 데이터 수집·카탈로그

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| D01 | 완료 | AI Hub 71490 발음 WAV/JSON/TextGrid 322,380쌍 | raw QC와 checksum | 변경 금지 |
| D02 | 완료 | FLEURS 2,915 WAV 검증 | native corpus verify + 통합 manifest | 외부 ASR·정렬 평가에만 연결 |
| D03 | 완료 | KSS 12,854 embedded audio 검증 | native corpus verify + 통합 manifest | 비상업 TTS 경로에만 연결 |
| D04 | 완료 | Seoul Corpus 240 FLAC/TextGrid 검증 | native corpus verify + 통합 manifest | 비상업 정렬 외부평가에만 연결 |
| D05 | 완료 | ZEROTH 22,720 FLAC/전사 검증 | native corpus verify + 통합 manifest | ASR 외부평가에 연결 |
| D06 | 완료 | 322,380건 분포·label coverage·QC data card | `DATA_CARD_AIHUB71490_KO.md` | 라이선스 원문 링크 보강 |
| D07 | 실행 준비 완료(정적 preflight 100%·실제 전문가 평가 0%) | 독립 전문가 2인, 1,000–2,000 발화 재평가 | config/item·rating·calibration·locked·completion schema/기관승인·reviewer·preregistration·one-shot authorization template/contract/CLI/card/readiness SHA `dc6f7415…1a16`/`756d8e95…4546`/`e9c23383…b42f`/`fe7ae401…29ce`/`c5a352fa…6a2e`/`1b9dcb49…910f`/`3e8a0c63…bc4d`/`fddc578c…53c2`/`2e1bc517…c79`/`8479443a…a3e6`/`77ad2f1a…1a95`/`869f952f…d98b`/`38b65bfc…be1`/`45130ecb…1c83`; H00 actual approval에서 fail-closed, 실제 expert/rating/audio/locked/metric/result 0, D07 획득 0 | H00 실제 승인·D07 기관 승인 뒤 G04 240건을 먼저 완료하고 별도 800 calibration gate를 통과한 후 최대 1,200 locked item을 one-shot 평가 |
| D08 | 실행 준비 완료(정적 preflight 100%·실제 모집·녹음 0%) | 20개 문장 원어민 다화자 reference 녹음 | config/R1 metadata·D1 QC·A1 completion schema/H00·consent·rights·QC-prereg template/contract/CLI/card/readiness SHA `4f05a431…239e`/`6ae7bf0c…364e`/`4064c226…6aa6`/`1ae8e5d3…c59c`/`88321e78…93b1`/`ae8bee17…c193`/`89d02baf…e7ed`/`e1d7b3a6…7472`/`1082147d…0549`/`f4a711e7…bbf8`/`cd7d3d45…d2c1`/`4c5a1b99…6279`; R00 deep 뒤 H00 TBD에서 path/read 0으로 fail-closed, 실제 external/audio/mic/QC/output 0, D08 획득 0 | H00 승인·개별 동의·녹음권리·QC 사전등록을 실제 동결하고 원어민 모집·녹음·청취 QC 수행 |
| D09 | 외부 의존(정적 실행 준비 100%·실제 수집 0%) | feasibility와 별도 powered confirmatory 학습자 데이터 | 2-phase/2-comparison strict schema·prereg/manual/auth template·delivery DAG·contract/CLI/card/readiness; config/policy/contract/CLI SHA `1bb10f98…d92b`/`21688508…6d2`/`3be8cfe6…709d`/`14c86c5b…befe`; H00 TBD에서 protected path/read와 human/model/API/intervention/rating/stat/output 0 | 실제 H00와 S108/E01/C02/E02/C03/E00/D08-lineage/R03/R04를 완료하고 phase별 수동 prereg/manual/auth를 동결한 뒤 feasibility 모집 |
| D10 | 완료 | native 4-source 38,729건·118.519시간 통합, privacy/license gate 통과 | `DATA_CARD_NATIVE_KOREAN_KO.md`, manifest/report hash | 제한 manifest는 R1 유지, 공개 report만 A1 검토 |

#### D07 준비도와 실제 독립 전문가 gold 평가 분리

정책 보정: D07 성인 전문가 평가는 개인 연구이므로 기관·IRB 승인 대신
개인 연구 scope attestation, 라이선스, 평가자 동의·자격 attestation을
요구한다. 아래 표의 H00/D07 기관 승인 문구는 참여자 연구 경로나 레거시
executor의 정적 상태를 가리키며, 실제 D07 평가 0%와 실행 계약 amendment
미완료 상태는 그대로다.

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; G04 240건과 별도 D07 1,000–2,000건, calibration 800+locked 최대 1,200, 정확히 독립 primary 2인과 disagreement-only adjudicator, calibration gate→one-shot locked 순서를 강제하는 package 완료 | config-only source 25건·G04 aggregate 1건 외 H00/external/D1/R1/audio/rating/locked/model/GPU/network/metric/output 0; full은 H00 actual approval TBD에서 모든 보호 path/read 0으로 종료; targeted 17개, 관련 회귀 166개와 전체 1,091개, Ruff/format/mypy 통과 | 0 |
| D07 연구 완료 | 0%; H00·D07 기관 승인, 실제 전문가·표집·평정·calibration·locked access·agreement·gold completion이 없음 | expert/rating/audio/locked/metric/result/registry/commit/output counter 모두 0 | D07–D09 묶음 0/2 |

Calibration은 scalar ICC/QWK `>=0.70` 또는 taxonomy agreement
`>=0.65`를 요구하고, 실패하면 재교육과 별도 재표집 후 다시 통과해야
한다. Exact allocation·자격 증빙·disagreement trigger·통계량·CI·cluster·
multiplicity·support·one-shot recovery는 승인된 frozen preregistration
전까지 TBD다. 이를 임의로 채우지 않으므로 WP1 10/12와 전체 48/100을
유지한다.

#### D08 준비도와 실제 외부 데이터 수집 분리

정책 보정: D08 원어민 녹음은 개인 연구 scope attestation, 개별 동의,
recording license, QC preregistration을 요구하며 기관 승인을 선행 gate로
요구하지 않는다. 아래 표의 H00 승인 문구는 참여자 연구 경로나 레거시
executor의 정적 상태를 가리키며, 실제 수집 0%는 그대로다.

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; R00 actual→H00 승인→개별 동의→녹음권리→QC preregistration→R1/D1/A1 순서와 48kHz/24-bit mono·자연 2+명료 1 take·clipping<0.001·decoder0·human content 확인을 강제하는 package 검증 완료 | config-only source 19건 외 runtime/external/output 0; full은 R00 deep 1·public/aggregate 33·raw/row 0 뒤 H00 TBD에서 path/read 0으로 종료; targeted 17개, 관련 회귀 162개와 Ruff/format/mypy 통과 | 0 |
| D08 연구 완료 | 0%; 실제 기관 승인·동의·원어민·recording session/audio·human QC·completion이 없음 | 모집/동의/녹음/audio/mic/model/network/GPU/QC/result/commit/output counter 모두 0 | D07–D09 묶음 0/2 |

문장당 4명은 현재 planning target이며 binding floor가 아니다. Exact
DC/content/noise/reverb/interruption metric·threshold와 adjudication도
승인된 preregistration 전까지 TBD다. 이를 임의로 채우지 않으므로 WP1
10/12와 전체 48/100을 유지한다.

### WP2. Manifest·split·G0

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| G00 | 완료 | 322,380건 3-modality 결합, parse 실패 0 | raw manifest/QC | 고정 |
| G01 | 완료 | speaker-disjoint 5-way split, 교집합 0 | split hash | 고정 |
| G02 | 완료 | PCM cross-split 중복 0 | enriched audit | 고정 |
| G03 | 완료 | pairing≥99%, decoder≤0.1%, time-bound≤1% | G0 audit JSON | 고정 |
| G04 | 실패(1차 감사 완료·95% 미달) | 240건 수동 정렬 정확도≥95% | 240/240 실제 전문가 판정 완료, `alignment_accuracy=0.7417`(178/240), `status=failed`; scoring TSV/review report/result SHA `05a4fc2b…d17`/`add6942a…e17`/`411eb721…d17`; 실패 분석 report SHA `1bda1868…19d`(층화·operation별 실패율, 검수자 메모); phone-level accuracy 지표 도입(legacy 240건은 미수집이라 null); duration≥5초 30건 재감사 표본 신규 생성(metadata SHA `452299fc…5d2`, 검수 진행 중) | 30건 재감사 완료 확인, phone-level accuracy 채택 여부와 최종 G04 gate 정의는 연구책임자 결정 필요 |
| G05 | 완료 | 20개 prompt 예약, challenge 5,522건 | challenge JSON/hash | 학습 filter에 연결 |
| G06 | 완료 | 17종 error ontology 미매핑 0, phone coverage 99.9682% | data card/taxonomy tests | unknown audit 유지 |

### WP3. 명시적 특징·기준선

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| B00 | 완료 | duration/RMS/clipping/ZCR/pause/F0/nPVI 함수 테스트 | unit tests | 유지 |
| B01 | 완료 | 322,380건 제한구역 모델용 label/split manifest, 0 sentinel 격리 | modeling Parquet/report | 원천경로 포함 운영용으로 접근 제한 |
| B02 | 완료 | train/dev utterance/phone 비식별 explicit feature cache | canonical Parquet, 9개 phone shard, 완료 report/hash | 입력 열 allowlist 고정 유지 |
| B03 | 완료 | train/dev만으로 phone binary baseline 튜닝 | 3 checkpoints/dev report/registry, privacy-safe 실패 분석, 사후 threshold report | primary dev F1 0.4902, GOP·SSL 보강 |
| B04 | 완료 | utterance 3개 점수 baseline | ordinal/regression dev report | 최고 fluency Spearman 0.4780 |
| B05 | 대기(production-static preflight 100%·실제 locked/evaluator 0%) | explicit B03와 S108을 같은 사전등록 batch에서 단 한 번 평가하고 slice·speaker bootstrap·Holm을 누락 없이 보고 | 9 strict schema+2 template, B03/B06 boundary·G04→D07→S107→S108 freeze→prereg→batch→checkpoint hash-only→authorization 계약, read-only CLI/deep payload validator/readiness; config/policy/contract/CLI/doc SHA `9efafe48…c4c`/`1af53682…3e4`/`5c523c64…c1fe`/`9a89ee31…d8f9`/`d3231568…b0f2`; 실제 locked/model/GPU/metric/output 0 | G04 240/240·D07·S107·S108 actual을 완료한 뒤 no-TBD prereg/동일 batch/별도 one-shot authorization을 동결하고 joint executor를 독립 구현·검증 후 한 번만 실행 |
| B06 | 실패(개발) | phone macro F1≥0.60, Spearman≥0.50 | 사전 F1 0.4902; 사후 탐색 F1 0.5599/AUPRC 0.1613, report SHA `ef16c405…` | gate를 낮추지 않고 GOP·WavLM 진행 |
| B07 | 완료(개발·oracle; GOP adoption gate 통과) | train-only acoustic phone posterior에서 target-vs-competing-phone GOP를 산출하고 explicit prosody와 결합해 speaker-disjoint dev에서 3-seed 비교 | actual config/training SHA `9a2070ab…bdb0`/`891389af…87ea`; generation/commit/registry `bc1a7eb1…d6e09`/`ff31a88e…907c`/`5db35282…eb12`, exact artifact 21건; raw Parquet·commit-only 전수 재계산에서 mean phone macro-F1 `0.630442±0.000222`, error AUPRC `0.264573±0.000228`, within-run explicit-refit F1 `0.564433`, paired delta `+0.066010±0.000222`, 사전 `0.60` gate 통과; dev-only oracle/silver, deployable false, canonical B03 대체 아님, 보호 split 0; GOP/SSL 2점 획득 | completed B07 checkpoint/prediction lineage를 S106의 네 번째 component·26-feature allowlist·`gop_only` control로 재동결 |

#### B05 준비도와 실제 one-shot locked 평가 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production-static preflight readiness | 100%; B03 exact config/report/registry·seed 42/43/44 checkpoint·feature allowlist·threshold 0.5를 고정하고 B06 post-hoc 대체를 금지한다. B05 explicit와 S108은 동일 preregistered batch/transaction에서 모두 동결한 뒤 한 번만 실행하며 첫 arm 결과를 둘 다 seal되기 전에 볼 수 없다. 성공·실패 모두 authorization을 소모하고 같은 test 재실행을 금지한다 | source pin 35/35, schema 9/9, config-only의 B03/B06 static validation 1/1과 보호 counter 0; full은 G04 TBD에서 G04 path/read 0 및 모든 downstream/checkpoint/model/GPU/locked/metric/output 0으로 fail-closed; formatter 수정 직전 전체 1,185/1,185, 수정·재고정 뒤 exact slice/bootstrap/Holm 누락·추가·중복·empty rejection 포함 targeted 20/20 및 Ruff/format/mypy 통과 | 0 |
| B05 연구 완료 | 0%; G04·D07·S107·S108 actual, frozen prereg/batch/auth, joint production executor, locked inference·metric·report·registry·joint root가 없음 | actual locked locator/row/audio/label/prediction/model/GPU/inference/bootstrap/Holm/result/output/registry/commit counter 모두 0 | 0/2 |

Hash DAG는 `prereg/freeze/manifest/auth → 두 arm prediction manifest →
서로 독립인 arm report → consumed receipt → arm registry → single joint
root commit`의 단방향이다. Prediction/report의 receipt·joint-root 역참조와
child commit의 root 역참조를 금지한다. Completed report는 preregistered
slice/bootstrap/Holm key 전부를 exact order로 가져야 하며 빈 결과로
완료할 수 없다. 독립 감사에서 formatter drift를 한 번 발견해 contract를
재포맷하고 config pin을 다시 고정한 뒤 targeted/static gate를 재통과했다.
실제
잠금평가가 아니므로 B05 0/2와 최종 gate 0/2는 유지한다. B07 GOP/SSL
2점을 별도로 반영해 WP3 8/12와 전체 48/100이다.

#### B07 준비도와 실제 GOP/SSL 연구 완료 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production-static readiness | 100%; realized identity와 binary error label의 feature 사용을 금지하고, 정상 train row만으로 38-class acoustic prototype을 학습한다. Seed-bound 5-fold에서 fold 0은 temperature, folds 1–4는 row-pooled outer OOF C/threshold 선택에만 쓰며, final all-primary-train cross-fit refit 뒤 dev를 seed당 정확히 한 번 평가한다. CPU-only·RSS 8 GiB·native pool 4 이하, exact join, immutable 21-file generation, completed registry→commit, TOCTOU 재해시와 label-free checkpoint replay를 강제한다 | Static config `dd037316…c209`의 독립 감사 뒤 PyArrow 호환성 수정 actual config `9a2070ab…bdb0`; source pin 11/11, focused 21/21, torch-preimport 3/3, 당시 전체 1,206/1,206, Ruff/format/mypy와 19종 mutation rejection 통과 | 0 |
| B07 연구 완료 | 100%; 정본 2,233,039-row 입력에서 3-seed train/dev 비교, runtime RSS/pool 관측, prediction/checkpoint/report, completed registry/generation/commit 및 read-only 전수 재계산 완료 | Artifact 21/21, source 11/11, cache report 2+shard 18, prediction 3×424,315행과 모든 hash/schema/metric/slice/selection replay 일치; peak RSS 4.55 GiB, pool cap 4, 보호 접근 0; F1 `0.630442±0.000222`, AUPRC `0.264573±0.000228`, gate 통과 | GOP/SSL 2/2 |

정적 스냅샷의 두 실행 승인 flag는 모두 `false`였다. 이를 하나의
변경으로 `true`와 `approved_for_canonical_execution`으로 전환한 첫
실행은 PyArrow 25.0.0의 `Dataset.scanner(columns=tuple)` 호환성에서
fail-closed했다. 두 캐시의 hash와 train/dev metadata는 입력 감사에서
읽었지만 train embedding batch, model fit, dev embedding projection과
dev metric은 열리지 않았다. 실패 receipt
`attempt_001_failure.json`(SHA-256 `19b96c9f…a0cc`)에 원 config/source/
registry hash와 보호 경계를 보존했다. `columns=list`와 필터의 0-row
batch skip을 구현하고 실제 Parquet 회귀·전체 suite를 통과한 actual
config SHA는 `9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0`다.
이 config의 attempt 2는 completed commit을 만들었고 독립
`--validate-commit-only`와 raw prediction replay를 모두 통과했다.
Oracle alignment·silver label을 사용한 development-only 결과이며
locked-test·배포·canonical B03 결과가 아니다. GOP/SSL milestone은
완료했고 이제 이 exact commit을 S106 amendment의 네 번째 component로만
편입한다.

### WP4. S1 WavLM·계층 모델·fusion

> S104–S108의 의존 순서, split/label 접근, 산출물과 G04 전후 승격 조건은
> [WP4 S103 이후 실행 계약](./WP4_POST_S103_EXECUTION_CONTRACT_KO.md)을
> 따른다. 이 tracker는 실제 상태·완료 증거·고정 배점의 정본이고, 링크
> 문서는 세부 실행 protocol의 정본이다.

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| S100 | 완료 | Base+/Large BF16 inference OOM 0 | benchmark JSON | 유지 |
| S101 | 완료(개발·oracle) | frozen Base+ feature cache와 head 3 seeds | full cache·전수검증·CUDA smoke 통과; report/registry `completed`·failure 없음, seeds 42/43/44, 등록 artifact 13개; report SHA `112eedfe…9949a2c`, registry SHA `0f994aa5…1b9f79f`, run ID `aihub71490-wavlm-base-plus-frozen-head-v1-dev`; dev-only oracle alignment, promotion 금지, locked/stack/calibration 미접근 | S102 비canonical 100건 smoke·독립 review |
| S102 | 완료(개발·oracle) | frozen Large 동일 조건 3 seeds | full cache report/validation SHA `98fe95d0…5fe47`/`d3f34b34…83676e`와 head report/registry SHA `ba869265…fdd23`/`85c90e95…5b565`; seeds 42/43/44, completed/failure null, artifact 13개·전이 49/49 검증; prediction Parquet 직접 재계산 phone macro F1 0.671164±0.000443, AUPRC 0.360830±0.000669, utterance QWK/Spearman/MAE/ICC는 pronunciation 0.162764/0.274672/0.802580/0.140056, fluency 0.271806/0.495003/0.791396/0.307800, comprehensibility 0.129550/0.260861/0.771907/0.135741; dev-only oracle alignment, promotion 금지, stack/calibration/locked-test 미접근; S102 4점 획득 | S103의 고정 입력으로 보존 |
| S103 | 완료(개발·oracle; Large 채택 gate 실패) | Large−Base+ phone F1 및 95% CI | comparison commit/report/receipt SHA `e45cf869…16c6e`/`90db58ee…78213`/`342a9bd2…620a6`, results-table commit/Markdown/JSON SHA `af0cb1d6…73e38`/`89db2965…5b0f`/`d8556c3a…d7a20`; prediction 원시 재계산과 5,000회 paired-speaker bootstrap 재실행 결과 Large−Base+ F1 `+0.0014656956`, 95% CI `[0.0000061249, 0.0029903885]`, two-sided p `0.0491902`; 사전 고정 `+0.03` gate 실패, strict 재렌더 Markdown/JSON exact 일치, dev-only oracle/no-promotion·보호 split 미접근; S103 2점 획득 | Large 확대를 중단하고 S104의 고정 Base+ adapter branch 실행 |
| S104 | v1 시도 무효화·v2 current-code 3-seed GPU 실행 중 | adapter/partial fine-tune 후보 | v2 config SHA `7a78e718…be6434`, execution lock SHA `c37ce4e3…a3e`; full preflight는 Base+·169,837 utterances, 현재 `intervals.py` SHA `0fc28d73…47cf`; user-systemd unit `intelligentai-s104-v2.service` active, trainer PID `57618`, CUDA 사용 관측. 현재 checkpoint·metric·generation·completed registry·commit 0, DEV-S104-003 `INVALIDATING`, S104 획득 0 | terminal 상태까지 supervisor 실행을 유지하고 v2 completed commit을 `--validate-commit-only`로 독립 검증한 뒤 v2 기준 S105 re-pin |
| S105 | 실행 준비 완료(구현 100%·실제 실험 0%) | phone→word→utterance multi-task/HiPAMA ablation | production runner·82-file generation·5개 registry·deep commit validator와 원자 publish/recovery를 구현·합성 검증; config/validator/readiness SHA `e7818240…64ea0`/`e2f7269c…3ad1`/`f5839e59…6ed1`; S105 targeted 53개·관련 회귀 63개, Ruff/format/mypy, 독립 C/H/M 0 통과; config-only는 S104/data/raw/CUDA read와 output 0, full preflight는 S104 commit 부재에서 fail-closed·S105 output 0; 실제 checkpoint/metric/generation/registry/commit 0, S105 획득 0 | S104 completed commit의 독립 검증 직후 full preflight와 5 variant×3 seed 정본 GPU 실행 |
| S106 | 4-component production generator·bundle v3 consumer·deep validator 완료(실제 generator 실행·fusion 실험 0%) | explicit+S104+S105+GOP confidence-aware fusion | exact order `explicit,s104_adapter,s105_selected_hierarchy,gop_acoustic_posterior`, 4×3=12 stack prediction·26-feature·`gop_only`; main/generator config, generator CLI/module, projection/contract/training/train CLI/readiness SHA `196ed40a…caabb`/`feafe24c…72b0`/`249a9747…314d`/`31b15dde…f5b`/`baeb6192…40e`/`8210581f…7793`/`e8366b15…8097`/`b1ed2979…888c`/`aa8b127f…386e`; held-fd filtered bounded Parquet scanner, anonymous unlinked label staging, crash-released single-writer flock, exact sealed producer tree·별도 inode public view, fsync·cleanup·recovery를 구현하고 독립 C/H/M 0/0/0 승인; S106 focused 98/98, S107/S108 44/44, direct downstream 226/226; config-only actual/output 0; actual blocker는 S104/S105 validated commit 부재 2개이며 execution lock·12 prediction·bundle·metric·generation·registry·commit 모두 0, S106 획득 0 | S104·S105 completed commit 독립 검증 뒤 production generator로 12 prediction/receipt/label/bundle v3를 발행하고 stack-only 정본 실행 |
| S107 | 실행 준비 완료(구현 100%·실제 calibration 0%) | calibration ECE≤0.10, issued precision≥0.90 | calibration-only production runner·7-file generation·deep validator와 provisional engineering policy를 구현·합성 검증; frozen S106 exact pin 반영 뒤 config/contract/runner/readiness SHA `6702ea19…909f`/`04909937…ac1e`/`201973a2…d6d3`/`c1abb3bc…e1c7`; targeted 15/15, S106→S107 70/70; full preflight는 S106 commit 부재에서 fail-closed되어 calibration/data/raw/env 접근과 output 0; 실제 calibration bundle·fit·metric·generation·registry·commit 0, S107 획득 0 | S104–S106 completed commit을 차례로 독립 검증한 뒤 calibration split에서만 정본 실행 |
| S108 | no-locked 준비·정본 dry-run 완료(실제 final/locked 0%) | phone F1≥0.80, utterance ICC≥0.70, worst gap≤0.10 | aggregate-only 상태기계·publisher·validator 구현과 정본 no-locked snapshot 완료; frozen S106/S107 exact pin 반영 뒤 config/CLI/module/readiness SHA `038947cc…65aa`/`467ef601…b6b6`/`38002184…b2ba2`/`542aabfa…1566`; 기존 receipt는 exact legacy-config allowlist와 `historical_snapshot=true`로만 보존하며 새 actual 결과로 승격하지 않음; generation/checklist/receipt SHA `73f85af4…aa92`/`dd34ec5c…375f`/`d90cb96f…fa88`, S107→S108 44/44, 상태 `engineering_in_progress`, locked 6종 및 protected/row/raw/env 접근 0, 실제 final metric·completion·score·PENDING 해제·promotion 0, S108 획득 0 | S104–S107 정본 완료와 preregistration·G04·D07 통과 뒤 별도 one-shot locked orchestrator를 검증하고 단일 batch 실행 |

#### S104–S108 준비도와 실제 연구 진행 분리

| 단계 | 구현·실행 준비 완성도 | 실제 실험 산출물 완성도 | 배점 판정 |
|---|---:|---:|---|
| S104 | 100%; v2 current-code immutable lock과 정본 GPU 실행 경로 실행 중 | 0%; v1 시도는 무효화됐고 v2 trainer는 아직 terminal artifact를 만들지 않음, metric·generation artifact·commit·completed registry 0 | 0/2 |
| S105 | 100%; production runner·deep validator·fault/recovery 검증 완료 | 0%; S104 completed commit 대기, 실제 GPU 실험과 결과 0 | 0/2 |
| S106 | 100%; production component generator, bundle v3 consumer, producer evidence deep validator와 held-fd bounded scan·anonymous label staging·single-writer/fsync/recovery 검증 완료 | 0%; S104/S105 completed commit 대기, actual predecessor audit·stack 접근·12 prediction·bundle·결과 0 | 0/1 |
| S107 | 100%; calibration-only runner·deep validator·provisional policy 검증 완료 | 0%; S106 completed commit 대기, calibration 접근·fit·metric·결과 0 | 0/1 |
| S108 | 100%; no-locked aggregate 상태기계·publisher·validator와 정본 dry-run snapshot 완료 | 0%; `ready_for_locked_batch=false`, locked 접근·최종 metric·promotion 결과 0 | 0/1 |

구현 준비도 100%는 해당 행에 명시한 코드 범위의 완성을 뜻할 뿐 실제
연구 단계의 완료가 아니다. S106 production generator도 구현·합성 검증된
상태일 뿐 S104/S105 actual commit이 없어 정본 production 호출은 아직
없다. 고정 원장에는 정본 generation·completed registry·commit과 독립
검증이 모두 존재하는 단계만 점수를 부여한다.

### WP5. S2 독립 감사

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| S200 | 실행 준비 완료(구현 100%·실제 S2 0%) | S1과 backbone/정렬/holdout/목표함수 분리 계약 | config/contract/CLI/data card/readiness SHA `01649186…355b`/`b9f13b57…a35b`/`9b4e528f…b9d1`/`fb362153…4256`/`b5516759…b14f`; R00 paper integration 뒤 manuscript provenance pin만 refresh, 정책 불변; 관련 18개 테스트와 Ruff/mypy, 독립 C/H/M 0 통과; config-only source read 12, predecessor/external/data/model/protected/holdout/locked/GPU/metric/result/output 0; full preflight는 S108 final `research_candidate_passed` commit 부재에서 predecessor read attempt/success 1/0으로 fail-closed, 나머지 접근·output 0 | S108 final commit과 외부 attestation 뒤 full preflight; S201/S202 구현·실행 |
| S201 | 실행 준비 완료(정적 preflight 100%·실제 S2 0%) | Whisper 내용 일치 hard gate | config/contract/CLI/data card/amendment/readiness SHA `d166cd8a…29b7`/`88f0cd14…d7667`/`0f10c1d9…d2b2`/`189e0eca…b594`/`dd6a11c6…ce26`/`a966e985…7b33`; provenance pin cascade refresh 뒤 정책 불변; `openai/whisper-large-v3` exact revision `06f233fe…fce1`, offline local-only·`ko`/`transcribe`·deterministic decoding 고정; WER/CER/content-mismatch는 모두 `TBD_PREREGISTRATION`, production runner 없음; config-only source 12건 외 predecessor/data/model/download/GPU/metric/output 0, full preflight는 S200 정적·deep 선행자 metadata read 13건 뒤 부재한 S108 final에서 attempt 1로 fail-closed하고 threshold/external/data/model/output 0; 실제 trainer/data/model/metric/result/commit 0, S201 획득 0 | S108 final·S200 lock 뒤 threshold 사람 승인 사전등록, 외부 attestation·local bundle·production runner 준비 후 실행 |
| S202 | 실행 준비 완료(정적 preflight 100%·실제 S2 0%) | HuBERT/별도 표현 pairwise·ordinal auditor | config/contract/CLI/data-model card/amendment/readiness SHA `9a608103…2662`/`0ba78271…daac`/`05cddea0…c97d`/`cadafe86…9c3d`/`62da51db…5417`/`ee7c739f…4943`; provenance pin cascade refresh 뒤 정책 불변; S108→S200→S201 completed gate 순서와 S1/oracle/silver/Whisper score leakage 금지를 정적으로 강제; model/revision/label/rater/adjudication/loss/metric/CI/gate는 모두 `TBD_PREREGISTRATION`; production trainer 없음; config-only source 15건 외 runtime 접근·output 0, full preflight는 S108 부재의 `predecessor_s108`에서 fail-closed하고 downstream validation·data/model/holdout/GPU/metric/output 0; 실제 trainer/data/model/metric/result/commit 0, S202 획득 0 | S108 final→S200→S201 completed commit 뒤 사람 승인 사전등록·독립 label/rater/adjudication·holdout 계약을 고정하고 production trainer를 별도 구현 |
| S203 | 실행 준비 완료(정적 preflight 100%·실제 전문가 holdout 0%) | 심각 S1 오판 recall≥0.80, false-high-score 상대 감소≥0.30, 정상 referral≤0.15, 불일치 전문가 오류 적중률≥0.60 | config/contract/CLI/evaluation card/amendment/readiness SHA `610e6142…73d0`/`35ee786e…578b`/`afa42ee9…0756`/`a9fb81d3…b2a8f`/`e1fec889…197a`/`308d9e11…71f5`; mutable tracker self-pin을 제거해 순환 drift를 차단(source 24), unit/integration SHA `c64e5761…c22b`/`c803dc67…794a`; S108→S200→S201→S202 선행 순서와 D07/locked/S2 split overlap 0을 강제하는 fail-closed 정적 preflight 구현; S200–S203 관련 79개·Ruff/format/mypy 통과; metric 정의·severity·CI·cluster·slice·support·multiplicity·rater·adjudication·one-shot access family·join statistic은 모두 `TBD_PREREGISTRATION`; production evaluator·holdout 접근·metric/result/commit 0 | S108 final과 S200–S202 실제 completed commit 뒤 사람 승인 사전등록·독립 전문가 holdout attestation·production evaluator를 별도 구현하고 one-shot 실행 |

#### S200–S203 준비도와 실제 S2 연구 진행 분리

| 단계 | 구현·실행 준비 완성도 | 실제 실험 산출물 완성도 | 배점 판정 |
|---|---:|---:|---|
| S200 | 100%; 독립성 계약·data card·fail-closed preflight와 접근 계측 검증 완료 | 0%; S108 final predecessor가 없고 S201 production runner/S202 trainer·모델 download·GPU 실행·실제 metric/result 0 | 0/1 |
| S201 | 100%; exact Whisper revision·deterministic decoding·자료 역할과 fail-closed 정적 preflight 검증 완료 | 0%; production runner·실제 data/model·download·GPU·WER/CER/content-mismatch metric·result/commit 0, threshold는 모두 `TBD_PREREGISTRATION` | 0/2 |
| S202 | 100%; 독립 auditor의 단계 순서·정보 차단·fail-closed 정적 preflight와 접근 계측 검증 완료 | 0%; 모든 model/revision/label/rater/adjudication/loss/metric/CI/gate가 `TBD_PREREGISTRATION`이고 production trainer·실제 data/model/holdout/GPU·metric/result/commit 0 | 0/3 |
| S203 | 100%; S108→S200→S201→S202 이중 검증, D07/locked/S2 split 분리와 one-shot no-access fail-closed 정적 preflight 검증 완료 | 0%; production evaluator·전문가 holdout 접근·S1/S2 prediction join·metric/CI/gate·result/registry/commit 0, metric 정의와 통계·평가 계약은 `TBD_PREREGISTRATION` | 0/2 |

S200–S203의 `실행 준비 완료`는 계약·정적 preflight 구현의 완성을
뜻하며 실제 S2 감사나 네 단계의 완료를 뜻하지 않는다. S201 production
runner, S202 production trainer, S203 production evaluator와 실제
자료·모델·holdout·metric은 모두 없다. 정본 실험 산출물이 없으므로
WP5 0/8을 유지한다.
R00 완료 1점과 B07 GOP/SSL 2점을 반영한 전체 진행률은 48/100이다.

### WP6. 근거 제한 코칭

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| C00 | 완료 | DiagnosticProfile 계약과 우선순위/gate 테스트 | API/unit tests | 유지 |
| C01 | 진행 중(공학 계약·ontology 준비 완료, 연구 완료 보류) | 실제 tag×target-phone 근거와 보정·전문가 감사까지 포함한 taxonomy/advice ontology 완성 | aggregate catalog/schema/validation report/data card | joint aggregate 확보 → S107 threshold 반영 → C03 감사 |
| C02 | 실행 준비 완료(정적 preflight 100%·실제 coaching 0%) | 재현 가능한 top-1/3 coaching 생성 | config/input·output schema/preregistration template/contract/CLI/card/readiness SHA `6b472366…ee28`/`cd2588ff…7742`/`935db571…c643`/`6d34fd0f…040b`/`59941428…1667`/`4dbbd04b…292f`/`88749a14…7b1`/`327c7403…d1b8`; Decimal deterministic rank·no-backfill, C01 catalog-only, low-confidence rerecord, unsupported expert_review, free-generation 금지를 강제; config-only source 19/runtime 0, full은 C01 deep 뒤 S107 completion TBD에서 path/read/module 0으로 fail-closed; 실제 diagnostic/coaching/effect/model/GPU/network/LLM/output 0, C02 획득 0 | S107 actual completion·threshold report와 evidence matrix를 독립 사전등록한 뒤 D1 executor를 별도 구현·실행 |
| C03 | 실행 준비 완료(정적 preflight 100%·실제 전문가 감사 0%) | 200건 전문가 감사 | config/item·rating·report schema/approval·reviewer·prereg template/contract/CLI/card/readiness SHA `8d5857b4…3d00`/`52be547d…1e97`/`be1bda4b…3ae9`/`5c3102aa…b1dd`/`242befd1…d58c`/`ba34657c…f724`/`2b5a149c…a47e`/`641107be…531c`/`6dec5c22…0b27`/`fd22d34e…0d0c`/`72140f75…8af2`; 200건·층화·모델점수 blind, 위치/유형≥0.90·unsupported<0.02·4종 zero-tolerance를 고정; config-only source 21/runtime 0, full은 S107 TBD에서 모든 predecessor/external/data/runtime/output 0; 실제 expert/rating/audio/metric/result 0, C03 획득 0 | S107·C02 actual completion 뒤 기관/전문가 승인·reviewer attestation·no-TBD prereg를 동결하고 blind 200건 감사 실행 |

#### C01 준비도와 실제 연구 진행 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Engineering contract/ontology readiness | 100%; aggregate-only taxonomy·mapping·우선순위·abstention 계약과 validator 구현 완료 | 공식 17 tags·176,523 intervals·unknown 0, mapping 규칙, label/feature 강제 분리, top-1/top-3와 unsupported/저신뢰 abstention, validator `valid`, C/H/M 0 | 0 |
| C01 연구 완료 | 보류; 실제 tag×target-phone joint aggregate, S107 calibrated threshold, C03 human review가 없음 | aggregate source 접근 1회; row/raw/locator/env/identifier 접근 0; 현 `0.75`는 provisional engineering 값 | 0/1 |

따라서 현재 catalog와 한국어 advice 문구는 `engineering_draft`다. 이는
실제 coaching 결과, production 배포 적합성, 위치·유형 정확도 또는 안전성
검증을 뜻하지 않는다. C01 1점은 세 차단 근거가 모두 충족될 때까지
보류하며 WP6 1/7을 유지한다. R00 완료 1점을 별도로 반영한 전체
진행률은 48/100이다.

#### C02 준비도와 실제 coaching 진행 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; C01 deep validation→S107 actual completion→frozen preregistration 순서와 deterministic top-1/top-3·no-backfill·abstention·catalog-only/no-LLM 규칙을 강제하는 package 검증 완료 | config-only source 19건 외 runtime 0; full은 C01 aggregate 1·protected 0 뒤 S107 `TBD_S107_COMPLETION`에서 path/read/module 0으로 종료; targeted 21개, C01+C02 33개, 관련 회귀 140개와 Ruff/format/mypy 통과 | 0 |
| C02 연구 완료 | 0%; S107 completion/threshold와 exact evidence matrix·confidence aggregation·approved preregistration, D1 input, coaching output, effect evidence가 없음 | diagnostic/coaching/effect/model/GPU/network/LLM/free-generation/output counter 모두 0 | 0/3 |

현재 top-1/top-3 priority 식과 abstention 형태는 구현됐지만 exact confidence
semantics와 issue-type별 support matrix는 근거가 없어
`TBD_PREREGISTRATION`이다. S107의 `abstain_all_no_provisional_candidate`를
우회하거나 blocked 후보를 backfill하지 않는다. 준비성은 실제 코칭의
정확도·안전성이나 C03 통과를 뜻하지 않으므로 WP6 1/7과 전체 48/100을
유지한다.

#### C03 준비도와 실제 전문가 감사 진행 분리

정책 보정: C03 성인 전문가 감사는 개인 연구 scope attestation과 평가자
동의·자격 attestation을 요구한다. 기관·전문가 승인 문구는 레거시 executor
계약의 상태이며 실제 감사 0%와 amendment 미완료를 바꾸지 않는다.

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; S107 actual→C02 actual D1 bundle→기관/전문가 승인→reviewer attestation→frozen preregistration 순서와 blind 200-item gate를 강제하는 package 검증 완료 | config-only source 21건 외 runtime 0; full은 S107 TBD에서 S107/C02/approval/reviewer/prereg/audio/rating/model/GPU/network/metric/output 0; targeted 15개, C01–C03 47개, 전체 1,047개와 Ruff/format/mypy 통과 | 0 |
| C03 연구 완료 | 0%; 실제 승인·전문가·reviewer·item/audio·rating·adjudication·metric/report가 없음 | expert/rating/audio/protected/model/GPU/network/metric/result/commit counter 모두 0 | 0/2 |

Primary는 위치 정확도와 유형 정확도 각각 `>=0.90`, unsupported claim
rate `<0.02`다. 위험·차별·정체성 추론·의학 판단은 한 건이라도 있으면
pass를 금지하고 수정·재감사한다. Exact allocation, reviewer N/자격,
ratings/item, adjudication, CI/multiplicity/support와 상세 risk taxonomy는
TBD이므로 WP6 1/7과 전체 48/100을 유지한다.

### WP7. 다중 참조음·TTS

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| R00 | 완료 | 라이선스가 명시된 20개 고정 문장 | project-original CC0-1.0 sentence card/schema/notice/validator/test/report/data card SHA `08dc149c…d82c`/`1f8bcf03…7b92`/`ffd86dc8…62c9`/`967517e7…e7d0`/`251b0d01…4f30`/`95b4fd86…6dd`/`d48e75ee…138c`; 20/20 unique, reserved collision·PII·forbidden 0, validator `valid`, R00 completion allowed | R01 후보 생성 전 음성·화자·동의·라이선스 계약 고정 |
| R01 | 실행 준비 완료(정적 preflight 100%·실제 후보 생성 0%) | 인간+CosyVoice 후보, 문장당 8–16개 | config/schema/human template/CosyVoice template/contract/CLI/unit/integration/card/readiness SHA `ae6034ba…4f64`/`7ffe1b15…1470`/`aeeebf42…d096`/`2ce20ac3…c2e`/`e70bd5ed…a95`/`e0daf9c1…bac5`/`d589c83b…f8e3`/`69de05e6…1e07`/`f2d25a6d…6d40`/`c15784dc…d465`; R00 paper integration 뒤 manuscript pin만 `fca96187…f8a5`로 provenance refresh, 정책·runtime 경계 불변; R00 public validator·deep validation을 먼저 통과한 뒤 human attestation `TBD_EXTERNAL_ATTESTATION`에서 fail-closed; human path·CosyVoice·data/audio/model/env/GPU/candidate/metric/R02/publication/output 접근 0; 실제 후보·오디오·모델 실행·GPU 실행·manifest·completion artifact 0, R01 획득 0 | 외부 human 동의·녹음·라이선스 attestation과 CosyVoice code/model/weight-license·offline env/local bundle·GPU binding을 승인·고정한 뒤 별도 production recorder/inference로 후보 생성 |
| R02 | 실행 준비 완료(정적 preflight 100%·실제 gate 0%) | ASR/artifact/휴지·리듬/목표음소 hard gate | config/report schema/preregistration·ASR·artifact template/contract/CLI/unit/integration/card/readiness SHA `d3e73343…f6b7`/`60525a3b…a92b`/`07b53aa9…28c0`/`f1c0ea10…f53f`/`940f36e3…91cc`/`a50e6287…f5bf`/`c40ab4b0…3be5`/`22c83a3f…81d8`/`4caa1f7f…b5a1`/`7cb96924…a61f`/`e3c26f2b…4c4c`; R00 deep 뒤 R01 completion `TBD_R01_COMPLETION`에서 fail-closed, R01/D1/audio/model/GPU/metric/S1/S2/gold/locked/output 0; exact metric·threshold·checkpoint·normalization·segmentation·support·license는 `TBD_*`, human/TTS 동일 blind gate와 탈락 D1 보존·R03 eligible-only 계약; 실제 report/eligible/quarantine/registry/commit 0, R02 획득 0 | R01 완료 뒤 exact gate 사전등록·artifact 승인, 별도 source-blind executor 실행과 completed bundle 독립 검증 |
| R03 | 실행 준비 완료(정적 preflight 100%·실제 선택 0%) | Pareto+diversity로 문장당 3–5개 | config/bank schema/preregistration template/contract/CLI/unit/integration/card/readiness SHA `6e24735f…2fce`/`2e6c4728…ca83`/`8854d0f9…6de8`/`626e5641…0bb8`/`36c21580…2517`/`299cbecd…c374`/`d431add1…1487`/`93edf4a2…f20d`/`c014aece…8f88`; R00→R01 completed→R02 completed→R03 prereg 순서, source-blind·outcome-blind, D1 bank/A1 aggregate와 immutable R01/R02 lineage를 강제; canonical full은 R00 deep 뒤 R01 completion TBD에서 path attempt 0, candidate/audio/model/GPU/metric/selection/R04/output 0; 실제 bank/trace/aggregate/commit 0, R03 획득 0 | R01/R02 실제 완료 뒤 objective·normalization·dominance/tie·diversity distance/min separation·seed를 data 접근 전에 승인·사전등록하고 별도 selector 실행 |
| R04 | 실행 준비 완료(정적 preflight 100%·실제 blind 평가 0%) | blind MOS/CMOS에서 무선별 TTS 우위 | config/contract/CLI/randomization·rating schema/card/readiness SHA `6d78c8ea…2881`/`8bcb3d40…9b7c`/`6dd7c7b8…36e`/`1d76bfac…1a32`/`510f0063…b09f`/`e7e5be7b…1f03`/`d4a93610…a1ddc`; config-only source 25/runtime·D1·output 0, full은 R00 deep 뒤 R01 completion TBD에서 path/read 0으로 fail-closed; 실제 listener/audio/model/GPU/rating/MOS/CMOS/statistics/result/commit 0, R04 획득 0 | R01–R03 실제 completion, 인간 연구 승인·preregistration·pilot nuisance·SAP·listener attestation을 동결한 뒤 blind 평가 실행 |
| R05 | 실행 준비 완료(정적 preflight 100%·실제 gate 판정·GRPO 0%) | GRPO 진입 gate 판정 | config/decision schema/preregistration·authorization template/contract/CLI/card/readiness SHA `e47ff970…b06f`/`22174e8c…8c47`/`83b343a5…ac4b`/`2480c833…8645`/`3041ea27…47be`/`917b72f6…807d`/`b8095707…171c`/`4ea70a32…3109`; config-only source 14/runtime 0, full은 S108 completion TBD에서 path/read 0으로 fail-closed; 실제 metric/effect/decision/model/download/GPU/reward/training/output 0, R05 획득 0 | S108·S203·R04·H04 실제 completion과 독립 preregistration·인간 authorization 뒤 aggregate-only gate 판정 |

#### R01 준비도와 실제 후보 생성 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; R00 deep validator 우선, 외부 attestation·권리·offline runtime·local model bundle·GPU pin·D1 privacy·R02 전 비공개를 강제하는 read-only fail-closed package 구현 완료 | config-only source 13건 외 runtime 접근 0; full preflight는 R00 validator/deep validation 1/1, public·aggregate 접근 33건, raw audio/row 0 뒤 `human_attestation=TBD_EXTERNAL_ATTESTATION`에서 종료; targeted 24개, R00+R01 37개와 Ruff/format/mypy 통과, C/H/M 0 | 0 |
| R01 연구 완료 | 0%; human/CosyVoice candidate·audio·model·GPU·manifest·metric·R02·publication·completion artifact가 모두 없음 | 문장 20개, 문장당 8–16개(계획 12), human=TTS, source당 4–8개, source별 화자/voice≥2, speed/pitch 각 3 bucket, UUIDv4 opaque ID, D1, R02-before-publication은 계약값일 뿐 실제 결과가 아님 | 0/2 |

CosyVoice repository/model revision과 code/model-card license ledger는 관측
사실일 뿐 사용 승인 증거가 아니다. Standalone weight license, 외부 승인,
offline environment, local weight bundle과 exact GPU binding은 아직
`TBD`이며 download·fine-tuning·GRPO를 실행하지 않았다. 따라서 R01 정적
준비성에는 점수를 주지 않고 WP7 1/10과 전체 48/100을 유지한다.

#### R02 준비도와 실제 hard-gate 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; R00→R01 completed bundle→preregistration→ASR/artifact attestation 뒤에만 D1 audio를 허용하고 human/TTS 동일 source-blind 네 gate, S1/S2·gold·locked 금지, 탈락 D1 보존과 R03 eligible-only를 강제하는 package 구현 완료 | Config-only source 16건 외 runtime 0; full은 R00 validator/deep 1/1·public/aggregate 33·target rule 20·raw/row 0 뒤 R01 `TBD_R01_COMPLETION`에서 path attempt 0으로 종료; R02 targeted 22개, R00→R02 59개, Ruff/format/mypy, C/H/M 0 | 0 |
| R02 연구 완료 | 0%; R01 candidate/audio와 exact gate 승인·metric 실행·report/partition/registry/commit이 없음 | metric·threshold·model/checkpoint·normalization·segmentation·minimum support·license는 `TBD_*`; 실제 audio/model/GPU/metric/output 0 | 0/2 |

Aggregate transcript error 2%는 실행계획에 있는 완료 경계지만 candidate
threshold가 아니다. Exact aggregate metric ID·정의·aggregation과 네 gate
수치는 사람 승인 사전등록 전까지 TBD다. R00 target phone/rule는 coverage
audit target일 뿐 observed realization/gold가 아니며, R02 pass도 publication
또는 human/TTS 품질 우위를 뜻하지 않는다. 따라서 WP7 1/10과 전체
48/100을 유지한다.

#### R04–R05 준비도와 실제 평가·GRPO 진행 분리

정책 보정: R04 성인 청취자 평가는 개인 연구 scope attestation, 개별 동의,
listener attestation, preregistration, pilot/SAP가 선행한다. 기관 승인
문구는 참여자 연구 경로 또는 레거시 정적 계약으로만 해석하며 실제 blind
평가 0%를 유지한다.

| 단계 | 구현·실행 준비 완성도 | 실제 실험 산출물 완성도 | 배점 판정 |
|---|---:|---:|---|
| R04 | 100%; human+hard-gated TTS, source blind, listener×sentence×candidate MOS, same-sentence CMOS, 승인→preregistration→pilot→SAP→listener attestation→D1 순서와 privacy/statistics 경계를 강제하는 정적 package 검증 완료 | 0%; R01–R03 actual completion, 인간 승인, listener, audio, rating, MOS/CMOS, 통계, superiority report, registry, commit이 모두 없음 | 0/3 |
| R05 | 100%; S108→S203→R04→H00/H04→preregistration→authorization 순서, 고정 5-gate와 aggregate-only decision, model/license/reward/safety assurance를 강제하는 정적 package 검증 완료 | 0%; 선행 actual completion, metric/effect evidence, gate decision report, model/download/GPU/reward/GRPO 실행과 commit이 모두 없음 | 0/1 |

R04 config-only는 source pin 25개만 읽고 모든 runtime/D1/output counter가
0이었다. Full preflight는 R00 deep validation 뒤 R01 completion
`TBD_R01_COMPLETION`에서 R01 path/read 0으로 종료했다. R05 config-only는
source pin 14개만 읽고 runtime/output counter가 0이었으며, full preflight는
S108 completion `TBD_S108_COMPLETION`에서 predecessor path/read 0으로
종료했다. 두 package의 결합 targeted test 57개와 Ruff/format/mypy가
독립 재검증을 통과했다. 준비성은 실제 사람 평가나 GRPO 진입 근거가
아니므로 WP7 1/10과 전체 48/100을 유지한다.

### WP8. 폐쇄루프·metamorphic

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| E00 | 실행 준비 완료(정적 preflight 100%·실제 폐루프 실행 0%) | 녹음→진단→코칭→참조→재녹음 재현 script | config/completion·preregistration·session·trace·authorization·D08↔R03 lineage·reference·S108 handoff schema/contract/CLI/card/readiness SHA `39a3dd0b…d13b`/`2341fde0…8f0`/`5cd175a5…6ce`/`842d54b5…27ac`/`265ddd81…1196`/`95803950…99bd`/`816b8248…4d98`/`4746cf2b…68ad`/`675a8a14…761e`/`b33cfd7a…151ca`/`9f846ae8…a75f`/`8ff23c5b…81f2`/`3017cfd7…c3fd`/`5e329a31…50bd`; S108→E01→C02/E02→C03→R03→D08→prereg→authorization→exact lineage 순서를 강제하고 full은 S108 TBD에서 모든 actual counter 0으로 fail-closed; 실제 input/audio/inference/API/delivery/rerecord/metric/output 0, E00 획득 0 | 모든 선행 actual completion 뒤 preregistration과 human H00+개별동의 또는 learner-input 전용 nonhuman fixture authorization을 동결하고 제한 executor에서 실행 |
| E01 | 실행 준비 완료(정적 preflight 100%·실제 metamorphic 실행 0%) | silence/gain/duration/F0/phone 변형 suite | config/input·report schema/preregistration template/contract/CLI/card/readiness SHA `d9971f69…26dd`/`4b345975…73d`/`7e7e4e7b…e577`/`1e364ac4…2f7`/`0b4e6dec…6e43`/`591153fe…7863`/`bef7b093…0eae`/`9f3d413e…faee`; 문서 근거가 있는 pass rate≥0.95만 고정하고 magnitude·expected relation·집계/CI는 TBD; config-only source 14/runtime 0, full은 S108 completion TBD에서 path/read 0으로 fail-closed; 실제 perturbation/inference/metric/decision/output 0, E01 획득 0 | S108 actual final과 독립 사전등록 뒤 non-locked evaluation set에서 production perturbation/evaluator를 별도 실행 |
| E02 | 실행 준비 완료(정적 preflight 100%·실제 API guard 실행 0%) | confidence 밖 자동 교정 차단 | config/input·API response·C02 bundle schema/prereg template/contract/CLI/card/readiness SHA `bd87c5ae…7c04`/`23826019…7e73`/`e15d55e2…0cd8`/`611d6c4f…96ca`/`8b1e0490…d4f9`/`6c1ce292…7764`/`bb7e0e43…9c1a`/`1e3516cd…c9d8`/`3e9445b2…e058`; OOD/low-confidence/unsupported/abstain-all에서 advice/reference/action 차단, rerecord/expert_review만 허용; config-only source 25/runtime 0, full은 S107 TBD에서 S107/C02/request/API/output 0; 실제 guard/API/coaching/reference/model/output 0, E02 획득 0 | S107·C02 actual completion과 OOD/결합 규칙 사전등록 뒤 production API에 exact deep binding하여 실행 |

#### E00 준비도와 실제 폐루프 진행 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; attempt 1→actual S108 handoff→E02→C02 catalog top-1→licensed R03/D08 reference→attempt 2→same-scorer comparison과 네 blocked safe exit를 강제하는 fail-closed package 검증 완료 | config-only source 50건 외 runtime 0; full은 S108 `TBD_S108_COMPLETION`에서 source TOCTOU 50건 뒤 S108 path/read/deep 0, 모든 후속·governance·session·input/audio/model/GPU/API/delivery/rerecord/metric/result/output 0; targeted 20개, 관련 회귀 222개, 저장소 전체 1,128개와 Ruff/format/mypy 통과 | 0 |
| E00 연구 완료 | 0%; 실제 S108/E01/C02/E02/C03/R03/D08 completion, preregistration, authorization, session과 폐루프 실행이 없음 | input/audio/microphone/inference/API/coaching/reference/rerecord/metric/result/registry/commit/publication counter 모두 0 | 0/2 |

Locked result의 복사·재생은 actual S108 production handoff를 대체할 수 없다.
독립 R01 human candidate도 D08이라고 추정하지 않고 exact R03 candidate
row/audio object/audio content와 D08 metadata row·aggregate rights·개별
recording grant를 제한 영역 lineage receipt에서 교차 결합해야 한다.
Nonhuman fixture의 `contains_human_voice=false`는 learner attempt input에만
적용하며, 별도 동의·허가된 D08 human reference는 그 범위 밖이다. 정적
준비성은 실제 데모나 교육 효과가 아니므로 WP8 0/5와 전체 48/100을
유지한다.

#### E01 준비도와 실제 metamorphic 진행 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; S108 final→독립 preregistration→input manifest 순서, locked-test·S203 expert holdout 금지와 silence/gain/duration/F0/phone family 및 pass rate 0.95를 강제하는 read-only fail-closed package 검증 완료 | config-only source 14건 외 runtime/data/model/network/GPU/locked/output 0; full은 S108 `TBD_S108_COMPLETION`에서 predecessor path/read 0으로 종료; targeted 31개, 관련 회귀 139개와 Ruff/format/mypy 통과 | 0 |
| E01 연구 완료 | 0%; exact magnitude·expected relation·aggregate/CI가 사전등록되지 않았고 actual input/model/perturbation/inference/metric/report가 없음 | 실행 counter와 output 모두 0, completion claim false | 0/2 |

`>=0.95`는 Phase 3 non-holdout metamorphic gate로 문서에 근거가 있지만,
각 perturbation의 수치·불변/단조 기대·case 수·pass aggregation·CI는
근거가 없어 `TBD_PREREGISTRATION`으로 유지한다. 이를 임의로 채우지
않으므로 WP8 0/5와 전체 48/100을 유지한다.

#### E02 준비도와 실제 confidence guard 진행 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; S107 actual threshold policy→C02 actual bundle→E02 prereg 순서, threshold 복제 금지, C02 no-backfill/no-free-generation 승계와 safe API response를 강제하는 package 검증 완료 | config-only source 25건 외 runtime 0; full은 S107 TBD에서 S107/C02/request/model/GPU/network/LLM/API/output 0; targeted 25개, 관련 회귀 69개와 Ruff/format/mypy·schema 검사 통과 | 0 |
| E02 연구 완료 | 0%; actual S107/C02, OOD 정의·threshold/rule·혼합 사유 정책과 production API request/response가 없음 | guard/API/coaching/reference/action/model/metric/output counter 모두 0 | 0/1 |

Blocked response에는 PII·raw path·free text·모델 score·교정 payload를
넣지 않고 reason/evidence provenance와 `rerecord` 또는 `expert_review`
safe next action만 허용한다. 실제 integration artifact가 없으므로 WP8
0/5와 전체 48/100을 유지한다.

### WP9. 전문가 평가·파일럿/RCT

H00–H04는 신규 참여자 모집·중재·RCT·학습자 pre/post/retention을 선택할
때만 적용하는 참여자 연구 경로다. 현재 개인 연구의 전문가·원어민·청취자
평가에는 H00 기관 승인 gate를 적용하지 않는다.

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| H00 | 개인 연구 참여자 경로 미사용(정적 contract retired) | 개인 연구의 전문가·원어민·청취자 평가에는 기관 승인 gate를 적용하지 않음 | 기존 기관 승인 문서·attestation은 retired-contract 통합 안내로 이동; 새 실행·모집·동의·기관 판단·개인정보 산출물 0 | 참여자 연구로 범위를 바꿀 때만 별도 범위 결정과 관련 계약을 다시 작성 |
| H01 | 개인 연구 참여자 경로 미사용(정적 contract retired) | 학습효과·종단 중재를 현재 범위에 포함하지 않음 | 기존 학습효과 사전등록·power 계획은 retired-contract 통합 안내로 이동; pilot·통계·완료 주장 0 | 참여자 연구로 범위를 바꿀 때만 새 계획과 데이터/통계 계약을 작성 |
| H02 | 외부 의존(정적 실행 준비 100%·실제 참여자 0%) | 조건당 15–20명 feasibility 뒤 수동 final-N의 별도 confirmatory, 2주 중재 | D09 계약이 C3−C0/C5−C3를 서로 다른 participant-unit 1:1 비교로 고정하고 phase·comparison 간 participant/audio/rating/copy overlap 및 shared C3를 0으로 강제; 실제 모집·동의·배정·중재 0 | H00·delivery DAG·phase prereg/manual/auth 뒤 feasibility부터 실제 모집 |
| H03 | 외부 의존(정적 실행 준비 100%·실제 평정 0%) | pre/post/4주 유지, 조건·시점·신원 blind 독립 평가자 2인 이상 | D09 strict session/audio/QC/rating/disposition schema, fixed practice/transfer·14일·28일 retention·ITT 보존 계약; 실제 audio/rating/metric 0 | 승인된 평정자 자격·anchor·blocking·adjudication·support를 prereg에 동결하고 실제 수집 |
| H04 | 실행 준비 완료(정적 preflight 100%·실제 통계분석 0%) | mixed model/effect size/CI/sensitivity 분석 | config/feasibility·H01 lock·confirmatory H02/H03·aggregate input·stat report·completion schema/SAP·analyst template/contract/CLI/card/readiness SHA `9562274d…b1c3`/`45de774d…8f3`/`4dac929f…38b3`/`4850e106…bf59`/`96ad0b59…527a`/`fc2dd9e8…ee5b`/`01d33475…3316`/`f693ed86…e775`/`f3fe61c3…e1f0`/`e87582a6…0103`/`7a0ca62f…4c01`/`266c59e1…80a0`/`94bd1a59…d146`; full은 H00 TBD에서 보호 path/read 0으로 fail-closed, 실제 participant/statistics/effect/report 0, H04 획득 0 | H00 실제 승인 뒤 별도 feasibility pilot→H01 수동 final-N lock→SAP→powered confirmatory H02/H03→독립 분석자 순으로 실제 증거를 동결한 뒤 별도 통계 executor 실행 |

#### H00 준비도와 실제 인간대상연구 승인 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; protocol/C0·C3·C5 manual, 배정·concealment, 동의·철회, 녹음/개인정보, 보존·접근·삭제·backup, 보상·미성년자, AI 위험·사람 escalation, transfer·사고·pause/stop·amendment/expiry를 공식 판단과 hash-chain으로 검증하는 package 완료 | config-only source 12건 외 runtime 0; full은 `TBD_H00_APPROVAL`에서 approval/attestation path/read 0; targeted 35개, 관련 회귀 109개와 Ruff/format/mypy 통과 | 0 |
| H00 연구 완료 | 0%; 기관 양식·제출·공식 판단/번호·서명, protocol/consent/PIA/DMP/manual, 보존기간·보상·미성년자·transfer 범위와 current status가 없음 | browser/submission/contact/official decision/participant/audio/personal/sensitive/model/GPU/TTS/output counter 모두 0 | 0/2 |

승인·면제·동등 공식 허가 범주는 연구팀이 추론할 수 없고 기관 원문이
명시한 경우에만 future validator가 수용한다. 이 package는 기관 제출이나
법률·윤리 판단을 대신하지 않으므로 WP9 0/8과 전체 48/100을 유지한다.

#### H04 준비도와 실제 학습효과 통계분석 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; H00→별도 feasibility pilot→H01 수동 final-N lock→frozen SAP→powered confirmatory H02/H03→blind analyst 순서와 ITT mixed model·CI·missingness sensitivity·Holm을 강제하는 package 완료 | config-only source 19건 외 runtime 0; full은 H00 actual approval TBD에서 source TOCTOU 19건 뒤 H00/feasibility/H01/SAP/H02/H03/analyst/participant/rating/audio/D1/model/GPU/network/statistics/effect/report/output 접근 0; targeted 17개, 관련 회귀 83개와 Ruff/format/mypy 통과 | 0 |
| H04 연구 완료 | 0%; 실제 승인된 파일럿·표본수 lock·SAP·확증 종단자료·분석자·통계 실행이 없음 | participant/rating/audio/restricted-data/statistics/effect/report/registry/commit counter 모두 0 | 0/1 |

Feasibility pilot은 조건당 15–20명의 실행가능성·nuisance 추정용이며
효능 주장을 허용하지 않는다. 그 receipt를 confirmatory bundle로
재사용할 수 없고, 관측 pilot treatment effect를 power 입력으로 사용할
수 없다. Exact software/model family·ordinal/linear 선택·MNAR delta·MI
반복·optimizer·convergence·CI/resampling support는 frozen SAP 전까지
TBD다. 실제 산출물이 없으므로 WP9 0/8과 전체 48/100을 유지한다.

#### D09/H02/H03 준비도와 실제 종단 수집 분리

| 구분 | 현재 상태 | 확인한 근거 | 배점 |
|---|---|---|---:|
| Production static readiness | 100%; feasibility `15–20/arm`은 실행가능성·nuisance 전용이고, 완료 뒤 H01 수동 final-N·공개 prereg·frozen SAP를 거쳐 별도 confirmatory를 모집한다. 각 phase의 C3−C0와 C5−C3도 독립 participant-unit 1:1 비교이며 shared C3와 participant/audio/rating/copy overlap을 모두 금지한다 | config-only source pin 46/46, Draft 2020-12 schema 11/11, feasibility/confirmatory full은 H00 TBD에서 source TOCTOU 46건 뒤 모든 보호 counter 0으로 fail-closed; 독립 targeted·DAG 회귀 243/243, 전체 1,165/1,165, Ruff/format/mypy 통과 | 0 |
| D09/H02/H03 연구 완료 | 0%; H00, delivery predecessor, phase별 prereg/manual/auth, 모집·동의·participant/audio/QC/randomization/intervention·blind rating·completion이 없음 | actual human/model/GPU/API/network/coaching/reference/rating/statistics/effect/output/registry/commit counter 모두 0 | D07–D09 0/2; H02/H03 0/4 |

Stage 1 delivery는 `S108+E01+C02+E02+C03`, stage 2는 여기에
`E00+R03+R04`를 더 요구한다. E00의 D08/R03 권리 lineage도 aggregate
receipt에 명시적으로 결합한다. Stage 2 활성화는 승인·안전·개인정보·
delivery readiness만 사용할 수 있고 stage 1의 unblinded effect 접근은
0이어야 한다. 실제 승인·사람 연구 결과가 없으므로 WP1 10/12, WP9 0/8과
전체 48/100은 유지한다.

### WP10. 논문 개정

| Task | 상태 | 완료 기준 | 증거/산출물 | 다음 행동 |
|---|---|---|---|---|
| P00 | 완료 | 기존 목표값·주장을 결과/가설/관련연구로 구분 | `paper/CLAIM_GAP_MAP_KO.md`; S101–S103 개발 결과와 R00 완료를 제한이 붙은 O, S104–S108을 X로 유지, 현재 artifact/claim 36/36·475/475 통과 | 새 후속 artifact마다 O/G/R/X와 dev-only·oracle·silver·no-promotion 경계를 유지 |
| P01 | 완료 | 데이터·split·QC 표와 cohort 그림 | `paper/tables/aihub71490_data_qc.md`, `paper/figures/aihub71490_cohort_flow.mmd/.svg`; source→filter 산술과 자동/전문가 G0 분리를 원천 JSON에서 재계산하는 SVG renderer·receipt, 34개 공격 회귀와 PDF 시각 감사 통과 | 검증 SVG를 draft/final PDF에 680×425로 편입하고 전 페이지 시각 감사 |
| P02 | 진행 중(S104/S105/B07 amendment production-static 100%·actual 0%) | baseline/S1/S2/ablation·slice·calibration 표 | explicit/Base+/Large/S103 개발 표와 R00 완료 경계를 canonical 원고·evidence map에 반영; S103 promotion 시점 commit `f01e41af…a1c6`와 33/33·463/463은 역사적 snapshot, 현재 canonical validator 36/36·475/475, PENDING 18·locked guard 통과; S104/S105/B07 stage-aware renderer·candidate/promotion receipt의 config/module/CLI/readiness SHA `f5f95253…1ae1`/`65080d26…abd4`/`1bfe7dae…c7dc`/`aec0e04d…67cb`, targeted 6/6·전체 1,212/1,212·config-only actual counter/output 0 | 세 actual completed commit 뒤 marker 18개를 보존한 candidate bundle을 만들고 generic/deep validation·독립 수동 apply; S108·S2·calibration 및 P03 최종 표까지 실제 artifact로 채운 뒤에만 P02+P03 묶음 1점 판정 |
| P03 | 대기 | TTS blind/closed-loop/RCT 결과 | table assets | WP7–9 결과 |
| P04 | 진행 중 | 방법·통계·실패 사례·윤리·재현성 개정 | R00 완료 경계를 canonical 원고·gap map에 반영하고 직접 지원 가능한 관련연구를 유지; methods-gap `50847eb7…a90c`, 40개 중 33개(82.5%) 충족, 후속/외부 C 7개 분리; stale `9` 표기를 `7`로 교정 | B07은 S104/S105 묶음의 reviewed amendment로 반영하고 실제 S1/S2/TTS/전문가/RCT 결과 뒤 남은 7개 갱신 |
| P05 | 진행 중 | 인용·수치·표·그림 cross-check와 최종 PDF | manuscript `dea99472…17eb`, evidence-map `1a4d897f…28be`, validation report `026799ab…b7bd`, validation doc `c4007554…e051`; artifact/claim 36/36·475/475, PENDING 18, locked guard 통과; 검증 SVG `d697e42c…7ce9e`와 license ledger `b6ceeb1f…f7cd4`를 evidence chain에 결박 | publication/P04 semantic pin을 현 revision으로 재발행하고, 후속 S104–S108/S2/TTS/학습효과와 draft/final PDF를 독립 감사 |

## 5. 즉시 실행 큐

1. 완료: 논문/계획/저장소 감사를 이 추적기에 병합했다.
2. 완료: stale 설정(`preserve_one_to_many`, manifest 경로)을 실제 고정
   pipeline과 일치시켰다.
3. 완료: AI Hub raw metadata의 발화 점수를 enriched split과 결합한
   제한구역 운영용 modeling Parquet을 생성했다.
4. 완료: prompt challenge, 오류 taxonomy, label/data card와 학습
   filter를 고정했다.
5. 완료: WAV decode/resample/pad/label-aware chunk API를 구현했다.
6. 완료: native 4-source 38,729건 통합 manifest와 공개 집계 data card를
   생성했다. KSS/Seoul은 비상업 lineage, locator manifest는 R1로 고정했다.
7. 완료: explicit feature cache 생성기를 구현했다. 원본 file stem이
   `UserID`와 연결될 수 있음을 발견해 초기 미완성 캐시를 폐기했고,
   namespaced SHA-256 발화 ID로 전량 재생성·감사했다.
8. 완료: train/dev 전용 explicit 3-seed engineering run을 실행했다.
   phone F1 0.4902, 최고 Spearman 0.4780으로 gate 실패를 기록했다.
9. 외부 전문가용 G0 감사 패킷은 변경하지 않고 판정을 기다린다.
10. 완료: G04/D07용 token 보호 로컬 review UI, 원자 resume state,
    privacy-safe scorer export와 aggregate report를 구현했다. 실제 판정은
    0/240으로 유지한다.
11. 완료: pinned frozen WavLM Base+ cache builder와 100건 BF16 smoke를
    구현·검증했다. OOM 0, 100 utterance/1,798 phone/321 word이며
    locator/stem/realized identity/operation 유출은 0이다.
12. 완료: v3 smoke의 config·builder·report SHA와 resource/privacy
    조건을 독립 자동 engineering review로 승인했다. 이는 사람
    전문가나 G0 정렬 검수가 아니다.
13. 완료: 승인된 bounded streaming Base+ full cache를 생성하고 독립
    validator로 전수 검사했다. 발화/phone/word
    169,837/2,233,039/408,790행, embedding vector
    169,837/2,233,039/408,790개, nonfinite/all-zero/forbidden split/error
    0이며 cache/validation report SHA는 `34da4c3e…`/`7abb9339…`다.
14. 완료: train-only speaker-disjoint threshold ablation을 실행했다.
    Primary-dev macro F1은 0.5599로 높아졌지만 AUPRC는 0.1613으로
    불변이고 0.60 gate에 미달해 B06 실패를 유지했다.
15. 완료: 논문 수치 evidence-map과 validator를 구현했다. Base+ full
    cache, P01 표·cohort source와 clean 재현 report를 포함한 당시 실제
    aggregate artifact 20개와 claim 183개, PENDING 18개,
    explicit·threshold·WavLM cache의
    locked-test/metric 미접근 및 registry split guard가 모두 통과했다.
16. 완료: 공개 PyTorch CUDA 13.0 wheel index를 포함한 exact scorer
    lock을 별도 임시 clean 환경에 설치했다. 외부 패키지 79/79가
    일치했고 실제 CUDA 행렬 연산과 최신 source 재설치 후 당시 전체 272개
    테스트가 통과했다.
17. 완료: full WavLM cache의 모든 shard/row/vector/hash/schema/split/
    privacy/index 연속성을 읽는 독립 validator와 frozen head 3-seed
    runner를 구현했다. S101 full cache 원자 publish·독립 검증 뒤 이
    runner로 실제 3-seed 실행을 완료했으며 결과 증거는 31번에 기록했다.
18. 완료: WavLM Large pinned wrapper/config/review gate를 구현했다.
    독립 실행 전 리뷰에서 공식 pinned preprocessor의
    `do_normalize=true`와 초기 설정의 `false` 불일치를 발견했다.
    이를 `true`와 preprocessor SHA-256 `60ca5a31…`로 수정하고 local
    metadata·config·checkpoint, Base+ head report/registry/13개 artifact,
    direct-call·canonical-path 우회까지 검증하도록 강화했다. Large cache
    당시 builder/config SHA는 `1ddde94d…`/`0191afdb…`, 동일 head config
    SHA는 `a48f2963…`이며 당시 전체 215개 테스트가 통과했다. 이후 Large v1
    smoke의 독립 review 실패는 32번에 보존했고, 수정된 v2 승인과
    canonical full 실행 시작은 34번에 기록했다.
19. 완료: frozen head 독립 리뷰에서 registry 기록 실패 시 이미
    publish된 완료 output이 남는 원자성 결함과 실행 소스 hash 누락을
    발견했다. 실패 output 원자 격리·삭제, trainer/head source hash-chain,
    config 기반 encoder variant, task별 RNG 재초기화와 import-time
    `CUBLAS_WORKSPACE_CONFIG=:4096:8` guard를 구현했고 CPU fault-injection
    포함 당시 scoped 10개 테스트와 정적 검사를 통과했다. 실제 학습 직전
    fresh-process deterministic CUDA forward/backward smoke를 수행해
    SHA-256 `878842435419924894c6b9713f7e133afec73d492b6379d05eed47f3dfec32f6`로
    고정한 뒤 3-seed 학습을 실행했다.
20. 완료: 실제 Base+/Large/S103 report가 생성된 뒤에만 수치를 표로
    내보내는 strict P02 renderer를 구현했다. Head·registry·13개 artifact·
    comparison hash-chain, seed/평균·event count·bootstrap·Holm 수치를
    재검산하고 비식별 aggregate Markdown/JSON만 원자 출력한다. 그 시점에는
    당시 synthetic 25개 테스트만 통과했으며 가짜 결과나 원고 수치 변경은
    0건이었다. 후속 하드닝 증거는 36번에 기록했다.
21. 완료: S103 Base+–Large strict paired comparator를 구현했다. 동일
    seed·ID·target·speaker·prediction order와 registry/source/artifact
    hash-chain을 강제하고 phone macro F1/AUPRC/contiguous-event F1,
    utterance 네 지표, 화자 단위 paired bootstrap 5,000회 95% CI,
    0.03 point-estimate gate와 Holm 보정을 aggregate-only report로
    계산한다. 당시 synthetic 7개 및 전체 233개 테스트를 통과했으며
    실제 Base/Large report 전에는 실행하지 않는다. 후속 하드닝 증거는
    36번에 기록했다.
22. 완료: H01의 planning-only power/preregistration 패키지를 구현했다.
    C0↔C3/C3↔C5 두 병렬 단계, 2주 post와 그 4주 뒤 retention, blind
    comprehensibility, ITT/missingness, rater random intercept, Holm,
    비효과 기반 stop rule을 고정했다. 가정값 243-grid와 실제 aggregate
    pilot nuisance 입력을 분리하며 `final_sample_size=null`을 유지한다.
    독립 검증한 planning 산출물은
    `artifacts/planning/learning_effect_power_sensitivity_v1.json`
    (SHA-256
    `d63dc95bb4df4967775781def0386beb7ec959c396d847ae6b6e8c982dbce66d`)이다.
    실제 상태는 `assumed_sensitivity_only` 243건, pilot-informed 0건,
    `final_sample_size_locked=false`, `h01_completion_claim_allowed=false`다.
    IRB·모집·학습자 녹음·pilot 입력·확증 효과 플래그는 모두 false이고,
    raw/restricted·환경 파일·참여자 행을 읽지 않은 비식별 aggregate-only
    산출물이다. 실제 IRB·참여자·pilot 분산 전에는 H01의 외부 완료를
    주장하지 않는다.
23. 완료: Base+/Large 100건 smoke를 수동 JSON 편집 없이 승인·거부하는
    독립 reviewer를 구현했다. 정확 100건, 모든 shard/hash/schema/join/
    index/embedding/privacy/frame/resource와 model/preprocessor pin을
    전수 확인하며 canonical full 경로 덮어쓰기를 차단한다. 당시 synthetic
    22개를 포함한 전체 272개 테스트가 통과했고 실제 Large smoke
    완료 후에만 승인 report를 생성한다.
24. 완료: Base+ full cache의 실제 행·vector·shard·resource와
    2,289,794,304개 embedding scalar 전수검증 결과를 원고에 반영했다.
    BF16 autocast 요청, 관측 float32 hidden output, float16 저장을
    구분하고 head 성능은 `PENDING`으로 유지했다.
25. 완료: privacy-safe AI Hub 데이터·split·QC 논문 표와 편집 가능한
    Mermaid cohort flow를 생성했다. 322,380→185,751→185,109→169,837
    disposition과 137,759/32,078 train/dev 합, 자동 G0 수치를 고정
    artifact로 재검산했고 외부 전문가 정렬 G0는 `PENDING`으로 분리했다.
    당시 evidence validation은 artifact 20/20, claim 183/183, PENDING
    18개 및 WavLM cache metric 미평가 guard를 모두 통과했다.
26. 완료: 원고·실행계획·tracker의 핵심 주장을 O(observed engineering),
    G(pre-specified gate/target/protocol), R(related-work/design),
    X(external/PENDING)로 분류한 paper claim gap map을 작성했다. Silver
    label·gate·후속 통계 protocol·외부 미완료의 혼동 표현을 교정했고,
    PENDING 18개와 locked-test guard를 유지했다. 관련연구 원문 source와
    외부 실험은 별도 미완료로 남겼다.
27. 진행 중: P04 방법·통계·실패·윤리·재현성을 A(현재 증거),
    B(즉시 보강), C(후속/외부 대기), D(해제 조건) 40개 항목으로
    분해했다. 선행 문서 충돌을 해결하고 B 14개를 원고에 반영해 현재
    31/40(77.5%)를 충족했으며, 후속/외부 대기 9개를 남겼다.
28. 완료: R01–R08 관련연구의 논문·공식 repository/model·실제 LICENSE
    36개 URL을 1차 자료로 감사했다. WavLM/HiPAMA/Whisper/HuBERT는
    적용 범위를 제한한 partial verification, CosyVoice GRPO 공개 존재와
    공식 F5-TTS 코드 MIT 및 README/HF metadata의 CC-BY-NC weight 선언
    차이는 확인했다. Weight 저장소의 standalone LICENSE와 `F5R-TTS`
    정체는 미확인이다. Robust multi-reference 우월성·UTMOS 한국어 L2
    과벌점·기존 달성형 주장은 unverified로 유지했다.
29. 완료: 외부평가 패킷의 구형 crossover/rater 누락을 최신 H01
    participant 1:1 병렬 protocol과 정렬하고, WavLM validation 문서를
    Base+ full `completed/passed` 실측과 Large 미실행 상태로 교정했다.
30. 완료: WP10-P04 첫 편집 묶음으로 frozen-head/H01 planning 방법,
    실행 통계와 후속 bootstrap·Holm·mixed-model 계획의 분리,
    privacy/run/config/atomicity 교정 사례, 개인정보 zone·동의·license
    표, clean 환경·결정성·registry 재현성과 직접 지원 가능한 관련연구
    11개 참고문헌을 원고에 반영했다. 당시 evidence는 20/20 artifact,
    183/183 claim, PENDING 18개, locked-test 접근 0으로 재통과했다.
31. 완료: registry-integrated frozen WavLM Base+ head를 seeds
    42/43/44로 실행하고 report와 registry를 독립 검증했다. Final report
    SHA-256은
    `112eedfeb6348c1b5a258ae2ca7fd4b435c96b433d3326ade94a7f4399949a2c`,
    completed registry SHA-256은
    `0f994aa578a503697c53a3ed84377baea38ca34ebd4fc6e01bafac7a31b9f79f`,
    run ID는 `aihub71490-wavlm-base-plus-frozen-head-v1-dev`다.
    Report/registry status는 `completed`, registry failure는 `null`이고
    checkpoint·phone/utterance dev prediction·slice와 final report
    13개 artifact가 등록됐다. Phone macro F1은
    0.669699±0.001629, AUPRC는 0.353243±0.002218이다. Utterance
    QWK/Spearman/MAE/ICC(2,1)는 pronunciation
    0.098042/0.185194/0.837445/0.058316, fluency
    0.037078/0.199315/0.850812/0.043062, comprehensibility
    0.080252/0.155166/0.791802/0.052146이다. 이는 train-only
    inner-validation 뒤 seed당 dev 1회로 얻은 development-only
    oracle-alignment 결과이며 stack/calibration/locked-test는 읽지
    않았고 promotion은 허용하지 않는다. 사전 고정 원장에 따라 metric
    품질이 아니라 S101 head artifact 완료에 2점을 부여해 WP4를 3→5,
    전체를 37→39로 갱신했다.
32. 실패 보존·진행 중: WavLM Large v1 비canonical smoke 추론은
    100 utterance/1,798 phone/321 word, OOM 0으로 완료됐고 report
    SHA-256은
    `67b395299c94b5d58a489e9ed0da8eb4c140522f3a0454639acd13ec68b8bba2`다.
    그러나 독립 reviewer가 `phone_required_split_missing`,
    `utterance_required_split_missing`, `word_required_split_missing`을
    검출해 full 실행을 차단했다. Failed review artifact SHA-256은
    `5cd9e7571166b4676ecd4b877e282fd443de81a8931f5c82a5fe9e19ae1331f5`다.
    Ordered canonical manifest에서 bounded first-100이 train-only인 반면
    reviewer가 train/dev 양쪽을 요구한 계약 불일치가 원인이며 현재 수정
    중이다. 새 noncanonical smoke와 독립 review가 `passed`하기 전까지
    full은 NO-GO이고 S102 획득 점수는 0이다.
33. 완료: S101 Base+ 3-seed head의 개발 지표·registry·해석 제한을
    원고와 evidence-map에 연결했다. Canonical manuscript SHA-256은
    `da36092c0080a066670ca84753cec010614de5552c8b321b6af8a838b372ccd3`,
    evidence-map SHA-256은
    `a283d92348970698c3421c3d7857adda3da30df1d03a591352ef17ae90aeb9f3`,
    validation 문서 SHA-256은
    `8d26cfc48dcb23030cc81494ba4642952b5a1669533f8a7e6379d2987ce5767e`,
    validation report SHA-256은
    `d0bec9edc1b59786b29574cbac1de462290b066ab57e997413101b5847d77660`다.
    Artifact 25/25와 claim 231/231, PENDING 18 marker hash, 모든
    locked-test guard와 허용 registry split이 통과했다. Base+ 값은
    development-only oracle/silver engineering 결과이고 promotion
    근거가 아니며 S102–S108은 미완료다. P02는 계속 `진행 중`, P04는
    31/40, 배점 원장과 전체 진행률은 39/100으로 유지한다.
34. 진행 중: 수정된 WavLM Large v2 비canonical smoke를 100 utterance/
    1,798 phone/321 word, OOM 0으로 완료하고 독립 review 승인을 받았다.
    Smoke report SHA-256은
    `38cef40a99542fe881f03ad7ec00bb1b9b6ff4c2f3859d81d3e15b15a672f813`,
    approved review SHA-256은
    `42b58406c761cc3bfda2694f2bafde62b27553da5f7462b3272ab34fbdf7bd8c`다.
    Large builder/reviewer CLI/reviewer core/config SHA-256은 각각
    `bfca5a5d065a7a55b3fce5671750dd4011f8a2b0e538d3c3a6aa8eab4972772f`,
    `6e42189f5a927c9ba91062e04ca5337c5a8adff94b28cd1e56f826c85ce261f3`,
    `b7dc9a105d9817daa8d109910106c4bea74b0742c09b1ef5f8292ca6f9b7c85c`,
    `0191afdba13e38619ab6f2c24494f0766cf9f16a802575a132eda7dadcd9c4fd`다.
    선행 Base+ report/registry SHA
    `112eedfeb6348c1b5a258ae2ca7fd4b435c96b433d3326ade94a7f4399949a2c`/
    `0f994aa578a503697c53a3ed84377baea38ca34ebd4fc6e01bafac7a31b9f79f`
    까지 묶은 full combined gate가 통과해 canonical Large full 명령을
    exec session `28800`에서 시작했다. Smoke는 manifest 순서상
    train-only이고 `allowed_split_coverage_complete=false`이므로 full
    cache의 train/dev 완전성을 증명하지 않는다. Full 독립 validator가
    모든 kind의 train/dev를 통과하기 전에는 completion을 주장하지 않으며
    S102 점수 0, WP4 5/18, 전체 39/100을 유지한다.
35. 완료(post-validator 구현·실제 validation 문서 갱신, S102 head
    미완료): Large post-validator의 CLI/core/test/current doc SHA-256을 각각
    `5c7ade92adadfd16f443414f4d23848724e7889cc2499a6f64e7245f60c18038`,
    `f37889e52d3b63bdda6adc6563dc48ccb3a556e6afc83cefb3c39d33ec969518`,
    `bffc15efc5407545ff982cb1c892a38f21c9e0b4dec05a809e8d3c44dee62008`,
    `1bc88121ff2771465c73f3407745818707e9cab9ce88b7a46f318f32d530b6f8`
    로 고정했다. Current validation doc은 canonical Large full의 실제
    `passed` 결과와 전수검증 수치를 포함한다. 독립 코드 감사에서
    critical/high/medium(C/H/M) blocker는 0건이었고 구현 검증 당시 관련
    138개와 전체 409개 테스트가 통과했다. 실제 cache·validation 완료
    증거는 실행 로그 37에 기록했으며, 현재 Large head가 실행 중이다.
    Head report/registry 완료·독립 검증 전에는 S102 완료·4점을 주장하지
    않는다.
36. 완료(구현·합성 하드닝만, S103 미실행): S103 comparator의
    config/CLI/module/test/doc SHA-256을 각각
    `4db7ae9d9d7df9e9f35df65534d18fb6d3c46cb98f45dfaa35fadcdd5fe47e27`,
    `53a15aabbefb91751890693a1861a0679cef460555b31a6c3cc7e246b8980f13`,
    `8a6a9757b1ef0cfc3322935e975d9377b9580225346d4e4c6553bfabc1393381`,
    `c45d72fd5368742b82b0f73cfd3d22b88b3417b4e3c2f5b69b2ced474335667f`,
    `5d4e2ef596249e573e21d7731526e4f4f0ba544cde96687f576a5c451a1c0169`
    로 고정했다. Renderer CLI/core/test/doc SHA-256은 각각
    `cb1a90230611be4348d86b74b983f67a17a62c03c27fe13eae0af9c515243c97`,
    `b8875563cce001279d28481dcadb0d7f33c09192605705533a0b0363cf6d8703`,
    `42d4c93ca17a7b60ce77705f11ff483aeea1d147daa5895d7e86622cc55ed904`,
    `b38110aa01d99b5f12055f79daa101b0f18876ee4709a6d751015100e2ef3c84`
    다. Prediction의 exact Arrow 순서·타입·nullability·metadata,
    workspace 내부 exact path와 완료 registry/source/artifact hash-chain을
    pin하고, content-addressed 불변 report+execution receipt를 단일
    comparison commit으로, 렌더러 Markdown/JSON을 별도 output commit으로
    원자 publish하도록 하드닝했다. 모든 report/receipt/output/commit의
    file·directory fsync 실패 경계를 주입해 pre-commit 정리와 post-commit
    선형화를 구분했고, registry의 모든 전이 input/artifact 출력 충돌,
    casefold된 `.env*` 경로, commit과 계산된 generation 파일의 자기충돌을
    쓰기 전에 거부한다. Targeted 135개와 전체 484개 테스트가 통과했고
    독립 최종 재감사 결과 critical/high/medium(C/H/M) blocker는 0건이다.
    그러나 실제 Large head, comparison report/receipt/commit, renderer
    Markdown/JSON/output commit은 모두 없다. 다음 행동은 S102 실제
    검증·Large head 완료 뒤 비교와 렌더링을 순서대로 실행하는 것이며,
    그 전에는 S103 완료·2점을 주장하지 않는다. 따라서 S103 획득 0,
    WP4 5/18, 전체 39/100을 유지한다.
37. 완료(cache·독립 validation만, Large head 미실행): canonical WavLM
    Large full cache가 원자 publish됐고 report SHA-256은
    `98fe95d00e2faff9b21262c3f863134879df0eb45d392648b9aadc250455fe47`,
    독립 validation SHA-256은
    `d3f34b34a9bdf85844f43d4186ccdf46736f616ad1b61d806e9c59914783676e`
    다. Utterance/phone/word 169,837/2,233,039/408,790행과
    17/9/5=31 shards, embedding value 3,053,059,072개를 전수검증했다.
    Split은 train/dev뿐이고 nonfinite value, all-zero vector, forbidden split
    row와 OOM은 모두 0이며 full gate가 통과했다. 실제 cache
    5,851,341,472 bytes는 smoke에서 사전 고정한 upper
    7,481,336,023 bytes 이하고 temp partial은 남지 않았다. Large head
    trainer native preflight도 통과했지만 당시 output은 생성하지 않았다.
    검증 직후 fresh process에서 Large head seeds 42/43/44 실행을 시작했다.
    Exec session은 `81862`, PID는 `366801`, registry run ID는
    `aihub71490-wavlm-large-frozen-head-v1-dev`이고 status는 `running`이다.
    현재 출력은
    `.aihub71490_wavlm_large_frozen_head_v1.part-6c129e69e92a4edeaa25dbb13cb1ba83`
    뿐이며 canonical output은 없다. Head report/registry 완료·독립 검증
    전에는 S102 완료·4점을 주장하지 않는다. S102 획득 0, WP4 5/18,
    전체 39/100을 유지한다.
38. 완료(S104 실행 준비 구현·독립 하드닝만, 실제 GPU 미실행):
    S104 config/trainer CLI/contract/training/adapter/readiness 문서 SHA-256은
    각각
    `fa02145f7c86b1e57b4cc1a1316fd433c34705707e860f5888b6e946ea892bf7`,
    `68048e553fbb50f291588076897c9a7372c6bb557b8b8b952d23c1c60917cda5`,
    `32383ac84816b9d18f26d532c0c60b6dde2bf88ecbea5c47eb7df393a8550472`,
    `a7b41d507385c7f45b102f2d1603ad4214f0247ad9e0e08c6a3640f2df6bed80`,
    `6b5a89bbf26593c9aa8b2720b305cf448fba4c10510e890ff7339e5f4f6121f5`,
    `fa6136bf403c4d8314b979af8a95758971d9d2ba7b3fe945d9cc834b05b4f8f3`
    이다. Online raw-audio adapter/head joint trainer, train-only inner
    selection, seed당 dev 1회, seed당 5개와 aggregate 1개인 exact 16개
    content-addressed artifact, completed/failure registry, commit pointer와
    독립 commit validator를 구현했다. 실제 metadata audit의 primary
    범위는 167,506 utterances/2,199,869 phones이며 시간·frame-gradient
    blocker는 0이다. S104 묶음 93/93과 저장소 전체 557/557 테스트,
    Ruff/format/mypy 및 실제 Base+/Large snapshot pin과 Base+ registry
    전이 49/49 검증이 통과했고 독립 최종 C/H/M blocker는 0건이다.
    Full preflight는 실제 S103 commit 부재에서 fail-closed로 종료하며
    execution lock/commit/generation output은 0개였다. 실제 S104 CUDA
    학습, metric, checkpoint, commit과 completed registry는 모두 없고
    결과를 논문에 쓰지 않았다. 따라서 S104 획득 0, WP4 5/18, 전체
    39/100을 유지한다.
39. 완료(paper-promotion fail-closed 실행 준비만, 실제 후보 없음):
    WavLM paper-promotion module/CLI/test/doc/template SHA-256은 각각
    `7e836ad6cd0dfef1b339558082ae750b76bd7be5a261c88a49c5ef139758ec`,
    `e713c9ba9e180b77075dc64065d3127a165e8f4b833103573584b5c517da3bea`,
    `ac7cd3195b111a9c80b2e7ba373221324c1672b4f31b51e16dc331bf80144450`,
    `dc3c1fc874f9e39d999b15d21475828c8a5515e5d37a11cafde6f5d6b838a5ff`,
    `05ac255f8f51785dfa9645bcfd65fb6a12a1fb03fdfeb61eff5a8341bebcc2c4`
    다. Large report/registry, S103 report/receipt/commit과 results-table
    commit/Markdown/JSON의 전이 hash·수치·scope를 재검증하고 candidate
    manuscript/evidence-map/receipt/commit을 content-addressed bundle로만
    준비하는 gate를 구현했다. 새 단위 11개, paper 범위 160개와 당시
    전체 510개 테스트 증거가 통과했다. 그러나 실제 Large completed
    head와 S103/results-table commit이 없어 template은 `pending`이고
    ready manifest, candidate generation과 promotion receipt는 없다.
    Canonical 원고 SHA
    `da36092c0080a066670ca84753cec010614de5552c8b321b6af8a838b372ccd3`,
    evidence-map SHA
    `a283d92348970698c3421c3d7857adda3da30df1d03a591352ef17ae90aeb9f3`
    및 PENDING 18개는 바뀌지 않았다. 따라서 P02/P03 완료나 paper 추가
    점수를 주장하지 않으며 WP10 3/5와 전체 39/100을 유지한다.
40. 완료(S102 Large frozen-head 개발 실행·독립 검증): canonical Large
    head report SHA-256은
    `ba869265c6e6c1381677fe922922b68da7df92cbb9cbaf5501fff5dafa3fdd23`,
    completed registry SHA-256은
    `85c90e95b954ec2b74081f44b4e64d311330985a0429d76f0cb67b7a86d5b565`
    이다. Registry run ID는
    `aihub71490-wavlm-large-frozen-head-v1-dev`, status는 `completed`,
    failure는 `null`이며 seeds 42/43/44와 checkpoint·phone/utterance dev
    prediction·slice·final report 13개 artifact를 등록했다. 현재 strict
    renderer head contract로 report/registry/aggregate/artifact를
    검증하고, 전이 input/artifact 49개를 실제 SHA-256으로 모두 다시
    읽어 unique 49/49를 확인했다. 이어 prediction Parquet의
    phone 423,820행과 utterance 32,063행을 직접 읽어 seed별 metric을
    재계산해 report와 대조했다. Phone macro F1은
    0.671164±0.000443, AUPRC는 0.360830±0.000669이다. Utterance
    QWK/Spearman/MAE/ICC(2,1)는 pronunciation
    0.162764/0.274672/0.802580/0.140056, fluency
    0.271806/0.495003/0.791396/0.307800, comprehensibility
    0.129550/0.260861/0.771907/0.135741이다. 이는 train-only
    inner-validation 뒤 seed당 dev 1회로 얻은 development-only
    oracle-alignment 결과이며 stack/calibration/locked-test는 읽지
    않았고 promotion은 허용하지 않는다. Full cache·독립 validation과
    Large head 3-seed 완료 기준이 모두 충족돼 S102 4점을 부여하고
    WP4를 5→9, 전체를 39→43으로 갱신했다. 다음 정본 명령은
    `PYTHONPATH=src ./.venv-scorer/bin/python scripts/compare_wavlm_frozen_heads.py --config configs/experiments/wavlm_frozen_head_comparison_v1.yaml`이며,
    이 comparator가 만든 commit을 독립 검증하기 전에는 S103 완료·2점을
    주장하지 않는다.
41. 완료(S103 Base+–Large 개발 비교·독립 검증): comparison
    commit/report/execution receipt SHA-256은 각각
    `e45cf869ab54beff6bcf5a3c674c0bdff0d75460ab8daabe50c7477afca16c6e`,
    `90db58ee3af3bb47b88a8ae1700e6a0a9ce9f16feeaa06b8e1c5d7ffbe778213`,
    `342a9bd2b97de87a1bfd38c3ed0a22819b1151eb37836d90e941951781f620a6`
    이다. Results-table commit/Markdown/JSON SHA-256은 각각
    `af0cb1d616e3c9377985b880bb19233647964b744ab23c98e6b69d257c273e38`,
    `89db29654f8c542154040e1626131fc2df29ea71ad0c99b36e531244cd255b0f`,
    `d8556c3a433e5d09b119ffaa567735c34bf10f19ba065a3173602d44ab5d7a20`
    다. 현재 strict loader로 두 head, comparison commit·contract·source,
    privacy, metric, primary·multiplicity 계약을 모두 다시 검증하고
    renderer payload와 Markdown을 메모리에서 재생성해 committed
    JSON/Markdown과 exact 일치를 확인했다. 이어 Base+/Large prediction
    Parquet의 phone 423,820행과 utterance 32,063행을 직접 읽어 seed별
    지표를 재계산하고, 동일 5,000회 화자 단위 paired bootstrap을 독립
    재실행했다. Primary Large−Base+ phone macro F1은
    `+0.001465695617904439`, 95% CI는
    `[0.0000061248957422009065, 0.0029903885141528744]`,
    two-sided p는 `0.04919016196760648`로 report와 정확히 일치한다.
    Point estimate가 사전 고정 `+0.03` 채택 gate에 미달했으므로 Large
    확대를 중단하고 S104의 Base+ adapter branch를 선택한다. 이 결과는
    development-only oracle/no-promotion이며 보호 split에는 접근하지
    않았다. Artifact 완료 원장에 따라 S103 2점을 부여해 WP4를 9→11,
    전체를 43→45로 갱신했다.
42. 완료(S103 논문 승격·정본 통합 독립 검증): paper-promotion
    commit/receipt SHA-256은 각각
    `f01e41af8bdbeb08f599ab6d84ed1a8ee7fc1b0da6d10802711300a5d4d0a1c6`,
    `0c1806ca7ab16ac977bfb565483e013d5cc6e770591ba94a194f9aa7eb73dab0`
    이고 generation ID는
    `9aac308f22b03b6f7fd71fd9dae7ade92c7ab92fe1fa446979a4f2a86894f9c3`
    이다. Commit loader와 candidate validation이 통과한 bundle을 독립
    검토 뒤 정본에 적용했다. 현재 canonical manuscript/evidence-map과
    validation report SHA-256은 각각
    `f0ed2301910eef608bfd7a7c5be879ab233ff29e7a080aecaabcd6e0e39b6d98`,
    `1614d1512604146f1773193a5a1919f2c2892ea16d48872e90c6f064ba31df88`,
    `2f33749d73bbdf59f18312db8c7d315594f92f91e4c9d402790afe7a6f15ba20`
    이다. Canonical validator는 artifact 33/33, claim 463/463,
    PENDING marker 18개와 locked-test guard를 모두 통과했다. S103
    개발 표와 범위 제한은 실제 원고·evidence-map에 반영됐지만
    S104–S108, S2, ablation·slice·calibration 및 P03 외부 결과가
    남아 있으므로 P02는 `진행 중`이다. P02+P03 묶음 1점은 부여하지
    않으며 WP10 3/5와 전체 45/100을 유지한다.
43. 완료(S104 첫 full preflight 안전 실패 보존·overlap 계약 최소
    교정): 실제 첫 실행은 `S104 registry root overlaps an input`으로
    fail-closed 종료됐고 execution lock·학습 결과는 쓰지 않았다. 원인은
    predecessor registry가 함께 있는 공용 `artifacts/experiment_registry`
    parent 전체를 input과 대조한 과도한 overlap 판정이었다. 공용 parent
    대신 새 run의 planned registry directory만 검증하도록 최소
    교정했으며, registry-root 탈출·symlink·predecessor run directory
    collision 거부는 유지했다. 현재 config/contract/preflight
    integration test/readiness 문서 SHA-256은 각각
    `aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9`,
    `a5c3c933b4fb55cbe0f8c33deb70a98bf47b53539304f6299b0728188b9ea06c`,
    `ce97c67a3e1605ad5b12cdb62df67c3518a515181d6800e35f50347b400cba08`,
    `0e2a3779fa8f682ab80fe0b60dfbf1a5174cecc595ddda3c56bcf5d71f60e0f2`
    다. Preflight integration 36/36, S104 4-file 묶음 83개와 별도 독립
    targeted 62개 테스트가 통과했고 Ruff check·format check와 mypy도
    통과했다. 이는 실패 복구와 실행 준비 증거이며 S104 완료나 배점
    근거는 아니다.
44. 진행 중(S104 immutable Base+ execution lock·정본 GPU 학습 시작):
    교정 뒤 실제 full preflight는 `ready_for_s104_gpu_training`으로
    통과해 S103 gate가 선택한 Base+를 고정했다. 전체 train/dev
    169,837발화와 primary 167,506발화를 검증했고 preflight의 raw-audio
    open과 CUDA/GPU 실행은 모두 0이다. Immutable execution lock
    SHA-256은
    `8d8778061ec8e366208618071a806e2b04c78e22612ffdabdd6a46f047d274ca`
    다. `CUDA_VISIBLE_DEVICES=0`,
    `CUBLAS_WORKSPACE_CONFIG=:4096:8`로 canonical 3-seed trainer를
    2026-07-29 08:41 KST에 시작했으며 registry run ID
    `aihub71490-wavlm-s104-adapter-v1-dev`는 `running`, failure는
    `null`이다. 실제 metric·generation artifact·commit·completed
    registry와 commit-only 독립 validator가 모두 확인되기 전에는 S104
    2점을 부여하지 않는다. 따라서 WP4 11/18과 전체 45/100을 유지하며,
    현재 순서는 실행 모니터링→`--validate-commit-only`→S105다.
45. 진행 중(S104 readiness 실행 상태 동기화): GPU admission 뒤 readiness
    문서를 canonical GPU 실행 중 상태로 갱신했으며 현재 SHA-256은
    `2998840326831ab67dc518ccf5ed0bcb05b75d1ed47b3c5506e316a90dbfd6b3`
    이다. 43번의 `0e2a3779…e0f2`는 GPU 실행 전 preflight snapshot으로
    보존한다. Registry는 `running`, failure `null`이고 실제 metric·
    generation artifact·commit·completed registry는 모두 0이다. 완료
    commit·validator가 없으므로 S104 0점과 전체 45/100은 변하지 않는다.
46. 완료(S105 production readiness만, 실제 실험 0): S104 결과를 보고
    설계를 바꾸지 못하도록 5개 누적 variant×3 seeds, 동일 시드의 frozen
    S104, train-inner 선택과 seed당 dev 1회, word label/loss/PCC 금지,
    paired-speaker bootstrap 5,000회·Holm·worst-group-drop 계약을
    사전 고정했다. Production runner는 정확히 82개 generation artifact,
    variant별 5개 registry, content-addressed 원자 publish, 동시 실행
    무덮어쓰기, OOM/fault/recovery와 deep commit validation을 구현한다.
    Config/validator/readiness 문서 SHA-256은 각각
    `e7818240f477af9957c4e13b88479a69820bbb44d48ece2c47e9a54cfc964ea0`,
    `e2f7269c757ddee82f33de6787df466b29cbe9e1e2b1fed90130eaaec1c53ad1`,
    `f5839e59a1319f435d8503816bd87c78c574b16901888bdc40714adedc016ed1`
    이다. S105 targeted 53개와 관련 회귀 63개, Ruff/format/mypy 및 독립
    C/H/M 0 검증이 통과했다. Config-only는 S104/data/raw/CUDA를 읽거나
    출력을 쓰지 않았고, 실제 full preflight는 S104 commit 부재에서 종료
    코드 1로 fail-closed되어 S105 output을 남기지 않았다. 따라서 실제
    5 variant×3 seed GPU 실행, checkpoint, metric, generation, completed
    registry, commit은 모두 0이고 S105 0/2, WP4 11/18, 전체 45/100을
    유지한다.
47. 완료(S106 production readiness만, 실제 실험 0): Explicit·frozen
    S104·선택된 S105 hierarchy를 exact key로 결합하고 seed mean/variance를
    join 전에 계산하는 stack-only runner를 고정했다. Label과 분리된
    20개 feature allowlist, speaker-disjoint 5-fold OOF 선택, in-sample
    metric을 내지 않는 final fit, 정확히 7개 generation artifact,
    completed registry·immutable commit, content-addressed 원자 publish와
    deep read-only validator를 구현했다. Config/contract/training/readiness
    문서 SHA-256은 각각
    `b7a22d6e1a3448146b15016fcf5c5242391c744baba0c37f0b1d344109e9f31d`,
    `c58a4e89800f920156deebcaa16ea84c47120fd249cda24ba1ee6d8364539d4a`,
    `728254ed1dfe4220e1217560e1089c117f5cc73bb232653c93406863dd13ccdd`,
    `04cfa1056adb93894e1bd6e354456ac5ed2ad782706da93f1835dc85ce128503`
    이다. Unit 20개와 integration 11개, Ruff/format/mypy 및 독립 C/H/M
    0 검증이 통과했다. Config-only는 predecessor/data/stack 접근과
    output이 0이었고, 실제 full preflight는 S104 commit 부재에서 stack
    locator 해석 전에 종료 코드 1로 fail-closed됐다. 따라서 실제 stack
    access, execution lock, component bundle, OOF metric, generation,
    registry, commit은 모두 0이고 S106 0/1, WP4 11/18, 전체 45/100을
    유지한다.
48. 완료(S107 production readiness만, 실제 calibration 실험 0):
    Calibration split만 허용하는 scalar temperature calibrator,
    provisional issue/abstention policy, reliability·risk-coverage·slice
    reporter와 정확히 7개 artifact의 content-addressed generation,
    completed/failure registry, immutable commit·recovery·deep validator를
    구현했다. Config/contract/production runner/readiness 문서 SHA-256은
    각각
    `ac01b9e7c96c76deb13c7716f7e5d8c017f781d64ee4f02e22605d7a9473ab3d`,
    `6aaa2042db254f38a8e3d12d6aa2b78acab5f0947e28bc59b723e666399a0cc4`,
    `201973a25e75da77ec86468a60094c22ac3d3376d936d5e2d089531d52b5d6d3`,
    `d081997fe271eb90b91831f630966b291870dff7284121ed045bdacd8113d74b`
    이다. 독립 config-only 검사와 관련 46개 테스트, Ruff와 mypy가
    통과했다. Config-only는 predecessor/calibration/data/raw/env와
    output 접근이 0이었고, full preflight는 S106 commit 부재에서 종료
    코드 1로 fail-closed되어 실제 calibration locator나 row를 열거나
    output을 남기지 않았다. 따라서 실제 calibration bundle·execution
    lock·fit·metric·generation·registry·commit은 모두 0이고 S107 0/1,
    WP4 11/18, 전체 45/100을 유지한다.
49. 완료(S108 no-locked 구현·정본 dry-run snapshot만, 실제 final/locked
    결과 0): Aggregate-only predecessor·외부 gate 상태기계, 원자
    publisher와 read-only validator를 구현하고 현재 상태의 정본
    no-locked snapshot을 발행·검증했다. Config/CLI/module/readiness 문서
    SHA-256은 각각
    `b59ddeb1354d8c72875269ce433830eb0482af9714a84f9ab104741331826d15`,
    `467ef6011338ec48ef1485ce118466f5e440b4f7828f57e1bf6767622703b6b6`,
    `62c51fb33b8510318d59c5ac76076441397735d6582a6cd8a809ca93e6784799`,
    `0d6fa204d4bd6cb7510190ac497f3dff443515a1fef613801459758ed8bdd57d`
    이다. Canonical generation ID는
    `73f85af411619e341b19cd78966f39acaf84e4cf9346bb34195cb222af0aaa92`,
    checklist와 receipt SHA-256은
    `dd34ec5cdb65a3eaf210f806645f5cccd780bf3fd062dc89daf2ff7199d9375f`,
    `d90cb96fdb699938e9e6e1517412384b3a61efcf3dc4abfe475fec3cc41ffa88`
    이며 validator는 `valid`다. 세 개의 과거 비정본 snapshot은 삭제·
    덮어쓰기 없이 보존했다. 현재 상태는 `engineering_in_progress`이고
    S103은 valid, S104는 `commit_missing_registry_running`이며
    preregistration/G04/D07은 각각 blocked, `ready_for_locked_batch=false`
    다. Locked locator/row/audio/label/prediction/전체 접근 6개 카운터와
    row-level/protected/raw/env 접근은 모두 0이고 S108 completion·score,
    research metric, PENDING 해제와 promotion은 모두 false다. S108 관련
    29개, S107→S108 회귀 44개, 저장소 전체 687개 테스트와 Ruff/mypy가
    통과했지만 이는 no-locked 준비 증거일 뿐 실제 final/locked 결과가
    아니다. 따라서 S108 0/1, WP4 11/18, 전체 45/100을 유지한다.
50. 완료(S200 production readiness만, 실제 S2 0): S1과 다른
    backbone·정렬·holdout·목표함수를 강제하는 독립 감사 계약,
    fail-closed preflight와 S2 data card를 구현·합성 검증했다.
    Config/contract/CLI/data card/readiness 문서 SHA-256은 각각
    `33f9c39805bdc5ec5542e5cd444a2629181d731a747dfe495975f4efa816a4fe`,
    `b9f13b578ef89586c38fd2d80b0d8d985cf735d52e2532c2958888cb1da1a35b`,
    `9b4e528f025743fe18dfcf7e6bb3a2ed3e57d3bcf05c76ce459ceac520a5b9d1`,
    `fb362153895cd5021cd7e066faee28dc0d3137a5d58c9af906f6788ee0d14256`,
    `dba78f8b69a2fb1972f7b2831f25f4257fe74b4dea680613bb061e3c55694cbd`
    이다. 관련 18개 테스트와 Ruff/mypy 및 독립 C/H/M 0 검증이
    통과했다. Config-only는 source 파일 12개만 읽고 predecessor,
    external, data, model, protected, holdout, locked, GPU, metric,
    result, output 접근이 모두 0이었다. 실제 full preflight는 미래
    S108 final `research_candidate_passed` commit 부재에서 predecessor
    read attempt/success 1/0으로 fail-closed됐고 external과 나머지
    접근·output은 모두 0이었다. S201/S202 trainer·모델 download·GPU
    실행·실제 metric/result도 모두 0이다. 따라서 S200 상태는
    `실행 준비 완료`지만 실제 S2 단계는 `대기`이고, S200 0/1, WP5
    0/8, 전체 45/100을 유지한다.
51. 완료(C01 engineering contract·ontology readiness만, 연구 완료·배점
    보류): 유일한 개인정보 안전 aggregate source SHA-256
    `4ab53c6837b1a28fbf153dd3a351a195f6931a43694467114cea6c21b8a57b13`
    에서 공식 17 tags, 176,523 intervals, unknown 0을 고정했다.
    Catalog/schema/validation report SHA-256은 각각
    `1ba0c0198533abd51a3caa4b176f17aae17aa48d08899f6e7ced634a1eef0d60`,
    `aea001bca187af90f6aece0eebbfd8e2cd28304f054be11c7a170cac7efb6bb7`,
    `f81b31fe35ae7fc84d80ae1be2f965d35147066b985916f38f0d1e45638723cc`
    이다. Mapping/ontology validator/prioritizer/CLI/data card SHA-256은
    각각
    `291862fe406101909302284f7d6bd62418e23179c1425fb2233c7f38f5876f8e`,
    `7debfdb76e3e8c2f92b2c9b2aacb66b44a6dbd5f433d0276c78b1ae1b6550ff9`,
    `b05f8ce48a5c2c8d86d77f476340cda24612d69bc2945cd74d50ce2381eccadf`,
    `144f83db469af5781f22421bcea8959f319ae8a5f5ce56978ba293c7b45018b9`,
    `14d5e61bc671cc781fc711a2bfb589271ea9b87b3c8f5a8585840b23e19204b7`
    이다. Half-open interval overlap·동률·multi-tag·미매칭 mapping,
    gold label과 model feature의 강제 분리, top-1/top-3 우선순위와
    저신뢰·unsupported-claim abstention을 구현했고 validator는
    `valid`, 최종 자동 감사는 C/H/M 0이다. Aggregate source 접근은
    1회였고 row/raw/locator/env/identifier 접근은 모두 0이다. C01
    targeted 45개와 당시 전체 unit+API 534개가 통과했으며 warning
    7개는 기존 deprecation/attention-mask 경고다. 이후 병렬 작성 중인
    S201의 일시적 pin 불일치 8건은 C01 증거에 포함하지 않으며, S201
    동결 뒤 최신 저장소 전체 회귀를 다시 확인해야 한다. 현재
    `tag × target phone` joint aggregate, C03 human review, S107
    calibrated threshold가 없고 `0.75`는 provisional engineering
    기본값일 뿐 배포 근거가 아니다. 따라서 C01 연구 완료와 1점은
    보류하고 WP6 1/7, 전체 45/100을 유지한다.
52. 완료(S201 production static readiness만, 실제 내용 gate 0):
    S201 전용 strict config/contract, read-only fail-closed preflight,
    데이터·모델 카드와 논문 amendment template을 구현·합성 검증했다.
    Config/contract/CLI/unit/integration/data card/amendment/readiness
    SHA-256은 각각
    `0cb1cb24cd5b481032b8b3ba219df1343f1da21d9e13df1c10baca66122f1973`,
    `88f0cd148129fd880b8af4322b30d21715e9aea26eea4d48bb2f64f130ad7667`,
    `0f10c1d9104e80152baba94e1764ffcc1af2b2c2f0147d20af656586ef22d2b2`,
    `ea0a21a9aa59540dc2153d305b871aa5b1744659b190c6ccd3aa4367d24c5463`,
    `6416d490b99450f57e00847e29beca34c0a073436f27c841821340f5f23cfa8e`,
    `189e0eca2828bdd9fc53802e91f6dab0cd0df89d4e3f686b047c4cb5ac51b594`,
    `dd6a11c6749876d9bae2f92e5f19725602794ff90514f9eada6f72de3ec5ce26`,
    `07f4810e3e39b210e2245b456fc6995e68753656b1f526736b155f86916b0d3c`
    이다. 모델은 `openai/whisper-large-v3` exact revision
    `06f233fe06e710322aca913c1bc4249a0d71fce1`로 고정했고, offline
    local-only, 한국어 `ko`·`transcribe`, deterministic decoding만
    허용한다. WER/CER/content-mismatch 임계값과 결합 규칙은 모두
    `TBD_PREREGISTRATION`이며 production runner를 만들지 않았다.
    Config-only는 source 12건만 읽고 predecessor/data/model/download/
    GPU/metric/output 접근이 모두 0이었다. Full preflight는 S200 정적
    계약·deep 선행자 metadata 13건을 읽은 뒤 부재한 S108 final commit
    read attempt 1에서 fail-closed했고 threshold/external/data/model/
    download/GPU/metric/output은 모두 0이었다. Targeted 18개,
    S200→S201 회귀 36개, 전체 unit 548개가 통과했고 기존 warning
    7개만 발생했다. Ruff와 mypy 4파일, C/H/M 0 검증도 통과했다.
    YAML loader type annotation의 `type: ignore`를 교정한 뒤 unit test와
    readiness 문서 hash를 위 최종값으로 다시 고정하고 전체 unit 회귀를
    재검증했다. 실제 trainer·data·model·WER/CER metric·result·registry·
    commit은 모두 0이다. 따라서 S201 0/2, WP5 0/8, 전체 45/100을
    유지한다.
53. 완료(R00 실제 완료, 1점): 프로젝트에서 직접 작성한 한국어 고정
    문장 20개와 CC0-1.0 notice를 machine-readable sentence card로
    고정했다. Card/schema/CC0 notice/validator/unit test/validation
    report/data card SHA-256은 각각
    `08dc149ccf10006a587e6216dda0471c19b5c55eab6b320b4348587527d82c4d`,
    `1f8bcf0325414e1923cda0def9a973ac2f7be7bd85cca4ee869743ef862a7b92`,
    `ffd86dc89cf0957dccc61f6ecb7af1c7ebbbdbff2bd87c951e54c40afc1262c9`,
    `967517e74194936c91dad1eed4fcfb0c059b5480af32b8adfc9725fdb7d6e7d0`,
    `251b0d01c1a32773168586d0dacd823dbb9453845b98df4dd1b83fbdcc804f30`,
    `95b4fd861a3c21222ff13859cfbc9c196e3fd06493852bca823d8a63dc2816dd`,
    `d48e75ee83d61be9434d0f94c0f4fdda5401450e25a6dac422644c969e02138c`
    이다. 외부 원어민 문장 사용은 0건이고 noncommercial-only는
    `false`다. 초성 19개, 문장 안에서 관측된 한국어 모음 18개와
    보충 중성 ㅒ/ㅙ/ㅞ, 표면 종성 7개, 음운 규칙 8개, 운율 목표 11개를
    포함하며 난이도는 초급/중급/고급 7/7/6개다. 정규화 문장 20개가
    모두 고유하고 AI Hub reserved prompt hash 충돌, PII, 금지 내용은
    각각 0건이다. Validator는 `valid`,
    `r00_completion_claim_allowed=true`를 반환했고, R00-07과 R00-11의
    육안 자연성 교정 뒤 다시 검증했다. 독립 실행한 targeted 13개와
    관련 회귀, Ruff, mypy가 모두 통과했으며 최종 C/H/M은 0이다.
    접근은 public/aggregate evidence로만 제한했고 raw audio, row-level,
    locator, environment, model, human-rating, native plaintext 접근은
    모두 0이다. `r01_completion_claim_allowed`, TTS/audio 생성, 인간
    평가는 모두 `false`이므로 R01 이후는 계속 미완료다. 따라서 R00
    1점을 부여해
    WP7을 0→1/10, 전체 진행률을 45→46/100으로 갱신한다.
54. 완료(S202 production static readiness만, 실제 독립 auditor 0):
    S202 전용 strict config/contract, read-only fail-closed preflight,
    data-model card와 논문 amendment template을 구현·합성 검증했다.
    Config/contract/CLI/unit/integration/data-model card/amendment/readiness
    SHA-256은 각각
    `482505d52fc405ebf3b032c0d7eae4e7ae20e44af99340ca85a752f3424024f5`,
    `0ba78271b3d4d99ec3fff865e65bbe992428edfb587785021632677bc46fdaac`,
    `05cddea0070d146a43f830c3e0c767163bc913530bace8dbd61d53b37ec7c97d`,
    `8db7ee5448735ce2e8c0313b6dce86634406c59c63f6d78d0e5dc5334fc9a64e`,
    `0370d387170105a810bd0175e3d8f69afdbcdf09d4ee54509b1171510d3e36bf`,
    `cadafe868cda02955134a5d89f9d94aa4b6b881e812f0732a2e7d30cc2729c3d`,
    `62da51dbe9289acf349f177f69c4585ef08c48d2b363055c36874bd235935417`,
    `63adeb528cd10df9c5ba84e988a8cdd6dfa6009b3fc8079a1eba113d4ec7b8de`
    이다. S108→S200→S201의 completed gate 순서를 강제하고
    S1/oracle/silver/Whisper score leakage를 금지한다. Model/revision/
    label/rater/adjudication/loss/metric/CI/gate는 모두
    `TBD_PREREGISTRATION`이며 production trainer를 만들지 않았다.
    Config-only는 source 15건만 읽고 다른 runtime 접근과 output이 모두
    0이었다. Full preflight는 부재한 S108에서 stage
    `predecessor_s108`로 fail-closed했다. Predecessor read
    attempt/success는 1/0, read count는 13이었고 S108/S200/S201
    validation, preregistration/external/data/model/holdout/GPU/metric/
    prediction/checkpoint/result/output은 모두 0이었다. S202 targeted
    18개, S200→S202 관련 54개, 전체 unit 577개가 통과했고 기존 warning
    7개만 발생했다. Ruff/mypy 4파일과 독립 C/H/M 0 검증도 통과했다.
    실제 trainer·data·model·metric·result·registry·commit은 모두
    0이므로 S202 0/3, WP5 0/8을 유지하며, R00 완료를 반영한 전체
    진행률도 46/100으로 유지한다.
55. 완료(S203 production static readiness만, 실제 전문가 holdout 감사
    0): S203 전용 strict config/contract, read-only fail-closed preflight,
    평가 카드와 논문 amendment template을 구현·합성 검증했다.
    Config/contract/CLI/unit/integration/evaluation card/amendment/readiness
    SHA-256은 각각
    `67bfd4e87ae59ba581c819fae17750520723006da991d5c1090f4490f7d5a4c7`,
    `35ee786e59b247c28eb2cd06e5e58c11096cb4ad7944b74c0ab6950b5d2f578b`,
    `afa42ee94aceabbb2cfaa6e589c290909c2636fb652ae80a08dcb9b39a3e0756`,
    `a34dfc8c0814895a3415d4af0449e94883eefc55b6f0b66f7ab97a33578c31b1`,
    `f4581e6cb5370bd92540a7e106fc0422b418bb98d40a49ba97df1a00d90d3478`,
    `a9fb81d3d4d5e4b9e6eab5c80e86b056a494e3ef6d52ad61af7aa3e4f59b2a8f`,
    `e1fec889cbf9456194adec25a9ddd267a6ea67b53bee92999d368fdff216197a`,
    `3374e79af0e15034513357feab214687ccb41d7d981db3a6ee2efaad8e5aa2ee`
    다. S108→S200→S201→S202 순서와 각 public/static·deep validation을
    강제하고, D07·S1 locked-test·S2 개발 split과 S203 holdout의
    item/speaker/prompt/rater overlap 0을 요구한다. 문서 근거가 있는
    gate는 심각 S1 오판 recall `>=0.80`, false-high-score 상대 감소
    `>=0.30`, 정상 referral `<=0.15`, 불일치 전문가 오류 적중률
    `>=0.60`으로 고정했다. Metamorphic `>=0.95`는 별도 non-holdout
    gate이며 S203 primary metric으로 복제하지 않았다. 네 metric의
    정의·severity·CI·cluster·slice·최소 support·multiplicity·rater·
    adjudication·one-shot access family·join statistic은 모두
    `TBD_PREREGISTRATION`이고, 하나라도 남으면 holdout attestation과
    data path를 열지 않는다. Config-only는 source 25건만 읽고 다른
    runtime 접근과 output이 모두 0이었다. Full preflight는 S202 static
    C1을 확인한 뒤 부재한 S108에서 `predecessor_s108`로 fail-closed했다.
    S202 public source 15건, public validation 1회, predecessor metadata
    validation attempt/read 1/13건, S108 commit read attempt/success 1/0이었고
    S108/S200/S201/S202 completion·deep validation, preregistration·
    attestation·protected
    data·model·prediction·holdout·locked-test·GPU·metric·result·output은
    모두 0이었다. S203 targeted 25개, S200→S203 관련 회귀 79개,
    Ruff/format/mypy와 독립 C/H/M 0 검증이 통과했다. Production
    evaluator, 실제 expert holdout, S1/S2 prediction join, metric·CI·
    gate 판정, generation·registry·commit은 모두 없으므로 S203 0/2,
    WP5 0/8을 유지하며 전체 진행률도 46/100으로 유지한다.
56. 완료(R01 production static readiness만, 실제 후보 생성 0):
    R00 정본 sentence package의 public·aggregate deep validation을 선행하고
    human consent/license/recording attestation, CosyVoice code/model/
    weight-license attestation, offline runtime lock·local bundle·exact GPU
    pin, D1 candidate schema와 R02 전 publication 금지를 순서대로 강제하는
    strict config/schema/template/contract/read-only CLI와 fault test를
    구현했다. Config/schema/human attestation template/CosyVoice
    attestation template/contract/CLI/unit/integration/data-model-license
    card/readiness SHA-256은 각각
    `ae6034ba3053fdf196fcccecc394adf5a517275253ac3abdafd72c953ede4f64`,
    `7ffe1b152276b12621575680d022d9b87cd9c19e7fe36374a3186cf7449e1470`,
    `aeeebf426b86427f3f2a1f4fd1adc1f4382aa3ace6d88474feed3d8081ecd096`,
    `2ce20ac3fd17714ff487c47f45b227b490e75fa8bebe6e7596bb4ce8328abc2e`,
    `e70bd5ed2fcf678537ef8ec41ceb2b76bfba3037cd40a2fc4b617dee31619a95`,
    `e0daf9c1b83db51a0501f5e3e7663ba8890a58a904dc5178a41e1f4b98d9bac5`,
    `d589c83b71939a2ec9fde9d5228d4e6cb0b4de11337b2e9bf39de9b69d6cf8e3`,
    `69de05e6be504c15903abbc177e43aa05121c1f5473a3f1ecd8e2a23a5741e07`,
    `f2d25a6dafe24416e8b745ba42af76ff6a3801363e7fd69536a51d55dfe36d40`,
    `c15784dc676b8256261fb98da0cd543b7dc8f846a4c6abe23d4b20f62e10d465`
    이다. Config-only는 source 13건만 읽고 다른 runtime 접근과 output은
    모두 0이었다. Full preflight는 R00 validator/deep validation 1/1,
    public·aggregate 접근 33건, raw audio/row 접근 0을 확인한 다음
    `stage=human_attestation`,
    `error=human attestation is TBD_EXTERNAL_ATTESTATION`으로 의도대로
    종료했다. Human attestation path attempt/read 0, CosyVoice
    attestation 0, data/audio/model/env/GPU/candidate/metric/R02/publication/
    output 0이었다. CosyVoice repository/model/license ledger는 관측
    사실로만 기록했으며 weight license·authorization·environment·local
    bundle·GPU는 `TBD`이고 download·GRPO를 실행하지 않았다. 정책은 고정
    문장 20개, 문장당 8–16개(계획 12), human=TTS, source당 4–8개,
    source별 화자/voice 최소 2개, speed/pitch 각 3 bucket, UUIDv4 opaque
    ID, D1, R02-before-publication을 강제한다. Targeted 24개와 R00+R01
    37개, Ruff/format/mypy 4개 및 독립 C/H/M 0 검증이 통과했다. 실제
    human/TTS 후보·audio·model·GPU·manifest·metric·completion artifact는
    모두 0이므로 R01 0/2, WP7 1/10과 전체 46/100을 유지한다.
    이후 R00 paper integration으로 canonical manuscript SHA가
    `fca961873ac0fccea878ebd9ef4781d908bdc5f80b672adde15ac6fa1c5df8a5`
    로 바뀌어 R01 config의 해당 source pin만 provenance refresh했다.
    R01 targeted 24/24, config-only valid(source 13/runtime 0), full의
    R00 deep 뒤 human TBD fail-closed와 output 0을 다시 확인했으며 후보
    정책·실제 실행 상태·배점은 변하지 않았다.
57. 완료(R02 production static readiness만, 실제 hard gate 0):
    ASR 내용 일치, acoustic artifact, pause/rhythm, target-phone coverage의
    네 gate를 R00→completed R01→사람 승인 preregistration→ASR/artifact
    attestation 뒤에만 실행할 수 있는 R02 전용 strict package를 구현했다.
    Config/report schema/preregistration template/ASR template/artifact
    template/contract/CLI/unit/integration/data-model-license card/readiness
    SHA-256은 각각
    `d3e73343e6123bfbf08301868fe9ed945cb591c88dde9dbc01bf8ffe444cf6b7`,
    `60525a3b685574a3dce193a7423d77a811050c9c38d36b2c3ba947b71f06a92b`,
    `07b53aa937d0490116c4b8aeedc5d6aab1754479990404ccb318dd2dd98528c0`,
    `f1c0ea10e08baf425b36d1fd3ad936b93557af5224f043df374d19648a2df53f`,
    `940f36e3c1c0f82c1eaa408256bf9461961f7bb354c8f81e833dce8a7a2191cc`,
    `a50e6287ffdb56c9ec2f22859a9be800cc3fb787b7c3390f4330052412eaf5bf`,
    `c40ab4b0f829d0dae1d93fa5bf55e12e7e61933fffae8aa31c7aa904ee8e3be5`,
    `22c83a3feadb0f576a97408db292abac7268f41060d444ff810e6b262ff981d8`,
    `4caa1f7f75d6e276b8ee9774c1b029e1641f9b02b4e4261cc4e769143f9bb5a1`,
    `7cb9692472c32e89029a455d28f3ed65f2854860264932a06198eec7f203a61f`,
    `e3c26f2b737cf73d5ad609008bdf3b93d31c8240978f0eea5d73ecef58cd4c4c`
    다. 계획이 family/역할만 지지하는 Whisper-large-v3, UTMOSv2,
    duration/pause/nPVI와 R00 target-rule coverage는 보존하되 exact
    metric·threshold·model/checkpoint·normalization·segmentation·support·
    license를 `TBD_PREREGISTRATION`/`TBD_ATTESTATION`으로 fail-closed했다.
    Human/TTS는 동일 source-blind gate를 사용하고 source label은 sealed
    D1 sidecar로 분리한다. 탈락 candidate는 삭제하지 않고 D1 quarantine에
    보존하며 R03는 exact eligible partition만 읽는다. S1/S2 prediction·
    checkpoint·split, learner gold와 locked test는 전부 금지했다. Future
    deep validator는 R01 commit/registry/manifest/human/CosyVoice
    attestation과 R02 blind manifest/lineage/report/eligible/quarantine/
    registry/commit을 hash·내용 양쪽에서 재검증한다. Config-only는 C1
    source 16개 외 runtime 접근과 output 0이었다. Full은 R00 validator/
    deep 1/1, public·aggregate read 33, target-rule audit 20, raw audio/row
    0을 확인한 뒤 `R01 completion commit is TBD_R01_COMPLETION`에서 path
    attempt 0으로 종료했다. R01 evidence, preregistration/attestation,
    D1 audio, model/download/GPU/metric/S1/S2/gold/locked/source label,
    output/publication은 모두 0이다. R02 targeted 22개, R00→R02 관련
    회귀 59개, Ruff/format/mypy와 C/H/M 0이 통과했다. 실제 report·
    eligible·quarantine·registry·commit은 없으므로 R02 0/2, WP7 1/10,
    전체 46/100을 유지한다.
58. 완료(R00 paper integration과 downstream provenance pin refresh,
    점수 불변): R00 완료 사실과 “R01 이후 미실시” 경계를 canonical
    manuscript/evidence-map/claim-gap/methods-gap에 반영했다. Manuscript,
    evidence-map, validation report, validation doc, claim-gap, methods-gap
    SHA-256은 각각
    `fca961873ac0fccea878ebd9ef4781d908bdc5f80b672adde15ac6fa1c5df8a5`,
    `b0f11241eb96e40e9ee2c9a2e7a3f568bdb33bd846958dd1071dd23052c2f8ea`,
    `7d20db4924ab4d22190c57b2c0065495f2d8e8a9ffdc9f26f5ec3cfa8f9b513f`,
    `240a7b7104a70a3f443834758a22c6125625dd60c181cddd275632bd26c7074c`,
    `7b41a85108b2e93ef385f57ba9e6a3ca7d5f7735c818b946044de52b71946fc4`,
    `321ee585a4aeaecbb4b11b8d5183e1c1fa274420e2146475dc8d8f4299020070`
    이다. Canonical validator는 artifact 34/34, claim 475/475, PENDING
    18과 locked guard를 통과했고 P04 충족률은 33/40=82.5%다. 이
    manuscript pin 변경을 R01과 S200→S203 config/readiness provenance
    chain에 재고정했다. S200 config/readiness는
    `0164918650dda466a67a2382ebb4118a28e03f38cad3c7cbf22116ebc815355b`/
    `b551675916d9c2dbe3daedd49f70579764db267b45196d6ca4a2dc52a435b14f`,
    S201은
    `d166cd8ad60730a1a15d94e683023cf3c66ef701ff3cd823eedf72ddd32029b7`/
    `a966e9853d4eabc483a8b01671850b83caa4cae008c862def52aa1d97bcf7b33`,
    S202는
    `9a608103a333e800ae9096179dad26c72519610da52cdc2f704d0bf48e8b2662`/
    `ee7c739f6d1c3e760a17b95d58e78c1509b651678af685373dcf17d91d254943`,
    S203 config/readiness/unit/integration은
    `610e614264dcab3e8a2fcb677e2de85b2661fdb1791d94b6652dcf1628aa73d0`,
    `308d9e11be9cadceaace2b35541609c7d590d00c84b247e69adff88d16b271f5`,
    `c64e57618d30354592071bf382cf2ddf84883d0ea7e6bb20d477db1da337c22b`,
    `c803dc67fb8c1d48421151c236b0772bf4f9b744d70d057e45578c0175f2794a`
    다. S203의 mutable tracker source pin은 자기참조 순환 drift를 막기
    위해 제거해 source 24개가 됐다. S200–S203 관련 79개,
    Ruff/format/mypy, 각 config-only와 S108-missing full fail-closed/output
    0을 재확인했다. 이는 provenance-only refresh이며 실제 S2/TTS/RCT
    결과나 새 배점이 아니므로 전체 46/100을 유지한다.
59. 완료(R03 production static readiness만, 실제 Pareto/diversity 선택
    0): R02 exact eligible partition만 대상으로 R00 20문장마다 3–5개
    reference를 선택하는 R03 전용 strict config/bank schema/preregistration
    template/contract/read-only CLI와 bounded fault test를 구현했다.
    Config/schema/template/contract/CLI/unit/integration/data-model card/
    readiness SHA-256은 각각
    `6e24735fb93fe6e4bd71f08c61861d05bef003b9cebc6ed5210c3c8dc4ef2fce`,
    `2e6c4728943fbfb5519f7e961fca68da816b82e44fc65f003938644ade68ca83`,
    `8854d0f9b6654b174ea69e484cb806c0e2ff9c787e3134d2a9c404974a5b6de8`,
    `626e5641e86c19fdac2582d3d1f62e006a2f12ba448dad5127936c241fd00bb8`,
    `36c21580b433d8bea2669eb504ef86cdc36c0d45889d287739edb05d18fe2517`,
    `299cbecd56f67d4feb5577ace37961cad1078a65477d987a252597db0ee1c374`,
    `d431add1a94a139183beb6e765851158cf737d74b2abdaccdf969980163d1487`,
    `93edf4a288998d2c2956aad9f9b1bf55943c50ff474189c3c137d4052689f20d`,
    `c014aece9038a3b1999e93906c50399c774d2822b73ba1956237cb881e758f88`
    이다. Paper/plan이 지지하는 naturalness·prosody fit·pedagogical
    clarity·diversity objective 이름, Pareto, 문장당 3–5개와
    speaker/speed/pitch 보존만 고정하고, exact objective measurement,
    normalization, dominance/tolerance, tie·frontier shortfall/overflow,
    diversity distance/weights/minimum separation/cluster와 seed/order/
    count/tie-break/replay는 모두 `TBD_PREREGISTRATION`으로 fail-closed했다.
    Selector는 source kind·attestation과 R04/MOS/CMOS/learner outcome에
    blind하며 source는 immutable selection 뒤 lineage join으로만
    확인한다. Future D1 bank는 문장별 human+CosyVoice 수 차이 1 이하,
    distinct speaker/speed/pitch 최소 2/2/2, R01 audio/source와 R02
    eligible-row exact hash를 강제하고 quarantine을 거부한다. A1은
    row/ID/speaker/audio hash를 거부하며 R04 전 publication은 false다.
    Config-only는 source 17개 외 runtime 접근과 output 0이었다. Full은
    R00 validator/deep 1/1, public·aggregate evidence 10, raw audio/row
    0을 확인한 뒤 `R01 completion commit is TBD_R01_COMPLETION`에서
    path resolution/read attempt 0으로 종료했다. R02 completion,
    R03 preregistration, eligible/quarantine selection input, audio/model/
    GPU/objective/Pareto/diversity/selection/R04/output/publication은 모두
    0이다. R03 targeted 29개, R00→R03 관련 회귀 88개,
    Ruff/format/mypy와 C/H/M 0이 통과했다. 실제 selected candidate,
    bank/trace/A1 aggregate/registry/commit은 없으므로 R03 0점,
    WP7 1/10과 전체 46/100을 유지한다.
60. 완료(R04 production static readiness만, 실제 blind 평가 0):
    Human reference와 R02 hard-gated TTS를 source-blind하게 비교하는
    R04 config, 인간 승인·preregistration·pilot nuisance·SAP·listener
    attestation template, randomization/rating schema, strict read-only
    contract/CLI, data-model-privacy-statistics card와 readiness를 구현했다.
    Config/contract/CLI/randomization schema/rating schema/card/readiness
    SHA-256은 각각
    `6d78c8ea28b81a29e20389468f4da07b8e77c0a73f71c1ba1c9437da92722881`,
    `8bcb3d405a72e0f69c37b21e28f6e2634754be15fb527f5e9046720db8f49b7c`,
    `6dd7c7b813889e5cf007825dac0afc69a2a49d19d4f1b55c8f12d2e90647a36e`,
    `1d76bfac83317eea307740f0d137f1e6fd1b926cb2e3d40187b30402caef1a32`,
    `510f0063048a5cbb0da6312d06949c4c059f0c8d39415a5a7063213c61d3b09f`,
    `e7e5be7bab7a4de18ccddcbe6c6af388b5356459670c396d169c0f4d0f1c1f03`,
    `d4a93610e0f10193d29bd1aa07f942bc041102f7bb9f4b490781e526303a1ddc`
    다. MOS 1–5 네 척도와 CMOS -3–3, listener·sentence random effect,
    bootstrap secondary, power 0.80와 양측 alpha 0.05, raw D1/A1
    aggregate 분리를 고정했다. Exact N·estimand/link/CI/multiplicity/
    superiority margin 등 근거 없는 선택은 `TBD_PREREGISTRATION`으로
    남겼다. Config-only는 source 25건 외 runtime/D1/output 0이었고,
    full은 R00 deep·public/aggregate 10건 뒤 R01 completion TBD에서
    R01 path/read 0으로 fail-closed했다. 실제 listener/audio/model/GPU/
    rating/MOS/CMOS/statistics/result/registry/commit/output은 0이다.
    R00–R04 회귀 107개, Ruff/format/mypy, 독립 결합 targeted 57개가
    통과했다. 따라서 R04 0/3, WP7 1/10과 전체 46/100을 유지한다.
61. 완료(R05 production static readiness만, 실제 gate·GRPO 0):
    S1 phone F1 0.80, S1 utterance ICC 0.70, S2 severe-error recall 0.80,
    R04 blind superiority, actual pilot learning-effect signal의 고정
    다섯 gate와 model/license/reward/safety assurance를 강제하는 R05
    config/decision schema/preregistration·authorization template/strict
    contract/CLI/card/readiness를 구현했다. 해당 SHA-256은 각각
    `e47ff97009b1286cb9c318f875f5e72aac31b38a84ed4b40d6b9682f8a59b06f`,
    `22174e8c8e844fdb79b969e7e6800de741ce71e2f3349e3b4771cdf2bfad8c47`,
    `83b343a59050093c9698d278f52d2ece4c1a4dabc694410b7d3fbcf233faac4b`,
    `2480c83359549298d9a1b4a17ffa3f5f6eb755e3bf55364c8bb58a38b50e8645`,
    `3041ea274404fec56a46f36fd5c79a16b64a76294e26df2e2fa40132bfdf47be`,
    `917b72f6429a80ced5db1f080b0cebb5d7ab33e6e6fc75318bf4be76b37e807d`,
    `b8095707280864440be68601302e2b8b995282f1bf66931fd91828111ff9171c`,
    `4ea70a32598f4b18e49e5dd6dd83b329a2078fb8293816fbd70f7e39b5473109`
    이다. Exact artifact pointer·estimand·CI·blind margin·learning-effect
    rule과 model/license/reward 승인 항목은 독립 사전등록·사람 승인
    전까지 TBD로 잠갔다. Config-only는 source 14건 외 runtime 0,
    full은 S108 completion TBD에서 predecessor path/read 0으로
    fail-closed했고 모든 protected/audio/rating/participant/model/network/
    GPU/reward/metric/effect/decision/output은 0이었다. Targeted 38개,
    관련 회귀 198개, Ruff/format/mypy와 독립 결합 targeted 57개가
    통과했다. R05는 NO-GO 판정도 아닌 증거 대기 BLOCKED이며 실제
    decision report·GRPO가 없으므로 R05 0/1, WP7 1/10과 전체 46/100을
    유지한다.
62. 완료(E01 production static readiness만, 실제 metamorphic 실행 0):
    S108 final research candidate를 대상으로 silence/gain/duration/F0/phone
    perturbation을 평가하는 config, strict input/report schema,
    preregistration template, read-only contract/CLI, data-model-safety
    card와 readiness를 구현했다. Config/input schema/report schema/
    template/contract/CLI/unit/integration/card/readiness SHA-256은 각각
    `d9971f691e6cedca9f1b5b7a37c38d4198055a271e7eab62c6f7617fe68526dd`,
    `4b3459751aacb225a2637f80a90562809c8de840ab4e520dc07180b5c070d73d`,
    `7e7e4e7be86f06b800de108c0744156b5b7db469efb99b19430ef4b4b77ee577`,
    `1e364ac47061e27d7bd82e76df9302e26ff85ca15258f2d017d492cef28aa2f7`,
    `0b4e6decf6b6da63baa6939a44ae2b39e0d7a18d8d6c79c7e037ef7ac9236e43`,
    `591153fe7812ca4e32a027e2da277483dcc4938b21cb77ed2a1fa9f22a437863`,
    `4436e06327e49c3d5e66062126b976383fe74b7602e072eeb1e40ca503dd58ec`,
    `8f919c728770336ac74b5c5a11890ee012b6e04f0a563d0a541d093d49dd6ed4`,
    `bef7b09317ceb82e9eb5c0de0c20990cb111f9aa5bcede2c822a3fd267cb0eae`,
    `9f3d413ed78ce6af6ea4a10795a5a6a4cef2d57ddfb1fa78f6f54280209dfaee`
    다. Canonical 문서가 지지하는 전체 expectation pass rate
    `>=0.95`만 고정했고 각 family의 magnitude·expected relation·case
    count·aggregation·CI는 `TBD_PREREGISTRATION`으로 잠갔다.
    Config-only는 source 14건만 읽고 runtime/data/model/network/GPU/
    locked/output 0, full은 S108 completion TBD에서 predecessor path/read
    0으로 fail-closed했다. Targeted 31개, S108/S200–S203/E01 회귀
    139개, Ruff/format/mypy와 root 독립 targeted 31개가 통과했다.
    실제 perturbation/audio/model inference/metric/decision/report/commit은
    모두 0이므로 E01 0/2, WP8 0/5와 전체 46/100을 유지한다.
63. 완료(C02 production static readiness만, 실제 coaching 0):
    C01 ontology와 향후 S107 calibrated threshold를 결합해 catalog
    문구만 deterministic top-1/top-3으로 선택하는 config, strict
    diagnostic input/coaching output schema, preregistration template,
    read-only contract/CLI, data-model-safety card와 readiness를 구현했다.
    Config/input schema/output schema/template/contract/CLI/unit/integration/
    card/readiness SHA-256은 각각
    `6b472366202f9ce510082cdea8f216594137d98023da4d913f9e338bada5ee28`,
    `cd2588ffac9ff10870977e125969c7709ecde25c050c4845ab0c9b9f1f1d7742`,
    `935db571a2371e96b082a59979af7b24a508318f5385f62032d4ce735ed6c643`,
    `6d34fd0feb2af5ab0a1b0d08560883be39ae4d55fca89f4f9f42484eeb05040b`,
    `59941428030e653b013459b003ce99d1169cd940ee9a06fb320634d75dd11667`,
    `4dbbd04b408922808f622011dffba06297540c9a917589c4e21e73333a1f292f`,
    `b59708c6c5f49e7a75ccc93fc5c64a145740f14b97d98fac401934e10299ae26`,
    `27eae98cac4a146ef701de259a03c380ef88794818ec4babc1c48c78a04ca4d0`,
    `88749a14a61da78b612f8c22edcb13278f4906081971bcda2d55dd119e72b7b1`,
    `327c7403a13c247d75fd0c5a685fd36fa72f89281911fdd2e8117824c9bbd1b8`
    이다. Base-10 Decimal 12자리 round-half-even priority, deterministic
    tie-break, blocked top-k no-backfill, threshold equality, low-confidence
    `rerecord`, unsupported/unknown `expert_review`, phone-localized evidence
    receipt, C01 advice byte-exact copy와 free-generation LLM 금지를
    고정했다. Exact threshold·confidence aggregation·evidence support
    matrix/minimum count는 `TBD_PREREGISTRATION`으로 남겼다. Config-only는
    source 19건 외 runtime/model/GPU/network/LLM/output 0, full은 C01
    deep·aggregate read 1/1과 protected read 0 뒤 S107 completion TBD에서
    S107 path/read/module 0으로 fail-closed했다. Targeted 21개, C01+C02
    33개, 관련 회귀 140개, Ruff/format/mypy와 root 독립 targeted 21개가
    통과했다. 실제 diagnostic/coaching/effect/result/commit은 모두
    0이므로 C02 0/3, WP6 1/7과 전체 46/100을 유지한다.
64. 완료(D08 production static readiness만, 실제 외부 수집 0):
    R00 20문장을 동의된 성인 원어민 다화자 reference로 녹음하기 위한
    config, strict R1 metadata/D1 QC/A1 completion schema, H00·개별 동의·
    녹음권리·QC preregistration template, read-only contract/CLI,
    data-model-license-privacy card와 readiness를 구현했다. Config,
    metadata/QC/completion schema, H00/consent/rights/QC template,
    contract/CLI/unit/integration/card/readiness SHA-256은 각각
    `4f05a4310c7780630a986411fa2c12b95f9d12fb1a95f456d4b7207c5826239e`,
    `6ae7bf0c6d30cf85e9f02fad37586d0dcb06baac7789e62f6310c4e20732364e`,
    `4064c2260d3df57b9f41977bcbe303f393ea4d7c582c295b31e39a80df646aa6`,
    `1ae8e5d37e0ce25b7d5831e321c44d7bff8d9805c7c24a3f2367b73dad6ac59c`,
    `88321e78e556d53949f92cfdc8dadda6195fe8807ae235fe507544d4199693b1`,
    `ae8bee17dd8e94db3efd86b5dbe277c77521ab3f89730a370dea15a4df05c193`,
    `89d02baf5b9d5ad8ec415575aefeddbcdb89d096bc950026a00fdbdad637e7ed`,
    `e1d7b3a6b81614852f76c809299eae490c75d8e0ac2855f90b569da146537472`,
    `1082147df808503dcf92be8466984171f9b2c5364afc0fcc78dd88cae7700549`,
    `f4a711e79f71fffab8b499151055423f6328e055b65f2ccaace97787350bbbf8`,
    `02a9f98e248d8b2e7c1825939f2413dacefafaac97d2f93ac8a3bc9bfe5a558d`,
    `888ba0e0fa64022bc0c081606635020e95a14803bf0b499e713fe5e580bd5313`,
    `cd7d3d452480af17180aa6fab1f2c27112f87a5d40343a4db1ace0943c32d2c1`,
    `4c5a1b99efd92311c1165da76feb96de953fdfd01b13bc35190c2a9e7c076279`
    이다. 자연속도 2 take+명료 교육속도 1 take, 48kHz/24-bit mono PCM,
    동일 equipment/microphone/room, 15–20cm, clipping ratio <0.001,
    decoder error 0, ASR triage-only와 2명 pool 중 최소 1명 human content
    확인을 고정했다. 문장당 4명은 planning target으로만 두고 exact
    speaker floor·DC/noise/reverb/interruption/content threshold와
    adjudication은 `TBD_PREREGISTRATION`으로 잠갔다. Config-only는
    source 19건 외 runtime/external/output 0, full은 R00 validator/deep
    1/1·public/aggregate 33·raw/row 0 뒤 H00 TBD에서 H00 path/read 0으로
    fail-closed했다. Targeted 17개, 관련 회귀 162개, Ruff/format/mypy와
    root 독립 targeted 17개가 통과했다. 실제 모집·동의·녹음/audio/mic/
    QC/result/commit은 모두 0이므로 D07–D09 묶음 0/2, WP1 10/12와 전체
    46/100을 유지한다.
65. 완료(H00 production static readiness만, 실제 공식 판단 0):
    C0/C3와 C3/C5 학습자 연구의 기관/IRB 또는 동등 공식 판단을 검증하는
    config, public approval와 restricted official-document attestation
    schema/template, strict read-only contract/CLI, ethics/privacy card와
    readiness를 구현했다. Config, approval schema/template, attestation
    schema/template, contract/CLI/unit/integration/card/readiness SHA-256은
    각각
    `7128d30304bd3d1c8784ad79e14485a9bbca52fed9d90c878370d549e45f9698`,
    `39c0aaf985dcf013cdae2d8e0276b8b838da3bcb11c945e4b88abf83d6bc8a20`,
    `0f4754ee631e3c75c66ad942fc74b93092b87b8bf49ab02f94b2393bf6324b3d`,
    `d157f4c68efa208116155392694d8319101a5fe8e374477c73e2a6ffb48619bc`,
    `e6214303e9d049c841571fe6ba61040bd3a0109d14c476f34b0f6838ef6df0a5`,
    `1ef2c62b8c654bb409a2717061292548dd48d79fbae2e88b1b6ab93c224985cd`,
    `4f14313b8c2b202d6f99ea0c1ffb9f5824c3a958c43ba3dfb096111c942db0c1`,
    `35294a16fdd150c8fce14a4a10445c135f7ccec3410d36256f6c93f305d75b9c`,
    `5238317ee03a9f203c9f2eefa011c1cde684de4234b34f174a0f1c599f43cb43`,
    `4b5c5ed6beca0d703e1bd746ef9a4e317a5fd84ab1c682c428bf30b8e96c30ee`,
    `52261068d25b34d92608ea25e3fee7ae725cfe749edf6c4373ebee6aa405e020`
    이다. Protocol/version/hash·C0/C3/C5 manual, participant 1:1 병렬
    배정·L1/baseline 층화·concealment, 녹음/개인정보 목적, consent/
    withdrawal/retention/access/deletion/backup, compensation/minors,
    AI 오피드백 위험·사람 escalation, 가명키 분리, external/TTS transfer,
    incident/pause/stop/resume, amendment/expiry/current status를 강제한다.
    기관 양식·판단 범주/번호·protocol·보존기간·보상·미성년자·transfer
    범위는 근거가 없어 `TBD_EXTERNAL`이다. Config-only는 source 12건
    외 runtime 0, full은 `TBD_H00_APPROVAL`에서 approval/attestation
    path/read 0으로 fail-closed했다. Targeted 35개, 관련 회귀 109개,
    Ruff/format/mypy와 root 독립 targeted 35개가 통과했다. 실제 browser/
    submission/기관 연락/공식 판단/참여자/동의/녹음/개인정보/model/GPU/
    TTS/intervention/result/commit은 모두 0이므로 H00 0/2, WP9 0/8과
    전체 46/100을 유지한다.
66. 완료(C03 production static readiness만, 실제 전문가 감사 0):
    S107 actual calibration과 C02 actual D1 coaching 뒤 200건을 blind
    전문가가 정확도·안전성 감사하도록 하는 config, strict item/rating/
    report schema, institutional/expert approval·reviewer attestation·
    frozen preregistration template, read-only contract/CLI, data-model-
    privacy-safety-statistics card와 readiness를 구현했다. Config, 세
    schema, 세 template, contract/CLI/unit/integration/card/readiness
    SHA-256은 각각
    `8d5857b486fa3b64936dbfc5dd53c3514e82b9c992c45a826e14ffe24a523d00`,
    `52be547d3db0fe8226c138322e6a0fd1d014b8344dec15dae349db3032471e97`,
    `be1bda4b88c1fe1ef5b399267e69aa71c5c084e1a48a85cca7bf2ad102bd3ae9`,
    `5c3102aa2fdf9c2a58b100f928a16971dbb22c5a28fabed22b92a4370bb9b1dd`,
    `242befd186382078b558af6fa03b8a5f92e0a4caaa070d0d404e26fab5afd58c`,
    `ba34657c4a115099207c242b484e38620b7d921961127d7f552dfd2c24cdf724`,
    `2b5a149c37633560e5d0920fef917b91bf8c88ba7ed0c675c43955504e10a47e`,
    `641107be437439eb17e8ad32cfc531b085ea6a6f6ee436c6c2a9423b4d9a531c`,
    `6dec5c22a0d18150de8f1d48ce4896ed1bc0580f14d6356cb1d919e1ee1b0b27`,
    `d9ba5817f8d3835822e9938b4e866e5b449c7a85ceb346ccb799b82b081d2993`,
    `cba27993946c4f244cb430a548aae5157738a61add7ef7154a210b4fd1827b8f`,
    `fd22d34e1abfbdac4e5fbf2ca45ef97ee615bac3336aa4ac85acb5fa348a0d0c`,
    `72140f75f7997cd0a7bf7f7cc618e5f803d5f8b50a67ebb8dd32a09cab918af2`
    다. Primary 200건을 L1/error type/confidence로 층화하고 reviewer에게
    audio/target sentence/top-1 advice만 보이며 model score/source를
    blind한다. 위치/유형 정확도 `>=0.90`, unsupported `<0.02`를
    고정하고 risk/discrimination/identity inference/medical judgment
    한 건이면 pass 금지·수정·재감사를 강제한다. Exact allocation,
    reviewer N/자격, ratings/adjudication/CI/multiplicity/support와
    abstention/referral exact gate는 TBD다. Config-only는 source 21건
    외 runtime 0, full은 S107 TBD에서 S107 path/read/module 0과 C02/
    approval/reviewer/prereg/audio/rating/model/GPU/network/metric/output
    모두 0으로 fail-closed했다. Targeted 15개, C01–C03 47개, 전체
    repository 1,047개, Ruff/format/mypy와 root 독립 targeted 15개가
    통과했다. 기존 warning은 FastAPI 1개와 torch mask 6개뿐이었다.
    실제 expert/rating/audio/metric/result/commit은 모두 0이므로 C03
    0/2, WP6 1/7과 전체 46/100을 유지한다.
67. 완료(E02 production static readiness만, 실제 API guard 실행 0):
    S107/C02 actual 결과의 confidence boundary 밖에서 자동 교정 advice,
    reference와 corrective action을 모두 차단하는 config, strict input/
    API response/C02 bundle schema, preregistration template, read-only
    contract/CLI, data-model-API-safety card와 readiness를 구현했다.
    Config, 세 schema, template, contract/CLI/unit/integration/card/readiness
    SHA-256은 각각
    `bd87c5aed432a6e2dccbabc6c7e5f558d1f3e200233e19a8776be3a5f48d7c04`,
    `2382601910d088ed3fb0b8d839bc38c6f992cf60fc592a8043ea586e76807e73`,
    `e15d55e2f2db1efdd82ed98436272df124ea6631d9f7eb0e28b1f832d4270cd8`,
    `611d6c4fc1cc4e1f1fd15a9bcc185deba781c145b23e2ed7432affbe5ae496ca`,
    `8b1e0490e3fbad3efe2f3ac8624187e32a386d35428bcd21a2c82f832bf0d4f9`,
    `6c1ce292df05f2cf119bcf5f5cae4ac24b65323568e243775e57e8acdf3f7764`,
    `bb7e0e434a0b18047f55151c537bb85d663308bfd3e3f5d2ff926f5b58d69c1a`,
    `9433f6904b4b4c434d84e893d766cb00546529e85a48eb4cbdf03eef917b56c9`,
    `cf50094bb80363b6cdd6010a3fd1279c9a82a943be776d80ff8c24aced3b0459`,
    `1e3516cd9b38493f96f0de6c1e4d6ecedd2641b7a597af6d9c2941e27e48c9d8`,
    `3e9445b2b3fcea978e0073e6aaa2ea4426d73e8ab2e4c6833d493265f6c8e058`
    이다. OOD·low-confidence·unsupported·abstain-all이면 advice/reference/
    action을 모두 차단하고 `rerecord` 또는 `expert_review`만 허용한다.
    S107 numeric threshold 복제 금지, actual S107/C02 deep binding, C02
    no-backfill·free-generation 금지, C02 YAML handoff와 static snapshot
    TOCTOU를 강제한다. API response에는 model score·PII·raw path·free
    text·교정 payload를 금지한다. OOD 정의·threshold/rule·혼합 사유
    정책은 `TBD_PREREGISTRATION`이다. Config-only는 source 25건 외
    runtime 0, full은 S107 TBD에서 S107/C02/request/model/GPU/network/
    LLM/API/output 0으로 fail-closed했다. Targeted 25개, 관련 회귀 69개,
    Ruff/format/mypy/schema 검사와 root 독립 targeted 25개가 통과했다.
    실제 request/guard/API/coaching/reference/action/model/metric/result/
    commit은 모두 0이므로 E02 0/1, WP8 0/5와 전체 46/100을 유지한다.
68. 완료(D07 production static readiness만, 실제 전문가 gold 평가 0):
    G04 240건과 분리된 D07 1,000–2,000건 독립 재평가를 위한 config,
    strict item/rating/calibration/locked/completion schema, 기관승인·
    reviewer·frozen preregistration·one-shot locked authorization
    template, read-only contract/CLI, data-model-privacy-statistics card와
    readiness를 구현했다. Config, 다섯 schema, 네 template, contract/
    CLI/unit/integration/card/readiness SHA-256은 각각
    `dc6f74155ee9220193825681e9047fb08b488e8ce2397dedcefece41b8e81a16`,
    `756d8e954077800e5b3de975b8839796b375c5d29d7ce343832d45fdfc994546`,
    `e9c233837d9693ba5de04d4ad86fb10b12dacbc36c496571f23115fd58a0b42f`,
    `fe7ae40123f5d70bd5a99af7e8597656bcb6a6bd9813adcc26a3529e7eff29ce`,
    `c5a352fa3d08631b09c2ec183779e2300a8d075ea9be2db78f0b2b1f79256a2e`,
    `1b9dcb492531cbdd305d13c3e79a87027cd297ba0d41fe0f04bb05683e2a910f`,
    `3e8a0c6314e42ce6fa9ae36cea4bf568f618957d99769592679c7218194abc4d`,
    `fddc578c52392a3e5c72c35ce9eda1fcc59e8080c2f914403d782cc1788d53c2`,
    `2e1bc517b8651f9aed09697fffcfaa8361074efdaedd66317cdd4af7fae82c79`,
    `8479443a2d181e8a8190af81eacd8c51e2353cd1b1e095a5f21b1d2eb65aa3e6`,
    `77ad2f1acb1d2dff06b5d010248c83260f1eca9eab815b897047dba96bbc1a95`,
    `869f952f84c2f9aec463e5edf43ed1a134a750429ca3cbc5ba0fe9225292d98b`,
    `1809dd8998e743610c861976a875a57937545edcd31b5119de8ff4115c4eb218`,
    `28f9b6ecb5b7cb8e3c8e39190c3412110c73c354e67fdc8d66bac9fa8ccb6166`,
    `38b65bfcfd135d6560fce396dc9ea8b5103169fccf9f182492424dde06483be1`,
    `45130ecbd454433a3a4febfdb33db45b91eb3296f60ce0e0617b094ad5a81c83`
    이다. G04는 aggregate 0/240만 읽고 UI state는 열지 않는다. D07은
    800 calibration과 최대 1,200 locked로 분리하고 정확히 독립 primary
    2인, disagreement-only adjudicator, 원평정 보존과 세 가지 sensitivity
    분석을 고정했다. Calibration gate는 scalar ICC/QWK `>=0.70` 또는
    taxonomy agreement `>=0.65`이며, 실패하면 재교육과 별도 재표집을
    요구한다. Exact allocation·자격 증빙·disagreement trigger·통계량·
    CI·cluster·multiplicity·support·one-shot recovery는 TBD다. Config-only는
    source 25건과 G04 aggregate 1건 외 runtime 0, full은 H00 actual
    approval TBD에서 H00/external/D1/R1/audio/rating/locked/model/GPU/
    network/metric/output path/read 0으로 fail-closed했다. Targeted 17개,
    관련 회귀 166개, 전체 1,091개와 Ruff/format/mypy, root 독립
    preflight/targeted 17개가 통과했다. 실제 expert/rating/audio/locked/
    metric/result/registry/commit은 모두 0이므로 D07–D09 묶음 0/2,
    WP1 10/12와 전체 46/100을 유지한다.
69. 완료(H04 production static readiness만, 실제 통계분석 0):
    인간대상 학습효과의 ITT mixed-model 분석을 위한 config, strict
    feasibility pilot/H01 final-N lock/confirmatory H02-H03/aggregate
    input/statistical report/completion schema, frozen SAP·blind analyst
    template, read-only contract/CLI, data-model-privacy-safety-statistics
    card와 readiness를 구현했다. Config, 여섯 schema, 두 template,
    contract/CLI/unit/integration/card/readiness SHA-256은 각각
    `9562274d370758b67ae732a0c971cf6945e6cd15bd8870f4bd5f2d852730b1c3`,
    `45de774d546c010bdc84d3f2d7645bb17a706bfe16506a2fef66469072f998f3`,
    `4dac929f65defcd3d74f4f0b6cfacdf21ae9474f2294c105ca4b9b3b1c7138b3`,
    `4850e106b7048b5b233a08838c0ad2dd5f169567bf02d5b0124b21bc64cdbf59`,
    `96ad0b59d642d52e131be1ca05b797469711cd8bf9f7e0c2dd2de01cfe73527a`,
    `fc2dd9e830a9484efb0ba8308545b320dfc0afe3c614f3891881533a2f8eee5b`,
    `01d33475bf490f85e0362c29511c6bfd7742c581a0b0b98d981a5eb74afb3316`,
    `f693ed86b2a4a6e3650de7ea6c149ad77355cd97976d9df23a514c2f48fbe775`,
    `f3fe61c3ed5b3adb2d6d1fb9603e6316b885ff70757207b5e163247911cae1f0`,
    `e87582a633a0fd76f4642c8f8e7682a2cac85e2d5e841188f91ebfe0d7d70103`,
    `7a0ca62f07273502eea69d89e2eca79cae62847c21edb0f4b71c8fa6b9ef4c01`,
    `56179a5ab75fc9186df9822571cde3c70252cf79f04c6258e6ee4d9a63a60629`,
    `3ec8015be8f4479b6e29266f970e74b7b2b1e935e238b2f82ae451793a719b00`,
    `266c59e1d0ba0a0bd50b57f23884f69f3fb560bdc014b638b7ec00c0171280a0`,
    `94bd1a59375dbc4df0a6645feca22feb2758e6785acf006c6e955878242dd146`
    이다. H00 actual 승인 뒤 조건당 15–20명 feasibility pilot을 별도
    receipt로 완료하고, 그 nuisance aggregate로 H01 final-N을 통계
    담당자·연구책임자가 수동 lock한 다음 SAP를 동결한다. 이후 별도
    powered confirmatory H02/H03 bundle과 blind analyst attestation만
    H04에 허용한다. Pilot receipt를 confirmatory bundle로 재사용하거나
    관측 pilot effect를 power 입력으로 쓰는 경로를 금지하고, pilot
    nuisance SHA·H01 lock·confirmatory N을 교차 결합한다. Primary는
    무작위 배정 ITT와 participant/sentence/rater random intercept mixed
    model이며 raw/standardized effect와 95% CI, MAR MI·MNAR delta·
    complete-case·per-protocol sensitivity, family별 Holm을 요구한다.
    Config-only는 source 19건 외 runtime 0, full은 H00 TBD에서 source
    TOCTOU 19건 뒤 보호 counter 48개 모두 0으로 fail-closed했다.
    Targeted 17개, 관련 회귀 83개, Ruff/format/mypy와 root 독립
    preflight/targeted 17개가 통과했다. 실제 participant/rating/audio/
    restricted-data/statistics/effect/report/registry/commit은 모두
    0이므로 H04 0/1, WP9 0/8과 전체 46/100을 유지한다.
70. 완료(E00 production static readiness만, 실제 폐루프 실행 0):
    attempt 1→actual S108 production handoff→E02 confidence guard→C02
    catalog top-1→exact D08↔R03 licensed reference→attempt 2→same-scorer
    comparison과 blocked safe exit를 강제하는 config, strict completion/
    preregistration/session/trace/authorization/lineage/reference/S108
    handoff schema·template, read-only contract/CLI, data-model-privacy-
    safety-statistics card와 readiness를 구현했다. Config, 아홉 schema/
    template, contract/CLI/unit/integration/card/readiness SHA-256은 각각
    `39a3dd0bf340ab0fde63adf6a3f7b9aef273ba5c1d0b32fa24511528b35dd13b`,
    `2341fde03ad5d0746d1acd5bf289cf838d2ea024229f835c18732fcc641f18f0`,
    `5cd175a577ba792359968ca25433263747a66ea56d14d567c1c99ebd7a2786ce`,
    `842d54b5bf1b881a18497f3de902b1ecb3101042aa7fe34da36a0dc50ec327ac`,
    `265ddd81d8a11a8d9b033f705fe4d3ce95b2ef24fe27414f7548d695799f1196`,
    `958039509bf0a0e1fe76ea77910d93f528046509a7d2dc4e3330b30d96ff99bd`,
    `816b82489d45badcd5208ce6f829acde9f1f02da494b87b66d8bfbcb2b724d98`,
    `4746cf2b9b0da14d235f91bf347f4c02757f1fde9ef3e70d9a6a1f76c5f068ad`,
    `675a8a149f99f31e48bd6eef1cae92437c00c6a3a017f64f66e4d15ba991761e`,
    `b33cfd7aabb459fb1f2b7784e5de235c3d935619982084a16a44f856297151ca`,
    `9f846ae86f1a7a14f3f400224dee08f9b4bfeda5db27377f7dab157b5cd4a75f`,
    `8ff23c5b8c8cce54ea1229ccef722d88f3aad71e5999bb5f4b389222d14981f2`,
    `8dbf1d67f9d2ad03a51ea403b1ee4a3a3599387f9f531c97de75db95263c3fef`,
    `0153d572572ff1d8d3940fb9ca1e71e59c2c4bb78f3f838ef7a5e95b6a5a53c2`,
    `3017cfd732703e9699dc0549dfdccdc7bf17b84c2780b32ff36f4f2bde5cc3fd`,
    `5e329a31582c6a446ed2eda5abbe7abbb2e415d303b430aff520cedc307350bd`
    이다. Locked result copy/replay는 실제 S108 진단을 대체하지 못하고,
    독립 R01 human을 D08로 추정하지 않으며 exact candidate/audio/
    metadata-row/aggregate rights/per-recording grant 교차 결합을 요구한다.
    Fixture의 human-voice false 범위는 learner attempt input에만 적용하고
    licensed D08 human reference는 별도 동의·권리 gate를 통과한다.
    Config-only는 source 50건 외 모든 runtime/output counter 0, full은
    S108 TBD에서 source TOCTOU 50건 뒤 모든 보호 counter 0으로
    fail-closed했다. Targeted 20개, 관련 회귀 222개, 저장소 전체
    1,128개와 Ruff/format/mypy가 통과했다. 실제 input/audio/model/API/
    delivery/rerecord/metric/result/output은 모두 0이므로 E00 0/2,
    WP8 0/5와 전체 46/100을 유지한다.
71. 진행 예정(B07 GOP/SSL 결손을 실행 DAG에 명시):
    구현계획의 S1 Branch B와 Phase 1은 CTC/acoustic posterior 기반 GOP를
    요구하고 원고도 explicit 실패 뒤 frozen WavLM과 GOP를 후속 비교한다고
    명시하지만, 현재 저장소에는 `features/gop.py`, GOP config/cache/model/
    report/registry가 없다. 기존 WavLM Base+ phone cache는 2,233,039개
    oracle target interval embedding과 binary error label을 제공하지만
    realized phone identity는 의도적으로 포함하지 않는다. 따라서 realized
    label을 train acoustic-posterior target으로만 쓰고 dev feature에서
    차단하는 별도 D1 join, phone inventory, target-vs-competitor posterior
    식, speaker-disjoint inner tuning과 dev 1회 계약을 B07로 추가했다.
    실제 구현·학습·metric·registry는 아직 0이며, 이를 임의로 완료 처리하지
    않는다. B07 완료 뒤에만 GOP component를 S106 stack fusion에 추가하는
    amendment를 동결한다. WP3 GOP/SSL 0/2와 전체 46/100은 유지한다.
72. 진행(B07 realized-free 실행계약·입력 감사 고정, 실제 실험 0):
    Base+ phone cache와 explicit phone cache의 schema/report를 다시 감사해
    별도 realized-label join이 필요하다는 초기 가정을 철회했다. Base+
    cache는 realized identity를 의도적으로 제거했지만 정상
    `binary_error_label=0` train row의 정규 `target_label`만으로 38-class
    acoustic posterior를 학습할 수 있다. 실제 입력 감사에서 train/dev
    target interval은 `1,808,724/424,315`, match-only는
    `1,701,622/387,203`, target/match inventory는 두 split 모두 38종이었다.
    `GOP_ACOUSTIC_POSTERIOR_READINESS_KO.md`에 5-fold speaker cross-fit
    prototype posterior, uniform prior, train-inner temperature 선택,
    target-vs-best-competitor GOP, B03 여섯 explicit feature 결합,
    seed 42/43/44의 train-inner C/threshold 선택·full-train refit·dev 1회,
    immutable generation/registry/commit과 S106 4-component amendment
    조건을 고정했다. 이는 설계·입력 감사일 뿐 구현·학습·metric·registry·
    commit은 모두 0이므로 WP3와 전체 46/100은 유지한다.
73. 완료(D09/H02/H03 production-static readiness만, 실제 인간연구 0):
    `d09_h02_h03_longitudinal_collection_v1.yaml`, strict D1/A1 schema
    11개, preregistration/manual/authorization template 3개, contract,
    read-only CLI와 데이터·개인정보·안전/readiness 문서를 완성했다.
    Feasibility는 두 비교 각각 `15–20/arm`의 no-efficacy pilot이며,
    관측 treatment effect를 power 입력으로 쓰지 않는다. 완료 뒤 H01
    수동 final-N·공개 prereg·frozen SAP를 거쳐 participant/audio/rating을
    재사용하지 않는 confirmatory를 별도 모집한다. 한 phase 안의
    C3−C0와 C5−C3도 shared C3 없이 서로 다른 participant 집합으로
    randomize하며 participant/audio/rating/copy 교집합과 stage 2 활성화 전
    unblinded-effect access를 0으로 강제한다. Delivery DAG는 stage 1
    `S108+E01+C02+E02+C03`, stage 2
    `stage1+E00+R03+R04`이고 E00의 D08/R03 권리 lineage를 결합한다.
    Config/policy/contract/CLI SHA는
    `1bb10f98…d92b`/`21688508…6d2`/`3be8cfe6…709d`/
    `14c86c5b…befe`다. 독립 config-only source 46/46, schema 11/11,
    targeted·DAG 회귀 243/243, 전체 1,165/1,165와
    Ruff/format/mypy를 통과했다. Full feasibility/confirmatory는 H00
    TBD에서 모든 보호 path/read와 실제 human/model/API/intervention/
    rating/statistics/output counter 0으로 종료했다. 따라서 정적 준비만
    완료했고 D09/H02/H03 실제 배점과 전체 46/100은 유지한다.
74. 완료(B05 production-static readiness만, 실제 one-shot locked 평가 0):
    Explicit B03와 향후 S108을 같은 사전등록 batch에서 단 한 번만
    평가하는 strict schema 9개와 template 2개, checkpoint hash-only
    검증, 순차 gate, one-shot authorization 소비, Parquet prediction
    manifest, 단방향 hash DAG, exact slice·bootstrap·Holm 결과 계약,
    read-only CLI와 readiness 문서를 완성했다. 최종
    config/policy/contract/CLI/doc SHA-256은
    `9efafe48…c4c`/`1af53682…3e4`/`5c523c64…c1fe`/
    `9a89ee31…d8f9`/`d3231568…b0f2`다. 독립 감사에서 발견한 두 Python
    파일의 formatter drift를 수정하고 contract pin을 다시 고정한 뒤,
    config-only source·TOCTOU `35/35`, B03/B06 boundary `1/1`,
    schema `9/9`, targeted `20/20`, Ruff check/format과 mypy를
    재검증했다. 저장소 전체 `1,185/1,185`는 formatter 수정 직전에
    통과했으며 수정은 두 파일의 기계적 재포맷과 해당 source pin 갱신으로
    한정했다. Full preflight는 예상대로
    G04 TBD에서 fail-closed했으며 G04 path/read와 모든 후속·locked/model/
    GPU/metric/output counter는 0이다. 따라서 production-static 준비만
    100%이고 실제 locked 평가·연구 완료는 0%, WP3와 전체 46/100을
    유지한다.
75. 완료(B07 production-static 구현·독립 감사만, 실제 정본 실험 0):
    Realized-free GOP acoustic posterior의 config, GOP 함수, strict
    contract, bounded CPU cross-fit trainer, immutable publisher와
    read-only deep validator를 구현했다. 최종 config/readiness/GOP/
    contract/training/prepare/train SHA-256은
    `dd03731649fd3b9d1347b0ee8eda3df5163fa199c2c97d5967e401265622c209`/
    `78d25dc6dcb75413a30c5221599722b93d88ec26a9f18a93e426672e873126a6`/
    `dcc0a2f163e7ddeccca39490a48c14c668be775131167e46051bc97143ddefc6`/
    `355be8c18492b1f3b462f2436c810b15d7cdc4cd0428a3c72ef7b19ee94d704d`/
    `c18eede32c0d30714dfb9e38ca2d0b78295260bca80576383585f8a5d2863e5d`/
    `259445b1cd690ea2f475c697396b152aeac58b70e71cc500a4a55a5b12b129b7`/
    `ab76bc149d3f37eb5745c389110ab92ea08ec650e0631d304321cb933a12f192`
    이다. 독립 config-only에서 source pin `11/11`, 보호 cache/train/dev/
    metric/.env/raw/stack/calibration/locked/output 접근 0을 확인했다.
    Focused `20/20`, torch 선행-import integration `2/2`, 저장소 전체
    `1,205/1,205`, Ruff check/format과 mypy가 통과했고, 독립 selection/
    leakage/resource/commit 감사와 19종 critical mutation rejection 결과
    C/H/M은 `0/0/0`이다. 그러나 canonical 두 실행 flag는 아직
    `false`이고 generation root·registry·commit·실제 dev metric은
    모두 0이다. 따라서 production-static만 완료했으며 GOP/SSL 0/2,
    WP3 6/12와 전체 46/100을 유지한다. 다음 행동은 두 flag의 일관된
    승인 전환, CPU-only 정본 실행, completed commit의 read-only 전수
    재계산이며, 그 뒤에만 S106을 4-component로 개정한다.
76. 진행(B07 정본 attempt 1 fail-closed·호환성 수정, 실제 metric 0):
    Static config의 status와 두 permission만 일관되게 승인 상태로 바꾼
    config SHA `ae89aeb072c8837df66de13049bfa1323b57e0a15c6b63a9dc72eb65ee6ef8ff`
    로 정본 실행을 시작했다. 두 9-shard cache의 hash와 2,233,039행
    train/dev metadata 감사를 마친 뒤 첫 train embedding scanner
    생성에서 PyArrow 25.0.0이 tuple `columns`를 거부해 4.77초 만에
    실패했다. Failed registry SHA는
    `0b0ff6542d1ceb4709036cd0be799c1b6ae2f3729c0d89d813c213840d1a8b83`
    이며 stage `train_selection_dev_once`, OOM false, metric/artifact/
    generation/commit 0, stack/calibration/locked/raw/.env 접근 0이다.
    Train embedding batch 소비, model fit, dev embedding projection과
    dev metric도 0이다. Retry가 registry를 덮어쓰므로 원 config/source/
    registry hash와 경계를 별도 failure receipt SHA
    `19b96c9f31ef1d6315668ae12d3d702ab18dd3365069ee768e3983ab278ca0cc`
    로 보존했다. Scanner `columns`를 list로 바꾸고 필터가 반환할 수 있는
    0-row batch를 offset/key 검증 전에 skip하되 최종 exact row-count
    검증은 유지했다. 실제 ParquetEmbeddingSource 회귀를 추가한 최종
    training/config SHA는
    `891389afb3bcaca18ec13d077d4ecf193e3876a04e25f96cc034e7db54b387ea`/
    `9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0`
    이다. Source 11/11, focused 21/21, torch 선행-import integration
    3/3, 전체 1,206/1,206, Ruff/format/mypy와 독립 GO 감사를 통과했다.
    아직 실제 dev metric·completed registry·generation·commit은 0이므로
    GOP/SSL 0/2와 전체 46/100을 유지하고 수정 config로 정본을 재실행한다.
77. 진행(B07 정본 attempt 2 실행 시작, terminal evidence 대기):
    2026-07-29 15:36:53 KST에 config SHA
    `9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0`
    로 CPU-only 정본 재실행을 시작했다. PID `515064`, 시작 51초
    heartbeat에서 CPU 145%, RSS 2.15 GiB, process thread 19였고,
    registry는 `running`, failure null, metric/artifact/generation/commit
    0, stack/calibration/locked/raw/.env 접근 0이었다. Native pool cap은
    process thread 총수와 별개이며 runner 안의 `threadpool_info()`와
    terminal registry/validator에서 최대 4인지 판정한다. 같은 시점 S104
    PID `386918`은 GPU 학습을 계속하며 두 실행의 accelerator 경계는
    분리돼 있다. Terminal completed commit과 read-only 전수 재계산 전에는
    B07 배점과 논문 수치를 갱신하지 않는다.
78. 완료(S105 prelaunch read-only 재감사만, actual 실행 NO-GO):
    S105 hierarchy config SHA
    `e7818240f477af9957c4e13b88479a69820bbb44d48ece2c47e9a54cfc964ea0`,
    production source pin 8/8, S104 config pin
    `aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9`
    와 그 transitive source pin 14/14를 독립 재확인했다. Config-only는
    S104/data/raw-audio/CUDA/output 접근 0으로 통과했고, S105 관련 53개
    테스트와 Ruff check/format, production mypy도 통과해 C/H/M은
    `0/0/0`이다. 그러나 S104 PID `386918`이 non-terminal이고 commit이
    없으므로 현재 launch는 NO-GO다. S105 execution lock/generation/
    commit/registry도 모두 0이며 actual metric을 열지 않았다. S104 exit
    0→completed commit→public validator의 `valid`와 artifact 16건→GPU
    완전 해제를 확인한 뒤에만 CPU full preflight로 immutable lock을
    만들고 BF16 GPU 정본 실행으로 전환한다. 따라서 S105 0/2와 전체
    46/100을 유지한다.
79. 완료(S104/S105/B07 논문 amendment gap read-only 감사만):
    세 actual commit 뒤의 원고 반영 순서를 `S104 validator → S105
    validator(S104 재검증 포함) → B07 validator → stage-aware renderer
    → active manuscript/evidence-map hash lock → candidate-only validation
    → immutable receipt → 독립 수동 apply`로 고정했다. 현재 18개
    PENDING marker와 exact marker hash
    `1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673`
    는 부분 결과가 생겨도 삭제·변경하지 않고 각 결과 블록을 marker
    앞에 삽입한다. S105의 committed `ablation_table.md`는 재사용할 수
    있지만 S104/B07 paper-safe renderer, S105 bootstrap/Holm/subgroup
    확장 renderer, 세 stage를 함께 묶는 promotion validator·content-
    addressed receipt는 아직 없다. 기존 S103 renderer/promotion은
    schema·232 claim이 hard-coded라 구조만 재사용한다. Generic evidence
    validator도 deep stage validator 호출, dynamic generation pointer,
    immutable validator receipt와 null→NA 변환을 대신하지 못한다.
    따라서 이 결손의 production-static 구현을 병렬 착수하되 actual
    metric·canonical 원고/evidence-map write는 세 completed commit 전
    0으로 유지한다. 감사 자체는 P02/P05 완료가 아니므로 전체 46/100은
    유지한다.
80. 진행(S104 seed 42 epoch 4 validation 종료→epoch 5 fit):
    PID `386918`의 I/O·GPU phase 경계에서 약 15:45:30 KST epoch 4
    inner validation 진입, 15:56:10 warning write와 15:57 이후 fit
    읽기 기울기 복귀를 확인했다. Seed artifact directory와 파일은 0,
    registry `running`, metric/artifact 0/0, failure null, commit 없음이므로
    early-stop/final-dev가 아니라 epoch 5 fit 진입으로 판정한다. 같은
    source pin과 Base+ branch를 유지하고 있으며 terminal evidence 전
    S104 0/2, S105 NO-GO와 전체 46/100을 유지한다.
81. 진행(B07 attempt 2 completed commit 생성, 독립 전수 재계산 중):
    15:36:53–16:04:57 KST의 1,685.01초 CPU-only 정본 실행이
    `completed`, failure null로 종료했다. Generation ID는
    `bc1a7eb174c6a8f69627292f30c9229235dcde8e10e69a7e07899847986d6e09`,
    exact file/registry artifact는 21/21, commit/registry SHA는
    `ff31a88e694d23c89996193646d7c062069c9e776b5ed72ef7fc354950f3907c`/
    `5db35282cab879b3a05fa5a6b5cd9a5cac00c5469215ffc77a6dc34abf95eb12`
    다. Peak RSS는 4,885,979,136 bytes로 8 GiB gate 이내였고,
    OpenBLAS/OpenMP 세 pool 모두 4 threads, CUDA/torch/probe false,
    stack/calibration/locked/raw/.env 접근 0이다. Config/source pin
    11/11은 terminal에도 일치했고 temp generation은 0이다. Registry에
    metric 3건이 seal됐지만 값은 아직 승격하지 않았다. PID `519367`의
    read-only `--validate-commit-only`가 train selection/dev projection,
    acoustic/error checkpoint와 prediction/report를 전수 재계산 중이다.
    이 validator와 별도 hash/schema 감사가 모두 통과할 때까지 GOP/SSL
    배점과 논문 수치, S106 amendment는 보류해 전체 46/100을 유지한다.
82. 완료(S104/S105/B07 논문 amendment production-static만, actual 0):
    세 public deep validator를 `S104→S105→B07` 순서로 먼저 실행하고
    completed commit/report/registry/execution lock을 재결박한 뒤에만
    paper-safe Markdown/JSON과 candidate 원고/evidence-map을 만드는
    stage-aware 패키지를 새 파일 6개로 구현했다. Config/module/CLI/
    readiness SHA-256은
    `f5f95253eb33b7db09fe219235edf59e298354a4e93460d07c32d7dab53a1ae1`/
    `65080d261e63e6455a3079508e64f9baea5a7b5c18c03b18953acbd1a71babd4`/
    `1bfe7dae5c8e0e64bae10d65ff0bf6dac6f5f7d9e927b04db8920befb956c7dc`/
    `aec0e04dc4bf96edbae2ac363ba7ea946e8774c9e26dec853646e1c2468367cb`
    다. S105 committed ablation table byte 재사용과 bootstrap/CI/raw·Holm/
    subgroup 확장, S104/B07 표, JSON null→`NA`, active hash lock, 18개
    PENDING marker/hash 보존, content-addressed generation·renderer/
    promotion receipt·atomic commit, generic evidence validation과 manual
    apply 전 canonical write 금지를 강제한다. 독립 config-only에서
    actual metric/artifact·registry/canonical/output counter 0, pending
    18/hash exact를 확인했고 targeted 6/6, 저장소 전체 1,212/1,212,
    전체 Ruff lint와 신규 4파일 format/mypy가 통과했다. 기존 전역
    formatter drift 36파일과 typing debt 22파일/103건은 실행 중 pin
    보호를 위해 수정하지 않았다. 실제 S104/S105/B07 세 commit 소비,
    candidate output과 canonical apply는 0이므로 P02/P05 배점과 전체
    46/100은 유지한다.
83. 완료(B07 GOP/SSL 정본·독립 전수 검증, adoption gate 통과):
    Attempt 2의 config/training SHA
    `9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0`/
    `891389afb3bcaca18ec13d077d4ecf193e3876a04e25f96cc034e7db54b387ea`
    로 2,233,039-row cache를 CPU-only 실행했다. Generation/commit/
    registry SHA는
    `bc1a7eb174c6a8f69627292f30c9229235dcde8e10e69a7e07899847986d6e09`/
    `ff31a88e694d23c89996193646d7c062069c9e776b5ed72ef7fc354950f3907c`/
    `5db35282cab879b3a05fa5a6b5cd9a5cac00c5469215ffc77a6dc34abf95eb12`
    이고 status `completed`, failure null, artifact 21/21이다. Seed
    42/43/44 phone macro-F1은 `0.6305274645/0.6306092019/0.6301906836`,
    error AUPRC는 `0.2647009421/0.2647094361/0.2643096374`다. 평균±표본
    SD는 F1 `0.6304424500±0.0002218332`, AUPRC
    `0.2645733385±0.0002284113`; within-run explicit-refit control F1
    `0.5644327427`, paired delta `+0.0660097074±0.0002218332`다.
    사전 mean F1 `>=0.60` gate는 통과했다. Commit-only validator가
    read-only full train-selection/dev와 acoustic/error checkpoint를
    재계산했고 root·독립 auditor의 raw Parquet confusion/F1/AUPRC/
    paired/slice/selection replay가 exact 일치했다. Source 11/11,
    generation 21/21, cache report 2+shard 18, prediction
    3×424,315행·17열의 hash/schema가 일치하며 peak RSS 4.55 GiB,
    native pool cap 4, dev predict/metric-open seed당 1/1, dev
    hyperparameter 및 stack/calibration/locked/raw/.env 접근 0이다.
    Oracle alignment·silver-label development-only이며 deployable false,
    canonical B03 대체가 아니다. 고정 원장에 따라 GOP/SSL 2점을 부여해
    WP3 6→8/12, 전체 46→48/100으로 갱신하고 exact B07 lineage의 S106
    4-component amendment를 시작한다.
84. 완료(S106 B07 amendment v2 소비자 계약·접근 의미·TOCTOU 보강,
    실제 generator/실험 0): S106 config/preflight/training CLI/contract/
    projection/training/readiness SHA는
    `6c348c166ba1935b17bf512a0f92442c965a1efe0448edb5bea4c586340ded0f`/
    `edc9d58f8960a3fbf18e83815131909a6bee4de7028653b185c0524ef7b0dfd6`/
    `b1ed2979e086d32aa0fdbbed70e64bb9c5f8039617132cc48195edacc888d88c`/
    `4c245c246c7674b371c078ff0e5af1afb7be68d4e7876c16d82d6929c93ea6ec`/
    `04344e2608554d332fec16d117b82e796e3dd70298fac37798f5ea5ef7f2ddec`/
    `4d3b249a3c5041bf3291addadc858afd51df439b593feae5c7b8ada8b7b5c901`/
    `d214f22a2c2757818e59e23775569cc01a2b7ffa3bb93efe5e085fdc4c555713`
    이다. Exact component order
    `explicit,s104_adapter,s105_selected_hierarchy,gop_acoustic_posterior`,
    4×3=12 prediction, 26 feature, `gop_only`를 유지했다. Public validator
    source 감사를 반영해 성공 preflight의 invocation을 S104 2회(직접 1+
    S105 내부 1), S105 1회, B07 1회로 고정하고 predecessor audit의
    train/fit·dev·dev-prediction read=true와 fusion feature train/dev
    read·predecessor dev-prediction component reuse=false를 서로 다른
    ledger로 분리했다. B07 full recomputation attestation이 없으면
    fail-closed한다. Identity/label/12 component Parquet은 no-follow 동일
    byte snapshot에서 hash→parse하고 inode/stat을 재확인하며,
    BundleEvidence는 deep-copy/deep-freeze한다. Precommit/commit validator는
    정본 bundle normalized-record SHA와 feature-freeze SHA를 다시 계산한다.
    Label은 bundle 단계에서 선언 path/hash 문자열/stat만 확인하고 content와
    computed hash는 feature freeze 뒤 연다. Unit/integration SHA는
    `20be3c7ef28102cc2b8e913251c2acae33c07813a9f3695cba57ab403cdc79f3`/
    `c7337d0935f60f926187ad4c72e54f83fd0e9b800115e3a5d4080c06128ba137`/
    `85b5d1e165e0c0fd6a49b181a81c8cb5249c8c4c32a4a6c0dc046c61aa8df653`,
    S106 targeted 39/39, S106–S108+하위 회귀 309/309, 저장소 전체
    1,220/1,220, Ruff/mypy가 통과했다. S107 config/contract는
    `1818965e9a3d1e954acabe49f703b86ae141ab72c2908a82d2eea26dd527cfd0`/
    `04909937b7b8eb4a135a0f7e44c7ec0afc8a438c29aae2d2cbe3de4dcdafac1e`,
    S108 config/module은
    `3fd60d608a931b560ff4ebe8af9206ed5da0cee930b2ac311c9b9db26be407d8`/
    `38002184f5a48924c79eab88ef3ce9cc9b29beebe3474ab2408c8cdeb31b2ba2`
    로 최소 재고정했다. S108 기존 receipt는 exact legacy allowlist의
    `historical_snapshot=true`일 뿐 새 actual 결과가 아니다. Config-only는
    모든 predecessor/data/output 접근 0으로 통과했고, 실제 full preflight는
    S104 commit 부재에서 fail-closed되어 predecessor audit·stack·label·raw·
    env·GPU·output 접근과 execution lock/prediction/bundle/metric/generation/
    registry/commit 모두 0이다. 실제 loader/materializer/generator는 pending,
    S106 0/1, WP4 11/18, 전체 48/100을 유지한다.
85. 완료(S106 predecessor lineage H1·blocker M1·S107/S108 M2 보강,
    실제 generator/실험 0): S104 독립 감사 finding을 반영해 S106 config에
    S104 config/validator module exact pin을 추가하고, S104·S105·B07
    모두의 `config`, `validator_module`, `report`를 exact
    `{path,sha256,size_bytes}` canonical source record로 execution lock에
    저장하도록 일반화했다. Report는 각 predecessor commit의 generation
    path·artifact record·actual hash/size와 다시 결합하고, S104는
    top-level `experiment_report_sha256`도 확인한다. Bundle document build,
    execution-lock/bundle validation, run, precommit/recovery,
    `--validate-commit-only`가 세 stage의 actual source를 매번
    fail-closed 재검증하며, 기존 public validator 호출 수
    `S104=2,S105=1,B07=1`은 유지한다. Alignment-confidence source 부재는
    canonical null+missing 경로이므로 blocker에서 제거해 실제 blocker를
    exact 4개로 config/runtime/readiness에 정렬했다. 최종 S106
    config/contract/training/projection/readiness SHA는
    `3dc08422ad8bd6c7bbad6a9c852116acd31254751cc8cdcb23c558f2e3e85c62`/
    `bd2bf7f9cdadd95e17ef627e89a3066829e5a1e2d8dd835c0760bf59b9664f25`/
    `1492106ed6ddd2fa1f82ab6b3c4800e4660389ab6bbb9773fb501849fe8970de`/
    `d45b919b5645ff04df2672539bace7670f0df44dba32ef15d1891d2d7d283585`/
    `e47ec2726dd0cc942ca1d7279c2a2a945f2b3bf5695f33ec035ca7ee6cfa62d9`
    이다. S107 config/readiness는
    `1af2feba3ce83ae0e006d83dd5f521a30ffd1d3de7d38b899ded3893831b9f8e`/
    `8d251609ee0cb205cb17e0382d4e380acd24c1b83d56123cb32b91f63c853a5e`,
    S108 config/readiness는
    `e382849fceecc4f5352316aa18a86866ba19734baa4d9b8ad4c4f3ee5e52a93e`/
    `094bb5ff027ac023b165e4f097765aa5285651d6df3bc04ed269f892c52d8078`
    로 갱신하고 직접 후속 pin을 최소 연쇄 갱신했다. Path-based source pin
    514개는 mismatch 0이다. S106 focused 52/52, S107/S108 focused 44/44,
    직접 downstream 회귀 226/226, 저장소 전체 1,233/1,233와 Ruff/mypy가
    통과했다. S106/S107/S108 config-only는 각각 predecessor/data/
    protected/locked/output 접근 0이며 S108 source 14/14가 일치했다.
    독립 frozen-snapshot 재감사는 source/config/readiness 핵심 H1/M1/M2를
    clean으로 판정했고 유일한 medium인 tracker current-row 구 hash는 이
    로그와 S106–S108 current 행에서 교정했다. 역사 로그 84 이하의 당시
    hash는 historical snapshot으로 보존했다. 실행 중인 S104
    config/module/report/commit은 수정하지 않았다. 실제 S106 loader/
    materializer/generator와 12 prediction, bundle, metric, generation,
    registry, commit은 여전히 0이므로 S106 0/1, WP4 11/18, 전체
    48/100을 유지한다.
86. 완료(S106 production component generator v3 동결·문서/hash cascade,
    실제 production 실행 0): 정본 source/config 8개를
    `wavlm_s106_component_inputs_v1.yaml`
    `feafe24c7bcd32130519b884e39bf27fe4ae77d372004de8137cf06d963572b0`,
    generator CLI
    `249a97476ce9635dcf7fcc481a7cd1b13272a110c92e9eab927f4dba59cf4314`,
    generator module
    `31b15dde4d4a2ae4ee0b069a6268a03e951cf0593b39a0e047cde64d4311df5b`,
    projection
    `baeb6192517989d9c6ca42859a5c27e379853652842008e6893baa355a12340e`,
    contract
    `8210581f6d3a9b15b8af6c8d769d0b0a1d51e6b7410801f4210903b5ec037793`,
    fusion training
    `e8366b150f507b12a48a7608fbbb2f9af619fcdd7eb10ffaceda4c9952d88097`,
    train CLI
    `b1ed2979e086d32aa0fdbbed70e64bb9c5f8039617132cc48195edacc888d88c`,
    main config
    `196ed40aa59c77af1f7d4d975941b90229d21a9de16f4109e7621b4c3fbcaabb`
    로 동결했다. Production bundle은 v3, generator commit은 v2다.
    Restricted Parquet disk snapshot 없이 held `O_NOFOLLOW` descriptor의
    filtered/bounded scanner를 사용하고, label은 anonymous unlinked
    staging에서 bounded batch로 쓴다. Exact producer
    commit/registry/receipt/generation identity, sealed tree와 별도 inode
    public view, `0440`/`0550` mode·link count, persistent crash-released
    `flock`, file/directory fsync, private Base+ snapshot cleanup,
    rename→seal crash recovery와 success 전 deep validation을 강제한다.
    독립 감사 두 번 모두 C/H/M `0/0/0`, exact8·AST·implementation pin
    일치, S106 focused `98/98`, S107 targeted `15/15`,
    S106→S107 `70/70`, S107→S108 `44/44`, 직접 downstream `226/226`,
    Ruff/format 9파일을 통과했다. 기본 mypy production 5파일과
    `--ignore-missing-imports` 9파일은 통과했고, 기본 9파일의 두 진단은
    설치되지 않은 PyArrow stub뿐이다. Readiness SHA는 S106
    `aa8b127fd35447a36dfe121cedacb8574297db5bc62bc5e0f8e0bbcb3758386e`,
    S107
    `c1abb3bc1960e78b768ba07e4d0a02712cee3b781f971eb6a97a53db2665e1c7`,
    S108
    `542aabfaba3c13817671e925f8340eb2e7553d4849201f26cdd41caa312d1566`
    이다. S107/S108 config는 각각
    `6702ea196481784319c03e413d8ee6844a060e83407aed204f9344cb0230909f`,
    `038947cc9ee77840870cc0df31a6d66e9384f89908a7d91a8706cda0757e65aa`
    로 고정했다. Readiness pin cascade 뒤 C02/C03/E01/E02/E00/S203/R05/
    B05/D09 config SHA는 각각 `b9589253…4075`/`e75f04da…f391`/
    `e815574a…25bb`/`eab8fcf4…9f43`/`3df3318c…9615`/
    `cd3bf0a2…1950`/`f6286a57…d919`/`233489ef…8aeb`/
    `d36efd2b…cdce`이며 YAML path/hash 708건 mismatch 0이다. 실제
    config-only는 predecessor/data/raw/GPU/output 0이고 actual blocker는
    S104/S105 validated commit 부재 두 개다. 실제 execution lock,
    12 prediction, receipt, label, bundle, metric, generation, registry,
    commit은 0이므로 S106 0/1, WP4 11/18, 전체 48/100을 유지한다.
87. 완료(안정 snapshot 저장소 전체 회귀·D06 라이선스 원문 ledger 분리):
    첫 전체 회귀는 병렬 D06 문서 편집이 canonical
    `DATA_LICENSE_AND_ROLE_MATRIX_KO.md`를 실행 도중 변경해 29건이
    fail-closed 되었으므로 최종 검증으로 채택하지 않았다. 이 실행의
    S106 관련 실패는 0건이었다. D06의 원문 URL 감사 결과는 별도
    `DATA_LICENSE_SOURCE_LEDGER_KO.md`로 옮겨 SHA-256
    `b6ceeb1f874de3071462e98c3526136302ab54d3ea9ecc109400d1a3f03f7cd4`
    로 보존하고, canonical matrix는 기존 exact SHA-256
    `3257b0dcdb511b79576ffd99329bcde0754e477b6215b5ae81f2c609684dcf58`
    로 복구해 source pin drift를 0으로 만들었다. AIHub, FLEURS,
    ZEROTH, KSS, Seoul corpus의 공식 원문 12 URL은 HTTP 200을
    재확인했고 KSS는 `needs-original-license-review`로 제한했다.
    복구 뒤 관련 H00/R01/S200–S203 회귀 `124/124`를 통과했다. 더 이상
    canonical source/config/readiness를 수정하지 않은 안정 snapshot에서
    저장소 전체 회귀를 다시 실행해 **1,685/1,685**를 통과했다. 경고는
    기존 Starlette deprecation 1건과 torch mask-type 6건뿐이다. S106
    `98/98`, S107/S108 `44/44`, direct downstream `226/226`도 최종
    green이다. 이 검증은 구현·계약 정합성 근거이며 실제 S104–S108
    연구 결과나 새 배점이 아니므로 전체 진행률은 **48/100**으로
    유지한다.
88. 완료(P01 검증 SVG·P04/P05 의미 통합): Mermaid 텍스트를 그대로
    신뢰하지 않고 data card, 자동 G0, prompt reservation, explicit filter
    report의 hash·split·count·lineage를 다시 계산하는 deterministic SVG
    renderer를 보강했다. Canonical namespace, import/start 구현 pin,
    stable source snapshot, workspace-root anchor lock, output/receipt
    transaction·rollback, output-root rebind·동시 writer·fault injection,
    XML 1.0·외부 URI 차단을 포함한 34개 회귀와 Ruff/mypy를 통과했다.
    PDF 변환 시각 감사에서 1페이지, JavaScript 0, 한글 font embedded,
    겹침·잘림 0을 확인하고 세로 edge label을 수평으로 교정했다. 최종
    SVG/receipt SHA-256은 `d697e42cea9a820c24c082dd183653bc0a59a9ba4e3406c85eafb8117437ce9e`/
    `4809b501e09c8f374e4a27760fea9a6e35f76f7d173b9eb8490b85f586bc7eff`다.
    이 SVG와 자료 license ledger를 evidence-map에 추가하고 stale P04
    C `9`를 canonical `7`로 교정했다. Evidence validator는 artifact
    `36/36`, claim `475/475`, PENDING `18`, locked guard를 통과했고
    report SHA-256은
    `026799ab9ceb3d93128cc7c4558dcbe8d5ed4f0be3afefe19db198bb5e48b7bd`다.
    B07의 dev-only 결과는 S104/S105와 함께 review할 기존 amendment
    경로에 남겼으며, 외부 전문가 G0와 license `PENDING`은 해제하지
    않았다.
89. 진행 중(draft PDF production 경로): Markdown→sanitized HTML,
    isolated LibreOffice 두 번, Ghostscript 결정 정규화, Poppler
    구조·글꼴·텍스트·URL·JavaScript 검사, 전 페이지 PNG, content-addressed
    publish와 post-deep rollback의 PDF01–PDF08 계약을 검토했다. 세부
    PDF 실행 계획 문서는 retired-contract 통합 안내로 이동했으며, 현재
    `docs/paper/`에는 원고·evidence 정본만 유지한다. 실제 feasibility
    probe는 A4 19쪽, PDF 1.7, 암호화·JavaScript 0, 관측 font 8/8
    embedded였고 독립 두 normalized PDF가 SHA-256
    `55bee3315b8355822c58f640c783449ca746621905c59f28ed76f37a53a97d4e`로
    일치했다. Host의 network namespace는 허용되지 않아 이를 숨기지
    않고, `strace` 변환 probe의 network syscall 60행이 모두 `AF_UNIX`,
    `AF_INET/AF_INET6` 0임을 확인했다. Production builder·deep
    validator·공격 회귀와 canonical draft artifact는 구현 중이며,
    최종 사람 시각 승인·실험 결과가 없으므로 P05 완료나 새 배점으로
    세지 않는다.
90. 진행(S104 첫 CUDA 시도 외부 SIGTERM 종료·정본 사용 금지):
    unified-exec 세션 `91182`의 PID `386918`은 seed 42 epoch 9 fit 중
    마지막 23:23:17 KST에 생존했고 23:23:47 최초 부재, 세션 회수
    exit code 143으로 종료됐다. root와 독립 감시자는 signal·kill·
    terminate·restart를 보내지 않았으며, kernel/NVRM/XID/OOM/reboot/
    shutdown 증거도 0이다. 발신 주체와 직접 원인은 확정하지 않는다.
    Registry SHA-256 `ff7fac6b…b7f2`는 `running`, ended/failure null,
    metric/artifact 0/0으로 멈췄고 generation에는 자식 0인 partial
    하나만 남았다. checkpoint, prediction, report, completed registry,
    generation, commit은 모두 0이다. 이를 DEV-S104-002
    `INVALIDATING`으로 기록하고 S104 0/2, S105 NO-GO, 전체
    **48/100**을 유지한다. 수동 삭제·registry 편집을 금지하고,
    exact stale state와 pin을 확인하는 one-shot rollover를 공격
    검증한 뒤 첫 시도를 content-addressed archive/receipt로 보존한다.
    새 실행은 동일 immutable config/lock으로 처음부터 수행하되
    persistent user-systemd service와 연속 pin watcher로 unified-exec
    수명에서 분리한다.
91. 완료(G04 1차 240건 실제 전문가 감사·gate 미달·원인 분석): 독립
    검수자가 token 보호 로컬 review UI(브라우저 및 Windows tkinter
    클라이언트)로 240건 전부를 실제 청취·판정했다. 판정을 원본
    worksheet에 반영하는 과정에서 발생한 pinned SHA-256 불일치는
    pristine 사본으로 복구해 해소했다. 결과:

    ```text
    sample_count=240, valid_decision_count=240
    correct_count=178, alignment_accuracy=0.7417
    status=failed (minimum_accuracy=0.95, 228/240 필요)
    reviewer_count=2, maximum_boundary_error_ms.max=50.0
    ```

    scoring TSV/review report/result JSON SHA-256:
    `05a4fc2b7c1fc7cda73eff5fb539dd3d04fa65b0a824fbf0a6daa9e7622f05c5`/
    `add6942a428fad939db4d806773d061d947184be0cedaea13d7ddb1ca8f18e17`/
    `411eb721f05a1c2afd562233a6dee911cc3a9be147e9b7e624cc521942072d17`.

    실패 62건을 층화·자동 operation·검수자 메모로 분석했다(report SHA
    `1bda1868c643d0ee525f815b9638ff23983812b68ef818037c6078d7a29a419d`).
    검수자 메모 기반 가설 두 건(#129 Unicode 자모 인코딩, #216 target
    null)은 원본 데이터 직접 대조 결과 둘 다 사실이 아니었다. 그래도
    `_parse_interval_list`의 null-label 방어(JSON null이
    `str(None)`으로 조용히 `"None"` 문자열이 되던 결함 차단)와
    `align_intervals`의 Hangul 조합/호환 자모 정규화(`unicodedata` 이름
    기반 자체 검증 매핑, 67개 전수 커버)는 별개의 정당한 방어 코드로
    반영했다(`src/voice_coach/data/aihub71490.py`,
    `src/voice_coach/data/intervals.py`). 실제 enriched Parquet 429만
    target interval 전수 검사 결과 `"None"` 문자열 라벨은 0건이었다.

    "발화가 길수록 실패율이 높다"는 관찰은 phone(음소) 개수 누적에 따른
    이항분포 효과로 확인됐다(phone 단위 오류율은 길이 무관 5~8%로
    일정, report SHA
    `ce0303c0ddcb41ce92a2860ad799d253fc57caeb43625b016e8984b958240317`).
    tolerance_seconds/min_overlap_ratio 완화는 deletion+insertion을
    줄이지 않고 오히려 늘려, 파라미터 튜닝으로 해결되는 문제도 아님을
    확인했다.

    이에 따라 `incorrect_interval_count` 필드와 phone-level accuracy
    지표를 review 스키마·GUI·`review_alignment_audit.py`·
    `score_alignment_audit.py`에 도입했다(발화 단위 accuracy를 대체하지
    않고 병행 보고; 기존 240건은 legacy로 null 처리). duration 5초
    이상 utterance만 대상으로 한 독립 30건 표본을 현재(버그 수정
    반영) `align_intervals()`로 재계산해 생성했다(metadata SHA
    `452299fce50289ae04653c9d2bf5ca567dd074e08605e8ac45cdcf96d1a595d2`,
    `docs/audit/aihub71490_manual_alignment_audit_long30_v2_public.tsv`).
    관련 pytest 전체와 D07 계약 테스트 통과, 무관한 사전 존재 실패 3건은
    `git stash`로 이번 작업과 무관함을 확인했다.

    G04는 여전히 95% 미달로 미점수 상태이며, WP2 배점을 올리지 않는다.
    30건 재감사 완료와 phone-level accuracy 채택 여부는 후속 항목이다.

92. 완료(현재 코드 기준 S104 v2 contract·full preflight; 실제 GPU 0):
    기존 v1 실행 시도의 full read-only rollover audit가
    `implementation interval_module SHA-256 differs`에서 fail-closed됐다.
    v1이 고정한 `intervals.py` SHA는
    `60b430cc421920f2f7907b93391d18a80b9f9f47fa83e669ba3a5aa07fed2b9e`,
    현재 checkout SHA는
    `0fc28d734951ca300bb208d2b76b89a9207093886853a0ce65adf30705c247cf`다.
    따라서 v1 registry/partial/lock은 삭제·수정·archive하지 않았다.
    사용자 승인으로 별도 v2 config
    `configs/experiments/wavlm_s104_adapter_v2_current_code.yaml`를 만들고
    config SHA `7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434`,
    execution lock
    `artifacts/experiments/aihub71490_wavlm_s104_adapter_v2_current_code/execution_lock.json`
    SHA `c37ce4e3d0aa7b7e0c016c56342397b818d6316d6a0e7e22bab9867a63ab5a3e`를
    고정했다. planned run ID는
    `aihub71490-wavlm-s104-adapter-v2-current-code-dev`, 선택 후보는 Base+,
    포함 utterance는 169,837, preflight status는
    `ready_for_s104_gpu_training`이다. config-only와 full preflight 모두
    raw-audio/GPU/output 접근 0으로 통과했으며, 실제 checkpoint·metric·
    generation·completed registry·commit은 0이다. 이를 DEV-S104-003
    `INVALIDATING`으로 기록하고 S104 0/2, S105 이후 단계의 v2 re-pin은
    v2 completed commit 이후에만 허용한다.

93. 진행(현재 코드 기준 S104 v2 GPU 3-seed 실행 시작; terminal 결과 0):
    v2 execution lock SHA `c37ce4e3d0aa7b7e0c016c56342397b818d6316d6a0e7e22bab9867a63ab5a3e`를
    변경하지 않고 2026-08-03 19:18 KST user-systemd unit
    `intelligentai-s104-v2.service`에서 trainer PID `57618`을 시작했다.
    임시 연속 watcher는 config·lock·implementation hash를 10초마다
    확인하며 mismatch 시 fail-closed 종료한다. 19:20 KST CUDA process와
    약 1.6 GiB GPU allocation이 관측됐으나, checkpoint·metric·generation
    artifact·registry·commit은 아직 0이다. S104 terminal commit과
    `--validate-commit-only` 독립 검증 전에는 S105–S108 v2 re-pin이나
    결과·점수 반영을 진행하지 않는다.

94. 완료(개인 연구 범위 정책 정정; 실행 계약 amendment 미완료):
    기관 승인/IRB를 현재 성인 전문가·원어민·청취자 평가의 선행 gate로 요구하지
    않도록 관련 정본 문서를 수정했다. 개인 scope attestation, individual
    consent/license, privacy, blind/prereg remain. H00은 참여자 모집·중재·RCT·
    학습자 pre/post/retention을 선택할 때만 적용한다. 기존 D07/R04/C03/D08
    v1 CLI/config는 레거시 H00 gate를 계속 검사하며 실제 evaluation은 열지
    않았다. 점수·사람 평가·음성 수집·registry·commit 상태는 변경하지 않았다.

## 6. 외부 의존과 정직한 중단 기준

다음 항목은 자동화 에이전트가 사람 대신 완료 판정을 만들 수 없다.

- 한국어 음성학 전문가의 240건 정렬 감사
- 독립 전문가 2인의 1,000–2,000건 재평가와 ICC/QWK
- 원어민 다화자 reference 녹음 및 사용 동의
- TTS/코칭 blind MOS·CMOS·정확도 감사
- 참여자 연구를 선택할 때의 IRB 또는 기관 연구윤리 검토, 학습자 동의와 모집
- 2주 훈련 및 4주 유지 평가

이 외부 작업을 기다리는 동안 train/dev engineering, 평가 코드,
annotation 도구, protocol, power analysis, 표·그림 자동 생성기를 계속
완성한다. 외부 결과가 없으면 논문에는 “미실시/계획”으로 남기며 숫자를
추정하거나 생성하지 않는다.

## 7. 사전 고정한 충돌 해소 결정

| 쟁점 | 고정 결정 |
|---|---|
| 논문 범위 | 1차 실증 범위는 중국어·일본어 L1 AI Hub 71490으로 제한한다. 더 넓은 한국어 L2 일반화 주장에만 71469/71479/71489를 요구한다. |
| Python | 실측·검증된 Python 3.12 scorer 환경을 기준으로 한다. |
| phone 정렬 | 30ms 후보 창 안에서 strongest unused 일대일 overlap을 사용한다. one-to-many는 ablation 전까지 사용하지 않는다. |
| time-bound | 오류율 1% 미만을 gate로 사용하고 flag가 있는 1,070건은 학습에서 제외·감사 slice로 보존한다. |
| explicit F1 | 0.50은 smoke gate, 0.60은 engineering 완료 gate다. |
| TTS 후보 수 | 문장당 4개는 pipeline smoke, 본 품질 실험은 8–16개다. `N≥100`은 계산 상한 설명일 뿐 기본 조건이 아니다. |
| 다중 참조 거리 | hard gate 뒤 k-nearest median/trimmed soft-min을 주 방법, pure minimum을 ablation으로 둔다. |
| coaching 감사 | 더 엄격한 200건을 최종 기준으로 사용한다. |
| 전문가 gold | 2인×최대 2,000건을 목표로 하고 그중 800건을 calibration 용도로 사전 지정한다. |
| S2 | Whisper는 내용/WER hard gate, HuBERT 계열은 독립 음성 auditor 후보로 역할을 분리한다. |
| locked test | 수동 G0 통과 전에는 열지 않는다. train/dev engineering 결과는 “provisional”로만 기록한다. |
| word PCC | 독립 word-level 인간 label을 수집하기 전에는 보고하지 않는다. phone label에서 파생한 순환 정답을 금지한다. |
| WP4 S103 이후 | S104–S108의 분기·split·label·gate·단일 locked batch는 [후속 실행 계약](./WP4_POST_S103_EXECUTION_CONTRACT_KO.md)을 따른다. 이 계약 자체는 task 완료나 배점 근거가 아니다. |

## 8. 논문 결손 지도

현재 PDF는 10쪽짜리 설계/PoC 보고서이며 Results 절과 편집 가능한
`.tex`/`.docx`/`.md` 원본이 없다. 선행연구 인용 수치, 목표값, 설명용
가상 JSON을 실제 결과와 구분해야 한다.

최종 개정에는 다음 산출물이 필요하다.

1. 데이터·화자·시간·split·QC 표와 filtering flow
2. explicit/Base+/Large/adapter/HiPAMA/fusion 주 결과와 ablation
3. calibration/risk-coverage와 subgroup forest plot
4. S2/metamorphic 결과
5. TTS 자동·blind 인간평가
6. 실제 비식별 진단→코칭→재시도 사례
7. feasibility/RCT pre·post·retention 결과
8. seed, hyperparameter, data/model/split hash와 실패 사례 부록

현재 논문에 반영된 실측값은 322,380발화, 1,830화자, 344.208시간,
고정 split·자동 G0, explicit dev·사후 threshold 실패 결과, Base+ full
feature cache·전수검증·GPU resource와 Base+ 3-seed head dev 결과다.
Base+ head 값은 oracle target boundary와 silver label을 쓴
development-only engineering 결과이며 promotion·배포 성능이 아니다.
Locked-test, Large/S103, 외부 전문가·교육 효과 결과는 아직 포함하지
않으며 cache/resource 수치를 모델 성능으로 해석하지 않는다.

## 9. 모델링 label 고정 결과

modeling Parquet은 prompt 원문 대신 정규화 prompt SHA-256을 저장하지만,
음원 접근을 위한 원본 상대경로와 file stem을 보존한다. file stem의
prefix가 원시 `UserID`와 연결될 수 있으므로 이 파일은
`data/processed/` 아래의 **접근 제한 운영용 artifact**이며 공개·학습
캐시용 비식별 artifact가 아니다. 외부로 나갈 수 있는 feature cache에는
namespaced SHA-256 `utterance_id_sha256`만 저장하고 원본 경로·stem을
금지한다.

- record/column: 322,380/30
- unique prompt: 229
- 발음 점수 유효: 322,337, 결측 sentinel: 43
- 이해도 점수 유효: 322,337, 결측 sentinel: 43
- 유창성 점수 유효: 81,780, 결측 sentinel: 240,600
- modeling Parquet SHA-256:
  `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1`

공식 평정 척도는 1–5이므로 원본의 0은 최저 점수가 아니라 미평정으로
mask한다. prompt가 229개뿐이고 모두 반복되므로 기존 speaker split의
결과와 별도로 prompt challenge를 설계한다.

## 10. Explicit feature cache 실측

privacy 교정과 8-worker 병렬화 뒤 train/dev 전용 canonical utterance
cache를 전량 생성했다.

- source scan: 322,380/322,380
- 포함: 169,837발화
  - train 137,759
  - dev 32,078
- 제외:
  - stack/calibration/locked-test 136,629
  - QC flag 642
  - 예약 prompt 15,272
- column: 42
- 처리량: 665 utterances/s, 2,525× audio realtime
- SHA-256:
  `a9f99d66b200468dd23733b232265c035577c81e6bf7aaa565871c7f5863558f`

발화·화자 ID가 전 행에서 소문자 64자리 SHA-256인지 확인했고, 원본
file stem·경로·realized phone·alignment operation·error tag/count 열은
없다. 이 결과는 metric을 계산하지 않은 oracle-boundary feature
artifact다. canonical utterance report SHA-256은
`2f7b540b5fb6d2aaad3e2efa29da5f5b23c967645da0eb98d9a6d68ffc5b762e`다.

선택적 phone cache도 같은 train/dev/QC/prompt filter로 전량 생성했다.

- target phone: 2,233,039건
  - train 1,808,724
  - dev 424,315
- binary label:
  - correct 2,088,825
  - error 144,214
- shard: 9개(250,000건 8개 + 233,039건 1개), 총 44 MiB
- phone report SHA-256:
  `c11e7ed85461d9c623aad390ff65b94981c686aaa859494c07a7f569ed29e53a`
- `oracle_alignment=true`: 전 행 및 모든 Parquet schema metadata
- model input allowlist: target/이전/다음 phone, oracle duration,
  segment RMS/ZCR
- target-only 열: `binary_error_label`

phone artifact에는 realized label, alignment operation, error tag/count를
내보내지 않았다. 따라서 gold operation은 binary target 생성에만
사용되고 모델 입력에는 들어갈 수 없다. phone run에서 함께 만들어진
cheap utterance 파일은 report에
`incidental_duplicate_for_phone_cache`로 명시했고 canonical SHA-256
참조와 join-key 순서가 완전히 일치함을 확인했다.

개인정보 교정 전 생성된 미완성 cache는 final publish 전에 중단하고
정확히 식별한 `.part`만 제거했다. 최종 실행 후 임시 `.part`는 0개이며,
9개 shard의 개별 행 수·SHA-256이 report와 모두 일치한다. ProcessPool
테스트는 동적 import 객체를 직접 pickle하지 않고 실제 CLI subprocess
경로를 사용하도록 교정했다. 당시 전체 125개 테스트가 통과했다.

## 11. Explicit 기준선 development 결과

`aihub71490-explicit-v1-dev-primary-20260729` run이 2026-07-29
03:06:47–03:08:19 KST에 완료됐다. 공통 registry는 config 1개와 data
artifact 11개, metric event 4개, 결과 artifact 13개의 크기·SHA-256을
기록했다.

주 분석 범위는 Chinese/Japanese L1과 gender F/M이다.

- primary train/dev:
  - utterance 135,443/32,063
  - phone 1,776,049/423,820
- phone balanced logistic, selected `C=10.0`
  - macro F1 0.490189
  - AUPRC 0.161323
  - all-dev audit F1 0.490127
- utterance cumulative ordinal logistic
  - pronunciation QWK/Spearman/MAE/ICC:
    0.1233/0.1655/0.8624/0.1435
  - fluency: 0.4483/0.4780/0.8036/0.4698
  - comprehensibility: 0.1153/0.1633/0.9060/0.1299

세 seed의 결과가 같은 것은 결정적 solver 반복 때문이며 불확실성이 0이라는
뜻이 아니다. phone smoke 0.50, engineering 0.60과 모든 utterance
Spearman 0.50 gate에 미달했다. gate를 낮추거나 결과를 숨기지 않는다.
Train speaker 내부 validation으로 threshold를 고른 사후 탐색
ablation은 완료했으며, frozen WavLM Base+와 GOP 보강을 다음 비교로
진행한다.

결과 report SHA-256은
`a5c3ade0738777409bb57956b088e025081cb63886808c6d6fc87724d0875519`,
완료 registry SHA-256은
`0445d69cb18fd9105a531a4e5d342d60c7cad94050c6a031c0d7ee3ef6e2141e`다.
주 분석 범위 오류를 발견해 publish 전에 중단한 첫 run은
`failed/KeyboardInterrupt` registry로 보존했다. stack/calibration/
locked-test는 두 run 모두 읽지 않았다.

논문용 고정 표는 현재 유지되는 `docs/paper/tables/` 정본에 통합했다.

## 12. 갱신 기록

- 2026-07-29: 장기 목표 등록. 논문·실행계획·현재 자산 병렬 감사 시작.
- 2026-07-29: 전체 연구를 WP0–WP10, T00–P05로 분해하고 초기 가중
  진행률 24/100을 기록.
- 2026-07-29: 설정의 다대다 phone 정렬과 오래된 manifest 경로를 실제
  일대일 G0 pipeline 및 로컬 enriched Parquet 경로로 교정.
- 2026-07-29: 논문 결손·계획 DAG·머신 준비도 병렬 감사를 병합하고,
  11개 문서/설정 충돌의 사전 고정 결정을 기록.
- 2026-07-29: 322,380건/30열 modeling Parquet 생성. 1–5 이외의
  평정값 0을 결측으로 격리하고 prompt hash와 세 발화 점수를 추가.
- 2026-07-29: 전체 344.208시간 data card 생성. realized phone 보수적
  정규화 coverage 99.9682%, error tag 17종 ontology 미매핑 0을 확인.
- 2026-07-29: 20개 prompt를 사전 예약해 5,522건 prompt-disjoint
  challenge와 다른 split 제외 21,240건을 고정.
- 2026-07-29: soundfile decode, 16kHz polyphase resample, padding과
  20초 label-aware chunk API 및 테스트 구현.
- 2026-07-29: aarch64/CUDA 13 scorer exact lock 79개가 현재 환경과
  일치함을 확인했다. `pip check`의 `nvidia-cusparselt-cu13` 플랫폼
  metadata 경고는 별도 기록하고 실제 CUDA smoke 결과와 함께 판정한다.
- 2026-07-29: secret-conscious experiment registry를 구현하고 단위
  당시 테스트 6개를 통과했다. `.env*` 추적을 차단하고 config/data/model/
  checkpoint hash, seed, git 상태, 런타임·GPU, metric을 원자적으로 남긴다.
- 2026-07-29: explicit cache 초기 실행 5,000건 시점의 개인정보 감사를
  통해 원본 file stem prefix와 `UserID`의 연결 가능성을 발견했다.
  공개되지 않은 미완성 산출물을 즉시 폐기하고 cache join key를
  namespaced SHA-256으로 교체했다. 운영용 manifest와 공개 가능한
  파생 artifact의 경계를 재분류했다.
- 2026-07-29: `DATA_PRIVACY_BOUNDARY_KO.md`에 R0/R1/D1/A1/C1 구역,
  D1 금지 열, 원자 publish·실패 cleanup·반출 검사를 고정했다. 새
  schema의 utterance 100건/phone 608건 smoke에서 64자리 해시와 원본
  stem·경로 열 미포함을 확인했다.
- 2026-07-29: FLEURS/KSS/Seoul/ZEROTH 38,729개 audio object,
  118.519시간의 통합 native manifest를 생성했다. source 기대 수,
  libsndfile metadata 38,729건, stable ID 중복 0, 절대경로 0과 checksum을
  확인했다. locator가 있는 Parquet는 R1, raw stem·path 값이 없는 집계
  report/data card는 A1 후보로 분리하고 KSS/Seoul 비상업 제한을 유지했다.
- 2026-07-29: 외부평가·참여자 연구 실행 패키지는 현재 개인 연구 범위와
  맞지 않아 retired-contract 통합 안내로 이동했다. 사람 평가나
  참여자 연구를 완료했다고 주장하지 않는다.
- 2026-07-29: train/dev explicit utterance cache 169,837건을 전량
  생성했다. source 322,380건을 모두 scan했고 평가 split 136,629건,
  QC 642건, 예약 prompt 15,272건을 제외했다. hash ID·금지 열·artifact
  checksum을 독립 재검증했으며 phone cache 생성을 시작했다.
- 2026-07-29: 로그에만 남고 JSON이 누락된 WavLM Large GB10 BF16
  benchmark를 pinned local revision으로 재실행했다. 8/12/20초 OOM 0,
  p95 RTF 0.00522–0.00624, peak allocation 1.38–1.64 GiB였고 artifact
  SHA-256을 실행 로그에 기록했다.
- 2026-07-29: explicit primary-scope 3-seed dev run을 완료했다. phone
  macro F1 0.4902/AUPRC 0.1613, 최고 발화 Spearman 0.4780으로 두
  engineering gate 모두 실패했다. 결과·checkpoint·prediction·slice
  13개 artifact와 완료 registry를 검증하고 논문 표에 반영했다.
- 2026-07-29: explicit 실패를 privacy-safe aggregate로 분석하고,
  train-only speaker-disjoint threshold ablation을 사후 탐색 분석으로
  격리해 실행했다. Macro F1은 0.5599로 개선됐으나 AUPRC 0.1613은
  불변이고 0.60에 미달해 B06과 WP3 획득 점수를 유지했다. Report/
  registry hash와 별도 논문 표를 기록했다.
- 2026-07-29: train/dev target phone 2,233,039건을 9개 oracle-boundary
  Parquet shard로 생성했다. correct/error는 2,088,825/144,214건이며,
  shard별 checksum, hash-only ID, 금지 열 미포함, canonical join key,
  atomic cleanup을 전량 검증해 B02를 완료했다.
- 2026-07-29: G04/D07 외부 전문가 검수 준비로 loopback/token 보호 로컬
  review UI/CLI를 구현했다. raw path/file stem은 서버 내부 locator로만
  유지하고 item/reviewer namespaced SHA-256, 선택 audio stream, traversal
  차단, 원자 resume, 중복·미완료·범위 검증, scorer 호환 export를
  synthetic test와 실제 240건 read-only smoke로 검증했다. 실제 상태는
  0/240이며 전문가 결과를 생성하지 않았다.
- 2026-07-29: `microsoft/wavlm-base-plus` revision
  `4c66d4806a428f2e922ccfa1a962776e232d487b`를 local-only/frozen으로
  고정한 cache builder를 구현했다. 공식 pinned preprocessing의
  `do_normalize=false`, `return_attention_mask=true`를 강제하고,
  train/dev·QC·예약 prompt filter, 20초 label-aware chunk, 정확한
  320-sample stride/400-sample receptive field mapping을 테스트했다.
  최신 100건 BF16 smoke는 OOM 0, 102 chunks, 100 utterance/1,798 phone/
  321 unlabeled-word row를 생성했고 schema 금지 열과 raw locator/stem
  값 hit는 0이었다. full 실행은 explicit dev 결과와 config/builder/report
  SHA 및 RSS/disk resource review가 승인될 때까지 CLI gate로 차단했다.
- 2026-07-29: v3 smoke를 독립 자동 engineering review로 재검증해
  config/builder/report hash, 전체 shard, frame geometry, privacy,
  peak RSS와 disk capacity gate를 승인했다. 이는 human expert/G0
  review가 아니다. Review SHA-256
  `9bc7f71f085623d0c84efe3baccbbdb8703c162852738b6a7d5ac1648621edf7`로
  full gate를 열고 bounded streaming 전체 cache 실행을 시작했다.
- 2026-07-29: 원고 수치와 privacy-safe aggregate artifact를 연결하는
  evidence-map/CLI를 구현했다. 해시 drift·누락·NaN·반올림 불일치,
  PENDING 임의 수치화와 locked-test metric 접근을 실패 처리한다. Base+
  full cache, P01 표·cohort source와 clean 재현 report를 포함한 당시
  원고에서 artifact 20/20, claim 183/183, PENDING 18개와
  explicit·threshold·WavLM cache
  미접근 guard가 통과했다.

## 13. 수동 정렬 review 준비 실측

[`LOCAL_ALIGNMENT_REVIEW_UI_KO.md`](LOCAL_ALIGNMENT_REVIEW_UI_KO.md)에
실행·보안·resume·export 절차를 고정했다.

- 실제 worksheet/detail 로드: 240/240
- 실제 전문가 완료: 0/240
- aggregate status: `incomplete`
- alignment accuracy: `null`
- reviewer count: 0
- review state: 미생성
- raw file stem/path의 브라우저 payload 노출: 0
- 선택한 hash item의 audio read-only stream smoke: HTTP 200
- progress report SHA-256:
  `04b9de68d31dbdd3d3d77efc2f9ec710378043eb82840772321c9c0a338b7ca5`

기본 bind는 `127.0.0.1`이며 non-loopback은 명시적 override 없이는
거부한다. 임시 access token은 기관 인증을 대체하지 않으므로 제한
음성을 외부에 공개하지 않는다. state와 scorer export에는 raw item/
reviewer ID나 path를 저장하지 않는다. 240건 완료 전에는 scorer TSV를
생성하지 않고 진행 accuracy도 계산하지 않는다.

## 14. Frozen WavLM Base+ cache smoke 실측

최신 승인 후보는
`data/processed/aihub71490/features/wavlm_base_plus_frozen_v1_smoke100_v3`
와 동명의 `_report.json`이다. 이전 smoke는 구현 중간 검증이며 최신
config/builder SHA를 고정한 v3만 full-run 검토 근거로 사용한다.

- model: `microsoft/wavlm-base-plus`
- revision: `4c66d4806a428f2e922ccfa1a962776e232d487b`
- local-only/frozen: true/true
- preprocessing: 16 kHz, `do_normalize=false`,
  `return_attention_mask=true`
- inference/storage: CUDA BF16 autocast / float16 storage
  (final hidden-state observed float32)
- 입력: train 100건, 예약 prompt 제외 5건, metric 열 접근 0
- 출력: utterance 100, target-bearing phone 1,798, word 321
- chunk/frame: 102 chunks, 24,021 frames, short padding 0,
  hidden-shape↔pinned geometry 불일치 0
- 처리량: 30.85 utterances/s, 148.66× audio realtime
- GPU: NVIDIA GB10, OOM 0, peak allocated 753,814,016 bytes,
  peak reserved 929,038,336 bytes
- process peak RSS: 2,084,425,728 bytes
- bounded writer: utterance 1,024, phone/word 2,048 row-group 상한;
  smoke 최대 buffer 100/1,798/321, 별도 synthetic test 최대 2행
- schema/privacy: float16 fixed-size list, SHA-256 join key,
  prohibited column hit 0, raw locator/stem 값 hit 0, word transcript 0,
  realized phone/operation 0
- full reference row: 169,837 utterance, 2,233,039 phone, 408,790 word
- 압축 관측치 선형 예상: 4,569,214,738 bytes
- float16 embedding 고정 byte 합: 4,579,588,608 bytes
- 1.25 capacity safety 상한: 5,711,518,423 bytes
- config SHA-256:
  `3641d04c974363d615cbc914a3e9d80eff39f9c0e64bc338aa2ff04ab9fef1ba`
- builder SHA-256:
  `2acd48d4f7dbb735dab96cdf4b9222a654627a96cf6a068539f0480839a56dde`
- smoke report SHA-256:
  `6faca0d2f5438e70f59363db3aa4e8f14ff5c1fcb8dd1f016c9d375dc0d09d9f`

Root와 분리된 자동 기술 검증이 위 세 SHA, 전체 smoke shard,
frame geometry, privacy, 실제 peak RSS, 전체 cache 상한과 최대 row-group
행 수를 재검증했다. Review artifact는
`artifacts/reviews/wavlm_base_plus_frozen_v1_smoke100_v3_review.json`,
SHA-256은
`9bc7f71f085623d0c84efe3baccbbdb8703c162852738b6a7d5ac1648621edf7`다.
`review_type=automated_engineering_gate`, `human_expert_review=false`이며,
이는 모델 품질·수동 G0·음성학 전문가 판정이 아니다. 이 승인으로
`--allow-full` gate를 열었다. 이후 bounded streaming 전체 cache를
완료하고 행 수·schema·hash·자원 사용을 독립 검증했으며, 검증된
cache로 Base+ head seeds 42/43/44의 registry-integrated 개발 실행까지
완료했다. Head report·registry·지표·해석 제한은 즉시 실행 큐 31번에
기록했으며 smoke/cache 자원 수치를 모델 성능으로 해석하지 않는다.

## 15. Explicit 실패 분석과 사후 threshold ablation

사전 기준선의 primary-dev 정상 class F1은 0.7628이었지만 오류 class
F1은 0.2176이었다. All-dev의 실제 오류 비율 8.746%에 비해 threshold
0.5의 오류 예측 비율은 37.781%로 높았다. 최소 100건 privacy-safe
all-dev slice에서 Chinese/Japanese macro F1은 0.4888/0.4925,
primary 범위 밖 gender `O`는 0.4265, 숙련도 `Advance`는 0.4710이었다.
Target phone 최저값은 `ㅠ` 0.1347, `spn` 0.1576, `ㅇ` 0.1618이었다.

이 진단 뒤 사전 기준선과 분리된 `post-hoc/exploratory`, dev-only
ablation을 실행했다. Seed별 primary train 화자 860명을 hash 기반
688/172명 inner train/validation으로 분리했고 speaker overlap은 0이다.
같은 여섯 특징과 balanced logistic family 안에서 C와 threshold를
inner validation만으로 선택하고, full primary train refit 뒤 dev를
한 번 평가했다. Stack/calibration/locked-test는 읽지 않았다.

- seed 42/43/44 selected C: 10/10/10
- selected threshold: 0.68/0.67/0.68
- primary-dev macro F1: 0.5588/0.5621/0.5588
- mean ± sample SD: 0.559911 ± 0.001929
- 사전 기준선 대비 F1 차이: +0.069722
- primary-dev AUPRC: 0.161323 ± 0.000000, 기준선과 불변
- engineering gate 0.60: 실패
- B06: `failed_unchanged`
- promotion decision: 허용하지 않음

완료 증거는 다음과 같다.

- failure-analysis JSON SHA-256:
  `bfdcff140326dfb73b09b0c564dbcaef75b1a4a028d865819cc6f9e53aece65b`
- failure-analysis Markdown SHA-256:
  `aff130481880aac4324034bf9c77562d46733d64e6fa79f2c3c282a72f467eb4`
- ablation config SHA-256:
  `62323455d80807ed9ad534bc53f6b21b41feb66836038699cefedc4b8fdedb10`
- ablation report SHA-256:
  `ef16c405ad01d1c5add4c05ce077072b218836ee683e626e12a0e24f4637a7e1`
- completed registry SHA-256:
  `e9c6ba64729f0b4a8bab577f569444f0a1e5689795fde71fc28544d1bdb43c99`
- registry run ID:
  `aihub71490-explicit-threshold-ablation-v1-20260729`
- 논문 표:
  `docs/paper/tables/explicit_threshold_ablation_dev.md`

이 사후 분석은 B06 gate를 통과하지 못했으므로 WP3 획득 점수와 전체
진행률을 올리는 근거로 사용하지 않았다.

## 16. 논문 수치 evidence validation 실측

[`MANUSCRIPT_EVIDENCE_VALIDATION_KO.md`](MANUSCRIPT_EVIDENCE_VALIDATION_KO.md)에
증거 맵 계약, 실행법, 결과 추가 절차를 고정했다. 검증 대상은 원본
데이터가 아니라 privacy-safe JSON report와 processed Parquet 집계다.

- hashed artifact: 34/34 통과
- manuscript claim: 475/475 통과
- PENDING marker: 18개, marker multiset hash 일치
- explicit dev/threshold ablation/WavLM Base+ cache/head locked-test access:
  모두 `false`
- registry metric split: `dev`, `inner_val`, `dev_exploratory`
- overall status: `passed`
- manuscript SHA-256:
  `fca961873ac0fccea878ebd9ef4781d908bdc5f80b672adde15ac6fa1c5df8a5`
- evidence-map SHA-256:
  `b0f11241eb96e40e9ee2c9a2e7a3f568bdb33bd846958dd1071dd23052c2f8ea`
- validation 문서 SHA-256:
  `240a7b7104a70a3f443834758a22c6125625dd60c181cddd275632bd26c7074c`
- validation report SHA-256:
  `7d20db4924ab4d22190c57b2c0065495f2d8e8a9ffdc9f26f5ec3cfa8f9b513f`

P05는 수치 cross-check 기반을 확보했으므로 `진행 중`으로 바꿨다. 최종
완료에는 아직 S104 이후 S1·S2·TTS·학습효과 artifact 연결, 인용·그림
렌더링 검사와 최종 PDF 독립 감사가 필요하다.

## 17. Claude Opus 위임 상태 (2026-08-04)

사용자 승인에 따라 Claude Code 2.1.221의 `opus` 모델(자동 압축 상한 1M)에
인간 직접 평가 관련 후속 작업의 준비·계약·fail-closed 검토를 위임했다.
상세 기록은 [`docs/governance/AI_DELEGATION_RECORD_KO.md`](governance/AI_DELEGATION_RECORD_KO.md)에 둔다.

- Opus는 정적 `docs/`와 거버넌스 문서만 읽었다.
- `data/restricted/`, raw audio, 제한 manifest/locator, 상세 annotation,
  checkpoint, token, `.env*`, 사람 평가 payload 접근은 0이다.
- Claude 파일 수정과 실험 실행은 0이다.
- Opus 반환 상태: `claude_preparation_completed`
- 인간 청취·전문가 판정·동의·참여자 평가 상태: `human_completed`가 아님
- G04/D07/D08/C03/R04/S2 실제 평가와 tracker 점수는 변경하지 않음

Opus는 S104 terminal commit·독립 validator 전 후속 실행 금지, G04 30건의
240건 gate 대체 금지, D07/D08/C03/R04/S2의 실제 인간 판단 필요,
`TBD_PREREGISTRATION`·one-shot·locked 접근의 fail-closed 처리를 재확인했다.
이는 준비·계약 감사 결과이며 인간 평가 완료나 연구 결과가 아니다.
