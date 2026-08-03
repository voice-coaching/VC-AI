# 설계 PDF 요구 실험·실행 매트릭스

기준 시각: 2026-07-29 23:37 KST  
상태: 실행 중  
원문:
`docs/한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처.pdf`  
원문 SHA-256:
`eb403f04e9df77932baba9c671c4e7ee6b2adc2a7eb31bbdc43970e321907ebb`

## 1. 목적과 판정 규칙

이 문서는 10쪽 설계 PDF를 다시 읽어 추출한 요구사항을 실제 코드,
데이터, 실험 결과와 연결한다. 기존
`RESEARCH_COMPLETION_TRACKER_KO.md`의 세부 실행 이력은 유지하되, 이
문서는 PDF의 Phase 0~7과 표 5~8을 기준으로 논문 완주 경로를 한눈에
보여 주는 인덱스다.

상태는 다음처럼 엄격하게 구분한다.

- `완료`: 실제 데이터와 정본 실행 결과, metric, registry/commit 또는
  사람 평가 원본이 있다.
- `개발 완료`: train/dev 또는 oracle/silver 범위의 실제 결과는 있으나
  독립 gold, locked 평가 또는 배포 근거가 아니다.
- `구현 준비`: 계약, runner, 정적 preflight나 합성 테스트만 있다.
- `실행 중`: 정본 프로세스가 살아 있으나 완료 artifact가 아직 없다.
- `미실행`: 실제 데이터·모델·metric 결과가 없다.
- `외부 의존`: 개인 연구 scope attestation, 전문가·원어민 평가 또는
  참여자 연구의 기관 승인·학습자 참여가 필요하다.

PDF에는 IRB 승인 상태, 모집 표본수, power analysis, 특정 코퍼스와 GPU
요구사항이 적혀 있지 않다. 저장소의 IRB·표본수·데이터·하드웨어 계획은
PDF의 관측 사실이 아니라 후속 실행 계약이다.

## 2. 현재 결론

| Phase | PDF 목표 | 현재 실제 상태 | 판정 |
|---|---|---|---|
| 0 | 스키마·rubric·taxonomy·라이선스 | 9축 canonical label·missing-mask와 17-tag ontology primitive는 있으나 실제 9축 rubric/annotation, G04/D07 사람 검증과 최종 라이선스 감사가 남음 | 부분 완료 |
| 1 | m1~m8 모듈형 기준선 | explicit/B07 dev와 eGeMAPS·F0·화자 cosine primitive, g2pK offline expected-phone 및 **현재 project-dev** 고정 16건 expected-source diagnostic, 실제 project-dev 4건의 두 F0 backend bounded diagnostic은 완료; m1 checkpoint·MFA 실제 정렬/DTW·정본 corpus F0 사전등록/확대·UTMOSv2/WavLM-SV 실행은 없음 | 개발 일부 완료 |
| 2 | WavLM-large+HiPAMA+fusion | S101~S103 dev 완료. S104 최초 정본 실행은 외부 `SIGTERM`(exit `143`)으로 비정상 종료돼 commit·metric·artifact가 없고 registry의 `running`은 stale 상태다. S106 self/downstream pin과 정적 회귀는 통과했으나 S105~S108 실제 결과 없음 | 중단·재실행 필요 |
| 3 | 독립 S2+metamorphic | S200~S203/E01 정적 계약, WER/CER, silence/gain/F0 및 seeded Praat duration 변형·격리 합성 worker receipt와 5-family 수치 관계 primitive는 있으나 phone 실제 변형·production runner/trainer·실제 S1/S2 metric 없음 | 구현 준비 |
| 4 | 다중 생성기 reference bank+Pareto | R00 문장 20개만 완료; candidate audio, Pareto, MOS/CMOS 없음 | 초기 단계 |
| 5 | 근거 제한 코칭 | C00/C01 공학 계약·ontology 초안만 있고 C02/C03 실제 결과 없음 | 구현 준비 |
| 6 | GRPO+오프라인 adversarial | 3-player 연구 prototype과 실제 AI Hub·KSS 1-step 연결 smoke는 완료; StyleTTS2/F5R-TTS 정본 학습 없음 | prototype |
| 7 | ablation+blind 평가+RCT | planning/power sensitivity만 있고 승인·참여자·평정·효과 결과 없음 | 외부 의존 |

기존 tracker의 가중 진행률 `48/100`은 데이터·코드·계약·개발 실험
milestone을 포함한 프로젝트 관리 수치다. 이를 PDF의 과학적 검증 완료율,
성능 달성률 또는 논문 제출 가능률로 해석하지 않는다.

## 3. Phase 0 — 스키마·rubric·taxonomy·라이선스

| ID | 필수 작업·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P0-01 | 내용·분절음·duration·pause·억양·energy·rhythm·자연스러움·이해가능성 9축 유지 | exact 9축 순서, unit-interval caller attestation, missing-mask와 observed-only pair 순수 primitive·31개 테스트 | primitive 완료 | 실제 raw rubric 정규화, annotation coverage, loss/head·축별 metric과 독립 평가 |
| P0-02 | phone→syllable→word→utterance 및 accuracy/fluency/prosody/comprehensibility 출력 | S105 model/runner 정적 구현 | 구현 준비 | S105 실제 ablation 실행 |
| P0-03 | 치환·삭제·삽입·왜곡, 종성, 긴장/기식, 모음 질, 음운 규칙 taxonomy | C01 17-tag ontology와 validator | 개발 완료 | joint tag×target-phone 근거와 C03 전문가 감사 |
| P0-04 | duration/pause/F0/경계톤/prominence/rhythm 초분절 taxonomy | PDF의 6축·8개 일반 오류, missing/observed-negative, multi-label·축별 provenance 순수 계약과 30개 테스트 | primitive 완료 | 한국어 ToBI·위치·prominence rubric, raw-tag crosswalk, 실제 이중 annotation과 전문가 검토 |
| P0-05 | `Severity×Confidence×Impact×Trainability`, 상위 1~3개 | prioritizer와 C00/C02 계약 테스트 | 구현 준비 | 보정 confidence와 learner history를 사용한 실제 실행 |
| P0-06 | 저신뢰 재녹음과 내용→음소→artifact→운율 순 lexicographic gate | E02/R02 정적 계약 | 구현 준비 | S107 threshold 뒤 production guard 실행 |
| P0-07 | 생성기·데이터·가중치 라이선스와 배포 경계 | 데이터 역할 매트릭스와 source ledger | 부분 | CoreaSpeech/CosyVoice/StyleTTS/F5 계열 최종 원문·artifact별 감사 |

## 4. Phase 1 — m1~m8 모듈형 기준선

