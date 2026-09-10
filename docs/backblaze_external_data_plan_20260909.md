# Backblaze 외부 데이터 수집·활용 계획

2026-09-09. 사용자의 저장 위치 지시에 따라 **외부 데이터의 영구 원본과 파생 자료는 Backblaze B2에 저장**한다. RunPod는 실행에 필요한 작은 승인 자산과 작업별 임시 캐시를 사용한다. PC나 RunPod의 영구 볼륨을 새 외부 데이터의 원본 저장소로 사용하지 않는다.

## 환경변수와 실제 접속

- 프로젝트 루트 `.env`에 제공받은 B2 자격증명을 저장했다. Windows 파일 ACL은 현재 사용자 전용이며 `.gitignore`로 `.env`와 `.env.*`를 제외한다. 값이 비어 있는 [`.env.example`](../.env.example)만 공유용이다.
- RunPod 수집 작업용 사본은 `/root/.config/intelligentai/.env`에 있고 디렉터리 `0700`, 파일 `0600`을 확인했다. `/workspace` 공유 볼륨은 지정한 `0600`을 보존하지 않고 `0666`으로 보여 그 위치의 임시 사본을 제거했다. 모델 시작 환경·compose·일반 복구 archive에는 키를 추가하지 않았다.
- RunPod 컨테이너가 재생성되면 `/root` 사본은 없어질 수 있다. 보존된 PC `.env`를 수집 전용 위치에 다시 전달한다. 비밀정보를 B2 데이터 manifest나 일반 배포 archive로 복원하는 방식은 사용하지 않는다.
- PC와 RunPod 양쪽에서 **B2 Native API 인증 및 기존 비공개 버킷 확인**에 성공했다. 버킷은 `speakai2026`, ID는 `e5cc71977ef1880ca60b0c17`이다. 새 버킷은 만들지 않는다.

