"""Deterministic, offline draft-PDF builder for the canonical paper.

The module deliberately separates structural config-only validation from all
source, tool, and output access. A production build renders a strictly
sanitized Markdown document twice through isolated LibreOffice profiles under
``strace``, blanks only LibreOffice's fixed-width volatile PDF tokens, validates
both PDFs with Ghostscript's ``nullpage`` device, and publishes only when the
losslessly canonicalized bytes and all Poppler postflight artifacts agree.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time
from typing import Any, Final, Iterator, Mapping, NoReturn, Sequence
import unicodedata
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zlib

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA: Final = "voice_coach.paper_draft_pdf_config.v1"
RECEIPT_SCHEMA: Final = "voice_coach.paper_draft_pdf_receipt.v1"
POSTFLIGHT_SCHEMA: Final = "voice_coach.paper_draft_pdf_postflight.v1"
COMMIT_SCHEMA: Final = "voice_coach.paper_draft_pdf_commit.v1"

CANONICAL_CONFIG: Final = "configs/paper/draft_pdf_v1.yaml"
CANONICAL_OUTPUT_ROOT: Final = "artifacts/reproducibility/paper_draft_pdf"
CANONICAL_COMMIT: Final = "artifacts/reproducibility/paper_draft_pdf/commit.json"
CANONICAL_LOCK: Final = "artifacts/reproducibility/.paper_draft_pdf.publish.lock"
CANONICAL_MODULE: Final = "src/voice_coach/reproducibility/paper_draft_pdf.py"
CANONICAL_CLI: Final = "scripts/build_paper_draft_pdf.py"
CANONICAL_INPUTS: Final = {
    "manuscript": "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "evidence_report": (
        "artifacts/reproducibility/manuscript_evidence_validation.json"
    ),
    "publication_commit": ("artifacts/reproducibility/paper_publication/commit.json"),
    "semantic_commit": ("artifacts/reproducibility/p04_p05_gap_validation/commit.json"),
    "cohort_svg": "docs/paper/figures/aihub71490_cohort_flow.svg",
    "cohort_receipt": (
        "artifacts/reproducibility/paper_figures/aihub71490_cohort_flow_v1.json"
    ),
    "cohort_renderer": ("src/voice_coach/reproducibility/cohort_flow_svg.py"),
    "cohort_cli": "scripts/render_aihub71490_cohort_flow.py",
}
CANONICAL_TOOLS: Final = {
    "libreoffice": "/usr/lib/libreoffice/program/soffice",
    "strace": "/usr/bin/strace",
    "ghostscript": "/usr/bin/gs",
    "pdfinfo": "/usr/bin/pdfinfo",
    "pdffonts": "/usr/bin/pdffonts",
    "pdftotext": "/usr/bin/pdftotext",
    "pdftoppm": "/usr/bin/pdftoppm",
}
INPUT_KEYS: Final = tuple(CANONICAL_INPUTS)
TOOL_KEYS: Final = tuple(CANONICAL_TOOLS)
GENERATION_FILES: Final = (
    "draft.pdf",
    "extracted-layout.txt",
    "extracted-semantic.txt",
    "extracted-table.txt",
    "postflight.json",
    "receipt.json",
    "pages",
)
ROOT_MODE: Final = 0o750
GENERATIONS_MODE: Final = 0o750
GENERATION_MODE: Final = 0o550
PAGES_MODE: Final = 0o550
FILE_MODE: Final = 0o440
LOCK_MODE: Final = 0o600
HISTORY_MODE: Final = 0o750
HISTORY_SUFFIX: Final = "_history_unvalidated"
MAX_SOURCE_BYTES: Final = 16 * 1024 * 1024
MAX_TOOL_BYTES: Final = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES: Final = 256 * 1024 * 1024
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
GENERATION_RE: Final = SHA256_RE
PAGE_RE: Final = re.compile(r"^page-(\d{3})\.png$")
PENDING_RE: Final = re.compile(r"\[PENDING:[^\]]+\]")
REFERENCE_RE: Final = re.compile(r"(?m)^(\d+)\.\s+(.+)")
INLINE_LINK_RE: Final = re.compile(r"(?<!!)\[([^\]\n]*)\]\(([^)\n]*)\)")
INLINE_IMAGE_RE: Final = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
HTML_TAG_RE: Final = re.compile(
    r"<!--|-->|<![A-Za-z]|\?xml|</?[A-Za-z][^>]*>",
    flags=re.IGNORECASE,
)
ALLOWED_MARKER_COMMENTS: Final = {
    "<!-- BEGIN WAVLM_S103_RESULTS_TABLE -->",
    "<!-- END WAVLM_S103_RESULTS_TABLE -->",
}
COHORT_SOURCE_LINK: Final = "figures/aihub71490_cohort_flow.mmd"
COHORT_MARKDOWN_IMAGE: Final = (
    "![AI Hub 71490 train/dev cohort와 G0 흐름](figures/aihub71490_cohort_flow.svg)"
)
COHORT_PLACEHOLDER: Final = "VOICE_COACH_COHORT_FIGURE_PLACEHOLDER_V1"
COHORT_RESOURCE: Final = "resources/aihub71490_cohort_flow.svg"
XMP_CREATOR_TOOL: Final = "UnknownApplication"
XMP_TOOLKIT: Final = "XMP toolkit 2.9.1-13, framework 1.6"
NETWORK_SYSCALLS: Final = {
    "accept",
    "accept4",
    "bind",
    "connect",
    "getpeername",
    "getsockname",
    "getsockopt",
    "listen",
    "recvmmsg",
    "recvfrom",
    "recvmsg",
    "sendmmsg",
    "sendmsg",
    "sendto",
    "setsockopt",
    "shutdown",
    "socket",
    "socketpair",
}
PDF_PAGE_BOX_NAMES: Final = (
    "MediaBox",
    "CropBox",
    "BleedBox",
    "TrimBox",
    "ArtBox",
)
COHORT_PALETTE: Final = {
    "#fde8e8": (253, 232, 232),
    "#e4f5e9": (228, 245, 233),
    "#fff4cc": (255, 244, 204),
    "#f2e8ff": (242, 232, 255),
}
COHORT_COLOR_TOLERANCE: Final = 10
COHORT_COLOR_MIN_PIXELS: Final = 64
COHORT_BBOX_MIN_WIDTH_RATIO: Final = 0.40
COHORT_BBOX_MIN_HEIGHT_RATIO: Final = 0.15
NETWORK_POSTFLIGHT_STAGES: Final = (
    "pdfinfo-summary",
    "pdfinfo-page-boxes",
    "pdfinfo-javascript",
    "pdfinfo-metadata",
    "pdfinfo-urls",
    "pdffonts",
    "pdftotext-layout",
    "pdftotext-semantic",
    "pdftotext-table",
    "pdftoppm",
)
NETWORK_ALL_STAGES: Final = (
    *(f"tool-version-{key}" for key in TOOL_KEYS),
    "libreoffice-build-1",
    "libreoffice-build-2",
    "ghostscript-validate-1",
    "ghostscript-validate-2",
    *NETWORK_POSTFLIGHT_STAGES,
)
FORBIDDEN_PATH_PARTS: Final = {
    ".env",
    ".env.local",
    "credential",
    "credentials",
    "locked",
    "locked_test",
    "raw",
    "restricted",
    "secret",
    "secrets",
}
TOP_LEVEL_TOKEN_ALLOWLIST: Final = {
    "blockquote_close",
    "blockquote_open",
    "bullet_list_close",
    "bullet_list_open",
    "heading_close",
    "heading_open",
    "inline",
    "list_item_close",
    "list_item_open",
    "ordered_list_close",
    "ordered_list_open",
    "paragraph_close",
    "paragraph_open",
    "table_close",
    "table_open",
    "tbody_close",
    "tbody_open",
    "td_close",
    "td_open",
    "th_close",
    "th_open",
    "thead_close",
    "thead_open",
    "tr_close",
    "tr_open",
}
INLINE_TOKEN_ALLOWLIST: Final = {
    "code_inline",
    "em_close",
    "em_open",
    "hardbreak",
    "link_close",
    "link_open",
    "softbreak",
    "strong_close",
    "strong_open",
    "text",
}
HTML_TAG_ALLOWLIST: Final = {
    "a",
    "blockquote",
    "body",
    "br",
    "code",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "head",
    "html",
    "img",
    "li",
    "meta",
    "ol",
    "p",
    "span",
    "strong",
    "style",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "title",
    "tr",
    "ul",
}
HTML_ATTR_ALLOWLIST: Final = {
    "html": {"lang"},
    "meta": {"charset"},
    "a": {"href"},
    "img": {"alt", "height", "src", "width"},
    "ol": {"start"},
    "table": {"class", "width"},
    "td": {"class"},
}
TABLE_ROW_CELL_SEPARATOR: Final = "␟"


class PaperDraftPdfError(RuntimeError):
    """Fail-closed error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ToolRecord:
    path: str
    sha256: str
    size_bytes: int
    version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "version": self.version,
        }


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    nlink: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class FileSnapshot:
    record: FileRecord
    identity: FileIdentity
    payload: bytes
    absolute_path: Path


@dataclass(frozen=True)
class ToolSnapshot:
    record: ToolRecord
    identity: FileIdentity
    payload: bytes
    absolute_path: Path


@dataclass(frozen=True)
class RenderNamespace:
    config_path: str
    input_paths: Mapping[str, str]
    module_path: str
    cli_path: str
    tool_paths: Mapping[str, str]
    output_root: str
    commit_path: str
    lock_path: str
    title: str
    production: bool = False


CANONICAL_NAMESPACE: Final = RenderNamespace(
    config_path=CANONICAL_CONFIG,
    input_paths=CANONICAL_INPUTS,
    module_path=CANONICAL_MODULE,
    cli_path=CANONICAL_CLI,
    tool_paths=CANONICAL_TOOLS,
    output_root=CANONICAL_OUTPUT_ROOT,
    commit_path=CANONICAL_COMMIT,
    lock_path=CANONICAL_LOCK,
    title="한국어 L2 발음 진단과 폐쇄루프 보이스 코칭",
    production=True,
)


@dataclass(frozen=True)
class RenderConfig:
    workspace_root: Path
    namespace: RenderNamespace
    config_snapshot: FileSnapshot
    policy: Mapping[str, Any]
    inputs: Mapping[str, FileRecord]
    implementation: Mapping[str, FileRecord]
    tools: Mapping[str, ToolRecord]
    output_root: str
    commit_path: str


@dataclass(frozen=True)
class ImplementationSnapshot:
    module: FileSnapshot
    cli: FileSnapshot
    test_only: bool


@dataclass(frozen=True)
class SourceBundle:
    snapshots: Mapping[str, FileSnapshot]
    indirect_snapshots: Mapping[str, FileSnapshot]
    manuscript: str
    semantics: Mapping[str, Any]

    @property
    def records(self) -> Mapping[str, FileRecord]:
        return {
            **{key: value.record for key, value in self.snapshots.items()},
            **{key: value.record for key, value in self.indirect_snapshots.items()},
        }


@dataclass(frozen=True)
class HtmlArtifact:
    payload: bytes
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class BuildRun:
    raw_pdf: bytes
    network: Mapping[str, Any]


@dataclass(frozen=True)
class PostflightResult:
    document: Mapping[str, Any]
    extracted_layout_text: bytes
    extracted_semantic_text: bytes
    extracted_table_text: bytes
    pages: Mapping[str, bytes]


@dataclass(frozen=True)
class BuildProducts:
    pdf: bytes
    extracted_layout_text: bytes
    extracted_semantic_text: bytes
    extracted_table_text: bytes
    postflight: bytes
    pages: Mapping[str, bytes]
    receipt: bytes
    generation_id: str
    receipt_value: Mapping[str, Any]


@dataclass(frozen=True)
class DirectoryGuard:
    name: str
    descriptor: int
    identity: FileIdentity


@dataclass(frozen=True)
class OutputGuard:
    root_descriptor: int
    root_identity: FileIdentity
    parent_chain: tuple[DirectoryGuard, ...]
    parent_descriptor: int
    output_descriptor: int
    generations_descriptor: int
    history_descriptor: int
    lock_descriptor: int
    parent_identity: FileIdentity
    output_identity: FileIdentity
    generations_identity: FileIdentity
    history_identity: FileIdentity
    lock_identity: FileIdentity


@dataclass(frozen=True)
class CreatedGeneration:
    generation_id: str
    descriptor: int
    pages_descriptor: int
    identity: FileIdentity
    pages_identity: FileIdentity


@dataclass(frozen=True)
class RetainedGeneration:
    generation_id: str
    retained_name: str
    identity: FileIdentity


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_string_hash(values: Sequence[str]) -> str:
    return _sha256("\n".join(sorted(values)).encode("utf-8"))


def _identity(value: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        nlink=value.st_nlink,
        size_bytes=value.st_size,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def _same_filesystem_object(left: FileIdentity, right: FileIdentity) -> bool:
    """Compare stable identity fields for objects whose metadata may mutate."""

    return (
        left.device == right.device
        and left.inode == right.inode
        and stat.S_IFMT(left.mode) == stat.S_IFMT(right.mode)
    )


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PaperDraftPdfError(code)
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    code: str,
) -> None:
    if set(value) != expected:
        raise PaperDraftPdfError(
            code,
            f"expected={sorted(expected)} actual={sorted(value)}",
        )


