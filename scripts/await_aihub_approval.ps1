param(
  [int]$DatasetKey = 71490,
  [string]$FileKeys = '475431,475432,475434,475435,475437,475439,475440',
  [int]$RetrySeconds = 300,
  [int]$MaxAttempts = 24
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env.local'
$toolPath = Join-Path $projectRoot 'data\restricted\aihub\bin\aihubshell'
$bashPath = 'C:\Program Files\Git\bin\bash.exe'
$destination = Join-Path $projectRoot "data\restricted\aihub\dataset-$DatasetKey"
$statusPath = Join-Path $destination 'approval-watcher.log'

function Get-AihubApiKey {
  if (-not (Test-Path -LiteralPath $envPath)) {
    throw '.env.local is missing.'
  }

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

if (-not (Test-Path -LiteralPath $toolPath)) { throw "Official aihubshell is missing: $toolPath" }
if (-not (Test-Path -LiteralPath $bashPath)) { throw "Git Bash is missing: $bashPath" }
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$apiKey = Get-AihubApiKey
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
  $attemptLog = Join-Path $destination ("approval-attempt-{0:D2}.log" -f $attempt)
  Write-Status "ATTEMPT $attempt/$MaxAttempts"

  Push-Location $destination
  try {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & $bashPath $toolPath -mode d -datasetkey $DatasetKey -filekey $FileKeys -aihubapikey $apiKey 2>&1
    $nativeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    $output | Out-File -LiteralPath $attemptLog -Encoding utf8
    $outputText = $output | Out-String
  } finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
  }

  if ($outputText -match 'Request successful with HTTP status 200|Download successful') {
    Write-Status 'DOWNLOAD_COMPLETED'
    exit 0
  }

  if ($outputText -match 'HTTP status 502|권한|인증|Authentication') {
    Write-Status 'WAITING_FOR_PORTAL_APPROVAL'
    if ($attempt -lt $MaxAttempts) { Start-Sleep -Seconds $RetrySeconds }
    continue
  }

  Write-Status 'UNEXPECTED_FAILURE'
  throw "AI Hub download returned an unexpected result; see $attemptLog"
}

Write-Status 'APPROVAL_NOT_AVAILABLE_BEFORE_RETRY_LIMIT'
exit 2