| ID | 모듈·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P1-01/m1 | g2pK/MFA와 XLS-R/WavLM phone-CTC 내용·음소 일치 | observed TextGrid phone용 dev-only loader; g2pK 0.9.4 exact offline adapter·공개 예문 3건 완료. 구 16건 결과는 split drift로 현재 dev 근거에서 제외했고, 현재 modeling manifest의 project-dev 35,074건에서 raw 접근 전에 seed 42로 고정한 16건 expected-source diagnostic은 4/16 exact, macro normalized edit agreement `0.853541`(정확도 아님); MFA native ARM64 blocked, G04·checkpoint·observed agreement 0 | 개발 일부 완료 | 현재 split의 transcript-contract/비정규 source-policy 전수 감사와 사전등록 확대·slice, G04 뒤 CTC, x86_64 MFA artifact/license gate 뒤 predicted alignment smoke |
| P1-02/m2 | GOP/logit-GOP/CTC-GOP 오류 유형·위치 | oracle span의 WavLM prototype-posterior B07 dev macro-F1 `0.630442`; logit/CTC-GOP 비교는 없음 | 개발 일부 완료 | 세 GOP 정의 비교, predicted alignment, 독립 gold/locked 평가와 S106 편입 |
| P1-03/m3 | MFA+DTW duration/pause | energy-threshold pause와 gold interval duration proxy; caller-explicit cost/band/step/tie/normalization의 순수 duration-sequence DTW와 66개 테스트; MFA 실제 정렬·경계 감사·corpus DTW 0 | primitive | 승인된 x86_64 exact runtime, G2P-off micro-smoke·사람 경계 감사 뒤 단위·정책 사전등록과 실제 DTW |
| P1-04/m4 | torchcrepe+Parselmouth F0 contour | 두 exact-version CPU adapter·4주파수 synthetic smoke와 caller-explicit agreement primitive 완료. 현재 modeling manifest project-dev에서 audio-read 전 4건을 고정했고 locked 접근 0; 3건 비교에서 common frame 1,226, jointly voiced 182, voicing agreement `0.609299`, MAE `6.288689 Hz/62.572193 cents`, RMSE `19.256441 Hz/226.004311 cents`, octave 4건. 1건은 PCM 범위 위반으로 fail-closed했으며 bounded incomplete diagnostic임 | 개발 진단 | protocol 사전등록·표본 확대·failure 정책 고정 뒤 non-locked agreement, speaker normalization·경계톤·ablation |
| P1-05/m5 | openSMILE eGeMAPS energy/loudness | RMS proxy; 별도 aarch64 환경과 strict 16 kHz in-memory adapter에서 openSMILE 2.6.0 eGeMAPSv02 88-functionals smoke 완료 | primitive 완료 | 실제 non-locked cache, split/privacy 계약과 ablation |
| P1-06/m6 | nPVI+temporal rhythm | gold word/phone duration의 실제 nPVI 함수·cache | 개발 일부 완료 | predicted segmentation, reference 상대 rhythm metric과 한국어 규칙 감사 |
| P1-07/m7 | UTMOSv2 자연스러움/artifact 보상 | code/HF exact revision·5-checkpoint metadata 감사 완료; weight/local bundle/runtime/실제 후보 점수 없음 | 미실행 | transitive Wav2Vec2/timm·license 승인·offline bundle·safe load 뒤 한국어 OOD audit와 R02/R04 |
| P1-08/m8 | WavLM-SV speaker/style similarity | explicit cosine primitive와 Base+ SV exact upstream manifest/config 감사 완료; checkpoint byte·실제 embedding/audio/consent/style validity는 0 | primitive 완료 | checkpoint 권리·biometric consent·safe offline load 뒤 승인 reference에서 SV와 style construct를 분리 검증 |

재감사에서 발견한 m1 일반 runner의 `test.jsonl` 조기 접근은
교정했다. 일반 smoke/full loader는 train과 reviewed-dev만 열고,
workspace 밖·경로 구성요소 symlink·파일 symlink를 `openat`과
`O_NOFOLLOW`로 거부한다. smoke도 manifest 전체 provenance를 검증한 뒤
앞 5,000/1,000건만 반환한다. 독립 재검증에서 27개 테스트와 실제
train/dev 전체 scan이 통과했다. 다만 이는 G04 240건 수동 감사나
별도 승인된 one-shot locked evaluator를 대체하지 않으며, 두 항목과
checkpoint/PER는 여전히 0이다.

## 5. Phase 2 — S1 WavLM-large+HiPAMA+fusion

| ID | 필수 실험·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P2-01 | 24-layer trainable weighted sum | frozen Base+/Large와 S104 adapter branch | 부분 | exact layer-mix ablation과 source hash 고정 |
| P2-02 | HiPAMA phone→syllable→word→utterance | S105 production runner 정적 준비 | 구현 준비 | S104 commit 뒤 5 variant×3 seed 실행 |
| P2-03 | phone/word/utterance/aspect/calibration multi-task loss | S105 model/contract | 구현 준비 | 결측 mask를 포함한 실제 학습·gradient audit |
| P2-04 | confidence-aware Branch A+B fusion | S106 self/downstream source pin 정합, core 98개·S107/S108 44개 회귀와 4개 config-only 통과 | 구현 준비 | S104→S105 commit 뒤 실제 4-component prediction 생성·fusion 학습·metric |
| P2-05 | Phone precision/recall/F1/AUC, F1≥0.80 | S101/S102 dev F1 약 `0.67` | 개발 실패 | S104~S108 뒤 독립 gold/locked 평가 |
| P2-06 | Word PCC/Spearman, PCC≥0.70 | 최종 word 결과 없음 | 미실행 | S105/S106 word head와 평가 |
| P2-07 | Utterance PCC/Spearman/MAE/ICC, ICC≥0.70 | dev ICC는 목표에 크게 미달 | 개발 실패 | S104~S108와 전문가 gold |
| P2-08 | ECE/Brier/risk-coverage, ECE≤0.10 | S107 runner만 준비 | 구현 준비 | S106 commit 뒤 calibration split 실행 |

S104 최초 정본 실행은 2026-07-29 08:40:44 KST에 PID `386918`로
시작됐으나, 23:19:59 KST 마지막 생존 확인 뒤 23:23:39 KST 최초 부재가
확인됐다. 원 실행 session `91182`의 23:29:19 KST poll은 exit code
`143`을 반환했다. 정본 CLI는 자체적으로 `0` 또는 `1`만 반환하고 signal
handler가 없으므로, 이는 일반 Python 예외가 아닌 `SIGTERM(15)` 종료를
지지한다. 다만 신호 발신자와 이유는 확인되지 않았다. 같은 시간대 cgroup
OOM/oom_kill은 `0`, kernel·system journal의 OOM/segfault/NVIDIA Xid
증거도 없었다. Registry는 시작 시각 이후 갱신되지 않은 `running`,
failure `null`, metric/artifact `0/0`이며 commit이 없다. Generation
root에는 비어 있는 `.part-1ee97821e55d4fa283dea77182283c8e`만 남았다.
따라서 이 실행은 결과가 아니라 **종료 원인 미확정의 중단 실행**으로
판정하며 S105 입력으로 사용하지 않는다. 23:31 KST CPU-only config/full
preflight는 source/data/S103/execution-lock binding이 그대로이고 raw
audio·CUDA 접근 없이 exact lock `8d877806…274ca`를 재사용함을
확인했다. 보존된 incident evidence를 먼저 고정하고, PTY와 분리된
supervisor·exit/signal/resource telemetry·bounded CUDA micro-preflight를
거친 새 실행만 유효 후보로 삼는다.

