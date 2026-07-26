param(
    [string]$DatasetRoot = (Join-Path $PSScriptRoot '../data/restricted/aihub/dataset-71490'),
    [switch]$DeleteArchiveAfterVerify
)

$ErrorActionPreference = 'Stop'
$datasetPath = [System.IO.Path]::GetFullPath($DatasetRoot)
if (-not (Test-Path -LiteralPath $datasetPath -PathType Container)) {
    throw "Dataset root does not exist: $datasetPath"
}

$tarCommand = Get-Command tar -ErrorAction Stop
$tar = $tarCommand.Source
$logRoot = Join-Path $datasetPath 'extraction-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem

$archiveOrder = @(
    'TS_Speech_sound_1.zip',
    'TS_Speech_sound_2.zip',
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
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $prefix = $destinationFull.TrimEnd('\', '/') + $separator
    if (-not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Archive entry escapes destination: $EntryName"
    }
    return $target
}

function Get-ArchiveVerification {
    param(
        [System.IO.FileInfo]$Archive,
        [string]$Destination
    )

    $destinationFull = [System.IO.Path]::GetFullPath($Destination)
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $destinationPrefix = $destinationFull.TrimEnd('\', '/') + $separator
    $fileInventory = [System.Collections.Generic.Dictionary[string, int64]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($file in Get-ChildItem -LiteralPath $destinationFull -File -Recurse) {
        $relativeFile = $file.FullName.Substring($destinationPrefix.Length).Replace('\', '/')
        $fileInventory[$relativeFile] = $file.Length
    }

    $zipFile = [System.IO.Compression.ZipFile]::OpenRead($Archive.FullName)
    try {
        [int64]$expectedBytes = 0
        [int64]$verifiedBytes = 0
        [int64]$requiredAdditionalBytes = 0
        $expectedCount = 0
        $verifiedCount = 0
        $missingCount = 0
        $sizeMismatchCount = 0

        foreach ($entry in $zipFile.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) {
                continue
            }

            $expectedCount++
            $expectedBytes += $entry.Length
            Get-SafeEntryPath -Destination $Destination -EntryName $entry.FullName | Out-Null
            $relativeEntry = $entry.FullName.TrimStart('/', '\').Replace('\', '/')
            [int64]$actualLength = 0
            if (-not $fileInventory.TryGetValue($relativeEntry, [ref]$actualLength)) {
                $missingCount++
                $requiredAdditionalBytes += $entry.Length
                continue
            }

            if ($actualLength -ne $entry.Length) {
                $sizeMismatchCount++
                if ($entry.Length -gt $actualLength) {
                    $requiredAdditionalBytes += ($entry.Length - $actualLength)
                }
                continue
            }

            $verifiedCount++
            $verifiedBytes += $actualLength
        }
    }
    finally {
        $zipFile.Dispose()
    }

    return [pscustomobject]@{
        Complete = (
            $verifiedCount -eq $expectedCount -and
            $verifiedBytes -eq $expectedBytes
        )
        ExpectedCount = $expectedCount
        ExpectedBytes = $expectedBytes
        VerifiedCount = $verifiedCount
        VerifiedBytes = $verifiedBytes
        MissingCount = $missingCount
        SizeMismatchCount = $sizeMismatchCount
        RequiredAdditionalBytes = $requiredAdditionalBytes
    }
}

function Write-CompletionMarker {
    param(
        [System.IO.FileInfo]$Archive,
        [string]$Destination,
        [pscustomobject]$Verification,
        [string]$Marker
    )

    $markerData = [ordered]@{
        archive = $Archive.Name
        destination = $Destination
        files = $Verification.VerifiedCount
        bytes = $Verification.VerifiedBytes
        archive_bytes = $Archive.Length
        verified_at = (Get-Date -Format o)
    }
    $markerData | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding utf8
}

$archiveLookup = @{}
$packageRoots = @(Get-ChildItem -LiteralPath $datasetPath -Directory)
foreach ($packageRoot in $packageRoots) {
    $releaseRoots = @(Get-ChildItem -LiteralPath $packageRoot.FullName -Directory)
    foreach ($releaseRoot in $releaseRoots) {
        foreach ($splitName in @('Training', 'Validation')) {
            $splitDirectory = Join-Path $releaseRoot.FullName $splitName
            if (-not (Test-Path -LiteralPath $splitDirectory -PathType Container)) {
                continue
            }

            foreach ($dataDirectory in Get-ChildItem -LiteralPath $splitDirectory -Directory) {
                foreach ($archive in Get-ChildItem -LiteralPath $dataDirectory.FullName -File -Filter '*.zip') {
                    if (-not ($archiveOrder -contains $archive.Name)) {
                        continue
                    }
                    if ($archiveLookup.ContainsKey($archive.Name)) {
                        throw "Duplicate archive name found: $($archive.Name)"
                    }
                    $archiveLookup[$archive.Name] = $archive
                }
            }
        }
    }
}

foreach ($archiveName in $archiveOrder) {
    if (-not $archiveLookup.ContainsKey($archiveName)) {
        Write-Output "SKIP archive not found: $archiveName"
        continue
    }
    $archive = $archiveLookup[$archiveName]

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

    Write-Output "CHECKING_EXISTING: $archiveName"
    $verification = Get-ArchiveVerification -Archive $archive -Destination $destinationFull
    if ($verification.Complete) {
        Write-CompletionMarker `
            -Archive $archive `
            -Destination $destinationFull `
            -Verification $verification `
            -Marker $marker
        Write-Output "COMPLETE_EXISTING: $archiveName files=$($verification.VerifiedCount) bytes=$($verification.VerifiedBytes)"

        if ($DeleteArchiveAfterVerify) {
            Remove-Item -LiteralPath $archive.FullName -Force
            Write-Output "REMOVED verified archive: $archiveName"
        }
        continue
    }

    $drive = [System.IO.DriveInfo]::new(([System.IO.Path]::GetPathRoot($destinationFull)))
    $minimumFreeBytes = $verification.RequiredAdditionalBytes + 2GB
    if ($drive.AvailableFreeSpace -lt $minimumFreeBytes) {
        throw "Insufficient free space for $archiveName. Need at least $([Math]::Round($minimumFreeBytes / 1GB, 2)) GiB, available $([Math]::Round($drive.AvailableFreeSpace / 1GB, 2)) GiB."
    }

    Write-Output "EXTRACTING: $archiveName expected_files=$($verification.ExpectedCount) verified_files=$($verification.VerifiedCount) missing_files=$($verification.MissingCount) size_mismatches=$($verification.SizeMismatchCount) additional_bytes=$($verification.RequiredAdditionalBytes)"
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
    $verification = Get-ArchiveVerification -Archive $archive -Destination $destinationFull
    if (-not $verification.Complete) {
        throw "Verification failed for $archiveName. Missing=$($verification.MissingCount), size mismatches=$($verification.SizeMismatchCount), verified=$($verification.VerifiedCount)/$($verification.ExpectedCount)"
    }

    Write-CompletionMarker `
        -Archive $archive `
        -Destination $destinationFull `
        -Verification $verification `
        -Marker $marker
    Write-Output "COMPLETE: $archiveName files=$($verification.VerifiedCount) bytes=$($verification.VerifiedBytes)"

    if ($DeleteArchiveAfterVerify) {
        Remove-Item -LiteralPath $archive.FullName -Force
        Write-Output "REMOVED verified archive: $archiveName"
    }
}
