param(
    [string]$DatasetRoot = (Join-Path $PSScriptRoot '../data/restricted/aihub/dataset-71490')
)

$ErrorActionPreference = 'Stop'
$datasetPath = [System.IO.Path]::GetFullPath($DatasetRoot)
if (-not (Test-Path -LiteralPath $datasetPath -PathType Container)) {
    throw "Dataset root does not exist: $datasetPath"
}

$pythonCommand = Get-Command python -ErrorAction Stop
$python = $pythonCommand.Source
$extractor = Join-Path $PSScriptRoot 'extract_verified_zip.py'
if (-not (Test-Path -LiteralPath $extractor -PathType Leaf)) {
    throw "Safe ZIP extractor does not exist: $extractor"
}

$archives = Get-ChildItem -LiteralPath $datasetPath -Recurse -File -Filter '*.zip' |
    Where-Object { $_.BaseName -match '(?i)(lab|tag)' } |
    Sort-Object FullName

foreach ($archive in $archives) {
    $destination = Join-Path $archive.DirectoryName (Join-Path 'extracted' $archive.BaseName)
    $completeMarker = Join-Path $destination '.verified-zip-extraction-complete.json'
    if (Test-Path -LiteralPath $completeMarker -PathType Leaf) {
        Write-Output "SKIP completed: $($archive.Name)"
        continue
    }

    Write-Output "EXTRACTING: $($archive.FullName)"
    & $python $extractor $archive.FullName $destination `
        --strip-leading-separators --no-fsync
    if ($LASTEXITCODE -ne 0) {
        throw "Extraction failed for $($archive.Name), exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $completeMarker -PathType Leaf)) {
        throw "Completion marker was not created for $($archive.Name)"
    }
    Write-Output "COMPLETE: $($archive.Name)"
}
