[CmdletBinding()]
param(
    [ValidateSet('Status', 'Download', 'Verify', 'Extract', 'All')]
    [string]$Action = 'Status',

    [string[]]$Dataset = @('all'),

    [string]$CatalogPath,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ([string]::IsNullOrWhiteSpace($CatalogPath)) {
    $CatalogPath = Join-Path $projectRoot 'configs/data/native_korean_corpora.psd1'
}
$CatalogPath = [System.IO.Path]::GetFullPath($CatalogPath)
$archiveHelper = Join-Path $PSScriptRoot 'native_corpus_archive.py'

function Get-OptionalValue {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Table,
        [Parameter(Mandatory = $true)] [string]$Key
    )
    if ($Table.ContainsKey($Key)) {
        return $Table[$Key]
    }
    return $null
}

function Get-SafeChildPath {
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$RelativePath
    )
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $rootFull $RelativePath))
    $prefix = $rootFull.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (
        $candidate -ne $rootFull -and
        -not $candidate.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Path escapes its allowed root: $RelativePath"
    }
    return $candidate
}

function Write-JsonAtomically {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] $Data
    )
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporaryPath = "$Path.tmp.$PID"
    try {
        $json = $Data | ConvertTo-Json -Depth 12
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporaryPath, $json, $encoding)
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Read-JsonMarker {
    param([Parameter(Mandatory = $true)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return [System.IO.File]::ReadAllText($Path) | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-StateDirectory {
    param([Parameter(Mandatory = $true)] [string]$StoragePath)
    return Join-Path $StoragePath '.native-corpus-state'
}

function Get-FileMarkerPath {
    param(
        [Parameter(Mandatory = $true)] [string]$StoragePath,
        [Parameter(Mandatory = $true)] [hashtable]$File
    )
    return Join-Path (Get-StateDirectory -StoragePath $StoragePath) (
        "$($File.Name).complete.json"
    )
}

function Test-ParquetEnvelope {
    param([Parameter(Mandatory = $true)] [string]$Path)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        if ($stream.Length -lt 8) {
            throw "Parquet file is too short: $Path"
        }
        $first = New-Object byte[] 4
        $last = New-Object byte[] 4
        if ($stream.Read($first, 0, 4) -ne 4) {
            throw "Could not read the Parquet header: $Path"
        }
        $stream.Seek(-4, [System.IO.SeekOrigin]::End) | Out-Null
        if ($stream.Read($last, 0, 4) -ne 4) {
            throw "Could not read the Parquet footer: $Path"
        }
        $firstMagic = [System.Text.Encoding]::ASCII.GetString($first)
        $lastMagic = [System.Text.Encoding]::ASCII.GetString($last)
        if (
            $firstMagic -ne 'PAR1' -or
            $lastMagic -ne 'PAR1'
        ) {
            throw "Invalid Parquet header/footer magic: $Path"
        }
    }
    finally {
        $stream.Dispose()
    }
    return [ordered]@{ parquetEnvelope = 'PAR1' }
}

function Test-FleursTsv {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [int]$ExpectedRows
    )
    $rowCount = 0
    $audioNames = New-Object 'System.Collections.Generic.HashSet[string]' (
        [System.StringComparer]::Ordinal
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    foreach ($line in [System.IO.File]::ReadLines($Path, $utf8)) {
        $rowCount++
        $columns = $line.Split("`t")
        if ($columns.Count -ne 7) {
            throw "$Path line $rowCount has $($columns.Count) columns; expected 7"
        }
        $id = [Int64]0
        $samples = [Int64]0
        if (-not [Int64]::TryParse($columns[0], [ref]$id)) {
            throw "$Path line $rowCount has an invalid id"
        }
        if (-not [Int64]::TryParse($columns[5], [ref]$samples)) {
            throw "$Path line $rowCount has an invalid sample count"
        }
        if (-not $columns[1].EndsWith(
            '.wav',
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "$Path line $rowCount has an invalid audio filename"
        }
        if (-not $audioNames.Add($columns[1])) {
            throw "$Path contains a duplicate audio filename: $($columns[1])"
        }
    }
    if ($rowCount -ne $ExpectedRows) {
        throw "TSV row mismatch for $Path : $rowCount != $ExpectedRows"
    }
    return [ordered]@{
        tsvRows = $rowCount
        tsvColumns = 7
        uniqueAudioNames = $audioNames.Count
    }
}

function Test-RequiredText {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string[]]$RequiredText
    )
    $text = [System.IO.File]::ReadAllText(
        $Path,
        (New-Object System.Text.UTF8Encoding($false, $true))
    )
    foreach ($required in $RequiredText) {
        if (
            $text.IndexOf(
                $required,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -lt 0
        ) {
            throw "Required text '$required' is missing from $Path"
        }
    }
    return [ordered]@{ requiredTextMatched = @($RequiredText) }
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        throw 'python was not found; Python 3.12+ is required for safe tar extraction'
    }
    return $python.Source
}

function Invoke-ArchiveHelper {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('inspect', 'extract', 'validate-fleurs')]
        [string]$Command,
        [Parameter(Mandatory = $true)] [string]$ArchivePath,
        [string]$ExpectedGzipCrc32,
        [string]$Destination,
        [string]$TsvPath,
        [int]$ExpectedRows
    )
    if (-not (Test-Path -LiteralPath $archiveHelper -PathType Leaf)) {
        throw "Archive helper not found: $archiveHelper"
    }
    $arguments = @($archiveHelper, $Command, '--archive', $ArchivePath)
    if (-not [string]::IsNullOrWhiteSpace($ExpectedGzipCrc32)) {
        $arguments += @('--expected-gzip-crc32', $ExpectedGzipCrc32)
    }
    if (-not [string]::IsNullOrWhiteSpace($Destination)) {
        $arguments += @('--destination', $Destination)
    }
    if (-not [string]::IsNullOrWhiteSpace($TsvPath)) {
        $arguments += @('--tsv', $TsvPath)
    }
    if ($ExpectedRows -gt 0) {
        $arguments += @('--expected-rows', $ExpectedRows.ToString())
    }

    $output = & (Get-PythonCommand) @arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Archive helper failed for $ArchivePath : $($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine) | ConvertFrom-Json
}