S106의 19:10 KST snapshot은 component-generator pin 불일치로
40 passed/28 failed였다. 다른 세션이 의도한 source와 self/downstream
pin을 함께 갱신한 뒤, 19:53:48 KST 마지막 수정에서 10분 이상 안정된
28개 파일을 20:04 KST 재검증했다. Core S106은 98 passed, S107/S108
downstream은 44 passed였고 S106 main/generator·S107·S108 config-only
네 개도 모두 exit 0이었다. 실행 전후 28파일 manifest SHA-256은
`b1d2194d82b7ab3338bebc748f8d52a4fd81e7a05d5f8844edd5597ca3fc2646`로
같았다. 이 정적 통과는 실제 S106 prediction/fusion 결과가 아니다.

## 6. Phase 3 — 독립 S2와 metamorphic suite

| ID | 필수 작업·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P3-01 | S1 WavLM과 다른 HuBERT/Whisper backbone | S200 독립성 계약; S201 정규화·WER/CER 순수 함수만 구현 | 구현 준비 | local model bundle과 production 실행 |
| P3-02 | S1 MFA/CTC와 다른 alignment-free CTC/DTW | S200 계약 | 구현 준비 | 실제 동일 item 이중 경로 검증 |
| P3-03 | 독립 전문가 holdout, pairwise+ordinal | S202/S203 schema/preflight | 외부 의존 | D07·별도 holdout·trainer·one-shot 평가 |
| P3-04 | 승인/불일치/불확실성 출력 | S2 output 계약 | 구현 준비 | actual joined prediction과 전문가 판정 |
| P3-05 | silence 추가 시 phone score 불변 | E01 schema와 caller-explicit zero-padding in-memory primitive | primitive 완료 | magnitude/rounding/허용오차 사전등록, 실제 audio·S1/S2 실행 |
| P3-06 | gain 변화 시 pronunciation score 불변 | E01 schema와 caller-explicit dB gain·clipping in-memory primitive | primitive 완료 | magnitude/clipping/허용오차 사전등록, 실제 audio·S1/S2 실행 |
| P3-07 | 특정 음절 duration 확대 시 해당 지표만 악화 | 6-point DurationTier 면적을 exact 제어하는 seeded Parselmouth overlap-add primitive와 seccomp·sealed-memfd·content-addressed single-job **합성** receipt `9bea2805…0145` 완료; 실제 발화·matched control·S1/S2 없음 | 합성 기술 gate | k=1 identity-PSOLA paired control, 승인된 실제 정렬 음절과 S1/S2·collateral metric 평가 |
| P3-08 | F0 scaling 시 정규화 contour 유지 | exact Parselmouth 0.4.7 PitchTier multiplier+PSOLA, duration/sample-count 불변·무 clipping의 in-memory primitive와 합성 180 Hz×1.2 smoke; 실제 발화·독립 backend·relation metric 0 | primitive 완료 | magnitude/common-grid/unvoiced/tolerance 사전등록, 실제 non-locked audio·S1/S2 평가와 collateral-change 감사 |
| P3-09 | phone substitution 시 해당 phone score 악화 | restricted case/worker/A1 aggregate v2 schema와 순수 validator·49개 적대적 테스트 완료; 실제 donor·관측 경계·합성 worker·독립 verifier·metric은 0 | 정적 계약 | 동의된 non-locked 동일화자·동일문장 correct/substitution/same-phone control 녹음과 전문가 경계 뒤 실제 worker·독립 phone/ABX 검증 |
| P3-10 | 전체 metamorphic pass rate와 재학습 trigger | `equal_per_case` count-derived aggregate primitive; 0.95 gate/CI/trigger와 실제 case는 0 | 구현 준비 | 사전등록 집계·invalid policy 뒤 non-locked report와 failure taxonomy |

S200~S203/E01의 관련 110개 기존 테스트와 S201 metric 6개에 더해
silence/gain primitive 합성 테스트 36개, F0 PSOLA primitive 58개,
duration primitive 전용 92개와 수치 relation evaluator 44개가 CPU에서
통과했다. 이는 source pin,
접근 차단, schema와 in-memory 파형·수치 비교 배선 검증이다. 실제 S2
inference/training, 실제 발화 변형, per-case ledger와 metric은 0이다.
PDF와 E01 계약의 정본 변형 family는 silence, gain, duration, F0, phone
substitution 다섯 개이며 현재 phone waveform 변형은 없다. Duration은
caller attestation뿐이던 primitive에서 별도 합성 worker receipt까지
강화됐지만, 실제 한국어 음성이나 S1/S2 relation을 포함하지 않으므로
여전히 논문 성능 근거가 아니다.
구현 계획에만 있는 background noise는 별도 탐색 항목이며 PDF 완료
조건에 넣지 않는다.

E01의 canonical manuscript와 claim-gap source pin drift로 정적 회귀
6건이 실패하는 것을 재현한 뒤 현재 안정 revision에 맞춰 갱신했다.
전용 31건과 config-only는 통과했고 full preflight는 의도대로
`stage=s108_final`의 `TBD_S108_COMPLETION`에서 input/output path resolve
전에 fail-closed했다. S203 자체의 독립 source currency와 실제 실행은
별도 gate로 남는다.

이 수정이 R/S2/C/E/H/D 실행 DAG의 transitive config SHA를 바꾸므로
source pin을 아래에서 위로 다시 계산했다. 특히 R03 사전등록 템플릿에
남아 있던 R01·R02 과거 binding을 현재 값으로 교정하고
R03→R04→E00→D09를 재고정했다. 전체 YAML/JSON parse,
`path+sha256`와 `<prefix>_path+<prefix>_sha256`, 템플릿 내 R01/R02
binding의 독립 감사에서 drift는 0이었고 관련 integration 16개와
unit 17개 파일의 **376 tests passed**를 확인했다. 이는 실행 가능한
설정 그래프의 source currency 근거이며 실제 후보·평정·metric 실행
결과가 아니다.

같은 감사에서 readiness 문서의 “현재 config SHA” 표시는 실행 pin과
별개로 13개가 과거 snapshot에 머문 것을 확인했다. 해당 문서와
연쇄 변경된 D08/E01 문서를 갱신한 뒤 17개 readiness의 표시 hash,
R03 template 표시와 E02 derived policy를 다시 대조해 drift 0을
확인했다. 과거 검증 이력으로 명시된 축약 hash는 역사 기록으로
유지한다.

