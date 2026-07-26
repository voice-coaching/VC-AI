param(
    [string]$DatasetRoot = (Join-Path $PSScriptRoot '../data/restricted/aihub/dataset-71490')
)

$ErrorActionPreference = 'Stop'
$tarCommand = Get-Command tar -ErrorAction Stop
$tar = $tarCommand.Source
$logRoot = Join-Path $DatasetRoot 'extraction-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$archives = Get-ChildItem -LiteralPath $DatasetRoot -Recurse -File -Filter '*.zip' |
    Where-Object {
        $_.BaseName -match '(?i)(lab|tag)'
    } |
    Sort-Object FullName

foreach ($archive in $archives) {
    $destination = Join-Path $archive.DirectoryName (Join-Path 'extracted' $archive.BaseName)
    $completeMarker = Join-Path $destination '.extraction-complete'
    if (Test-Path -LiteralPath $completeMarker) {
        Write-Output "SKIP completed: $($archive.Name)"
        continue
    }

    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipFile = [System.IO.Compression.ZipFile]::OpenRead($archive.FullName)
    try {
        $expectedFiles = $zipFile.Entries.Count
    }
    finally {
        $zipFile.Dispose()
    }
    $actualFiles = @(Get-ChildItem -LiteralPath $destination -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.extraction-complete' }).Count
    if ($actualFiles -eq $expectedFiles) {
        Set-Content -LiteralPath $completeMarker -Value (Get-Date -Format o) -NoNewline
        Write-Output "RECOVERED completed: $($archive.Name)"
        continue
    }

    $stderr = Join-Path $logRoot ($archive.BaseName + '.stderr.log')
    Write-Output "EXTRACTING: $($archive.FullName)"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $tar -xf $archive.FullName -C $destination 2>> $stderr
    $tarExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($tarExitCode -ne 0) {
        throw "Extraction failed for $($archive.Name), exit code $tarExitCode"
    }

    Set-Content -LiteralPath $completeMarker -Value (Get-Date -Format o) -NoNewline
    Write-Output "COMPLETE: $($archive.Name)"
}
