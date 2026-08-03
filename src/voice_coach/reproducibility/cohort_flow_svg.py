"""Deterministic, aggregate-only SVG renderer for the AI Hub cohort flow.

The renderer intentionally supports only the small Mermaid subset used by the
canonical cohort diagram.  It validates every aggregate against immutable JSON
sources before emitting a portable SVG with no scripts, external references, or
``foreignObject`` elements.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Final, Iterator, Mapping, NoReturn, Sequence
import xml.etree.ElementTree as ET

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA: Final = "voice_coach.paper.cohort_flow_svg_config.v2"
RECEIPT_SCHEMA: Final = "voice_coach.paper.cohort_flow_svg_receipt.v2"
CANONICAL_CONFIG_PATH: Final = "configs/paper/aihub71490_cohort_flow_v1.yaml"
CANONICAL_RENDERER_PATH: Final = "src/voice_coach/reproducibility/cohort_flow_svg.py"
CANONICAL_CLI_PATH: Final = "scripts/render_aihub71490_cohort_flow.py"
CANONICAL_OUTPUT_SVG: Final = "docs/paper/figures/aihub71490_cohort_flow.svg"
CANONICAL_RECEIPT: Final = (
    "artifacts/reproducibility/paper_figures/aihub71490_cohort_flow_v1.json"
)
CANONICAL_LOCK: Final = (
    "artifacts/reproducibility/paper_figures/.aihub71490_cohort_flow_v1.lock"
)
CANONICAL_SOURCE_PATHS: Final = {
    "source_mmd": "docs/paper/figures/aihub71490_cohort_flow.mmd",
    "data_card": ("data/processed/aihub71490/manifests/aihub71490_data_card.json"),
    "g0_audit": "data/processed/aihub71490/manifests/aihub71490_g0_audit.json",
    "prompt_reservation": (
        "data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json"
    ),
    "filter_report": (
        "data/processed/aihub71490/features/explicit_utterance_train_dev_v1_report.json"
    ),
}
CANONICAL_TITLE: Final = "AI Hub 71490 학습·개발 cohort 흐름"
CANONICAL_DESCRIPTION: Final = (
    "322,380건의 집계 메타데이터에서 평가 split, QC 오류, 예약 prompt를 "
    "순서대로 제외해 169,837건의 train/dev cache를 구성한 흐름이다. "
    "자동 G0는 통과했지만 외부 전문가 정렬 감사는 PENDING이다."
)
SVG_MODE: Final = 0o644
RECEIPT_MODE: Final = 0o440
LOCK_MODE: Final = 0o600
EXPECTED_NODE_IDS: Final = (
    "A",
    "S",
    "X1",
    "B",
    "Q",
    "X2",
    "C",
    "R",
    "X3",
    "D",
    "T",
    "V",
    "G",
    "G1",
    "G2",
    "H",
)
EXPECTED_EDGES: Final = (
    ("A", "S", None, False),
    ("S", "X1", "제외", False),
    ("S", "B", "train/dev", False),
    ("B", "Q", None, False),
    ("Q", "X2", "제외", False),
    ("Q", "C", "통과", False),
    ("C", "R", None, False),
    ("R", "X3", "제외", False),
    ("R", "D", "통과", False),
    ("D", "T", None, False),
    ("D", "V", None, False),
    ("A", "G", "aggregate audit", True),
    ("G", "G1", None, False),
    ("G", "G2", None, False),
    ("G", "H", "대체하지 않음", True),
)
EXPECTED_CLASS_MEMBERS: Final = {
    "source": ("A", "G", "G1", "G2"),
    "gate": ("S", "Q", "R"),
    "excluded": ("X1", "X2", "X3"),
    "included": ("B", "C", "D", "T", "V"),
    "pending": ("H",),
}
EXPECTED_STYLES: Final = {
    "source": ("#e8f0fe", "#2457a6", "#12223a"),
    "gate": ("#fff4cc", "#9a6b00", "#3c2a00"),
    "excluded": ("#fde8e8", "#a52a2a", "#411"),
    "included": ("#e4f5e9", "#24753d", "#153d23"),
    "pending": ("#f2e8ff", "#7040a0", "#321850"),
}
SOURCE_KEYS: Final = ("data_card", "g0_audit", "prompt_reservation", "filter_report")
LINEAGE_LABELS: Final = {
    "data card": "data_card",
    "automatic G0": "g0_audit",
    "prompt challenge": "prompt_reservation",
    "explicit utterance filter report": "filter_report",
}
FORBIDDEN_PATH_PARTS: Final = {
    ".env",
    ".env.local",
    "credential",
    "credentials",
    "locked",
    "locked_test",
    "locked-test",
    "raw",
    "restricted",
    "secret",
    "secrets",
}
MAX_SOURCE_BYTES: Final = 8 * 1024 * 1024
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
NODE_RE: Final = re.compile(r'^\s{4}([A-Z][A-Z0-9]*)(\[|\{)"([^"]+)"(\]|\})$')
SOLID_EDGE_RE: Final = re.compile(
    r'^\s{4}([A-Z][A-Z0-9]*)\s+-->(?:\|"([^"]+)"\|)?\s+'
    r"([A-Z][A-Z0-9]*)$"
)
DOTTED_EDGE_RE: Final = re.compile(
    r'^\s{4}([A-Z][A-Z0-9]*)\s+-\.\s+"([^"]+)"\s+\.-\>\s+'
    r"([A-Z][A-Z0-9]*)$"
)
CLASS_DEF_RE: Final = re.compile(
    r"^\s{4}classDef\s+([a-z]+)\s+fill:(#[0-9a-f]+),"
    r"stroke:(#[0-9a-f]+),color:(#[0-9a-f]+);$"
)
CLASS_MEMBER_RE: Final = re.compile(r"^\s{4}class\s+([A-Z0-9,]+)\s+([a-z]+);$")


class CohortFlowValidationError(ValueError):
    """Fail-closed validation error with a stable machine-readable code."""

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
class RenderNamespace:
    config_path: str
    source_paths: Mapping[str, str]
    renderer_path: str
    cli_path: str
    output_svg: str
    receipt: str
    lock_path: str
    title: str
    description: str
    production: bool = False


CANONICAL_NAMESPACE: Final = RenderNamespace(
    config_path=CANONICAL_CONFIG_PATH,
    source_paths=CANONICAL_SOURCE_PATHS,
    renderer_path=CANONICAL_RENDERER_PATH,
    cli_path=CANONICAL_CLI_PATH,
    output_svg=CANONICAL_OUTPUT_SVG,
    receipt=CANONICAL_RECEIPT,
    lock_path=CANONICAL_LOCK,
    title=CANONICAL_TITLE,
    description=CANONICAL_DESCRIPTION,
    production=True,
)


@dataclass(frozen=True)
class ImplementationSnapshot:
    renderer: FileSnapshot
    cli: FileSnapshot
    test_only: bool


@dataclass(frozen=True)
class PublishedPairState:
    svg: FileSnapshot | None
    receipt: FileSnapshot | None

    @property
    def complete(self) -> bool:
        return self.svg is not None and self.receipt is not None


@dataclass(frozen=True)
class ValidatedSources:
    diagram: Diagram
    snapshots: Mapping[str, FileSnapshot]
    decoded: Mapping[str, Mapping[str, Any]]

    @property
    def records(self) -> Mapping[str, FileRecord]:
        return {key: value.record for key, value in self.snapshots.items()}


@dataclass(frozen=True)
class LabelSpan:
    text: str
    bold: bool


@dataclass(frozen=True)
class DiagramNode:
    node_id: str
    shape: str
    lines: tuple[tuple[LabelSpan, ...], ...]

    @property
    def plain_lines(self) -> tuple[str, ...]:
        return tuple("".join(span.text for span in line) for line in self.lines)


@dataclass(frozen=True)
class Diagram:
    nodes: Mapping[str, DiagramNode]
    edges: tuple[tuple[str, str, str | None, bool], ...]
    styles: Mapping[str, tuple[str, str, str]]
    class_members: Mapping[str, tuple[str, ...]]
    lineage: Mapping[str, str]


@dataclass(frozen=True)
class RenderConfig:
    workspace_root: Path
    config_path: Path
    namespace: RenderNamespace
    config_snapshot: FileSnapshot
    title: str
    description: str
    width: int
    height: int
    source_mmd: FileRecord
    sources: Mapping[str, FileRecord]
    implementation: Mapping[str, FileRecord]
    output_svg: str
    receipt: str


@dataclass(frozen=True)
class NodeLayout:
    x: int
    y: int
    width: int
    height: int


LAYOUT: Final = {
    "A": NodeLayout(360, 90, 330, 74),
    "S": NodeLayout(360, 190, 220, 88),
    "X1": NodeLayout(750, 190, 390, 104),
    "B": NodeLayout(360, 300, 300, 74),
    "Q": NodeLayout(360, 400, 220, 88),
    "X2": NodeLayout(750, 400, 250, 74),
    "C": NodeLayout(360, 500, 300, 74),
    "R": NodeLayout(360, 600, 220, 88),
    "X3": NodeLayout(750, 600, 270, 74),
    "D": NodeLayout(360, 700, 320, 74),
    "T": NodeLayout(190, 820, 220, 74),
    "V": NodeLayout(530, 820, 220, 74),
    "G": NodeLayout(1120, 90, 300, 64),
    "G1": NodeLayout(1120, 235, 350, 100),
    "G2": NodeLayout(1120, 385, 350, 100),
    "H": NodeLayout(1120, 550, 350, 100),
}


EDGE_PATHS: Final = (
    ("A", "S", "M 360 127 V 146"),
    ("S", "X1", "M 470 190 H 555"),
    ("S", "B", "M 360 234 V 263"),
    ("B", "Q", "M 360 337 V 356"),
    ("Q", "X2", "M 470 400 H 625"),
    ("Q", "C", "M 360 444 V 463"),
    ("C", "R", "M 360 537 V 556"),
    ("R", "X3", "M 470 600 H 615"),
    ("R", "D", "M 360 644 V 663"),
    ("D", "T", "M 300 737 V 770 H 190 V 783"),
    ("D", "V", "M 420 737 V 770 H 530 V 783"),
    ("A", "G", "M 525 90 H 970"),
    ("G", "G1", "M 1120 122 V 185"),
    ("G", "G2", "M 970 90 H 960 V 385 H 945"),
    ("G", "H", "M 1270 90 H 1400 V 550 H 1295"),
)


EDGE_LABELS: Final = {
    ("S", "X1"): (515, 176),
    ("S", "B"): (410, 254),
    ("Q", "X2"): (545, 386),
    ("Q", "C"): (405, 454),
    ("R", "X3"): (545, 586),
    ("R", "D"): (405, 654),
    ("A", "G"): (745, 76),
    ("G", "H"): (1348, 315),
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _raise_yaml_duplicate_key(_: str) -> NoReturn:
    raise CohortFlowValidationError("CONFIG_DUPLICATE_KEY")


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
            raise CohortFlowValidationError("CONFIG_KEY_INVALID")
        if key in result:
            _raise_yaml_duplicate_key(key)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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


def _require_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CohortFlowValidationError(code)
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    code: str,
) -> None:
    if set(value) != expected:
        raise CohortFlowValidationError(
            code,
            f"expected={sorted(expected)} actual={sorted(value)}",
        )


def _safe_relative_path(value: Any, *, code: str, output: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise CohortFlowValidationError(code)
    if "\x00" in value or "\\" in value:
        raise CohortFlowValidationError(code, value)
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or pure.as_posix() != value
    ):
        raise CohortFlowValidationError(code, value)
    lowered = {part.lower() for part in pure.parts}
    if lowered & FORBIDDEN_PATH_PARTS:
        raise CohortFlowValidationError(code, value)
    if any(part.startswith(".env") for part in lowered):
        raise CohortFlowValidationError(code, value)
    return pure.as_posix()


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        nlink=metadata.st_nlink,
        size_bytes=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _assert_parent_chain(root: Path, relative: str, *, code: str) -> Path:
    path = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            raise CohortFlowValidationError(code, relative) from None
        except OSError as exc:
            raise CohortFlowValidationError(code, relative) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CohortFlowValidationError(code, relative)
    return path


def _read_stable_path(
    root: Path,
    relative: str,
    *,
    expected_sha256: str | None,
    code: str,
) -> FileSnapshot:
    path = _assert_parent_chain(root, relative, code=f"{code}_PARENT_INVALID")
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise CohortFlowValidationError(f"{code}_MISSING", relative) from exc
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise CohortFlowValidationError(f"{code}_NOT_REGULAR", relative)
    if path_before.st_nlink != 1:
        raise CohortFlowValidationError(f"{code}_NLINK_INVALID", relative)
    if path_before.st_size > MAX_SOURCE_BYTES:
        raise CohortFlowValidationError(f"{code}_TOO_LARGE", relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CohortFlowValidationError(f"{code}_OPEN_FAILED", relative) from exc
    try:
        descriptor_before = os.fstat(descriptor)
        before_identity = _identity(path_before)
        descriptor_identity = _identity(descriptor_before)
        if before_identity != descriptor_identity:
            raise CohortFlowValidationError(f"{code}_PATH_FD_MISMATCH", relative)
        chunks: list[bytes] = []
        remaining = descriptor_before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise CohortFlowValidationError(f"{code}_SHORT_READ", relative)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise CohortFlowValidationError(f"{code}_GREW", relative)
        descriptor_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise CohortFlowValidationError(f"{code}_PATH_CHANGED", relative) from exc
    if (
        _identity(descriptor_after) != descriptor_identity
        or _identity(path_after) != descriptor_identity
    ):
        raise CohortFlowValidationError(f"{code}_PATH_CHANGED", relative)
    payload = b"".join(chunks)
    digest = _sha256_bytes(payload)
    if expected_sha256 is not None and digest != expected_sha256:
        raise CohortFlowValidationError(f"{code}_HASH_MISMATCH", relative)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CohortFlowValidationError(f"{code}_OUTSIDE_WORKSPACE", relative) from exc
    return FileSnapshot(
        record=FileRecord(relative, digest, len(payload)),
        identity=descriptor_identity,
        payload=payload,
        absolute_path=resolved,
    )


def _revalidate_snapshot(
    root: Path,
    snapshot: FileSnapshot,
    *,
    code: str,
) -> FileSnapshot:
    current = _read_stable_path(
        root,
        snapshot.record.path,
        expected_sha256=snapshot.record.sha256,
        code=code,
    )
    if current.identity != snapshot.identity or current.payload != snapshot.payload:
        raise CohortFlowValidationError(
            f"{code}_SNAPSHOT_CHANGED", snapshot.record.path
        )
    return current


def _validate_xml_text(value: str, *, code: str) -> None:
    for character in value:
        point = ord(character)
        if not (
            point in (0x09, 0x0A, 0x0D)
            or 0x20 <= point <= 0xD7FF
            or 0xE000 <= point <= 0xFFFD
            or 0x10000 <= point <= 0x10FFFF
        ):
            raise CohortFlowValidationError(code)


def _validate_svg_payload(payload: bytes) -> None:
    lowered = payload.lower()
    if any(
        forbidden in lowered
        for forbidden in (
            b"<!doctype",
            b"<!entity",
            b"<script",
            b"<foreignobject",
            b"javascript:",
            b"data:",
        )
    ):
        raise CohortFlowValidationError("SVG_UNSAFE_OUTPUT")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CohortFlowValidationError("SVG_XML_INVALID") from exc
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise CohortFlowValidationError("SVG_ROOT_INVALID")
    for element in root.iter():
        local_tag = element.tag.rsplit("}", 1)[-1].casefold()
        if local_tag in {"script", "foreignobject", "style"}:
            raise CohortFlowValidationError("SVG_UNSAFE_OUTPUT")
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
                raise CohortFlowValidationError("SVG_EXTERNAL_REFERENCE")


def _parse_record(value: Any, *, code: str, output: bool = False) -> FileRecord:
    mapping = _require_mapping(value, code)
    _require_exact_keys(mapping, {"path", "sha256"}, code)
    path = _safe_relative_path(mapping["path"], code=code, output=output)
    digest = mapping["sha256"]
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise CohortFlowValidationError(code, "invalid sha256")
    return FileRecord(path=path, sha256=digest, size_bytes=-1)


def load_render_config(
    config_path: Path,
    *,
    workspace_root: Path,
    namespace: RenderNamespace = CANONICAL_NAMESPACE,
) -> RenderConfig:
    """Load and structurally validate only the YAML configuration."""

    root = workspace_root.resolve(strict=True)
    namespace_paths = {
        namespace.config_path,
        *namespace.source_paths.values(),
        namespace.renderer_path,
        namespace.cli_path,
        namespace.output_svg,
        namespace.receipt,
        namespace.lock_path,
    }
    if (
        set(namespace.source_paths) != {"source_mmd", *SOURCE_KEYS}
        or len(namespace_paths) != 11
        or (namespace.production and namespace != CANONICAL_NAMESPACE)
        or (
            namespace.config_path == CANONICAL_CONFIG_PATH
            and namespace != CANONICAL_NAMESPACE
        )
    ):
        raise CohortFlowValidationError("NAMESPACE_INVALID")
    for value in namespace_paths:
        _safe_relative_path(value, code="NAMESPACE_PATH_INVALID")
    configured_relative: str
    if config_path.is_absolute():
        try:
            configured_relative = config_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise CohortFlowValidationError("CONFIG_OUTSIDE_WORKSPACE") from exc
    else:
        configured_relative = _safe_relative_path(
            config_path.as_posix(),
            code="CONFIG_PATH_INVALID",
        )
    if configured_relative != namespace.config_path:
        raise CohortFlowValidationError("CONFIG_PATH_NOT_CANONICAL")
    config_snapshot = _read_stable_path(
        root,
        namespace.config_path,
        expected_sha256=None,
        code="CONFIG",
    )
    try:
        raw = yaml.load(
            config_snapshot.payload.decode("utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except (UnicodeError, yaml.YAMLError) as exc:
        raise CohortFlowValidationError("CONFIG_READ_FAILED") from exc
    config = _require_mapping(raw, "CONFIG_NOT_MAPPING")
    _require_exact_keys(
        config,
        {
            "schema_version",
            "title",
            "description",
            "canvas",
            "source_mmd",
            "sources",
            "implementation",
            "output_svg",
            "receipt",
        },
        "CONFIG_KEYS_INVALID",
    )
    if config["schema_version"] != CONFIG_SCHEMA:
        raise CohortFlowValidationError("CONFIG_SCHEMA_INVALID")
    title = config["title"]
    description = config["description"]
    if not isinstance(title, str) or title != title.strip() or title != namespace.title:
        raise CohortFlowValidationError("CONFIG_TITLE_INVALID")
    if (
        not isinstance(description, str)
        or description != description.strip()
        or description != namespace.description
    ):
        raise CohortFlowValidationError("CONFIG_DESCRIPTION_INVALID")
    _validate_xml_text(title, code="CONFIG_TITLE_XML_INVALID")
    _validate_xml_text(description, code="CONFIG_DESCRIPTION_XML_INVALID")
    canvas = _require_mapping(config["canvas"], "CONFIG_CANVAS_INVALID")
    _require_exact_keys(canvas, {"width", "height"}, "CONFIG_CANVAS_INVALID")
    width, height = canvas["width"], canvas["height"]
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or (width, height) != (1440, 900)
    ):
        raise CohortFlowValidationError("CONFIG_CANVAS_INVALID")
    source_mmd = _parse_record(config["source_mmd"], code="CONFIG_MMD_INVALID")
    if source_mmd.path != namespace.source_paths["source_mmd"]:
        raise CohortFlowValidationError("CONFIG_MMD_PATH_INVALID")
    sources_raw = _require_mapping(config["sources"], "CONFIG_SOURCES_INVALID")
    if tuple(sources_raw) != SOURCE_KEYS:
        raise CohortFlowValidationError("CONFIG_SOURCE_ORDER_INVALID")
    sources = {
        key: _parse_record(
            sources_raw[key], code=f"CONFIG_SOURCE_{key.upper()}_INVALID"
        )
        for key in SOURCE_KEYS
    }
    if any(sources[key].path != namespace.source_paths[key] for key in SOURCE_KEYS):
        raise CohortFlowValidationError("CONFIG_SOURCE_PATH_INVALID")
    implementation_raw = _require_mapping(
        config["implementation"],
        "CONFIG_IMPLEMENTATION_INVALID",
    )
    if tuple(implementation_raw) != ("renderer", "cli"):
        raise CohortFlowValidationError("CONFIG_IMPLEMENTATION_ORDER_INVALID")
    implementation = {
        "renderer": _parse_record(
            implementation_raw["renderer"],
            code="CONFIG_RENDERER_INVALID",
        ),
        "cli": _parse_record(
            implementation_raw["cli"],
            code="CONFIG_CLI_INVALID",
        ),
    }
    if (
        implementation["renderer"].path != namespace.renderer_path
        or implementation["cli"].path != namespace.cli_path
    ):
        raise CohortFlowValidationError("CONFIG_IMPLEMENTATION_PATH_INVALID")
    output_svg = _safe_relative_path(
        config["output_svg"],
        code="CONFIG_OUTPUT_INVALID",
        output=True,
    )
    receipt = _safe_relative_path(
        config["receipt"],
        code="CONFIG_RECEIPT_INVALID",
        output=True,
    )
    if not output_svg.endswith(".svg") or not receipt.endswith(".json"):
        raise CohortFlowValidationError("CONFIG_OUTPUT_SUFFIX_INVALID")
    if output_svg != namespace.output_svg or receipt != namespace.receipt:
        raise CohortFlowValidationError("CONFIG_OUTPUT_PATH_INVALID")
    return RenderConfig(
        workspace_root=root,
        config_path=config_snapshot.absolute_path,
        namespace=namespace,
        config_snapshot=config_snapshot,
        title=title,
        description=description,
        width=width,
        height=height,
        source_mmd=source_mmd,
        sources=sources,
        implementation=implementation,
        output_svg=output_svg,
        receipt=receipt,
    )


def config_summary(config: RenderConfig) -> dict[str, Any]:
    """Return a no-source-read summary used by ``--config-only``."""

    return {
        "schema_version": CONFIG_SCHEMA,
        "status": "config_valid",
        "source_count": len(config.sources) + 1,
        "source_reads": 0,
        "output_writes": 0,
        "output_svg": config.output_svg,
        "receipt": config.receipt,
        "canvas": {"width": config.width, "height": config.height},
    }


def _assert_safe_existing_file(root: Path, relative: str) -> Path:
    return _read_stable_path(
        root,
        relative,
        expected_sha256=None,
        code="SOURCE",
    ).absolute_path


def _read_stable_source(root: Path, record: FileRecord) -> tuple[bytes, FileRecord]:
    snapshot = _read_stable_path(
        root,
        record.path,
        expected_sha256=record.sha256,
        code="SOURCE",
    )
    return snapshot.payload, snapshot.record


def _parse_label(value: str) -> tuple[tuple[LabelSpan, ...], ...]:
    _validate_xml_text(value, code="MMD_LABEL_XML_INVALID")
    if any(token in value.lower() for token in ("<script", "<style", "<img", "href=")):
        raise CohortFlowValidationError("MMD_LABEL_UNSAFE")
    pieces = re.split(r"(<br\s*/>|</?b>)", value, flags=re.IGNORECASE)
    lines: list[list[LabelSpan]] = [[]]
    bold = False
    for piece in pieces:
        if not piece:
            continue
        lowered = piece.lower()
        if re.fullmatch(r"<br\s*/>", lowered):
            if not lines[-1]:
                raise CohortFlowValidationError("MMD_LABEL_EMPTY_LINE")
            lines.append([])
        elif lowered == "<b>":
            if bold:
                raise CohortFlowValidationError("MMD_LABEL_NESTED_BOLD")
            bold = True
        elif lowered == "</b>":
            if not bold:
                raise CohortFlowValidationError("MMD_LABEL_UNBALANCED_BOLD")
            bold = False
        else:
            if "<" in piece or ">" in piece or "&" in piece:
                raise CohortFlowValidationError("MMD_LABEL_MARKUP_INVALID")
            lines[-1].append(LabelSpan(piece, bold))
    if bold or any(not line for line in lines):
        raise CohortFlowValidationError("MMD_LABEL_UNBALANCED")
    return tuple(tuple(line) for line in lines)


def parse_mermaid(payload: bytes) -> Diagram:
    """Parse and strictly validate the canonical Mermaid subset."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CohortFlowValidationError("MMD_UTF8_INVALID") from exc
    if "\r" in text or not text.endswith("\n"):
        raise CohortFlowValidationError("MMD_NEWLINE_INVALID")
    lines = text.splitlines()
    if not lines or lines[0] != "flowchart TD":
        raise CohortFlowValidationError("MMD_HEADER_INVALID")
    nodes: dict[str, DiagramNode] = {}
    edges: list[tuple[str, str, str | None, bool]] = []
    styles: dict[str, tuple[str, str, str]] = {}
    class_members: dict[str, tuple[str, ...]] = {}
    lineage: dict[str, str] = {}
    pending_lineage_label: str | None = None

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("%%"):
            comment = stripped[2:].strip()
            label_match = re.fullmatch(r"(.+) SHA-256:", comment)
            if label_match:
                pending_lineage_label = label_match.group(1)
                continue
            if SHA256_RE.fullmatch(comment):
                if pending_lineage_label is None:
                    raise CohortFlowValidationError("MMD_LINEAGE_ORPHAN_HASH")
                if pending_lineage_label not in LINEAGE_LABELS:
                    raise CohortFlowValidationError(
                        "MMD_LINEAGE_LABEL_INVALID",
                        pending_lineage_label,
                    )
                key = LINEAGE_LABELS[pending_lineage_label]
                if key in lineage:
                    raise CohortFlowValidationError("MMD_LINEAGE_DUPLICATE", key)
                lineage[key] = comment
                pending_lineage_label = None
                continue
            if comment == "Source lineage (aggregate-only; no row identifiers):":
                continue
            raise CohortFlowValidationError("MMD_COMMENT_INVALID", comment)

        node_match = NODE_RE.fullmatch(line)
        if node_match:
            node_id, opener, label, closer = node_match.groups()
            expected_closer = "]" if opener == "[" else "}"
            if closer != expected_closer or node_id in nodes:
                raise CohortFlowValidationError("MMD_NODE_INVALID", node_id)
            nodes[node_id] = DiagramNode(
                node_id=node_id,
                shape="rectangle" if opener == "[" else "diamond",
                lines=_parse_label(label),
            )
            continue

        solid_match = SOLID_EDGE_RE.fullmatch(line)
        if solid_match:
            source, label, target = solid_match.groups()
            edges.append((source, target, label, False))
            continue
        dotted_match = DOTTED_EDGE_RE.fullmatch(line)
        if dotted_match:
            source, label, target = dotted_match.groups()
            edges.append((source, target, label, True))
            continue

        class_def_match = CLASS_DEF_RE.fullmatch(line)
        if class_def_match:
            name, fill, stroke, color = class_def_match.groups()
            if name in styles:
                raise CohortFlowValidationError("MMD_STYLE_DUPLICATE", name)
            styles[name] = (fill, stroke, color)
            continue
        class_member_match = CLASS_MEMBER_RE.fullmatch(line)
        if class_member_match:
            members, name = class_member_match.groups()
            if name in class_members:
                raise CohortFlowValidationError("MMD_CLASS_DUPLICATE", name)
            class_members[name] = tuple(members.split(","))
            continue
        raise CohortFlowValidationError("MMD_SYNTAX_UNSUPPORTED", stripped[:80])

    if pending_lineage_label is not None:
        raise CohortFlowValidationError("MMD_LINEAGE_MISSING_HASH")
    if tuple(nodes) != EXPECTED_NODE_IDS:
        raise CohortFlowValidationError("MMD_NODE_ORDER_INVALID")
    if tuple(edges) != EXPECTED_EDGES:
        raise CohortFlowValidationError("MMD_EDGE_SET_INVALID")
    if styles != EXPECTED_STYLES:
        raise CohortFlowValidationError("MMD_STYLE_SET_INVALID")
    if class_members != EXPECTED_CLASS_MEMBERS:
        raise CohortFlowValidationError("MMD_CLASS_SET_INVALID")
    if set(lineage) != set(SOURCE_KEYS):
        raise CohortFlowValidationError("MMD_LINEAGE_SET_INVALID")
    for source, target, _, _ in edges:
        if source not in nodes or target not in nodes:
            raise CohortFlowValidationError("MMD_EDGE_NODE_MISSING")
    if set(LAYOUT) != set(nodes):
        raise CohortFlowValidationError("MMD_LAYOUT_NODE_MISMATCH")
    return Diagram(
        nodes=nodes,
        edges=tuple(edges),
        styles=styles,
        class_members=class_members,
        lineage=lineage,
    )


