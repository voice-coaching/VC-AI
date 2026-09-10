# Backblaze B2 수집·복원 안내

작업 경계는 [AGENTS.md](../AGENTS.md), 상세 저장·활용 설계는 [외부 데이터 계획](../docs/backblaze_external_data_plan_20260909.md)을 따른다. 버킷 ID·이름과 credential은 작업 시 env에서 읽으며 현재 접속 여부는 별도로 확인한다.

## 수집·복원 판단

1. 대상 출처·버전·필요한 부분과 보유 manifest를 확인하고 같은 hash의 객체를 중복 저장하지 않는다.
2. 제한된 메모리·임시 part로 원본 바이트를 읽어 B2에 보관한다. B2에 URL만 전달하면 외부 자료가 자동 다운로드되는 구조가 아니다.
3. file ID·크기·hash·출처·변환 revision·완료 상태를 기록한다. 영구 원본/파생 자료 저장, 카탈로그 검토, 제품에서 실제 사용 중인 상태를 구분한다.
4. 저장 검증 뒤 해당 작업의 임시 사본을 정리한다. 객체 삭제는 정확한 버전과 복구·실행 의존성을 확인해 요청 범위에 적용한다.

사용자 녹음 저장소와 연구자료 B2 연결은 별도로 확인한다. 내부 API의 동의/grant 제외 결정은 외부 자료의 출처·이용 범위 확인을 제거하는 지시가 아니다.

[다운로더](../scripts/download_aihub_secure.py)는 로컬 Path 기반이다. `b2://` 경로나 env만 넣으면 B2로 전환되지 않는다. 범용 B2 수집기·캐시 제한기는 미구현이며 cache/prefix 설정의 구현 상태는 루트 계획을 따른다.

## 인증·버킷 확인 예제

Master Application Key는 Native API에 사용하고 S3 호환 API에는 사용하지 않는다. S3 adapter가 필요하면 그 용도의 별도 application key와 역할별 설정을 준비한다. [공식 키 설명](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)

프로젝트 루트의 전용 PC PowerShell에서 [env 로딩](env.md)을 한 뒤 **같은 프로세스에서** 아래 코드를 이어 실행한다. 별도 도구 호출 사이의 변수 유지를 가정하지 않는다. 이 예제는 인증·조회이며 버킷·객체를 변경하지 않는다. 실패 응답은 상태와 고정 오류 코드만 확인하고 인증 본문 전체를 출력하지 않는다.

[인증 응답](https://www.backblaze.com/apidocs/b2-authorize-account)의 storageApi 주소를 다음 요청에 사용한다. 예제는 [버킷 조회](https://www.backblaze.com/apidocs/b2-list-buckets) 권한과 지정한 비공개 버킷을 전제로 한다. 권한 오류를 버킷 부재로 단정하지 않는다.

```powershell
$ErrorActionPreference = 'Stop'
if ($env:B2_API_MODE -ne 'native' -or
    $env:B2_AUTH_URL -ne 'https://api.backblazeb2.com/b2api/v4/b2_authorize_account') {
    throw 'Load the expected B2 Native API environment first'
}
if (-not $env:B2_APPLICATION_KEY_ID -or -not $env:B2_APPLICATION_KEY -or
    -not $env:B2_BUCKET_ID -or -not $env:B2_BUCKET_NAME) {
    throw 'Missing B2 credentials'
}
$b2CredentialBytes = [Text.Encoding]::UTF8.GetBytes(
    $env:B2_APPLICATION_KEY_ID + ':' + $env:B2_APPLICATION_KEY
)
$b2Basic = [Convert]::ToBase64String($b2CredentialBytes)
try {
    $b2Auth = Invoke-RestMethod -Method Get -Uri $env:B2_AUTH_URL `
        -Headers @{ Authorization = ('Basic ' + $b2Basic) } -TimeoutSec 60
} finally {
    [Array]::Clear($b2CredentialBytes, 0, $b2CredentialBytes.Length)
    Remove-Variable b2CredentialBytes,b2Basic -ErrorAction SilentlyContinue
}
$b2Api = $b2Auth.apiInfo.storageApi.apiUrl
$b2Download = $b2Auth.apiInfo.storageApi.downloadUrl
$b2Headers = @{ Authorization = $b2Auth.authorizationToken }
$b2BucketRequest = @{
    accountId = $b2Auth.accountId
    bucketId = $env:B2_BUCKET_ID
} | ConvertTo-Json -Compress
$b2BucketReply = Invoke-RestMethod -Method Post `
    -Uri "$b2Api/b2api/v4/b2_list_buckets" -Headers $b2Headers `
    -ContentType 'application/json' -Body $b2BucketRequest -TimeoutSec 60