function Test-ConfiguredFileContent {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath,
        [Parameter(Mandatory = $true)] [string]$Path
    )
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.Length -ne [Int64]$FileConfig.Bytes) {
        throw "Byte mismatch for $Path : $($item.Length) != $($FileConfig.Bytes)"
    }

    Write-Host "HASHING [$($DatasetConfig.Id)] $($FileConfig.Name)"
    $sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedSha256 = [string](Get-OptionalValue -Table $FileConfig -Key 'Sha256')
    if (
        -not [string]::IsNullOrWhiteSpace($expectedSha256) -and
        $sha256 -ne $expectedSha256.ToLowerInvariant()
    ) {
        throw "SHA-256 mismatch for $Path : $sha256 != $expectedSha256"
    }

    $kind = [string]$FileConfig.Kind
    $validation = $null
    switch ($kind) {
        'parquet' {
            $validation = Test-ParquetEnvelope -Path $Path
        }
        'fleurs-tsv' {
            $validation = Test-FleursTsv `
                -Path $Path `
                -ExpectedRows ([int]$FileConfig.ExpectedRows)
        }
        'license-evidence' {
            $validation = Test-RequiredText `
                -Path $Path `
                -RequiredText @($FileConfig.RequiredText)
        }
        'tar.gz' {
            $crc32 = [string](Get-OptionalValue -Table $FileConfig -Key 'GzipCrc32')
            $pairTsv = [string](Get-OptionalValue -Table $FileConfig -Key 'PairTsv')
            if (-not [string]::IsNullOrWhiteSpace($pairTsv)) {
                $tsvPath = Get-SafeChildPath -Root $StoragePath -RelativePath $pairTsv
                if (-not (Test-Path -LiteralPath $tsvPath -PathType Leaf)) {
                    throw "Paired FLEURS metadata is missing: $tsvPath"
                }
                $validation = Invoke-ArchiveHelper `
                    -Command 'validate-fleurs' `
                    -ArchivePath $Path `
                    -ExpectedGzipCrc32 $crc32 `
                    -TsvPath $tsvPath `
                    -ExpectedRows ([int]$FileConfig.ExpectedRows)
            }
            else {
                $validation = Invoke-ArchiveHelper `
                    -Command 'inspect' `
                    -ArchivePath $Path `
                    -ExpectedGzipCrc32 $crc32
            }
        }
        default {
            throw "Unsupported file kind '$kind' for $Path"
        }
    }

    return [ordered]@{
        sha256 = $sha256
        validation = $validation
    }
}

