# 논문 draft PDF 생성·검증 실행 계획

최초 작성: 2026-07-29 KST  
상태: **production backend actual E2E 통과 / 독립 감사 진행 중 /
canonical config·정본 draft PDF 발행 보류**

## 1. 목적과 완료 경계

`docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md`와 검증된 표·그림을 A4 PDF로
만들고, 입력 hash·도구 버전·PDF 구조·내장 글꼴·추출 텍스트·페이지별
렌더링 결과를 한 receipt에 묶는다.

현재 원고에는 외부 전문가 평가, locked-test, S104–S108, S2, TTS blind
평가와 학습 효과에 관한 `PENDING`이 남아 있다. 따라서 지금 생성하는
PDF는 어디까지나 **draft**다. 다음 조건을 모두 충족하기 전에는
`final`, `camera-ready`, `P05 완료`로 표시하지 않는다.

1. 원고의 모든 실측 수치가 evidence-map 검증을 통과한다.
2. 해제 근거가 없는 `PENDING`은 그대로 보존한다.
3. publication lint와 semantic-currency audit가 현재 원고 revision에서
   다시 통과한다.
4. PDF postflight와 사람의 전 페이지 시각 감사를 모두 통과한다.
5. 최종 PDF hash가 tracker와 publication receipt에 결박된다.

## 2. 2026-07-29 실제 feasibility probe

현재 고정 scorer 환경에서 다음 로컬 경로를 실제 실행했다.

- Markdown parser: `markdown-it-py 4.2.0`
- 문서 변환: `LibreOffice 24.2.7.2`
- PDF 구조·텍스트·글꼴 검사: Poppler `24.02.0`
- PDF 문법 검증: Ghostscript `10.02.1`의 `nullpage` device
- 한국어 글꼴: `Noto Sans CJK KR`, `Noto Sans Mono CJK KR`

원고를 HTML로 렌더링하고 LibreOffice로 변환한 결과 A4 PDF 19쪽이
생성됐고, PDF 1.7, 암호화 없음, JavaScript 없음, 사용 글꼴 내장을
확인했다. 초기 HTML 표는 행 간격이 과도했지만 `table/tr/td`의
line-height·padding·page-break 계약과 명시적 table width를 적용해
정상화했다.

SVG는 PDF 안에 vector로 들어가지만 CSS의 `width:100%`만으로는
LibreOffice에서 지나치게 작게 배치됐다. 따라서 production builder는
그림마다 HTML `width`·`height` 속성을 명시하고 실제 PDF 페이지를
PNG로 다시 렌더링해 크기를 검사해야 한다.

초기 feasibility probe에서는 Ghostscript `pdfwrite`와
`OmitInfoDate`·`OmitID`를 사용해 byte-identical PDF를 얻었다. 그러나
후속 원고 전체 probe에서 이 rewrite가 한국어 ToUnicode mapping과
제목 추출을 손상시키는 것이 확인되어 production 경로에서는
폐기했다.

현재 production 구현은 LibreOffice 원본 PDF의 classic xref와 Info
allowlist를 먼저 검증한 뒤, byte 길이와 xref offset을 바꾸지 않고
`CreationDate`, 선택적 `ModDate`, trailer `ID`, `DocChecksum`의
고정폭 token만 공백으로 치환한다. 실제 서로 다른 LibreOffice 원본
두 개를 이 방식으로 처리했을 때 canonical bytes가 exact 일치했고,
한국어 텍스트 추출과 관측 글꼴 8/8의 embedded·Unicode mapping이
모두 보존됐다. Ghostscript는 rewrite에 관여하지 않고 두 canonical
PDF 각각을 `-sDEVICE=nullpage`로 읽어 문법을 검증한다.

2026-07-29의 pre-publication actual E2E에서는 현재 canonical source와
실제 local tool을 production backend에 넣어 두 번 독립 변환했다.
Lossless canonical PDF는 두 build에서 byte-identical이었고 SHA-256
`f0afc4e5c4e03a5d42f9c223796613aa9015eb776fd3265ab64f747eb402ba82`,
748,173 bytes, A4 18쪽이었다. 관측 글꼴은 8/8 embedded·Unicode
mapping, `PENDING` 18개·참고문헌 11개·표 header 13개가 통과했고,
cohort figure palette/bbox는 7쪽에서 확인됐다. Postflight subprocess
10단계와 build 전체 21단계는 combined file+network trace에서
금지 address family를 관측하지 않았다. 이 probe는 실제 backend
실행 가능성 증거지만 canonical config에서 실행·발행한 정본 generation은
아니다. 독립 감사와 정본 publish가 끝나기 전에는 completion으로
승격하지 않는다.

