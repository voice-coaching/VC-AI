[CmdletBinding()]
param(
    [string[]]$Dataset = @('all'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$syncScript = Join-Path $PSScriptRoot 'sync_native_korean_corpora.ps1'

# Compatibility entry point. The unified pipeline adds pinned checksums,
# resumable .part files, completion markers, and FLEURS metadata validation.
& $syncScript -Action Download -Dataset $Dataset -DryRun:$DryRun