def _integer(value: Any, code: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PaperDraftPdfError(code)
    return value


def _number(value: Any, code: str, *, minimum: float = 0.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PaperDraftPdfError(code)
    converted = float(value)
    if converted < minimum or not math.isfinite(converted):
        raise PaperDraftPdfError(code)
    return converted


def _string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PaperDraftPdfError(code)
    return value


def _safe_relative(value: Any, code: str) -> str:
    text = _string(value, code)
    if "\x00" in text or "\\" in text:
        raise PaperDraftPdfError(code)
    path = PurePosixPath(text)
    lowered = {part.casefold() for part in path.parts}
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != text
        or lowered & FORBIDDEN_PATH_PARTS
        or any(part.startswith(".env") for part in lowered)
    ):
        raise PaperDraftPdfError(code, text)
    return text


def _safe_absolute(value: Any, code: str) -> str:
    text = _string(value, code)
    path = Path(text)
    if (
        not path.is_absolute()
        or "\x00" in text
        or path.as_posix() != text
        or any(
            part.casefold() in FORBIDDEN_PATH_PARTS
            or part.casefold().startswith(".env")
            for part in path.parts
        )
    ):
        raise PaperDraftPdfError(code, text)
    return text


def _parse_file_record(value: Any, code: str) -> FileRecord:
    item = _mapping(value, code)
    _exact_keys(item, {"path", "sha256", "size_bytes"}, code)
    path = _safe_relative(item.get("path"), code)
    digest = _string(item.get("sha256"), code)
    if SHA256_RE.fullmatch(digest) is None:
        raise PaperDraftPdfError(code)
    return FileRecord(
        path=path,
        sha256=digest,
        size_bytes=_integer(item.get("size_bytes"), code, minimum=1),
    )


def _parse_tool_record(value: Any, code: str) -> ToolRecord:
    item = _mapping(value, code)
    _exact_keys(item, {"path", "sha256", "size_bytes", "version"}, code)
    path = _safe_absolute(item.get("path"), code)
    digest = _string(item.get("sha256"), code)
    if SHA256_RE.fullmatch(digest) is None:
        raise PaperDraftPdfError(code)
    return ToolRecord(
        path=path,
        sha256=digest,
        size_bytes=_integer(item.get("size_bytes"), code, minimum=1),
        version=_string(item.get("version"), code),
    )


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_unique_mapping(
    loader: yaml.Loader,
    node: yaml.Node,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise PaperDraftPdfError("CONFIG_KEY_INVALID")
        if key in result:
            raise PaperDraftPdfError("CONFIG_DUPLICATE_KEY", key)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _json(payload: bytes, code: str) -> Mapping[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise PaperDraftPdfError(f"{code}_DUPLICATE_KEY", key)
            result[key] = value
        return result

    def constant(value: str) -> NoReturn:
        raise PaperDraftPdfError(f"{code}_NON_FINITE", value)

    try:
        value = json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperDraftPdfError(code) from exc
    return _mapping(value, code)


def _assert_namespace(namespace: RenderNamespace) -> None:
    relative_paths = {
        namespace.config_path,
        *namespace.input_paths.values(),
        namespace.module_path,
        namespace.cli_path,
        namespace.output_root,
        namespace.commit_path,
        namespace.lock_path,
    }
    if (
        tuple(namespace.input_paths) != INPUT_KEYS
        or tuple(namespace.tool_paths) != TOOL_KEYS
        or len(relative_paths) != 14
        or len(set(namespace.tool_paths.values())) != len(TOOL_KEYS)
        or (namespace.production and namespace != CANONICAL_NAMESPACE)
        or (
            namespace.config_path == CANONICAL_CONFIG
            and namespace != CANONICAL_NAMESPACE
        )
    ):
        raise PaperDraftPdfError("NAMESPACE_INVALID")
    for path in relative_paths:
        _safe_relative(path, "NAMESPACE_PATH_INVALID")
    for path in namespace.tool_paths.values():
        _safe_absolute(path, "NAMESPACE_TOOL_PATH_INVALID")
    if (
        namespace.commit_path != f"{namespace.output_root}/commit.json"
        or namespace.lock_path.startswith(namespace.output_root + "/")
        or PurePosixPath(namespace.lock_path).parent
        != PurePosixPath(namespace.output_root).parent
    ):
        raise PaperDraftPdfError("NAMESPACE_OUTPUT_INVALID")


def _resolve_config_relative(
    config_path: Path,
    root: Path,
    namespace: RenderNamespace,
) -> str:
    if config_path.is_absolute():
        try:
            relative = config_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise PaperDraftPdfError("CONFIG_OUTSIDE_WORKSPACE") from exc
    else:
        relative = _safe_relative(config_path.as_posix(), "CONFIG_PATH_INVALID")
    if relative != namespace.config_path:
        raise PaperDraftPdfError("CONFIG_PATH_NOT_CANONICAL")
    return relative


def _read_from_dirfd(
    root: Path,
    components: Sequence[str],
    *,
    record: FileRecord | None,
    record_path: str,
    code: str,
    maximum: int,
    absolute_path: Path,
) -> FileSnapshot:
    if not components or any(
        part in {"", ".", ".."} or "/" in part for part in components
    ):
        raise PaperDraftPdfError(f"{code}_PATH_INVALID", record_path)
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise PaperDraftPdfError(f"{code}_ROOT_INVALID", record_path) from exc
    directories: list[DirectoryGuard] = []
    file_descriptor = -1
    try:
        root_identity = _identity(os.fstat(root_descriptor))
        if not _same_filesystem_object(root_identity, _identity(root.lstat())):
            raise PaperDraftPdfError(f"{code}_ROOT_REBOUND", record_path)
        parent = root_descriptor
        for name in components[:-1]:
            descriptor = _open_directory_at(
                parent,
                name,
                f"{code}_PARENT_INVALID",
            )
            descriptor_identity = _identity(os.fstat(descriptor))
            path_identity = _directory_identity_at(
                parent,
                name,
                f"{code}_PARENT_REBOUND",
            )
            if not _same_filesystem_object(descriptor_identity, path_identity):
                os.close(descriptor)
                raise PaperDraftPdfError(f"{code}_PARENT_REBOUND", record_path)
            directories.append(DirectoryGuard(name, descriptor, descriptor_identity))
            parent = descriptor
        name = components[-1]
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise PaperDraftPdfError(f"{code}_MISSING", record_path) from exc
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
            or (record is not None and before.st_size != record.size_bytes)
        ):
            raise PaperDraftPdfError(f"{code}_METADATA_INVALID", record_path)
        try:
            file_descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        except OSError as exc:
            raise PaperDraftPdfError(f"{code}_OPEN_FAILED", record_path) from exc
        descriptor_before = os.fstat(file_descriptor)
        identity = _identity(descriptor_before)
        if identity != _identity(before):
            raise PaperDraftPdfError(f"{code}_PATH_FD_MISMATCH", record_path)
        chunks: list[bytes] = []
        remaining = descriptor_before.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise PaperDraftPdfError(f"{code}_SHORT_READ", record_path)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise PaperDraftPdfError(f"{code}_GREW", record_path)
        descriptor_after = os.fstat(file_descriptor)
        try:
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise PaperDraftPdfError(f"{code}_PATH_CHANGED", record_path) from exc
        if identity != _identity(descriptor_after) or identity != _identity(after):
            raise PaperDraftPdfError(f"{code}_PATH_CHANGED", record_path)
        chain_parent = root_descriptor
        for item in directories:
            path_identity = _directory_identity_at(
                chain_parent,
                item.name,
                f"{code}_PARENT_REBOUND",
            )
            if not _same_filesystem_object(
                path_identity, item.identity
            ) or not _same_filesystem_object(
                _identity(os.fstat(item.descriptor)),
                item.identity,
            ):
                raise PaperDraftPdfError(f"{code}_PARENT_REBOUND", record_path)
            chain_parent = item.descriptor
        if not _same_filesystem_object(
            root_identity,
            _identity(root.lstat()),
        ):
            raise PaperDraftPdfError(f"{code}_ROOT_REBOUND", record_path)
        payload = b"".join(chunks)
        if record is not None and _sha256(payload) != record.sha256:
            raise PaperDraftPdfError(f"{code}_HASH_MISMATCH", record_path)
        final_record = record or FileRecord(
            record_path,
            _sha256(payload),
            len(payload),
        )
        return FileSnapshot(final_record, identity, payload, absolute_path)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for item in reversed(directories):
            os.close(item.descriptor)
        os.close(root_descriptor)


def _read_relative(
    root: Path,
    record: FileRecord,
    code: str,
    *,
    maximum: int = MAX_SOURCE_BYTES,
) -> FileSnapshot:
    relative = PurePosixPath(record.path)
    return _read_from_dirfd(
        root,
        relative.parts,
        record=record,
        record_path=record.path,
        code=code,
        maximum=maximum,
        absolute_path=root / relative,
    )


def _capture_relative(
    root: Path,
    relative: str,
    *,
    code: str,
    maximum: int,
) -> FileSnapshot:
    safe = _safe_relative(relative, f"{code}_PATH_INVALID")
    path = PurePosixPath(safe)
    return _read_from_dirfd(
        root,
        path.parts,
        record=None,
        record_path=safe,
        code=code,
        maximum=maximum,
        absolute_path=root / path,
    )


def _capture_absolute(
    path: Path,
    *,
    record_path: str,
    code: str,
    maximum: int,
) -> FileSnapshot:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise PaperDraftPdfError(f"{code}_PATH_INVALID", record_path)
    parts = PurePosixPath(absolute.as_posix()).parts
    return _read_from_dirfd(
        Path("/"),
        parts[1:],
        record=None,
        record_path=record_path,
        code=code,
        maximum=maximum,
        absolute_path=absolute,
    )


def _revalidate_relative(
    root: Path,
    snapshot: FileSnapshot,
    code: str,
) -> None:
    current = _read_relative(root, snapshot.record, code)
    if current.identity != snapshot.identity or current.payload != snapshot.payload:
        raise PaperDraftPdfError(f"{code}_SNAPSHOT_CHANGED", snapshot.record.path)


def _read_tool(record: ToolRecord, code: str) -> ToolSnapshot:
    path = PurePosixPath(record.path)
    snapshot = _read_from_dirfd(
        Path("/"),
        path.parts[1:],
        record=FileRecord(record.path, record.sha256, record.size_bytes),
        record_path=record.path,
        code=code,
        maximum=MAX_TOOL_BYTES,
        absolute_path=Path(record.path),
    )
    return ToolSnapshot(
        record=record,
        identity=snapshot.identity,
        payload=snapshot.payload,
        absolute_path=snapshot.absolute_path,
    )


def _revalidate_tool(snapshot: ToolSnapshot, code: str) -> None:
    current = _read_tool(snapshot.record, code)
    if current.identity != snapshot.identity or current.payload != snapshot.payload:
        raise PaperDraftPdfError(f"{code}_SNAPSHOT_CHANGED", snapshot.record.path)


def load_render_config(
    config_path: Path,
    *,
    workspace_root: Path,
    namespace: RenderNamespace = CANONICAL_NAMESPACE,
) -> RenderConfig:
    """Load only the config; referenced sources, tools, and outputs stay closed."""

    root = workspace_root.resolve(strict=True)
    _assert_namespace(namespace)
    relative = _resolve_config_relative(config_path, root, namespace)
    snapshot = _capture_relative(
        root,
        relative,
        code="CONFIG",
        maximum=MAX_SOURCE_BYTES,
    )
    try:
        raw = yaml.load(snapshot.payload.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise PaperDraftPdfError("CONFIG_PARSE_FAILED") from exc
    config = _mapping(raw, "CONFIG_INVALID")
    _exact_keys(
        config,
        {
            "schema_version",
            "status",
            "policy",
            "inputs",
            "implementation",
            "tools",
            "output",
        },
        "CONFIG_KEYS_INVALID",
    )
    if config.get("schema_version") != CONFIG_SCHEMA or config.get("status") != "ready":
        raise PaperDraftPdfError("CONFIG_SCHEMA_STATUS_INVALID")
    policy = _mapping(config.get("policy"), "CONFIG_POLICY_INVALID")
    _exact_keys(
        policy,
        {
            "draft",
            "final",
            "visual_review_status",
            "markdown_parser",
            "markdown_parser_version",
            "html_enabled",
            "expected_title",
            "expected_markdown_tables",
            "expected_references",
            "expected_pending_count",
            "expected_pending_markers_sha256",
            "expected_https_links",
            "svg_width",
            "svg_height",
            "source_date_epoch",
            "libreoffice_timeout_seconds",
            "tool_timeout_seconds",
            "raster_dpi",
            "blank_white_ratio",
            "kernel_network_namespace",
        },
        "CONFIG_POLICY_KEYS_INVALID",
    )
    if (
        policy.get("draft") is not True
        or policy.get("final") is not False
        or policy.get("visual_review_status") != "pending"
        or policy.get("markdown_parser") != "markdown-it-py"
        or policy.get("markdown_parser_version") != "4.2.0"
        or policy.get("html_enabled") is not False
        or policy.get("expected_title") != namespace.title
        or _integer(
            policy.get("expected_markdown_tables"),
            "CONFIG_POLICY_INVALID",
        )
        != 13
        or _integer(policy.get("expected_references"), "CONFIG_POLICY_INVALID") != 11
        or _integer(
            policy.get("expected_pending_count"),
            "CONFIG_POLICY_INVALID",
        )
        != 18
        or SHA256_RE.fullmatch(
            _string(
                policy.get("expected_pending_markers_sha256"),
                "CONFIG_POLICY_INVALID",
            )
        )
        is None
        or _integer(policy.get("expected_https_links"), "CONFIG_POLICY_INVALID") < 1
        or _integer(policy.get("svg_width"), "CONFIG_POLICY_INVALID") != 680
        or _integer(policy.get("svg_height"), "CONFIG_POLICY_INVALID") != 425
        or _integer(policy.get("source_date_epoch"), "CONFIG_POLICY_INVALID") < 1
        or _number(
            policy.get("libreoffice_timeout_seconds"),
            "CONFIG_POLICY_INVALID",
            minimum=1.0,
        )
        > 600.0
        or _number(
            policy.get("tool_timeout_seconds"),
            "CONFIG_POLICY_INVALID",
            minimum=1.0,
        )
        > 300.0
        or not 72 <= _integer(policy.get("raster_dpi"), "CONFIG_POLICY_INVALID") <= 200
        or _number(
            policy.get("blank_white_ratio"),
            "CONFIG_POLICY_INVALID",
        )
        != 0.9999
        or policy.get("kernel_network_namespace") is not False
    ):
        raise PaperDraftPdfError("CONFIG_POLICY_INVALID")
    inputs_raw = _mapping(config.get("inputs"), "CONFIG_INPUTS_INVALID")
    if tuple(inputs_raw) != INPUT_KEYS:
        raise PaperDraftPdfError("CONFIG_INPUT_ORDER_INVALID")
    inputs = {
        key: _parse_file_record(inputs_raw[key], f"CONFIG_INPUT_{key.upper()}")
        for key in INPUT_KEYS
    }
    if any(inputs[key].path != namespace.input_paths[key] for key in INPUT_KEYS):
        raise PaperDraftPdfError("CONFIG_INPUT_PATH_INVALID")
    implementation_raw = _mapping(
        config.get("implementation"),
        "CONFIG_IMPLEMENTATION_INVALID",
    )
    if tuple(implementation_raw) != ("module", "cli"):
        raise PaperDraftPdfError("CONFIG_IMPLEMENTATION_ORDER_INVALID")
    implementation = {
        "module": _parse_file_record(
            implementation_raw["module"],
            "CONFIG_MODULE_INVALID",
        ),
        "cli": _parse_file_record(
            implementation_raw["cli"],
            "CONFIG_CLI_INVALID",
        ),
    }
    if (
        implementation["module"].path != namespace.module_path
        or implementation["cli"].path != namespace.cli_path
    ):
        raise PaperDraftPdfError("CONFIG_IMPLEMENTATION_PATH_INVALID")
    tools_raw = _mapping(config.get("tools"), "CONFIG_TOOLS_INVALID")
    if tuple(tools_raw) != TOOL_KEYS:
        raise PaperDraftPdfError("CONFIG_TOOL_ORDER_INVALID")
    tools = {
        key: _parse_tool_record(tools_raw[key], f"CONFIG_TOOL_{key.upper()}")
        for key in TOOL_KEYS
    }
    if any(tools[key].path != namespace.tool_paths[key] for key in TOOL_KEYS):
        raise PaperDraftPdfError("CONFIG_TOOL_PATH_INVALID")
    output = _mapping(config.get("output"), "CONFIG_OUTPUT_INVALID")
    _exact_keys(
        output,
        {"root", "commit", "generations", "lock"},
        "CONFIG_OUTPUT_INVALID",
    )
    root_path = _safe_relative(output.get("root"), "CONFIG_OUTPUT_INVALID")
    commit_path = _safe_relative(output.get("commit"), "CONFIG_OUTPUT_INVALID")
    if (
        root_path != namespace.output_root
        or commit_path != namespace.commit_path
        or output.get("generations") != "generations"
        or output.get("lock") != namespace.lock_path
    ):
        raise PaperDraftPdfError("CONFIG_OUTPUT_INVALID")
    return RenderConfig(
        workspace_root=root,
        namespace=namespace,
        config_snapshot=snapshot,
        policy=dict(policy),
        inputs=inputs,
        implementation=implementation,
        tools=tools,
        output_root=root_path,
        commit_path=commit_path,
    )


def config_only_summary(config: RenderConfig) -> dict[str, Any]:
    """Return the zero-source/tool/output-access contract."""

    return {
        "schema_version": CONFIG_SCHEMA,
        "status": "valid_config_only",
        "config": config.config_snapshot.record.as_dict(),
        "declared_input_count": len(config.inputs),
        "declared_tool_count": len(config.tools),
        "actual_state": {
            "referenced_source_files_read": 0,
            "tool_files_read": 0,
            "tool_processes_started": 0,
            "output_paths_statted": 0,
            "output_paths_opened": 0,
            "outputs_created": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
            "raw_files_read": 0,
            "locked_test_files_read": 0,
            "network_requests": 0,
        },
    }


def capture_absolute_snapshot(
    path: Path,
    *,
    record_path: str,
    code: str,
) -> FileSnapshot:
    """Capture an already-selected implementation file without following links."""

    return _capture_absolute(
        path,
        record_path=record_path,
        code=code,
        maximum=MAX_SOURCE_BYTES,
    )


def _snapshot_with_path(snapshot: FileSnapshot, path: str) -> FileSnapshot:
    return FileSnapshot(
        record=FileRecord(
            path,
            snapshot.record.sha256,
            snapshot.record.size_bytes,
        ),
        identity=snapshot.identity,
        payload=snapshot.payload,
        absolute_path=snapshot.absolute_path,
    )


def production_implementation_snapshot(
    config: RenderConfig,
    *,
    cli_start_snapshot: FileSnapshot,
) -> ImplementationSnapshot:
    if not config.namespace.production:
        raise PaperDraftPdfError("PRODUCTION_NAMESPACE_REQUIRED")
    module = _snapshot_with_path(
        _MODULE_IMPORT_SNAPSHOT,
        config.namespace.module_path,
    )
    cli = _snapshot_with_path(cli_start_snapshot, config.namespace.cli_path)
    if (
        module.absolute_path != config.workspace_root / config.namespace.module_path
        or cli.absolute_path != config.workspace_root / config.namespace.cli_path
    ):
        raise PaperDraftPdfError("IMPLEMENTATION_PATH_MISMATCH")
    result = ImplementationSnapshot(module=module, cli=cli, test_only=False)
    _validate_implementation(config, result)
    return result


def capture_test_implementation_snapshot(
    config: RenderConfig,
) -> ImplementationSnapshot:
    if config.namespace.production:
        raise PaperDraftPdfError("TEST_NAMESPACE_REQUIRED")
    result = ImplementationSnapshot(
        module=_read_relative(
            config.workspace_root,
            config.implementation["module"],
            "TEST_MODULE",
        ),
        cli=_read_relative(
            config.workspace_root,
            config.implementation["cli"],
            "TEST_CLI",
        ),
        test_only=True,
    )
    _validate_implementation(config, result)
    return result


def _validate_implementation(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
) -> None:
    if config.namespace.production == implementation.test_only:
        raise PaperDraftPdfError("IMPLEMENTATION_SNAPSHOT_KIND_INVALID")
    for key, snapshot in (
        ("module", implementation.module),
        ("cli", implementation.cli),
    ):
        expected = config.implementation[key]
        if snapshot.record != expected:
            raise PaperDraftPdfError("IMPLEMENTATION_PIN_MISMATCH", key)
        _revalidate_relative(
            config.workspace_root,
            snapshot,
            f"IMPLEMENTATION_{key.upper()}",
        )


def _load_tool_snapshots(config: RenderConfig) -> Mapping[str, ToolSnapshot]:
    snapshots = {
        key: _read_tool(config.tools[key], f"TOOL_{key.upper()}") for key in TOOL_KEYS
    }
    seen: dict[tuple[int, int], str] = {}
    for key, snapshot in snapshots.items():
        inode = (snapshot.identity.device, snapshot.identity.inode)
        if inode in seen:
            raise PaperDraftPdfError(
                "TOOL_INODE_ALIAS",
                f"{seen[inode]}={key}",
            )
        seen[inode] = key
    return snapshots


def _revalidate_tools(tools: Mapping[str, ToolSnapshot]) -> None:
    for key, snapshot in tools.items():
        _revalidate_tool(snapshot, f"TOOL_{key.upper()}")


def _bound_receipt_record(
    value: Mapping[str, Any],
    *,
    code: str,
) -> FileRecord:
    receipt = _mapping(value.get("receipt"), f"{code}_RECEIPT_INVALID")
    return _parse_file_record(receipt, f"{code}_RECEIPT_INVALID")


def _record_matches(
    value: Any,
    expected: FileRecord,
    *,
    code: str,
) -> None:
    item = _mapping(value, code)
    if (
        item.get("path") != expected.path
        or item.get("sha256") != expected.sha256
        or ("size_bytes" in item and item.get("size_bytes") != expected.size_bytes)
    ):
        raise PaperDraftPdfError(code)


def _validate_cohort_svg(payload: bytes) -> None:
    lowered = payload.lower()
    if any(
        token in lowered
        for token in (
            b"<!doctype",
            b"<!entity",
            b"<script",
            b"<foreignobject",
            b"javascript:",
            b"data:",
        )
    ):
        raise PaperDraftPdfError("COHORT_SVG_UNSAFE")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise PaperDraftPdfError("COHORT_SVG_XML_INVALID") from exc
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise PaperDraftPdfError("COHORT_SVG_ROOT_INVALID")
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1].casefold()
        if local in {"script", "foreignobject", "style"}:
            raise PaperDraftPdfError("COHORT_SVG_UNSAFE")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            value = raw_value.strip().casefold()
            if (
                name in {"href", "src"}
                or name.startswith("on")
                or "javascript:" in value
                or "data:" in value
                or "http:" in value
                or "https:" in value
                or ("url(" in value and not value.startswith("url(#"))
            ):
                raise PaperDraftPdfError("COHORT_SVG_EXTERNAL_REFERENCE")


def _decode_utf8(snapshot: FileSnapshot, code: str) -> str:
    try:
        value = snapshot.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError(code) from exc
    if "\r" in value or not value.endswith("\n"):
        raise PaperDraftPdfError(f"{code}_NEWLINE_INVALID")
    return value


def _validate_input_semantics(
    config: RenderConfig,
    snapshots: Mapping[str, FileSnapshot],
) -> tuple[Mapping[str, FileSnapshot], str]:
    manuscript = _decode_utf8(snapshots["manuscript"], "MANUSCRIPT_UTF8_INVALID")
    evidence = _json(snapshots["evidence_report"].payload, "EVIDENCE_JSON_INVALID")
    if evidence.get("status") != "passed":
        raise PaperDraftPdfError("EVIDENCE_STATUS_INVALID")
    summary = _mapping(evidence.get("summary"), "EVIDENCE_SUMMARY_INVALID")
    if summary.get("artifact_count") != summary.get("artifact_passed") or summary.get(
        "claim_count"
    ) != summary.get("claim_passed"):
        raise PaperDraftPdfError("EVIDENCE_SUMMARY_INVALID")
    pending = _mapping(
        evidence.get("pending_contract"),
        "EVIDENCE_PENDING_INVALID",
    )
    if (
        pending.get("status") != "passed"
        or pending.get("marker_count") != config.policy["expected_pending_count"]
        or pending.get("markers_sha256")
        != config.policy["expected_pending_markers_sha256"]
        or pending.get("errors") != []
    ):
        raise PaperDraftPdfError("EVIDENCE_PENDING_INVALID")
    _record_matches(
        evidence.get("manuscript"),
        snapshots["manuscript"].record,
        code="EVIDENCE_MANUSCRIPT_PIN_INVALID",
    )
    locked = _mapping(
        evidence.get("locked_test_guard"),
        "EVIDENCE_LOCKED_GUARD_INVALID",
    )
    accessed = _mapping(
        locked.get("accessed"),
        "EVIDENCE_LOCKED_GUARD_INVALID",
    )
    if (
        locked.get("status") != "passed"
        or locked.get("errors") != []
        or not accessed
        or any(value is not False for value in accessed.values())
    ):
        raise PaperDraftPdfError("EVIDENCE_LOCKED_GUARD_INVALID")
    privacy = _mapping(evidence.get("privacy"), "EVIDENCE_PRIVACY_INVALID")
    if (
        privacy.get("aggregate_only") is not True
        or privacy.get("environment_files_read") is not False
        or privacy.get("raw_data_read") is not False
    ):
        raise PaperDraftPdfError("EVIDENCE_PRIVACY_INVALID")

    indirect: dict[str, FileSnapshot] = {}
    publication_commit = _json(
        snapshots["publication_commit"].payload,
        "PUBLICATION_COMMIT_JSON_INVALID",
    )
    if publication_commit.get("status") != "committed":
        raise PaperDraftPdfError("PUBLICATION_COMMIT_STATUS_INVALID")
    publication_receipt_record = _bound_receipt_record(
        publication_commit,
        code="PUBLICATION_COMMIT",
    )
    publication_receipt = _read_relative(
        config.workspace_root,
        publication_receipt_record,
        "PUBLICATION_RECEIPT",
    )
    indirect["publication_receipt"] = publication_receipt
    publication = _json(
        publication_receipt.payload,
        "PUBLICATION_RECEIPT_JSON_INVALID",
    )
    if publication.get("status") != "passed":
        raise PaperDraftPdfError("PUBLICATION_RECEIPT_STATUS_INVALID")
    publication_pending = _mapping(
        publication.get("pending_contract"),
        "PUBLICATION_PENDING_INVALID",
    )
    if (
        publication_pending.get("marker_count")
        != config.policy["expected_pending_count"]
        or publication_pending.get("markers_sha256")
        != config.policy["expected_pending_markers_sha256"]
        or publication_pending.get("markers_preserved") is not True
    ):
        raise PaperDraftPdfError("PUBLICATION_PENDING_INVALID")
    source_pins = publication.get("source_pins")
    if not isinstance(source_pins, list):
        raise PaperDraftPdfError("PUBLICATION_SOURCE_PINS_INVALID")
    source_pin_map = {
        item.get("path"): item for item in source_pins if isinstance(item, dict)
    }
    _record_matches(
        source_pin_map.get(snapshots["manuscript"].record.path),
        snapshots["manuscript"].record,
        code="PUBLICATION_MANUSCRIPT_PIN_INVALID",
    )
    _record_matches(
        source_pin_map.get(snapshots["evidence_report"].record.path),
        snapshots["evidence_report"].record,
        code="PUBLICATION_EVIDENCE_PIN_INVALID",
    )
    publication_privacy = _mapping(
        publication.get("privacy"),
        "PUBLICATION_PRIVACY_INVALID",
    )
    if (
        publication_privacy.get("aggregate_only") is not True
        or publication_privacy.get("environment_files_read") is not False
        or publication_privacy.get("locked_test_files_read") is not False
        or publication_privacy.get("raw_files_read") is not False
        or publication_privacy.get("secret_files_read") is not False
    ):
        raise PaperDraftPdfError("PUBLICATION_PRIVACY_INVALID")

    semantic_commit = _json(
        snapshots["semantic_commit"].payload,
        "SEMANTIC_COMMIT_JSON_INVALID",
    )
    if (
        semantic_commit.get("status") != "committed"
        or semantic_commit.get("p04_complete") is not False
        or semantic_commit.get("p05_complete") is not False
    ):
        raise PaperDraftPdfError("SEMANTIC_COMMIT_STATUS_INVALID")
    semantic_receipt_record = _bound_receipt_record(
        semantic_commit,
        code="SEMANTIC_COMMIT",
    )
    semantic_receipt = _read_relative(
        config.workspace_root,
        semantic_receipt_record,
        "SEMANTIC_RECEIPT",
    )
    indirect["semantic_receipt"] = semantic_receipt
    semantic = _json(
        semantic_receipt.payload,
        "SEMANTIC_RECEIPT_JSON_INVALID",
    )
    completion = _mapping(
        semantic.get("completion_claims"),
        "SEMANTIC_COMPLETION_INVALID",
    )
    if (
        semantic.get("publication_status") not in {"completed", "passed"}
        or completion.get("p04_complete") is not False
        or completion.get("p05_complete") is not False
    ):
        raise PaperDraftPdfError("SEMANTIC_COMPLETION_INVALID")
    semantic_sources = _mapping(
        semantic.get("source_records"),
        "SEMANTIC_SOURCE_RECORDS_INVALID",
    )
    _record_matches(
        semantic_sources.get("manuscript"),
        snapshots["manuscript"].record,
        code="SEMANTIC_MANUSCRIPT_PIN_INVALID",
    )
    _record_matches(
        semantic_sources.get("validation_report"),
        snapshots["evidence_report"].record,
        code="SEMANTIC_EVIDENCE_PIN_INVALID",
    )

    _validate_cohort_svg(snapshots["cohort_svg"].payload)
    cohort = _json(
        snapshots["cohort_receipt"].payload,
        "COHORT_RECEIPT_JSON_INVALID",
    )
    if (
        cohort.get("status") != "passed"
        or cohort.get("schema_version")
        != "voice_coach.paper.cohort_flow_svg_receipt.v2"
    ):
        raise PaperDraftPdfError("COHORT_RECEIPT_STATUS_INVALID")
    _record_matches(
        cohort.get("output"),
        snapshots["cohort_svg"].record,
        code="COHORT_SVG_PIN_INVALID",
    )
    cohort_implementation = _mapping(
        cohort.get("implementation"),
        "COHORT_IMPLEMENTATION_INVALID",
    )
    _record_matches(
        cohort_implementation.get("renderer"),
        snapshots["cohort_renderer"].record,
        code="COHORT_RENDERER_PIN_INVALID",
    )
    _record_matches(
        cohort_implementation.get("cli"),
        snapshots["cohort_cli"].record,
        code="COHORT_CLI_PIN_INVALID",
    )
    research_state = _mapping(
        cohort.get("research_state"),
        "COHORT_RESEARCH_STATE_INVALID",
    )
    if (
        research_state.get("manual_alignment_g0") != "PENDING"
        or research_state.get("research_completion_claimed") is not False
    ):
        raise PaperDraftPdfError("COHORT_RESEARCH_STATE_INVALID")
    return indirect, manuscript


def _load_sources(config: RenderConfig) -> SourceBundle:
    snapshots = {
        key: _read_relative(
            config.workspace_root,
            config.inputs[key],
            f"SOURCE_{key.upper()}",
        )
        for key in INPUT_KEYS
    }
    all_values = [
        ("config", config.config_snapshot),
        *snapshots.items(),
    ]
    seen: dict[tuple[int, int], str] = {}
    for key, snapshot in all_values:
        inode = (snapshot.identity.device, snapshot.identity.inode)
        if inode in seen:
            raise PaperDraftPdfError("SOURCE_INODE_ALIAS", f"{seen[inode]}={key}")
        seen[inode] = key
    indirect, manuscript = _validate_input_semantics(config, snapshots)
    semantics = _manuscript_semantics(config, manuscript)
    return SourceBundle(
        snapshots=snapshots,
        indirect_snapshots=indirect,
        manuscript=manuscript,
        semantics=semantics,
    )


def _revalidate_sources(config: RenderConfig, sources: SourceBundle) -> None:
    for key, snapshot in {
        **sources.snapshots,
        **sources.indirect_snapshots,
    }.items():
        _revalidate_relative(
            config.workspace_root,
            snapshot,
            f"SOURCE_{key.upper()}",
        )


def _manuscript_semantics(
    config: RenderConfig,
    manuscript: str,
) -> Mapping[str, Any]:
    title_match = re.match(r"^# ([^\n]+)\n", manuscript)
    if title_match is None or title_match.group(1) != config.namespace.title:
        raise PaperDraftPdfError("MANUSCRIPT_TITLE_INVALID")
    markers = PENDING_RE.findall(manuscript)
    marker_hash = _canonical_string_hash(markers)
    if (
        len(markers) != config.policy["expected_pending_count"]
        or marker_hash != config.policy["expected_pending_markers_sha256"]
    ):
        raise PaperDraftPdfError("MANUSCRIPT_PENDING_INVALID")
    if manuscript.count("## 참고문헌") != 1 or manuscript.count("## 부록") != 1:
        raise PaperDraftPdfError("MANUSCRIPT_REFERENCE_SECTION_INVALID")
    reference_section = manuscript.split("## 참고문헌", 1)[1].split(
        "## 부록",
        1,
    )[0]
    reference_matches = list(
        re.finditer(
            r"(?ms)^(\d+)\.\s+(.*?)(?=^\d+\.\s+|\Z)",
            reference_section,
        )
    )
    if [int(match.group(1)) for match in reference_matches] != list(
        range(1, config.policy["expected_references"] + 1)
    ):
        raise PaperDraftPdfError("MANUSCRIPT_REFERENCE_COUNT_INVALID")
    reference_probes = []
    reference_contents: list[str] = []
    for match in reference_matches:
        visible = INLINE_LINK_RE.sub(lambda item: item.group(1), match.group(2))
        visible = visible.replace("*", "").replace("`", "")
        normalized_visible = re.sub(r"\s+", " ", visible).strip()
        if not normalized_visible:
            raise PaperDraftPdfError("MANUSCRIPT_REFERENCE_EMPTY")
        reference_contents.append(normalized_visible)
        probe = f"{match.group(1)}. {normalized_visible}"
        reference_probes.append(probe)
    if len(set(reference_contents)) != len(reference_contents):
        raise PaperDraftPdfError("MANUSCRIPT_REFERENCE_COLLISION")
    return {
        "title": title_match.group(1),
        "pending_markers": tuple(markers),
        "pending_markers_sha256": marker_hash,
        "reference_count": len(reference_matches),
        "reference_probes": tuple(reference_probes),
        "reference_probes_sha256": _canonical_string_hash(reference_probes),
    }


class _SanitizedHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.images: list[dict[str, str]] = []
        self.body_depth = 0
        self.visible_text_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag not in HTML_TAG_ALLOWLIST:
            raise PaperDraftPdfError("HTML_TAG_NOT_ALLOWED", tag)
        allowed = HTML_ATTR_ALLOWLIST.get(tag, set())
        values: dict[str, str] = {}
        for name, value in attrs:
            if name not in allowed or value is None or name in values:
                raise PaperDraftPdfError("HTML_ATTRIBUTE_NOT_ALLOWED", f"{tag}:{name}")
            values[name] = value
        if tag == "a":
            href = values.get("href", "")
            if not _is_safe_https(href):
                raise PaperDraftPdfError("HTML_LINK_NOT_HTTPS")
            self.hrefs.append(href)
        if tag == "img":
            self.images.append(values)
        if tag == "table" and values not in (
            {"width": "100%"},
            {"width": "100%", "class": "dense-table"},
        ):
            raise PaperDraftPdfError("HTML_TABLE_LAYOUT_INVALID")
        if tag == "td" and values not in ({}, {"class": "atomic-cell"}):
            raise PaperDraftPdfError("HTML_TABLE_CELL_CLASS_INVALID")
        if tag == "body":
            self.body_depth += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in HTML_TAG_ALLOWLIST:
            raise PaperDraftPdfError("HTML_TAG_NOT_ALLOWED", tag)
        if tag == "body":
            if self.body_depth != 1:
                raise PaperDraftPdfError("HTML_BODY_STRUCTURE_INVALID")
            self.body_depth = 0

    def handle_data(self, data: str) -> None:
        if self.body_depth:
            self.visible_text_parts.append(data)

    def handle_comment(self, data: str) -> None:
        raise PaperDraftPdfError("HTML_COMMENT_NOT_ALLOWED", data[:40])

    def handle_decl(self, decl: str) -> None:
        if decl.casefold() != "doctype html":
            raise PaperDraftPdfError("HTML_DECLARATION_NOT_ALLOWED")


def _is_safe_https(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment.startswith("/")
    )


def _markdown_destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<"):
        closing = value.find(">")
        if closing <= 1:
            return ""
        return value[1:closing]
    return value.split(maxsplit=1)[0] if value else ""


def _preprocess_markdown(manuscript: str) -> tuple[str, int]:
    lines: list[str] = []
    marker_seen = 0
    cohort_seen = 0
    cohort_image_seen = 0
    stripped_links = 0
    for line in manuscript.splitlines():
        stripped = line.strip()
        if stripped in ALLOWED_MARKER_COMMENTS:
            marker_seen += 1
            continue
        if "<!--" in line or "-->" in line:
            raise PaperDraftPdfError("MARKDOWN_COMMENT_NOT_ALLOWED")
        line = line.replace("<unk>", "&lt;unk&gt;")
        if HTML_TAG_RE.search(line):
            raise PaperDraftPdfError("MARKDOWN_RAW_HTML_NOT_ALLOWED", line[:80])

        if INLINE_IMAGE_RE.search(line):
            if stripped != COHORT_MARKDOWN_IMAGE:
                raise PaperDraftPdfError("MARKDOWN_IMAGE_NOT_ALLOWED")
            cohort_image_seen += 1
            lines.append(COHORT_PLACEHOLDER)
            continue

        def sanitize_link(match: re.Match[str]) -> str:
            nonlocal cohort_seen, stripped_links
            label, raw_destination = match.groups()
            destination = _markdown_destination(raw_destination)
            if destination == COHORT_SOURCE_LINK:
                cohort_seen += 1
            if _is_safe_https(destination):
                return match.group(0)
            stripped_links += 1
            return label

        sanitized_line = INLINE_LINK_RE.sub(sanitize_link, line)
        lines.append(sanitized_line)
    if marker_seen != 2:
        raise PaperDraftPdfError("MARKDOWN_MARKER_COMMENT_SET_INVALID")
    if cohort_seen != 1:
        raise PaperDraftPdfError("MARKDOWN_COHORT_LINK_INVALID")
    if cohort_image_seen != 1:
        raise PaperDraftPdfError("MARKDOWN_COHORT_IMAGE_INVALID")
    return "\n".join(lines) + "\n", stripped_links


def _mutate_links(tokens: Sequence[Any]) -> tuple[list[str], int]:
    https_links: list[str] = []
    stripped = 0
    for token in tokens:
        if token.type != "inline":
            continue
        children = token.children or []
        stack: list[str] = []
        for child in children:
            if child.type not in INLINE_TOKEN_ALLOWLIST:
                raise PaperDraftPdfError(
                    "MARKDOWN_INLINE_TOKEN_NOT_ALLOWED",
                    child.type,
                )
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                if _is_safe_https(href):
                    https_links.append(href)
                    stack.append("a")
                else:
                    child.tag = "span"
                    child.attrs = {}
                    stack.append("span")
                    stripped += 1
            elif child.type == "link_close":
                if not stack:
                    raise PaperDraftPdfError("MARKDOWN_LINK_STACK_INVALID")
                child.tag = stack.pop()
        if stack:
            raise PaperDraftPdfError("MARKDOWN_LINK_STACK_INVALID")
    return https_links, stripped


def _install_renderer_table_attributes(tokens: Sequence[Any]) -> None:
    """Install renderer-owned table width, density, and atomic-cell classes."""

    table_token: Any | None = None
    in_head = False
    head_cell_count = 0
    in_body = False
    cell_token: Any | None = None
    cell_parts: list[str] = []
    for token in tokens:
        if token.type == "table_open":
            if table_token is not None:
                raise PaperDraftPdfError("MARKDOWN_TABLE_STRUCTURE_INVALID")
            table_token = token
            head_cell_count = 0
            token.attrSet("width", "100%")
        elif token.type == "table_close":
            if table_token is None:
                raise PaperDraftPdfError("MARKDOWN_TABLE_STRUCTURE_INVALID")
            if head_cell_count >= 7:
                table_token.attrSet("class", "dense-table")
            table_token = None
        elif token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif in_head and token.type == "th_open":
            head_cell_count += 1
        elif token.type == "tbody_open":
            in_body = True
        elif token.type == "tbody_close":
            if cell_token is not None:
                raise PaperDraftPdfError("MARKDOWN_TABLE_CELL_STRUCTURE_INVALID")
            in_body = False
        elif in_body and token.type == "td_open":
            if cell_token is not None:
                raise PaperDraftPdfError("MARKDOWN_TABLE_CELL_STRUCTURE_INVALID")
            cell_token = token
            cell_parts = []
        elif in_body and cell_token is not None and token.type == "inline":
            cell_parts.append(token.content)
        elif in_body and token.type == "td_close":
            if cell_token is None:
                raise PaperDraftPdfError("MARKDOWN_TABLE_CELL_STRUCTURE_INVALID")
            cell_text = re.sub(r"\s+", " ", " ".join(cell_parts)).strip()
            if len(cell_text) >= 2 and not any(
                character.isspace() for character in cell_text
            ):
                cell_token.attrSet("class", "atomic-cell")
            cell_token = None
            cell_parts = []
    if table_token is not None or in_head or in_body or cell_token is not None:
        raise PaperDraftPdfError("MARKDOWN_TABLE_STRUCTURE_INVALID")


def _sanitize_structural_token_attributes(tokens: Sequence[Any]) -> None:
    """Validate source attrs before installing renderer-owned attributes."""

    for token in tokens:
        attrs = dict(token.attrs or {})
        if not attrs:
            continue
        if token.type in {"th_open", "td_open"}:
            if set(attrs) != {"style"} or attrs["style"] not in {
                "text-align:left",
                "text-align:center",
                "text-align:right",
            }:
                raise PaperDraftPdfError(
                    "MARKDOWN_TOKEN_ATTRIBUTE_NOT_ALLOWED",
                    token.type,
                )
            # Alignment is presentation-only and the fixed stylesheet owns table
            # rendering.  Removing it keeps the sanitized HTML attribute surface
            # smaller than markdown-it's default renderer output.
            token.attrs = {}
            continue
        if token.type == "ordered_list_open":
            start = attrs.get("start")
            if set(attrs) != {"start"} or not str(start).isdigit():
                raise PaperDraftPdfError(
                    "MARKDOWN_TOKEN_ATTRIBUTE_NOT_ALLOWED",
                    token.type,
                )
            continue
        raise PaperDraftPdfError(
            "MARKDOWN_TOKEN_ATTRIBUTE_NOT_ALLOWED",
            token.type,
        )
    _install_renderer_table_attributes(tokens)


def _table_headers(tokens: Sequence[Any]) -> list[str]:
    """Return one full, whitespace-normalized header-row signature per table."""

    headers: list[str] = []
    in_head = False
    in_row = False
    cells: list[str] = []
    for token in tokens:
        if token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif in_head and token.type == "tr_open":
            if in_row:
                raise PaperDraftPdfError("MARKDOWN_TABLE_HEADER_STRUCTURE_INVALID")
            in_row = True
            cells = []
        elif in_head and token.type == "tr_close":
            if not in_row or not cells:
                raise PaperDraftPdfError("MARKDOWN_TABLE_HEADER_STRUCTURE_INVALID")
            signature = re.sub(r"\s+", " ", " ".join(cells)).strip()
            if not signature:
                raise PaperDraftPdfError("MARKDOWN_TABLE_HEADER_STRUCTURE_INVALID")
            headers.append(signature)
            in_row = False
            cells = []
        elif in_head and in_row and token.type == "inline":
            text = re.sub(r"\s+", " ", token.content).strip()
            if text:
                cells.append(text)
    if in_head or in_row:
        raise PaperDraftPdfError("MARKDOWN_TABLE_HEADER_STRUCTURE_INVALID")
    return headers


def _table_body_numeric_probes(
    tokens: Sequence[Any],
    table_headers: Sequence[str],
) -> list[str]:
    """Return table-body numeric atoms that must never be line-split."""

    header_atoms = {
        atom for header in table_headers for atom in _header_atomic_tokens(header)
    }
    probes: set[str] = set()
    in_body = False
    for token in tokens:
        if token.type == "tbody_open":
            in_body = True
        elif token.type == "tbody_close":
            in_body = False
        elif in_body and token.type == "inline":
            for raw in re.findall(r"\S+", token.content):
                atom = raw.strip("`*_")
                if (
                    atom not in header_atoms
                    and any(character.isdigit() for character in atom)
                    and (
                        any(
                            marker in atom
                            for marker in (",", ".", "/", "%", "±", "–", "—")
                        )
                        or len(atom) >= 3
                    )
                    and re.fullmatch(
                        r"[0-9.,/%+±−–—<>=≤≥~≈()]+(?:[A-Za-z]+)?",
                        atom,
                    )
                    is not None
                ):
                    probes.add(atom)
    return sorted(probes)


def _table_body_contract(
    tokens: Sequence[Any],
) -> tuple[list[list[str]], list[str]]:
    """Return per-table tbody row signatures and single-token cell probes."""

    table_rows: list[list[str]] = []
    rows: list[str] = []
    atomic_cells: set[str] = set()
    in_body = False
    in_row = False
    in_cell = False
    cells: list[str] = []
    cell_parts: list[str] = []
    for token in tokens:
        if token.type == "tbody_open":
            if in_body:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            in_body = True
            rows = []
        elif token.type == "tbody_close":
            if not in_body or in_row or in_cell:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            if not rows:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_EMPTY")
            table_rows.append(rows)
            in_body = False
        elif in_body and token.type == "tr_open":
            if in_row:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            in_row = True
            cells = []
        elif in_body and token.type == "tr_close":
            if not in_row or in_cell or not cells:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            if any(TABLE_ROW_CELL_SEPARATOR in cell for cell in cells):
                raise PaperDraftPdfError("MARKDOWN_TABLE_ROW_SEPARATOR_COLLISION")
            rows.append(TABLE_ROW_CELL_SEPARATOR.join(cells))
            in_row = False
            cells = []
        elif in_body and in_row and token.type == "td_open":
            if in_cell:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            in_cell = True
            cell_parts = []
        elif in_body and in_row and in_cell and token.type == "inline":
            cell_parts.append(token.content)
        elif in_body and in_row and token.type == "td_close":
            if not in_cell:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
            text = re.sub(r"\s+", " ", " ".join(cell_parts)).strip()
            if not text:
                raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_CELL_EMPTY")
            cells.append(text)
            if len(text) >= 2 and not any(character.isspace() for character in text):
                atomic_cells.add(text)
            in_cell = False
            cell_parts = []
    if in_body or in_row or in_cell:
        raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_STRUCTURE_INVALID")
    if not table_rows:
        raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_EMPTY")
    return table_rows, sorted(atomic_cells)


def _html_wrapper(config: RenderConfig, body: str) -> bytes:
    title = config.namespace.title
    css = """
@page { size: A4 portrait; margin: 14mm 13mm 14mm 13mm; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "Noto Sans CJK KR", "Noto Sans KR", sans-serif;
  font-size: 9.4pt; line-height: 1.42; color: #172033;
}
h1 { font-size: 19pt; line-height: 1.18; margin: 0 0 7mm; color: #14213d; }
h2 { font-size: 14pt; line-height: 1.2; margin: 6mm 0 2.5mm; color: #193b68; }
h3 { font-size: 11.5pt; line-height: 1.25; margin: 4mm 0 2mm; }
h4 { font-size: 10.5pt; line-height: 1.25; margin: 3.5mm 0 1.8mm; }
h5 { font-size: 9.8pt; line-height: 1.25; margin: 3mm 0 1.5mm; }
p, blockquote, ul, ol { margin: 1.8mm 0; }
blockquote { border-left: 2.5pt solid #789; padding-left: 3mm; color: #40516b; }
code {
  font-family: "Noto Sans Mono CJK KR", monospace;
  font-size: 8.5pt; overflow-wrap: anywhere;
}
a { color: #174ea6; text-decoration: underline; }
table {
  border-collapse: collapse; width: 100%; min-width: 100%; table-layout: auto;
  margin: 2.5mm 0 4mm; font-size: 7.6pt; line-height: 1.22;
  page-break-inside: auto;
}
thead { display: table-header-group; }
tbody tr, tbody td {
  break-inside: avoid-page !important;
  page-break-inside: avoid !important;
}
th, td {
  border: 0.5pt solid #8a96a8; padding: 1.15mm 1.25mm;
  vertical-align: top; overflow-wrap: normal; word-break: normal;
  hyphens: none;
}
th { background: #eaf0f8; font-weight: 700; white-space: nowrap; }
td.atomic-cell { white-space: nowrap; }
table.dense-table { font-size: 6.9pt; }
table.dense-table th, table.dense-table td { padding: 0.8mm 0.7mm; }
figure { margin: 4mm auto 5mm; page-break-inside: avoid; text-align: center; }
img { display: block; margin: 0 auto; width: 680px; height: 425px; }
figcaption { font-size: 8pt; color: #53657d; margin-top: 1.5mm; }
""".strip()
    document = (
        "<!doctype html>\n"
        '<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
    return document.encode("utf-8")


def render_sanitized_html(
    config: RenderConfig,
    manuscript: str,
) -> HtmlArtifact:
    """Render Markdown with HTML disabled and a closed token/tag allowlist."""

    try:
        from importlib.metadata import version as distribution_version

        import markdown_it  # type: ignore[import-not-found]
        from markdown_it import MarkdownIt  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise PaperDraftPdfError("MARKDOWN_IT_MISSING") from exc
    installed_version = distribution_version("markdown-it-py")
    if (
        markdown_it.__version__ != config.policy["markdown_parser_version"]
        or installed_version != config.policy["markdown_parser_version"]
    ):
        raise PaperDraftPdfError("MARKDOWN_IT_VERSION_MISMATCH")
    prepared, prestripped_links = _preprocess_markdown(manuscript)
    parser = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
        },
    ).enable("table")
    tokens = parser.parse(prepared)
    unexpected = sorted({token.type for token in tokens} - TOP_LEVEL_TOKEN_ALLOWLIST)
    if unexpected:
        raise PaperDraftPdfError(
            "MARKDOWN_TOKEN_NOT_ALLOWED",
            ",".join(unexpected),
        )
    _sanitize_structural_token_attributes(tokens)
    table_count = sum(token.type == "table_open" for token in tokens)
    if table_count != config.policy["expected_markdown_tables"]:
        raise PaperDraftPdfError("MARKDOWN_TABLE_COUNT_INVALID")
    https_links, token_stripped_links = _mutate_links(tokens)
    stripped_links = prestripped_links + token_stripped_links
    if len(https_links) != config.policy["expected_https_links"]:
        raise PaperDraftPdfError("MARKDOWN_HTTPS_LINK_COUNT_INVALID")
    body = parser.renderer.render(tokens, parser.options, {})
    placeholder = f"<p>{COHORT_PLACEHOLDER}</p>"
    if body.count(placeholder) != 1:
        raise PaperDraftPdfError("MARKDOWN_COHORT_PLACEHOLDER_INVALID")
    figure = (
        "<figure>"
        f'<img src="{COHORT_RESOURCE}" width="680" height="425" '
        'alt="AI Hub 71490 학습·개발 cohort 흐름">'
        "<figcaption>AI Hub 71490 학습·개발 cohort 흐름"
        " — 외부 전문가 정렬 감사 PENDING</figcaption>"
        "</figure>"
    )
    body = body.replace(placeholder, figure)
    payload = _html_wrapper(config, body)
    lowered = payload.lower()
    if any(
        value in lowered
        for value in (
            b"javascript:",
            b"@import",
            b"url(",
            b"<script",
            b"<iframe",
            b"<object",
            b"<embed",
        )
    ):
        raise PaperDraftPdfError("HTML_UNSAFE_CONTENT")
    validator = _SanitizedHtmlParser()
    try:
        validator.feed(payload.decode("utf-8"))
        validator.close()
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("HTML_UTF8_INVALID") from exc
    if validator.body_depth != 0 or not any(
        part.strip() for part in validator.visible_text_parts
    ):
        raise PaperDraftPdfError("HTML_BODY_STRUCTURE_INVALID")
    if validator.images != [
        {
            "src": COHORT_RESOURCE,
            "width": "680",
            "height": "425",
            "alt": "AI Hub 71490 학습·개발 cohort 흐름",
        }
    ]:
        raise PaperDraftPdfError("HTML_IMAGE_CONTRACT_INVALID")
    headers = _table_headers(tokens)
    if len(headers) != table_count or len(set(headers)) != len(headers):
        raise PaperDraftPdfError("MARKDOWN_TABLE_HEADER_COLLISION")
    numeric_probes = _table_body_numeric_probes(tokens, headers)
    body_row_groups, atomic_cell_probes = _table_body_contract(tokens)
    if len(body_row_groups) != table_count:
        raise PaperDraftPdfError("MARKDOWN_TABLE_BODY_COUNT_INVALID")
    body_rows = [row for rows in body_row_groups for row in rows]
    return HtmlArtifact(
        payload=payload,
        audit={
            "status": "passed",
            "parser": "markdown-it-py",
            "parser_version": markdown_it.__version__,
            "parser_distribution_version": installed_version,
            "html_enabled": False,
            "top_level_token_types": sorted({token.type for token in tokens}),
            "inline_token_types": sorted(
                {child.type for token in tokens for child in (token.children or [])}
            ),
            "table_count": table_count,
            "table_headers_sha256": _canonical_string_hash(headers),
            "table_header_probes": headers,
            "table_body_numeric_probe_count": len(numeric_probes),
            "table_body_numeric_probes_sha256": _canonical_string_hash(numeric_probes),
            "table_body_numeric_probes": numeric_probes,
            "table_body_row_count": len(body_rows),
            "table_body_row_signatures_sha256": _canonical_string_hash(body_rows),
            "table_body_row_signatures": body_rows,
            "table_body_row_groups": body_row_groups,
            "table_body_atomic_cell_probe_count": len(atomic_cell_probes),
            "table_body_atomic_cell_probes_sha256": _canonical_string_hash(
                atomic_cell_probes
            ),
            "table_body_atomic_cell_probes": atomic_cell_probes,
            "visible_body_text_sha256": _sha256(
                "\n".join(validator.visible_text_parts).encode("utf-8")
            ),
            "https_link_count": len(https_links),
            "https_links_sha256": _canonical_string_hash(https_links),
            "stripped_non_https_link_count": stripped_links,
            "raw_html_count": 0,
            "comment_marker_count_removed": 2,
            "cohort_source_link_seen": 1,
            "cohort_svg_image_seen": 1,
            "cohort_svg_width": 680,
            "cohort_svg_height": 425,
        },
    )


def _fixed_environment(temporary_root: Path, source_date_epoch: int) -> dict[str, str]:
    isolation_root = temporary_root / "isolated-environment"
    home = isolation_root / "home"
    config = isolation_root / "xdg-config"
    cache = isolation_root / "xdg-cache"
    data = isolation_root / "xdg-data"
    runtime = isolation_root / "xdg-runtime"
    for directory in (home, config, cache, data, runtime):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "SAL_USE_VCLPLUGIN": "svp",
        "SOURCE_DATE_EPOCH": str(source_date_epoch),
        "TZ": "UTC",
        "TMPDIR": str(temporary_root),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "XDG_DATA_HOME": str(data),
        "XDG_RUNTIME_DIR": str(runtime),
    }