## 3. 하위 작업·완료 기준

| ID | 하위 작업 | 현재 상태 | 완료 기준 |
|---|---|---|---|
| PDF00 | 로컬 도구·글꼴 실측 | 완료(비정본 probe) | parser/LibreOffice/Poppler/Ghostscript 버전과 한국어 글꼴 확인 |
| PDF01 | config-only 계약 | 구현 완료·감사 대기 | canonical config·입력·출력 namespace 고정, source/tool/output 접근 0 |
| PDF02 | Markdown→sanitized HTML | 구현 완료·감사 대기 | raw HTML·script·외부 resource·local absolute URI·secret 0, 표 13개·인용·PENDING 보존 |
| PDF03 | 검증된 SVG 편입 | 구현 완료·감사 대기 | hardened cohort SVG receipt deep 통과, 명시적 680×425 배치, source hash 결박 |
| PDF04 | 격리 LibreOffice 변환 | actual E2E 통과·감사 대기 | 별도 user profile, network syscall trace, A4 PDF 생성, timeout/failure cleanup |
| PDF05 | lossless 결정화·GS 검증 | actual E2E 통과·감사 대기 | 독립 두 build의 canonical PDF SHA-256 exact 일치, 고정폭 volatile token만 제거, GS nullpage 통과 |
| PDF06 | machine postflight | actual E2E 통과·감사 대기 | A4, 암호화/JS 0, 모든 글꼴 embedded+Unicode, 제목·인용·18개 marker 의미 보존, blank page 0 |
| PDF07 | 시각 감사용 page raster | 기계 구현 완료·사람 검토 대기 | 전 페이지 PNG 생성, 크기·빈 파일·cohort palette/bbox 검사, 표 잘림·그림 크기·한글 대체 글꼴 사람 검토 |
| PDF08 | content-addressed publish | 구현·fault test 완료·독립 감사/정본 발행 대기 | single-writer lock, exact tree/mode/nlink, commit rollback, deep validator와 fault tests |
| PDF09 | 최종 P05 승격 | 외부·실험 의존 | 모든 실제 결과 반영, 최종 원고 revision 감사, 사람 시각 승인과 final hash 결박 |

Production-static 준비율과 실제 연구 완료율을 구분한다. PDF renderer를
완성하더라도 실제 실험·사람 평가가 남아 있으면 PDF09와 P05는 0이다.

## 4. 보안·재현성 계약

- `.env*`, API key, raw/restricted/locked row-level 자료는 입력으로
  선언하거나 읽지 않는다.
- 외부 HTTPS 링크는 PDF hyperlink text로만 허용하고 build 중 network
  request를 하지 않는다.
- 현재 host는 unprivileged network namespace 생성이 허용되지 않았다
  (`unshare --net`, `bwrap --unshare-net` 모두 실패). 따라서 production
  builder는 격리가 됐다고 오기하지 않고, 변환 subprocess를 `strace`의
  network syscall 추적 아래 실행해 `AF_INET/AF_INET6` socket·connect가
  하나라도 관측되면 실패한다. 이 관측은 방화벽을 대체하지 않으며
  receipt에는 `kernel_network_namespace=false`로 그대로 기록한다. 실제
  원고 변환 probe에서는 network syscall 60행이 모두 LibreOffice IPC와
  `nscd`용 `AF_UNIX`였고 `AF_INET/AF_INET6`는 0이었다.
- 상대 로컬 문서 링크는 절대 `file://` annotation으로 내보내지 않는다.
- SVG에는 script, `foreignObject`, 외부 reference, JavaScript URI를
  허용하지 않는다.
- 변환 전후 manuscript/evidence/publication/SVG 입력을 stable FD와
  pathname identity로 다시 검증한다.
- PDF, 추출 text, postflight report와 receipt만 aggregate publication
  영역에 둔다.
- 동시 writer, 2단계 write fault, tool timeout, postflight 실패 시 이전
  valid commit을 복구하거나 새 commit을 제거하고 parent directory를
  fsync한다.

### 4.1 고정 namespace와 산출물 tree

Production 구현은 아래 경로만 정본으로 허용한다. Config-only는 이
문자열·schema·정책만 검사하고 참조 source, 실행 도구, 출력 경로를
`stat/open/create`하지 않는다.

- config: `configs/paper/draft_pdf_v1.yaml`
- output root: `artifacts/reproducibility/paper_draft_pdf`
- commit: `artifacts/reproducibility/paper_draft_pdf/commit.json`
- generation:
  `artifacts/reproducibility/paper_draft_pdf/generations/<generation_sha256>/`