$b2Bucket = @($b2BucketReply.buckets | Where-Object {
    $_.bucketId -eq $env:B2_BUCKET_ID -and $_.bucketName -eq $env:B2_BUCKET_NAME
})
if ($b2Bucket.Count -ne 1 -or $b2Bucket[0].bucketType -ne 'allPrivate') {
    throw 'Expected private bucket was not found'
}
$b2Bucket | Select-Object bucketName,bucketId,bucketType
```

`bad_auth_token`·`expired_auth_token`은 재인증하고 권한 부족은 키의 대상·capability를 확인한다. 토큰은 Authorization 헤더로 전달하며 query string·로그·manifest에 넣지 않는다.

## Prefix의 현재 객체 목록

같은 PowerShell 프로세스에서 확인할 prefix를 지정한다. 아래는 env의 외부 자료 prefix를 사용하며, 모델·기존 자료·복구 객체의 비용 조사에는 해당 prefix를 별도로 지정한다. 출력은 이름·file ID·크기와 합계뿐이다.

```powershell
$b2Prefix = $env:B2_EXTERNAL_PREFIX
if (-not $b2Prefix -or -not $b2Api -or -not $b2Headers -or -not $b2Bucket) {
    throw 'Set the requested prefix and complete authentication/bucket verification in this process first'
}
$b2NextName = $null
$b2CurrentObjects = 0L
$b2CurrentBytes = 0L
do {
    $b2ListRequest = @{ bucketId = $env:B2_BUCKET_ID; prefix = $b2Prefix; maxFileCount = 1000 }
    if ($b2NextName) { $b2ListRequest.startFileName = $b2NextName }
    $b2Page = Invoke-RestMethod -Method Post `
        -Uri "$b2Api/b2api/v4/b2_list_file_names" -Headers $b2Headers `
        -ContentType 'application/json' `
        -Body ($b2ListRequest | ConvertTo-Json -Compress) -TimeoutSec 60
    foreach ($b2File in $b2Page.files) {
        $b2CurrentObjects++
        $b2CurrentBytes += $b2File.contentLength
        $b2File | Select-Object fileName,contentLength,fileId
    }
    $b2NextName = $b2Page.nextFileName
} while ($b2NextName)
[pscustomobject]@{ CurrentObjects = $b2CurrentObjects; CurrentBytes = $b2CurrentBytes }
```

[목록 API](https://www.backblaze.com/apidocs/b2-list-file-names)의 마지막 페이지까지 조회했을 때만 prefix의 합계로 보고한다. 이 값은 과거 버전·숨김 marker·미완료 part를 포함한 청구 용량이 아니다. 비용 조사에는 [파일 버전](https://www.backblaze.com/docs/cloud-storage-files)과 [미완료 대용량 파일](https://www.backblaze.com/docs/cloud-storage-large-files)도 확인한다. 중간 실패 시 집계를 전체 용량으로 보고하지 않는다.

## 다운로드·복원

manifest의 **file ID·크기·SHA-256**을 기준으로 [Native 다운로드](https://www.backblaze.com/apidocs/b2-download-file-by-id)를 수행한다. 인증의 downloadUrl에 요청하며 임의 최신 버전을 이름만으로 선택하지 않는다.

소규모 자료는 작업별 임시 파일에 받고 크기·hash 일치 후 사용한다. 실패한 partial 파일은 사용하지 않고 정리한다. 정리 대상의 절대 경로가 그 작업의 임시 폴더 내부인지 확인한다. 대형 자료는 필요한 shard/part만 선택하고, 전체 archive를 RunPod 영구 디스크에 복원하지 않는다.

특정 PDF의 보관·검증 사례는 [외부 데이터 기록](../docs/backblaze_external_data_plan_20260909.md)에 남긴다. 사례의 객체 key·크기·hash를 일반 다운로드의 기본값으로 복제하지 않는다. 저장 사례는 범용 캐시·중단 재개 구현이나 교육 내용의 제품 반영을 의미하지 않는다.

## 신규 자료 업로드 절차

현재 저장 사례는 있지만 범용 B2 수집 CLI는 아직 없다. 기존 `scripts/download_aihub_secure.py`는 로컬 `Path` 기반이므로 `b2://...`를 출력 경로에 넣어도 동작하지 않는다. 후속 구현은 다음 순서를 따른다.

1. 원본 접근 권한과 기존 보유 객체를 확인한다. 출처 버전·SHA-256이 같은 자료는 기존 file ID를 참조한다.
2. 원본 HTTP/API에서 제한된 메모리 또는 작업별 임시 캐시로 읽어 크기·SHA-1·SHA-256을 계산한다. 새 외부 원본의 영구 목적지는 B2다.
3. 작은 파일은 `apiUrl`의 `b2_get_upload_url`에 `bucketId`를 보내고 `uploadUrl`과 업로드 전용 `authorizationToken`을 받는다. 응답을 로그에 남기지 않는다. [업로드 URL API](https://www.backblaze.com/apidocs/b2-get-upload-url)
4. 해당 `uploadUrl`에 파일 바이트를 POST한다. `Authorization`에는 업로드 전용 토큰, `X-Bz-File-Name`에는 UTF-8 percent-encoded 객체 key, `Content-Type`, 정확한 `Content-Length`, `X-Bz-Content-Sha1`을 전달한다. JSON body나 HTTP chunked upload로 보내지 않는다. [파일 업로드 API](https://www.backblaze.com/apidocs/b2-upload-file)
5. 응답의 file ID·이름·크기·SHA-1을 대조한다. 작은 자료는 다운로드 SHA-256도 검증하고 `source-manifest.json`을 B2에 저장한다. manifest 저장·검증이 끝나면 원본 임시 사본을 제거한다.

큰 음성·영상 자료는 `b2_start_large_file` → `b2_get_upload_part_url` → `b2_upload_part` → `b2_finish_large_file` 순서로 구현한다. part별 SHA-1과 전체 SHA-256, 완료 여부를 기록한다. 64MiB part·동시 전송 2개는 현재 **계획값**이며 서버 제한과 전체 4GiB 캐시 예산을 확인해 적용한다. 중단 파일은 재개 또는 정리 대상으로 기록한다. 미완료 part도 비용을 차지한다. [공식 large-file 설명](https://www.backblaze.com/docs/cloud-storage-large-files)


## 보관 위치와 복구 근거

기존 `intelligentai/datasets/`, `intelligentai/models/`, `intelligentai/recovery/`와 신규 원본·파생·교육 release의 역할은 [저장 계획](../docs/backblaze_external_data_plan_20260909.md)을 따른다. 구조를 맞추기 위해 기존 원본을 복제하지 않는다. prefix의 실제 값은 env와 대상 manifest를 확인한다.

과거 복구 객체는 [이전 실행 기록](../docs/runpod_b2_storage_migration_execution.md), 로컬에 있을 경우 `.codex/local/history.md`와 원본 영수증에서 찾는다. 해당 revision과 file ID·hash를 확인한다. 이름에 과거 Pod ID가 포함됐다는 이유만으로 복구 객체를 삭제하지 않는다.

overlay는 전체 모델·Python 환경·비밀 설정의 복구 보장이 아니다. manifest의 포함 범위와 대상 디스크 여유를 확인하고 필요한 파일만 복원한다. 비밀값을 복구 archive에 추가하지 않는다.

작업 후 인증 프로세스를 닫고 저장/복원 대상·완료 버전·크기·hash·확인 범위를 기록한다. B2 원자료나 gold 오류 라벨을 그대로 실시간 Clova 입력에 넣지 않으며, 검토된 작은 카탈로그를 사용하는 후속 계획과 구분한다.
