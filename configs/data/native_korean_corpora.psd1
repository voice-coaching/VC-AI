@{
    SchemaVersion = 1
    Datasets = @(
        @{
            Id = 'fleurs-ko-kr'
            DisplayName = 'Google FLEURS ko-KR'
            License = 'CC-BY-4.0'
            Purpose = 'native Korean ASR/alignment external evaluation'
            Storage = 'data/raw/open/fleurs-ko-kr'
            Files = @(
                @{
                    Name = 'train.tar.gz'
                    Bytes = [Int64]1420236944
                    Sha256 = '985d45a1ab3967f6851761bac8f7f325360eba3381ffed96f9ce920d3c24e9d9'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/train'
                    PairTsv = 'train.tsv'
                    ExpectedRows = 2307
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/audio/train.tar.gz?download=true'
                    )
                }
                @{
                    Name = 'dev.tar.gz'
                    Bytes = [Int64]126162634
                    Sha256 = '496edcb5323e75b4a2830f5b5623684a0baf86d3728101853fd4fe503372157c'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/dev'
                    PairTsv = 'dev.tsv'
                    ExpectedRows = 226
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/audio/dev.tar.gz?download=true'
                    )
                }
                @{
                    Name = 'test.tar.gz'
                    Bytes = [Int64]214425558
                    Sha256 = '3489e529f2aad18d3357b746c5f955941d258b79dc60d8a102d5bced2a223184'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/test'
                    PairTsv = 'test.tsv'
                    ExpectedRows = 382
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/audio/test.tar.gz?download=true'
                    )
                }
                @{
                    Name = 'train.tsv'
                    Bytes = [Int64]1249277
                    Sha256 = '5a943f1fdbb8534c09ac8a81d94418c0f04b030d56a1485ec72f57a9a34d7a58'
                    ChecksumSource = 'SHA-256 of the pinned Hugging Face revision'
                    Kind = 'fleurs-tsv'
                    ExpectedRows = 2307
                    PairedArchive = 'train.tar.gz'
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/train.tsv?download=true'
                    )
                }
                @{
                    Name = 'dev.tsv'
                    Bytes = [Int64]124920
                    Sha256 = '6b236de107c6a1672233f6d710d26adfdb55570a3e6e35aca9dc4ff2be01cea4'
                    ChecksumSource = 'SHA-256 of the pinned Hugging Face revision'
                    Kind = 'fleurs-tsv'
                    ExpectedRows = 226
                    PairedArchive = 'dev.tar.gz'
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/dev.tsv?download=true'
                    )
                }
                @{
                    Name = 'test.tsv'
                    Bytes = [Int64]211844
                    Sha256 = 'cf2f7c8765f6203e3c46ef620d5e936d3f331b6e8865455557777dd1347517f5'
                    ChecksumSource = 'SHA-256 of the pinned Hugging Face revision'
                    Kind = 'fleurs-tsv'
                    ExpectedRows = 382
                    PairedArchive = 'test.tar.gz'
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr/test.tsv?download=true'
                    )
                }
                @{
                    Name = 'README.source.md'
                    Bytes = [Int64]385614
                    Sha256 = '688f79f2a5c731af3796e9f683eb02f9b3f09d040decd8c5625d0f37098e71c6'
                    ChecksumSource = 'SHA-256 of the pinned Hugging Face revision'
                    Kind = 'license-evidence'
                    RequiredText = @(
                        'cc-by-4.0'
                        'Creative Commons license (CC-BY)'
                        'FLEURS'
                    )
                    Urls = @(
                        'https://huggingface.co/datasets/google/fleurs/resolve/70bb2e84b976b7e960aa89f1c648e09c59f894dd/README.md?download=true'
                    )
                }
            )
        }
        @{
            Id = 'kss'
            DisplayName = 'KSS Dataset'
            License = 'CC-BY-NC-SA-4.0'
            Purpose = 'research-only native Korean TTS baseline'
            Storage = 'data/raw/research-noncommercial/kss'
            Files = @(
                @{
                    Name = 'train-00000-of-00007.parquet'
                    Bytes = [Int64]556061209
                    Sha256 = '31ad648360f30ac4a21e89a22ee88ca18198a5a57e184afd63c2cc285190a214'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00000-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00001-of-00007.parquet'
                    Bytes = [Int64]600367995
                    Sha256 = '191fc43952838bce02c6f727962fb64f854d59eed8fa4f308ac1ff3bf66c0d6d'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00001-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00002-of-00007.parquet'
                    Bytes = [Int64]585576771
                    Sha256 = '2e016c5d4754b9b57fba3923ab30eb47910715f932b525a40f69625fe101ec02'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00002-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00003-of-00007.parquet'
                    Bytes = [Int64]497397025
                    Sha256 = 'c058952c8ea84071467b2bf86cf951bf129a94ca794101d8be1ca51a25a934e4'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00003-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00004-of-00007.parquet'
                    Bytes = [Int64]494763248
                    Sha256 = '9395a49de11ed44e361459d9dbf707653ef15b947ab49eaf9ea0967290ee35ff'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00004-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00005-of-00007.parquet'
                    Bytes = [Int64]530129801
                    Sha256 = 'f2865c8483ad4ba34e65cb4ea18d0dfb4c87be2902c01c42170d831fbe20ee88'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00005-of-00007.parquet?download=true'
                    )
                }
                @{
                    Name = 'train-00006-of-00007.parquet'
                    Bytes = [Int64]544410674
                    Sha256 = '89e1fb82efaac11cda47642e94280f5369172d4da029bef333f8e0049c76c2dd'
                    ChecksumSource = 'Hugging Face X-Linked-ETag (SHA-256)'
                    Kind = 'parquet'
                    Urls = @(
                        'https://huggingface.co/datasets/Bingsu/KSS_Dataset/resolve/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/data/train-00006-of-00007.parquet?download=true'
                    )
                }
            )
        }
        @{
            Id = 'seoul-corpus'
            DisplayName = 'Seoul Corpus'
            License = 'CC-BY-NC-2.0'
            Purpose = 'research-only native Korean phone alignment and prosody reference'
            Storage = 'data/raw/research-noncommercial/seoul-corpus'
            Files = @(
                @{
                    Name = 'readme.tgz'
                    Bytes = [Int64]27530017
                    GzipCrc32 = 'daad9b38'
                    ChecksumSource = 'gzip CRC32 from the OpenSLR SLR113 distribution'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/readme'
                    Urls = @(
                        'https://www.openslr.org/resources/113/readme.tgz'
                        'https://openslr.trmal.net/resources/113/readme.tgz'
                        'https://openslr.elda.org/resources/113/readme.tgz'
                    )
                }
                @{
                    Name = 'label.tgz'
                    Bytes = [Int64]57779067
                    GzipCrc32 = '90a61aa0'
                    ChecksumSource = 'gzip CRC32 from the OpenSLR SLR113 distribution'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/label'
                    Urls = @(
                        'https://www.openslr.org/resources/113/label.tgz'
                        'https://openslr.trmal.net/resources/113/label.tgz'
                        'https://openslr.elda.org/resources/113/label.tgz'
                    )
                }
                @{
                    Name = 'sound.tgz'
                    Bytes = [Int64]2589900202
                    GzipCrc32 = '30b6be1e'
                    ChecksumSource = 'gzip CRC32 from the OpenSLR SLR113 distribution'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted/sound'
                    Urls = @(
                        'https://www.openslr.org/resources/113/sound.tgz'
                        'https://openslr.trmal.net/resources/113/sound.tgz'
                        'https://openslr.elda.org/resources/113/sound.tgz'
                    )
                }
            )
        }
        @{
            Id = 'zeroth-korean'
            DisplayName = 'ZEROTH-Korean'
            License = 'CC-BY-4.0'
            Purpose = 'native Korean ASR/alignment baseline'
            Storage = 'data/raw/open/zeroth-korean'
            Files = @(
                @{
                    Name = 'zeroth_korean.tar.gz'
                    Bytes = [Int64]10339720618
                    GzipCrc32 = '927b7f23'
                    ChecksumSource = 'gzip CRC32 from the OpenSLR SLR40 distribution'
                    Kind = 'tar.gz'
                    ExtractDirectory = 'extracted'
                    LegacyCandidates = @('BIT*.tmp')
                    Urls = @(
                        'https://www.openslr.org/resources/40/zeroth_korean.tar.gz'
                        'https://openslr.trmal.net/resources/40/zeroth_korean.tar.gz'
                        'https://openslr.elda.org/resources/40/zeroth_korean.tar.gz'
                    )
                }
            )
        }
    )
}