def _decode_json(payload: bytes, code: str) -> Mapping[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CohortFlowValidationError(f"{code}_DUPLICATE_KEY", key)
            result[key] = value
        return result

    def reject_constant(value: str) -> NoReturn:
        raise CohortFlowValidationError(f"{code}_NON_FINITE", value)

    try:
        value = json.loads(
            payload,
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CohortFlowValidationError(code) from exc
    return _require_mapping(value, code)


def _integer(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CohortFlowValidationError(code)
    return value


def _sha_value(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CohortFlowValidationError(code)
    return value


def _number(value: Any, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CohortFlowValidationError(code)
    converted = float(value)
    if not converted >= 0.0 or not converted < float("inf"):
        raise CohortFlowValidationError(code)
    return converted


def _rate(value: Any, code: str) -> float:
    converted = _number(value, code)
    if converted > 1.0:
        raise CohortFlowValidationError(code)
    return converted


def _nested(mapping: Mapping[str, Any], *keys: str, code: str) -> Any:
    current: Any = mapping
    for key in keys:
        current_mapping = _require_mapping(current, code)
        if key not in current_mapping:
            raise CohortFlowValidationError(code)
        current = current_mapping[key]
    return current


def _format_percent(value: float, digits: int = 7) -> str:
    if value == 0.0:
        return "0%"
    return f"{value * 100:.{digits}f}%"


def _expected_plain_labels(
    data_card: Mapping[str, Any],
    g0: Mapping[str, Any],
    prompt: Mapping[str, Any],
    filter_report: Mapping[str, Any],
    *,
    source_records: Mapping[str, FileRecord],
) -> Mapping[str, tuple[str, ...]]:
    if data_card.get("schema_version") != "aihub71490.data_card.v1":
        raise CohortFlowValidationError("DATA_CARD_SCHEMA_INVALID")
    if g0.get("schema_version") != "aihub71490.g0.audit.v1":
        raise CohortFlowValidationError("G0_SCHEMA_INVALID")
    if prompt.get("schema_version") != "voice_coach.prompt_challenge_reservation.v1":
        raise CohortFlowValidationError("PROMPT_SCHEMA_INVALID")
    if (
        filter_report.get("schema_version")
        != "voice_coach.explicit_feature_cache_report.v1"
    ):
        raise CohortFlowValidationError("FILTER_SCHEMA_INVALID")
    if filter_report.get("status") != "completed":
        raise CohortFlowValidationError("FILTER_STATUS_INVALID")
    if g0.get("automated_status") != "passed":
        raise CohortFlowValidationError("G0_AUTOMATED_STATUS_INVALID")
    manual = _require_mapping(
        g0.get("manual_alignment_audit"),
        "G0_MANUAL_AUDIT_INVALID",
    )
    if (
        manual.get("status") != "pending"
        or _integer(manual.get("required_samples"), "G0_MANUAL_AUDIT_INVALID") != 240
        or _number(manual.get("minimum_accuracy"), "G0_MANUAL_AUDIT_INVALID") != 0.95
    ):
        raise CohortFlowValidationError("G0_MANUAL_AUDIT_STATE_CHANGED")

    total = _integer(data_card.get("record_count"), "DATA_CARD_COUNT_INVALID")
    g0_total = _integer(g0.get("record_count"), "G0_COUNT_INVALID")
    source_rows = _integer(
        _nested(
            filter_report,
            "progress",
            "source_rows_scanned",
            code="FILTER_COUNTS_INVALID",
        ),
        "FILTER_COUNTS_INVALID",
    )
    prompt_rows = _integer(
        _nested(
            prompt,
            "source",
            "row_count",
            code="PROMPT_COUNTS_INVALID",
        ),
        "PROMPT_COUNTS_INVALID",
    )
    if len({total, g0_total, source_rows, prompt_rows}) != 1:
        raise CohortFlowValidationError("SOURCE_TOTAL_DISAGREEMENT")

    split_counts = _require_mapping(
        _nested(data_card, "split_counts", code="DATA_CARD_SPLITS_INVALID"),
        "DATA_CARD_SPLITS_INVALID",
    )
    expected_split_keys = {"train", "dev", "stack", "calibration", "locked_test"}
    if set(split_counts) != expected_split_keys:
        raise CohortFlowValidationError("DATA_CARD_SPLITS_INVALID")
    stack = _integer(split_counts.get("stack"), "DATA_CARD_SPLITS_INVALID")
    calibration = _integer(
        split_counts.get("calibration"),
        "DATA_CARD_SPLITS_INVALID",
    )
    locked = _integer(split_counts.get("locked_test"), "DATA_CARD_SPLITS_INVALID")
    source_train = _integer(split_counts.get("train"), "DATA_CARD_SPLITS_INVALID")
    source_dev = _integer(split_counts.get("dev"), "DATA_CARD_SPLITS_INVALID")
    evaluation = stack + calibration + locked
    candidate = source_train + source_dev
    if evaluation + candidate != total:
        raise CohortFlowValidationError("DATA_CARD_SPLIT_SUM_INVALID")

    g0_splits = _require_mapping(
        g0.get("split_counts"),
        "G0_SPLITS_INVALID",
    )
    if set(g0_splits) != expected_split_keys or {
        key: _integer(g0_splits.get(key), "G0_SPLITS_INVALID")
        for key in expected_split_keys
    } != {
        key: _integer(split_counts.get(key), "DATA_CARD_SPLITS_INVALID")
        for key in expected_split_keys
    }:
        raise CohortFlowValidationError("G0_SPLIT_DISAGREEMENT")
    data_split_sha = _sha_value(
        data_card.get("split_assignment_sha256"),
        "DATA_CARD_SPLIT_SHA_INVALID",
    )
    if (
        _sha_value(g0.get("split_assignment_sha256"), "G0_SPLIT_SHA_INVALID")
        != data_split_sha
    ):
        raise CohortFlowValidationError("G0_SPLIT_SHA_DISAGREEMENT")

    modeling_sha = _sha_value(
        data_card.get("manifest_sha256"),
        "DATA_CARD_MANIFEST_SHA_INVALID",
    )
    prompt_source = _require_mapping(
        prompt.get("source"),
        "PROMPT_SOURCE_INVALID",
    )
    filter_source = _require_mapping(
        filter_report.get("source"),
        "FILTER_SOURCE_INVALID",
    )
    if (
        _sha_value(
            prompt_source.get("modeling_manifest_sha256"),
            "PROMPT_MODELING_SHA_INVALID",
        )
        != modeling_sha
        or _sha_value(
            filter_source.get("modeling_manifest_sha256"),
            "FILTER_MODELING_SHA_INVALID",
        )
        != modeling_sha
        or _sha_value(
            filter_source.get("prompt_reservation_sha256"),
            "FILTER_PROMPT_SHA_INVALID",
        )
        != source_records["prompt_reservation"].sha256
    ):
        raise CohortFlowValidationError("SOURCE_LINEAGE_DISAGREEMENT")

    exclusions = _require_mapping(
        _nested(
            filter_report,
            "progress",
            "exclusions",
            code="FILTER_COUNTS_INVALID",
        ),
        "FILTER_COUNTS_INVALID",
    )
    excluded_split = _integer(
        exclusions.get("split_not_allowed"),
        "FILTER_COUNTS_INVALID",
    )
    excluded_qc = _integer(
        exclusions.get("qc_flags_present"),
        "FILTER_COUNTS_INVALID",
    )
    excluded_prompt = _integer(
        exclusions.get("reserved_prompt"),
        "FILTER_COUNTS_INVALID",
    )
    if excluded_split != evaluation:
        raise CohortFlowValidationError("FILTER_SPLIT_EXCLUSION_DISAGREEMENT")
    qc_pass = candidate - excluded_qc
    final = qc_pass - excluded_prompt
    included = _require_mapping(
        _nested(
            filter_report,
            "progress",
            "included_rows_by_split",
            code="FILTER_COUNTS_INVALID",
        ),
        "FILTER_COUNTS_INVALID",
    )
    train = _integer(included.get("train"), "FILTER_COUNTS_INVALID")
    dev = _integer(included.get("dev"), "FILTER_COUNTS_INVALID")
    written = _integer(
        _nested(
            filter_report,
            "progress",
            "utterance_rows_written",
            code="FILTER_COUNTS_INVALID",
        ),
        "FILTER_COUNTS_INVALID",
    )
    if train + dev != final or written != final:
        raise CohortFlowValidationError("FILTER_FINAL_SUM_INVALID")
    if excluded_split + excluded_qc + excluded_prompt + written != total:
        raise CohortFlowValidationError("FILTER_SEQUENTIAL_SUM_INVALID")
    if (
        _nested(
            filter_report,
            "progress",
            "source_scan_complete",
            code="FILTER_COUNTS_INVALID",
        )
        is not True
    ):
        raise CohortFlowValidationError("FILTER_SCAN_INCOMPLETE")

    prompt_split_counts = _require_mapping(
        _nested(
            prompt,
            "counts",
            "source_rows_by_split",
            code="PROMPT_COUNTS_INVALID",
        ),
        "PROMPT_COUNTS_INVALID",
    )
    filter_split_counts = _require_mapping(
        _nested(
            filter_report,
            "progress",
            "source_rows_by_split",
            code="FILTER_COUNTS_INVALID",
        ),
        "FILTER_COUNTS_INVALID",
    )
    expected_source_splits = {
        key: _integer(value, "DATA_CARD_SPLITS_INVALID")
        for key, value in split_counts.items()
    }
    if (
        set(prompt_split_counts) != expected_split_keys
        or set(filter_split_counts) != expected_split_keys
        or {
            key: _integer(prompt_split_counts.get(key), "PROMPT_COUNTS_INVALID")
            for key in expected_split_keys
        }
        != expected_source_splits
        or {
            key: _integer(filter_split_counts.get(key), "FILTER_COUNTS_INVALID")
            for key in expected_split_keys
        }
        != expected_source_splits
    ):
        raise CohortFlowValidationError("PROMPT_SPLIT_DISAGREEMENT")

    prompt_counts = _require_mapping(
        prompt.get("counts"),
        "PROMPT_COUNTS_INVALID",
    )
    reserved_by_split = _require_mapping(
        prompt_counts.get("reserved_rows_by_source_split"),
        "PROMPT_RESERVED_COUNTS_INVALID",
    )
    if set(reserved_by_split) != expected_split_keys:
        raise CohortFlowValidationError("PROMPT_RESERVED_COUNTS_INVALID")
    reserved = {
        key: _integer(
            reserved_by_split.get(key),
            "PROMPT_RESERVED_COUNTS_INVALID",
        )
        for key in expected_split_keys
    }
    if any(reserved[key] > expected_source_splits[key] for key in expected_split_keys):
        raise CohortFlowValidationError("PROMPT_RESERVED_COUNTS_INVALID")
    reserved_train_dev = reserved["train"] + reserved["dev"]
    reserved_lower_bound = max(0, reserved_train_dev - excluded_qc)
    if not reserved_lower_bound <= excluded_prompt <= reserved_train_dev:
        raise CohortFlowValidationError("PROMPT_FILTER_RESERVED_BOUNDS_INVALID")
    dispositions = _require_mapping(
        prompt_counts.get("row_dispositions"),
        "PROMPT_DISPOSITIONS_INVALID",
    )
    disposition_values = {
        key: _integer(dispositions.get(key), "PROMPT_DISPOSITIONS_INVALID")
        for key in (
            "prompt_challenge",
            "exclude_from_training_and_tuning",
            "preserve_source_split",
        )
    }
    if sum(disposition_values.values()) != total or disposition_values[
        "prompt_challenge"
    ] + disposition_values["exclude_from_training_and_tuning"] != sum(
        reserved.values()
    ):
        raise CohortFlowValidationError("PROMPT_DISPOSITIONS_INVALID")

    pairing = _rate(g0.get("pairing_rate"), "G0_METRIC_INVALID")
    decoder = _rate(g0.get("decoder_failure_rate"), "G0_METRIC_INVALID")
    time_bound = _rate(g0.get("time_bound_error_rate"), "G0_METRIC_INVALID")
    thresholds = _require_mapping(g0.get("thresholds"), "G0_THRESHOLDS_INVALID")
    expected_thresholds = {
        "expected_records": total,
        "min_pairing_rate": 0.99,
        "max_decoder_failure_rate": 0.001,
        "max_time_bound_error_rate": 0.01,
    }
    actual_thresholds = {
        "expected_records": _integer(
            thresholds.get("expected_records"),
            "G0_THRESHOLDS_INVALID",
        ),
        "min_pairing_rate": _rate(
            thresholds.get("min_pairing_rate"),
            "G0_THRESHOLDS_INVALID",
        ),
        "max_decoder_failure_rate": _rate(
            thresholds.get("max_decoder_failure_rate"),
            "G0_THRESHOLDS_INVALID",
        ),
        "max_time_bound_error_rate": _rate(
            thresholds.get("max_time_bound_error_rate"),
            "G0_THRESHOLDS_INVALID",
        ),
    }
    if (
        actual_thresholds != expected_thresholds
        or pairing < actual_thresholds["min_pairing_rate"]
        or decoder > actual_thresholds["max_decoder_failure_rate"]
        or time_bound > actual_thresholds["max_time_bound_error_rate"]
        or _integer(
            g0.get("annotation_parse_failures"),
            "G0_ANNOTATION_FAILURES_INVALID",
        )
        != 0
        or g0.get("failed_automated_gates") != []
        or g0.get("overall_g0_status") != "pending_manual_alignment_audit"
    ):
        raise CohortFlowValidationError("G0_GATE_RECOMPUTATION_FAILED")
    speaker_overlap = g0.get("cross_split_speaker_overlap_examples")
    pcm_overlap = g0.get("cross_split_pcm_duplicate_examples")
    if (
        not isinstance(speaker_overlap, list)
        or not isinstance(pcm_overlap, list)
        or speaker_overlap
        or pcm_overlap
    ):
        raise CohortFlowValidationError("G0_OVERLAP_INVALID")
    _sha_value(g0.get("parquet_sha256"), "G0_PARQUET_SHA_INVALID")
    if (
        _integer(g0.get("speaker_count"), "G0_SUMMARY_INVALID")
        != _integer(data_card.get("speaker_count"), "DATA_CARD_SUMMARY_INVALID")
        or _integer(g0.get("unique_pcm_count"), "G0_SUMMARY_INVALID")
        != _integer(data_card.get("unique_pcm_count"), "DATA_CARD_SUMMARY_INVALID")
        or g0.get("qc_flag_counts") != data_card.get("qc_flag_counts")
        or g0.get("phone_alignment_operations")
        != data_card.get("phone_alignment_operations")
    ):
        raise CohortFlowValidationError("G0_DATA_CARD_DISAGREEMENT")
    selection = _require_mapping(
        prompt.get("selection"),
        "PROMPT_SELECTION_INVALID",
    )
    reserved_prompts = prompt.get("reserved_prompts")
    if (
        not isinstance(reserved_prompts, list)
        or _integer(
            selection.get("selected_prompt_count"),
            "PROMPT_SELECTION_INVALID",
        )
        != len(reserved_prompts)
        or _integer(
            selection.get("requested_prompt_count"),
            "PROMPT_SELECTION_INVALID",
        )
        != len(reserved_prompts)
        or _integer(
            selection.get("locked_test_candidate_prompt_count"),
            "PROMPT_SELECTION_INVALID",
        )
        != _integer(data_card.get("prompt_count"), "DATA_CARD_PROMPT_INVALID")
    ):
        raise CohortFlowValidationError("PROMPT_SELECTION_INVALID")
    unknown = _integer(
        data_card.get("unknown_error_tag_occurrences"),
        "DATA_CARD_ONTOLOGY_INVALID",
    )
    outputs = _require_mapping(
        filter_report.get("outputs"),
        "FILTER_OUTPUT_INVALID",
    )
    utterance_output = _require_mapping(
        outputs.get("utterance"),
        "FILTER_OUTPUT_INVALID",
    )
    phone_output = _require_mapping(
        outputs.get("phone"),
        "FILTER_OUTPUT_INVALID",
    )
    if (
        _integer(utterance_output.get("row_count"), "FILTER_OUTPUT_INVALID") != written
        or utterance_output.get("role") != "canonical"
        or SHA256_RE.fullmatch(str(utterance_output.get("sha256", ""))) is None
        or phone_output.get("enabled") is not False
        or _integer(phone_output.get("row_count"), "FILTER_OUTPUT_INVALID") != 0
    ):
        raise CohortFlowValidationError("FILTER_OUTPUT_INVALID")
    return {
        "A": ("전체 source metadata scan", f"{total:,}"),
        "S": ("고정 split gate",),
        "X1": (
            "평가 split 제외",
            f"{evaluation:,}",
            (f"stack {stack:,} + calibration {calibration:,} + locked-test {locked:,}"),
        ),
        "B": ("train/dev 후보", f"{candidate:,}"),
        "Q": ("QC flag gate",),
        "X2": ("QC 제외", f"{excluded_qc:,}"),
        "C": ("QC 통과", f"{qc_pass:,}"),
        "R": ("예약 prompt gate",),
        "X3": ("예약 prompt 제외", f"{excluded_prompt:,}"),
        "D": ("최종 train/dev cache", f"{final:,}"),
        "T": ("train", f"{train:,}"),
        "V": ("dev", f"{dev:,}"),
        "G": ("자동 G0 aggregate",),
        "G1": (
            f"pairing {_format_percent(pairing)}",
            f"decoder failure {_format_percent(decoder)}",
            f"time-bound error {_format_percent(time_bound)}",
        ),
        "G2": (
            f"split speaker overlap {len(speaker_overlap):,}",
            f"PCM duplicate {len(pcm_overlap):,}",
            f"ontology 미매핑 {unknown:,}",
        ),
        "H": ("외부 전문가 정렬 G0", "PENDING", "자동 G0와 별도"),
    }


def _validate_sources(
    config: RenderConfig,
) -> ValidatedSources:
    mmd_snapshot = _read_stable_path(
        config.workspace_root,
        config.source_mmd.path,
        expected_sha256=config.source_mmd.sha256,
        code="SOURCE_MMD",
    )
    diagram = parse_mermaid(mmd_snapshot.payload)
    snapshots: dict[str, FileSnapshot] = {"source_mmd": mmd_snapshot}
    decoded: dict[str, Mapping[str, Any]] = {}
    for key in SOURCE_KEYS:
        snapshot = _read_stable_path(
            config.workspace_root,
            config.sources[key].path,
            expected_sha256=config.sources[key].sha256,
            code=f"SOURCE_{key.upper()}",
        )
        snapshots[key] = snapshot
        decoded[key] = _decode_json(
            snapshot.payload,
            f"{key.upper()}_JSON_INVALID",
        )
        if diagram.lineage[key] != snapshot.record.sha256:
            raise CohortFlowValidationError("MMD_LINEAGE_HASH_MISMATCH", key)
    expected = _expected_plain_labels(
        decoded["data_card"],
        decoded["g0_audit"],
        decoded["prompt_reservation"],
        decoded["filter_report"],
        source_records={key: value.record for key, value in snapshots.items()},
    )
    for node_id in EXPECTED_NODE_IDS:
        if diagram.nodes[node_id].plain_lines != expected[node_id]:
            raise CohortFlowValidationError(
                "MMD_LABEL_STALE",
                f"{node_id}: expected={expected[node_id]!r} "
                f"actual={diagram.nodes[node_id].plain_lines!r}",
            )
    return ValidatedSources(
        diagram=diagram,
        snapshots=snapshots,
        decoded=decoded,
    )


def _node_class(diagram: Diagram, node_id: str) -> str:
    matches = [
        class_name
        for class_name, members in diagram.class_members.items()
        if node_id in members
    ]
    if len(matches) != 1:
        raise CohortFlowValidationError("MMD_NODE_CLASS_INVALID", node_id)
    return matches[0]


def _render_text_line(
    spans: Sequence[LabelSpan],
    *,
    x: int,
    y: float,
    color: str,
) -> str:
    children = "".join(
        (
            f'<tspan font-weight="{"700" if span.bold else "400"}">'
            f"{html.escape(span.text)}</tspan>"
        )
        for span in spans
    )
    return (
        f'      <text x="{x}" y="{y:.1f}" text-anchor="middle" '
        f'dominant-baseline="middle" fill="{color}" font-size="15">'
        f"{children}</text>"
    )


def _render_node(diagram: Diagram, node_id: str) -> list[str]:
    node = diagram.nodes[node_id]
    layout = LAYOUT[node_id]
    class_name = _node_class(diagram, node_id)
    fill, stroke, color = diagram.styles[class_name]
    plain_label = " / ".join(node.plain_lines)
    result = [
        (
            f'    <g id="node-{node_id}" role="img" '
            f'aria-label="{html.escape(plain_label, quote=True)}">'
        ),
        f"      <title>{html.escape(plain_label)}</title>",
    ]
    if node.shape == "diamond":
        half_width = layout.width // 2
        half_height = layout.height // 2
        points = (
            f"{layout.x},{layout.y - half_height} "
            f"{layout.x + half_width},{layout.y} "
            f"{layout.x},{layout.y + half_height} "
            f"{layout.x - half_width},{layout.y}"
        )
        result.append(
            f'      <polygon points="{points}" fill="{fill}" stroke="{stroke}" '
            'stroke-width="2"/>'
        )
    else:
        result.append(
            (
                f'      <rect x="{layout.x - layout.width // 2}" '
                f'y="{layout.y - layout.height // 2}" width="{layout.width}" '
                f'height="{layout.height}" rx="10" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="2"/>'
            )
        )
    line_height = 21
    start_y = layout.y - (len(node.lines) - 1) * line_height / 2
    for index, spans in enumerate(node.lines):
        result.append(
            _render_text_line(
                spans,
                x=layout.x,
                y=start_y + index * line_height,
                color=color,
            )
        )
    result.append("    </g>")
    return result


def render_svg(
    config: RenderConfig,
    diagram: Diagram,
    source_records: Mapping[str, FileRecord],
) -> bytes:
    """Render canonical SVG bytes from a validated diagram."""

    edge_lookup = {(source, target): path for source, target, path in EDGE_PATHS}
    if set(edge_lookup) != {(source, target) for source, target, _, _ in diagram.edges}:
        raise CohortFlowValidationError("EDGE_LAYOUT_MISMATCH")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{config.width}" '
            f'height="{config.height}" viewBox="0 0 {config.width} {config.height}" '
            'role="img" aria-labelledby="diagram-title diagram-description" '
            'font-family="Noto Sans CJK KR, Noto Sans KR, Malgun Gothic, sans-serif">'
        ),
        f'  <title id="diagram-title">{html.escape(config.title)}</title>',
        (f'  <desc id="diagram-description">{html.escape(config.description)}</desc>'),
        "  <metadata>",
        (
            "    "
            + html.escape(
                json.dumps(
                    {
                        "schema_version": RECEIPT_SCHEMA,
                        "aggregate_only": True,
                        "pending_manual_alignment": True,
                        "source_sha256": {
                            key: record.sha256
                            for key, record in sorted(source_records.items())
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        ),
        "  </metadata>",
        "  <defs>",
        (
            '    <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" '
            'refY="4" orient="auto" markerUnits="strokeWidth">'
        ),
        '      <path d="M 0 0 L 8 4 L 0 8 z" fill="#425466"/>',
        "    </marker>",
        "  </defs>",
        '  <rect width="1440" height="900" fill="#ffffff"/>',
        (
            '  <text x="720" y="31" text-anchor="middle" fill="#14213d" '
            'font-size="22" font-weight="700">'
            f"{html.escape(config.title)}</text>"
        ),
        (
            '  <g id="edges" fill="none" stroke="#425466" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
        ),
    ]
    for source, target, label, dotted in diagram.edges:
        dash = ' stroke-dasharray="7 6"' if dotted else ""
        lines.append(
            (
                f'    <path id="edge-{source}-{target}" '
                f'd="{edge_lookup[(source, target)]}"{dash} marker-end="url(#arrow)"/>'
            )
        )
        if label is not None:
            label_x, label_y = EDGE_LABELS[(source, target)]
            lines.append(
                (
                    f'    <text x="{label_x}" y="{label_y}" '
                    'text-anchor="middle" fill="#334155" stroke="none" '
                    f'font-size="13">{html.escape(label)}</text>'
                )
            )
    lines.extend(("  </g>", '  <g id="nodes">'))
    for node_id in EXPECTED_NODE_IDS:
        lines.extend(_render_node(diagram, node_id))
    lines.extend(
        (
            "  </g>",
            (
                '  <text x="20" y="883" fill="#64748b" font-size="11">'
                "집계 전용 · 외부 전문가 정렬 감사 PENDING · locked-test 미개봉"
                "</text>"
            ),
            "</svg>",
            "",
        )
    )
    payload = "\n".join(lines).encode("utf-8")
    _validate_svg_payload(payload)
    return payload


def _config_record(config: RenderConfig) -> FileRecord:
    return config.config_snapshot.record


def capture_absolute_snapshot(
    path: Path,
    *,
    record_path: str,
    code: str,
) -> FileSnapshot:
    """Capture a stable absolute file for import/start provenance."""

    resolved = path.resolve(strict=True)
    snapshot = _read_stable_path(
        resolved.parent,
        resolved.name,
        expected_sha256=None,
        code=code,
    )
    return FileSnapshot(
        record=FileRecord(
            path=record_path,
            sha256=snapshot.record.sha256,
            size_bytes=snapshot.record.size_bytes,
        ),
        identity=snapshot.identity,
        payload=snapshot.payload,
        absolute_path=snapshot.absolute_path,
    )


def _snapshot_with_record(
    snapshot: FileSnapshot,
    record_path: str,
) -> FileSnapshot:
    return FileSnapshot(
        record=FileRecord(
            record_path,
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
    """Bind the executing renderer import and CLI start bytes to canonical pins."""

    if not config.namespace.production:
        raise CohortFlowValidationError("PRODUCTION_NAMESPACE_REQUIRED")
    renderer = _snapshot_with_record(
        _RENDERER_IMPORT_SNAPSHOT,
        config.namespace.renderer_path,
    )
    cli = _snapshot_with_record(
        cli_start_snapshot,
        config.namespace.cli_path,
    )
    if (
        renderer.absolute_path != config.workspace_root / config.namespace.renderer_path
        or cli.absolute_path != config.workspace_root / config.namespace.cli_path
    ):
        raise CohortFlowValidationError("IMPLEMENTATION_PATH_MISMATCH")
    result = ImplementationSnapshot(renderer=renderer, cli=cli, test_only=False)
    _validate_implementation_snapshot(config, result)
    return result


def capture_test_implementation_snapshot(
    config: RenderConfig,
) -> ImplementationSnapshot:
    """Explicit test-only snapshot for an isolated workspace fixture."""

    if config.namespace.production:
        raise CohortFlowValidationError("TEST_NAMESPACE_REQUIRED")
    renderer = _read_stable_path(
        config.workspace_root,
        config.namespace.renderer_path,
        expected_sha256=config.implementation["renderer"].sha256,
        code="TEST_RENDERER",
    )
    cli = _read_stable_path(
        config.workspace_root,
        config.namespace.cli_path,
        expected_sha256=config.implementation["cli"].sha256,
        code="TEST_CLI",
    )
    result = ImplementationSnapshot(renderer=renderer, cli=cli, test_only=True)
    _validate_implementation_snapshot(config, result)
    return result


def _validate_implementation_snapshot(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
) -> None:
    if config.namespace.production == implementation.test_only:
        raise CohortFlowValidationError("IMPLEMENTATION_SNAPSHOT_KIND_INVALID")
    for name, snapshot in (
        ("renderer", implementation.renderer),
        ("cli", implementation.cli),
    ):
        configured = config.implementation[name]
        if snapshot.record != FileRecord(
            configured.path,
            configured.sha256,
            snapshot.record.size_bytes,
        ):
            raise CohortFlowValidationError(
                "IMPLEMENTATION_CONFIG_MISMATCH",
                name,
            )
        _revalidate_snapshot(
            config.workspace_root,
            snapshot,
            code=f"IMPLEMENTATION_{name.upper()}",
        )


def _receipt_payload(
    config: RenderConfig,
    svg: bytes,
    source_records: Mapping[str, FileRecord],
    *,
    implementation: ImplementationSnapshot,
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "passed",
        "scope": "aggregate_only_publication_figure",
        "privacy": {
            "row_identifiers": 0,
            "speaker_identifiers": 0,
            "raw_or_restricted_reads": 0,
            "locked_test_opened": False,
        },
        "research_state": {
            "automated_g0": "passed",
            "manual_alignment_g0": "PENDING",
            "pending_removed": False,
            "research_completion_claimed": False,
        },
        "diagram": {
            "node_count": len(EXPECTED_NODE_IDS),
            "edge_count": len(EXPECTED_EDGES),
            "width": config.width,
            "height": config.height,
            "portable_svg": {
                "script_count": 0,
                "external_reference_count": 0,
                "foreign_object_count": 0,
            },
        },
        "inputs": {
            key: record.as_dict() for key, record in sorted(source_records.items())
        },
        "implementation": {
            "config": _config_record(config).as_dict(),
            "renderer": implementation.renderer.record.as_dict(),
            "cli": implementation.cli.record.as_dict(),
        },
        "output": {
            **FileRecord(
                path=config.output_svg,
                sha256=_sha256_bytes(svg),
                size_bytes=len(svg),
            ).as_dict(),
            "mode": "0644",
            "nlink": 1,
        },
        "publication": {
            "lock_path": config.namespace.lock_path,
            "lock_mode": "0600",
            "single_writer": True,
            "transactional_pair": True,
            "post_publish_deep_validation": True,
            "receipt_mode": "0440",
            "receipt_nlink": 1,
        },
    }


def _assert_safe_output_parent(root: Path, relative: str) -> Path:
    relative = _safe_relative_path(relative, code="OUTPUT_PATH_INVALID", output=True)
    path = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o755)
            except FileExistsError:
                pass
            except OSError as exc:
                raise CohortFlowValidationError(
                    "OUTPUT_PARENT_CREATE_FAILED",
                    relative,
                ) from exc
            metadata = current.lstat()
        except OSError as exc:
            raise CohortFlowValidationError("OUTPUT_PARENT_INVALID", relative) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CohortFlowValidationError("OUTPUT_PARENT_SYMLINK", relative)
    resolved_parent = path.parent.resolve(strict=True)
    if root != resolved_parent and root not in resolved_parent.parents:
        raise CohortFlowValidationError("OUTPUT_OUTSIDE_WORKSPACE", relative)
    return path


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> FileSnapshot:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".part",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_nlink != 1
    ):
        raise CohortFlowValidationError("PUBLISHED_FILE_METADATA_INVALID", str(path))
    if path.read_bytes() != payload:
        raise CohortFlowValidationError("PUBLISHED_FILE_BYTES_INVALID", str(path))
    return FileSnapshot(
        record=FileRecord(path.name, _sha256_bytes(payload), len(payload)),
        identity=_identity(metadata),
        payload=payload,
        absolute_path=path.resolve(strict=True),
    )


@contextmanager
def _publication_lock(config: RenderConfig) -> Iterator[FileSnapshot]:
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    root_descriptor = os.open(config.workspace_root, root_flags)
    try:
        root_path_metadata = config.workspace_root.lstat()
        root_descriptor_metadata = os.fstat(root_descriptor)
        if _identity(root_path_metadata) != _identity(
            root_descriptor_metadata
        ) or not stat.S_ISDIR(root_descriptor_metadata.st_mode):
            raise CohortFlowValidationError("WORKSPACE_ROOT_REBOUND")
        fcntl.flock(root_descriptor, fcntl.LOCK_EX)
        path = _assert_safe_output_parent(
            config.workspace_root,
            config.namespace.lock_path,
        )
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, LOCK_MODE)
            created = True
        except FileExistsError:
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise CohortFlowValidationError("LOCK_OPEN_FAILED") from exc
        except OSError as exc:
            raise CohortFlowValidationError("LOCK_OPEN_FAILED") from exc
        try:
            if created:
                os.fchmod(descriptor, LOCK_MODE)
                os.fsync(descriptor)
                _fsync_directory(path.parent)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            descriptor_metadata = os.fstat(descriptor)
            try:
                path_metadata = path.lstat()
            except OSError as exc:
                raise CohortFlowValidationError("LOCK_PATH_CHANGED") from exc
            if (
                _identity(descriptor_metadata) != _identity(path_metadata)
                or not stat.S_ISREG(descriptor_metadata.st_mode)
                or descriptor_metadata.st_nlink != 1
                or stat.S_IMODE(descriptor_metadata.st_mode) != LOCK_MODE
                or descriptor_metadata.st_size != 0
            ):
                raise CohortFlowValidationError("LOCK_METADATA_INVALID")
            yield FileSnapshot(
                record=FileRecord(
                    config.namespace.lock_path,
                    _sha256_bytes(b""),
                    0,
                ),
                identity=_identity(descriptor_metadata),
                payload=b"",
                absolute_path=path.resolve(strict=True),
            )
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
    finally:
        try:
            fcntl.flock(root_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(root_descriptor)


def _optional_snapshot(
    config: RenderConfig,
    relative: str,
    *,
    code: str,
) -> FileSnapshot | None:
    path = config.workspace_root / relative
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CohortFlowValidationError(f"{code}_STAT_FAILED", relative) from exc
    return _read_stable_path(
        config.workspace_root,
        relative,
        expected_sha256=None,
        code=code,
    )


def _capture_published_pair(config: RenderConfig) -> PublishedPairState:
    return PublishedPairState(
        svg=_optional_snapshot(config, config.output_svg, code="EXISTING_SVG"),
        receipt=_optional_snapshot(config, config.receipt, code="EXISTING_RECEIPT"),
    )


def _assert_distinct_inodes(
    values: Sequence[tuple[str, FileSnapshot]],
) -> None:
    seen: dict[tuple[int, int], str] = {}
    for label, snapshot in values:
        key = (snapshot.identity.device, snapshot.identity.inode)
        previous = seen.get(key)
        if previous is not None:
            raise CohortFlowValidationError(
                "PATH_INODE_ALIAS",
                f"{previous}={label}",
            )
        seen[key] = label


def _assert_namespace_inodes(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
    sources: ValidatedSources,
    lock: FileSnapshot,
    pair: PublishedPairState,
) -> None:
    values: list[tuple[str, FileSnapshot]] = [
        ("config", config.config_snapshot),
        ("renderer", implementation.renderer),
        ("cli", implementation.cli),
        ("lock", lock),
        *[(key, snapshot) for key, snapshot in sources.snapshots.items()],
    ]
    if pair.svg is not None:
        values.append(("output_svg", pair.svg))
    if pair.receipt is not None:
        values.append(("receipt", pair.receipt))
    _assert_distinct_inodes(values)


def _revalidate_inputs(
    config: RenderConfig,
    implementation: ImplementationSnapshot,
    sources: ValidatedSources,
) -> None:
    _revalidate_snapshot(config.workspace_root, config.config_snapshot, code="CONFIG")
    _validate_implementation_snapshot(config, implementation)
    for key, snapshot in sources.snapshots.items():
        _revalidate_snapshot(
            config.workspace_root,
            snapshot,
            code=f"SOURCE_{key.upper()}",
        )


def _revalidate_lock(config: RenderConfig, lock: FileSnapshot) -> None:
    _revalidate_snapshot(config.workspace_root, lock, code="LOCK")


def _remove_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _rollback_pair(
    config: RenderConfig,
    previous: PublishedPairState,
) -> None:
    output_path = _assert_safe_output_parent(
        config.workspace_root,
        config.output_svg,
    )
    receipt_path = _assert_safe_output_parent(
        config.workspace_root,
        config.receipt,
    )
    if previous.complete:
        assert previous.svg is not None
        assert previous.receipt is not None
        _atomic_write(
            output_path,
            previous.svg.payload,
            mode=stat.S_IMODE(previous.svg.identity.mode),
        )
        _atomic_write(
            receipt_path,
            previous.receipt.payload,
            mode=stat.S_IMODE(previous.receipt.identity.mode),
        )
    else:
        _remove_if_present(output_path)
        _remove_if_present(receipt_path)


def _validate_published_locked(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
    lock: FileSnapshot,
) -> dict[str, Any]:
    _revalidate_lock(config, lock)
    _revalidate_snapshot(config.workspace_root, config.config_snapshot, code="CONFIG")
    _validate_implementation_snapshot(config, implementation)
    sources = _validate_sources(config)
    expected_svg = render_svg(config, sources.diagram, sources.records)
    output = _read_stable_path(
        config.workspace_root,
        config.output_svg,
        expected_sha256=_sha256_bytes(expected_svg),
        code="SVG",
    )
    if (
        output.payload != expected_svg
        or stat.S_IMODE(output.identity.mode) != SVG_MODE
        or output.identity.nlink != 1
    ):
        raise CohortFlowValidationError("SVG_PUBLISHED_STATE_INVALID")
    receipt = _read_stable_path(
        config.workspace_root,
        config.receipt,
        expected_sha256=None,
        code="RECEIPT",
    )
    if (
        stat.S_IMODE(receipt.identity.mode) != RECEIPT_MODE
        or receipt.identity.nlink != 1
    ):
        raise CohortFlowValidationError("RECEIPT_PUBLISHED_STATE_INVALID")
    actual_receipt = _decode_json(receipt.payload, "RECEIPT_JSON_INVALID")
    expected_receipt = _receipt_payload(
        config,
        expected_svg,
        sources.records,
        implementation=implementation,
    )
    if actual_receipt != expected_receipt:
        raise CohortFlowValidationError("RECEIPT_CONTENT_MISMATCH")
    if _canonical_json(expected_receipt) != receipt.payload:
        raise CohortFlowValidationError("RECEIPT_CANONICAL_BYTES_MISMATCH")
    _assert_namespace_inodes(
        config,
        implementation,
        sources,
        lock,
        PublishedPairState(svg=output, receipt=receipt),
    )
    _revalidate_inputs(config, implementation, sources)
    _revalidate_lock(config, lock)
    return expected_receipt


def render_and_publish(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
) -> dict[str, Any]:
    """Validate sources, render exact SVG, and atomically publish SVG + receipt."""

    _validate_implementation_snapshot(config, implementation)
    with _publication_lock(config) as lock:
        _revalidate_snapshot(
            config.workspace_root, config.config_snapshot, code="CONFIG"
        )
        _validate_implementation_snapshot(config, implementation)
        sources = _validate_sources(config)
        previous = _capture_published_pair(config)
        _assert_namespace_inodes(
            config,
            implementation,
            sources,
            lock,
            previous,
        )
        svg = render_svg(config, sources.diagram, sources.records)
        receipt_value = _receipt_payload(
            config,
            svg,
            sources.records,
            implementation=implementation,
        )
        output_path = _assert_safe_output_parent(
            config.workspace_root,
            config.output_svg,
        )
        receipt_path = _assert_safe_output_parent(
            config.workspace_root,
            config.receipt,
        )
        _revalidate_inputs(config, implementation, sources)
        _revalidate_lock(config, lock)
        publication_started = False
        try:
            publication_started = True
            _revalidate_lock(config, lock)
            _atomic_write(output_path, svg, mode=SVG_MODE)
            _revalidate_lock(config, lock)
            _atomic_write(
                receipt_path,
                _canonical_json(receipt_value),
                mode=RECEIPT_MODE,
            )
            _revalidate_lock(config, lock)
            return _validate_published_locked(
                config,
                implementation=implementation,
                lock=lock,
            )
        except BaseException:
            if publication_started:
                try:
                    _rollback_pair(config, previous)
                except BaseException as rollback_error:
                    raise CohortFlowValidationError(
                        "ROLLBACK_FAILED",
                        type(rollback_error).__name__,
                    ) from rollback_error
            raise


def validate_published(
    config: RenderConfig,
    *,
    implementation: ImplementationSnapshot,
) -> dict[str, Any]:
    """Recompute and byte-validate the published SVG and its receipt."""

    _validate_implementation_snapshot(config, implementation)
    with _publication_lock(config) as lock:
        return _validate_published_locked(
            config,
            implementation=implementation,
            lock=lock,
        )


_RENDERER_IMPORT_SNAPSHOT: Final = capture_absolute_snapshot(
    Path(__file__),
    record_path=CANONICAL_RENDERER_PATH,
    code="RENDERER_IMPORT",
)