def _run_checked(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    environment: Mapping[str, str],
    working_directory: Path,
    code: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
            cwd=working_directory,
            start_new_session=True,
            close_fds=True,
            pass_fds=(),
        )
    except OSError as exc:
        raise PaperDraftPdfError(f"{code}_START_FAILED") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise PaperDraftPdfError(f"{code}_TIMEOUT") from exc
    completed = subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if completed.returncode != 0:
        # The session leader may already have exited while a redirected or
        # detached-looking descendant remains in the inherited process group.
        # Clean the whole builder-owned session on every abnormal termination,
        # not only on timeout.
        _terminate_process_group(process)
        detail = (completed.stderr or completed.stdout)[-300:].decode(
            "utf-8",
            errors="replace",
        )
        raise PaperDraftPdfError(f"{code}_FAILED", detail)
    # A well-behaved command leaves its builder-owned process group empty.
    # Clean any redirected orphan before returning success as defense in depth.
    _terminate_process_group(process)
    return completed


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort TERM→KILL cleanup for the builder-owned process session."""

    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.communicate()
    # Reap any direct descendants that were still in the process group.  Most
    # grandchildren are re-parented by the kernel, but this closes the direct
    # child case without blocking on unrelated processes.
    while True:
        try:
            waited, _ = os.waitpid(-process_group, os.WNOHANG)
        except (ChildProcessError, ProcessLookupError):
            break
        if waited <= 0:
            break


def _file_trace_audit(
    payload: bytes,
    *,
    workspace_root: Path,
    isolation_root: Path,
) -> Mapping[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("FILE_TRACE_UTF8_INVALID") from exc
    if not text.strip():
        raise PaperDraftPdfError("FILE_TRACE_EMPTY")
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    forbidden_literals = {
        workspace_root.as_posix().casefold(),
        real_home.as_posix().casefold(),
        "/.env",
        "/raw/",
        "/restricted/",
        "/locked/",
        "/secret/",
    }
    isolation = isolation_root.resolve().as_posix().casefold()
    violations: set[str] = set()
    for match in re.finditer(r'"((?:\\.|[^"\\])*)"', text):
        value = match.group(1).replace(r"\"", '"').replace(r"\\", "\\")
        lowered = value.casefold()
        if lowered.startswith(isolation):
            continue
        for forbidden in forbidden_literals:
            if forbidden and (
                lowered == forbidden
                or lowered.startswith(forbidden + "/")
                or forbidden in lowered
            ):
                violations.add(forbidden)
    if violations:
        raise PaperDraftPdfError(
            "FILE_TRACE_FORBIDDEN_ACCESS",
            ",".join(sorted(violations)),
        )
    return {
        "strace_file_trace": True,
        "isolated_working_directory": True,
        "isolated_home": True,
        "isolated_xdg": True,
        "workspace_access_count": 0,
        "real_home_access_count": 0,
        "environment_file_access_count": 0,
        "raw_restricted_locked_secret_access_count": 0,
        "runtime_closure": (
            "primary executables content-pinned; helper executables and "
            "dynamic libraries observed by file trace but not content-pinned"
        ),
    }


def _run_file_traced_checked(
    command: Sequence[str],
    *,
    strace_path: str,
    trace_path: Path,
    timeout_seconds: float,
    environment: Mapping[str, str],
    working_directory: Path,
    workspace_root: Path,
    code: str,
) -> subprocess.CompletedProcess[bytes]:
    traced_command = [
        strace_path,
        "-f",
        "--kill-on-exit",
        "-qq",
        "-s",
        "512",
        "-e",
        "trace=file",
        "-o",
        str(trace_path),
        "--",
        *command,
    ]
    completed = _run_checked(
        traced_command,
        timeout_seconds=timeout_seconds,
        environment=environment,
        working_directory=working_directory,
        code=code,
    )
    if (
        not trace_path.is_file()
        or trace_path.is_symlink()
        or trace_path.stat().st_nlink != 1
        or trace_path.stat().st_size > MAX_ARTIFACT_BYTES
    ):
        raise PaperDraftPdfError("FILE_TRACE_OUTPUT_INVALID", code)
    _file_trace_audit(
        trace_path.read_bytes(),
        workspace_root=workspace_root,
        isolation_root=working_directory,
    )
    return completed


def _network_subset_from_combined_trace(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("COMBINED_TRACE_UTF8_INVALID") from exc
    selected: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        if re.fullmatch(r"[0-9]+\s+--- SIGCHLD \{.*\} ---", line):
            selected.append(line)
            continue
        call_match = re.match(
            r"^[0-9]+\s+([a-z][a-z0-9_]*)\(",
            line,
        )
        resumed_match = re.match(
            r"^[0-9]+\s+<\.\.\. ([a-z][a-z0-9_]*) resumed>",
            line,
        )
        if call_match is None and resumed_match is None:
            raise PaperDraftPdfError(
                "COMBINED_TRACE_LINE_INVALID",
                str(line_number),
            )
        if call_match is not None:
            call = call_match.group(1)
        else:
            assert resumed_match is not None
            call = resumed_match.group(1)
        if call in NETWORK_SYSCALLS:
            selected.append(line)
    return ("\n".join(selected) + ("\n" if selected else "")).encode("utf-8")


def _run_combined_traced_checked(
    command: Sequence[str],
    *,
    strace_path: str,
    trace_path: Path,
    timeout_seconds: float,
    environment: Mapping[str, str],
    working_directory: Path,
    workspace_root: Path,
    code: str,
) -> tuple[subprocess.CompletedProcess[bytes], Mapping[str, Any]]:
    traced_command = [
        strace_path,
        "-f",
        "--kill-on-exit",
        "-qq",
        "-s",
        "512",
        "-e",
        "trace=network,file",
        "-o",
        str(trace_path),
        "--",
        *command,
    ]
    completed = _run_checked(
        traced_command,
        timeout_seconds=timeout_seconds,
        environment=environment,
        working_directory=working_directory,
        code=code,
    )
    if (
        not trace_path.is_file()
        or trace_path.is_symlink()
        or trace_path.stat().st_nlink != 1
        or trace_path.stat().st_size > MAX_ARTIFACT_BYTES
    ):
        raise PaperDraftPdfError("COMBINED_TRACE_OUTPUT_INVALID", code)
    payload = trace_path.read_bytes()
    _file_trace_audit(
        payload,
        workspace_root=workspace_root,
        isolation_root=working_directory,
    )
    network = _network_trace_audit(_network_subset_from_combined_trace(payload))
    return completed, network


def _run_offline_traced_checked(
    command: Sequence[str],
    *,
    strace_path: str,
    trace_path: Path,
    timeout_seconds: float,
    environment: Mapping[str, str],
    working_directory: Path,
    workspace_root: Path,
    code: str,
) -> subprocess.CompletedProcess[bytes]:
    completed, network = _run_combined_traced_checked(
        command,
        strace_path=strace_path,
        trace_path=trace_path,
        timeout_seconds=timeout_seconds,
        environment=environment,
        working_directory=working_directory,
        workspace_root=workspace_root,
        code=code,
    )
    _network_contract(network)
    return completed


def _strip_strace_quoted_strings(value: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
                result.append('"')
            continue
        result.append(character)
        if character == '"':
            in_string = True
    if in_string:
        raise PaperDraftPdfError("NETWORK_TRACE_STRING_UNTERMINATED")
    return "".join(result)


def _network_trace_audit(payload: bytes) -> Mapping[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("NETWORK_TRACE_UTF8_INVALID") from exc
    signal_pattern = re.compile(
        r"^(?P<pid>[0-9]+)\s+--- SIGCHLD "
        r"\{si_signo=SIGCHLD, si_code=[A-Z_]+, si_pid=[0-9]+, "
        r"si_uid=[0-9]+, si_status=[0-9]+, si_utime=[0-9]+"
        r"(?: /\* [0-9.]+ s \*/)?\, si_stime=[0-9]+"
        r"(?: /\* [0-9.]+ s \*/)?\} ---$"
    )
    call_pattern = re.compile(
        r"^(?P<pid>[0-9]+)\s+(?P<call>[a-z][a-z0-9_]*)\((?P<body>.*)$"
    )
    resumed_pattern = re.compile(
        r"^(?P<pid>[0-9]+)\s+<\.\.\. "
        r"(?P<call>[a-z][a-z0-9_]*) resumed>(?P<body>.*)$"
    )
    unfinished: dict[tuple[str, str], int] = {}
    structural_families: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        if signal_pattern.fullmatch(line):
            continue
        resumed = resumed_pattern.fullmatch(line)
        if resumed is not None:
            call = resumed.group("call")
            key = (resumed.group("pid"), call)
            if call not in NETWORK_SYSCALLS or unfinished.get(key, 0) < 1:
                raise PaperDraftPdfError(
                    "NETWORK_TRACE_RESUME_INVALID",
                    str(line_number),
                )
            unfinished[key] -= 1
            resumed_body = _strip_strace_quoted_strings(resumed.group("body"))
            if re.search(r"\bSCM_RIGHTS\b", resumed_body):
                raise PaperDraftPdfError(
                    "NETWORK_SCM_RIGHTS_FORBIDDEN",
                    str(line_number),
                )
            resumed_families = re.findall(
                r"\bsa_family=(AF_[A-Z0-9_]+)\b",
                resumed_body,
            )
            structural_families.extend(resumed_families)
            resumed_scrubbed = re.sub(
                r"\bsa_family=AF_[A-Z0-9_]+\b",
                "",
                resumed_body,
            )
            if re.search(r"\bAF_[A-Z0-9_]+\b", resumed_scrubbed):
                raise PaperDraftPdfError(
                    "NETWORK_FAMILY_CONTEXT_INVALID",
                    str(line_number),
                )
            continue
        match = call_pattern.fullmatch(line)
        if match is None or match.group("call") not in NETWORK_SYSCALLS:
            raise PaperDraftPdfError(
                "NETWORK_TRACE_LINE_INVALID",
                str(line_number),
            )
        call = match.group("call")
        body = _strip_strace_quoted_strings(match.group("body"))
        if re.search(r"\bSCM_RIGHTS\b", body):
            raise PaperDraftPdfError(
                "NETWORK_SCM_RIGHTS_FORBIDDEN",
                str(line_number),
            )
        if body.endswith("<unfinished ...>"):
            key = (match.group("pid"), call)
            unfinished[key] = unfinished.get(key, 0) + 1
        if call in {"socket", "socketpair"}:
            family_match = re.match(r"\s*(AF_[A-Z0-9_]+)\s*,", body)
            if family_match is None:
                raise PaperDraftPdfError(
                    "NETWORK_SOCKET_FAMILY_INVALID",
                    str(line_number),
                )
            structural_families.append(family_match.group(1))
        sockaddr_families = re.findall(
            r"\bsa_family=(AF_[A-Z0-9_]+)\b",
            body,
        )
        structural_families.extend(sockaddr_families)
        scrubbed = re.sub(
            r"\bsa_family=AF_[A-Z0-9_]+\b",
            "",
            body,
        )
        if call in {"socket", "socketpair"}:
            scrubbed = re.sub(
                r"^\s*AF_[A-Z0-9_]+\s*,",
                "",
                scrubbed,
                count=1,
            )
        if re.search(r"\bAF_[A-Z0-9_]+\b", scrubbed):
            raise PaperDraftPdfError(
                "NETWORK_FAMILY_CONTEXT_INVALID",
                str(line_number),
            )
    if any(count for count in unfinished.values()):
        raise PaperDraftPdfError("NETWORK_TRACE_UNFINISHED")
    forbidden = sorted(set(structural_families) - {"AF_UNIX"})
    if forbidden:
        raise PaperDraftPdfError(
            "NETWORK_FAMILY_FORBIDDEN",
            ",".join(forbidden),
        )
    return {
        "strace_network_trace": True,
        "kernel_network_namespace": False,
        "allowed_address_families": ["AF_UNIX"],
        "af_inet_count": structural_families.count("AF_INET"),
        "af_inet6_count": structural_families.count("AF_INET6"),
        "forbidden_family_count": 0,
        "observed_forbidden_address_family_syscalls": 0,
        "scm_rights_transfer_count": 0,
        "observation_only_not_firewall": True,
        "relative_or_local_pdf_annotation_allowed": False,
    }


def _tool_path(
    tools: Mapping[str, ToolSnapshot],
    key: str,
) -> str:
    try:
        return tools[key].record.path
    except KeyError as exc:
        raise PaperDraftPdfError("TOOL_SNAPSHOT_MISSING", key) from exc


def _libreoffice_pdf_producer(config: RenderConfig) -> str:
    version = config.tools["libreoffice"].version
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\..*)?", version)
    if match is None:
        raise PaperDraftPdfError("LIBREOFFICE_VERSION_FORMAT_INVALID")
    return f"LibreOffice {match.group(1)}.{match.group(2)}"


def _decode_pdf_utf16_hex(value: bytes, code: str) -> str:
    if len(value) % 4 != 0 or not value.startswith(b"FEFF"):
        raise PaperDraftPdfError(code)
    try:
        return bytes.fromhex(value.decode("ascii")).decode("utf-16")
    except (UnicodeError, ValueError) as exc:
        raise PaperDraftPdfError(code) from exc


def _canonicalize_libreoffice_pdf(
    payload: bytes,
    *,
    expected_title: str,
    expected_producer: str,
) -> bytes:
    """Canonicalize only LibreOffice's fixed-width volatile PDF fields."""

    if (
        not payload.startswith(b"%PDF-1.7")
        or len(payload) > MAX_ARTIFACT_BYTES
        or payload.count(b"/CreationDate") != 1
        or payload.count(b"/ModDate") > 1
        or payload.count(b"/ID") != 1
        or payload.count(b"/DocChecksum") != 1
        or payload.count(b"startxref") != 1
    ):
        raise PaperDraftPdfError("LO_PDF_VOLATILE_FIELD_SET_INVALID")
    trailer_pattern = re.compile(
        rb"trailer\n"
        rb"<</Size (?P<size>[1-9][0-9]*)/Root (?P<root>[1-9][0-9]*) 0 R\n"
        rb"/Info (?P<info>[1-9][0-9]*) 0 R\n"
        rb"(?P<id_token>/ID \[ <(?P<id1>[0-9A-F]{32})>\n"
        rb"<(?P<id2>[0-9A-F]{32})> \])\n"
        rb"(?P<checksum_token>/DocChecksum /"
        rb"(?P<checksum>[0-9A-F]{32}))\n"
        rb">>\nstartxref\n(?P<xref>[1-9][0-9]*)\n%%EOF\n?\Z"
    )
    trailer = trailer_pattern.search(payload)
    if trailer is None or trailer.group("id1") != trailer.group("id2"):
        raise PaperDraftPdfError("LO_PDF_TRAILER_INVALID")
    xref_offset = int(trailer.group("xref"))
    if (
        xref_offset >= trailer.start()
        or payload[xref_offset : xref_offset + 5] != b"xref\n"
        or payload.find(b"trailer\n", xref_offset) != trailer.start()
    ):
        raise PaperDraftPdfError("LO_PDF_STARTXREF_INVALID")
    info_number = trailer.group("info")
    info_marker = b"\n" + info_number + b" 0 obj\n<<"
    if payload.count(info_marker) != 1:
        raise PaperDraftPdfError("LO_PDF_INFO_OBJECT_INVALID")
    info_start = payload.index(info_marker) + len(info_marker)
    info_suffix = b">>\nendobj\n\n"
    if payload[xref_offset - len(info_suffix) : xref_offset] != info_suffix:
        raise PaperDraftPdfError("LO_PDF_INFO_OBJECT_INVALID")
    info_end = xref_offset - len(info_suffix)
    info_body = payload[info_start:info_end]
    info_pattern = re.compile(
        rb"/Title<(?P<title>[0-9A-F]+)>\n"
        rb"/Producer<(?P<producer>[0-9A-F]+)>\n"
        rb"(?P<creation>/CreationDate"
        rb"\(D:[0-9]{14}(?:Z'|[+-][0-9]{2}'[0-9]{2}')\))"
        rb"(?:\n(?P<modification>/ModDate"
        rb"\(D:[0-9]{14}(?:Z'|[+-][0-9]{2}'[0-9]{2}')\)))?"
    )
    info = info_pattern.fullmatch(info_body)
    if info is None:
        raise PaperDraftPdfError("LO_PDF_INFO_ALLOWLIST_INVALID")
    if (
        _decode_pdf_utf16_hex(
            info.group("title"),
            "LO_PDF_TITLE_ENCODING_INVALID",
        )
        != expected_title
        or _decode_pdf_utf16_hex(
            info.group("producer"),
            "LO_PDF_PRODUCER_ENCODING_INVALID",
        )
        != expected_producer
    ):
        raise PaperDraftPdfError("LO_PDF_INFO_VALUE_INVALID")

    template = bytearray(payload)
    volatile_spans = [
        (
            info_start + info.start("creation"),
            info_start + info.end("creation"),
            b" ",
        ),
        (
            trailer.start("id_token"),
            trailer.end("id_token"),
            b" ",
        ),
        (
            trailer.start("checksum_token"),
            trailer.end("checksum_token"),
            b" ",
        ),
    ]
    modification = info.span("modification")
    if modification != (-1, -1):
        volatile_spans.append(
            (
                info_start + modification[0] - 1,
                info_start + modification[1],
                b" ",
            )
        )
    for start, end, fill in volatile_spans:
        template[start:end] = fill * (end - start)
    result = bytes(template)
    if len(result) != len(payload):
        raise PaperDraftPdfError("LO_PDF_CANONICAL_LENGTH_CHANGED")
    return result


