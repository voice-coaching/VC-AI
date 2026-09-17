# 논문 → 특허 검색 실제 수집·모델 비교·구현 기록

수행일: 2026-09-14 KST. 범위: 독립적인 로컬 연구 파일럿. 사용자가 지정한 분야는 음성·시청각 발음 분석이다.

**실제 특허 표본 수집, 두 모델의 벡터 색인·검색 비교, 로컬 HTTP 작업 흐름을 실행했다. 한국 특허 서비스 전체 구현·배포 또는 검색 품질 검증 완료는 아니다.** 실행 방법과 구현/미구현 구분은 [파일럿 안내](patent_search_pilot.md), 목표 설계는 [의미 검색 v2 계획](paper_to_patent_implementation_plan_20260913.md)에 있다.

## 1. 데이터와 실행 환경

| 항목 | 실제 결과 |
|---|---|
| 공급원 | Harvard USPTO Patent Dataset, `HUPD/hupd` 공식 January 2016 sample |
| 공급원 revision | `f570a84b03663180b6034c1f7f4c15864f94385e` |
| 다운로드 크기 | 387,636,489 bytes |
| 원본 SHA-256 | `c8db7a2e730f04a95946847d39a74b4a9ea53b0f4fd357bb8789abd77684e37f` 일치 확인 |
| 읽은 공보 JSON | 26,808건. 공개번호의 날짜 suffix를 실제 응답에 맞춰 분리 |
| 검색 표본 | application ID 해시 기준 512공보. 검색어로 선정하지 않음 |
| corpus SHA-256 | `b04ac374ffd39ed45c8dc8458549db7c21b7fae2b837fdadaac9d77bb49fe270` |
| 검색 텍스트 | 두 모델 각각 1,032개 제목·초록/초록 문단 벡터, 1,024차원. 청구항은 원문만 수집하고 이번 색인에서는 제외 |
| 범위 | 미국 영문 출원 공보. 2016년 1월 출원 샘플이며 공개일은 별개. 한국·전체 미국 범위가 아님 |
| 논문 입력 | LipNet `1611.01599v2`, Lip Reading Sentences in the Wild `1611.05358v2` 공개 초록 |
| 환경 | Windows Python 3.14.6, 별도 CPU/GPU venv. GPU는 GTX 1660 6GB, torch 2.14.0+cu126 |
| 영구 저장 | B2 credential 미설정으로 미연결. `data/cache/patent-pilot`은 재생성 가능한 로컬 작업 캐시 |

