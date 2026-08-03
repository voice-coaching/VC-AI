# 연구 데이터 라이선스와 역할 매트릭스

기준 시각: 2026-07-29 KST

이 문서는 논문 실험에 필요한 데이터가 어떤 모듈과 평가에 사용되는지,
어떤 산출물을 외부에 배포할 수 없는지 고정한다. 세부 checksum과 revision은
`data/manifests/`와 `configs/data/native_korean_corpora.psd1`을 기준으로
한다.

| 데이터 | 현 로컬 상태 | 라이선스/접근 | 연구 역할 | 금지·주의 |
|---|---|---|---|---|
| AI Hub 71490 발음 | 322,380 WAV/JSON/TextGrid, 344.208시간 | 승인 기반 제한 데이터 | 중국어·일본어 L1 S1 phone/utterance 감독학습, calibration, locked test | 원본·파생 annotation·경로 목록 재배포 금지, raw ID 공개 금지 |
| FLEURS ko-KR | 2,915 WAV+TSV | CC-BY-4.0 | 원어민 ASR·alignment 외부평가 | 출처 표시, L2 오류 감독학습으로 사용 금지 |
| ZEROTH-Korean | 22,720 FLAC+전사 | CC-BY-4.0 | 원어민 ASR·alignment 기준선 | 출처 표시, L2 오류 감독학습으로 사용 금지 |
| KSS | 12,854 embedded audio | CC-BY-NC-SA-4.0 | 연구용 TTS/원어민 기준음 후보 | 비상업 경로 격리, 상업 artifact에 혼합 금지, 동일조건 공유 확인 |
| Seoul Corpus | 240 FLAC/TextGrid | CC-BY-NC-2.0 | phone alignment·자발화 운율 외부평가 | 비상업 연구만, 원본 재배포 금지 |
| 전문가 재평가 | 미수집 | 별도 동의·기관 정책 | G0, calibration, 독립 gold/S2 holdout | 비식별, 평가자 분리, 보존·폐기 계획 필요 |
| 원어민 reference 녹음 | 미수집 | 별도 녹음·사용 동의 | 20문장 multi-reference와 TTS 편향 감사 | 음성·화자 정보 접근 통제, 용도·기간 명시 |
| 학습자 파일럿/RCT | 미수집 | IRB/PIPA·명시적 동의 | C0↔C3, C3↔C5 학습 효과 | 모집 전 승인, 철회권, 미성년자 정책, 4주 유지 데이터 보호 |

## 실험 경계

- 1차 모델의 일반화 주장은 AI Hub 71490이 포괄하는 중국어·일본어 L1에
  제한한다.
- 영어·기타 아시아어·유럽어 L1 일반화가 필요할 때만 AI Hub
  71469/71479/71489를 별도 승인·다운로드·G0 후 확장한다.
- 원어민 코퍼스는 평가·정렬·reference/TTS 역할이며 학습자 오류 label을
  대체하지 않는다.
- KSS와 Seoul Corpus에서 파생된 모델·feature도 비상업 artifact root에
  분리하고 최종 공개 모델의 lineage에서 제외한다.
- Git에는 코드, 집계 수치, URL, revision, checksum만 저장한다. 음성,
  전사, TextGrid, 개인정보, 제한 경로 manifest, 체크포인트는 저장하지
  않는다.