제공된 키는 Master Application Key다. Backblaze 공식 문서는 Master Key가 S3 호환 API에서 지원되지 않는다고 명시하므로 현재는 **Native API**를 사용한다. 이 키를 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`에 넣지 않는다. S3 도구가 필요한 후속 구현에서는 해당 버킷·prefix에 한정된 별도 application key를 사용할 수 있다. 이번에 새 키를 생성하지는 않았다. [Backblaze 공식 app key 문서](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)

| 환경변수 | 용도 |
|---|---|
| `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY` | 수집 작업의 Native API 인증. 문서·로그에 값 출력 금지 |
| `B2_API_MODE=native`, `B2_AUTH_URL` | 인증 시작점. 실제 API·다운로드 URL은 인증 응답에서 얻음 |
| `B2_BUCKET_NAME`, `B2_BUCKET_ID` | 기존 비공개 버킷 고정 |
| `B2_EXTERNAL_PREFIX=intelligentai/external` | 신규 외부 원본·출처 manifest |
| `B2_DERIVED_PREFIX=intelligentai/derived` | 추출 텍스트·특징·라벨·검색 index |
| `B2_COACHING_RELEASE_PREFIX=intelligentai/releases/coaching` | 승인된 발음 교육 카탈로그 배포 묶음 |
| `RUNPOD_EXTERNAL_CACHE_DIR=/tmp/intelligentai-external-data` | 작업별 임시 캐시 루트 제안 |
| `RUNPOD_EXTERNAL_CACHE_MAX_BYTES=4294967296` | 전체 임시 캐시 4GiB 한도 제안 |

`.env`는 파일을 만들었다고 모든 프로세스에 자동 적용되는 것이 아니다. 수집 전용 실행기가 명시적으로 읽도록 연결해야 한다. prefix·캐시 변수도 현재는 **후속 수집기 구현을 위한 설정값**이며 기존 downloader에서 자동 강제되지 않는다. 기존 backend의 `AI_ANALYSIS_OBJECT_STORAGE_*` 및 AWS credential chain은 사용자 녹음 저장소용으로 유지한다.

## 먼저 확인한 B2 보유 자료

신규 교원용 PDF 저장 전, `intelligentai/` prefix의 현재 객체 110개, **348,124,902,992 bytes**를 Native API로 조회했다. 이전 버전·숨김 파일·미완료 part를 포함한 청구 용량 조사는 아니다.

| 현재 prefix | 객체 수 | bytes | 활용 판단 |
|---|---:|---:|---|
| `intelligentai/datasets/` | 4 | 25,054,742,058 | FLEURS 한국어·Zeroth와 manifest/license. 같은 원본 재다운로드 방지 |
| `intelligentai/models/` | 47 | 110,778,018,852 | Clova·기존 모델 cache·runtime 자산. 기존 배포에 필요한 모델 보존 |
| `intelligentai/recovery/` | 59 | 212,292,142,082 | 복구 archive와 기록. 외부 데이터 수집 목적으로 전체 복원하지 않음 |

AI Hub 71490/538/68의 **독립적으로 사용할 수 있는 원본 객체**는 이번 공개 객체명 목록에서 확인되지 않았다. 암호화 복구 archive의 내부를 열어 조사한 것은 아니므로 원본 자체가 없다고 단정하지 않는다. 기존 manifest와 보관 위치를 먼저 대조한 뒤 필요한 데이터만 추가한다.

## 수집 우선순위

| 순서 | 자료 | B2에 보관할 내용 | 다음 활용·조건 |
|---|---|---|---|
| 1 | 국립국어원 발음교육 지침서 | 공식 PDF, 원본 URL·수집일·SHA-256 manifest | 페이지별 텍스트와 발음 지도 항목 추출 → 검토된 목표음 안내. 서비스용 재사용과 내용 검토는 별도 |
| 2 | 한국어기초사전 | 인증된 API 응답 snapshot과 출처·발음/활용 metadata | 목표 발음·단어 문맥·연습 예시 보강. 사전 API 인증키 별도 필요 |
| 3 | 기존 AI Hub 71490 자료 | 승인된 원본 WAV·JSON·TextGrid 또는 기존 객체 참조, speaker split·오류 구간 manifest | 기존 오류 ontology와 목표/실제 발음 대응 보강. AI Hub 인증·사용 승인 및 보관 조건 확인 |
| 4 | AI Hub 538/68 중 필요한 부분 | 선택 shard의 원본과 source receipt, 영상/랜드마크 metadata | 프레임별 입술 형상·전이·음성 정렬 특징. 공개 라벨을 교정 행동 정답으로 간주하지 않음 |
| 5 | SNU-MAC | 이용 범위가 확인된 조음 음성·영상·annotation만 저장 | 혀/입술 정면·측면의 교육 reference. 문의·권한 확인 전 자동 수집 대상에 넣지 않음 |
| 6 | 교정 전후 자체 수집 사례 | 동의·접근 범위가 기록된 발화, 전문가 지시, 재발화와 판단 | 실제로 어떤 교정 행동이 도움이 됐는지 보강 |

자료의 공식 출처와 라벨 한계는 [상세 피드백 계획의 외부 근거 조사](clova_detailed_feedback_20260909.md)를 따른다. B2 접속 키는 AI Hub·사전·SNU-MAC의 접근 권한을 대신하지 않는다. 수집 registry는 자료별 `stored_verified`, `ready_to_fetch`, `credentials_required`, `permission_required`, `needs_existing_object_lookup` 상태를 구분한다.

## 객체 구조와 중복 방지

```text
speakai2026/
  intelligentai/external/
    knowledge/<provider>/<source>/<retrieved-date>/<sha256>/
      source.pdf
      source-manifest.json
    corpora/<dataset>/<version>/<split>/<shard>/
      <original-file>
      source-manifest.json
    registry/<revision>.json
  intelligentai/derived/
    knowledge/<source>/<source-sha256>/<extractor-revision>/
    speech/<dataset>/<pipeline-revision>/<split>/<shard>/
    visual/<dataset>/<pipeline-revision>/<split>/<shard>/
  intelligentai/releases/coaching/<release-id>/
    manifest.json
    guidance-catalog.json
    retrieval-index/
