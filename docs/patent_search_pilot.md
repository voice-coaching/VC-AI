# 논문 → 특허 검색 파일럿 실행 안내

작성일: 2026-09-14. 독립 모듈: `src/patent_search`. 음성 분석 서버와 별도 실행한다.

## 구현 범위

실제 데이터 수집, 두 임베딩 모델의 색인·비교, 정확 코사인 검색, BM25, RRF와 80+10+10 후보 보존, 선택적 cross-encoder, 인증된 로컬 작업 API를 구현했다. API는 단일 사용자 연구용이며 원문 문단을 **관련성이 입증된 설명**으로 바꾸지 않는다. 결과의 `PASSAGES_ONLY_UNVERIFIED`, `NOT_EVALUATED`는 실제 미검증 상태다.

| 기능 | 파일럿 동작·한계 |
|---|---|
| 데이터 | HUPD 공식 샘플의 revision·387,636,489 bytes·SHA-256 고정, archive 스크립트 실행·디렉터리 추출 없이 JSON 읽기 |
| 표본 | 공개번호·날짜 정규화 후 application ID SHA-256 순으로 512건 선택. 논문 검색어를 수집 조건으로 사용하지 않음 |
| 모델 | BGE-M3 / multilingual-e5-large의 별도 모델 revision·prefix·400토큰 문단·40토큰 overlap |
| 검색 | 같은 corpus의 BM25와 전체 허용 집합 exact cosine. OpenSearch/ANN 배포는 미구현 |
| 청구항 | 원문 수집, 선택적 문단 색인 지원. 기본 실행은 초록 기반. 항 의존 관계의 구조화·조합 판정은 미구현 |
| 근거 | 공보·field·문자 위치와 실제 문단, 원래 순위·점수 반환. 생성 설명·기술 관계 자동 확정은 미구현 |
| 논문 | 제목·초록/텍스트 등록, PDF 본문 20MiB·50쪽·20초 subprocess 추출. 원본은 보관하지 않고 digest·페이지/텍스트 위치 보존 |
| 기술 요소 | 원문 문장 기반 초기 항목 → 사용자 수정·확인. LLM 기술 구조 추출을 구현했다고 표시하지 않음 |
| 작업 | SQLite 영속 상태·멱등 키·버전 고정·단일 worker·대기 포함 180초. 재기동 시 미완료 작업을 FAILED로 표시 |
| 취소·삭제 | 후속 계산 및 결과 저장 차단. 이미 진행 중인 모델 호출의 즉시 강제 중단은 미지원. 반환 뒤 폐기 |
| 인증 | loopback 127.0.0.1 전용, 독립 Bearer 토큰 constant-time 검증. 다중 사용자 로그인·공개 서비스는 미구현 |
| 저장소 | `data/cache/patent-pilot`은 재생성 가능한 임시 작업 사본. B2 영구 저장은 credential 미설정으로 미연결 |