function Write-FileCompletionMarker {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath,
        [Parameter(Mandatory = $true)] [string]$FinalPath,
        [Parameter(Mandatory = $true)] $Verification
    )
    $item = Get-Item -LiteralPath $FinalPath -ErrorAction Stop
    $markerPath = Get-FileMarkerPath -StoragePath $StoragePath -File $FileConfig
    $marker = [ordered]@{
        schemaVersion = 1
        datasetId = $DatasetConfig.Id
        fileName = $FileConfig.Name
        bytes = [Int64]$item.Length
        expectedBytes = [Int64]$FileConfig.Bytes
        sha256 = $Verification.sha256
        expectedSha256 = [string](Get-OptionalValue -Table $FileConfig -Key 'Sha256')
        gzipCrc32 = [string](Get-OptionalValue -Table $FileConfig -Key 'GzipCrc32')
        lastWriteTimeUtcTicks = $item.LastWriteTimeUtc.Ticks
        verifiedAtUtc = [DateTime]::UtcNow.ToString('o')
        validation = $Verification.validation
    }
    Write-JsonAtomically -Path $markerPath -Data $marker
}

function Test-FileCompletionMarker {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath,
        [Parameter(Mandatory = $true)] [string]$FinalPath
    )
    if (-not (Test-Path -LiteralPath $FinalPath -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $FinalPath
    if ($item.Length -ne [Int64]$FileConfig.Bytes) {
        return $false
    }
    $marker = Read-JsonMarker (
        Get-FileMarkerPath -StoragePath $StoragePath -File $FileConfig
    )
    if ($null -eq $marker) {
        return $false
    }
    if (
        [Int64]$marker.bytes -ne $item.Length -or
        [Int64]$marker.expectedBytes -ne [Int64]$FileConfig.Bytes -or
        [Int64]$marker.lastWriteTimeUtcTicks -ne $item.LastWriteTimeUtc.Ticks
    ) {
        return $false
    }
    $expectedSha256 = [string](Get-OptionalValue -Table $FileConfig -Key 'Sha256')
    if (
        -not [string]::IsNullOrWhiteSpace($expectedSha256) -and
        ([string]$marker.sha256).ToLowerInvariant() -ne $expectedSha256.ToLowerInvariant()
    ) {
        return $false
    }
    $pairTsv = [string](Get-OptionalValue -Table $FileConfig -Key 'PairTsv')
    if (-not [string]::IsNullOrWhiteSpace($pairTsv)) {
        if (
            $null -eq $marker.validation -or
            $marker.validation.metadata_audio_match -ne $true
        ) {
            return $false
        }
    }
    return $true
}

function Get-LegacyCandidates {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath
    )
    $patterns = @(Get-OptionalValue -Table $FileConfig -Key 'LegacyCandidates')
    $candidates = @()
    foreach ($pattern in $patterns) {
        if ([string]::IsNullOrWhiteSpace([string]$pattern)) {
            continue
        }
        if (
            ([string]$pattern).Contains([System.IO.Path]::DirectorySeparatorChar) -or
            ([string]$pattern).Contains([System.IO.Path]::AltDirectorySeparatorChar)
        ) {
            throw "Legacy candidate patterns must be file names only: $pattern"
        }
        $candidates += @(
            Get-ChildItem `
                -LiteralPath $StoragePath `
                -Filter ([string]$pattern) `
                -File `
                -Force `
                -ErrorAction SilentlyContinue
        )
    }
    return @(
        $candidates |
            Where-Object { $_.Length -eq [Int64]$FileConfig.Bytes } |
            Sort-Object FullName -Unique
    )
}

function Get-ConfiguredFileStatus {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath
    )
    $finalPath = Get-SafeChildPath -Root $StoragePath -RelativePath $FileConfig.Name
    $partialPath = "$finalPath.part"
    if (Test-Path -LiteralPath $finalPath -PathType Leaf) {
        $item = Get-Item -LiteralPath $finalPath
        if ($item.Length -ne [Int64]$FileConfig.Bytes) {
            return [ordered]@{
                state = 'INVALID_SIZE'
                detail = "$($item.Length)/$($FileConfig.Bytes)"
            }
        }
        if (
            Test-FileCompletionMarker `
                -FileConfig $FileConfig `
                -StoragePath $StoragePath `
                -FinalPath $finalPath
        ) {
            return [ordered]@{ state = 'COMPLETE'; detail = "$($item.Length) bytes" }
        }
        return [ordered]@{ state = 'UNVERIFIED'; detail = "$($item.Length) bytes" }
    }
    if (Test-Path -LiteralPath $partialPath -PathType Leaf) {
        $item = Get-Item -LiteralPath $partialPath
        return [ordered]@{
            state = 'PARTIAL'
            detail = "$($item.Length)/$($FileConfig.Bytes)"
        }
    }
    $legacy = @(Get-LegacyCandidates -FileConfig $FileConfig -StoragePath $StoragePath)
    if ($legacy.Count -gt 0) {
        return [ordered]@{
            state = 'LEGACY_COMPLETE_CANDIDATE'
            detail = ($legacy.FullName -join ', ')
        }
    }
    return [ordered]@{ state = 'MISSING'; detail = '0 bytes' }
}

