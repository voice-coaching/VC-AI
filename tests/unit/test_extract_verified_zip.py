from pathlib import Path
import subprocess
import sys
import zipfile


SCRIPT = Path(__file__).parents[2] / "scripts" / "extract_verified_zip.py"


def test_verified_zip_extraction_skips_macos_metadata(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("audio/a.flac", b"audio")
        writer.writestr("__MACOSX/._a.flac", b"metadata")
    destination = tmp_path / "out"
    subprocess.run(
        [sys.executable, str(SCRIPT), str(archive), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (destination / "audio" / "a.flac").read_bytes() == b"audio"
    assert not (destination / "__MACOSX").exists()
    assert (destination / ".verified-zip-extraction-complete.json").is_file()


def test_verified_zip_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("../escaped", b"bad")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(archive), str(tmp_path / "out")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "escaped").exists()


def test_verified_zip_can_normalize_explicit_leading_separator(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "aihub.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("/label.json", b"{}")
    destination = tmp_path / "out"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(archive),
            str(destination),
            "--strip-leading-separators",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (destination / "label.json").read_bytes() == b"{}"
