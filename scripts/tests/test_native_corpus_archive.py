from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_corpus_archive  # noqa: E402


class NativeCorpusArchiveTest(unittest.TestCase):
    def test_fleurs_pair_and_resumable_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "dev.tar.gz"
            tsv_path = root / "dev.tsv"
            destination = root / "extracted"
            audio_name = "123.wav"
            audio_bytes = b"RIFF-test-audio"

            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo(f"dev/{audio_name}")
                member.size = len(audio_bytes)
                archive.addfile(member, io.BytesIO(audio_bytes))

            tsv_path.write_text(
                "\t".join(
                    [
                        "1",
                        audio_name,
                        "전사",
                        "원문",
                        "ㅈ ㅓ ㄴ ㅅ ㅏ",
                        "100",
                        "FEMALE",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = native_corpus_archive.validate_fleurs_pair(
                archive_path, tsv_path, 1, None
            )
            self.assertTrue(result["metadata_audio_match"])

            first = native_corpus_archive.extract_archive(
                archive_path, destination, None
            )
            self.assertEqual(first["newly_extracted_files"], 1)
            second = native_corpus_archive.extract_archive(
                archive_path, destination, None
            )
            self.assertEqual(second["resumed_files"], 1)

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("../escape.txt")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))

            with self.assertRaisesRegex(ValueError, "path traversal"):
                native_corpus_archive.inspect_archive(archive_path, None)


if __name__ == "__main__":
    unittest.main()