function Assert-FreeSpace {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [Int64]$RequiredBytes,
        [string]$Operation = 'operation'
    )
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    $drive = New-Object System.IO.DriveInfo($root)
    if ($drive.AvailableFreeSpace -lt $RequiredBytes) {
        throw (
            "Insufficient space for $Operation. Required: " +
            "$([Math]::Round($RequiredBytes / 1GB, 2)) GiB; available: " +
            "$([Math]::Round($drive.AvailableFreeSpace / 1GB, 2)) GiB"
        )
    }
}

function Invoke-CurlDownload {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$PartialPath
    )
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($null -eq $curl) {
        $curl = Get-Command curl -ErrorAction SilentlyContinue
    }
    if ($null -eq $curl) {
        throw 'curl was not found'
    }
    $success = $false
    foreach ($url in @($FileConfig.Urls)) {
        Write-Output "DOWNLOADING [$($DatasetConfig.Id)] $($FileConfig.Name)"
        & $curl.Source `
            --location `
            --fail `
            --retry 20 `
            --retry-all-errors `
            --retry-delay 10 `
            --continue-at - `
            --output $PartialPath `
            ([string]$url)
        $curlExitCode = $LASTEXITCODE
        if (
            (Test-Path -LiteralPath $PartialPath -PathType Leaf) -and
            (Get-Item -LiteralPath $PartialPath).Length -eq [Int64]$FileConfig.Bytes
        ) {
            $success = $true
            break
        }
        if ($curlExitCode -eq 0) {
            throw "curl returned success but the byte count is wrong: $PartialPath"
        }
        Write-Warning "Source failed with curl exit $curlExitCode; trying next mirror"
    }
    if (-not $success) {
        throw "All download sources failed for $($DatasetConfig.Id)/$($FileConfig.Name)"
    }
}

