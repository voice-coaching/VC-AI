param(
  [Parameter(Mandatory = $true)] [int]$DatasetKey,
  [Parameter(Mandatory = $true)] [string]$FileKeys
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'aihub538_p8_guard.ps1')
Assert-NoAihub538P8GenericFileKey -FileKeys $FileKeys
if ($DatasetKey -eq 538) {
  throw 'Dataset 538 is fail-closed in this legacy downloader. Use the typed Python label-audit, V5 source-prefix, or P8 one-shot entrypoint.'
}
$shell = Get-Command aihubshell -ErrorAction SilentlyContinue
if ($null -eq $shell) {
  throw 'aihubshell was not found. Install the official AI Hub Shell after the portal approval is complete.'
}
if ([string]::IsNullOrWhiteSpace($env:AIHUB_API_KEY)) {
  throw 'Set AIHUB_API_KEY in your local environment. Do not paste the key into chat or commit it to the repository.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $projectRoot "data\restricted\aihub\dataset-$DatasetKey"
New-Item -ItemType Directory -Force -Path $destination | Out-Null

Push-Location $destination
try {
  & $shell.Source -mode d -datasetkey $DatasetKey -filekey $FileKeys -aihubapikey $env:AIHUB_API_KEY
  if ($LASTEXITCODE -ne 0) {
    throw "AI Hub download failed for dataset $DatasetKey. Check account approval, API key, and selected file keys."
  }
} finally {
  Pop-Location
}