Phone substitution은 현재 로컬 자산만으로 실제 성공 주장을 만들 수
없다. 단순 splice나 full-waveform prototype은 목표 phone만 바뀌었다는
construct를 보장하지 않는다. 최소 실행안은 purpose-recorded 세 쌍의
관측 경계를 전문가가 확정한 뒤 target 내부에서 duration-matched donor
transplant와 complementary crossfade를 수행하고, mask 밖 PCM identity,
same-phone reconstruction control, 독립 phone verifier와 blind ABX,
S201/S202 collateral gate를 함께 기록하는 restricted v2다. 이 녹음·동의와
전문가 판정에 앞서 restricted sidecar·worker receipt·paired aggregate의
세 Draft 2020-12 schema와 pure no-I/O validator를 먼저 구현했다. U→I,
U→T, I→T와 privacy/lineage/gate를 정적으로 강제하고 49개 focused
test와 strict mypy를 통과했지만, splice·audio·inference·metric은 모두
0이므로 actual 실행이나 완료로 표시하지 않는다.

## 7. Phase 4 — reference bank와 Pareto 선택

| ID | 필수 실험·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P4-01 | text+F0/speed/style/speaker로 다중 후보 생성 | R00 문장 20개, audio 0 | 초기 | R01 human/CosyVoice 후보 생성 |
| P4-02 | text/artifact/pause/target-phone hard gate | R02 정적 계약 | 구현 준비 | 실제 candidate metric과 partition |
| P4-03 | 자연스러움×운율×교육적 명료성×다양성 Pareto | R03 정적 계약과 explicit-policy source-blind Pareto primitive | primitive 완료 | 승인 사전등록과 eligible candidate에 실행 |
| P4-04 | speaker/speed/prosody diversity clustering | 3–5개 exhaustive-maximin, speaker/speed/pitch floor와 replay digest primitive | primitive 완료 | 실제 objective·candidate로 실행하고 source join 뒤 balance 검증 |
| P4-05 | 인간/단일 TTS/S1/S1+S2/multi-ref 5조건 | 실제 조건 audio 없음 | 외부 의존 | D08·R01~R03·S2 |
| P4-06 | source-blind MOS/CMOS | R04 정적 계약 | 외부 의존 | 승인·평가자·audio·rating·통계 |
| P4-07 | WER/UTMOSv2/ECAPA-TDNN SIM | WER/CER·speaker cosine 순수 함수와 UTMOSv2/WavLM-SV upstream metadata 감사만 있고 실제 metric 없음 | 미실행 | exact 승인 bundle 뒤 5조건에 동일 protocol 적용 |

CoreaSpeech 최신 공개 리비전은 카드의 47-shard 표기와 달리 35개
Parquet만 남아 있다. 삭제된 과거 shard를 우회하지 않는다. 현재
고정 리비전의 첫 shard 4,754,132,131 byte를 무인증으로 내려받아 게시
SHA-256과 Parquet magic을 검증했다. 3,592행, 20화자,
15.506219시간이며 아직 `qc_only`라서 정본 학습 완료 근거가 아니다.

PDF와 현재 저장소 계약의 범위도 같지 않다. PDF 후보군은
CosyVoice2·StyleTTS2·F5R-TTS·FastSpeech2/FastPitch·사람 녹음을
포함하지만 현재 R01은 사람/코시보이스 두 종류만 허용하고 `style`
condition을 보존하지 않는다. PDF의 참조 비교식은 후보까지의 단순
최솟값인 반면 구현 계획은 outlier 위험 때문에 k-nearest
median/trimmed soft-min을 primary로 두고 단순 최솟값을 ablation으로
남겼다. R04 역시 현재 source kind 두 개만 표현해 PDF의
인간/단일 TTS/S1/S1+S2/multi-reference 다섯 조건을 직접 구분하지
못한다. 이 차이는 안전한 축소 구현이지 PDF 요구 완료 근거가 아니다.

## 8. Phase 5 — 근거 제한 코칭

| ID | 필수 작업·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P5-01 | targetText/level/topIssues/prosody/advice ID 구조화 입력 | C00/C02 schema | 구현 준비 | actual diagnostic bundle 연결 |
| P5-02 | ontology/RAG 허용 범위 안의 피드백 | C01 catalog, free generation 금지 | 구현 준비 | S107/C02 뒤 executor 실행 |
| P5-03 | 긍정→핵심 1개→ms 위치→구체 지시→minimal pair | advice catalog/contract | 구현 준비 | 200건 생성과 blind expert audit |
| P5-04 | 저신뢰 피드백 보류·재녹음 | C02/E02 contract | 구현 준비 | calibrated threshold와 API guard |
| P5-05 | history/error curve 기반 다음 target | learner model 실제 이력 없음 | 미실행 | E00과 longitudinal learner state |

현재 C02는 개인정보와 근거 일탈을 막기 위해 target text, 자유 생성
문장, runtime LLM과 network를 의도적으로 금지하고 catalog 문장만
반환한다. 또한 오류의 start/end millisecond를 payload에 직접 담지
않는다. 따라서 이는 PDF가 제안한 learner level·prosody·위치 정보를
받는 LLM/RAG 코칭 agent의 안전한 공학 계약일 뿐, 그 agent 자체를
구현·검증한 결과가 아니다.

## 9. Phase 6 — GRPO·adversarial·reward hacking

| ID | 필수 실험·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P6-01 | F5R-TTS GRPO에 S1/S2 scalar reward 통합 | R05 preflight만 있음 | 미실행 | S108/S203/R04/H04 gate 뒤 실행 |
| P6-02 | 발음·운율·교육적 모방가능성 다목적 reward | reward 계약 미고정 | 미실행 | 사전등록·ablation |
| P6-03 | StyleTTS2 discriminator gradient의 offline min-max | 3-player 연구 prototype과 실데이터 1-step 배선 검증 | prototype | StyleTTS2 정본 모델/데이터로 검증 |
| P6-04 | 온라인 S1/S2는 stop-gradient scalar reward만 사용 | prototype에서 직접 gradient 차단 계약 | 구현 준비 | best-of-N/GRPO 실제 trajectory audit |
| P6-05 | hard gate+holdout+metamorphic reward-hacking 감사 | 정적 계약만 있음 | 미실행 | 생성 후보와 S2/E01 결과로 감사 |

현재 `adversarial_tts_feedback_v1`은 구조와 gradient 경계를 검증하는
연구 prototype이다. AI Hub·KSS 음원을 연 1-step CPU smoke에서도
checkpoint를 만들지 않았고 생성기 reward가 개선되지 않았다. 이를
StyleTTS2/F5R-TTS 실행, 품질 향상 또는 논문 표 8 완료로 부르지 않는다.

