param(
  [Parameter(Mandatory = $true)] [int]$DatasetKey,
  [Parameter(Mandatory = $true)] [int]$FileKey,
  [Parameter(Mandatory = $true)] [string]$ExpectedFileName
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env.local'
$destination = Join-Path $projectRoot "data\restricted\aihub\dataset-$DatasetKey"
$archivePath = Join-Path $destination "file-$FileKey.tar"
$statusPath = Join-Path $destination "file-$FileKey-resume.log"

function Get-AihubApiKey {
  foreach ($line in Get-Content -LiteralPath $envPath) {
    $index = $line.IndexOf('=')
    if ($index -lt 0) { continue }
    $name = $line.Substring(0, $index).Trim().Trim([char]0xFEFF)
    if ($name -eq 'AIHUB_API_KEY') {
      $value = $line.Substring($index + 1).Trim().Trim('"').Trim("'")
      if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
  }
  throw 'AIHUB_API_KEY is missing or empty in .env.local.'
}

function Write-Status([string]$Message) {
  Add-Content -LiteralPath $statusPath -Value "$(Get-Date -Format o) $Message"
}

New-Item -ItemType Directory -Force -Path $destination | Out-Null
$apiKey = Get-AihubApiKey
$url = "https://api.aihub.or.kr/down/0.6/$DatasetKey.do?fileSn=$FileKey"

Write-Status "DOWNLOADING filekey=$FileKey"
& curl.exe --location --fail --retry 20 --retry-all-errors --retry-delay 10 --continue-at - --output $archivePath --header "apikey:$apiKey" $url
if ($LASTEXITCODE -ne 0) {
  Write-Status "CURL_FAILED exit=$LASTEXITCODE; partial archive retained for resume"
  exit $LASTEXITCODE
}

Write-Status 'EXTRACTING_ARCHIVE'
& tar.exe -xf $archivePath -C $destination *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Status "TAR_FAILED exit=$LASTEXITCODE; archive retained"
  exit $LASTEXITCODE
}

$partPattern = "^{0}\.part(?<index>\d+)$" -f [regex]::Escape($ExpectedFileName)
$parts = Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object { $_.Name -match $partPattern }
if ($parts.Count -eq 0) {
  $finalCandidate = Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object { $_.Name -eq $ExpectedFileName } | Select-Object -First 1
  if ($null -eq $finalCandidate) { throw "Expected output not found: $ExpectedFileName" }
  Write-Status "COMPLETED direct-file $($finalCandidate.Length) bytes"
  exit 0
}

$groups = $parts | Group-Object DirectoryName
foreach ($group in $groups) {
  $orderedParts = $group.Group | Sort-Object { [int]([regex]::Match($_.Name, $partPattern).Groups['index'].Value) }
  $outputPath = Join-Path $group.Name $ExpectedFileName
  if (Test-Path -LiteralPath $outputPath) { throw "Refusing to overwrite existing output: $outputPath" }

  $outputStream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
  try {
    foreach ($part in $orderedParts) {
      $inputStream = [System.IO.File]::OpenRead($part.FullName)
      try { $inputStream.CopyTo($outputStream) } finally { $inputStream.Dispose() }
    }
  } finally {
    $outputStream.Dispose()
  }

  foreach ($part in $orderedParts) { Remove-Item -LiteralPath $part.FullName -Force }
  Write-Status "COMPLETED merged-file $outputPath"
}
