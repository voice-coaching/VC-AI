[CmdletBinding()]
param(
    [int]$PollSeconds = 30,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$syncScript = Join-Path $PSScriptRoot 'sync_native_korean_corpora.ps1'

Write-Warning (
    'The BITS finalizer is deprecated. Using the checksum/marker-based ' +
    'native corpus pipeline; PollSeconds is retained only for compatibility.'
)
$null = $PollSeconds

& $syncScript `
    -Action Download `
    -Dataset @('fleurs-ko-kr', 'zeroth-korean') `
    -DryRun:$DryRun
