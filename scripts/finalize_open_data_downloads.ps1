param(
  [int]$PollSeconds = 30
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statusLog = Join-Path $projectRoot 'data\raw\open\download-finalizer.log'

$downloads = @(
  [pscustomobject]@{
    JobName = 'intelligentAI-zeroth-korean'
    ExpectedBytes = [Int64]10339720618
    Partial = 'data\raw\open\zeroth-korean\zeroth_korean.tar.gz.part'
    Final = 'data\raw\open\zeroth-korean\zeroth_korean.tar.gz'
  },
  [pscustomobject]@{
    JobName = 'intelligentAI-fleurs-ko-train'
    ExpectedBytes = [Int64]1420236944
    Partial = 'data\raw\open\fleurs-ko-kr\train.tar.gz.part'
    Final = 'data\raw\open\fleurs-ko-kr\train.tar.gz'
  },
  [pscustomobject]@{
    JobName = 'intelligentAI-fleurs-ko-dev'
    ExpectedBytes = [Int64]126162634
    Partial = 'data\raw\open\fleurs-ko-kr\dev.tar.gz.part'
    Final = 'data\raw\open\fleurs-ko-kr\dev.tar.gz'
  },
  [pscustomobject]@{
    JobName = 'intelligentAI-fleurs-ko-test'
    ExpectedBytes = [Int64]214425558
    Partial = 'data\raw\open\fleurs-ko-kr\test.tar.gz.part'
    Final = 'data\raw\open\fleurs-ko-kr\test.tar.gz'
  }
)

function Write-Status([string]$Message) {
  $line = "$(Get-Date -Format o) $Message"
  Add-Content -LiteralPath $statusLog -Value $line
}

while ($true) {
  $remaining = 0

  foreach ($download in $downloads) {
    $finalPath = Join-Path $projectRoot $download.Final
    if (Test-Path -LiteralPath $finalPath) {
      continue
    }

    $job = Get-BitsTransfer -Name $download.JobName -ErrorAction SilentlyContinue
    if ($null -eq $job) {
      Write-Status "ERROR missing BITS job: $($download.JobName)"
      throw "Missing BITS job: $($download.JobName)"
    }

    if ($job.JobState -eq 'Error') {
      Write-Status "ERROR $($download.JobName): $($job.JobState) $($job.Error.Description)"
      throw "BITS job failed: $($download.JobName)"
    }

    if ($job.JobState -eq 'TransientError') {
      Write-Status "RETRYING $($download.JobName): $($job.Error.Description)"
      $remaining++
      continue
    }

    if ($job.JobState -ne 'Transferred') {
      $remaining++
      continue
    }

    Complete-BitsTransfer -BitsJob $job
    $partialPath = Join-Path $projectRoot $download.Partial
    $downloadedFile = Get-Item -LiteralPath $partialPath -ErrorAction Stop
    if ($downloadedFile.Length -ne $download.ExpectedBytes) {
      Write-Status "ERROR byte mismatch for $($download.JobName): $($downloadedFile.Length) != $($download.ExpectedBytes)"
      throw "Byte mismatch for $($download.JobName)"
    }

    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Write-Status "COMPLETED $($download.JobName) $($downloadedFile.Length) bytes"
  }

  if ($remaining -eq 0) {
    Write-Status 'ALL_DOWNLOADS_COMPLETED'
    exit 0
  }

  Start-Sleep -Seconds $PollSeconds
}