function Complete-OneFile {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath,
        [switch]$DownloadIfMissing,
        [switch]$ForceVerify
    )
    $finalPath = Get-SafeChildPath -Root $StoragePath -RelativePath $FileConfig.Name
    $status = Get-ConfiguredFileStatus -FileConfig $FileConfig -StoragePath $StoragePath
    if ($status.state -eq 'COMPLETE' -and -not $ForceVerify) {
        Write-Output "SKIP [$($DatasetConfig.Id)] $($FileConfig.Name): COMPLETE"
        return
    }
    if ($status.state -eq 'COMPLETE' -and $ForceVerify) {
        $status = [ordered]@{
            state = 'UNVERIFIED'
            detail = 'forced full verification'
        }
    }
    if ($DryRun) {
        if ($status.state -eq 'MISSING' -and $DownloadIfMissing) {
            Write-Output "WOULD_DOWNLOAD [$($DatasetConfig.Id)] $($FileConfig.Name)"
        }
        elseif ($status.state -eq 'LEGACY_COMPLETE_CANDIDATE') {
            Write-Output "WOULD_VERIFY_AND_ADOPT [$($DatasetConfig.Id)] $($FileConfig.Name)"
        }
        elseif ($status.state -eq 'UNVERIFIED') {
            Write-Output "WOULD_VERIFY [$($DatasetConfig.Id)] $($FileConfig.Name)"
        }
        else {
            Write-Output "BLOCKED [$($DatasetConfig.Id)] $($FileConfig.Name): $($status.state)"
        }
        return
    }
    New-Item -ItemType Directory -Force -Path $StoragePath | Out-Null

    if ($status.state -eq 'INVALID_SIZE') {
        throw "Refusing to overwrite invalid final file: $finalPath ($($status.detail))"
    }
    if ($status.state -eq 'UNVERIFIED') {
        $verification = Test-ConfiguredFileContent `
            -DatasetConfig $DatasetConfig `
            -FileConfig $FileConfig `
            -StoragePath $StoragePath `
            -Path $finalPath
        Write-FileCompletionMarker `
            -DatasetConfig $DatasetConfig `
            -FileConfig $FileConfig `
            -StoragePath $StoragePath `
            -FinalPath $finalPath `
            -Verification $verification
        Write-Output "VERIFIED [$($DatasetConfig.Id)] $($FileConfig.Name)"
        return
    }
    if ($status.state -eq 'LEGACY_COMPLETE_CANDIDATE') {
        $legacy = @(Get-LegacyCandidates -FileConfig $FileConfig -StoragePath $StoragePath)
        if ($legacy.Count -ne 1) {
            throw "Expected one legacy candidate for $finalPath; found $($legacy.Count)"
        }
        $verification = Test-ConfiguredFileContent `
            -DatasetConfig $DatasetConfig `
            -FileConfig $FileConfig `
            -StoragePath $StoragePath `
            -Path $legacy[0].FullName
        Move-Item -LiteralPath $legacy[0].FullName -Destination $finalPath
        $adoptedItem = Get-Item -LiteralPath $finalPath -Force
        $temporaryAttributeMask = (
            [System.IO.FileAttributes]::Hidden -bor
            [System.IO.FileAttributes]::System
        )
        $adoptedItem.Attributes = [System.IO.FileAttributes](
            [int]$adoptedItem.Attributes -band (-bnot [int]$temporaryAttributeMask)
        )
        Write-FileCompletionMarker `
            -DatasetConfig $DatasetConfig `
            -FileConfig $FileConfig `
            -StoragePath $StoragePath `
            -FinalPath $finalPath `
            -Verification $verification
        Write-Output "ADOPTED [$($DatasetConfig.Id)] $($FileConfig.Name)"
        return
    }
    if (-not $DownloadIfMissing) {
        throw "File is not available for verification: $finalPath ($($status.state))"
    }

    $partialPath = "$finalPath.part"
    $partialBytes = [Int64]0
    if (Test-Path -LiteralPath $partialPath -PathType Leaf) {
        $partialBytes = (Get-Item -LiteralPath $partialPath).Length
        if ($partialBytes -gt [Int64]$FileConfig.Bytes) {
            throw "Partial file is larger than expected: $partialPath"
        }
    }
    $remainingBytes = [Int64]$FileConfig.Bytes - $partialBytes
    Assert-FreeSpace `
        -Path $StoragePath `
        -RequiredBytes ($remainingBytes + 512MB) `
        -Operation "download of $($DatasetConfig.Id)/$($FileConfig.Name)"
    Invoke-CurlDownload `
        -DatasetConfig $DatasetConfig `
        -FileConfig $FileConfig `
        -PartialPath $partialPath
    $verification = Test-ConfiguredFileContent `
        -DatasetConfig $DatasetConfig `
        -FileConfig $FileConfig `
        -StoragePath $StoragePath `
        -Path $partialPath
    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Write-FileCompletionMarker `
        -DatasetConfig $DatasetConfig `
        -FileConfig $FileConfig `
        -StoragePath $StoragePath `
        -FinalPath $finalPath `
        -Verification $verification
    Write-Output "COMPLETED [$($DatasetConfig.Id)] $($FileConfig.Name)"
}

function Write-DatasetDownloadMarker {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath
    )
    if ($DryRun) {
        return
    }
    $files = @()
    foreach ($file in @($DatasetConfig.Files)) {
        $finalPath = Get-SafeChildPath -Root $StoragePath -RelativePath $file.Name
        if (
            -not (
                Test-FileCompletionMarker `
                    -FileConfig $file `
                    -StoragePath $StoragePath `
                    -FinalPath $finalPath
            )
        ) {
            throw "Dataset is not fully verified: $($DatasetConfig.Id)/$($file.Name)"
        }
        $fileMarker = Read-JsonMarker (
            Get-FileMarkerPath -StoragePath $StoragePath -File $file
        )
        $files += [ordered]@{
            name = $file.Name
            bytes = [Int64]$fileMarker.bytes
            sha256 = $fileMarker.sha256
        }
    }
    $marker = [ordered]@{
        schemaVersion = 1
        datasetId = $DatasetConfig.Id
        displayName = $DatasetConfig.DisplayName
        license = $DatasetConfig.License
        completedAtUtc = [DateTime]::UtcNow.ToString('o')
        files = $files
    }
    $markerPath = Join-Path (
        Get-StateDirectory -StoragePath $StoragePath
    ) 'download.complete.json'
    Write-JsonAtomically -Path $markerPath -Data $marker
}

