$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$worker = Join-Path $PSScriptRoot 'download_and_finalize.ps1'

# The AI Hub L2 datasets are intentionally absent: they require account verification,
# dataset approval, and an API key. This queue contains only no-login public sources.
$downloads = @(
  [pscustomobject]@{
    Name = 'fleurs-ko-dev'
    Url = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data/ko_kr/audio/dev.tar.gz?download=true'
    RelativePath = 'data\raw\open\fleurs-ko-kr\dev.tar.gz'
    ExpectedBytes = [Int64]126162634
  },
  [pscustomobject]@{
    Name = 'fleurs-ko-test'
    Url = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data/ko_kr/audio/test.tar.gz?download=true'
    RelativePath = 'data\raw\open\fleurs-ko-kr\test.tar.gz'
    ExpectedBytes = [Int64]214425558
  },
  [pscustomobject]@{
    Name = 'seoul-corpus-readme'
    Url = 'https://www.openslr.org/resources/113/readme.tgz'
    RelativePath = 'data\raw\research-noncommercial\seoul-corpus\readme.tgz'
    ExpectedBytes = [Int64]27530017
  },
  [pscustomobject]@{
    Name = 'seoul-corpus-label'
    Url = 'https://www.openslr.org/resources/113/label.tgz'
    RelativePath = 'data\raw\research-noncommercial\seoul-corpus\label.tgz'
    ExpectedBytes = [Int64]57779067
  },
  [pscustomobject]@{
    Name = 'seoul-corpus-sound'
    Url = 'https://www.openslr.org/resources/113/sound.tgz'
    RelativePath = 'data\raw\research-noncommercial\seoul-corpus\sound.tgz'
    ExpectedBytes = [Int64]2589900202
  },
  [pscustomobject]@{
    Name = 'kss-00000'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00000-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00000-of-00007.parquet'
    ExpectedBytes = [Int64]556061209
  },
  [pscustomobject]@{
    Name = 'kss-00001'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00001-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00001-of-00007.parquet'
    ExpectedBytes = [Int64]600367995
  },
  [pscustomobject]@{
    Name = 'kss-00002'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00002-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00002-of-00007.parquet'
    ExpectedBytes = [Int64]585576771
  },
  [pscustomobject]@{
    Name = 'kss-00003'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00003-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00003-of-00007.parquet'
    ExpectedBytes = [Int64]497397025
  },
  [pscustomobject]@{
    Name = 'kss-00004'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00004-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00004-of-00007.parquet'
    ExpectedBytes = [Int64]494763248
  },
  [pscustomobject]@{
    Name = 'kss-00005'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00005-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00005-of-00007.parquet'
    ExpectedBytes = [Int64]530129801
  },
  [pscustomobject]@{
    Name = 'kss-00006'
    Url = 'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/main/data/train-00006-of-00007.parquet?download=true'
    RelativePath = 'data\raw\research-noncommercial\kss\train-00006-of-00007.parquet'
    ExpectedBytes = [Int64]544410674
  }
)

foreach ($download in $downloads) {
  $finalPath = Join-Path $projectRoot $download.RelativePath
  if (Test-Path -LiteralPath $finalPath) {
    Write-Output "[$($download.Name)] already complete"
    continue
  }

  $partialPath = "$finalPath.part"
  & $worker -Name $download.Name -Url $download.Url -PartialPath $partialPath -FinalPath $finalPath -ExpectedBytes $download.ExpectedBytes
}

Write-Output 'PUBLIC_RESEARCH_CORPORA_COMPLETED'