또한 저장소 R05는 R04 blind superiority와 실제 학습효과·retention까지
확인한 뒤에만 GRPO를 허용한다. 이는 PDF의 Phase 6 학습 뒤 Phase 7
사람 평가 순서와 반대지만, 검증되지 않은 scorer를 reward로 최적화하는
위험을 줄이기 위한 의도적 gate다. 논문에서는 PDF 원안과 실제 실행
DAG를 구분해 기술해야 한다.

## 10. Phase 7 — ablation·blind 평가·RCT

| ID | 필수 실험·성공 기준 | 현재 근거 | 상태 | 남은 실행 |
|---|---|---|---|---|
| P7-01 | generator/scorer/reference ablation | S103 일부 scorer ablation만 완료 | 부분 | 논문 contrast 전체 사전등록·실행 |
| P7-02 | 5조건 expert blind MOS/CMOS | R04 정적 준비 | 외부 의존 | 실제 평가 |
| P7-03 | C0 단일 TTS | protocol만 있음 | 외부 의존 | H00 뒤 구현·모집 |
| P7-04 | C1 C0+종합점수 | protocol만 있음 | 외부 의존 | 동일 |
| P7-05 | C2 C1+다차원점수 | protocol만 있음 | 외부 의존 | 동일 |
| P7-06 | C3 C2+구조화 피드백 | protocol만 있음 | 외부 의존 | 동일 |
| P7-07 | C4 C3+개인화 curriculum | protocol만 있음 | 외부 의존 | 동일 |
| P7-08 | C5 다중 참조+이중 폐쇄루프 | protocol만 있음 | 외부 의존 | 동일 |
| P7-09 | pre/post comprehensibility Cohen's d≥0.5 | 실제 사람 자료 없음 | 외부 의존 | powered confirmatory와 blind rating |
| P7-10 | pronunciation accuracy·satisfaction | 실제 자료 없음 | 외부 의존 | 승인된 척도와 분석 |
| P7-11 | 4주 retention | 실제 자료 없음 | 외부 의존 | longitudinal collection |
| P7-12 | Loop A 품질과 Loop B 학습효과 분리·인과 간극 | 실제 결합 자료 없음 | 외부 의존 | R04+H04 별도 결과 후 분석 |
| P7-13 | PIPA/GDPR·미성년자 동의·편향 감사 | H00 package만 준비 | 외부 의존 | 참여자 연구를 선택할 때의 기관 공식 판단·동의·감사 이행 |

PDF는 C0~C5를 모두 제시하지만 arm 간 primary contrast, 모집 N,
randomization, allocation concealment, 결측 처리와 참여자 연구의 IRB 절차를 정하지
않았다. 저장소는 feasibility와 confirmatory를 분리하고
`C3-C0`, `C5-C3`를 독립 participant-unit 비교로 계획했지만, 이는 실제
승인·사전등록 전까지 계획일 뿐이다.

## 11. 실행 DAG

1. **자동·GPU 경로**:
   S104 중단 증거 보존·bounded CUDA preflight·감독 재실행 완료·commit 검증
   → S105 실제 ablation → S106 4-component
   prediction/fusion → S107 calibration → S108 no-locked/final 준비.
2. **데이터·정렬 경로**:
   G04 240건 전문가 정렬 → D07 독립 gold → B05/S108 one-shot locked
   평가.
3. **S2 경로**:
   S108 final → S200 독립성 lock → S201 Whisper content gate →
   S202 HuBERT pairwise/ordinal → S203 전문가 holdout.
4. **reference/TTS 경로**:
   R00 → R01 후보 → R02 hard gate → R03 Pareto/diversity → R04 blind
   평가 → 조건 충족 시에만 R05 GRPO.
5. **코칭·폐쇄루프 경로**:
   S107 → C02 → C03 → E01/E02 → R03/D08 → E00.
6. **사람 연구 경로**:
   H00 공식 판단 → feasibility → H01 final-N 수동 lock·SAP →
   별도 confirmatory H02/H03 → H04.
7. **논문 경로**:
   완료 artifact마다 evidence map과 결과 표를 갱신하고, 마지막에
   P05 인용·수치·표·그림·PDF를 독립 감사한다.

## 12. 2026-07-29 재감사에서 실제 실행한 항목

- PDF 10쪽 전체 텍스트를 재추출하고 Phase 0~7, 표 5~8 요구를
  원자 단위로 재작성했다.
- 현재 manuscript evidence를 별도 report로 다시 검증해 artifact
  `36/36`, claim `475/475`, PENDING marker `18`, locked-test guard
  `passed`를 확인했다.
- S104 PID와 raw audio read가 증가하는 동안 비파괴 모니터링했으나,
  최초 정본 실행은 마지막 생존 23:19:59 이후 `SIGTERM` exit `143`으로
  종료됐다. 신호 발신자·이유는 확인되지 않았고 OOM/Xid/segfault 증거는
  없다. Stale `running` registry, 빈 partial directory, execution lock과
  원 session poll을 보존했으며 완료 metric/artifact/commit은 모두 0이다.
- CoreaSpeech 현재 공개 리비전의 첫 shard를 다운로드하고 byte 수,
  SHA-256, Parquet magic, aggregate metadata를 검증했다. 인증은
  필요하지 않았다.
- CoreaSpeech의 연구 전용·재배포/2차 수정 금지 경계를 fail-closed로
  확인하고 모든 embedded WAV header를 aggregate-only로 검사하는
  read-only 감사기와 5개 테스트를 추가했다. S104와 I/O를 경합하지
  않도록 3,592행 전체 실행은 아직 시작하지 않았다.
- AI Hub L2+KSS loader, missing-label mask, duration matching과 audio
  resolver를 구현하고 실제 음원 CPU 1-step smoke를 완료했다. learner
  16건과 native 16건을 열어 네 optimizer의 finite loss/gradient를
  확인했지만 checkpoint는 없고 reward도 개선되지 않아 연결 검증으로만
  판정한다. aggregate report SHA-256은
  `24cb23347cb7b99bf659e67be48cc8333c41a418923809dbbced3000eb471055`다.
- H00, D07, D08, R04, C03의 `config-only`를 다시 실행해 각각
  `valid_config_only`를 확인했다. source pin은 순서대로
  12/25/19/25/21개 모두 검증됐지만, 실제 승인·전문가·원어민·평가자
  수와 audio/rating/metric은 모두 0이므로 외부 실험 완료 근거가
  아니다.
- PDF P0-01의 9축을 exact canonical order로 고정하고, 실제 관측
  `0.0`과 결측 zero fill을 별도 boolean mask로 구분하는 순수 primitive를
  추가했다. 신규 31개와 인접 label/API/S105 회귀를 합친 52개 테스트가
  통과했다. Raw rubric 정규화, 실제 9축 annotation, loss/model head와
  사람 평가는 0이다.
