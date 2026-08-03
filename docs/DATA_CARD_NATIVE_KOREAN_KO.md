# 원어민 한국어 통합 코퍼스 데이터 카드

생성일: 2026-07-29 KST

이 문서는 FLEURS ko-KR, KSS, Seoul Corpus와 ZEROTH-Korean의 공개 가능한
집계 data card다. 원문, 음성, speaker ID, source audio path, raw file
stem과 transcript reference를 포함하지 않는다. 모델 성능 결과가 아니라
데이터 구성·기술 metadata·허용 역할을 기록한다.

## 규모

| source | audio object | 시간 | transcript reference | 라이선스 |
|---|---:|---:|---:|---|
| FLEURS ko-KR | 2,915 | 10.040 h | 2,915 | CC-BY-4.0 |
| KSS | 12,854 | 12.857 h | 12,854 | CC-BY-NC-SA-4.0 |
| Seoul Corpus | 240 | 42.752 h | 240 | CC-BY-NC-2.0 |
| ZEROTH-Korean | 22,720 | 52.871 h | 22,720 | CC-BY-4.0 |
| **합계** | **38,729** | **118.519 h** | **38,729** | 혼합 |

audio object의 시간 범위는 0.768초–750.019초다. 네 source의 record 단위가
동일하지 않으므로 38,729를 단일한 utterance 수로 해석하지 않는다.
FLEURS·KSS·ZEROTH는 주로 발화 단위이고 Seoul Corpus는 240개 장시간
세션 녹음이다.

## 오디오 기술 metadata

| source | format | sample rate | channel |
|---|---|---|---|
| FLEURS ko-KR | WAV 2,915 | 16 kHz 2,915 | mono 2,915 |
| KSS | embedded WAV 12,854 | 44.1 kHz 12,854 | mono 12,853, stereo 1 |
| Seoul Corpus | FLAC 240 | 22.05 kHz 240 | mono 240 |
| ZEROTH-Korean | FLAC 22,720 | 16 kHz 22,720 | mono 22,720 |

libsndfile가 38,729개 object의 sample rate, channel, frame 수와 duration
header를 읽었고 실패는 0건이다. 이 검사는 전체 waveform sample을 다시
인코딩했다는 뜻이 아니다. KSS embedded audio를 포함해 원본 audio는
재작성하거나 materialize하지 않았다. 모델 입력 단계에서는 source
sample rate와 channel을 기록한 뒤 별도 read-only decoder/downmix/
resample 계약을 적용한다.

## 원래 split

| source | split | record |
|---|---|---:|
| FLEURS ko-KR | train / dev / test | 2,307 / 226 / 382 |
| KSS | train | 12,854 |
| Seoul Corpus | unspecified | 240 |
| ZEROTH-Korean | train / test | 22,263 / 457 |

이 split은 source가 제공한 구성을 보존한 값이다. 서로 다른 source의
train/test를 합쳐 단일 모델 평가 split으로 간주하지 않는다. Seoul
Corpus에는 이 manifest 단계에서 임의 split을 만들지 않았다.

## 허용 역할

| source | intended role | 금지·주의 |
|---|---|---|
| FLEURS ko-KR | 원어민 ASR·alignment 외부평가 | 출처 표시, L2 오류 감독 label로 사용 금지 |
| KSS | 비상업 연구용 TTS·원어민 기준선 | 상업 artifact/model lineage 혼합 금지 |
| Seoul Corpus | 비상업 phone alignment·자발화 운율 reference | 원본 재배포 금지, 비상업 연구만 |
| ZEROTH-Korean | 원어민 ASR·alignment 기준선 | 출처 표시, L2 오류 감독 label로 사용 금지 |

KSS와 Seoul Corpus의 `noncommercial_only`는 `true`,
`raw_redistribution_allowed`는 `false`로 고정했다. FLEURS와 ZEROTH의
CC-BY-4.0 역시 출처 표시 의무를 없애지 않는다. 라이선스가 다른 source의
feature나 model을 결합할 때는 가장 제한적인 lineage를 별도 관리한다.

## 통합 manifest와 품질 검사

운영 manifest는 source ID와 canonical locator를 namespaced SHA-256으로
결합한 stable ID를 사용한다.

- record/column: 38,729/18
- stable ID: 38,729개, 중복 0개
- source count 계약: 2,915 / 12,854 / 240 / 22,720, 불일치 0
- audio metadata 유효: 38,729, 실패 0
- transcript reference 존재: 38,729
- 상대경로 절대값: 0
- locator 또는 transcript-reference 계약 위반: 0
- transcript 원문·audio bytes·direct identifier 열: 0

재현 식별자:

- 제한 운영용 manifest SHA-256:
  `2562b52c31de9bd1c85e8d9cc446c9ceaaebe53a3a7f8679834f8a39c251d5a6`
- 현재 공개 집계 report SHA-256:
  `d50e6f788f6e631c37b54631325dfe840c0460ecf342af71f5e38b6f638037b4`

## 개인정보·반출 경계

`native_korean_v1.parquet`에는 실제 audio를 열기 위한 상대경로 또는
KSS Parquet row reference가 들어 있다. Seoul/ZEROTH filename에는 source
고유 식별 구조가 남을 수 있으므로 이 파일은 R1 운영 제한 artifact이며
공개하지 않는다. 직접 식별자를 제거하고 privacy review를 통과했어도
“privacy safe”라고 부르지 않는다. schema metadata는
`privacy_reviewed=true`, `contains_direct_identifiers=false`,
`access_scope=restricted_internal`, `shareable=false`다.

`native_korean_v1.report.json`과 이 data card에는 집계, license/role,
checksum만 포함한다. 전수 검사에서 source path, transcript-reference와
raw file-stem **값**이 0건임을 확인했다. report 안의
`report_contains_audio_locator` 같은 key는 금지 값의 종류를 설명하는
검사 이름이며 실제 locator 값이 아니다. 이 둘은 A1 집계 결과 후보지만
기관 정책과 라이선스 검토 후에만 외부 공유한다.

stable hash와 checksum은 익명화 또는 배포 허가가 아니다. 원본 음성,
전사, TextGrid, KSS embedded bytes와 운영 manifest는 Git에 넣지 않는다.

## 한계

- transcript availability는 reference 존재를 뜻하며 transcript 정확도나
  정규화 품질을 보증하지 않는다.
- audio 검사는 header와 locator 계약 검사다. 전 구간 음질·clipping·SNR
  감사는 downstream feature QC에서 별도로 수행한다.
- KSS는 단일 화자 중심이고 Seoul Corpus는 발화가 아닌 장시간 세션이므로
  단순 record 가중 결합은 source 편향을 만든다.
- 통합 manifest는 별도 speaker ID를 공개하지 않으므로 화자 균형과
  speaker-disjoint 여부는 각 source의 접근 제한 metadata에서 감사한다.
- 네 코퍼스에는 한국어 학습자 오류 gold label이 없으며 AI Hub L2
  감독학습 데이터를 대체하지 않는다.