```

기존 `datasets/open`, `models`, `recovery` 객체는 새 구조를 맞추려고 복제하지 않는다. registry에서 기존 key/file ID/hash를 참조한다. 원본 이름·크기만 같다고 중복으로 판정하지 않고 출처 버전과 SHA-256을 대조한다. 동일 이름 재업로드는 B2의 새 버전을 만들어 비용이 증가할 수 있으므로 완료된 동일 객체가 있으면 건너뛴다. [Backblaze 파일·버전 설명](https://www.backblaze.com/docs/cloud-storage-files)

source manifest에는 URL·자료 버전·수집 시각, bucket/key/file ID, bytes·SHA-256, 원본 권한 상태, 변환 revision·상위 원본 hash를 기록한다. 토큰이 포함된 URL, 다운로드 Authorization 헤더, `.env`, 원본의 사용자 식별자를 공개 manifest에 기록하지 않는다. 제한 metadata도 B2 비공개 범위 안에 둔다.

## 원본 URL에서 B2로 전달하는 방식

B2는 객체 저장소이므로 수집 프로세스가 원본 서버에서 읽은 바이트를 B2로 전달한다. 새 수집기는 **원본 HTTP/API → 제한된 메모리·임시 part → B2** 흐름을 사용한다. 전체 코퍼스를 PC나 RunPod 영구 디스크에 먼저 내려받지 않는다.

작은 PDF·JSON은 길이와 해시를 확인해 Native API로 업로드한다. 큰 음성·영상 archive는 Native large-file upload로 나누어 보내고 part 번호·SHA-1과 전체 SHA-256을 기록한다. 최초 제안은 64MiB part, 동시 전송 2개이며 실제 구현에서는 서버의 최소 part 크기·권고값과 자원 예산을 함께 확인한다. 업로드 중단 시 part 영수증으로 재개하고, 완료 전 객체를 분석 자료로 공개하지 않는다. 미완료 part도 저장 비용에 포함되므로 중단 상태와 정리 정책을 기록한다. [공식 대용량 업로드 문서](https://www.backblaze.com/docs/cloud-storage-large-files), [Native API 업로드](https://www.backblaze.com/docs/cloud-storage-upload-files-with-the-native-api)

완료 판단은 HTTP 성공만으로 하지 않는다. 전송 크기·part 검증, 전체 hash와 manifest를 확인한다. 작은 문서는 다운로드해 SHA-256까지 대조한다. 큰 파일은 전송 중 전체 hash와 part 검증을 저장하고, 필요한 범위를 복원할 때도 검증한다. 전체 왕복 재다운로드가 없는 대형 객체를 `roundtrip_verified=true`로 표시하지 않는다.

현재 [AI Hub downloader](../scripts/download_aihub_secure.py)는 로컬 `Path`와 `.partial` 파일을 전제로 하며 B2 URI를 직접 지원하지 않는다. 또한 일부 데이터의 승인 절차가 파일 descriptor/inode와 결합되어 있다. 이를 임의 pipe나 FUSE mount로 우회하지 않는다. 승인 절차를 유지하는 B2 sink를 구현하거나, 한 번에 허용된 작은 shard만 임시 staging해 B2 검증 후 지운다. shard 하나가 캐시 예산보다 크고 streaming이 불가능하면 다운로드를 시작하지 않고 수집 방식부터 조정한다.

## RunPod에서 분석하고 Clova에 활용

1. **필요한 shard 선택:** B2 registry의 file ID·hash를 기준으로 특정 문서, 화자 split, 음소 문맥 또는 영상 shard만 선택한다.
2. **작업별 materialize:** `/tmp/intelligentai-external-data/<job-id>/`에 소유자 전용 권한으로 캐시한다. 시작 전에 남은 용량과 모든 진행 중 작업의 합계를 확인한다. 4GiB 한도는 구현 단계에서 실제 제한·거부 처리로 연결한다.
3. **원본과 파생 결과 분리:** PDF 텍스트·교육 항목, 음성 alignment·오류 라벨, 영상 trajectory를 처리하고 원본 hash와 변환 revision을 붙여 `derived/`에 저장한다. 기존 71490 manifest/enrichment 처리기를 캐시의 상대경로에 연결해 재사용한다.
4. **검토된 작은 자산 배포:** 발음 방법·대조 연습·적용 문맥·출처·필요 근거를 가진 카탈로그와 검색 index를 B2에서 version별 release로 만든다. 승인된 release만 RunPod 실행 cache로 가져온다. 배포 완료 후의 작은 실행 자산은 기존 배포 파이프라인에 필요한 자산으로 취급한다.
5. **Clova 직전 선택:** `seungun_bridge.py`와 `video_grounded_advice.py`의 claim 생성 앞에서 목표 음소·문맥과 현재 분석에 맞는 항목을 고른다. 일반 목표음 안내와 현재 시도의 진단을 구분하고 `requiredFragments` 검증을 재사용한다. 외부 원자료·gold 오류 라벨을 Clova의 실시간 입력으로 넘기지 않는다.
6. **검증 후 임시 파일 삭제:** 파생 객체와 manifest가 B2에 저장된 것을 확인한 뒤 해당 작업의 캐시를 삭제한다. 실패 작업은 재개 정보와 보유 이유를 기록하되 전체 데이터셋을 영구 캐시로 남기지 않는다.

원본을 B2에 뒀다는 이유만으로 매 요청마다 전체 archive를 내려받으면 전송·지연·GPU 대기 시간이 증가한다. 신규 코퍼스는 독립 처리 가능한 작은 shard로 보관하고, 기존 큰 TAR는 필요할 때 순차 처리해 작은 파생 shard를 만든다. 저장·다운로드 작업만을 위해 A6000을 새로 켜는 작업은 이번 계획에 포함하지 않는다. 현재 서비스 추론과 겹치는 대규모 분석·학습은 별도 일정과 자원 예산을 정한다.

## 이번 실행과 후속 구현의 구분

완료: `.env`와 비밀 없는 template, Git 제외, RunPod 전용 비밀파일, 양쪽 Native API 인증, 기존 bucket/current-object 목록 조회. 교원용 PDF의 B2 저장 결과는 아래 기록을 따른다.

계획: 범용 B2 수집기·registry publisher·캐시 제한기·문서 추출기·교육 카탈로그 선택기 구현, 추가 provider 인증/승인 확인, 코퍼스별 라벨 보강. `.env`만으로 이 기능들이 작동한다고 표시하지 않는다. 모델 설정·서비스 실행 코드는 이번 작업에서 수정하지 않고, 브라우저·자동 회귀 QA·모델 학습·Git commit/push도 수행하지 않는다.

### 첫 외부 자료 보관 완료

앞선 조사에서 받은 『세종한국어 5 교원용 지침서』 PDF를 기존 버킷에 이관했다.

- 원본 객체: `intelligentai/external/knowledge/national-institute-korean/sejong5-teacher-guide/20260909/23c9b0d98cc869f9b07671318a9b7dcaf6ab9ca55e04c7437dbf91ba03a9c6de/source.pdf`
- 크기: **18,495,513 bytes**.
- SHA-256: `23c9b0d98cc869f9b07671318a9b7dcaf6ab9ca55e04c7437dbf91ba03a9c6de`.
- 같은 경로의 `source-manifest.json`에 공식 원본 URL, 수집일, file ID, hash와 검토 상태를 보관했다.
- 업로드 SHA-1 및 다운로드 전체 SHA-256·바이트 수를 확인했다. 왕복 확인은 메모리로 수행했고, 그 후 PC의 기존 임시 PDF를 삭제했다. RunPod 영구 원본 사본은 만들지 않았다.
- 이 객체는 비공개 참고 자료로 저장한 상태이며, 내용의 카탈로그 승인과 제품 재사용 검토는 아직 완료하지 않았다.

이번 실행에서는 이 PDF와 출처 manifest만 새 외부 자료로 저장했다. AI Hub·사전·SNU-MAC 전체를 수집했다고 표시하지 않는다. 저장 위치 지시 이후 새 외부 원본은 B2를 기준으로 관리한다.