class BuildBackend:
    """Backend seam; only explicit non-production tests may override it."""

    test_only: bool = True
    backend_id: str = "abstract"

    def inspect_tools(
        self,
        config: RenderConfig,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
    ) -> Mapping[str, str]:
        raise NotImplementedError

    def build_once(
        self,
        config: RenderConfig,
        html: bytes,
        svg: bytes,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
        index: int,
    ) -> BuildRun:
        raise NotImplementedError

    def normalize(
        self,
        config: RenderConfig,
        raw_pdf: bytes,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
        index: int,
    ) -> bytes:
        raise NotImplementedError

    def postflight(
        self,
        config: RenderConfig,
        pdf: bytes,
        sources: SourceBundle,
        html: HtmlArtifact,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
    ) -> PostflightResult:
        raise NotImplementedError


class ProductionBackend(BuildBackend):
    test_only = False
    backend_id = "libreoffice-lossless-canonicalizer-gs-validate-poppler-v1"

    def inspect_tools(
        self,
        config: RenderConfig,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
    ) -> Mapping[str, str]:
        commands = {
            "libreoffice": ["--version"],
            "strace": ["--version"],
            "ghostscript": ["--version"],
            "pdfinfo": ["-v"],
            "pdffonts": ["-v"],
            "pdftotext": ["-v"],
            "pdftoppm": ["-v"],
        }
        timeout = float(config.policy["tool_timeout_seconds"])
        version_root = work / "tool-version-probes"
        version_root.mkdir(mode=0o700)
        environment = _fixed_environment(
            version_root,
            int(config.policy["source_date_epoch"]),
        )
        versions: dict[str, str] = {}
        for key in TOOL_KEYS:
            _revalidate_tool(tools[key], f"TOOL_{key.upper()}")
            command = [_tool_path(tools, key), *commands[key]]
            completed = _run_offline_traced_checked(
                command,
                strace_path=_tool_path(tools, "strace"),
                trace_path=version_root / f"{key}.combined.strace",
                timeout_seconds=timeout,
                environment=environment,
                working_directory=version_root,
                workspace_root=config.workspace_root,
                code=f"TOOL_VERSION_{key.upper()}",
            )
            observed = (completed.stdout + b"\n" + completed.stderr).decode(
                "utf-8", errors="replace"
            )
            expected = tools[key].record.version
            if expected not in observed:
                raise PaperDraftPdfError("TOOL_VERSION_MISMATCH", key)
            versions[key] = expected
            _revalidate_tool(tools[key], f"TOOL_{key.upper()}")
        return versions

    def build_once(
        self,
        config: RenderConfig,
        html: bytes,
        svg: bytes,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
        index: int,
    ) -> BuildRun:
        run_root = work / f"libreoffice-{index}"
        stage = run_root / "stage"
        resources = stage / "resources"
        profile = run_root / "profile"
        output = run_root / "output"
        for directory in (resources, profile, output):
            directory.mkdir(parents=True, mode=0o700)
        html_path = stage / "manuscript.html"
        svg_path = resources / "aihub71490_cohort_flow.svg"
        html_path.write_bytes(html)
        svg_path.write_bytes(svg)
        html_path.chmod(0o600)
        svg_path.chmod(0o600)
        trace = run_root / "combined.strace"
        environment = _fixed_environment(
            run_root,
            int(config.policy["source_date_epoch"]),
        )
        command = [
            _tool_path(tools, "libreoffice"),
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output),
            str(html_path),
        ]
        _revalidate_tool(tools["strace"], "TOOL_STRACE")
        _revalidate_tool(tools["libreoffice"], "TOOL_LIBREOFFICE")
        _, network = _run_combined_traced_checked(
            command,
            strace_path=_tool_path(tools, "strace"),
            trace_path=trace,
            timeout_seconds=float(config.policy["libreoffice_timeout_seconds"]),
            environment=environment,
            working_directory=run_root,
            workspace_root=config.workspace_root,
            code="LIBREOFFICE",
        )
        _revalidate_tool(tools["strace"], "TOOL_STRACE")
        _revalidate_tool(tools["libreoffice"], "TOOL_LIBREOFFICE")
        pdf = output / "manuscript.pdf"
        output_entries = sorted(path.name for path in output.iterdir())
        if (
            output_entries != ["manuscript.pdf"]
            or not pdf.is_file()
            or pdf.is_symlink()
        ):
            raise PaperDraftPdfError("LIBREOFFICE_OUTPUT_TREE_INVALID")
        payload = pdf.read_bytes()
        if not payload.startswith(b"%PDF-") or len(payload) > MAX_ARTIFACT_BYTES:
            raise PaperDraftPdfError("LIBREOFFICE_PDF_INVALID")
        return BuildRun(raw_pdf=payload, network=network)

    def normalize(
        self,
        config: RenderConfig,
        raw_pdf: bytes,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
        index: int,
    ) -> bytes:
        root = work / f"ghostscript-validation-{index}"
        root.mkdir(mode=0o700)
        source = root / "canonicalized.pdf"
        expected_producer = _libreoffice_pdf_producer(config)
        payload = _canonicalize_libreoffice_pdf(
            raw_pdf,
            expected_title=config.namespace.title,
            expected_producer=expected_producer,
        )
        source.write_bytes(payload)
        source.chmod(0o600)
        environment = _fixed_environment(
            root,
            int(config.policy["source_date_epoch"]),
        )
        command = [
            _tool_path(tools, "ghostscript"),
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=nullpage",
            str(source),
        ]
        _revalidate_tool(tools["ghostscript"], "TOOL_GHOSTSCRIPT")
        _run_offline_traced_checked(
            command,
            strace_path=_tool_path(tools, "strace"),
            trace_path=root / "combined.strace",
            timeout_seconds=float(config.policy["tool_timeout_seconds"]),
            environment=environment,
            working_directory=root,
            workspace_root=config.workspace_root,
            code="GHOSTSCRIPT",
        )
        _revalidate_tool(tools["ghostscript"], "TOOL_GHOSTSCRIPT")
        if not payload.startswith(b"%PDF-") or len(payload) != len(raw_pdf):
            raise PaperDraftPdfError("PDF_CANONICALIZATION_INVALID")
        return payload

    def postflight(
        self,
        config: RenderConfig,
        pdf: bytes,
        sources: SourceBundle,
        html: HtmlArtifact,
        tools: Mapping[str, ToolSnapshot],
        work: Path,
    ) -> PostflightResult:
        return _production_postflight(
            config,
            pdf,
            sources,
            html,
            tools,
            work,
        )