- generation 내부: `draft.pdf`, `extracted-layout.txt`,
  `extracted-semantic.txt`, `extracted-table.txt`, `postflight.json`,
  `receipt.json`, `pages/page-001.png` 형식의 전 페이지 raster

Output root와 `generations`는 mode `0750`, single-writer lock은
`0600`, sealed generation과 `pages`는 `0550`, commit과 모든 정본
파일은 `0440`, regular file의 hard-link count는 정확히 1로 고정한다.
Generation ID는 입력 pin만의 hash가 아니라 정규화 PDF와 모든 부속
산출물 manifest를 묶은 canonical receipt의 SHA-256으로 만든다.

### 4.2 builder와 deep validator의 분리

Builder는 같은 snapshot으로 LibreOffice 변환을 두 번 독립 실행하고,
각 원본에서 허용된 고정폭 volatile token만 lossless blanking한다.
두 canonical PDF byte가 동일하고 두 파일 모두 Ghostscript nullpage
검증을 통과할 때만 publish한다. Deep validator는 LibreOffice를 다시
실행하지 않고 다음을 재계산한다.

1. config·manuscript·evidence/publication/semantic commit·SVG와 SVG
   receipt·module·CLI의 고정 hash와 stable path identity
2. commit binding, generation tree의 exact entry·mode·nlink와 각 파일
   hash
3. Poppler로 추출한 PDF version/page size/page count/encryption/metadata,
   `pdffonts`의 전 글꼴 embedded·Unicode mapping 여부와 세 가지
   `pdftotext` 결과
4. default 추출의 제목·참고문헌 11개·`PENDING` 18개 exact Counter,
   `-raw` 추출의 Markdown 표 header 13개 source 순서와 합법적인
   연속-page `thead` 반복, `-layout` 추출의 페이지별 text 존재
5. 전 페이지 PNG의 연속 번호, PNG header·pixel 크기·byte 수와
   blank-page heuristic. `blank_white_ratio=0.9999`에서 raster와 추출
   text가 모두 비어야 blank로 판정하고, 거의 흰 raster인데 text만
   존재하면 invisible-text 위험으로 실패하며, nonblank raster의
   image-only page는 허용한다.

사람 시각 감사 결과는 별도 review receipt로 받는다. 기계 검증 성공을
표 잘림·그림 판독성·한글 글꼴의 사람 승인으로 바꾸지 않으며,
review receipt가 없으면 `visual_review_status=pending`과
`final_claim=false`를 유지한다.

### 4.3 필수 실패·공격 회귀

구현 완료 전 최소 다음 회귀를 synthetic workspace에서 통과시킨다.

- config-only source/output/tool 접근 0, duplicate YAML key·NaN·unknown
  field·비정본 namespace 거부
- config/source/module/CLI의 symlink, hardlink, inode 교체, 읽기 중 변경과
  검증 뒤 path rebind 거부
- output 또는 receipt가 source/config와 같은 path·inode인 alias 거부
- SVG의 `script`, `foreignObject`, external reference, JavaScript URI,
  malformed XML과 XML 1.0 비허용 문자를 거부
- Markdown raw HTML, image 외 resource, absolute/local/file URI를
  실행 가능한 resource로 내보내지 않음
- 동시 두 writer가 서로 다른 PDF/receipt를 교차 결박하지 못함
- HTML/LibreOffice/Ghostscript/Poppler/raster/두 번째 파일 write/commit
  교체/post-deep 단계별 fault와 timeout에서 partial commit 0
- 이전 valid commit이 있으면 byte-for-byte 복구하고, 없으면 새 commit을
  제거한 뒤 directory fsync
- commit·generation·page 누락/추가, mode 완화, hardlink, symlink,
  PDF/text/postflight/receipt/PNG 1-byte 변조를 deep validation이 거부
- 독립 두 production build의 normalized PDF SHA-256 exact 일치

## 5. 다음 실행 순서

1. cohort SVG renderer의 alias·race·rollback·G0 lineage 결함을 보강한다.
2. P04/P05 semantic publisher와 publication audit의 hardened commit을
   독립 검증한다.
3. SVG와 license ledger를 원고/evidence-map에 review 후 결박하고 모든
   pin을 갱신한다.
4. PDF01–PDF08 builder·validator·fault test를 구현한다.
5. draft PDF를 발행해 전 페이지 시각 감사를 수행한다.
6. S104 이후 결과와 외부 평가가 실제로 완료될 때마다 원고를 갱신하고
   같은 pipeline을 재실행한다.
