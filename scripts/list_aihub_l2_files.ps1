param(
  [int[]]$DatasetKeys = @(71490, 71469, 71479, 71489, 505)
)

$ErrorActionPreference = 'Stop'
$shell = Get-Command aihubshell -ErrorAction SilentlyContinue
if ($null -eq $shell) {
  throw 'aihubshell was not found. Install the official AI Hub Shell after the portal approval is complete.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$inventoryRoot = Join-Path $projectRoot 'data\restricted\aihub\inventory'
New-Item -ItemType Directory -Force -Path $inventoryRoot | Out-Null

foreach ($datasetKey in $DatasetKeys) {
  $outputPath = Join-Path $inventoryRoot "dataset-$datasetKey-files.txt"
  & $shell.Source -mode l -datasetkey $datasetKey 2>&1 | Tee-Object -FilePath $outputPath
  if ($LASTEXITCODE -ne 0) {
    throw "AI Hub file listing failed for dataset $datasetKey. Confirm that the portal approval is complete."
  }
}