def _decode_tool_output(value: bytes, code: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError(code) from exc


def _pdfinfo_value(text: str, key: str, code: str) -> str:
    matches = re.findall(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if len(matches) != 1:
        raise PaperDraftPdfError(code, key)
    return matches[0]


def _optional_pdfinfo_value(text: str, key: str, code: str) -> str | None:
    matches = re.findall(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", text)
    if len(matches) > 1:
        raise PaperDraftPdfError(code, key)
    return matches[0] if matches else None


def _pdfinfo_fixed_date_status(
    text: str,
    key: str,
    source_date_epoch: int,
) -> str:
    observed = _optional_pdfinfo_value(
        text,
        key,
        "PDFINFO_METADATA_INVALID",
    )
    if observed is None:
        return "absent"
    try:
        parsed = datetime.strptime(
            _normalized_text(observed),
            "%a %b %d %H:%M:%S %Y UTC",
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PaperDraftPdfError("PDFINFO_METADATA_DATE_INVALID", key) from exc
    if int(parsed.timestamp()) != source_date_epoch:
        raise PaperDraftPdfError("PDFINFO_METADATA_DATE_INVALID", key)
    return "fixed_source_date_epoch"


def _parse_positive_decimal_integer(value: str, code: str) -> int:
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise PaperDraftPdfError(code, value)
    return int(value)


def _parse_xmp_metadata(
    payload: bytes,
    *,
    expected_title: str,
    expected_producer: str,
) -> Mapping[str, Any]:
    lowered = payload.lower()
    if (
        not payload
        or b"<!--" in payload
        or b"-->" in payload
        or b"<!doctype" in lowered
        or b"<!entity" in lowered
        or b"creationdate" in lowered
        or b"modifydate" in lowered
        or b"metadatadate" in lowered
        or b"http://"
        in lowered.replace(
            b"http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            b"",
        )
        .replace(
            b"http://ns.adobe.com/ix/1.0/",
            b"",
        )
        .replace(
            b"http://ns.adobe.com/pdf/1.3/",
            b"",
        )
        .replace(
            b"http://ns.adobe.com/xap/1.0/",
            b"",
        )
        .replace(
            b"http://ns.adobe.com/xap/1.0/mm/",
            b"",
        )
        .replace(
            b"http://purl.org/dc/elements/1.1/",
            b"",
        )
    ):
        raise PaperDraftPdfError("PDF_XMP_UNSAFE")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("PDF_XMP_UTF8_INVALID") from exc
    processing_instructions = re.findall(r"<\?.*?\?>", text, flags=re.DOTALL)
    expected_instructions = [
        "<?xpacket begin='\ufeff' id='W5M0MpCehiHzreSzNTczkc9d'?>",
        '<?adobe-xap-filters esc="CRLF"?>',
        "<?xpacket end='w'?>",
    ]
    if processing_instructions != expected_instructions:
        raise PaperDraftPdfError("PDF_XMP_PROCESSING_INSTRUCTION_INVALID")
    root_start = text.find("<x:xmpmeta")
    root_end = text.rfind("</x:xmpmeta>")
    if (
        root_start < 0
        or root_end < root_start
        or text[:root_start].strip() != "\n".join(expected_instructions[:2])
        or text[root_end + len("</x:xmpmeta>") :].strip() != expected_instructions[2]
    ):
        raise PaperDraftPdfError("PDF_XMP_PACKET_BOUNDARY_INVALID")
    expected_namespaces = [
        ("x", "adobe:ns:meta/"),
        ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
        ("iX", "http://ns.adobe.com/iX/1.0/"),
        ("pdf", "http://ns.adobe.com/pdf/1.3/"),
        ("xmp", "http://ns.adobe.com/xap/1.0/"),
        ("xapMM", "http://ns.adobe.com/xap/1.0/mm/"),
        ("dc", "http://purl.org/dc/elements/1.1/"),
    ]
    try:
        namespaces = [
            namespace
            for _, namespace in ET.iterparse(
                io.BytesIO(payload),
                events=("start-ns",),
            )
        ]
        if namespaces != expected_namespaces:
            raise PaperDraftPdfError("PDF_XMP_NAMESPACE_SET_INVALID")
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise PaperDraftPdfError("PDF_XMP_XML_INVALID") from exc
    xmp_ns = "adobe:ns:meta/"
    rdf_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    pdf_ns = "http://ns.adobe.com/pdf/1.3/"
    xap_ns = "http://ns.adobe.com/xap/1.0/"
    xapmm_ns = "http://ns.adobe.com/xap/1.0/mm/"
    dc_ns = "http://purl.org/dc/elements/1.1/"
    xml_ns = "http://www.w3.org/XML/1998/namespace"
    if (
        root.tag != f"{{{xmp_ns}}}xmpmeta"
        or root.attrib != {f"{{{xmp_ns}}}xmptk": XMP_TOOLKIT}
        or (root.text or "").strip()
        or (root.tail or "").strip()
    ):
        raise PaperDraftPdfError("PDF_XMP_ROOT_INVALID")
    root_children = list(root)
    if len(root_children) != 1 or root_children[0].tag != f"{{{rdf_ns}}}RDF":
        raise PaperDraftPdfError("PDF_XMP_TREE_INVALID")
    rdf = root_children[0]
    if rdf.attrib or (rdf.text or "").strip() or (rdf.tail or "").strip():
        raise PaperDraftPdfError("PDF_XMP_TREE_INVALID")
    descriptions = list(rdf)
    if len(descriptions) != 4 or any(
        item.tag != f"{{{rdf_ns}}}Description" for item in descriptions
    ):
        raise PaperDraftPdfError("PDF_XMP_DESCRIPTION_SET_INVALID")
    producer_node, creator_node, identifier_node, title_node = descriptions
    if (
        producer_node.attrib
        != {
            f"{{{rdf_ns}}}about": "",
            f"{{{pdf_ns}}}Producer": expected_producer,
        }
        or list(producer_node)
        or (producer_node.text or "").strip()
    ):
        raise PaperDraftPdfError("PDF_XMP_PRODUCER_INVALID")
    if creator_node.attrib != {f"{{{rdf_ns}}}about": ""}:
        raise PaperDraftPdfError("PDF_XMP_CREATOR_INVALID")
    creator_children = list(creator_node)
    if (
        len(creator_children) != 1
        or creator_children[0].tag != f"{{{xap_ns}}}CreatorTool"
        or creator_children[0].attrib
        or (creator_children[0].text or "") != XMP_CREATOR_TOOL
        or list(creator_children[0])
    ):
        raise PaperDraftPdfError("PDF_XMP_CREATOR_INVALID")
    if (
        set(identifier_node.attrib)
        != {
            f"{{{rdf_ns}}}about",
            f"{{{xapmm_ns}}}DocumentID",
        }
        or identifier_node.attrib[f"{{{rdf_ns}}}about"] != ""
    ):
        raise PaperDraftPdfError("PDF_XMP_DOCUMENT_ID_INVALID")
    document_id = identifier_node.attrib[f"{{{xapmm_ns}}}DocumentID"]
    if (
        re.fullmatch(
            r"uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            document_id,
        )
        is None
        or list(identifier_node)
        or (identifier_node.text or "").strip()
    ):
        raise PaperDraftPdfError("PDF_XMP_DOCUMENT_ID_INVALID")
    if title_node.attrib != {
        f"{{{rdf_ns}}}about": "",
        f"{{{dc_ns}}}format": "application/pdf",
    }:
        raise PaperDraftPdfError("PDF_XMP_TITLE_INVALID")
    title_children = list(title_node)
    if (
        len(title_children) != 1
        or title_children[0].tag != f"{{{dc_ns}}}title"
        or title_children[0].attrib
    ):
        raise PaperDraftPdfError("PDF_XMP_TITLE_INVALID")
    alt_children = list(title_children[0])
    if (
        len(alt_children) != 1
        or alt_children[0].tag != f"{{{rdf_ns}}}Alt"
        or alt_children[0].attrib
    ):
        raise PaperDraftPdfError("PDF_XMP_TITLE_INVALID")
    item_children = list(alt_children[0])
    if (
        len(item_children) != 1
        or item_children[0].tag != f"{{{rdf_ns}}}li"
        or item_children[0].attrib != {f"{{{xml_ns}}}lang": "x-default"}
        or (item_children[0].text or "") != expected_title
        or list(item_children[0])
    ):
        raise PaperDraftPdfError("PDF_XMP_TITLE_INVALID")
    text_leaves = {creator_children[0], item_children[0]}
    for element in root.iter():
        if element not in text_leaves and element.text and element.text.strip():
            raise PaperDraftPdfError("PDF_XMP_TEXT_INVALID")
        if element.tail and element.tail.strip():
            raise PaperDraftPdfError("PDF_XMP_TEXT_INVALID")
    return {
        "allowlist_status": "passed",
        "producer": expected_producer,
        "creator_tool": XMP_CREATOR_TOOL,
        "document_id": document_id,
        "title": expected_title,
        "format": "application/pdf",
        "date_fields_present": False,
        "sha256": _sha256(payload),
        "size_bytes": len(payload),
    }


def _parse_pdffonts(text: str) -> tuple[list[str], bool, bool]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    data = [
        line
        for line in lines
        if not line.startswith("name ")
        and re.fullmatch(r"(?:-+\s+)*-+", line.strip()) is None
        and not line.startswith("Syntax")
    ]
    if not data:
        raise PaperDraftPdfError("PDFFONTS_EMPTY")
    names: list[str] = []
    all_embedded = True
    all_unicode = True
    pattern = re.compile(
        r"^(?P<name>\S+)\s+.+?\s+(?P<emb>yes|no)\s+"
        r"(?:yes|no)\s+(?P<uni>yes|no)\s+\d+\s+\d+\s*$"
    )
    for line in data:
        match = pattern.fullmatch(line)
        if match is None:
            raise PaperDraftPdfError("PDFFONTS_PARSE_FAILED", line[:100])
        names.append(match.group("name"))
        all_embedded = all_embedded and match.group("emb") == "yes"
        all_unicode = all_unicode and match.group("uni") == "yes"
    return sorted(names), all_embedded, all_unicode


def _parse_pdf_urls(text: str) -> list[str]:
    urls: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line == "Page  Type          URL":
            continue
        match = re.match(r"^\d+\s+\S+\s+(.+)$", line)
        if match is None:
            raise PaperDraftPdfError("PDF_URL_PARSE_FAILED", line[:100])
        url = match.group(1).strip()
        if not _is_safe_https(url):
            raise PaperDraftPdfError("PDF_URL_NOT_HTTPS", url)
        urls.append(url)
    return urls


def _parse_pdf_page_sizes(
    text: str,
    expected_pages: int,
) -> list[tuple[int, float, float]]:
    geometry = _parse_pdf_page_geometry(text, expected_pages)
    return [
        (
            int(record["page"]),
            float(record["width"]),
            float(record["height"]),
        )
        for record in geometry
    ]


def _parse_pdf_page_geometry(
    text: str,
    expected_pages: int,
) -> list[dict[str, Any]]:
    """Parse every numbered Poppler page geometry field exactly once."""

    fields: dict[int, dict[str, Any]] = {}

    def page_record(number: str) -> dict[str, Any]:
        page = int(number)
        if page < 1 or page > expected_pages:
            raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
        return fields.setdefault(page, {})

    size_pattern = re.compile(
        r"^Page\s+(\d+)\s+size:\s+"
        r"([0-9]+(?:\.[0-9]+)?)\s+x\s+"
        r"([0-9]+(?:\.[0-9]+)?)\s+pts(?:\s+\(A4\))?\s*$"
    )
    rotation_pattern = re.compile(r"^Page\s+(\d+)\s+rot:\s+(-?\d+)\s*$")
    box_pattern = re.compile(
        r"^Page\s+(\d+)\s+"
        r"(MediaBox|CropBox|BleedBox|TrimBox|ArtBox):\s+"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s+"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s+"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s+"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s*$"
    )
    for line in text.splitlines():
        size = size_pattern.fullmatch(line)
        if size is not None:
            record = page_record(size.group(1))
            if "width" in record or "height" in record:
                raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_DUPLICATE")
            record["width"] = float(size.group(2))
            record["height"] = float(size.group(3))
            continue
        rotation = rotation_pattern.fullmatch(line)
        if rotation is not None:
            record = page_record(rotation.group(1))
            if "rotation" in record:
                raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_DUPLICATE")
            record["rotation"] = int(rotation.group(2))
            continue
        box = box_pattern.fullmatch(line)
        if box is not None:
            record = page_record(box.group(1))
            boxes = record.setdefault("boxes", {})
            name = box.group(2)
            if name in boxes:
                raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_DUPLICATE")
            boxes[name] = [float(box.group(index)) for index in range(3, 7)]

    expected = list(range(1, expected_pages + 1))
    if sorted(fields) != expected:
        raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
    result: list[dict[str, Any]] = []
    for page in expected:
        record = fields[page]
        if set(record) != {"width", "height", "rotation", "boxes"} or set(
            record["boxes"]
        ) != set(PDF_PAGE_BOX_NAMES):
            raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
        result.append(
            {
                "page": page,
                "width": record["width"],
                "height": record["height"],
                "rotation": record["rotation"],
                "boxes": {name: record["boxes"][name] for name in PDF_PAGE_BOX_NAMES},
            }
        )
    return result


def _validate_a4_page_geometry(
    geometry: Sequence[Mapping[str, Any]],
    expected_pages: int,
) -> None:
    a4_width, a4_height = 595.276, 841.89
    if len(geometry) != expected_pages:
        raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
    for expected_page, record in enumerate(geometry, start=1):
        if (
            record.get("page") != expected_page
            or record.get("rotation") != 0
            or abs(float(record.get("width", -1.0)) - a4_width) > 1.0
            or abs(float(record.get("height", -1.0)) - a4_height) > 1.0
        ):
            raise PaperDraftPdfError("PDF_PAGE_NOT_A4")
        boxes = record.get("boxes")
        if not isinstance(boxes, Mapping) or set(boxes) != set(PDF_PAGE_BOX_NAMES):
            raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
        for name in PDF_PAGE_BOX_NAMES:
            coordinates = boxes[name]
            if (
                not isinstance(coordinates, list)
                or len(coordinates) != 4
                or any(
                    not isinstance(value, (int, float)) or isinstance(value, bool)
                    for value in coordinates
                )
            ):
                raise PaperDraftPdfError("PDF_PAGE_GEOMETRY_SET_INVALID")
            x0, y0, x1, y1 = (float(value) for value in coordinates)
            if (
                abs(x0) > 1.0
                or abs(y0) > 1.0
                or abs(x1 - a4_width) > 1.0
                or abs(y1 - a4_height) > 1.0
            ):
                raise PaperDraftPdfError("PDF_PAGE_BOX_NOT_A4", name)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    diagonal_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= diagonal_distance:
        return left
    if up_distance <= diagonal_distance:
        return up
    return upper_left


def _png_decode(payload: bytes) -> tuple[int, int, float, bytes]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PaperDraftPdfError("PNG_HEADER_INVALID")
    offset = 8
    width = height = bit_depth = color_type = interlace = -1
    compressed = bytearray()
    saw_end = False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise PaperDraftPdfError("PNG_CHUNK_TRUNCATED")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(payload):
            raise PaperDraftPdfError("PNG_CHUNK_TRUNCATED")
        data = payload[start:end]
        expected_crc = struct.unpack(">I", payload[end : end + 4])[0]
        actual_crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise PaperDraftPdfError("PNG_CRC_INVALID")
        if kind == b"IHDR":
            if length != 13:
                raise PaperDraftPdfError("PNG_IHDR_INVALID")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", data)
            if compression != 0 or filtering != 0:
                raise PaperDraftPdfError("PNG_IHDR_INVALID")
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            saw_end = True
            if end + 4 != len(payload):
                raise PaperDraftPdfError("PNG_TRAILING_BYTES")
        offset = end + 4
    channels = {0: 1, 2: 3, 6: 4}.get(color_type)
    if (
        not saw_end
        or width <= 0
        or height <= 0
        or bit_depth != 8
        or channels is None
        or interlace != 0
    ):
        raise PaperDraftPdfError("PNG_FORMAT_UNSUPPORTED")
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise PaperDraftPdfError("PNG_DEFLATE_INVALID") from exc
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise PaperDraftPdfError("PNG_SCANLINE_SIZE_INVALID")
    previous = bytearray(stride)
    white = 0
    rgb = bytearray(width * height * 3)
    rgb_cursor = 0
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        decoded = bytearray(stride)
        for index, byte in enumerate(encoded):
            left = decoded[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                value = byte
            elif filter_type == 1:
                value = (byte + left) & 0xFF
            elif filter_type == 2:
                value = (byte + up) & 0xFF
            elif filter_type == 3:
                value = (byte + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                value = (byte + _paeth(left, up, upper_left)) & 0xFF
            else:
                raise PaperDraftPdfError("PNG_FILTER_INVALID")
            decoded[index] = value
        for pixel in range(width):
            start = pixel * channels
            if color_type == 0:
                red = green = blue = decoded[start]
            else:
                red, green, blue = decoded[start : start + 3]
            is_white = red >= 250 and green >= 250 and blue >= 250
            if is_white:
                white += 1
            rgb[rgb_cursor : rgb_cursor + 3] = bytes((red, green, blue))
            rgb_cursor += 3
        previous = decoded
    return width, height, white / (width * height), bytes(rgb)


def _png_audit(payload: bytes) -> tuple[int, int, float]:
    width, height, white_ratio, _ = _png_decode(payload)
    return width, height, white_ratio


def _blank_page_signals(
    white_ratio: float,
    extracted_page_text: str,
    threshold: float,
) -> tuple[bool, bool, bool, str]:
    """Return raster/text decisions and reject invisible extracted text."""

    raster_blank = white_ratio >= threshold
    text_blank = not extracted_page_text.strip()
    if raster_blank and not text_blank:
        raise PaperDraftPdfError("PDF_TEXT_RASTER_VISIBILITY_MISMATCH")
    blank = raster_blank and text_blank
    status = "blank" if blank else "image_only" if text_blank else "visible_text"
    return raster_blank, text_blank, blank, status


def _cohort_figure_audit(
    pages: Sequence[tuple[int, int, int, bytes]],
) -> Mapping[str, Any]:
    """Prove that the distinctive cohort SVG palette survived rasterization."""

    per_page: dict[int, dict[str, int]] = {}
    page_bboxes: dict[int, tuple[int, int, int, int] | None] = {}
    qualifying_pages: list[int] = []
    for number, width, height, pixels in pages:
        if len(pixels) != width * height * 3:
            raise PaperDraftPdfError("COHORT_RASTER_PIXEL_SIZE_INVALID")
        counts = {color: 0 for color in COHORT_PALETTE}
        minimum_x = width
        minimum_y = height
        maximum_x = -1
        maximum_y = -1
        for index in range(width * height):
            offset = index * 3
            pixel = pixels[offset : offset + 3]
            matched = False
            for color, expected in COHORT_PALETTE.items():
                if all(
                    abs(pixel[channel] - expected[channel]) <= COHORT_COLOR_TOLERANCE
                    for channel in range(3)
                ):
                    counts[color] += 1
                    matched = True
                    break
            if matched:
                x = index % width
                y = index // width
                minimum_x = min(minimum_x, x)
                minimum_y = min(minimum_y, y)
                maximum_x = max(maximum_x, x)
                maximum_y = max(maximum_y, y)
        per_page[number] = counts
        page_bboxes[number] = (
            None if maximum_x < 0 else (minimum_x, minimum_y, maximum_x, maximum_y)
        )
        if all(count >= COHORT_COLOR_MIN_PIXELS for count in counts.values()):
            qualifying_pages.append(number)
    if len(qualifying_pages) != 1:
        raise PaperDraftPdfError("COHORT_RASTER_PALETTE_PAGE_INVALID")
    page_number = qualifying_pages[0]
    page = next((item for item in pages if item[0] == page_number), None)
    bbox = page_bboxes[page_number]
    if page is None or bbox is None:
        raise PaperDraftPdfError("COHORT_RASTER_BBOX_INVALID")
    _, width, height, _ = page
    minimum_x, minimum_y, maximum_x, maximum_y = bbox
    bbox_width_ratio = (maximum_x - minimum_x + 1) / width
    bbox_height_ratio = (maximum_y - minimum_y + 1) / height
    if (
        bbox_width_ratio < COHORT_BBOX_MIN_WIDTH_RATIO
        or bbox_height_ratio < COHORT_BBOX_MIN_HEIGHT_RATIO
    ):
        raise PaperDraftPdfError("COHORT_RASTER_BBOX_INVALID")
    return {
        "status": "passed",
        "page": page_number,
        "palette": list(COHORT_PALETTE),
        "color_tolerance": COHORT_COLOR_TOLERANCE,
        "minimum_pixels_per_color": COHORT_COLOR_MIN_PIXELS,
        "color_pixel_counts": per_page[page_number],
        "bbox": [minimum_x, minimum_y, maximum_x, maximum_y],
        "bbox_width_ratio": round(bbox_width_ratio, 8),
        "bbox_height_ratio": round(bbox_height_ratio, 8),
        "minimum_bbox_width_ratio": COHORT_BBOX_MIN_WIDTH_RATIO,
        "minimum_bbox_height_ratio": COHORT_BBOX_MIN_HEIGHT_RATIO,
    }


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _semantic_text_key(text: str) -> str:
    """Canonicalize only Unicode normalization and extraction whitespace."""

    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def _extracted_pages(
    text: str,
    expected_page_count: int,
    code: str,
) -> list[str]:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != expected_page_count:
        raise PaperDraftPdfError(code)
    return pages


def _whitespace_free_with_offsets(text: str) -> tuple[str, list[int], str]:
    normalized = unicodedata.normalize("NFC", text)
    characters: list[str] = []
    offsets: list[int] = []
    for offset, character in enumerate(normalized):
        if not character.isspace():
            characters.append(character)
            offsets.append(offset)
    return "".join(characters), offsets, normalized


def _header_atomic_tokens(header: str) -> list[str]:
    tokens = [
        token.strip("`*_")
        for token in re.findall(r"\S+", unicodedata.normalize("NFC", header))
    ]
    return [token for token in tokens if token]


def _literal_occurrence_count(text: str, token: str) -> int:
    return unicodedata.normalize("NFC", text).count(token)


def _table_header_postflight(
    table_text: str,
    table_headers: Sequence[str],
    page_count: int,
) -> Mapping[str, Any]:
    """Validate full header rows in Poppler's content-stream (`-raw`) order.

    LibreOffice legitimately repeats ``thead`` rows when a table crosses a
    page. A source header must therefore occur at least once, at most once on
    any page, and any repeated copies must be on consecutive continuation
    pages at the start of Poppler's whitespace-free page stream.
    """

    pages = _extracted_pages(
        table_text,
        page_count,
        "PDF_TABLE_TEXT_PAGE_SET_INVALID",
    )
    page_surfaces = [_whitespace_free_with_offsets(page) for page in pages]
    page_keys = [surface[0] for surface in page_surfaces]
    header_keys = [_semantic_text_key(header) for header in table_headers]
    if any(not key for key in header_keys) or len(header_keys) != len(set(header_keys)):
        raise PaperDraftPdfError("PDF_TABLE_HEADER_SOURCE_INVALID")

    records: list[dict[str, Any]] = []
    first_locations: list[tuple[int, int]] = []
    intervals: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for header, key in zip(table_headers, header_keys, strict=True):
        locations: list[tuple[int, int]] = []
        for page_number, page in enumerate(page_keys, start=1):
            count = page.count(key)
            if count > 1:
                raise PaperDraftPdfError(
                    "PDF_TABLE_HEADER_PAGE_DUPLICATE",
                    str(page_number),
                )
            if count == 1:
                locations.append((page_number, page.index(key)))
        if not locations:
            raise PaperDraftPdfError("PDF_TABLE_HEADER_MISSING", header)
        page_numbers = [page for page, _ in locations]
        if page_numbers != list(
            range(page_numbers[0], page_numbers[0] + len(page_numbers))
        ):
            raise PaperDraftPdfError("PDF_TABLE_HEADER_REPEAT_PAGE_INVALID")
        if any(offset != 0 for _, offset in locations[1:]):
            raise PaperDraftPdfError("PDF_TABLE_HEADER_REPEAT_OFFSET_INVALID")
        atomic_tokens = _header_atomic_tokens(header)
        if not atomic_tokens:
            raise PaperDraftPdfError("PDF_TABLE_HEADER_ATOMIC_SOURCE_INVALID")
        for page_number, offset in locations:
            _, character_offsets, normalized_page = page_surfaces[page_number - 1]
            end_index = offset + len(key) - 1
            if end_index >= len(character_offsets):
                raise PaperDraftPdfError("PDF_TABLE_HEADER_ATOMIC_RANGE_INVALID")
            surface = normalized_page[
                character_offsets[offset] : character_offsets[end_index] + 1
            ]
            for token in atomic_tokens:
                expected = _literal_occurrence_count(header, token)
                if _literal_occurrence_count(surface, token) != expected:
                    raise PaperDraftPdfError(
                        "PDF_TABLE_HEADER_ATOMIC_TOKEN_SPLIT",
                        token,
                    )
        first_locations.append(locations[0])
        intervals.append(
            (
                locations[0],
                (locations[-1][0], locations[-1][1] + len(key)),
            )
        )
        records.append(
            {
                "occurrence_count": len(locations),
                "pages": page_numbers,
                "first_page_offset": locations[0][1],
                "continuation_page_offsets": [offset for _, offset in locations[1:]],
                "atomic_token_count": len(atomic_tokens),
                "atomic_tokens_sha256": _canonical_string_hash(atomic_tokens),
                "atomic_tokens_contiguous_on_every_occurrence": True,
            }
        )
    if first_locations != sorted(first_locations) or len(set(first_locations)) != len(
        first_locations
    ):
        raise PaperDraftPdfError("PDF_TABLE_HEADER_ORDER_INVALID")
    if any(
        previous_end > next_start
        for (_, previous_end), (next_start, _) in zip(
            intervals,
            intervals[1:],
        )
    ):
        raise PaperDraftPdfError("PDF_TABLE_HEADER_INTERVAL_INTERLEAVING")
    return {
        "status": "passed",
        "extraction_mode": "pdftotext-raw",
        "source_signature_count": len(table_headers),
        "source_signatures_sha256": _canonical_string_hash(header_keys),
        "pagination_policy": {
            "minimum_occurrences_per_source_header": 1,
            "maximum_occurrences_per_page": 1,
            "repeated_pages_consecutive": True,
            "continuation_page_whitespace_free_offset": 0,
            "source_order_preserved": True,
            "source_header_intervals_non_overlapping": True,
        },
        "headers": records,
    }


def _sanitized_html_visible_text(payload: bytes) -> str:
    parser = _SanitizedHtmlParser()
    try:
        parser.feed(payload.decode("utf-8"))
        parser.close()
    except UnicodeDecodeError as exc:
        raise PaperDraftPdfError("HTML_UTF8_INVALID") from exc
    if parser.body_depth != 0 or not parser.visible_text_parts:
        raise PaperDraftPdfError("HTML_BODY_STRUCTURE_INVALID")
    return "\n".join(parser.visible_text_parts)


def _svg_visible_text(payload: bytes) -> str:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise PaperDraftPdfError("COHORT_SVG_XML_INVALID") from exc
    return "\n".join(
        "".join(element.itertext())
        for element in root.iter()
        if element.tag == "{http://www.w3.org/2000/svg}text"
    )


def _table_body_row_postflight(
    sources: SourceBundle,
    html: HtmlArtifact,
    table_text: str,
    table_headers: Sequence[str],
    page_count: int,
) -> Mapping[str, Any]:
    """Bind every source tbody row to one complete, ordered PDF page slice."""

    raw_groups = html.audit["table_body_row_groups"]
    if not isinstance(raw_groups, list) or not all(
        isinstance(group, list)
        and group
        and all(isinstance(row, str) and row for row in group)
        for group in raw_groups
    ):
        raise PaperDraftPdfError("TABLE_BODY_ROW_GROUP_SET_INVALID")
    row_groups = [[str(row) for row in group] for group in raw_groups]
    if len(row_groups) != len(table_headers):
        raise PaperDraftPdfError("TABLE_BODY_ROW_GROUP_COUNT_INVALID")

    pages = _extracted_pages(
        table_text,
        page_count,
        "PDF_TABLE_TEXT_PAGE_SET_INVALID",
    )
    page_surfaces = [_whitespace_free_with_offsets(page) for page in pages]
    page_keys = [surface[0] for surface in page_surfaces]
    header_keys = [_semantic_text_key(header) for header in table_headers]
    flat_rows = [row for group in row_groups for row in group]
    row_cells = [row.split(TABLE_ROW_CELL_SEPARATOR) for row in flat_rows]
    if any(not cells or any(not cell for cell in cells) for cells in row_cells):
        raise PaperDraftPdfError("TABLE_BODY_ROW_SIGNATURE_INVALID")
    row_keys = [
        "".join(_semantic_text_key(cell) for cell in cells) for cells in row_cells
    ]
    if any(not key for key in row_keys):
        raise PaperDraftPdfError("TABLE_BODY_ROW_SIGNATURE_INVALID")

    source_surface = (
        _sanitized_html_visible_text(html.payload)
        + "\n"
        + _svg_visible_text(sources.snapshots["cohort_svg"].payload)
    )
    source_key = _semantic_text_key(source_surface)
    source_table_multiplicity = Counter(row_keys)
    occurrence_records: list[dict[str, Any]] = []
    for key in dict.fromkeys(row_keys):
        expected = source_key.count(key)
        locations: list[tuple[int, int]] = []
        for page_number, page_key in enumerate(page_keys, start=1):
            cursor = 0
            while True:
                offset = page_key.find(key, cursor)
                if offset < 0:
                    break
                locations.append((page_number, offset))
                cursor = offset + len(key)
        observed = len(locations)
        if expected < source_table_multiplicity[key]:
            raise PaperDraftPdfError("TABLE_BODY_ROW_SOURCE_MULTIPLICITY_INVALID")
        if observed != expected:
            raise PaperDraftPdfError(
                "PDF_TABLE_BODY_ROW_OCCURRENCE_INVALID",
                f"expected={expected}:actual={observed}",
            )
        occurrence_records.append(
            {
                "signature_sha256": _sha256(key.encode("utf-8")),
                "source_table_multiplicity": source_table_multiplicity[key],
                "whole_source_occurrence_count": expected,
                "pdf_single_page_occurrence_count": observed,
                "pages": [page for page, _ in locations],
            }
        )

    bound_records: list[dict[str, Any]] = []
    row_index = 0
    previous_location = (-1, -1)
    for table_index, (header_key, group) in enumerate(
        zip(header_keys, row_groups, strict=True),
        start=1,
    ):
        header_locations = [
            (page_number, page_key.index(header_key))
            for page_number, page_key in enumerate(page_keys)
            if page_key.count(header_key) == 1
        ]
        if not header_locations:
            raise PaperDraftPdfError("PDF_TABLE_BODY_HEADER_BINDING_INVALID")
        page_index, header_offset = header_locations[0]
        cursor = header_offset + len(header_key)
        for signature in group:
            cells = signature.split(TABLE_ROW_CELL_SEPARATOR)
            key = "".join(_semantic_text_key(cell) for cell in cells)
            while not page_keys[page_index].startswith(key, cursor):
                if cursor != len(page_keys[page_index]):
                    raise PaperDraftPdfError(
                        "PDF_TABLE_BODY_ROW_SEQUENCE_INVALID",
                        f"table={table_index}:row={row_index + 1}",
                    )
                page_index += 1
                if page_index >= len(page_keys) or not page_keys[page_index].startswith(
                    header_key
                ):
                    raise PaperDraftPdfError(
                        "PDF_TABLE_BODY_CONTINUATION_HEADER_INVALID",
                        f"table={table_index}:row={row_index + 1}",
                    )
                cursor = len(header_key)

            location = (page_index + 1, cursor)
            if location <= previous_location:
                raise PaperDraftPdfError("PDF_TABLE_BODY_ROW_ORDER_INVALID")
            _, character_offsets, normalized_page = page_surfaces[page_index]
            end_index = cursor + len(key) - 1
            if end_index >= len(character_offsets):
                raise PaperDraftPdfError("PDF_TABLE_BODY_ROW_RANGE_INVALID")
            surface = normalized_page[
                character_offsets[cursor] : character_offsets[end_index] + 1
            ]
            source_row = " ".join(cells)
            atomic_tokens = _header_atomic_tokens(source_row)
            for token in dict.fromkeys(atomic_tokens):
                expected = _literal_occurrence_count(source_row, token)
                observed = _literal_occurrence_count(surface, token)
                if observed != expected:
                    raise PaperDraftPdfError(
                        "PDF_TABLE_BODY_ATOMIC_TOKEN_SPLIT",
                        token,
                    )
            cursor += len(key)
            previous_location = location
            row_index += 1
            bound_records.append(
                {
                    "table_index": table_index,
                    "page": page_index + 1,
                    "whitespace_free_offset": location[1],
                    "signature_sha256": _sha256(key.encode("utf-8")),
                    "atomic_token_count": len(atomic_tokens),
                    "atomic_tokens_sha256": _canonical_string_hash(atomic_tokens),
                }
            )

    if row_index != len(flat_rows):
        raise PaperDraftPdfError("PDF_TABLE_BODY_ROW_COUNT_INVALID")
    return {
        "status": "passed",
        "extraction_mode": "pdftotext-raw",
        "source_table_count": len(row_groups),
        "source_row_count": len(flat_rows),
        "source_row_signatures_sha256": _canonical_string_hash(flat_rows),
        "unique_row_signature_count": len(source_table_multiplicity),
        "single_page_containment": True,
        "source_order_preserved": True,
        "header_bound_contiguous_sequence": True,
        "atomic_tokens_contiguous_within_bound_row": True,
        "occurrence_records": occurrence_records,
        "bound_rows": bound_records,
    }


def _table_numeric_token_postflight(
    sources: SourceBundle,
    html: HtmlArtifact,
    table_text: str,
) -> Mapping[str, Any]:
    raw_probes = html.audit["table_body_numeric_probes"]
    if not isinstance(raw_probes, list) or not all(
        isinstance(item, str) and item for item in raw_probes
    ):
        raise PaperDraftPdfError("TABLE_NUMERIC_PROBE_SET_INVALID")
    probes = list(raw_probes)
    if len(probes) != len(set(probes)):
        raise PaperDraftPdfError("TABLE_NUMERIC_PROBE_SET_INVALID")
    source_surface = (
        _sanitized_html_visible_text(html.payload)
        + "\n"
        + _svg_visible_text(sources.snapshots["cohort_svg"].payload)
    )
    records: list[dict[str, Any]] = []
    for token in probes:
        expected = _literal_occurrence_count(source_surface, token)
        observed = _literal_occurrence_count(table_text, token)
        if expected < 1 or observed != expected:
            raise PaperDraftPdfError(
                "PDF_TABLE_NUMERIC_TOKEN_OCCURRENCE_INVALID",
                f"{token}:expected={expected}:actual={observed}",
            )
        records.append(
            {
                "token": token,
                "source_occurrence_count": expected,
                "pdf_occurrence_count": observed,
            }
        )
    return {
        "status": "passed",
        "normalization": "unicode_nfc_exact_no_whitespace_removal",
        "probe_count": len(probes),
        "probes_sha256": _canonical_string_hash(probes),
        "whole_visible_source_to_pdf_exact_occurrences": True,
        "records": records,
    }


def _semantic_postflight(
    config: RenderConfig,
    sources: SourceBundle,
    html: HtmlArtifact,
    extracted_semantic_text: bytes,
    extracted_table_text: bytes,
    blank_pages: int,
    page_count: int = 1,
) -> Mapping[str, Any]:
    text = _decode_tool_output(
        extracted_semantic_text,
        "EXTRACTED_SEMANTIC_TEXT_UTF8_INVALID",
    )
    table_text = _decode_tool_output(
        extracted_table_text,
        "EXTRACTED_TABLE_TEXT_UTF8_INVALID",
    )
    _extracted_pages(
        text,
        page_count,
        "PDF_SEMANTIC_TEXT_PAGE_SET_INVALID",
    )
    normalized = _normalized_text(text)
    semantics = sources.semantics
    if _normalized_text(str(semantics["title"])) not in normalized:
        raise PaperDraftPdfError("PDF_TITLE_NOT_PRESERVED")
    source_markers = sorted(
        _semantic_text_key(str(marker)) for marker in semantics["pending_markers"]
    )
    extracted_markers = sorted(
        _semantic_text_key(marker) for marker in PENDING_RE.findall(text)
    )
    if extracted_markers != source_markers:
        raise PaperDraftPdfError(
            "PDF_PENDING_MARKER_MULTISET_INVALID",
            f"expected={len(source_markers)} actual={len(extracted_markers)}",
        )
    reference_probes = [
        _semantic_text_key(str(probe)) for probe in semantics["reference_probes"]
    ]
    semantic_key = _semantic_text_key(text)
    reference_counts = [semantic_key.count(probe) for probe in reference_probes]
    if any(count != 1 for count in reference_counts):
        raise PaperDraftPdfError(
            "PDF_REFERENCE_MULTISET_INVALID",
            ",".join(str(count) for count in reference_counts),
        )
    table_headers = list(html.audit["table_header_probes"])
    table_header_audit = _table_header_postflight(
        table_text,
        [str(header) for header in table_headers],
        page_count,
    )
    table_body_row_audit = _table_body_row_postflight(
        sources,
        html,
        table_text,
        [str(header) for header in table_headers],
        page_count,
    )
    numeric_token_audit = _table_numeric_token_postflight(
        sources,
        html,
        table_text,
    )
    if blank_pages != 0:
        raise PaperDraftPdfError("PDF_BLANK_PAGE_DETECTED")
    return {
        "title_preserved": True,
        "pending_count": len(semantics["pending_markers"]),
        "pending_markers_sha256": semantics["pending_markers_sha256"],
        "normalized_pending_multiset_sha256": _canonical_string_hash(extracted_markers),
        "source_reference_count": semantics["reference_count"],
        "pdf_reference_signature_count": len(reference_counts),
        "reference_probes_sha256": semantics["reference_probes_sha256"],
        "reference_occurrence_counts": reference_counts,
        "source_table_count": html.audit["table_count"],
        "pdf_table_header_signature_count": len(table_headers),
        "table_headers_sha256": html.audit["table_headers_sha256"],
        "table_header_audit": table_header_audit,
        "table_body_row_audit": table_body_row_audit,
        "table_numeric_token_audit": numeric_token_audit,
        "table_body_preservation_status": (
            "row_signatures_and_atomic_tokens_machine_verified"
        ),
        "semantic_text": {
            "extraction_mode": "pdftotext-default",
            "sha256": _sha256(extracted_semantic_text),
            "size_bytes": len(extracted_semantic_text),
        },
        "table_text": {
            "extraction_mode": "pdftotext-raw",
            "sha256": _sha256(extracted_table_text),
            "size_bytes": len(extracted_table_text),
        },
        "visual_review_status": "pending",
        "blank_page_count": 0,
    }


def _production_postflight(
    config: RenderConfig,
    pdf: bytes,
    sources: SourceBundle,
    html: HtmlArtifact,
    tools: Mapping[str, ToolSnapshot],
    work: Path,
) -> PostflightResult:
    root = work / "postflight"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(mode=0o700)
    pdf_path = root / "draft.pdf"
    pdf_path.write_bytes(pdf)
    pdf_path.chmod(0o600)
    environment = _fixed_environment(
        root,
        int(config.policy["source_date_epoch"]),
    )
    timeout = float(config.policy["tool_timeout_seconds"])
    for key in ("strace", "pdfinfo", "pdffonts", "pdftotext", "pdftoppm"):
        _revalidate_tool(tools[key], f"TOOL_{key.upper()}")

    info_result = _run_offline_traced_checked(
        [_tool_path(tools, "pdfinfo"), "-box", str(pdf_path)],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdfinfo.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFINFO",
    )
    info = _decode_tool_output(info_result.stdout, "PDFINFO_UTF8_INVALID")
    version = _pdfinfo_value(info, "PDF version", "PDFINFO_PARSE_INVALID")
    if version != "1.7":
        raise PaperDraftPdfError("PDF_VERSION_INVALID", version)
    pages = _parse_positive_decimal_integer(
        _pdfinfo_value(info, "Pages", "PDFINFO_PARSE_INVALID"),
        "PDF_PAGE_COUNT_INVALID",
    )
    if _pdfinfo_value(info, "Encrypted", "PDFINFO_PARSE_INVALID") != "no":
        raise PaperDraftPdfError("PDF_ENCRYPTED")
    if _pdfinfo_value(info, "JavaScript", "PDFINFO_PARSE_INVALID") != "no":
        raise PaperDraftPdfError("PDF_JAVASCRIPT_PRESENT")
    creation_date_status = _pdfinfo_fixed_date_status(
        info,
        "CreationDate",
        int(config.policy["source_date_epoch"]),
    )
    modification_date_status = _pdfinfo_fixed_date_status(
        info,
        "ModDate",
        int(config.policy["source_date_epoch"]),
    )
    metadata_stream = _optional_pdfinfo_value(
        info,
        "Metadata Stream",
        "PDFINFO_METADATA_INVALID",
    )
    custom_metadata = _optional_pdfinfo_value(
        info,
        "Custom Metadata",
        "PDFINFO_METADATA_INVALID",
    )
    expected_producer = _libreoffice_pdf_producer(config)
    optional_empty_metadata = {
        key: _optional_pdfinfo_value(
            info,
            key,
            "PDFINFO_METADATA_INVALID",
        )
        for key in ("Author", "Subject", "Keywords", "Creator")
    }
    if (
        creation_date_status != "absent"
        or modification_date_status != "absent"
        or metadata_stream != "no"
        or custom_metadata != "no"
        or _pdfinfo_value(info, "Title", "PDFINFO_METADATA_INVALID")
        != config.namespace.title
        or _pdfinfo_value(info, "Producer", "PDFINFO_METADATA_INVALID")
        != expected_producer
        or any(value not in {None, ""} for value in optional_empty_metadata.values())
        or _pdfinfo_value(info, "Form", "PDFINFO_METADATA_INVALID") != "none"
        or _pdfinfo_value(info, "UserProperties", "PDFINFO_METADATA_INVALID") != "no"
        or _pdfinfo_value(info, "Tagged", "PDFINFO_METADATA_INVALID") != "yes"
        or _pdfinfo_value(info, "Suspects", "PDFINFO_METADATA_INVALID") != "no"
    ):
        raise PaperDraftPdfError("PDFINFO_METADATA_INVALID")
    box_result = _run_offline_traced_checked(
        [
            _tool_path(tools, "pdfinfo"),
            "-box",
            "-f",
            "1",
            "-l",
            str(pages),
            str(pdf_path),
        ],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdfinfo-boxes.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFINFO_PAGE_BOXES",
    )
    box_info = _decode_tool_output(
        box_result.stdout,
        "PDFINFO_PAGE_BOXES_UTF8_INVALID",
    )
    page_geometry = _parse_pdf_page_geometry(box_info, pages)
    _validate_a4_page_geometry(page_geometry, pages)

    js_result = _run_offline_traced_checked(
        [_tool_path(tools, "pdfinfo"), "-js", str(pdf_path)],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdfinfo-js.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFINFO_JS",
    )
    if js_result.stdout.strip():
        raise PaperDraftPdfError("PDF_JAVASCRIPT_PRESENT")
    metadata_result = _run_offline_traced_checked(
        [_tool_path(tools, "pdfinfo"), "-meta", str(pdf_path)],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdfinfo-meta.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFINFO_METADATA",
    )
    if metadata_result.stdout:
        raise PaperDraftPdfError("PDF_XMP_UNEXPECTED")
    metadata_audit = {
        "allowlist_status": "passed",
        "producer": expected_producer,
        "title": config.namespace.title,
        "metadata_stream_present": False,
        "xmp_status": "absent",
        "date_fields_present": False,
    }
    url_result = _run_offline_traced_checked(
        [_tool_path(tools, "pdfinfo"), "-url", str(pdf_path)],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdfinfo-url.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFINFO_URL",
    )
    urls = _parse_pdf_urls(
        _decode_tool_output(url_result.stdout, "PDF_URL_UTF8_INVALID")
    )
    if len(urls) != config.policy["expected_https_links"]:
        raise PaperDraftPdfError("PDF_URL_COUNT_INVALID")

    fonts_result = _run_offline_traced_checked(
        [_tool_path(tools, "pdffonts"), str(pdf_path)],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdffonts.file.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFFONTS",
    )
    font_names, all_embedded, all_unicode = _parse_pdffonts(
        _decode_tool_output(fonts_result.stdout, "PDFFONTS_UTF8_INVALID")
    )
    if not all_embedded or not all_unicode:
        raise PaperDraftPdfError("PDF_FONT_EMBEDDING_OR_UNICODE_INVALID")

    extracted_layout_path = root / "extracted-layout.txt"
    _run_offline_traced_checked(
        [
            _tool_path(tools, "pdftotext"),
            "-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(extracted_layout_path),
        ],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdftotext-layout.combined.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFTOTEXT_LAYOUT",
    )
    extracted_semantic_path = root / "extracted-semantic.txt"
    _run_offline_traced_checked(
        [
            _tool_path(tools, "pdftotext"),
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(extracted_semantic_path),
        ],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdftotext-semantic.combined.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFTOTEXT_SEMANTIC",
    )
    extracted_table_path = root / "extracted-table.txt"
    _run_offline_traced_checked(
        [
            _tool_path(tools, "pdftotext"),
            "-raw",
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(extracted_table_path),
        ],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdftotext-table.combined.strace",
        timeout_seconds=timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFTOTEXT_TABLE",
    )
    extraction_paths = {
        "layout": extracted_layout_path,
        "semantic": extracted_semantic_path,
        "table": extracted_table_path,
    }
    extracted_payloads: dict[str, bytes] = {}
    for mode, path in extraction_paths.items():
        if not path.is_file() or path.is_symlink():
            raise PaperDraftPdfError(
                "PDFTOTEXT_OUTPUT_MISSING",
                mode,
            )
        payload = path.read_bytes()
        if not payload or len(payload) > MAX_ARTIFACT_BYTES:
            raise PaperDraftPdfError("PDFTOTEXT_OUTPUT_INVALID", mode)
        _decode_tool_output(
            payload,
            f"EXTRACTED_{mode.upper()}_TEXT_UTF8_INVALID",
        )
        extracted_payloads[mode] = payload
    extracted_layout = extracted_payloads["layout"]
    extracted_semantic = extracted_payloads["semantic"]
    extracted_table = extracted_payloads["table"]

    raster_prefix = root / "page"
    raster_timeout = min(
        300.0,
        max(timeout, pages * min(timeout, 5.0)),
    )
    _run_offline_traced_checked(
        [
            _tool_path(tools, "pdftoppm"),
            "-png",
            "-r",
            str(config.policy["raster_dpi"]),
            "-f",
            "1",
            "-l",
            str(pages),
            str(pdf_path),
            str(raster_prefix),
        ],
        strace_path=_tool_path(tools, "strace"),
        trace_path=root / "pdftoppm.file.strace",
        timeout_seconds=raster_timeout,
        environment=environment,
        working_directory=root,
        workspace_root=config.workspace_root,
        code="PDFTOPPM",
    )
    observed_rasters: list[tuple[int, Path]] = []
    for path in root.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d+)\.png", path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise PaperDraftPdfError("PDFTOPPM_OUTPUT_TREE_INVALID")
        observed_rasters.append((int(match.group(1)), path))
    observed_rasters.sort()
    if [number for number, _ in observed_rasters] != list(range(1, pages + 1)):
        raise PaperDraftPdfError("PDFTOPPM_PAGE_SET_INVALID")
    extracted_pages = _extracted_pages(
        _decode_tool_output(
            extracted_layout,
            "EXTRACTED_LAYOUT_TEXT_UTF8_INVALID",
        ),
        pages,
        "PDFTOTEXT_LAYOUT_PAGE_SET_INVALID",
    )

    dpi = int(config.policy["raster_dpi"])
    expected_width = round(210 / 25.4 * dpi)
    expected_height = round(297 / 25.4 * dpi)
    page_payloads: dict[str, bytes] = {}
    raster_records: list[dict[str, Any]] = []
    decoded_rasters: list[tuple[int, int, int, bytes]] = []
    blank_pages = 0
    for number, path in observed_rasters:
        payload = path.read_bytes()
        width, height, white_ratio, pixels = _png_decode(payload)
        if abs(width - expected_width) > 3 or abs(height - expected_height) > 3:
            raise PaperDraftPdfError("PNG_A4_PIXEL_SIZE_INVALID")
        canonical_name = f"page-{number:03d}.png"
        page_payloads[canonical_name] = payload
        decoded_rasters.append((number, width, height, pixels))
        (
            raster_blank,
            text_blank,
            is_blank,
            visibility_status,
        ) = _blank_page_signals(
            white_ratio,
            extracted_pages[number - 1],
            float(config.policy["blank_white_ratio"]),
        )
        blank_pages += int(is_blank)
        raster_records.append(
            {
                "path": f"pages/{canonical_name}",
                "sha256": _sha256(payload),
                "size_bytes": len(payload),
                "width": width,
                "height": height,
                "white_ratio": round(white_ratio, 8),
                "raster_blank_candidate": raster_blank,
                "text_blank_candidate": text_blank,
                "visibility_status": visibility_status,
                "blank": is_blank,
            }
        )
    cohort_figure = _cohort_figure_audit(decoded_rasters)
    semantic = _semantic_postflight(
        config,
        sources,
        html,
        extracted_semantic,
        extracted_table,
        blank_pages,
        pages,
    )
    for key in ("strace", "pdfinfo", "pdffonts", "pdftotext", "pdftoppm"):
        _revalidate_tool(tools[key], f"TOOL_{key.upper()}")
    document = {
        "schema_version": POSTFLIGHT_SCHEMA,
        "status": "passed",
        "pdf": {
            "sha256": _sha256(pdf),
            "size_bytes": len(pdf),
            "version": version,
            "page_count": pages,
            "a4_page_count": pages,
            "page_geometry": page_geometry,
            "encrypted": False,
            "javascript_count": 0,
            "https_url_count": len(urls),
            "https_urls_sha256": _canonical_string_hash(urls),
            "relative_local_file_url_count": 0,
            "creation_date_status": creation_date_status,
            "modification_date_status": modification_date_status,
            "metadata_stream_present": False,
            "custom_metadata": False,
            "tagged": True,
            "form": "none",
            "user_properties": False,
            "suspects": False,
            "metadata": metadata_audit,
        },
        "fonts": {
            "font_count": len(font_names),
            "embedded_font_count": len(font_names),
            "unicode_mapped_font_count": len(font_names),
            "all_embedded": True,
            "all_unicode_mapped": True,
            "font_names": font_names,
        },
        "text_extraction": {
            "layout": {
                "mode": "pdftotext-layout",
                "sha256": _sha256(extracted_layout),
                "size_bytes": len(extracted_layout),
                "page_count": pages,
            },
            "semantic": {
                "mode": "pdftotext-default",
                "sha256": _sha256(extracted_semantic),
                "size_bytes": len(extracted_semantic),
                "page_count": pages,
            },
            "table": {
                "mode": "pdftotext-raw",
                "sha256": _sha256(extracted_table),
                "size_bytes": len(extracted_table),
                "page_count": pages,
            },
        },
        "semantics": semantic,
        "raster": {
            "dpi": dpi,
            "timeout_seconds": raster_timeout,
            "page_count": pages,
            "blank_page_count": blank_pages,
            "cohort_figure": cohort_figure,
            "pages": raster_records,
        },
        "network": _network_stage_evidence(NETWORK_POSTFLIGHT_STAGES),
        "claims": {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
        },
        "privacy": {
            "raw_files_read": 0,
            "locked_test_files_read": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
        },
    }
    return PostflightResult(
        document=document,
        extracted_layout_text=extracted_layout,
        extracted_semantic_text=extracted_semantic,
        extracted_table_text=extracted_table,
        pages=page_payloads,
    )


def _artifact_record(path: str, payload: bytes) -> FileRecord:
    return FileRecord(path, _sha256(payload), len(payload))


def _network_contract(network: Mapping[str, Any]) -> None:
    expected = {
        "strace_network_trace": True,
        "kernel_network_namespace": False,
        "allowed_address_families": ["AF_UNIX"],
        "af_inet_count": 0,
        "af_inet6_count": 0,
        "forbidden_family_count": 0,
        "observed_forbidden_address_family_syscalls": 0,
        "scm_rights_transfer_count": 0,
        "observation_only_not_firewall": True,
        "relative_or_local_pdf_annotation_allowed": False,
    }
    if network != expected:
        raise PaperDraftPdfError("NETWORK_CONTRACT_INVALID")


def _network_stage_evidence(stages: Sequence[str]) -> Mapping[str, Any]:
    if len(stages) != len(set(stages)) or not stages:
        raise PaperDraftPdfError("NETWORK_STAGE_SET_INVALID")
    return {
        "stage_count": len(stages),
        "stages": {
            stage: {
                "combined_file_network_trace": True,
                "observed_forbidden_address_family_syscalls": 0,
                "scm_rights_transfer_count": 0,
            }
            for stage in stages
        },
        "kernel_network_namespace": False,
        "observation_only_not_firewall": True,
    }


def _receipt_value(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
    sources: SourceBundle,
    tools: Mapping[str, ToolSnapshot],
    tool_versions: Mapping[str, str],
    html: HtmlArtifact,
    pdf: bytes,
    postflight: bytes,
    extracted_layout: bytes,
    extracted_semantic: bytes,
    extracted_table: bytes,
    pages: Mapping[str, bytes],
    *,
    backend_id: str,
) -> Mapping[str, Any]:
    page_records = [
        _artifact_record(f"pages/{name}", payload).as_dict()
        for name, payload in sorted(pages.items())
    ]
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "passed",
        "scope": "aggregate_only_paper_draft_pdf",
        "claims": {
            "draft": True,
            "final": False,
            "camera_ready": False,
            "p05_complete": False,
            "visual_review_status": "pending",
        },
        "privacy": {
            "raw_files_read": 0,
            "locked_test_files_read": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
        },
        "inputs": {
            key: record.as_dict() for key, record in sorted(sources.records.items())
        },
        "implementation": {
            "config": config.config_snapshot.record.as_dict(),
            "module": implementation.module.record.as_dict(),
            "cli": implementation.cli.record.as_dict(),
        },
        "tools": {
            key: {
                **tools[key].record.as_dict(),
                "observed_version": tool_versions[key],
            }
            for key in TOOL_KEYS
        },
        "render": {
            "backend": backend_id,
            "independent_build_count": 2,
            "normalized_pdf_byte_identical": True,
            "lossless_pdf_canonicalization_byte_identical": True,
            "xmp_metadata_status": "absent",
            "ghostscript_role": "validation_only_nullpage",
            "pdf_normalization": {
                "method": "fixed_width_volatile_field_whitespace_scrub_v1",
                "creation_date_removed": True,
                "modification_date_removed_if_present": True,
                "trailer_id_removed": True,
                "doc_checksum_removed": True,
                "byte_length_preserved": True,
                "xref_offsets_preserved": True,
                "ghostscript_rewrite": False,
            },
            "source_date_epoch": config.policy["source_date_epoch"],
            "subprocess_isolation": {
                "working_directory": "per_run_temporary",
                "home": "per_run_temporary",
                "xdg_config_cache_data_runtime": "per_run_temporary",
                "file_access_trace": True,
                "workspace_access_count": 0,
                "real_home_access_count": 0,
                "environment_file_access_count": 0,
                "raw_restricted_locked_secret_access_count": 0,
                "runtime_closure": (
                    "primary executables content-pinned; helper executables "
                    "and dynamic libraries observed by file trace but not "
                    "content-pinned"
                ),
            },
            "network": {
                "strace_network_trace": True,
                "kernel_network_namespace": False,
                "allowed_address_families": ["AF_UNIX"],
                "af_inet_count": 0,
                "af_inet6_count": 0,
                "forbidden_family_count": 0,
                "observed_forbidden_address_family_syscalls": 0,
                "scm_rights_transfer_count": 0,
                "observation_only_not_firewall": True,
                "relative_or_local_pdf_annotation_allowed": False,
                "stage_zero_evidence": _network_stage_evidence(NETWORK_ALL_STAGES),
            },
            "html": {
                **html.audit,
                "sha256": _sha256(html.payload),
                "size_bytes": len(html.payload),
            },
        },
        "artifacts": {
            "draft_pdf": _artifact_record("draft.pdf", pdf).as_dict(),
            "extracted_layout_text": _artifact_record(
                "extracted-layout.txt",
                extracted_layout,
            ).as_dict(),
            "extracted_semantic_text": _artifact_record(
                "extracted-semantic.txt",
                extracted_semantic,
            ).as_dict(),
            "extracted_table_text": _artifact_record(
                "extracted-table.txt",
                extracted_table,
            ).as_dict(),
            "postflight": _artifact_record(
                "postflight.json",
                postflight,
            ).as_dict(),
            "pages": page_records,
        },
        "publication": {
            "output_root": config.output_root,
            "commit_path": config.commit_path,
            "unvalidated_history_root": (
                PurePosixPath(config.output_root).parent
                / _history_directory_name(config)
            ).as_posix(),
            "unvalidated_retention": True,
            "canonical_generation_count": 1,
            "generation_id_rule": "sha256(canonical_receipt_bytes)",
            "single_writer": True,
            "stable_parent_namespace_lock": True,
            "held_output_dirfd_identity": True,
            "transactional_commit": True,
            "post_publish_deep_validation": True,
            "root_mode": "0750",
            "generations_mode": "0750",
            "generation_mode": "0550",
            "pages_mode": "0550",
            "file_mode": "0440",
            "nlink": 1,
        },
    }


def _validate_postflight_result(
    config: RenderConfig,
    sources: SourceBundle,
    html: HtmlArtifact,
    pdf: bytes,
    result: PostflightResult,
) -> None:
    document = _mapping(result.document, "POSTFLIGHT_DOCUMENT_INVALID")
    pdf_summary = _mapping(document.get("pdf"), "POSTFLIGHT_PDF_INVALID")
    fonts = _mapping(document.get("fonts"), "POSTFLIGHT_FONTS_INVALID")
    text_extraction = _mapping(
        document.get("text_extraction"),
        "POSTFLIGHT_TEXT_EXTRACTION_INVALID",
    )
    raster = _mapping(document.get("raster"), "POSTFLIGHT_RASTER_INVALID")
    claims = _mapping(document.get("claims"), "POSTFLIGHT_CLAIMS_INVALID")
    privacy = _mapping(document.get("privacy"), "POSTFLIGHT_PRIVACY_INVALID")
    network = _mapping(document.get("network"), "POSTFLIGHT_NETWORK_INVALID")
    if (
        document.get("schema_version") != POSTFLIGHT_SCHEMA
        or document.get("status") != "passed"
        or pdf_summary.get("sha256") != _sha256(pdf)
        or pdf_summary.get("size_bytes") != len(pdf)
        or pdf_summary.get("version") != "1.7"
        or pdf_summary.get("encrypted") is not False
        or pdf_summary.get("javascript_count") != 0
        or pdf_summary.get("https_url_count") != config.policy["expected_https_links"]
        or pdf_summary.get("https_urls_sha256") != html.audit["https_links_sha256"]
        or pdf_summary.get("relative_local_file_url_count") != 0
        or pdf_summary.get("creation_date_status") != "absent"
        or pdf_summary.get("modification_date_status") != "absent"
        or pdf_summary.get("metadata_stream_present") is not False
        or pdf_summary.get("custom_metadata") is not False
        or pdf_summary.get("tagged") is not True
        or pdf_summary.get("form") != "none"
        or pdf_summary.get("user_properties") is not False
        or pdf_summary.get("suspects") is not False
        or fonts.get("all_embedded") is not True
        or fonts.get("all_unicode_mapped") is not True
        or fonts.get("font_count") != fonts.get("embedded_font_count")
        or fonts.get("font_count") != fonts.get("unicode_mapped_font_count")
        or claims
        != {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
        }
        or privacy
        != {
            "raw_files_read": 0,
            "locked_test_files_read": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
        }
        or network != _network_stage_evidence(NETWORK_POSTFLIGHT_STAGES)
    ):
        raise PaperDraftPdfError("POSTFLIGHT_DOCUMENT_INVALID")
    metadata = _mapping(
        pdf_summary.get("metadata"),
        "POSTFLIGHT_METADATA_INVALID",
    )
    expected_producer = _libreoffice_pdf_producer(config)
    if (
        metadata.get("allowlist_status") != "passed"
        or metadata.get("producer") != expected_producer
        or metadata.get("title") != config.namespace.title
        or metadata.get("metadata_stream_present") is not False
        or metadata.get("xmp_status") != "absent"
        or metadata.get("date_fields_present") is not False
        or set(metadata)
        != {
            "allowlist_status",
            "producer",
            "title",
            "metadata_stream_present",
            "xmp_status",
            "date_fields_present",
        }
    ):
        raise PaperDraftPdfError("POSTFLIGHT_METADATA_INVALID")
    page_count = _integer(
        pdf_summary.get("page_count"),
        "POSTFLIGHT_PAGE_COUNT_INVALID",
        minimum=1,
    )
    expected_extractions = {
        "layout": (
            "pdftotext-layout",
            result.extracted_layout_text,
        ),
        "semantic": (
            "pdftotext-default",
            result.extracted_semantic_text,
        ),
        "table": (
            "pdftotext-raw",
            result.extracted_table_text,
        ),
    }
    if set(text_extraction) != set(expected_extractions):
        raise PaperDraftPdfError("POSTFLIGHT_TEXT_EXTRACTION_INVALID")
    for key, (mode, payload) in expected_extractions.items():
        record = _mapping(
            text_extraction.get(key),
            "POSTFLIGHT_TEXT_EXTRACTION_INVALID",
        )
        if record != {
            "mode": mode,
            "sha256": _sha256(payload),
            "size_bytes": len(payload),
            "page_count": page_count,
        }:
            raise PaperDraftPdfError("POSTFLIGHT_TEXT_EXTRACTION_INVALID", key)
    expected_raster_timeout = min(
        300.0,
        max(
            float(config.policy["tool_timeout_seconds"]),
            page_count * min(float(config.policy["tool_timeout_seconds"]), 5.0),
        ),
    )
    if (
        pdf_summary.get("a4_page_count") != page_count
        or raster.get("page_count") != page_count
        or raster.get("blank_page_count") != 0
        or raster.get("dpi") != config.policy["raster_dpi"]
        or raster.get("timeout_seconds") != expected_raster_timeout
        or len(result.pages) != page_count
    ):
        raise PaperDraftPdfError("POSTFLIGHT_PAGE_COUNT_INVALID")
    page_geometry = pdf_summary.get("page_geometry")
    if not isinstance(page_geometry, list):
        raise PaperDraftPdfError("POSTFLIGHT_PAGE_GEOMETRY_INVALID")
    try:
        _validate_a4_page_geometry(page_geometry, page_count)
    except (PaperDraftPdfError, TypeError, ValueError) as exc:
        raise PaperDraftPdfError("POSTFLIGHT_PAGE_GEOMETRY_INVALID") from exc
    semantic_expected = _semantic_postflight(
        config,
        sources,
        html,
        result.extracted_semantic_text,
        result.extracted_table_text,
        0,
        page_count,
    )
    if document.get("semantics") != semantic_expected:
        raise PaperDraftPdfError("POSTFLIGHT_SEMANTICS_INVALID")
    raster_records = raster.get("pages")
    if not isinstance(raster_records, list) or len(raster_records) != page_count:
        raise PaperDraftPdfError("POSTFLIGHT_RASTER_RECORDS_INVALID")
    records = {
        record.get("path"): record
        for record in raster_records
        if isinstance(record, dict)
    }
    expected_names = [f"page-{index:03d}.png" for index in range(1, page_count + 1)]
    if sorted(result.pages) != expected_names or len(records) != page_count:
        raise PaperDraftPdfError("POSTFLIGHT_RASTER_RECORDS_INVALID")
    layout_pages = _extracted_pages(
        _decode_tool_output(
            result.extracted_layout_text,
            "EXTRACTED_LAYOUT_TEXT_UTF8_INVALID",
        ),
        page_count,
        "POSTFLIGHT_LAYOUT_PAGE_SET_INVALID",
    )
    decoded_rasters: list[tuple[int, int, int, bytes]] = []
    for page_number, name in enumerate(expected_names, start=1):
        payload = result.pages[name]
        width, height, white_ratio, pixels = _png_decode(payload)
        (
            raster_blank,
            text_blank,
            blank,
            visibility_status,
        ) = _blank_page_signals(
            white_ratio,
            layout_pages[page_number - 1],
            float(config.policy["blank_white_ratio"]),
        )
        decoded_rasters.append((page_number, width, height, pixels))
        page_record = records.get(f"pages/{name}")
        if (
            page_record is None
            or page_record.get("sha256") != _sha256(payload)
            or page_record.get("size_bytes") != len(payload)
            or page_record.get("width") != width
            or page_record.get("height") != height
            or page_record.get("white_ratio") != round(white_ratio, 8)
            or page_record.get("raster_blank_candidate") is not raster_blank
            or page_record.get("text_blank_candidate") is not text_blank
            or page_record.get("visibility_status") != visibility_status
            or page_record.get("blank") is not blank
            or blank
        ):
            raise PaperDraftPdfError("POSTFLIGHT_RASTER_RECORD_INVALID", name)
    expected_cohort_figure = _cohort_figure_audit(decoded_rasters)
    if raster.get("cohort_figure") != expected_cohort_figure:
        raise PaperDraftPdfError("POSTFLIGHT_COHORT_FIGURE_INVALID")


def _build_products(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
    sources: SourceBundle,
    tools: Mapping[str, ToolSnapshot],
    backend: BuildBackend,
) -> BuildProducts:
    if config.namespace.production == backend.test_only:
        raise PaperDraftPdfError("BUILD_BACKEND_KIND_INVALID")
    html = render_sanitized_html(config, sources.manuscript)
    with tempfile.TemporaryDirectory(
        prefix="voice-coach-paper-draft-",
    ) as temporary:
        work = Path(temporary)
        tool_versions = backend.inspect_tools(config, tools, work)
        if set(tool_versions) != set(TOOL_KEYS):
            raise PaperDraftPdfError("TOOL_VERSION_SET_INVALID")
        first = backend.build_once(
            config,
            html.payload,
            sources.snapshots["cohort_svg"].payload,
            tools,
            work,
            1,
        )
        second = backend.build_once(
            config,
            html.payload,
            sources.snapshots["cohort_svg"].payload,
            tools,
            work,
            2,
        )
        _network_contract(first.network)
        _network_contract(second.network)
        normalized_first = backend.normalize(
            config,
            first.raw_pdf,
            tools,
            work,
            1,
        )
        normalized_second = backend.normalize(
            config,
            second.raw_pdf,
            tools,
            work,
            2,
        )
        if normalized_first != normalized_second:
            raise PaperDraftPdfError("NORMALIZED_PDF_NOT_DETERMINISTIC")
        postflight_result = backend.postflight(
            config,
            normalized_first,
            sources,
            html,
            tools,
            work,
        )
        _validate_postflight_result(
            config,
            sources,
            html,
            normalized_first,
            postflight_result,
        )
        postflight = _canonical_json(postflight_result.document)
        receipt_value = _receipt_value(
            config,
            implementation,
            sources,
            tools,
            tool_versions,
            html,
            normalized_first,
            postflight,
            postflight_result.extracted_layout_text,
            postflight_result.extracted_semantic_text,
            postflight_result.extracted_table_text,
            postflight_result.pages,
            backend_id=backend.backend_id,
        )
        receipt = _canonical_json(receipt_value)
    return BuildProducts(
        pdf=normalized_first,
        extracted_layout_text=postflight_result.extracted_layout_text,
        extracted_semantic_text=postflight_result.extracted_semantic_text,
        extracted_table_text=postflight_result.extracted_table_text,
        postflight=postflight,
        pages=dict(postflight_result.pages),
        receipt=receipt,
        generation_id=_sha256(receipt),
        receipt_value=receipt_value,
    )


def _open_directory_at(parent: int, name: str, code: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        raise PaperDraftPdfError(code, name) from exc


def _directory_identity_at(parent: int, name: str, code: str) -> FileIdentity:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as exc:
        raise PaperDraftPdfError(code, name) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PaperDraftPdfError(code, name)
    return _identity(metadata)


def _validate_directory(
    descriptor: int,
    expected: FileIdentity,
    expected_mode: int | None,
    code: str,
) -> None:
    metadata = os.fstat(descriptor)
    if (
        not _same_filesystem_object(_identity(metadata), expected)
        or not stat.S_ISDIR(metadata.st_mode)
        or (
            expected_mode is not None
            and stat.S_IMODE(metadata.st_mode) != expected_mode
        )
    ):
        raise PaperDraftPdfError(code)


def _fsync_directory_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def _history_directory_name(config: RenderConfig) -> str:
    return PurePosixPath(config.output_root).name + HISTORY_SUFFIX


def _rename_noreplace_at(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    if any(
        "/" in name or name in {"", ".", ".."}
        for name in (source_name, destination_name)
    ):
        raise PaperDraftPdfError("RENAME_NOREPLACE_NAME_INVALID")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PaperDraftPdfError("RENAME_NOREPLACE_UNAVAILABLE")
    result = renameat2(
        ctypes.c_int(source_descriptor),
        ctypes.c_char_p(os.fsencode(source_name)),
        ctypes.c_int(destination_descriptor),
        ctypes.c_char_p(os.fsencode(destination_name)),
        ctypes.c_uint(1),  # RENAME_NOREPLACE
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise FileExistsError(destination_name)
        raise PaperDraftPdfError(
            "RENAME_NOREPLACE_FAILED",
            os.strerror(error_number),
        )


def _move_to_unvalidated_history(
    guard: OutputGuard,
    *,
    source_descriptor: int,
    source_name: str,
    preferred_name: str,
    expected_identity: FileIdentity,
) -> str:
    directory_descriptor = -1
    original_mode: int | None = None
    source_metadata = os.stat(
        source_name,
        dir_fd=source_descriptor,
        follow_symlinks=False,
    )
    if stat.S_ISDIR(source_metadata.st_mode) and not stat.S_ISLNK(
        source_metadata.st_mode
    ):
        directory_descriptor = _open_directory_at(
            source_descriptor,
            source_name,
            "HISTORY_SOURCE_OPEN_FAILED",
        )
        opened_identity = _identity(os.fstat(directory_descriptor))
        if not _same_filesystem_object(opened_identity, expected_identity):
            os.close(directory_descriptor)
            raise PaperDraftPdfError("HISTORY_SOURCE_IDENTITY_INVALID")
        original_mode = stat.S_IMODE(source_metadata.st_mode)
        os.fchmod(directory_descriptor, original_mode | stat.S_IWUSR)
    for attempt in range(32):
        suffix = (
            ""
            if attempt == 0
            else "-"
            + _sha256(
                (f"{preferred_name}:{os.getpid()}:{time.time_ns()}:{attempt}").encode(
                    "utf-8"
                )
            )[:16]
        )
        destination_name = preferred_name + suffix
        try:
            _rename_noreplace_at(
                source_descriptor,
                source_name,
                guard.history_descriptor,
                destination_name,
            )
        except FileExistsError:
            continue
        except BaseException:
            if directory_descriptor >= 0 and original_mode is not None:
                os.fchmod(directory_descriptor, original_mode)
                os.close(directory_descriptor)
            raise
        if directory_descriptor >= 0 and original_mode is not None:
            os.fchmod(directory_descriptor, original_mode)
        observed = _identity(
            os.stat(
                destination_name,
                dir_fd=guard.history_descriptor,
                follow_symlinks=False,
            )
        )
        if not _same_filesystem_object(observed, expected_identity):
            raise PaperDraftPdfError("HISTORY_MOVE_IDENTITY_INVALID")
        _fsync_directory_fd(source_descriptor)
        _fsync_directory_fd(guard.history_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        return destination_name
    if directory_descriptor >= 0:
        if original_mode is not None:
            os.fchmod(directory_descriptor, original_mode)
        os.close(directory_descriptor)
    raise PaperDraftPdfError("HISTORY_NAME_EXHAUSTED")


def _create_or_open_directory(
    parent: int,
    name: str,
    *,
    mode: int,
    create: bool,
    code: str,
) -> tuple[int, FileIdentity]:
    created = False
    if create:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise PaperDraftPdfError(code, name) from exc
    descriptor = _open_directory_at(parent, name, code)
    metadata = os.fstat(descriptor)
    identity = _directory_identity_at(parent, name, code)
    if _identity(metadata) != identity:
        os.close(descriptor)
        raise PaperDraftPdfError(code, name)
    if created:
        os.fchmod(descriptor, mode)
        _fsync_directory_fd(descriptor)
        _fsync_directory_fd(parent)
        metadata = os.fstat(descriptor)
        identity = _directory_identity_at(parent, name, code)
        if _identity(metadata) != identity:
            os.close(descriptor)
            raise PaperDraftPdfError(code, name)
    if stat.S_IMODE(metadata.st_mode) != mode:
        os.close(descriptor)
        raise PaperDraftPdfError(code, name)
    return descriptor, identity


def _open_parent_directory_chain(
    root_descriptor: int,
    relative: PurePosixPath,
    *,
    create: bool,
) -> tuple[DirectoryGuard, ...]:
    chain: list[DirectoryGuard] = []
    current = root_descriptor
    try:
        for name in relative.parts:
            if name in {"", ".", ".."} or "/" in name:
                raise PaperDraftPdfError("OUTPUT_PARENT_INVALID", name)
            created = False
            if create:
                try:
                    os.mkdir(name, mode=0o750, dir_fd=current)
                    created = True
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise PaperDraftPdfError("OUTPUT_PARENT_INVALID", name) from exc
            descriptor = _open_directory_at(
                current,
                name,
                "OUTPUT_PARENT_INVALID",
            )
            metadata = os.fstat(descriptor)
            path_identity = _directory_identity_at(
                current,
                name,
                "OUTPUT_PARENT_REBOUND",
            )
            if not _same_filesystem_object(_identity(metadata), path_identity):
                os.close(descriptor)
                raise PaperDraftPdfError("OUTPUT_PARENT_REBOUND", name)
            if created:
                os.fchmod(descriptor, 0o750)
                _fsync_directory_fd(descriptor)
                _fsync_directory_fd(current)
                metadata = os.fstat(descriptor)
            item = DirectoryGuard(
                name=name,
                descriptor=descriptor,
                identity=_identity(metadata),
            )
            chain.append(item)
            current = descriptor
    except BaseException:
        for item in reversed(chain):
            os.close(item.descriptor)
        raise
    if not chain:
        raise PaperDraftPdfError("OUTPUT_PARENT_INVALID")
    return tuple(chain)


def _revalidate_parent_directory_chain(guard: OutputGuard) -> None:
    try:
        root_path_identity = _identity(
            os.stat(
                ".",
                dir_fd=guard.root_descriptor,
                follow_symlinks=False,
            )
        )
    except OSError as exc:
        raise PaperDraftPdfError("WORKSPACE_ROOT_REBOUND") from exc
    if not _same_filesystem_object(
        root_path_identity, guard.root_identity
    ) or not _same_filesystem_object(
        _identity(os.fstat(guard.root_descriptor)),
        guard.root_identity,
    ):
        raise PaperDraftPdfError("WORKSPACE_ROOT_REBOUND")
    parent = guard.root_descriptor
    for item in guard.parent_chain:
        path_identity = _directory_identity_at(
            parent,
            item.name,
            "OUTPUT_PARENT_REBOUND",
        )
        descriptor_identity = _identity(os.fstat(item.descriptor))
        if not _same_filesystem_object(
            path_identity, item.identity
        ) or not _same_filesystem_object(descriptor_identity, item.identity):
            raise PaperDraftPdfError("OUTPUT_PARENT_REBOUND", item.name)
        parent = item.descriptor
    if parent != guard.parent_descriptor:
        raise PaperDraftPdfError("OUTPUT_PARENT_REBOUND")


def _revalidate_output_guard(config: RenderConfig, guard: OutputGuard) -> None:
    try:
        workspace_path_identity = _identity(config.workspace_root.lstat())
    except OSError as exc:
        raise PaperDraftPdfError("WORKSPACE_ROOT_REBOUND") from exc
    if not _same_filesystem_object(
        workspace_path_identity,
        guard.root_identity,
    ):
        raise PaperDraftPdfError("WORKSPACE_ROOT_REBOUND")
    _revalidate_parent_directory_chain(guard)
    if not _same_filesystem_object(
        _identity(os.fstat(guard.parent_descriptor)),
        guard.parent_identity,
    ):
        raise PaperDraftPdfError("OUTPUT_PARENT_REBOUND")
    output_name = PurePosixPath(config.output_root).name
    if not _same_filesystem_object(
        _directory_identity_at(
            guard.parent_descriptor,
            output_name,
            "OUTPUT_ROOT_REBOUND",
        ),
        guard.output_identity,
    ):
        raise PaperDraftPdfError("OUTPUT_ROOT_REBOUND")
    _validate_directory(
        guard.output_descriptor,
        guard.output_identity,
        ROOT_MODE,
        "OUTPUT_ROOT_REBOUND",
    )
    if not _same_filesystem_object(
        _directory_identity_at(
            guard.output_descriptor,
            "generations",
            "GENERATIONS_REBOUND",
        ),
        guard.generations_identity,
    ):
        raise PaperDraftPdfError("GENERATIONS_REBOUND")
    _validate_directory(
        guard.generations_descriptor,
        guard.generations_identity,
        GENERATIONS_MODE,
        "GENERATIONS_REBOUND",
    )
    history_name = _history_directory_name(config)
    if not _same_filesystem_object(
        _directory_identity_at(
            guard.parent_descriptor,
            history_name,
            "HISTORY_REBOUND",
        ),
        guard.history_identity,
    ):
        raise PaperDraftPdfError("HISTORY_REBOUND")
    _validate_directory(
        guard.history_descriptor,
        guard.history_identity,
        HISTORY_MODE,
        "HISTORY_REBOUND",
    )
    lock_name = PurePosixPath(config.namespace.lock_path).name
    lock_path_identity = _identity(
        os.stat(
            lock_name,
            dir_fd=guard.parent_descriptor,
            follow_symlinks=False,
        )
    )
    if (
        lock_path_identity != guard.lock_identity
        or _identity(os.fstat(guard.lock_descriptor)) != guard.lock_identity
    ):
        raise PaperDraftPdfError("OUTPUT_LOCK_REBOUND")


@contextmanager
def _output_guard(
    config: RenderConfig,
    *,
    create: bool,
) -> Iterator[OutputGuard]:
    root_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    root_descriptor = os.open(config.workspace_root, root_flags)
    parent_chain: tuple[DirectoryGuard, ...] = ()
    parent_descriptor = output_descriptor = generations_descriptor = -1
    history_descriptor = -1
    lock_descriptor = -1
    try:
        root_identity = _identity(os.fstat(root_descriptor))
        if not _same_filesystem_object(
            root_identity,
            _identity(config.workspace_root.lstat()),
        ):
            raise PaperDraftPdfError("WORKSPACE_ROOT_REBOUND")
        fcntl.flock(root_descriptor, fcntl.LOCK_EX)
        output_relative = PurePosixPath(config.output_root)
        parent_relative = output_relative.parent
        if PurePosixPath(config.namespace.lock_path).parent != parent_relative:
            raise PaperDraftPdfError("OUTPUT_LOCK_PARENT_INVALID")
        parent_chain = _open_parent_directory_chain(
            root_descriptor,
            parent_relative,
            create=create,
        )
        parent_descriptor = parent_chain[-1].descriptor
        parent_identity = _identity(os.fstat(parent_descriptor))

        lock_name = PurePosixPath(config.namespace.lock_path).name
        lock_flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            try:
                lock_descriptor = os.open(
                    lock_name,
                    lock_flags | os.O_CREAT | os.O_EXCL,
                    LOCK_MODE,
                    dir_fd=parent_descriptor,
                )
                os.fchmod(lock_descriptor, LOCK_MODE)
                os.fsync(lock_descriptor)
                _fsync_directory_fd(parent_descriptor)
            except FileExistsError:
                lock_descriptor = os.open(
                    lock_name,
                    lock_flags,
                    dir_fd=parent_descriptor,
                )
        else:
            lock_descriptor = os.open(
                lock_name,
                lock_flags,
                dir_fd=parent_descriptor,
            )
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        lock_metadata = os.fstat(lock_descriptor)
        lock_identity = _identity(lock_metadata)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or stat.S_IMODE(lock_metadata.st_mode) != LOCK_MODE
            or lock_metadata.st_nlink != 1
            or lock_metadata.st_size != 0
            or _identity(
                os.stat(
                    lock_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            )
            != lock_identity
        ):
            raise PaperDraftPdfError("OUTPUT_LOCK_INVALID")

        output_descriptor, output_identity = _create_or_open_directory(
            parent_descriptor,
            output_relative.name,
            mode=ROOT_MODE,
            create=create,
            code="OUTPUT_ROOT_INVALID",
        )
        generations_descriptor, generations_identity = _create_or_open_directory(
            output_descriptor,
            "generations",
            mode=GENERATIONS_MODE,
            create=create,
            code="GENERATIONS_INVALID",
        )
        history_descriptor, history_identity = _create_or_open_directory(
            parent_descriptor,
            _history_directory_name(config),
            mode=HISTORY_MODE,
            create=create,
            code="HISTORY_INVALID",
        )
        guard = OutputGuard(
            root_descriptor=root_descriptor,
            root_identity=root_identity,
            parent_chain=parent_chain,
            parent_descriptor=parent_descriptor,
            output_descriptor=output_descriptor,
            generations_descriptor=generations_descriptor,
            history_descriptor=history_descriptor,
            lock_descriptor=lock_descriptor,
            parent_identity=parent_identity,
            output_identity=output_identity,
            generations_identity=generations_identity,
            history_identity=history_identity,
            lock_identity=lock_identity,
        )
        _revalidate_output_guard(config, guard)
        try:
            yield guard
        except BaseException as body_error:
            try:
                _revalidate_output_guard(config, guard)
            except BaseException as boundary_error:
                detail = getattr(
                    boundary_error,
                    "code",
                    type(boundary_error).__name__,
                )
                raise PaperDraftPdfError(
                    "OUTPUT_BOUNDARY_REVALIDATION_FAILED",
                    str(detail),
                ) from body_error
            raise
        else:
            _revalidate_output_guard(config, guard)
    finally:
        if lock_descriptor >= 0:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
        for descriptor in (
            history_descriptor,
            generations_descriptor,
            output_descriptor,
        ):
            if descriptor >= 0:
                os.close(descriptor)
        for item in reversed(parent_chain):
            os.close(item.descriptor)
        try:
            fcntl.flock(root_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(root_descriptor)


def _read_file_at(
    descriptor: int,
    name: str,
    *,
    mode: int,
    code: str,
    expected: FileRecord | None = None,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> FileSnapshot:
    if "/" in name or name in {"", ".", ".."}:
        raise PaperDraftPdfError(code, name)
    try:
        before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except OSError as exc:
        raise PaperDraftPdfError(f"{code}_MISSING", name) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != mode
        or before.st_nlink != 1
        or before.st_size > maximum
    ):
        raise PaperDraftPdfError(f"{code}_METADATA_INVALID", name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(name, flags, dir_fd=descriptor)
    except OSError as exc:
        raise PaperDraftPdfError(f"{code}_OPEN_FAILED", name) from exc
    try:
        opened = os.fstat(file_descriptor)
        identity = _identity(opened)
        if identity != _identity(before):
            raise PaperDraftPdfError(f"{code}_PATH_FD_MISMATCH", name)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise PaperDraftPdfError(f"{code}_SHORT_READ", name)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise PaperDraftPdfError(f"{code}_GREW", name)
        after_fd = os.fstat(file_descriptor)
    finally:
        os.close(file_descriptor)
    after_path = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if identity != _identity(after_fd) or identity != _identity(after_path):
        raise PaperDraftPdfError(f"{code}_PATH_CHANGED", name)
    payload = b"".join(chunks)
    record = FileRecord(name, _sha256(payload), len(payload))
    if expected is not None and (
        expected.sha256 != record.sha256
        or expected.size_bytes != record.size_bytes
        or PurePosixPath(expected.path).name != name
    ):
        raise PaperDraftPdfError(f"{code}_HASH_MISMATCH", name)
    return FileSnapshot(
        record=record,
        identity=identity,
        payload=payload,
        absolute_path=Path(f"/proc/self/fd/{descriptor}/{name}"),
    )


def _optional_file_at(
    descriptor: int,
    name: str,
    *,
    mode: int,
    code: str,
) -> FileSnapshot | None:
    try:
        os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return _read_file_at(descriptor, name, mode=mode, code=code)


def _revalidate_file_at(
    descriptor: int,
    name: str,
    snapshot: FileSnapshot,
    *,
    mode: int,
    code: str,
) -> None:
    current = _read_file_at(
        descriptor,
        name,
        mode=mode,
        code=code,
    )
    if current.identity != snapshot.identity or current.payload != snapshot.payload:
        raise PaperDraftPdfError(f"{code}_SNAPSHOT_CHANGED", name)


def _write_file_at(
    descriptor: int,
    name: str,
    payload: bytes,
    *,
    mode: int,
) -> None:
    temporary_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{name}.",
        suffix=".part",
        dir=f"/proc/self/fd/{descriptor}",
    )
    temporary_name = Path(temporary_path).name
    try:
        os.fchmod(temporary_descriptor, mode)
        with os.fdopen(temporary_descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            name,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        _fsync_directory_fd(descriptor)
    except BaseException:
        try:
            os.close(temporary_descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        raise
    snapshot = _read_file_at(
        descriptor,
        name,
        mode=mode,
        code="WRITTEN_FILE",
    )
    if snapshot.payload != payload:
        raise PaperDraftPdfError("WRITTEN_FILE_BYTES_MISMATCH", name)


def _unlink_if_present(descriptor: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=descriptor)
    except FileNotFoundError:
        return
    _fsync_directory_fd(descriptor)


def _remove_generation_at(generations_descriptor: int, name: str) -> None:
    try:
        generation_descriptor = _open_directory_at(
            generations_descriptor,
            name,
            "GENERATION_REMOVE_OPEN_FAILED",
        )
    except PaperDraftPdfError as exc:
        if exc.code == "GENERATION_REMOVE_OPEN_FAILED":
            try:
                os.stat(name, dir_fd=generations_descriptor)
            except FileNotFoundError:
                return
        raise
    try:
        os.fchmod(generation_descriptor, 0o700)
        entries = set(os.listdir(generation_descriptor))
        if "pages" in entries:
            pages_descriptor = _open_directory_at(
                generation_descriptor,
                "pages",
                "PAGES_REMOVE_OPEN_FAILED",
            )
            try:
                os.fchmod(pages_descriptor, 0o700)
                for page in os.listdir(pages_descriptor):
                    os.unlink(page, dir_fd=pages_descriptor)
                _fsync_directory_fd(pages_descriptor)
            finally:
                os.close(pages_descriptor)
            os.rmdir("pages", dir_fd=generation_descriptor)
            entries.remove("pages")
        for entry in entries:
            os.unlink(entry, dir_fd=generation_descriptor)
        _fsync_directory_fd(generation_descriptor)
    finally:
        os.close(generation_descriptor)
    os.rmdir(name, dir_fd=generations_descriptor)
    _fsync_directory_fd(generations_descriptor)


def _write_generation(
    config: RenderConfig,
    guard: OutputGuard,
    products: BuildProducts,
) -> CreatedGeneration | None:
    _revalidate_output_guard(config, guard)
    generation_id = products.generation_id
    try:
        metadata = os.stat(
            generation_id,
            dir_fd=guard.generations_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PaperDraftPdfError("GENERATION_EXISTING_INVALID")
        existing = _open_directory_at(
            guard.generations_descriptor,
            generation_id,
            "GENERATION_OPEN_FAILED",
        )
        os.close(existing)
        return None
    staging = f".staging-{generation_id}"
    try:
        os.mkdir(
            staging,
            mode=0o700,
            dir_fd=guard.generations_descriptor,
        )
    except FileExistsError:
        _remove_generation_at(guard.generations_descriptor, staging)
        os.mkdir(
            staging,
            mode=0o700,
            dir_fd=guard.generations_descriptor,
        )
    staging_descriptor = _open_directory_at(
        guard.generations_descriptor,
        staging,
        "GENERATION_STAGING_OPEN_FAILED",
    )
    staging_identity = _identity(os.fstat(staging_descriptor))
    current_name = staging
    keep_open = False
    try:
        os.mkdir("pages", mode=0o700, dir_fd=staging_descriptor)
        pages_descriptor = _open_directory_at(
            staging_descriptor,
            "pages",
            "PAGES_STAGING_OPEN_FAILED",
        )
        try:
            for page_name, payload in sorted(products.pages.items()):
                if PAGE_RE.fullmatch(page_name) is None:
                    raise PaperDraftPdfError("PAGE_NAME_INVALID", page_name)
                _write_file_at(
                    pages_descriptor,
                    page_name,
                    payload,
                    mode=FILE_MODE,
                )
            os.fchmod(pages_descriptor, PAGES_MODE)
            _fsync_directory_fd(pages_descriptor)
        finally:
            os.close(pages_descriptor)
        for name, payload in (
            ("draft.pdf", products.pdf),
            ("extracted-layout.txt", products.extracted_layout_text),
            ("extracted-semantic.txt", products.extracted_semantic_text),
            ("extracted-table.txt", products.extracted_table_text),
            ("postflight.json", products.postflight),
            ("receipt.json", products.receipt),
        ):
            _revalidate_output_guard(config, guard)
            _write_file_at(
                staging_descriptor,
                name,
                payload,
                mode=FILE_MODE,
            )
        os.fchmod(staging_descriptor, GENERATION_MODE)
        _fsync_directory_fd(staging_descriptor)
        _revalidate_output_guard(config, guard)
        try:
            os.rename(
                staging,
                generation_id,
                src_dir_fd=guard.generations_descriptor,
                dst_dir_fd=guard.generations_descriptor,
            )
        except FileExistsError:
            _remove_generation_at(guard.generations_descriptor, staging)
            return None
        current_name = generation_id
        final_identity = _directory_identity_at(
            guard.generations_descriptor,
            generation_id,
            "GENERATION_REBOUND",
        )
        if not _same_filesystem_object(final_identity, staging_identity):
            raise PaperDraftPdfError("GENERATION_REBOUND")
        _fsync_directory_fd(guard.generations_descriptor)
        _revalidate_output_guard(config, guard)
        pages_descriptor = _open_directory_at(
            staging_descriptor,
            "pages",
            "PAGES_FINAL_OPEN_FAILED",
        )
        pages_identity = _directory_identity_at(
            staging_descriptor,
            "pages",
            "PAGES_FINAL_REBOUND",
        )
        if not _same_filesystem_object(
            _identity(os.fstat(pages_descriptor)),
            pages_identity,
        ):
            os.close(pages_descriptor)
            raise PaperDraftPdfError("PAGES_FINAL_REBOUND")
        keep_open = True
        return CreatedGeneration(
            generation_id=generation_id,
            descriptor=staging_descriptor,
            pages_descriptor=pages_descriptor,
            identity=staging_identity,
            pages_identity=pages_identity,
        )
    except BaseException:
        try:
            current_identity = _directory_identity_at(
                guard.generations_descriptor,
                current_name,
                "GENERATION_ROLLBACK_REBOUND",
            )
        except PaperDraftPdfError as cleanup_error:
            if cleanup_error.code not in {
                "GENERATION_ROLLBACK_REBOUND",
            }:
                raise
        else:
            if not _same_filesystem_object(current_identity, staging_identity):
                raise PaperDraftPdfError("GENERATION_ROLLBACK_REBOUND")
            _remove_generation_at(guard.generations_descriptor, current_name)
        raise
    finally:
        if not keep_open:
            os.close(staging_descriptor)


def _commit_value(
    config: RenderConfig,
    products: BuildProducts,
) -> Mapping[str, Any]:
    receipt_path = (
        f"{config.output_root}/generations/{products.generation_id}/receipt.json"
    )
    core: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA,
        "status": "committed",
        "generation_id": products.generation_id,
        "claims": {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
        },
        "publication_namespace": {
            "config_path": config.namespace.config_path,
            "config_sha256": config.config_snapshot.record.sha256,
            "output_root": config.output_root,
            "commit_path": config.commit_path,
            "generations_name": "generations",
        },
        "receipt": FileRecord(
            receipt_path,
            products.generation_id,
            len(products.receipt),
        ).as_dict(),
    }
    core["binding_sha256"] = _sha256(_canonical_json(core))
    return core


def _previous_generation_id_for_retention(
    config: RenderConfig,
    guard: OutputGuard,
    previous_commit: FileSnapshot | None,
) -> str | None:
    generation_names = os.listdir(guard.generations_descriptor)
    if previous_commit is None:
        if generation_names:
            raise PaperDraftPdfError("PREVIOUS_CANONICAL_TREE_INVALID")
        return None
    commit = _json(previous_commit.payload, "PREVIOUS_COMMIT_JSON_INVALID")
    if _canonical_json(commit) != previous_commit.payload:
        raise PaperDraftPdfError("PREVIOUS_COMMIT_CANONICAL_INVALID")
    _exact_keys(
        commit,
        {
            "schema_version",
            "status",
            "generation_id",
            "claims",
            "publication_namespace",
            "receipt",
            "binding_sha256",
        },
        "PREVIOUS_COMMIT_KEYS_INVALID",
    )
    generation_id = _string(
        commit.get("generation_id"),
        "PREVIOUS_GENERATION_INVALID",
    )
    claims = _mapping(commit.get("claims"), "PREVIOUS_COMMIT_CLAIMS_INVALID")
    namespace = _mapping(
        commit.get("publication_namespace"),
        "PREVIOUS_COMMIT_NAMESPACE_INVALID",
    )
    config_digest = str(namespace.get("config_sha256", ""))
    if (
        commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
        or GENERATION_RE.fullmatch(generation_id) is None
        or generation_names != [generation_id]
        or claims
        != {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
        }
        or set(namespace)
        != {
            "config_path",
            "config_sha256",
            "output_root",
            "commit_path",
            "generations_name",
        }
        or namespace.get("config_path") != config.namespace.config_path
        or SHA256_RE.fullmatch(config_digest) is None
        or namespace.get("output_root") != config.output_root
        or namespace.get("commit_path") != config.commit_path
        or namespace.get("generations_name") != "generations"
    ):
        raise PaperDraftPdfError("PREVIOUS_COMMIT_INVALID")
    without_binding = dict(commit)
    binding = without_binding.pop("binding_sha256", None)
    if binding != _sha256(_canonical_json(without_binding)):
        raise PaperDraftPdfError("PREVIOUS_COMMIT_BINDING_INVALID")
    receipt_record = _parse_file_record(
        commit.get("receipt"),
        "PREVIOUS_COMMIT_RECEIPT_INVALID",
    )
    if (
        receipt_record.path
        != (f"{config.output_root}/generations/{generation_id}/receipt.json")
        or receipt_record.sha256 != generation_id
    ):
        raise PaperDraftPdfError("PREVIOUS_COMMIT_RECEIPT_INVALID")
    generation_descriptor, pages_descriptor, _, _ = _open_generation(
        guard,
        generation_id,
    )
    try:
        receipt = _read_file_at(
            generation_descriptor,
            "receipt.json",
            mode=FILE_MODE,
            code="PREVIOUS_RECEIPT",
        )
        if (
            len(receipt.payload) != receipt_record.size_bytes
            or _sha256(receipt.payload) != generation_id
        ):
            raise PaperDraftPdfError("PREVIOUS_RECEIPT_BINDING_INVALID")
    finally:
        os.close(pages_descriptor)
        os.close(generation_descriptor)
    return generation_id


def _retain_previous_generation(
    guard: OutputGuard,
    generation_id: str,
) -> RetainedGeneration:
    metadata = os.stat(
        generation_id,
        dir_fd=guard.generations_descriptor,
        follow_symlinks=False,
    )
    identity = _identity(metadata)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != GENERATION_MODE
    ):
        raise PaperDraftPdfError("PREVIOUS_GENERATION_METADATA_INVALID")
    retained_name = _move_to_unvalidated_history(
        guard,
        source_descriptor=guard.generations_descriptor,
        source_name=generation_id,
        preferred_name=generation_id,
        expected_identity=identity,
    )
    return RetainedGeneration(
        generation_id=generation_id,
        retained_name=retained_name,
        identity=identity,
    )


def _restore_retained_generation(
    guard: OutputGuard,
    retained: RetainedGeneration,
) -> None:
    descriptor = _open_directory_at(
        guard.history_descriptor,
        retained.retained_name,
        "HISTORY_RESTORE_OPEN_FAILED",
    )
    metadata = os.fstat(descriptor)
    original_mode = stat.S_IMODE(metadata.st_mode)
    if not _same_filesystem_object(_identity(metadata), retained.identity):
        os.close(descriptor)
        raise PaperDraftPdfError("HISTORY_RESTORE_IDENTITY_INVALID")
    os.fchmod(descriptor, original_mode | stat.S_IWUSR)
    try:
        _rename_noreplace_at(
            guard.history_descriptor,
            retained.retained_name,
            guard.generations_descriptor,
            retained.generation_id,
        )
    finally:
        os.fchmod(descriptor, original_mode)
        os.close(descriptor)
    restored = _identity(
        os.stat(
            retained.generation_id,
            dir_fd=guard.generations_descriptor,
            follow_symlinks=False,
        )
    )
    if not _same_filesystem_object(restored, retained.identity):
        raise PaperDraftPdfError("HISTORY_RESTORE_IDENTITY_INVALID")
    _fsync_directory_fd(guard.history_descriptor)
    _fsync_directory_fd(guard.generations_descriptor)


def _rollback_publish(
    guard: OutputGuard,
    *,
    previous_commit: FileSnapshot | None,
    created_generation: CreatedGeneration | None,
    retained_generation: RetainedGeneration | None,
) -> None:
    rebound_code: str | None = None
    if created_generation is not None:
        owned_names: list[str] = []
        for name in os.listdir(guard.generations_descriptor):
            metadata = os.stat(
                name,
                dir_fd=guard.generations_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISLNK(metadata.st_mode)
                and stat.S_ISDIR(metadata.st_mode)
                and _same_filesystem_object(
                    _identity(metadata),
                    created_generation.identity,
                )
            ):
                owned_names.append(name)
        if len(owned_names) != 1 or not _same_filesystem_object(
            _identity(os.fstat(created_generation.descriptor)),
            created_generation.identity,
        ):
            raise PaperDraftPdfError("GENERATION_ROLLBACK_REBOUND")
        owned_name = owned_names[0]
        generation_rebound = owned_name != created_generation.generation_id
        if generation_rebound:
            replacement_metadata = os.stat(
                created_generation.generation_id,
                dir_fd=guard.generations_descriptor,
                follow_symlinks=False,
            )
            replacement_identity = _identity(replacement_metadata)
            if _same_filesystem_object(
                replacement_identity,
                created_generation.identity,
            ):
                raise PaperDraftPdfError("GENERATION_ROLLBACK_AMBIGUOUS")
            _move_to_unvalidated_history(
                guard,
                source_descriptor=guard.generations_descriptor,
                source_name=created_generation.generation_id,
                preferred_name=(
                    f".quarantine-generation-{created_generation.generation_id}"
                ),
                expected_identity=replacement_identity,
            )
            rebound_code = "GENERATION_ROLLBACK_REBOUND"

        owned_pages_names: list[str] = []
        for name in os.listdir(created_generation.descriptor):
            metadata = os.stat(
                name,
                dir_fd=created_generation.descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISLNK(metadata.st_mode)
                and stat.S_ISDIR(metadata.st_mode)
                and _same_filesystem_object(
                    _identity(metadata),
                    created_generation.pages_identity,
                )
            ):
                owned_pages_names.append(name)
        if len(owned_pages_names) != 1 or not _same_filesystem_object(
            _identity(os.fstat(created_generation.pages_descriptor)),
            created_generation.pages_identity,
        ):
            raise PaperDraftPdfError("PAGES_ROLLBACK_REBOUND")
        owned_pages_name = owned_pages_names[0]
        if owned_pages_name != "pages":
            os.fchmod(created_generation.descriptor, 0o700)
            replacement_metadata = os.stat(
                "pages",
                dir_fd=created_generation.descriptor,
                follow_symlinks=False,
            )
            replacement_identity = _identity(replacement_metadata)
            if _same_filesystem_object(
                replacement_identity,
                created_generation.pages_identity,
            ):
                raise PaperDraftPdfError("PAGES_ROLLBACK_AMBIGUOUS")
            _move_to_unvalidated_history(
                guard,
                source_descriptor=created_generation.descriptor,
                source_name="pages",
                preferred_name=(
                    f".quarantine-pages-{created_generation.generation_id}"
                ),
                expected_identity=replacement_identity,
            )
            _rename_noreplace_at(
                created_generation.descriptor,
                owned_pages_name,
                created_generation.descriptor,
                "pages",
            )
            _fsync_directory_fd(created_generation.descriptor)
            rebound_code = "PAGES_ROLLBACK_REBOUND"

        _remove_generation_at(guard.generations_descriptor, owned_name)
        for name in os.listdir(guard.generations_descriptor):
            metadata = os.stat(
                name,
                dir_fd=guard.generations_descriptor,
                follow_symlinks=False,
            )
            if _same_filesystem_object(
                _identity(metadata),
                created_generation.identity,
            ):
                raise PaperDraftPdfError("GENERATION_ROLLBACK_INCOMPLETE")

    if retained_generation is not None:
        _restore_retained_generation(guard, retained_generation)
    if previous_commit is None:
        _unlink_if_present(guard.output_descriptor, "commit.json")
    else:
        _write_file_at(
            guard.output_descriptor,
            "commit.json",
            previous_commit.payload,
            mode=FILE_MODE,
        )
    if rebound_code is not None:
        raise PaperDraftPdfError(rebound_code)


def build_and_publish(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
    backend: BuildBackend | None = None,
) -> Mapping[str, Any]:
    """Build twice, postflight, transactionally publish, and deep-validate."""

    selected_backend = backend or ProductionBackend()
    _validate_implementation(config, implementation)
    with _output_guard(config, create=True) as guard:
        _revalidate_relative(
            config.workspace_root,
            config.config_snapshot,
            "CONFIG",
        )
        _validate_implementation(config, implementation)
        sources = _load_sources(config)
        tools = _load_tool_snapshots(config)
        _revalidate_sources(config, sources)
        _revalidate_tools(tools)
        products = _build_products(
            config,
            implementation,
            sources,
            tools,
            selected_backend,
        )
        _revalidate_relative(
            config.workspace_root,
            config.config_snapshot,
            "CONFIG",
        )
        _validate_implementation(config, implementation)
        _revalidate_sources(config, sources)
        _revalidate_tools(tools)
        _revalidate_output_guard(config, guard)
        previous_commit = _optional_file_at(
            guard.output_descriptor,
            "commit.json",
            mode=FILE_MODE,
            code="EXISTING_COMMIT",
        )
        previous_generation_id = _previous_generation_id_for_retention(
            config,
            guard,
            previous_commit,
        )
        created_generation: CreatedGeneration | None = None
        retained_generation: RetainedGeneration | None = None
        publication_started = False
        try:
            publication_started = True
            created_generation = _write_generation(config, guard, products)
            if (
                created_generation is not None
                and previous_generation_id is not None
                and previous_generation_id != products.generation_id
            ):
                retained_generation = _retain_previous_generation(
                    guard,
                    previous_generation_id,
                )
            commit = _canonical_json(_commit_value(config, products))
            _revalidate_output_guard(config, guard)
            _write_file_at(
                guard.output_descriptor,
                "commit.json",
                commit,
                mode=FILE_MODE,
            )
            _revalidate_output_guard(config, guard)
            return _validate_committed_locked(
                config,
                implementation=implementation,
                backend=selected_backend,
                guard=guard,
            )
        except BaseException:
            if publication_started:
                try:
                    _rollback_publish(
                        guard,
                        previous_commit=previous_commit,
                        created_generation=created_generation,
                        retained_generation=retained_generation,
                    )
                except BaseException as rollback_error:
                    raise PaperDraftPdfError(
                        "PUBLISH_ROLLBACK_FAILED",
                        type(rollback_error).__name__,
                    ) from rollback_error
            raise
        finally:
            if created_generation is not None:
                os.close(created_generation.pages_descriptor)
                os.close(created_generation.descriptor)


def _open_generation(
    guard: OutputGuard,
    generation_id: str,
) -> tuple[int, int, FileIdentity, FileIdentity]:
    if GENERATION_RE.fullmatch(generation_id) is None:
        raise PaperDraftPdfError("GENERATION_ID_INVALID")
    generation_descriptor = _open_directory_at(
        guard.generations_descriptor,
        generation_id,
        "GENERATION_OPEN_FAILED",
    )
    metadata = os.fstat(generation_descriptor)
    generation_identity = _directory_identity_at(
        guard.generations_descriptor,
        generation_id,
        "GENERATION_REBOUND",
    )
    if (
        not _same_filesystem_object(_identity(metadata), generation_identity)
        or stat.S_IMODE(metadata.st_mode) != GENERATION_MODE
    ):
        os.close(generation_descriptor)
        raise PaperDraftPdfError("GENERATION_MODE_INVALID")
    entries = set(os.listdir(generation_descriptor))
    if entries != set(GENERATION_FILES):
        os.close(generation_descriptor)
        raise PaperDraftPdfError("GENERATION_TREE_INVALID")
    pages_descriptor = _open_directory_at(
        generation_descriptor,
        "pages",
        "PAGES_OPEN_FAILED",
    )
    pages_metadata = os.fstat(pages_descriptor)
    pages_identity = _directory_identity_at(
        generation_descriptor,
        "pages",
        "PAGES_REBOUND",
    )
    if (
        not _same_filesystem_object(_identity(pages_metadata), pages_identity)
        or stat.S_IMODE(pages_metadata.st_mode) != PAGES_MODE
    ):
        os.close(pages_descriptor)
        os.close(generation_descriptor)
        raise PaperDraftPdfError("PAGES_MODE_INVALID")
    return (
        generation_descriptor,
        pages_descriptor,
        generation_identity,
        pages_identity,
    )


def _revalidate_open_generation(
    guard: OutputGuard,
    generation_id: str,
    generation_descriptor: int,
    pages_descriptor: int,
    generation_identity: FileIdentity,
    pages_identity: FileIdentity,
) -> None:
    generation_path_identity = _directory_identity_at(
        guard.generations_descriptor,
        generation_id,
        "GENERATION_REBOUND",
    )
    pages_path_identity = _directory_identity_at(
        generation_descriptor,
        "pages",
        "PAGES_REBOUND",
    )
    if (
        not _same_filesystem_object(generation_path_identity, generation_identity)
        or not _same_filesystem_object(
            _identity(os.fstat(generation_descriptor)),
            generation_identity,
        )
        or stat.S_IMODE(os.fstat(generation_descriptor).st_mode) != GENERATION_MODE
        or not _same_filesystem_object(pages_path_identity, pages_identity)
        or not _same_filesystem_object(
            _identity(os.fstat(pages_descriptor)),
            pages_identity,
        )
        or stat.S_IMODE(os.fstat(pages_descriptor).st_mode) != PAGES_MODE
    ):
        raise PaperDraftPdfError("GENERATION_REBOUND")


def _receipt_artifact_record(
    artifacts: Mapping[str, Any],
    key: str,
    expected_path: str,
) -> FileRecord:
    record = _parse_file_record(
        artifacts.get(key),
        f"RECEIPT_ARTIFACT_{key.upper()}_INVALID",
    )
    if record.path != expected_path:
        raise PaperDraftPdfError(f"RECEIPT_ARTIFACT_{key.upper()}_INVALID")
    return record


def _validate_output_namespace_tree(
    guard: OutputGuard,
    *,
    config: RenderConfig,
    implementation: ImplementationSnapshot,
    sources: SourceBundle,
    tools: Mapping[str, ToolSnapshot],
    html: HtmlArtifact,
    backend_id: str,
) -> None:
    if set(os.listdir(guard.output_descriptor)) != {"commit.json", "generations"}:
        raise PaperDraftPdfError("OUTPUT_ROOT_TREE_INVALID")
    generation_names = os.listdir(guard.generations_descriptor)
    if len(generation_names) != 1:
        raise PaperDraftPdfError(
            "CANONICAL_GENERATION_COUNT_INVALID",
            str(len(generation_names)),
        )
    for name in generation_names:
        if GENERATION_RE.fullmatch(name) is None:
            raise PaperDraftPdfError("GENERATION_NAME_INVALID", name)
        metadata = os.stat(
            name,
            dir_fd=guard.generations_descriptor,
            follow_symlinks=False,
        )
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != GENERATION_MODE
        ):
            raise PaperDraftPdfError("GENERATION_METADATA_INVALID", name)
        (
            generation_descriptor,
            pages_descriptor,
            generation_identity,
            pages_identity,
        ) = _open_generation(guard, name)
        try:
            receipt_snapshot = _read_file_at(
                generation_descriptor,
                "receipt.json",
                mode=FILE_MODE,
                code="HISTORICAL_RECEIPT",
            )
            if _sha256(receipt_snapshot.payload) != name:
                raise PaperDraftPdfError(
                    "HISTORICAL_RECEIPT_BINDING_INVALID",
                    name,
                )
            receipt = _json(
                receipt_snapshot.payload,
                "HISTORICAL_RECEIPT_JSON_INVALID",
            )
            if _canonical_json(receipt) != receipt_snapshot.payload:
                raise PaperDraftPdfError(
                    "HISTORICAL_RECEIPT_CANONICAL_INVALID",
                    name,
                )
            _validate_receipt_claims(receipt)
            (
                pdf,
                extracted_layout,
                extracted_semantic,
                extracted_table,
                postflight,
                pages,
            ) = _read_generation_artifacts(
                generation_descriptor,
                pages_descriptor,
                receipt,
            )
            postflight_value = _json(
                postflight,
                "HISTORICAL_POSTFLIGHT_JSON_INVALID",
            )
            if _canonical_json(postflight_value) != postflight:
                raise PaperDraftPdfError(
                    "HISTORICAL_POSTFLIGHT_CANONICAL_INVALID",
                    name,
                )
            historical_result = PostflightResult(
                document=postflight_value,
                extracted_layout_text=extracted_layout,
                extracted_semantic_text=extracted_semantic,
                extracted_table_text=extracted_table,
                pages=pages,
            )
            _validate_postflight_result(
                config,
                sources,
                html,
                pdf,
                historical_result,
            )
            tool_versions = _pinned_tool_versions_from_receipt(receipt, tools)
            expected_receipt = _receipt_value(
                config,
                implementation,
                sources,
                tools,
                tool_versions,
                html,
                pdf,
                postflight,
                extracted_layout,
                extracted_semantic,
                extracted_table,
                pages,
                backend_id=backend_id,
            )
            if receipt != expected_receipt:
                raise PaperDraftPdfError(
                    "HISTORICAL_RECEIPT_CONTENT_MISMATCH",
                    name,
                )
            _revalidate_open_generation(
                guard,
                name,
                generation_descriptor,
                pages_descriptor,
                generation_identity,
                pages_identity,
            )
        finally:
            os.close(pages_descriptor)
            os.close(generation_descriptor)


def _validate_commit(
    config: RenderConfig,
    guard: OutputGuard,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    commit_snapshot = _read_file_at(
        guard.output_descriptor,
        "commit.json",
        mode=FILE_MODE,
        code="COMMIT",
    )
    commit = _json(commit_snapshot.payload, "COMMIT_JSON_INVALID")
    if _canonical_json(commit) != commit_snapshot.payload:
        raise PaperDraftPdfError("COMMIT_CANONICAL_BYTES_INVALID")
    _exact_keys(
        commit,
        {
            "schema_version",
            "status",
            "generation_id",
            "claims",
            "publication_namespace",
            "receipt",
            "binding_sha256",
        },
        "COMMIT_KEYS_INVALID",
    )
    claims = _mapping(commit.get("claims"), "COMMIT_CLAIMS_INVALID")
    if (
        commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
        or claims
        != {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
        }
    ):
        raise PaperDraftPdfError("COMMIT_STATUS_INVALID")
    namespace = _mapping(
        commit.get("publication_namespace"),
        "COMMIT_NAMESPACE_INVALID",
    )
    if namespace != {
        "config_path": config.namespace.config_path,
        "config_sha256": config.config_snapshot.record.sha256,
        "output_root": config.output_root,
        "commit_path": config.commit_path,
        "generations_name": "generations",
    }:
        raise PaperDraftPdfError("COMMIT_NAMESPACE_INVALID")
    generation_id = _string(commit.get("generation_id"), "COMMIT_GENERATION_INVALID")
    if GENERATION_RE.fullmatch(generation_id) is None:
        raise PaperDraftPdfError("COMMIT_GENERATION_INVALID")
    receipt = _parse_file_record(
        commit.get("receipt"),
        "COMMIT_RECEIPT_INVALID",
    )
    expected_receipt_path = (
        f"{config.output_root}/generations/{generation_id}/receipt.json"
    )
    if receipt.path != expected_receipt_path or receipt.sha256 != generation_id:
        raise PaperDraftPdfError("COMMIT_RECEIPT_INVALID")
    without_binding = dict(commit)
    binding = without_binding.pop("binding_sha256", None)
    if binding != _sha256(_canonical_json(without_binding)):
        raise PaperDraftPdfError("COMMIT_BINDING_INVALID")
    return commit, commit_snapshot


def _read_generation_artifacts(
    generation_descriptor: int,
    pages_descriptor: int,
    receipt: Mapping[str, Any],
) -> tuple[bytes, bytes, bytes, bytes, bytes, Mapping[str, bytes]]:
    artifacts = _mapping(receipt.get("artifacts"), "RECEIPT_ARTIFACTS_INVALID")
    pdf_record = _receipt_artifact_record(artifacts, "draft_pdf", "draft.pdf")
    layout_text_record = _receipt_artifact_record(
        artifacts,
        "extracted_layout_text",
        "extracted-layout.txt",
    )
    semantic_text_record = _receipt_artifact_record(
        artifacts,
        "extracted_semantic_text",
        "extracted-semantic.txt",
    )
    table_text_record = _receipt_artifact_record(
        artifacts,
        "extracted_table_text",
        "extracted-table.txt",
    )
    postflight_record = _receipt_artifact_record(
        artifacts,
        "postflight",
        "postflight.json",
    )
    pdf = _read_file_at(
        generation_descriptor,
        "draft.pdf",
        mode=FILE_MODE,
        code="DRAFT_PDF",
        expected=pdf_record,
    ).payload
    layout_text = _read_file_at(
        generation_descriptor,
        "extracted-layout.txt",
        mode=FILE_MODE,
        code="EXTRACTED_LAYOUT_TEXT",
        expected=layout_text_record,
    ).payload
    semantic_text = _read_file_at(
        generation_descriptor,
        "extracted-semantic.txt",
        mode=FILE_MODE,
        code="EXTRACTED_SEMANTIC_TEXT",
        expected=semantic_text_record,
    ).payload
    table_text = _read_file_at(
        generation_descriptor,
        "extracted-table.txt",
        mode=FILE_MODE,
        code="EXTRACTED_TABLE_TEXT",
        expected=table_text_record,
    ).payload
    postflight = _read_file_at(
        generation_descriptor,
        "postflight.json",
        mode=FILE_MODE,
        code="POSTFLIGHT",
        expected=postflight_record,
    ).payload
    page_records = artifacts.get("pages")
    if not isinstance(page_records, list) or not page_records:
        raise PaperDraftPdfError("RECEIPT_PAGE_RECORDS_INVALID")
    records: dict[str, FileRecord] = {}
    for raw in page_records:
        record = _parse_file_record(raw, "RECEIPT_PAGE_RECORD_INVALID")
        path = PurePosixPath(record.path)
        if (
            len(path.parts) != 2
            or path.parts[0] != "pages"
            or PAGE_RE.fullmatch(path.parts[1]) is None
            or path.parts[1] in records
        ):
            raise PaperDraftPdfError("RECEIPT_PAGE_RECORD_INVALID")
        records[path.parts[1]] = record
    observed_names = sorted(os.listdir(pages_descriptor))
    if observed_names != sorted(records):
        raise PaperDraftPdfError("PAGES_TREE_INVALID")
    pages = {
        name: _read_file_at(
            pages_descriptor,
            name,
            mode=FILE_MODE,
            code="PAGE",
            expected=records[name],
        ).payload
        for name in observed_names
    }
    if observed_names != [
        f"page-{index:03d}.png" for index in range(1, len(observed_names) + 1)
    ]:
        raise PaperDraftPdfError("PAGE_SEQUENCE_INVALID")
    return (
        pdf,
        layout_text,
        semantic_text,
        table_text,
        postflight,
        pages,
    )


def _validate_receipt_claims(receipt: Mapping[str, Any]) -> None:
    claims = _mapping(receipt.get("claims"), "RECEIPT_CLAIMS_INVALID")
    privacy = _mapping(receipt.get("privacy"), "RECEIPT_PRIVACY_INVALID")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("scope") != "aggregate_only_paper_draft_pdf"
        or claims
        != {
            "draft": True,
            "final": False,
            "camera_ready": False,
            "p05_complete": False,
            "visual_review_status": "pending",
        }
        or privacy
        != {
            "raw_files_read": 0,
            "locked_test_files_read": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
        }
    ):
        raise PaperDraftPdfError("RECEIPT_CLAIMS_INVALID")


def _pinned_tool_versions_from_receipt(
    receipt: Mapping[str, Any],
    tools: Mapping[str, ToolSnapshot],
) -> Mapping[str, str]:
    receipt_tools = _mapping(receipt.get("tools"), "RECEIPT_TOOLS_INVALID")
    if set(receipt_tools) != set(TOOL_KEYS):
        raise PaperDraftPdfError("RECEIPT_TOOLS_INVALID")
    versions: dict[str, str] = {}
    for key in TOOL_KEYS:
        expected = {
            **tools[key].record.as_dict(),
            "observed_version": tools[key].record.version,
        }
        if receipt_tools.get(key) != expected:
            raise PaperDraftPdfError("RECEIPT_TOOL_PIN_INVALID", key)
        versions[key] = tools[key].record.version
    return versions


def _assert_output_source_inodes_distinct(
    config: RenderConfig,
    sources: SourceBundle,
    implementation: ImplementationSnapshot,
    tools: Mapping[str, ToolSnapshot],
    outputs: Sequence[FileSnapshot],
) -> None:
    values: list[tuple[str, FileIdentity]] = [
        ("config", config.config_snapshot.identity),
        *[
            (key, snapshot.identity)
            for key, snapshot in {
                **sources.snapshots,
                **sources.indirect_snapshots,
            }.items()
        ],
        ("module", implementation.module.identity),
        ("cli", implementation.cli.identity),
        *[(key, snapshot.identity) for key, snapshot in tools.items()],
        *[
            (f"output-{index}", snapshot.identity)
            for index, snapshot in enumerate(outputs)
        ],
    ]
    seen: dict[tuple[int, int], str] = {}
    for name, identity in values:
        key = (identity.device, identity.inode)
        if key in seen:
            raise PaperDraftPdfError("OUTPUT_SOURCE_INODE_ALIAS", f"{seen[key]}={name}")
        seen[key] = name


def _validate_committed_locked(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
    backend: BuildBackend,
    guard: OutputGuard,
) -> Mapping[str, Any]:
    if config.namespace.production == backend.test_only:
        raise PaperDraftPdfError("BUILD_BACKEND_KIND_INVALID")
    _revalidate_output_guard(config, guard)
    _revalidate_relative(config.workspace_root, config.config_snapshot, "CONFIG")
    _validate_implementation(config, implementation)
    sources = _load_sources(config)
    tools = _load_tool_snapshots(config)
    html = render_sanitized_html(config, sources.manuscript)
    _validate_output_namespace_tree(
        guard,
        config=config,
        implementation=implementation,
        sources=sources,
        tools=tools,
        html=html,
        backend_id=backend.backend_id,
    )
    commit, commit_snapshot = _validate_commit(config, guard)
    generation_id = str(commit["generation_id"])
    (
        generation_descriptor,
        pages_descriptor,
        generation_identity,
        pages_identity,
    ) = _open_generation(guard, generation_id)
    try:
        receipt_snapshot = _read_file_at(
            generation_descriptor,
            "receipt.json",
            mode=FILE_MODE,
            code="RECEIPT",
        )
        if (
            receipt_snapshot.record.sha256 != generation_id
            or _sha256(receipt_snapshot.payload) != generation_id
            or _parse_file_record(
                commit.get("receipt"),
                "COMMIT_RECEIPT_INVALID",
            ).size_bytes
            != len(receipt_snapshot.payload)
        ):
            raise PaperDraftPdfError("RECEIPT_GENERATION_BINDING_INVALID")
        receipt = _json(receipt_snapshot.payload, "RECEIPT_JSON_INVALID")
        if _canonical_json(receipt) != receipt_snapshot.payload:
            raise PaperDraftPdfError("RECEIPT_CANONICAL_BYTES_INVALID")
        _validate_receipt_claims(receipt)
        (
            pdf,
            extracted_layout,
            extracted_semantic,
            extracted_table,
            postflight,
            pages,
        ) = _read_generation_artifacts(
            generation_descriptor,
            pages_descriptor,
            receipt,
        )
        postflight_value = _json(postflight, "POSTFLIGHT_JSON_INVALID")
        if _canonical_json(postflight_value) != postflight:
            raise PaperDraftPdfError("POSTFLIGHT_CANONICAL_BYTES_INVALID")
        output_snapshots = [
            commit_snapshot,
            receipt_snapshot,
            _read_file_at(
                generation_descriptor,
                "draft.pdf",
                mode=FILE_MODE,
                code="DRAFT_PDF",
            ),
            _read_file_at(
                generation_descriptor,
                "extracted-layout.txt",
                mode=FILE_MODE,
                code="EXTRACTED_LAYOUT_TEXT",
            ),
            _read_file_at(
                generation_descriptor,
                "extracted-semantic.txt",
                mode=FILE_MODE,
                code="EXTRACTED_SEMANTIC_TEXT",
            ),
            _read_file_at(
                generation_descriptor,
                "extracted-table.txt",
                mode=FILE_MODE,
                code="EXTRACTED_TABLE_TEXT",
            ),
            _read_file_at(
                generation_descriptor,
                "postflight.json",
                mode=FILE_MODE,
                code="POSTFLIGHT",
            ),
            *[
                _read_file_at(
                    pages_descriptor,
                    name,
                    mode=FILE_MODE,
                    code="PAGE",
                )
                for name in sorted(pages)
            ],
        ]
        tool_versions = _pinned_tool_versions_from_receipt(receipt, tools)
        with tempfile.TemporaryDirectory(
            prefix="voice-coach-paper-draft-deep-",
        ) as temporary:
            work = Path(temporary)
            recomputed = backend.postflight(
                config,
                pdf,
                sources,
                html,
                tools,
                work,
            )
            _validate_postflight_result(
                config,
                sources,
                html,
                pdf,
                recomputed,
            )
        expected_postflight = _canonical_json(recomputed.document)
        if (
            expected_postflight != postflight
            or recomputed.extracted_layout_text != extracted_layout
            or recomputed.extracted_semantic_text != extracted_semantic
            or recomputed.extracted_table_text != extracted_table
            or dict(recomputed.pages) != dict(pages)
        ):
            raise PaperDraftPdfError("POSTFLIGHT_RECOMPUTATION_MISMATCH")
        expected_receipt = _receipt_value(
            config,
            implementation,
            sources,
            tools,
            tool_versions,
            html,
            pdf,
            postflight,
            extracted_layout,
            extracted_semantic,
            extracted_table,
            pages,
            backend_id=backend.backend_id,
        )
        if expected_receipt != receipt:
            raise PaperDraftPdfError("RECEIPT_CONTENT_MISMATCH")
        _assert_output_source_inodes_distinct(
            config,
            sources,
            implementation,
            tools,
            output_snapshots,
        )
        _revalidate_relative(
            config.workspace_root,
            config.config_snapshot,
            "CONFIG",
        )
        _validate_implementation(config, implementation)
        _revalidate_sources(config, sources)
        _revalidate_tools(tools)
        _revalidate_output_guard(config, guard)
        _validate_output_namespace_tree(
            guard,
            config=config,
            implementation=implementation,
            sources=sources,
            tools=tools,
            html=html,
            backend_id=backend.backend_id,
        )
        final_commit, final_commit_snapshot = _validate_commit(config, guard)
        if (
            final_commit != commit
            or final_commit_snapshot.identity != commit_snapshot.identity
            or final_commit_snapshot.payload != commit_snapshot.payload
        ):
            raise PaperDraftPdfError("COMMIT_SNAPSHOT_CHANGED")
        _revalidate_file_at(
            generation_descriptor,
            "receipt.json",
            receipt_snapshot,
            mode=FILE_MODE,
            code="RECEIPT_FINAL",
        )
        for name, snapshot in zip(
            (
                "draft.pdf",
                "extracted-layout.txt",
                "extracted-semantic.txt",
                "extracted-table.txt",
                "postflight.json",
            ),
            output_snapshots[2:7],
            strict=True,
        ):
            _revalidate_file_at(
                generation_descriptor,
                name,
                snapshot,
                mode=FILE_MODE,
                code="ARTIFACT_FINAL",
            )
        for name, snapshot in zip(
            sorted(pages),
            output_snapshots[7:],
            strict=True,
        ):
            _revalidate_file_at(
                pages_descriptor,
                name,
                snapshot,
                mode=FILE_MODE,
                code="PAGE_FINAL",
            )
        _revalidate_open_generation(
            guard,
            generation_id,
            generation_descriptor,
            pages_descriptor,
            generation_identity,
            pages_identity,
        )
    finally:
        os.close(pages_descriptor)
        os.close(generation_descriptor)
    _revalidate_relative(
        config.workspace_root,
        config.config_snapshot,
        "CONFIG",
    )
    _validate_implementation(config, implementation)
    _revalidate_sources(config, sources)
    _revalidate_tools(tools)
    _revalidate_output_guard(config, guard)
    _validate_output_namespace_tree(
        guard,
        config=config,
        implementation=implementation,
        sources=sources,
        tools=tools,
        html=html,
        backend_id=backend.backend_id,
    )
    final_commit, final_commit_snapshot = _validate_commit(config, guard)
    if (
        final_commit != commit
        or final_commit_snapshot.identity != commit_snapshot.identity
        or final_commit_snapshot.payload != commit_snapshot.payload
    ):
        raise PaperDraftPdfError("COMMIT_SNAPSHOT_CHANGED")
    return commit


def validate_committed(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
    backend: BuildBackend | None = None,
) -> Mapping[str, Any]:
    """Deep-validate the committed draft without running LibreOffice or GS."""

    selected_backend = backend or ProductionBackend()
    _validate_implementation(config, implementation)
    with _output_guard(config, create=False) as guard:
        return _validate_committed_locked(
            config,
            implementation=implementation,
            backend=selected_backend,
            guard=guard,
        )


_MODULE_IMPORT_SNAPSHOT: Final = capture_absolute_snapshot(
    Path(__file__),
    record_path=CANONICAL_MODULE,
    code="MODULE_IMPORT",
)