- P0-04의 duration/pause/F0/boundary-tone/prominence/rhythm 여섯 축과
  PDF에 열거된 일반 오류 여덟 개만 고정하는 순수 taxonomy 계약을
  추가했다. `None` 결측과 관측 음성에서 오류 없음인 빈 label을 분리하고,
  multi-label과 축별 privacy-neutral caller-attested provenance를
  보존한다. 전용 30개와 인접 회귀 96개가 통과했다. AI Hub broad tag,
  음향 특징 또는 9축 score와의 자동 매핑은 없으며 한국어 ToBI·위치
  rubric, 실제 annotation과 전문가 agreement는 0이다.
- Phase 1 코드를 모듈별로 재감사해 m1 checkpoint 0, m3~m5 proxy,
  m7~m8 scorer 0을 확인했다. m1 smoke의 `test.jsonl`/locked-test
  연결 위험을 발견해 해당 실행을 중지한 뒤 dev-only fail-closed
  loader로 교정했다. 경로 component/file symlink, workspace 탈출,
  limit 뒤 locked provenance와 scan 중 변경을 거부한다. 독립적으로
  27개 테스트와 실제 manifest 전체 provenance scan을 통과했고 반환값은
  train/dev 5,000/1,000, vocab 43이었다. G04와 one-shot final은 미실행이다.
- 별도 ARM64/Python 3.12 환경에 g2pK 0.9.4를 설치하고 exact freeze,
  MeCab dependency와 CMUdict 세 artifact digest를 고정했다. Upstream
  import가 CMUdict 부재 시 network download를 실행하는 경계를 발견해,
  strict adapter는 offline artifact 검증 전 import를 거부하고
  `nltk.download`를 차단한다. Compatibility-jamo expected-phone mapping
  단위 테스트 51개와 공개 README 예문 3건의 network-blocked 반복
  smoke를 통과했다. 이 expected output은 observed TextGrid를 대체하지
  않으며 CTC checkpoint와 m1 metric은 0이다.
- 구 g2pK artifact가 주장한 dev 16건을 현재 modeling manifest에
  identity-join한 결과는 train 11, dev 1, calibration 2, locked-test 2였다.
  322,380↔322,380 unique 1:1 join과 structural/unmatched/duplicate/path
  mismatch 0을 확인했고, legacy selection digest도 exact replay했다.
  따라서 구 `11/16`, macro `0.9464885753` 결과는 split drift 상태로 현재
  dev 논문 근거에서 제외하되 artifact 자체는 변경하지 않고 보존했다.
- 현재 modeling manifest의 project-dev 35,074건만 대상으로 raw JSON을
  열기 전에 seed 42 고정 16건을 선택했다. Project-dev JSON 접근
  `16`, 다른 split·audio·TextGrid·network 접근 `0`, 비교 16/16,
  exact 4/16, Levenshtein 합 31, macro normalized edit agreement
  `0.8535406198`였다. 이는 transcript-conditioned g2pK와 AI Hub expected
  annotation 사이의 bounded expected-source 진단이며 accuracy, observed
  pronunciation, checkpoint 또는 m1 완료 결과가 아니다. Split audit와
  current-dev runner의 신규 전용 테스트 7개, Ruff와 mypy를 독립 재확인했다.
- S201에 계약상 고정된 한국어 정규화와 WER/CER edit-distance 순수
  함수를 구현했다. 결과에는 transcript를 남기지 않으며 threshold는
  호출자가 명시해야 한다. 현재 threshold가 미등록이고 Whisper bundle과
  runner가 없으므로 실제 content gate 결과로 표시하지 않는다.
- S200~S203/E01 정적 계약과 S201 metric을 묶은 CPU 테스트 116개가
  통과했다. S203/E01 readiness의 stale config SHA는 발견했지만 동시
  편집 중인 source에 맞춰 임의 갱신하지 않았다.
- `.venv-paper-acoustics`를 S104 환경과 분리해 openSMILE 2.6.0을
  설치했고 `pip check`, eGeMAPSv02 `[1,88]` finite 합성 smoke를
  통과했다. 이어 버전·mono 16 kHz·88개 고유 열·finite 값을 강제하는
  in-memory adapter와 11개 계약 테스트를 추가했고 실제 열 순서 SHA-256
  `09a99dccd5995f20d3979302ad705c0d07d45c5a3e339f27d0cfd20e9893a3d5`를
  기록했다. Parselmouth 0.4.7은 공개 aarch64 wheel이 없어 첫 build가
  실패했지만 Ubuntu Python 개발 deb를 시스템 설치 없이 임시 추출해
  local wheel을 만들었다. `pip check`와 180 Hz 합성 Pitch 97/97 voiced,
  median `180.000170208 Hz`를 통과했다. 이어 caller가 time-step,
  floor/ceiling과 무성 정책을 모두 명시하는 strict in-memory contour
  adapter를 추가했다. 35개 단위 테스트와 eGeMAPS/explicit 회귀를 합친
  50개 테스트가 통과했고 실제 backend의 contour digest
  `da3b903af0bfda557affca6a6a0d569bca64c2ff3783eab160f508d60460af49`도
  재현했다. 당시에는 실제 corpus의 두 backend agreement가 없었고,
  아래의 bounded current-dev v4에서 처음 실행했다.
- 별도 ARM64 CPU 환경에 torchcrepe 0.0.24와
  torch/torchaudio 2.11.0+cpu를 고정하고 raw backend의 무음 가짜 F0와
  NaN fail-open 경계를 확인했다. 이어 base normalized float ndarray,
  CPU-only, explicit model/decoder/threshold/pad/두 RNG seed를 강제하는
  strict adapter를 추가했다. 신규 63개와 Parselmouth/eGeMAPS를 묶은
  109개 회귀가 통과했고, 실제 180 Hz smoke 101/101 voiced, median
  `181.398773 Hz`, contour SHA
  `1ba5feb8aac92181e8a5429705ed9799278c534a41ca54c9c03156302e3944ae`를
  재현했다. 무음은 no-voiced로 실패한다. 실제 한국어 agreement는 아래
  v4에서 제한적으로 실행했지만 threshold 사전등록과 확대 평가는 0이다.
- Parselmouth와 torchcrepe를 동일 byte의 90/120/180/250 Hz 합성 PCM에
  연결한 반복 agreement smoke를 실행했다. 두 backend median 차이는 평균
  `0.677581 Hz`, 최대 `1.398603 Hz`였고 각 sweep는 반복 시 exact했다.
  다만 같은 10 ms 설정에서도 window/padding 차이로 frame 수가 97 대
  101이므로 frame index 직접 비교는 금지했다. 당시 실제 한국어 contour,
  slice calibration은 0이었다.
- 위 frame 차이를 숨기지 않도록 common grid, linear/nearest,
  strict/trim endpoint, unvoiced gap 보존/연결을 caller가 명시하는 순수
  pitch agreement evaluator를 추가했다. Voicing 4분할과 jointly voiced
  Hz/cents/RMSE/octave 기술 통계를 계산하되 판정 threshold는 없다.
  신규 69개와 두 adapter 회귀를 합친 167개 테스트가 통과했다.