데이터 카드의 라이선스 metadata와 본문이 다르므로 연구용 제한을 적용했다. 상용 배포·한국 서비스 데이터 사용 허용을 받은 것으로 보고하지 않는다. [HUPD 공식 카드](https://huggingface.co/datasets/HUPD/hupd)

비교용 공개 논문은 [LipNet](https://arxiv.org/abs/1611.01599v2), [Lip Reading Sentences in the Wild](https://arxiv.org/abs/1611.05358v2)다. 사용자의 미공개 논문은 제공받거나 외부에 전송하지 않았다. 원문 초록과 source SHA는 로컬 캐시에 보존한다.

## 2. 실제 모델 비교

| 모델 | 고정 revision | 색인 생성 |
|---|---|---|
| BGE-M3 | `5617a9f61b028005a4858fdac845db406aefb181` | CPU 540.875초 |
| multilingual-e5-large | `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` | GPU 59.057초 |

두 색인 생성의 장치가 다르므로 위 시간을 모델 속도의 우열로 해석하지 않는다. 아래 **검색 비교는 두 모델 모두 GPU**에서 수행했다. 다운로드·모델 로드·색인 구축을 제외한 단일 실행 시간이며 두 논문만의 값이다. dense → hybrid 순서의 캐시·초기화 영향이 있으므로 p95나 처리량 보장이 아니다.

| 논문 | 모델 | dense 검색(초) | hybrid 검색(초) | BM25 상위 300 밖의 dense 후보 | dense/BM25 상위 10 교집합 |
|---|---|---:|---:|---:|---:|
| LipNet | BGE-M3 | 0.504 | 0.146 | 37 | 4 |
| LipNet | E5-large | 0.131 | 0.147 | 33 | 2 |
| Lip Reading Sentences in the Wild | BGE-M3 | 0.096 | 0.112 | 36 | 1 |
| Lip Reading Sentences in the Wild | E5-large | 0.099 | 0.115 | 46 | 1 |

**이 수치는 검색 목록 차이다. 추가 관련 특허 수·정확도가 아니다.** 수동 관련성 라벨이 없으므로 Precision@10, Recall, nDCG는 null로 기록했다. 실제 논문–한국 특허의 품질 우열이나 최종 운영 모델을 확정하지 않는다.

후보 보존 단계에서 dense 전용 후보가 최대 100개 재정렬 후보 안에 각각 15·15·17·14개 남았다(위 표 순서). 그러나 **재정렬을 끈 RRF 최종 20개에는 네 경우 모두 dense 전용 후보가 0개였다.** 독립적인 의미 검색 구현만으로 최종 화면에서 추가 발견이 보장되지 않는다는 실제 관측이다. 재정렬·도메인 표본·수동 판정을 거쳐 이 병목을 확인해야 한다.

색인 revision:

```text
BGE-M3: f33734bc5aa02861635b3a1742565fa0fc670729c5093d3951ea37d5fecf8898
E5-large: 565356f12c6e4e0af5aae89530d3a4c0c819f2fad15ddc095695a81759db12c8
```

상세 비교는 로컬 `data/cache/patent-pilot/comparison.json`에 있다. 실제 공보 ID·경로별 후보·순위·점수·원문 문단을 보존하며 원문을 이 공개 문서에 복제하지 않는다.

### Cross-encoder 실제 실행

LipNet의 같은 초록·BGE-M3 후보에 `BAAI/bge-reranker-v2-m3` revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`를 적용했다. CPU에서 후보 100건의 문단 쌍을 재정렬하고 20건을 반환했으며 `rankingStatus=COMPLETED`였다. 다운로드·최초 모델 로드를 포함한 검색 호출은 **258.175초**였다. GPU 임베딩 검색 시간과 직접 비교하거나 정상 상태의 API 지연으로 해석하지 않는다.

재정렬 후 상위 20건에는 **BM25 상위 300 밖의 dense 전용 후보 4건**이 포함됐다. 같은 입력의 재정렬 없는 RRF에서는 0건이었다. 후보 보존과 재정렬이 최종 결과 구성에 영향을 주는 것을 확인했지만, 이 4건이 실제 관련 특허라는 판정은 아직 없다. 모델 점수도 관련성 확률로 표시하지 않는다. 상세 원문·순위는 로컬 `data/cache/patent-pilot/reranked-lipnet.json`에 있다.

이 실행은 CLI에서 수행했다. API의 대기 포함 180초 제한을 넘으므로 현재 CPU 콜드 상태의 `rerank:true`를 운영 가능한 경로로 보고하지 않는다. 모델 사전 로드, 실행 장치·문단 예산 조정과 실제 처리 시간 확인이 필요하며 API 기본값은 `rerank:false`다.

## 3. 로컬 API 실행 근거

실제 BGE-M3 색인을 로드한 `127.0.0.1:8091`에서 공개 LipNet 초록으로 다음을 수행했다.

| 동작 | 응답·결과 |
|---|---|
| health / 인증된 capabilities | 200 / 200, 실제 corpus 512건 |
| 논문 등록 | 201, 원문 문장 기반 검토 항목과 paperVersion 반환 |
| 기술 요소 확인 | 200 |
| 비동기 검색 접수 | 202 |
| 검색 완료 | COMPLETED, 20공보, 4.170초. 원문 문장 8개를 포함한 실제 API 입력이며 모델 비교의 요약 단독 입력과 다름 |
| 무인증 capabilities | 401 |
| 동일 Idempotency-Key·동일 body 재접수 | 202, 동일 jobId 반환 |

여기서 COMPLETED는 파일럿의 후보 검색 완료다. `evidenceStatus=PASSAGES_ONLY_UNVERIFIED`, `explanationStatus=NOT_IMPLEMENTED`로 반환하며 입증된 기술 대응·생성 설명을 제공한 것이 아니다. HTTP 실행 기록은 로컬 `api-execution-receipt.json`에 있다. 서버 토큰은 출력하지 않고 별도 로컬 파일에 두었다.

PDF·취소 경합·강제 재시작 중 복구·다중 사용자·실제 품질 QA를 검증 완료로 표시하지 않는다. 자동 회귀 테스트·브라우저 자동화는 실행하지 않았다.

실행 후 이번 작업이 시작한 임시 API 프로세스는 종료했다. 공개 서버에 배포하거나 상시 서비스를 열지 않았다. Python 소스 7개 구문 확인, 변경 문서의 로컬 링크·코드 블록 확인, 데이터·토큰·venv의 Git 제외 여부를 정적으로 확인했다. 재실행 명령은 파일럿 안내에 있다.

## 4. 다음 단계의 실제 차단 조건

1. **한국 특허 데이터:** 현재 작업 폴더의 `.env`·`.env.local`에서 KIPRIS/Lens 자격을 확인할 수 없었다. 승인 상품과 키의 비공개 파일 경로 또는 허용된 데이터 파일이 필요하다. 미국 샘플을 한국 데이터로 조용히 대체하지 않는다.
2. **영구 보관:** B2 설정이 없어 원본·파생물 업로드·검증은 미수행이다. 현재 데이터·모델은 로컬 작업 캐시이며 영구 보관 완료로 보고하지 않는다.
3. **검색 품질:** 분야별 대표 논문과 관련 특허의 수동 판정이 필요하다. 현재 512건 무작위 표본은 발음 분석 분야의 충분한 관련 문서 포함을 보장하지 않는다.
4. **제품 구현:** OpenSearch/ANN·증분 전환·청구항 의존 관계·근거 판정/설명·다중 사용자 인증·화면은 미완료다. 단일 사용자 파일럿 API를 공개 서비스에 배포하지 않는다.

변경은 `src/patent_search`, 별도 requirements, 실행 안내·수행 기록에 한정한다. 기존 음성 분석 소스·AWS/RunPod·비밀 설정은 변경하지 않았다. commit/push/PR·외부 서버 배포는 수행하지 않았다.
