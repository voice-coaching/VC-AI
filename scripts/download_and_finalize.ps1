param(
  [Parameter(Mandatory = $true)] [string]$Name,
  [Parameter(Mandatory = $true)] [string]$Url,
  [Parameter(Mandatory = $true)] [string]$PartialPath,
  [Parameter(Mandatory = $true)] [string]$FinalPath,
  [Parameter(Mandatory = $true)] [Int64]$ExpectedBytes
)

$ErrorActionPreference = 'Stop'

if (Test-Path -LiteralPath $FinalPath) {
  throw "Refusing to overwrite an existing completed file: $FinalPath"
}

$partialDirectory = Split-Path -Parent $PartialPath
New-Item -ItemType Directory -Force -Path $partialDirectory | Out-Null

Write-Output "[$Name] downloading"
& curl.exe --location --fail --retry 20 --retry-all-errors --retry-delay 10 --continue-at - --output $PartialPath $Url
if ($LASTEXITCODE -ne 0) {
  throw "[$Name] curl failed with exit code $LASTEXITCODE"
}

$downloadedFile = Get-Item -LiteralPath $PartialPath -ErrorAction Stop
if ($downloadedFile.Length -ne $ExpectedBytes) {
  throw "[$Name] byte mismatch: $($downloadedFile.Length) != $ExpectedBytes"
}

Move-Item -LiteralPath $PartialPath -Destination $FinalPath
Write-Output "[$Name] completed: $ExpectedBytes bytes"