- 현재 modeling manifest의 4-column full scan과 official Validation
  regular-file inventory 35,820건의 unique join을 고정하고, project-dev
  3,964건에서 audio-read 전에 4건을 선택했다. 실제 접근은 dev 4,
  locked-test 0이고 PCM identity는 4/4였다. 한 건은 derived PCM이
  closed unit interval 밖이라 backfill·clipping 없이 fail-closed했고,
  나머지 3건의 common frame 1,226, jointly voiced 182에서 voicing
  agreement `0.6092985318`, pooled MAE `6.288689 Hz /
  62.572193 cents`, RMSE `19.256441 Hz / 226.004311 cents`, octave
  error 4건을 기록했다. 상태는
  `incomplete_bounded_technical_diagnostic`이며 정확도·품질·L1-vs-native
  또는 m4 완료 주장이 아니다. 신규 전용 35개와 Ruff/mypy를 독립
  재확인했다.
- MFA 3.4.1 공식 설치·Conda metadata와 현재 한국어 bundle을
  read-only로 감사했다. MFA 본체는 noarch지만 필수 Kalpy의
  `linux-aarch64` build가 없어 현재 ARM64 native 표준 경로는 blocked다.
  Korean bundle은 exact revision으로 고정 가능하지만 bundle LICENSE
  부재, card/content 수량·version 불일치와 미평가 G2P 때문에 사용
  승인을 열지 않았다. MFA 출력은 transcript/dictionary-conditioned
  predicted alignment이며 expected JSON이나 G04-pending observed
  TextGrid 정답을 대체하지 않는다. 설치·archive·audio·alignment·DTW는
  모두 0이다.
- m3 계산 경계로 두 positive-duration tuple만 받는 순수 DTW
  primitive를 추가했다. Reference/predicted provenance와
  absolute-seconds/log-ratio cost, absolute-index band, step, exact tie
  order, normalization, max-cell, index-path 공개 정책을 caller가 모두
  명시한다. 전용 66개와 nPVI·pitch agreement를 포함한 인접 회귀
  139개가 통과했다. 실제 audio/MFA/CTC/TextGrid를 읽지 않았으므로
  segmentation 품질, corpus m3 지표와 개선 효과는 0이다.
- m8/Phase 4 공통 수학 경계로, 사전 계산된 embedding의 L2 정규화와
  cosine을 다섯 가지 explicit 집계 정책으로 계산하는 순수 primitive를
  추가했다. 신규 21개와 기존 R02/R04를 묶은 회귀가 통과했다. 결과에는
  score/count/dimension/policy/digest만 남기지만 digest도 D1로 취급한다.
  WavLM-SV·ECAPA 모델, 실제 audio/embedding, consent receipt 실행은 0이다.
- WavLM Base+ SV의 exact HF revision, 5-file manifest, 게시 checkpoint
  크기/LFS SHA, X-Vector/weighted-layer/TDNN/statistic-pooling 구조와
  16 kHz 계약을 read-only로 감사했다. Checkpoint는 받거나 load하지
  않았다. 이 specific card의 라이선스 링크는 UniLM MIT가 아니라
  UniSpeech CC BY-SA 3.0 본문을 가리키지만 checkpoint 적용 범위는
  미명시이므로 외부 권리 승인을 요구한다. Speaker embedding은 D1
  생체유래 정보로 취급하고 1:N 식별을 금지하며, style 타당도는 별도
  crossed design 전까지 주장하지 않는다.
- UTMOSv2 공식 code와 model repo를 body 없는 metadata 중심으로 감사해
  exact revision, 5개 checkpoint의 게시 크기/LFS SHA와 code/model-card
  라이선스 표기를 기록했다. 가중치는 받거나 역직렬화하지 않았다. 현재
  code가 별도 Wav2Vec2·timm pretrained artifact와 가변 download 경로를
  요구할 수 있어, transitive manifest·외부 권리 승인·no-network
  construction·safe load가 없으면 R02 local-only attestation을 열지
  않도록 했다. 한국어/L2 타당도와 실제 점수는 0이다.
- E01의 silence와 gain에 대해 mono float PCM만 받는 in-memory primitive를
  추가했다. rounding, dB, clipping, peak와 output budget을 caller가 모두
  명시하고 raw waveform·ID·경로를 metadata에 남기지 않는다. 신규 합성
  테스트 36개와 기존 E01 계약 회귀가 통과했다. duration/F0/phone
  substitution, 실제 변형 audio와 pass-rate는 당시 0이었다.
- E01 잔여 변형군을 다시 감사해 단순 resampling은 제외하고, exact
  Parselmouth 0.4.7의 PitchTier 전역 multiplier와 PSOLA overlap-add만
  쓰는 F0 primitive를 추가했다. PitchTier time grid, 빈 DurationTier,
  16 kHz·sample count, input-domain time, 무 clipping/normalization을
  fail-closed로 강제한다. 전용 58개와 E01 선택 회귀 166개가 통과했고,
  180 Hz×1.2 합성 smoke는 16,000→16,000 samples, 재추출 median ratio
  `1.200237`로 반복 exact했다. 같은 Parselmouth 계열 확인이므로 독립
  agreement가 아니며 spectral/energy/naturalness 불변, 실제 robustness와
  phone 변형은 여전히 0이다.
- Duration 변형은 정렬 구간 `[s,e]` 밖의 scale을 1로 두고,
  `p=(kL-t)/(L-t)`인 plateau를 쓰는 6-point DurationTier로 target 구간
  평균 factor와 전체 연속시간 면적을 exact하게 맞췄다. Praat가 모든
  무성 구간에서 process-global RNG를 사용하므로 overlap-add 직전에
  seed를 설정하고 `finally`에서 safe-unpredictable reset하며, caller가
  dedicated single-job subprocess를 attest해야 한다. 유·무성 혼합 합성
  입력의 fresh process 5회 결과가 동일했고, 반 sample 경계에서는
  continuous `xmax`와 rounded sample count를 분리 검증하도록 독립 감사
  결함도 교정했고 Parselmouth 0.4.7과 embedded Praat 6.1.38을 함께
  검증한다. 전용 92개, Ruff, mypy는 통과했지만 primitive 단계에서는
  runtime isolation self-attestation, exact Praat build receipt, 실제 음성
  collateral metric과 content-addressed worker가 없었다.
  또한 DurationTier control support만 정렬 구간 안에 있고 PSOLA waveform
  변화는 전역일 수 있으며, `k=1` identity-PSOLA matched control이 없어
  의도한 duration 효과와 reconstruction collateral도 아직 분리하지 못한다.
