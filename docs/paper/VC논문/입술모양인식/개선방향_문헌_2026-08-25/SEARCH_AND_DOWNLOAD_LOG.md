# 개선 방향 문헌 탐색·다운로드 기록

- 조사일: 2026-08-25 (Asia/Seoul)
- 목적: 고정 프롬프트 발화에서 `전문가 기준 동작 → 사용자 영상/음성 → 차이 검출 → 이해 가능한 피드백`의 구현 근거와 한계를 검증
- 신규 저장 원문: PDF 45건
- 기존 폴더에서 재사용한 핵심 원문: OLKAVS PDF 1건 (`../2301.06375v2.pdf`)
- 범위: 음성 MDD/발음평가, 다중시점 VSR/AVSR, 단안 3D·4D 얼굴 복원, 음향-조음 역추정(AAI), RT-MRI·EMA·UTI, 시각·조음 피드백, VocalTractLab 파라미터 주장 감사

## 1. 검색 경로

다음의 원 출판·저자 저장소를 우선했다.

1. ACL Anthology, ISCA Archive, CVF Open Access
2. arXiv, HAL, 기관 리포지터리(독일국립도서관·튀빙겐대)
3. PubMed Central/Europe PMC, Frontiers, PLOS
4. 저자·프로젝트 공식 페이지와 GitHub(코드·데이터 존재 여부 확인용)
5. DOI/Crossref와 일반 웹 검색(후보 발견 후 반드시 1차 출처로 재확인)

대표 검색식은 다음과 같다.

- `mispronunciation detection diagnosis articulatory features L2 ASR overcorrection`
- `fixed prompt pronunciation assessment phone false acceptance surface faithful ASR`
- `multi-view lipreading 45 degree profile OuluVS2 Korean OLKAVS`
- `audio visual speech recognition Korean CER WER multi view`
- `monocular 3D face reconstruction speech lip FLAME SPECTRE AVFace`
- `speech driven 3D facial animation VOCA FaceFormer CodeTalker EMOTE`
- `acoustic to articulatory inversion EMA RT-MRI cross speaker cross language`
- `visual articulatory feedback L2 ultrasound EMA MRI pronunciation training`
- `VocalTractLab 23 parameters 30 control parameters aerodynamic`

## 2. 포함·제외 기준

포함 기준:

- 2016–2026년 연구를 우선하되, 23/30 파라미터와 조음 피드백의 출발점을 검증하기 위한 2006–2015년 기초 연구는 예외 포함
- 본 개선안의 어느 한 단계에 직접 필요한 데이터·모델·평가·사용자 피드백 근거가 있음
- 논문 본문과 메타데이터를 검증할 수 있는 합법적 공개 원문
- 동일 과제에서 이미 보유한 OLKAVS는 중복 저장하지 않고 기존 파일을 재사용

제외 기준:

- 유료벽만 있고 저자 공개본을 확인하지 못한 원문
- PDF 주소에 HTML 차단/랜딩 페이지만 반환된 파일
- 뇌 혈류 신호를 다루는 fMRI 연구: 본 과제의 구강 동작 영상화에는 RT-MRI가 해당함
- 순수 표정·제스처 생성처럼 발음 오류 검출/피드백과 연결 근거가 전혀 없는 연구
- 논문 중복본, 블로그·보도자료·2차 요약만 있는 항목

## 3. 다운로드 무결성 검사

- 모든 45개 파일에 대해 `pdfinfo` 성공 여부와 `pdftotext` 첫 페이지 파싱을 검사했다.
- 최종 결과: 45/45 성공, HTML/손상 PDF 0건.
- Springer 논문은 출판사 차단 페이지 대신 독일국립도서관의 공개 저자본을 사용했다.
- 원문 PDF를 확보하지 못한 아래 자료는 `.pdf`로 위장해 남기지 않았고, 보고서에서 메타데이터 근거로만 사용했다.

| 자료 | 처리 | 사유 |
|---|---|---|
| Bliss et al. (2018), visual articulation feedback review | 미수록 | PMC 본문은 열람 가능하나 OA PDF 패키지 확인 실패 |
| Suemitsu et al. (2015), real-time articulatory target feedback | 미수록 | 공개 HTML은 확인했으나 합법적 PDF 직접본 미확인 |
| Yiu et al. (2026), ultrasound biofeedback meta-analysis | 미수록 | CC BY XML은 확인했으나 당일 PMC PDF 패키지 URL이 404 |
| Kocjančič et al. (2024), classroom L2 vowel remediation | 미수록 | CC BY PMC 본문은 확인했으나 PDF 직접 다운로드가 403/404 |
| smartphone audible/inaudible pronunciation study (2022) | 미수록 | 초록·DOI만 확인, 공개 원문 라이선스 미확인 |

## 4. 공개성·라이선스 주의

`공개 접근 가능`과 `상업 제품에 재배포 가능`은 동일하지 않다. ISCA·ACL·CVF·arXiv의 공개 연구 사본은 로컬 연구 검토용으로 저장했지만, 모델 코드·학습 데이터·FLAME/VOCASET 계열 자산은 별도 약관이 적용될 수 있다. 특히 얼굴 메시·음성·다각도 영상은 생체정보이고, 연구 데이터의 공개 여부가 서비스 내 2차 이용 권한을 자동으로 주지 않는다. 제품화 전에는 각 코드·가중치·데이터·3D 베이스 모델의 라이선스를 항목별로 다시 검토해야 한다.

## 5. 산출물

- `PAPER_EVIDENCE_MATRIX.csv`: 논문별 데이터·언어·화자·장비/시점·라벨·입출력·지표·MDD 민감도·코드/데이터·라이선스·한계
- `FEASIBILITY_AND_PRACTICALITY_REVIEW_KO.md`: 주장 감사, 실현성/실용성 판정, 위험요인, 최소 검증 설계
- 이 문서: 검색전략, 포함/제외, 다운로드 감사

