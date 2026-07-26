param(
    [string]$DatasetRoot = (Join-Path $PSScriptRoot '..\data\restricted\aihub\dataset-71490'),
    [switch]$DeleteArchiveAfterVerify
)

$ErrorActionPreference = 'Stop'
$datasetPath = [System.IO.Path]::GetFullPath($DatasetRoot)
if (-not (Test-Path -LiteralPath $datasetPath -PathType Container)) {
    throw "Dataset root does not exist: $datasetPath"
}

$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$logRoot = Join-Path $datasetPath 'extraction-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem

$archiveOrder = @(
    'TS_pronunciation_sound_1.zip',
    'TS_pronunciation_sound_2.zip',
    'VS_pronunciation_sound.zip',
    'VS_Speech_sound.zip'
)

function Get-DestinationPath {
    param([System.IO.FileInfo]$Archive)

    if ($Archive.BaseName -match '(?i)pronunciation') {
        return Join-Path $Archive.DirectoryName 'extracted_pronunciation'
    }
    return Join-Path $Archive.DirectoryName 'extracted'
}

function Get-SafeEntryPath {
    param(
        [string]$Destination,
        [string]$EntryName
    )

    $relative = $EntryName.TrimStart('/', '\')
    if ([string]::IsNullOrWhiteSpace($relative)) {
        return $null
    }
    if ($relative -split '[/\\]' | Where-Object { $_ -eq '..' }) {
        throw "Unsafe archive entry: $EntryName"
    }

    $destinationFull = [System.IO.Path]::GetFullPath($Destination)
    $target = [System.IO.Path]::GetFullPath((Join-Path $destinationFull $relative))
    $prefix = $destinationFull.TrimEnd('\') + '\'
    if (-not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Archive entry escapes destination: $EntryName"
    }
    return $target
}

foreach ($archiveName in $archiveOrder) {
    $archive = Get-ChildItem -LiteralPath $datasetPath -Recurse -File -Filter $archiveName |
        Select-Object -First 1
    if (-not $archive) {
        Write-Output "SKIP archive not found: $archiveName"
        continue
    }

    $destination = Get-DestinationPath -Archive $archive
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    if (-not $destinationFull.StartsWith($datasetPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Extraction destination escapes dataset root: $destinationFull"
    }
    New-Item -ItemType Directory -Force -Path $destinationFull | Out-Null

    $marker = Join-Path $logRoot ($archive.BaseName + '.audio-extraction-complete.json')
    if (Test-Path -LiteralPath $marker) {
        Write-Output "SKIP completed: $archiveName"
        if ($DeleteArchiveAfterVerify -and (Test-Path -LiteralPath $archive.FullName)) {
            Remove-Item -LiteralPath $archive.FullName -Force
            Write-Output "REMOVED verified archive: $archiveName"
        }
        continue
    }

    $zipFile = [System.IO.Compression.ZipFile]::OpenRead($archive.FullName)
    try {
        $entries = @($zipFile.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) })
        $expectedCount = $entries.Count
        $expectedBytes = ($entries | Measure-Object -Property Length -Sum).Sum
    }
    finally {
        $zipFile.Dispose()
    }

    $drive = [System.IO.DriveInfo]::new(([System.IO.Path]::GetPathRoot($destinationFull)))
    if ($drive.AvailableFreeSpace -lt ($expectedBytes + 2GB)) {
        throw "Insufficient free space for $archiveName. Need at least $([Math]::Round(($expectedBytes + 2GB) / 1GB, 2)) GiB, available $([Math]::Round($drive.AvailableFreeSpace / 1GB, 2)) GiB."
    }

    Write-Output "EXTRACTING: $archiveName files=$expectedCount bytes=$expectedBytes"
    $stderr = Join-Path $logRoot ($archive.BaseName + '.audio-extraction.stderr.log')
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $tar -xf $archive.FullName -C $destinationFull 2>> $stderr
    $tarExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($tarExitCode -ne 0) {
        throw "Extraction failed for $archiveName, exit code $tarExitCode"
    }

    Write-Output "VERIFYING: $archiveName"
    $zipFile = [System.IO.Compression.ZipFile]::OpenRead($archive.FullName)
    try {
        $verifiedCount = 0
        [int64]$verifiedBytes = 0
        foreach ($entry in $zipFile.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) {
                continue
            }
            $target = Get-SafeEntryPath -Destination $destinationFull -EntryName $entry.FullName
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
                throw "Missing extracted file for $archiveName : $($entry.FullName)"
            }
            $actualLength = (Get-Item -LiteralPath $target).Length
            if ($actualLength -ne $entry.Length) {
                throw "Size mismatch for $archiveName : $($entry.FullName), expected $($entry.Length), actual $actualLength"
            }
            $verifiedCount++
            $verifiedBytes += $actualLength
        }
    }
    finally {
        $zipFile.Dispose()
    }

    if ($verifiedCount -ne $expectedCount -or $verifiedBytes -ne $expectedBytes) {
        throw "Verification totals failed for $archiveName"
    }

    $markerData = [ordered]@{
        archive = $archiveName
        destination = $destinationFull
        files = $verifiedCount
        bytes = $verifiedBytes
        archive_bytes = $archive.Length
        verified_at = (Get-Date -Format o)
    }
    $markerData | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding utf8
    Write-Output "COMPLETE: $archiveName files=$verifiedCount bytes=$verifiedBytes"

    if ($DeleteArchiveAfterVerify) {
        Remove-Item -LiteralPath $archive.FullName -Force
        Write-Output "REMOVED verified archive: $archiveName"
    }
}