- 이어 합성 입력만 허용하는 E01 duration single-job worker를 별도
  scorer/acoustic process 경계로 구현했다. 검증한 source·Python·wheel·
  Parselmouth extension bytes를 sealed memfd에서 실행하고 Python exec
  전에 libseccomp를 설치해 native socket probe까지 차단한다. 현재
  config `4edf03ff…2683`, application `e2fd0445…7552`, content-addressed
  receipt `9bea2805…0145`는 반복 시 byte-identical reuse됐고 mode
  `0600`, nlink `1`이다. 독립 감사에서 Critical/High 0, 전용 21개와
  E01 인접 **238 tests passed**, Ruff/format/mypy strict 통과를 확인했다.
  다만 전체 OS·NumPy native dependency의 서명된 환경 증명은 아니며
  `paper_ready=false`, `production_ready=false`, 실제 한국어·S1/S2·
  matched control은 모두 미완료다.
- E01 다섯 family의 base/transformed metric 쌍을 invariant,
  monotonic 또는 targeted-change 관계로 비교하는 순수 evaluator를
  추가했다. direction, tolerance/margin, inclusive/strict boundary와
  `equal_per_case` 집계를 호출자가 모두 명시하며 44개 신규 테스트와
  기존 E01 회귀가 통과했다. 결과에는 metric 값·ID·경로를 남기지 않고
  count-derived rate만 계산한다. 0.95 gate, CI, trigger와 실제 case
  실행은 하지 않는다.
- Phone substitution의 actual 실행을 흉내 내지 않고 restricted v2
  정적 경계를 먼저 추가했다. Case sidecar·worker receipt·A1 paired
  aggregate schema는 U→I/U→T/I→T, 사람 관측 경계와 동의·동일
  화자/세션/문맥 lineage, mask 밖 PCM identity, S1과 독립 verifier,
  S201 content-only와 S202 non-phone-gold, preregistration/support/invalid
  policy를 강제한다. Aggregate에는 private receipt-bundle digest 하나와
  count-derived 값만 허용한다. Focused 49개, Ruff/format, strict mypy는
  통과했지만 실제 음성·splice·verifier·metric 접근은 0이고
  `actual_execution=false`, `paper_complete=false`다.
- 이번 재감사에서 추가·보강한 GAN 배선, CoreaSpeech QC, S201,
  eGeMAPS, Parselmouth/torchcrepe, speaker cosine, R03, E01의 선택
  unit/integration 16개 파일을 한 번에 재실행해 **280 tests passed**를
  확인했다. 관련 source 11개에 Ruff check/format과 mypy도 통과했다.
  이 수치는 구현 회귀 통과 수이며 실제 음성 품질·사람 평가 표본이나
  논문 성능 metric 수가 아니다.
- 이어 9축 mask, common-grid pitch agreement, Parselmouth, torchcrepe와
  g2pK의 다섯 unit 파일을 한 번에 독립 재실행해 **251 tests passed**를
  확인했다. 관련 source/export에 Ruff check/format과 mypy도 통과했다.
  이 251개는 위 280개와 겹치는 선택 회귀이며 서로 더하지 않는다.
- legacy split의 g2pK v2 반영 당시 9축·초분절 taxonomy·duration DTW·pitch
  agreement·Parselmouth/torchcrepe·E01 waveform/relation·g2pK
  expected/agreement/runner·phone taxonomy의 선택 unit 13개 파일을
  한 번에 재실행해 **555 tests passed**를 확인했다. 관련 신규
  source/runner/export 10개와 test를 묶은 Ruff check/format, mypy,
  `git diff --check`도 통과했다. 이는 현재 split-currency 결과를
  검증하지 않는 과거 source snapshot이며, 앞의 선택 회귀와도 겹치므로
  실제 corpus 성능·독립 평가·사람 평가 수로 더하지 않는다.
- R03에 source/R04/outcome field를 재귀적으로 거부하고 입력 field를
  exact allowlist로 제한하는 순수 Pareto·diversity 선택기를 추가했다.
  normalization/direction/epsilon/tie/front 부족·초과/distance/seed와
  3–5개 제약은 모두 호출자가 명시하며 bank를 쓰지 않는다. 신규 22개와
  기존 R03 회귀 29개, 총 51개 CPU 테스트가 통과했다. 실제 R01/R02
  후보와 승인 사전등록이 없으므로 selected candidate와 R03 완료값은 0이다.
- S106의 19:10 snapshot은 40 passed/28 failed였지만, 의도한 source와
  self/downstream pin이 함께 갱신된 뒤 28파일 안정 snapshot을 다시
  감사했다. Core S106 98개, S107/S108 downstream 44개와 네 config-only가
  모두 통과했고 전후 manifest SHA
  `b1d2194d82b7ab3338bebc748f8d52a4fd81e7a05d5f8844edd5597ca3fc2646`가
  같았다. 현재 pin 실패는 0이지만 실제 prediction/fusion artifact는 0이다.
- 최신 source 상태에서 manuscript validator를 다시 실행해 artifact
  `36/36`, claim `475/475`, PENDING marker `18`, locked-test guard
  `passed`와 report SHA
  `026799ab9ceb3d93128cc7c4558dcbe8d5ed4f0be3afefe19db198bb5e48b7bd`를
  재확인했다.
- Publication v2는 exact locked key 16개와 digest
  `de011d5deb430036e293b3b0b16593508cc22f200a30cdcb314c4996184d2f11`,
  separator-bounded protected path, descriptor-bound source/output와
  rollback을 강제한다. 독립 재실행에서 v2+v1 99 tests, Ruff, mypy,
  config-only·production 2회·deep validation이 통과했고 generation
  `74b2da59330ab27b4b8fa78f1345a8bc63279d0800bd81c822635f7952fabde6`
  하나를 재사용했다. Raw/environment/secret/locked/network access와
  canonical write는 모두 0이었다. 이는 publication 정적 경계의 통과이며
  completion state는 계속 `paper_complete=false`, `not_submitted`,
  `not_approved`다.

## 13. 논문 완료 판정

다음 조건을 모두 충족해야 “논문에 필요한 모든 작업 완료”로 판정한다.

1. P2의 phone/word/utterance/calibration 지표가 독립 gold 또는
   사전등록 locked 평가에서 보고된다. 목표 미달도 정직하게 결과로
   보존하되 미실행 값은 없어야 한다.
2. S2와 metamorphic 실제 report가 있다.
3. reference 5조건의 자동·blind 평가가 있다.
4. 코칭 200건 전문가 감사가 있다.
5. 사람 대상 결론을 유지한다면 공식 기관 판단, 동의, 모집,
   pre/post/retention과 H04가 있다. 이를 수행하지 못하면 논문의 범위를
   기술·개발 연구로 명시적으로 축소해야 한다.
6. GRPO/GAN 기여를 결과 주장으로 유지한다면 실제 StyleTTS2 또는
   F5R-TTS 정본 실험과 reward-hacking 감사가 있다. 아니면 향후 과제로
   낮춘다.
7. 모든 결과가 evidence map, 표·그림, 실패 사례, 라이선스 부록과
   최종 PDF에 일치한다.