HUPD 카드의 라이선스 metadata는 CC-BY-SA-4.0이지만 본문 Licensing Information은 CC-BY-NC-SA-4.0이다. 이 차이가 해소되기 전에는 표본·파생 색인을 **연구용으로 제한**하며 상용 한국 특허 데이터로 사용하지 않는다. 원 제공자: Suzgun, Melas-Kyriazi, Sarkar, Kominers, Shieber, *The Harvard USPTO Patent Dataset* (2022). [공식 데이터 카드](https://huggingface.co/datasets/HUPD/hupd), [원 논문](https://arxiv.org/abs/2207.04043)

## 환경 준비

실행 경로는 프로젝트 루트다. 기존 음성 worker 환경에 패키지를 설치하지 않는다. 실제 실행 환경은 Windows Python 3.14이며 root `pyproject.toml`의 음성 프로젝트 설치 명령을 사용하지 않는다.

```powershell
python -m venv .venv-patent
.\.venv-patent\Scripts\python.exe -m pip install -r requirements/patent-search.txt
$env:PYTHONPATH = 'src'
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
```

GPU는 호환되는 NVIDIA driver가 있을 때 별도 환경에서 사용한다. 이번 작업의 CUDA wheel 공급원은 PyTorch 공식 cu126 index다.

```powershell
python -m venv .venv-patent-gpu
.\.venv-patent-gpu\Scripts\python.exe -m pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126 --no-deps
.\.venv-patent-gpu\Scripts\python.exe -m pip install -r requirements/patent-search.txt
```

아래 CPU Python 경로를 GPU 환경 Python으로 바꾸고 `index`·`compare`·`search`·`serve`에 `--device cuda`를 추가할 수 있다. 모델은 pinned revision으로 받으며 remote code를 실행하지 않는다. safetensors가 없는 pinned 모델은 PyTorch `weights_only=True`로 읽는다.

## 수집·색인·비교

```powershell
.\.venv-patent\Scripts\python.exe -m patent_search collect --limit 512
.\.venv-patent\Scripts\python.exe -m patent_search collect-papers
.\.venv-patent\Scripts\python.exe -m patent_search compare
```

`collect-papers`는 arXiv의 LipNet `1611.01599v2`와 Lip Reading Sentences in the Wild `1611.05358v2`의 공개 초록을 수집한다. 사용자 미공개 원고를 외부에 전송하지 않는다. Windows 기본 Python 인증서 체인에서 실패하는 arXiv 연결은 certifi CA bundle로 검증하며 TLS 검증을 끄지 않는다.

`compare`는 같은 표본에서 모델별 BM25·dense·hybrid 결과와 처리 시간을 실제 계산한다. 정답 라벨 없이 Precision·Recall·nDCG를 만들지 않는다. `denseOnlyCandidateCount`는 추가 관련 특허 수가 아닌 **검색 목록 차이**다. CPU/GPU 또는 모델의 색인 생성 장치가 다른 경우 buildSeconds를 모델 간 우열 근거로 비교하지 않는다.

생성 파일은 Git에서 제외된 다음 작업 디렉터리에 둔다.

```text
data/cache/patent-pilot/
  sample-jan-2016.tar.gz        # 출처 SHA-256 검증한 원본 캐시
  corpus.jsonl                 # 정규화한 선택 표본
  corpus-manifest.json         # 범위·출처·선택 규칙·hash·보관 상태
  public-papers.json           # 공개 논문 입력, 원문 대신 초록
  indexes/<indexRevision>/    # chunks.json, vectors.npy, manifest.json
  model-cache/                # pinned 모델 다운로드 캐시
  comparison.json            # 실제 실행 결과, 미판정 표시
  papers.sqlite3             # 로컬 API 사용자 입력·작업 상태
```

특허 원본·모델 캐시·사용자 논문·토큰 파일을 Git 또는 일반 배포 archive에 넣지 않는다. 임시 캐시를 영구 보관하려면 B2 연결·해시 확인과 보존 정책을 먼저 완료한다. 현재 캐시의 자동 만료·B2 업로드는 미구현이다.

## 로컬 API 실행

`PATENT_API_TOKEN`을 현재 프로세스에 주입한다. 음성 분석 또는 RunPod 관리 토큰을 재사용하지 않는다. 값은 출력하지 않는다. 이번 실행에서 생성한 토큰 파일은 아래 로컬 캐시에만 있으며 다른 clone에는 없다. 다른 환경에서는 별도 무작위 토큰을 비공개로 주입한다. 아래 index revision은 `comparison.json`의 해당 모델 `index.indexRevision`에서 읽는다.

```powershell
$env:PATENT_API_TOKEN = (Get-Content -Raw -Encoding UTF8 data/cache/patent-pilot/server-token.txt).Trim()
$patentReport = Get-Content -Raw -Encoding UTF8 data/cache/patent-pilot/comparison.json | ConvertFrom-Json
$patentIndex = $patentReport.models.'bge-m3'.index.indexRevision
.\.venv-patent\Scripts\python.exe -m patent_search serve --model bge-m3 --index-revision $patentIndex --port 8091
```

토큰이 없으면 기동을 거절한다. `GET /health`·`/readyz` 이외 경로는 Bearer 인증이 필요하다. 이 서버의 ready는 연구용 색인 로드를 뜻하며 한국 특허·근거 설명·상용 배포 준비를 뜻하지 않는다.

| 메서드·경로 | 용도 |
|---|---|
| `GET /api/patent-search-capabilities` | 실제 corpus·색인 범위, 지원·미지원 기능 |
| `POST /api/papers` | JSON `title`, `text`. 추출 문장과 응답의 `paperVersion` 확인 |
| `GET /api/papers/{id}` | 등록 본문·버전·기술 요소와 원문 위치 확인 |
| `POST /api/papers/pdf` | `Content-Type: application/pdf` 원본 bytes. PDF 임시 추출 |
| `PATCH /api/papers/{id}/features` | `paperVersion`, `features` 최대 8개. 수정 시 버전 증가·확인 취소 |
| `POST /api/papers/{id}/features/confirm` | 현재 `paperVersion` 확인 |
| `POST /api/patent-search-jobs` | `Idempotency-Key`, body 아래 예시. 비동기 202 |
| `GET /api/patent-search-jobs/{id}` | 작업·결과·실패 상태 |
| `GET /api/patent-search-jobs/{id}/results` | 고정된 최대 20공보. 파일럿은 pagination 없음 |
| `GET /api/patent-search-jobs/{id}/results/{publicationId}` | 해당 결과의 실제 특허 문단 |
| `GET /api/papers/{id}/patent-search-jobs` | 해당 논문의 검색 이력 |
| `POST /api/patent-search-jobs/{id}/cancel` | 취소 후 결과 쓰기 차단 |
| `DELETE /api/papers/{id}` | 논문·관련 작업·결과 삭제 |

```json
{
  "paperId": "등록 응답의 id",
  "paperVersion": 1,
  "countries": ["US"],
  "queryText": "긴 논문이면 검색에 사용할 원문 초록 또는 선택 구간",
  "mode": "hybrid_semantic",
  "rerank": false
}
```

`queryText`는 선택 사항이며 미지정 시 등록 본문을 쓴다. 긴 입력은 묵시적으로 자르지 않고 입력 축소를 요구한다. 각 기술 요소 최대 1500문자, 논문 text 최대 20만 문자다. `rerank:true`는 추가 cross-encoder를 처음 다운로드·로드하므로 준비 전 콜드 실행은 180초 deadline을 넘길 수 있다. 실패 시 PARTIAL 또는 FAILED로 표시한다. 현재 US 표본에 KR을 요청하면 `COUNTRY_NOT_INDEXED`로 거절한다.

## 다음 구현과 검증

한국 서비스에는 승인된 KIPRIS 상품·자격 또는 허용된 한국 특허 데이터, 실제 corpus 어댑터, B2 영구 보관, 한국어 분석기와 한국어 논문–특허 수동 판정이 필요하다. 대규모 ANN/OpenSearch·증분 색인 전환, 청구항 의존 관계 파싱, 요소별 근거 확인·설명 생성, 다중 사용자 인증·프런트엔드는 별도 미완료 항목이다.

사용자가 요청한 실제 수집·모델 비교와 필요한 구문·import·로컬 API 동작 확인을 수행 범위로 삼는다. 자동 회귀 테스트·브라우저 자동화는 실행하지 않는다. 품질 QA는 개발자의 직접 판정으로 완료하며 실제 실행 수치와 미완료 기능은 수행 기록에 구분한다.