function Test-ExtractionMarker {
    param(
        [Parameter(Mandatory = $true)] [string]$MarkerPath,
        [Parameter(Mandatory = $true)] [string]$ArchiveSha256
    )
    $marker = Read-JsonMarker -Path $MarkerPath
    if ($null -eq $marker) {
        return $false
    }
    return (
        ([string]$marker.archiveSha256).ToLowerInvariant() -eq
        $ArchiveSha256.ToLowerInvariant()
    )
}

function Extract-OneArchive {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$DatasetConfig,
        [Parameter(Mandatory = $true)] [hashtable]$FileConfig,
        [Parameter(Mandatory = $true)] [string]$StoragePath
    )
    $extractDirectory = [string](
        Get-OptionalValue -Table $FileConfig -Key 'ExtractDirectory'
    )
    if ([string]::IsNullOrWhiteSpace($extractDirectory)) {
        return
    }
    $archivePath = Get-SafeChildPath -Root $StoragePath -RelativePath $FileConfig.Name
    $destination = Get-SafeChildPath -Root $StoragePath -RelativePath $extractDirectory
    $extractionMarker = Join-Path $destination '.native-corpus-extraction-complete.json'
    $fileMarkerPath = Get-FileMarkerPath -StoragePath $StoragePath -File $FileConfig
    $fileMarker = Read-JsonMarker -Path $fileMarkerPath

    if (
        $null -ne $fileMarker -and
        (Test-ExtractionMarker `
            -MarkerPath $extractionMarker `
            -ArchiveSha256 ([string]$fileMarker.sha256))
    ) {
        Write-Output "SKIP_EXTRACT [$($DatasetConfig.Id)] $($FileConfig.Name): COMPLETE"
        return
    }
    if ($DryRun) {
        if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
            Write-Output "WOULD_EXTRACT_OR_RESUME [$($DatasetConfig.Id)] $($FileConfig.Name)"
        }
        else {
            Write-Output "BLOCKED_EXTRACT [$($DatasetConfig.Id)] $($FileConfig.Name): archive missing"
        }
        return
    }
    if (
        -not (
            Test-FileCompletionMarker `
                -FileConfig $FileConfig `
                -StoragePath $StoragePath `
                -FinalPath $archivePath
        )
    ) {
        Complete-OneFile `
            -DatasetConfig $DatasetConfig `
            -FileConfig $FileConfig `
            -StoragePath $StoragePath
        $fileMarker = Read-JsonMarker -Path $fileMarkerPath
    }

    $crc32 = [string](Get-OptionalValue -Table $FileConfig -Key 'GzipCrc32')
    $plan = Invoke-ArchiveHelper `
        -Command 'inspect' `
        -ArchivePath $archivePath `
        -ExpectedGzipCrc32 $crc32 `
        -Destination $destination
    $requiredBytes = [Int64]$plan.remaining_bytes + 1GB
    Assert-FreeSpace `
        -Path $destination `
        -RequiredBytes $requiredBytes `
        -Operation "extraction of $($DatasetConfig.Id)/$($FileConfig.Name)"

    Write-Output "EXTRACTING [$($DatasetConfig.Id)] $($FileConfig.Name)"
    $result = Invoke-ArchiveHelper `
        -Command 'extract' `
        -ArchivePath $archivePath `
        -ExpectedGzipCrc32 $crc32 `
        -Destination $destination
    $marker = [ordered]@{
        schemaVersion = 1
        datasetId = $DatasetConfig.Id
        archive = $FileConfig.Name
        archiveSha256 = $fileMarker.sha256
        completedAtUtc = [DateTime]::UtcNow.ToString('o')
        verifiedFiles = [Int64]$result.verified_files
        verifiedBytes = [Int64]$result.verified_bytes
    }
    Write-JsonAtomically -Path $extractionMarker -Data $marker
    Write-Output "EXTRACTED [$($DatasetConfig.Id)] $($FileConfig.Name)"
}

function Assert-Catalog {
    param([Parameter(Mandatory = $true)] [hashtable]$Catalog)
    if ([int]$Catalog.SchemaVersion -ne 1) {
        throw "Unsupported native corpus catalog schema: $($Catalog.SchemaVersion)"
    }
    $datasetIds = New-Object 'System.Collections.Generic.HashSet[string]' (
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($datasetConfig in @($Catalog.Datasets)) {
        if (-not $datasetIds.Add([string]$datasetConfig.Id)) {
            throw "Duplicate dataset id: $($datasetConfig.Id)"
        }
        $storagePath = Get-SafeChildPath `
            -Root $projectRoot `
            -RelativePath ([string]$datasetConfig.Storage)
        $fileNames = New-Object 'System.Collections.Generic.HashSet[string]' (
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($fileConfig in @($datasetConfig.Files)) {
            if (
                [System.IO.Path]::GetFileName([string]$fileConfig.Name) -ne
                [string]$fileConfig.Name
            ) {
                throw "Corpus file names cannot contain directories: $($fileConfig.Name)"
            }
            if (-not $fileNames.Add([string]$fileConfig.Name)) {
                throw "Duplicate file name in $($datasetConfig.Id): $($fileConfig.Name)"
            }
            if ([Int64]$fileConfig.Bytes -le 0) {
                throw "Expected bytes must be positive: $($fileConfig.Name)"
            }
            $sha256 = [string](Get-OptionalValue -Table $fileConfig -Key 'Sha256')
            $crc32 = [string](Get-OptionalValue -Table $fileConfig -Key 'GzipCrc32')
            if (
                [string]::IsNullOrWhiteSpace($sha256) -and
                [string]::IsNullOrWhiteSpace($crc32)
            ) {
                throw "A pinned checksum is required: $($datasetConfig.Id)/$($fileConfig.Name)"
            }
            if (
                -not [string]::IsNullOrWhiteSpace($sha256) -and
                $sha256 -notmatch '^[0-9a-fA-F]{64}$'
            ) {
                throw "Invalid SHA-256: $($datasetConfig.Id)/$($fileConfig.Name)"
            }
            if (
                -not [string]::IsNullOrWhiteSpace($crc32) -and
                $crc32 -notmatch '^[0-9a-fA-F]{8}$'
            ) {
                throw "Invalid gzip CRC32: $($datasetConfig.Id)/$($fileConfig.Name)"
            }
            if (@($fileConfig.Urls).Count -eq 0) {
                throw "At least one download URL is required: $($fileConfig.Name)"
            }
        }
        $null = $storagePath
    }
}

if (-not (Test-Path -LiteralPath $CatalogPath -PathType Leaf)) {
    throw "Native corpus catalog not found: $CatalogPath"
}
$catalog = Import-PowerShellDataFile -LiteralPath $CatalogPath
Assert-Catalog -Catalog $catalog

$requestedIds = @($Dataset | ForEach-Object { $_.ToLowerInvariant() })
if ($requestedIds -contains 'all') {
    $selectedDatasets = @($catalog.Datasets)
}
else {
    $selectedDatasets = @(
        $catalog.Datasets |
            Where-Object { $requestedIds -contains ([string]$_.Id).ToLowerInvariant() }
    )
    $unknownIds = @(
        $requestedIds |
            Where-Object {
                $_ -notin @(
                    $catalog.Datasets |
                        ForEach-Object { ([string]$_.Id).ToLowerInvariant() }
                )
            }
    )
    if ($unknownIds.Count -gt 0) {
        throw "Unknown dataset id(s): $($unknownIds -join ', ')"
    }
}

foreach ($datasetConfig in $selectedDatasets) {
    $storagePath = Get-SafeChildPath `
        -Root $projectRoot `
        -RelativePath ([string]$datasetConfig.Storage)
    $orderedFiles = @(
        $datasetConfig.Files |
            Sort-Object @{
                Expression = {
                    if ([string]$_.Kind -eq 'tar.gz') { 1 } else { 0 }
                }
            }, Name
    )

    if ($Action -eq 'Status') {
        foreach ($fileConfig in $orderedFiles) {
            $status = Get-ConfiguredFileStatus `
                -FileConfig $fileConfig `
                -StoragePath $storagePath
            Write-Output (
                "STATUS [$($datasetConfig.Id)] $($fileConfig.Name): " +
                "$($status.state) ($($status.detail))"
            )
        }
        continue
    }

    if ($Action -in @('Download', 'All')) {
        foreach ($fileConfig in $orderedFiles) {
            Complete-OneFile `
                -DatasetConfig $datasetConfig `
                -FileConfig $fileConfig `
                -StoragePath $storagePath `
                -DownloadIfMissing
        }
        Write-DatasetDownloadMarker `
            -DatasetConfig $datasetConfig `
            -StoragePath $storagePath
    }
    elseif ($Action -eq 'Verify') {
        foreach ($fileConfig in $orderedFiles) {
            Complete-OneFile `
                -DatasetConfig $datasetConfig `
                -FileConfig $fileConfig `
                -StoragePath $storagePath `
                -ForceVerify
        }
        Write-DatasetDownloadMarker `
            -DatasetConfig $datasetConfig `
            -StoragePath $storagePath
    }

    if ($Action -in @('Extract', 'All')) {
        foreach ($fileConfig in $orderedFiles) {
            Extract-OneArchive `
                -DatasetConfig $datasetConfig `
                -FileConfig $fileConfig `
                -StoragePath $storagePath
        }
    }
}
