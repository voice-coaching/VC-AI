from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import struct
import time
from typing import Any, Mapping
import zlib

import pytest
import yaml  # type: ignore[import-untyped]

import voice_coach.reproducibility.paper_draft_pdf as draft
from voice_coach.reproducibility.paper_draft_pdf import (
    BuildBackend,
    BuildRun,
    CONFIG_SCHEMA,
    ImplementationSnapshot,
    PaperDraftPdfError,
    PostflightResult,
    RenderConfig,
    RenderNamespace,
    TOOL_KEYS,
    build_and_publish,
    capture_test_implementation_snapshot,
    config_only_summary,
    load_render_config,
    render_sanitized_html,
    validate_committed,
)


@dataclass(frozen=True)
class Fixture:
    root: Path
    config_path: Path
    namespace: RenderNamespace


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(path: Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _png(
    width: int = 64,
    height: int = 40,
    *,
    included_colors: frozenset[int] | None = None,
) -> bytes:
    rows: list[bytes] = []
    palette = list(draft.COHORT_PALETTE.values())
    included = included_colors if included_colors is not None else frozenset(range(4))
    for y in range(height):
        row = bytearray()
        for x in range(width):
            color = (255, 255, 255)
            for index, candidate in enumerate(palette):
                start_x = 4 + index * 15
                if index in included and start_x <= x < start_x + 11 and 10 <= y < 30:
                    color = candidate
                    break
            row.extend(color)
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _lo_classic_xref_pdf(
    *,
    date: str = "D:20260729205111+09'00'",
    document_id: str = "70C6ED7B44B812BF3770C02932875125",
    second_id: str | None = None,
    checksum: str = "7262F352D7D21F0C04535CD705B99B00",
    modification_date: str | None = None,
    extra_info: bytes = b"",
    stable_prefix: bytes = b"",
) -> bytes:
    title = (
        (b"\xfe\xff" + "Synthetic Korean L2 Paper".encode("utf-16-be"))
        .hex()
        .upper()
        .encode()
    )
    producer = (
        (b"\xfe\xff" + "LibreOffice 24.2".encode("utf-16-be")).hex().upper().encode()
    )
    prefix = (
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj\n<</Type/Catalog>>\nendobj\n"
        + stable_prefix
        + b"\n2 0 obj\n<</Title<"
        + title
        + b">\n/Producer<"
        + producer
        + b">"
        + extra_info
        + b"\n/CreationDate("
        + date.encode()
        + b")"
    )
    if modification_date is not None:
        prefix += b"\n/ModDate(" + modification_date.encode() + b")"
    prefix += b">>\nendobj\n\n"
    xref_offset = len(prefix)
    xref = b"xref\n0 3\n0000000000 65535 f \n0000000015 00000 n \n0000000055 00000 n \n"
    trailer = (
        b"trailer\n<</Size 3/Root 1 0 R\n/Info 2 0 R\n"
        b"/ID [ <"
        + document_id.encode()
        + b">\n<"
        + (second_id or document_id).encode()
        + b"> ]\n/DocChecksum /"
        + checksum.encode()
        + b"\n>>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return prefix + xref + trailer


def _manuscript(title: str) -> tuple[str, list[str]]:
    markers = [f"[PENDING: synthetic-{index:02d}]" for index in range(1, 19)]
    lines = [
        f"# {title}",
        "",
        "## 초록",
        "",
        "Synthetic aggregate-only manuscript.",
        "",
        "[공식 링크](https://example.com/a)와 "
        "[공식 링크 B](https://example.org/b)를 사용한다.",
        "",
        "[로컬 표](tables/local.md), [file](file:///tmp/secret), "
        "[absolute](/tmp/local)은 annotation에서 제거한다.",
        "",
        "편집 가능한 [cohort source](figures/aihub71490_cohort_flow.mmd)를 포함한다.",
        "",
        draft.COHORT_MARKDOWN_IMAGE,
        "",
        *markers,
        "",
    ]
    for index in range(1, 14):
        if index == 5:
            lines.append("<!-- BEGIN WAVLM_S103_RESULTS_TABLE -->")
        lines.extend(
            (
                f"## 표 {index}",
                "",
                f"| 항목-{index} | 값-{index} |",
                "|---|---:|",
                f"| row-{index} | {index} |",
                "",
            )
        )
        if index == 5:
            lines.extend(("<!-- END WAVLM_S103_RESULTS_TABLE -->", ""))
    lines.extend(("## 참고문헌", ""))
    for index in range(1, 12):
        lines.append(f"{index}. Reference Author {index}, Synthetic Study {index}.")
    lines.extend(("", "## 부록", "", "Synthetic appendix.", ""))
    return "\n".join(lines), markers


def _synthetic_extracted_text(manuscript: str) -> bytes:
    """Approximate the visible text surface needed by the synthetic backend."""

    visible = re.sub(
        r"(?m)^\|(?:\s*:?-+:?\s*\|)+\s*$",
        "",
        manuscript,
    ).replace("|", " ")
    visible = draft.INLINE_LINK_RE.sub(lambda match: match.group(1), visible)
    visible = visible.replace("*", "").replace("`", "")
    return visible.encode()


def _namespace(root: Path) -> RenderNamespace:
    tools = {key: str((root / "tools" / key).absolute()) for key in TOOL_KEYS}
    return RenderNamespace(
        config_path="config.yaml",
        input_paths={
            "manuscript": "inputs/manuscript.md",
            "evidence_report": "inputs/evidence.json",
            "publication_commit": "inputs/publication/commit.json",
            "semantic_commit": "inputs/semantic/commit.json",
            "cohort_svg": "inputs/cohort.svg",
            "cohort_receipt": "inputs/cohort-receipt.json",
            "cohort_renderer": "inputs/cohort_renderer.py",
            "cohort_cli": "inputs/cohort_cli.py",
        },
        module_path="implementation/paper_draft_pdf.py",
        cli_path="implementation/build_paper_draft_pdf.py",
        tool_paths=tools,
        output_root="outputs/paper_draft_pdf",
        commit_path="outputs/paper_draft_pdf/commit.json",
        lock_path="outputs/.paper_draft_pdf.lock",
        title="Synthetic Korean L2 Paper",
        production=False,
    )


def _fixture(tmp_path: Path) -> Fixture:
    namespace = _namespace(tmp_path)
    manuscript_text, markers = _manuscript(namespace.title)
    manuscript = tmp_path / namespace.input_paths["manuscript"]
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(manuscript_text, encoding="utf-8")

    cohort_renderer = tmp_path / namespace.input_paths["cohort_renderer"]
    cohort_cli = tmp_path / namespace.input_paths["cohort_cli"]
    cohort_renderer.write_bytes(b"# cohort renderer\n")
    cohort_cli.write_bytes(b"# cohort cli\n")
    cohort_svg = tmp_path / namespace.input_paths["cohort_svg"]
    cohort_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="425">'
        '<title>Synthetic cohort</title><rect width="680" height="425"/>'
        "</svg>\n",
        encoding="utf-8",
    )
    cohort_receipt = tmp_path / namespace.input_paths["cohort_receipt"]
    _write_json(
        cohort_receipt,
        {
            "schema_version": "voice_coach.paper.cohort_flow_svg_receipt.v2",
            "status": "passed",
            "output": _record(
                cohort_svg,
                namespace.input_paths["cohort_svg"],
            ),
            "implementation": {
                "renderer": _record(
                    cohort_renderer,
                    namespace.input_paths["cohort_renderer"],
                ),
                "cli": _record(
                    cohort_cli,
                    namespace.input_paths["cohort_cli"],
                ),
            },
            "research_state": {
                "manual_alignment_g0": "PENDING",
                "research_completion_claimed": False,
            },
        },
    )
    pending_hash = draft._canonical_string_hash(markers)
    evidence = tmp_path / namespace.input_paths["evidence_report"]
    _write_json(
        evidence,
        {
            "status": "passed",
            "summary": {
                "artifact_count": 1,
                "artifact_passed": 1,
                "claim_count": 1,
                "claim_passed": 1,
            },
            "pending_contract": {
                "status": "passed",
                "marker_count": 18,
                "markers_sha256": pending_hash,
                "errors": [],
            },
            "manuscript": _record(
                manuscript,
                namespace.input_paths["manuscript"],
            ),
            "locked_test_guard": {
                "status": "passed",
                "errors": [],
                "accessed": {"synthetic": False},
            },
            "privacy": {
                "aggregate_only": True,
                "environment_files_read": False,
                "raw_data_read": False,
            },
        },
    )
    publication_receipt = tmp_path / "inputs/publication/receipt.json"
    _write_json(
        publication_receipt,
        {
            "status": "passed",
            "pending_contract": {
                "marker_count": 18,
                "markers_sha256": pending_hash,
                "markers_preserved": True,
            },
            "source_pins": [
                _record(manuscript, namespace.input_paths["manuscript"]),
                _record(evidence, namespace.input_paths["evidence_report"]),
            ],
            "privacy": {
                "aggregate_only": True,
                "environment_files_read": False,
                "locked_test_files_read": False,
                "raw_files_read": False,
                "secret_files_read": False,
            },
        },
    )
    publication_commit = tmp_path / namespace.input_paths["publication_commit"]
    _write_json(
        publication_commit,
        {
            "status": "committed",
            "receipt": _record(
                publication_receipt,
                "inputs/publication/receipt.json",
            ),
        },
    )
    semantic_receipt = tmp_path / "inputs/semantic/receipt.json"
    _write_json(
        semantic_receipt,
        {
            "publication_status": "completed",
            "completion_claims": {"p04_complete": False, "p05_complete": False},
            "source_records": {
                "manuscript": _record(
                    manuscript,
                    namespace.input_paths["manuscript"],
                ),
                "validation_report": _record(
                    evidence,
                    namespace.input_paths["evidence_report"],
                ),
            },
        },
    )
    semantic_commit = tmp_path / namespace.input_paths["semantic_commit"]
    _write_json(
        semantic_commit,
        {
            "status": "committed",
            "p04_complete": False,
            "p05_complete": False,
            "receipt": _record(
                semantic_receipt,
                "inputs/semantic/receipt.json",
            ),
        },
    )

    implementation_root = tmp_path / "implementation"
    implementation_root.mkdir()
    module = tmp_path / namespace.module_path
    cli = tmp_path / namespace.cli_path
    module.write_bytes(b"# synthetic PDF module\n")
    cli.write_bytes(b"# synthetic PDF CLI\n")
    tools: dict[str, dict[str, Any]] = {}
    for key, absolute in namespace.tool_paths.items():
        tool = Path(absolute)
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_bytes(f"#!/bin/sh\n# {key}\n".encode())
        tool.chmod(0o755)
        tools[key] = {
            "path": absolute,
            "sha256": _sha(tool),
            "size_bytes": tool.stat().st_size,
            "version": ("24.2.7.2" if key == "libreoffice" else f"synthetic-{key}-1"),
        }
    inputs = {
        key: _record(tmp_path / path, path)
        for key, path in namespace.input_paths.items()
    }
    config = {
        "schema_version": CONFIG_SCHEMA,
        "status": "ready",
        "policy": {
            "draft": True,
            "final": False,
            "visual_review_status": "pending",
            "markdown_parser": "markdown-it-py",
            "markdown_parser_version": "4.2.0",
            "html_enabled": False,
            "expected_title": namespace.title,
            "expected_markdown_tables": 13,
            "expected_references": 11,
            "expected_pending_count": 18,
            "expected_pending_markers_sha256": pending_hash,
            "expected_https_links": 2,
            "svg_width": 680,
            "svg_height": 425,
            "source_date_epoch": 1767225600,
            "libreoffice_timeout_seconds": 30,
            "tool_timeout_seconds": 10,
            "raster_dpi": 120,
            "blank_white_ratio": 0.9999,
            "kernel_network_namespace": False,
        },
        "inputs": inputs,
        "implementation": {
            "module": _record(module, namespace.module_path),
            "cli": _record(cli, namespace.cli_path),
        },
        "tools": tools,
        "output": {
            "root": namespace.output_root,
            "commit": namespace.commit_path,
            "generations": "generations",
            "lock": namespace.lock_path,
        },
    }
    config_path = tmp_path / namespace.config_path
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return Fixture(tmp_path, config_path, namespace)


def _load(fixture: Fixture) -> RenderConfig:
    return load_render_config(
        fixture.config_path,
        workspace_root=fixture.root,
        namespace=fixture.namespace,
    )


def _replace_inode_with_same_bytes(path: Path) -> None:
    metadata = path.stat()
    replacement = path.with_name(f".{path.name}.replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(stat.S_IMODE(metadata.st_mode))
    os.replace(replacement, path)


def _assert_no_committed_output(fixture: Fixture) -> None:
    output = fixture.root / fixture.namespace.output_root
    assert not (output / "commit.json").exists()
    generations = output / "generations"
    if generations.exists():
        assert list(generations.iterdir()) == []


def _target_path(fixture: Fixture, category: str, key: str) -> Path:
    if category == "config":
        return fixture.config_path
    if category == "input":
        return fixture.root / fixture.namespace.input_paths[key]
    if category == "implementation":
        relative = (
            fixture.namespace.module_path
            if key == "module"
            else fixture.namespace.cli_path
        )
        return fixture.root / relative
    if category == "tool":
        return Path(fixture.namespace.tool_paths[key])
    raise AssertionError(category)


def _read_category(
    fixture: Fixture,
    category: str,
    config: RenderConfig | None,
) -> None:
    if category == "config":
        _load(fixture)
    elif category == "input":
        assert config is not None
        draft._load_sources(config)
    elif category == "implementation":
        assert config is not None
        capture_test_implementation_snapshot(config)
    elif category == "tool":
        assert config is not None
        draft._load_tool_snapshots(config)
    else:
        raise AssertionError(category)


class FakeBackend(BuildBackend):
    test_only = True
    backend_id = "synthetic-test-v1"

    def inspect_tools(
        self,
        config: RenderConfig,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> Mapping[str, str]:
        del config, work
        return {key: tools[key].record.version for key in TOOL_KEYS}

    def build_once(
        self,
        config: RenderConfig,
        html: bytes,
        svg: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> BuildRun:
        del config, html, svg, tools, work
        return BuildRun(
            raw_pdf=f"%PDF-1.7\nraw-{index}\n".encode(),
            network={
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
            },
        )

    def normalize(
        self,
        config: RenderConfig,
        raw_pdf: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> bytes:
        del config, raw_pdf, tools, work, index
        return b"%PDF-1.7\nsynthetic-normalized\n"

    def postflight(
        self,
        config: RenderConfig,
        pdf: bytes,
        sources: draft.SourceBundle,
        html: draft.HtmlArtifact,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> PostflightResult:
        del tools, work
        extracted = _synthetic_extracted_text(sources.manuscript)
        page = _png()
        width, height, white_ratio, pixels = draft._png_decode(page)
        cohort_figure = draft._cohort_figure_audit([(1, width, height, pixels)])
        semantics = draft._semantic_postflight(
            config,
            sources,
            html,
            extracted,
            extracted,
            0,
        )
        document = {
            "schema_version": draft.POSTFLIGHT_SCHEMA,
            "status": "passed",
            "pdf": {
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "size_bytes": len(pdf),
                "version": "1.7",
                "page_count": 1,
                "a4_page_count": 1,
                "page_geometry": [
                    {
                        "page": 1,
                        "width": 595.276,
                        "height": 841.89,
                        "rotation": 0,
                        "boxes": {
                            name: [0.0, 0.0, 595.276, 841.89]
                            for name in draft.PDF_PAGE_BOX_NAMES
                        },
                    }
                ],
                "encrypted": False,
                "javascript_count": 0,
                "https_url_count": config.policy["expected_https_links"],
                "https_urls_sha256": html.audit["https_links_sha256"],
                "relative_local_file_url_count": 0,
                "creation_date_status": "absent",
                "modification_date_status": "absent",
                "metadata_stream_present": False,
                "custom_metadata": False,
                "tagged": True,
                "form": "none",
                "user_properties": False,
                "suspects": False,
                "metadata": {
                    "allowlist_status": "passed",
                    "producer": draft._libreoffice_pdf_producer(config),
                    "title": config.namespace.title,
                    "metadata_stream_present": False,
                    "xmp_status": "absent",
                    "date_fields_present": False,
                },
            },
            "fonts": {
                "font_count": 1,
                "embedded_font_count": 1,
                "unicode_mapped_font_count": 1,
                "all_embedded": True,
                "all_unicode_mapped": True,
                "font_names": ["SyntheticFont"],
            },
            "text_extraction": {
                "layout": {
                    "mode": "pdftotext-layout",
                    "sha256": hashlib.sha256(extracted).hexdigest(),
                    "size_bytes": len(extracted),
                    "page_count": 1,
                },
                "semantic": {
                    "mode": "pdftotext-default",
                    "sha256": hashlib.sha256(extracted).hexdigest(),
                    "size_bytes": len(extracted),
                    "page_count": 1,
                },
                "table": {
                    "mode": "pdftotext-raw",
                    "sha256": hashlib.sha256(extracted).hexdigest(),
                    "size_bytes": len(extracted),
                    "page_count": 1,
                },
            },
            "semantics": semantics,
            "raster": {
                "dpi": config.policy["raster_dpi"],
                "timeout_seconds": float(config.policy["tool_timeout_seconds"]),
                "page_count": 1,
                "blank_page_count": 0,
                "cohort_figure": cohort_figure,
                "pages": [
                    {
                        "path": "pages/page-001.png",
                        "sha256": hashlib.sha256(page).hexdigest(),
                        "size_bytes": len(page),
                        "width": width,
                        "height": height,
                        "white_ratio": round(white_ratio, 8),
                        "raster_blank_candidate": False,
                        "text_blank_candidate": False,
                        "visibility_status": "visible_text",
                        "blank": False,
                    }
                ],
            },
            "network": draft._network_stage_evidence(draft.NETWORK_POSTFLIGHT_STAGES),
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
            extracted_layout_text=extracted,
            extracted_semantic_text=extracted,
            extracted_table_text=extracted,
            pages={"page-001.png": page},
        )


class CallbackBackend(FakeBackend):
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.callback_completed = False

    def build_once(
        self,
        config: RenderConfig,
        html: bytes,
        svg: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> BuildRun:
        if not self.callback_completed:
            self.callback_completed = True
            self.callback()
        return super().build_once(config, html, svg, tools, work, index)


class VariantBackend(FakeBackend):
    def __init__(
        self,
        variant: str,
        *,
        fail_stage: str | None = None,
    ) -> None:
        self.variant = variant
        self.fail_stage = fail_stage
        self.postflight_count = 0

    def inspect_tools(
        self,
        config: RenderConfig,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> Mapping[str, str]:
        if self.fail_stage == "inspect":
            raise PaperDraftPdfError("SYNTHETIC_INSPECT_TIMEOUT")
        return super().inspect_tools(config, tools, work)

    def build_once(
        self,
        config: RenderConfig,
        html: bytes,
        svg: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> BuildRun:
        if self.fail_stage == f"build-{index}":
            raise PaperDraftPdfError(f"SYNTHETIC_BUILD_{index}_TIMEOUT")
        return super().build_once(config, html, svg, tools, work, index)

    def normalize(
        self,
        config: RenderConfig,
        raw_pdf: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> bytes:
        del config, raw_pdf, tools, work
        if self.fail_stage == f"normalize-{index}":
            raise PaperDraftPdfError(f"SYNTHETIC_NORMALIZE_{index}_TIMEOUT")
        return f"%PDF-1.7\nsynthetic-{self.variant}\n".encode()

    def postflight(
        self,
        config: RenderConfig,
        pdf: bytes,
        sources: draft.SourceBundle,
        html: draft.HtmlArtifact,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> PostflightResult:
        self.postflight_count += 1
        if self.fail_stage == "postflight" and self.postflight_count == 1:
            raise PaperDraftPdfError("SYNTHETIC_POSTFLIGHT_FAILED")
        if self.fail_stage == "deep-postflight" and self.postflight_count == 2:
            raise PaperDraftPdfError("SYNTHETIC_DEEP_POSTFLIGHT_FAILED")
        return super().postflight(config, pdf, sources, html, tools, work)


class NondeterministicBackend(VariantBackend):
    def normalize(
        self,
        config: RenderConfig,
        raw_pdf: bytes,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
        index: int,
    ) -> bytes:
        del config, raw_pdf, tools, work
        return f"%PDF-1.7\nsynthetic-{self.variant}-{index}\n".encode()


class PostflightCallbackBackend(FakeBackend):
    def __init__(self, callback: Any) -> None:
        self.callback = callback

    def inspect_tools(
        self,
        config: RenderConfig,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> Mapping[str, str]:
        raise AssertionError("deep validation must not run version subprocesses")

    def postflight(
        self,
        config: RenderConfig,
        pdf: bytes,
        sources: draft.SourceBundle,
        html: draft.HtmlArtifact,
        tools: Mapping[str, draft.ToolSnapshot],
        work: Path,
    ) -> PostflightResult:
        result = super().postflight(config, pdf, sources, html, tools, work)
        self.callback()
        return result


def _implementation(config: RenderConfig) -> ImplementationSnapshot:
    return capture_test_implementation_snapshot(config)


def test_config_only_accesses_no_sources_tools_or_outputs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    for relative in fixture.namespace.input_paths.values():
        (tmp_path / relative).unlink()
    for absolute in fixture.namespace.tool_paths.values():
        Path(absolute).unlink()
    (tmp_path / "implementation" / "paper_draft_pdf.py").unlink()
    (tmp_path / "implementation" / "build_paper_draft_pdf.py").unlink()
    summary = config_only_summary(_load(fixture))
    assert summary["actual_state"] == {
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
    }
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda text: text.replace(
                "status: ready\n",
                "status: ready\nstatus: ready\n",
                1,
            ),
            "CONFIG_DUPLICATE_KEY",
        ),
        (
            lambda text: text.replace(
                "blank_white_ratio: 0.9999",
                "blank_white_ratio: .nan",
                1,
            ),
            "CONFIG_POLICY_INVALID",
        ),
        (
            lambda text: f"unexpected: true\n{text}",
            "CONFIG_KEYS_INVALID",
        ),
    ],
)
def test_config_rejects_duplicate_nan_and_unknown_fields(
    tmp_path: Path,
    mutation: Any,
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    original = fixture.config_path.read_text(encoding="utf-8")
    fixture.config_path.write_text(mutation(original), encoding="utf-8")
    with pytest.raises(PaperDraftPdfError, match=error):
        _load(fixture)
    assert not (tmp_path / "outputs").exists()


def test_production_namespace_rejects_any_noncanonical_change(
    tmp_path: Path,
) -> None:
    namespace = replace(
        draft.CANONICAL_NAMESPACE,
        output_root="artifacts/reproducibility/not-canonical",
    )
    with pytest.raises(PaperDraftPdfError, match="NAMESPACE_INVALID"):
        load_render_config(
            Path(namespace.config_path),
            workspace_root=tmp_path,
            namespace=namespace,
        )


def test_namespace_rejects_config_output_path_alias(tmp_path: Path) -> None:
    namespace = _namespace(tmp_path)
    aliased = replace(namespace, config_path=namespace.output_root)
    with pytest.raises(PaperDraftPdfError, match="NAMESPACE_INVALID"):
        draft._assert_namespace(aliased)


@pytest.mark.parametrize(
    ("category", "key"),
    [
        ("config", ""),
        *[("input", key) for key in draft.INPUT_KEYS],
        ("implementation", "module"),
        ("implementation", "cli"),
        *[("tool", key) for key in TOOL_KEYS],
    ],
)
@pytest.mark.parametrize("attack", ["symlink", "hardlink"])
def test_all_pinned_files_reject_symlink_and_hardlink(
    tmp_path: Path,
    category: str,
    key: str,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = None if category == "config" else _load(fixture)
    target = _target_path(fixture, category, key)
    if attack == "symlink":
        backup = target.with_name(f".{target.name}.real")
        target.rename(backup)
        target.symlink_to(backup.name)
    else:
        os.link(target, target.with_name(f".{target.name}.hardlink"))
    with pytest.raises(PaperDraftPdfError):
        _read_category(fixture, category, config)
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    ("category", "key"),
    [
        ("config", ""),
        ("input", "manuscript"),
        ("implementation", "module"),
        ("implementation", "cli"),
        ("tool", "ghostscript"),
    ],
)
def test_pinned_file_read_race_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    key: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = None if category == "config" else _load(fixture)
    target = _target_path(fixture, category, key)
    target_inode = target.stat().st_ino
    original_read = os.read
    attacked = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal attacked
        payload = original_read(descriptor, count)
        if not attacked and os.fstat(descriptor).st_ino == target_inode:
            attacked = True
            _replace_inode_with_same_bytes(target)
        return payload

    monkeypatch.setattr(draft.os, "read", racing_read)
    with pytest.raises(PaperDraftPdfError, match="PATH_CHANGED"):
        _read_category(fixture, category, config)
    assert attacked


@pytest.mark.parametrize(
    ("category", "key"),
    [
        ("config", ""),
        ("input", "manuscript"),
        ("implementation", "module"),
        ("implementation", "cli"),
        ("tool", "ghostscript"),
    ],
)
def test_post_snapshot_inode_replacement_is_rejected_before_publish(
    tmp_path: Path,
    category: str,
    key: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    implementation = _implementation(config)
    target = _target_path(fixture, category, key)
    backend = CallbackBackend(lambda: _replace_inode_with_same_bytes(target))
    with pytest.raises(PaperDraftPdfError):
        build_and_publish(
            config,
            implementation=implementation,
            backend=backend,
        )
    _assert_no_committed_output(fixture)


@pytest.mark.parametrize("scope", ["parent", "output", "generations"])
def test_output_directory_rebind_is_rejected(
    tmp_path: Path,
    scope: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    implementation = _implementation(config)

    def rebind() -> None:
        parent = tmp_path / "outputs"
        output = parent / "paper_draft_pdf"
        generations = output / "generations"
        target = {
            "parent": parent,
            "output": output,
            "generations": generations,
        }[scope]
        backup = target.with_name(f"{target.name}-rebound-original")
        target.rename(backup)
        target.mkdir(mode=0o750)
        if scope == "parent":
            return
        if scope == "output":
            (target / "generations").mkdir(mode=0o750)

    with pytest.raises(PaperDraftPdfError, match="REBOUND"):
        build_and_publish(
            config,
            implementation=implementation,
            backend=CallbackBackend(rebind),
        )
    _assert_no_committed_output(fixture)


def test_sanitized_html_has_exact_svg_and_https_only(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    manuscript = (tmp_path / fixture.namespace.input_paths["manuscript"]).read_text(
        encoding="utf-8"
    )
    artifact = render_sanitized_html(config, manuscript)
    text = artifact.payload.decode()
    assert (
        '<img src="resources/aihub71490_cohort_flow.svg" '
        'width="680" height="425"' in text
    )
    assert "file:" not in text
    assert "/tmp/local" not in text
    assert "tables/local.md" not in text
    assert artifact.audit["table_count"] == 13
    assert artifact.audit["https_link_count"] == 2
    assert artifact.audit["stripped_non_https_link_count"] == 4


def test_raw_html_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    manuscript = (tmp_path / fixture.namespace.input_paths["manuscript"]).read_text(
        encoding="utf-8"
    )
    with pytest.raises(PaperDraftPdfError, match="MARKDOWN_RAW_HTML"):
        render_sanitized_html(
            config, manuscript.replace("## 초록", "<script>x</script>")
        )


@pytest.mark.parametrize(
    ("needle", "replacement", "error"),
    [
        (
            "## 초록",
            "<!-- unapproved marker -->\n## 초록",
            "MARKDOWN_COMMENT_NOT_ALLOWED",
        ),
        (
            "Synthetic aggregate-only manuscript.",
            "![remote](https://example.com/image.png)",
            "MARKDOWN_IMAGE_NOT_ALLOWED",
        ),
        (
            "Synthetic aggregate-only manuscript.",
            '<iframe src="https://example.com"></iframe>',
            "MARKDOWN_RAW_HTML_NOT_ALLOWED",
        ),
    ],
)
def test_markdown_attack_surface_is_rejected(
    tmp_path: Path,
    needle: str,
    replacement: str,
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    manuscript = (tmp_path / fixture.namespace.input_paths["manuscript"]).read_text(
        encoding="utf-8"
    )
    with pytest.raises(PaperDraftPdfError, match=error):
        render_sanitized_html(
            config,
            manuscript.replace(needle, replacement, 1),
        )


def test_actual_canonical_manuscript_renders_one_figure_in_source_order(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    base = _load(fixture)
    manuscript = Path("docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md").read_text(
        encoding="utf-8"
    )
    https_count = sum(
        draft._is_safe_https(draft._markdown_destination(match.group(2)))
        for match in draft.INLINE_LINK_RE.finditer(manuscript)
    )
    namespace = replace(
        base.namespace,
        title=draft.CANONICAL_NAMESPACE.title,
    )
    config = replace(
        base,
        namespace=namespace,
        policy={
            **base.policy,
            "expected_title": namespace.title,
            "expected_https_links": https_count,
        },
    )
    artifact = render_sanitized_html(config, manuscript)
    text = artifact.payload.decode("utf-8")
    assert artifact.audit["cohort_source_link_seen"] == 1
    assert artifact.audit["cohort_svg_image_seen"] == 1
    assert text.count("<figure>") == 1
    assert text.count(f'src="{draft.COHORT_RESOURCE}"') == 1
    assert "figures/aihub71490_cohort_flow.svg" not in text
    assert "figures/aihub71490_cohort_flow.mmd" not in text
    assert text.index("검증된 벡터 렌더는 다음과 같다") < text.index("<figure>")
    assert "<h4>" in text
    assert "<h5>" in text
    assert text.count("<table ") == 13
    assert text.count('class="dense-table"') == 1
    assert text.count('class="atomic-cell"') > 100
    assert "table-layout: auto" in text
    assert "overflow-wrap: normal" in text
    assert "white-space: nowrap" in text
    assert "break-inside: avoid-page !important" in text
    assert artifact.audit["table_body_row_count"] == 103
    assert len(artifact.audit["table_body_row_groups"]) == 13
    assert artifact.audit["table_body_atomic_cell_probe_count"] == 204
    assert (
        "overflow-wrap: anywhere"
        not in text.split("th, td", 1)[1].split(
            "figure",
            1,
        )[0]
    )


def test_pending_markers_are_an_exact_normalized_multiset(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    sources = draft._load_sources(config)
    html = render_sanitized_html(config, sources.manuscript)
    wrapped = sources.manuscript.replace(
        "[PENDING: synthetic-01]",
        "[PENDING:\n synthetic-01]",
        1,
    )
    semantics = draft._semantic_postflight(
        config,
        sources,
        html,
        _synthetic_extracted_text(wrapped),
        _synthetic_extracted_text(sources.manuscript),
        0,
    )
    assert semantics["pending_count"] == 18
    for invalid in (
        sources.manuscript + "\n[PENDING: synthetic-01]\n",
        sources.manuscript.replace("[PENDING: synthetic-18]", "", 1),
        sources.manuscript.replace("synthetic-18", "synthetic-XX", 1),
        sources.manuscript.replace(
            "[PENDING: synthetic-01]\n[PENDING: synthetic-02]",
            "[PENDING: synthetic-01 synthetic-02]\n[PENDING: synthetic-03]",
            1,
        ),
    ):
        with pytest.raises(
            PaperDraftPdfError,
            match="PDF_PENDING_MARKER_MULTISET_INVALID",
        ):
            draft._semantic_postflight(
                config,
                sources,
                html,
                _synthetic_extracted_text(invalid),
                _synthetic_extracted_text(sources.manuscript),
                0,
            )


def test_full_reference_and_table_header_signatures_are_exact_multisets(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    sources = draft._load_sources(config)
    html = render_sanitized_html(config, sources.manuscript)
    baseline = draft._normalized_text(
        _synthetic_extracted_text(sources.manuscript).decode()
    )
    semantics = draft._semantic_postflight(
        config,
        sources,
        html,
        baseline.encode(),
        baseline.encode(),
        0,
    )
    assert semantics["pdf_reference_signature_count"] == 11
    assert semantics["pdf_table_header_signature_count"] == 13
    assert semantics["table_body_preservation_status"] == (
        "row_signatures_and_atomic_tokens_machine_verified"
    )

    reference = str(sources.semantics["reference_probes"][0])
    header = str(html.audit["table_header_probes"][0])
    for attacked in (
        baseline.replace(reference, "", 1),
        baseline + " " + reference,
    ):
        with pytest.raises(
            PaperDraftPdfError,
            match="PDF_REFERENCE_MULTISET_INVALID",
        ):
            draft._semantic_postflight(
                config,
                sources,
                html,
                attacked.encode(),
                baseline.encode(),
                0,
            )
    for attacked in (
        baseline.replace(header, "", 1),
        baseline + " " + header,
        # A loose cell bag is not accepted as a full header-row signature.
        baseline.replace(header, header.replace(" ", " | ", 1), 1),
    ):
        with pytest.raises(PaperDraftPdfError, match="PDF_TABLE_HEADER_"):
            draft._semantic_postflight(
                config,
                sources,
                html,
                baseline.encode(),
                attacked.encode(),
                0,
            )


def test_table_header_repetition_requires_consecutive_page_top_copies(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    sources = draft._load_sources(config)
    html = render_sanitized_html(config, sources.manuscript)
    baseline = _synthetic_extracted_text(sources.manuscript).decode()
    headers = [str(value) for value in html.audit["table_header_probes"]]
    first = headers[0]

    baseline_key, character_offsets, _ = draft._whitespace_free_with_offsets(baseline)
    first_key = draft._semantic_text_key(first)
    first_offset = baseline_key.index(first_key)
    first_end = character_offsets[first_offset + len(first_key) - 1] + 1
    accepted = baseline[:first_end] + "\f" + first + baseline[first_end:]
    audit = draft._table_header_postflight(accepted, headers, 2)
    assert audit["headers"][0]["occurrence_count"] == 2
    assert audit["headers"][0]["pages"] == [1, 2]

    attacks = (
        (baseline + " " + first, 1, "PDF_TABLE_HEADER_PAGE_DUPLICATE"),
        (
            baseline + "\fcontinuation\f" + first,
            3,
            "PDF_TABLE_HEADER_REPEAT_PAGE_INVALID",
        ),
        (
            baseline + "\fcontinuation " + first,
            2,
            "PDF_TABLE_HEADER_REPEAT_OFFSET_INVALID",
        ),
    )
    for attacked, page_count, code in attacks:
        with pytest.raises(PaperDraftPdfError, match=code):
            draft._table_header_postflight(attacked, headers, page_count)

    split_index = character_offsets[first_offset + first_key.index("-")] + 1
    split_atom = baseline[:split_index] + "\n" + baseline[split_index:]
    with pytest.raises(
        PaperDraftPdfError,
        match="PDF_TABLE_HEADER_ATOMIC_TOKEN_SPLIT",
    ):
        draft._table_header_postflight(split_atom, headers, 1)

    split_continuation = baseline + "\f" + first.replace("-", "-\n", 1)
    with pytest.raises(
        PaperDraftPdfError,
        match="PDF_TABLE_HEADER_ATOMIC_TOKEN_SPLIT",
    ):
        draft._table_header_postflight(split_continuation, headers, 2)

    with pytest.raises(
        PaperDraftPdfError,
        match="PDF_TABLE_HEADER_INTERVAL_INTERLEAVING",
    ):
        draft._table_header_postflight("H1 H2\fH1", ["H1", "H2"], 2)


def test_table_numeric_tokens_use_exact_whole_surface_occurrence_counts(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    sources = draft._load_sources(_load(fixture))
    html = draft.HtmlArtifact(
        payload=(
            b"<!doctype html><html><head><title>x</title></head><body>"
            b'<p>136,629 narrative</p><table width="100%"><tbody>'
            b"<tr><td>136,629</td><td>0.1233</td></tr>"
            b"</tbody></table></body></html>"
        ),
        audit={"table_body_numeric_probes": ["0.1233", "136,629"]},
    )
    baseline = "136,629 narrative\n136,629 0.1233"
    audit = draft._table_numeric_token_postflight(sources, html, baseline)
    assert audit["probe_count"] == 2
    assert audit["records"][1] == {
        "token": "136,629",
        "source_occurrence_count": 2,
        "pdf_occurrence_count": 2,
    }

    attacks = (
        # The intact narrative occurrence cannot mask a split table value.
        "136,629 narrative\n136,62\n9 0.1233",
        baseline + "\n136,629",
        "136,629 narrative\n136,629 0.123\n3",
    )
    for attacked in attacks:
        with pytest.raises(
            PaperDraftPdfError,
            match="PDF_TABLE_NUMERIC_TOKEN_OCCURRENCE_INVALID",
        ):
            draft._table_numeric_token_postflight(sources, html, attacked)


def test_source_reference_and_header_collisions_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    sources = draft._load_sources(config)
    duplicate_reference = sources.manuscript.replace(
        "Reference Author 2, Synthetic Study 2.",
        "Reference Author 1, Synthetic Study 1.",
        1,
    )
    with pytest.raises(PaperDraftPdfError, match="MANUSCRIPT_REFERENCE_COLLISION"):
        draft._manuscript_semantics(config, duplicate_reference)

    duplicate_header = sources.manuscript.replace(
        "| 항목-2 | 값-2 |",
        "| 항목-1 | 값-1 |",
        1,
    )
    with pytest.raises(PaperDraftPdfError, match="MARKDOWN_TABLE_HEADER_COLLISION"):
        render_sanitized_html(config, duplicate_header)


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style/></svg>',
        (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<image href="https://example.com/x.png"/></svg>'
        ),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)"/></svg>'),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<rect fill="url(https://example.com/x)"/></svg>'
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<image href="javascript:alert(1)"/></svg>'
        ),
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg">\x01</svg>',
    ],
)
def test_svg_attack_matrix_is_rejected(payload: bytes) -> None:
    with pytest.raises(PaperDraftPdfError, match="COHORT_SVG"):
        draft._validate_cohort_svg(payload)


def test_network_trace_parser_allows_only_structural_af_unix() -> None:
    allowed = (
        b"111 socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0) = 3\n"
        b'111 connect(3, {sa_family=AF_UNIX, sun_path="/tmp/lo"}, 20) = 0\n'
        b'111 sendto(3, "harmless AF_INET payload", 24, 0, NULL, 0) = 24\n'
        b"111 accept(9, NULL, NULL <unfinished ...>\n"
        b"111 <... accept resumed>) = 10\n"
        b"111 --- SIGCHLD {si_signo=SIGCHLD, si_code=CLD_EXITED, "
        b"si_pid=222, si_uid=1000, si_status=0, si_utime=0, "
        b"si_stime=0} ---\n"
    )
    assert draft._network_trace_audit(allowed)["af_inet_count"] == 0
    for forbidden in (
        b"111 socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 3\n",
        b"111 socket(AF_INET6, SOCK_STREAM, IPPROTO_TCP) = 3\n",
        allowed + b"111 arbitrary-unparsed-network-line\n",
        allowed + b"111 sendto(3, AF_INET, 7, 0, NULL, 0) = 7\n",
        (
            b"333 recvfrom(4,  <unfinished ...>\n"
            b"333 <... recvfrom resumed>NULL, 0, 0, "
            b"{sa_family=AF_INET6}, [28]) = 0\n"
        ),
        (
            b"111 recvmsg(3, {msg_name=NULL, msg_namelen=0, "
            b'msg_iov=[{iov_base="x", iov_len=1}], msg_iovlen=1, '
            b"msg_control=[{cmsg_len=20, cmsg_level=SOL_SOCKET, "
            b"cmsg_type=SCM_RIGHTS, cmsg_data=[7]}], "
            b"msg_controllen=24, msg_flags=0}, 0) = 1\n"
        ),
    ):
        with pytest.raises(PaperDraftPdfError, match="NETWORK_"):
            draft._network_trace_audit(forbidden)


def test_pdf_url_parser_keeps_https_annotations_only() -> None:
    text = (
        "Page  Type          URL\n"
        "1     Annotation    https://example.com/a\n"
        "2     Annotation    https://example.org/b\n"
    )
    assert draft._parse_pdf_urls(text) == [
        "https://example.com/a",
        "https://example.org/b",
    ]
    for target in (
        "file:///tmp/local",
        "../relative.md",
        "/tmp/absolute",
        "http://example.com/insecure",
        "javascript:alert(1)",
    ):
        with pytest.raises(PaperDraftPdfError, match="PDF_URL_NOT_HTTPS"):
            draft._parse_pdf_urls(f"1 Annotation {target}\n")


def test_pdffonts_parser_accepts_poppler_24_separator_fixture() -> None:
    output = (
        "name                                 type              encoding"
        "         emb sub uni object ID\n"
        "------------------------------------ -----------------"
        " ---------------- --- --- --- ---------\n"
        "BAAAAA+NotoSansCJKkr-Bold            Type 1            Builtin"
        "          yes yes yes     10  0\n"
        "CAAAAA+NotoSansCJKkr-Regular         Type 1            Builtin"
        "          yes yes yes     15  0\n"
    )
    names, all_embedded, all_unicode = draft._parse_pdffonts(output)
    assert names == [
        "BAAAAA+NotoSansCJKkr-Bold",
        "CAAAAA+NotoSansCJKkr-Regular",
    ]
    assert all_embedded is True
    assert all_unicode is True
    _, all_embedded, all_unicode = draft._parse_pdffonts(
        output.replace(
            "yes yes yes     10",
            "yes yes no      10",
            1,
        )
    )
    assert all_embedded is True
    assert all_unicode is False


def test_lossless_libreoffice_pdf_canonicalizer_is_fixed_width_and_exact() -> None:
    first = _lo_classic_xref_pdf()
    second = _lo_classic_xref_pdf(
        date="D:20260729205114+09'00'",
        document_id="BA1293FC1025C66CB491C9D6C630B7AB",
        checksum="2BB9B98426172075FF19EC9D8C395363",
    )
    first_result = draft._canonicalize_libreoffice_pdf(
        first,
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    second_result = draft._canonicalize_libreoffice_pdf(
        second,
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    assert first_result == second_result
    assert len(first_result) == len(first) == len(second)
    assert b"/CreationDate" not in first_result
    assert b"/ID" not in first_result
    assert b"/DocChecksum" not in first_result
    xref_offset = int(first_result.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    assert first_result[xref_offset : xref_offset + 5] == b"xref\n"

    first_with_modification = _lo_classic_xref_pdf(
        modification_date="D:20260729205111+09'00'",
    )
    second_with_modification = _lo_classic_xref_pdf(
        date="D:20260729205114+09'00'",
        modification_date="D:20260729205114+09'00'",
        document_id="BA1293FC1025C66CB491C9D6C630B7AB",
        checksum="2BB9B98426172075FF19EC9D8C395363",
    )
    normalized_with_modification = draft._canonicalize_libreoffice_pdf(
        first_with_modification,
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    assert normalized_with_modification == draft._canonicalize_libreoffice_pdf(
        second_with_modification,
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    assert b"/ModDate" not in normalized_with_modification
    utc_result = draft._canonicalize_libreoffice_pdf(
        _lo_classic_xref_pdf(date="D:20260729133751Z'"),
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    assert len(utc_result) == len(_lo_classic_xref_pdf(date="D:20260729133751Z'"))
    assert b"/CreationDate" not in utc_result


def test_lossless_libreoffice_pdf_canonicalizer_rejects_malformed_contract() -> None:
    valid = _lo_classic_xref_pdf()
    attacks = (
        valid.replace(b"/CreationDate", b"/CreateXDate", 1),
        valid.replace(b"/ID [", b"/IX [", 1),
        valid.replace(b"/DocChecksum", b"/DocChecksuX", 1),
        valid.replace(
            b"/CreationDate(",
            b"/CreationDate(D:20260729205111+09'00')\n/CreationDate(",
            1,
        ),
        valid.replace(
            b"70C6ED7B44B812BF3770C02932875125> ]",
            b"00000000000000000000000000000000> ]",
            1,
        ),
        valid.replace(
            b"70C6ED7B44B812BF3770C02932875125",
            b"70c6ed7b44b812bf3770c02932875125",
            1,
        ),
        _lo_classic_xref_pdf(extra_info=b"\n/Author<FEFF0041>"),
        valid.replace(b"startxref\n", b"startxref\n1", 1),
        valid.replace(b"D:20260729205111+09'00'", b"D:2026072920511+09'00'", 1),
    )
    for attacked in attacks:
        with pytest.raises(PaperDraftPdfError, match="LO_PDF_"):
            draft._canonicalize_libreoffice_pdf(
                attacked,
                expected_title="Synthetic Korean L2 Paper",
                expected_producer="LibreOffice 24.2",
            )
    with pytest.raises(PaperDraftPdfError, match="LO_PDF_INFO_VALUE_INVALID"):
        draft._canonicalize_libreoffice_pdf(
            valid,
            expected_title="Wrong title",
            expected_producer="LibreOffice 24.2",
        )
    with pytest.raises(PaperDraftPdfError, match="LO_PDF_INFO_VALUE_INVALID"):
        draft._canonicalize_libreoffice_pdf(
            valid,
            expected_title="Synthetic Korean L2 Paper",
            expected_producer="Wrong producer",
        )

    # A stable, non-volatile byte is deliberately preserved.  The caller's
    # independent two-build equality gate therefore detects such drift.
    first = draft._canonicalize_libreoffice_pdf(
        _lo_classic_xref_pdf(stable_prefix=b"stable=A"),
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    second = draft._canonicalize_libreoffice_pdf(
        _lo_classic_xref_pdf(stable_prefix=b"stable=B"),
        expected_title="Synthetic Korean L2 Paper",
        expected_producer="LibreOffice 24.2",
    )
    assert first != second


def test_pdfinfo_numbered_page_box_fixture_is_required() -> None:
    numbered = (
        "Pages:           2\n"
        "Page    1 size:  595.276 x 841.89 pts (A4)\n"
        "Page    1 rot:   0\n"
        "Page    1 MediaBox: 0.00 0.00 595.28 841.89\n"
        "Page    1 CropBox: 0.00 0.00 595.28 841.89\n"
        "Page    1 BleedBox: 0.00 0.00 595.28 841.89\n"
        "Page    1 TrimBox: 0.00 0.00 595.28 841.89\n"
        "Page    1 ArtBox: 0.00 0.00 595.28 841.89\n"
        "Page    2 size:  595.276 x 841.89 pts (A4)\n"
        "Page    2 rot:   0\n"
        "Page    2 MediaBox: 0.00 0.00 595.28 841.89\n"
        "Page    2 CropBox: 0.00 0.00 595.28 841.89\n"
        "Page    2 BleedBox: 0.00 0.00 595.28 841.89\n"
        "Page    2 TrimBox: 0.00 0.00 595.28 841.89\n"
        "Page    2 ArtBox: 0.00 0.00 595.28 841.89\n"
    )
    assert draft._parse_pdf_page_sizes(numbered, 2) == [
        (1, 595.276, 841.89),
        (2, 595.276, 841.89),
    ]
    with pytest.raises(PaperDraftPdfError, match="PDF_PAGE_GEOMETRY_SET_INVALID"):
        draft._parse_pdf_page_sizes(
            "Pages: 2\nPage size: 595.276 x 841.89 pts (A4)\n",
            2,
        )
    geometry = draft._parse_pdf_page_geometry(numbered, 2)
    draft._validate_a4_page_geometry(geometry, 2)
    rotated = numbered.replace("Page    1 rot:   0", "Page    1 rot:   180")
    with pytest.raises(PaperDraftPdfError, match="PDF_PAGE_NOT_A4"):
        draft._validate_a4_page_geometry(
            draft._parse_pdf_page_geometry(rotated, 2),
            2,
        )
    clipped = numbered.replace(
        "Page    2 TrimBox: 0.00 0.00 595.28 841.89",
        "Page    2 TrimBox: 0.00 0.00 100.00 100.00",
    )
    with pytest.raises(PaperDraftPdfError, match="PDF_PAGE_BOX_NOT_A4"):
        draft._validate_a4_page_geometry(
            draft._parse_pdf_page_geometry(clipped, 2),
            2,
        )


def test_cohort_figure_palette_and_bbox_contract() -> None:
    positive = _png()
    width, height, _, pixels = draft._png_decode(positive)
    audit = draft._cohort_figure_audit([(1, width, height, pixels)])
    assert audit["status"] == "passed"
    assert audit["page"] == 1
    assert set(audit["color_pixel_counts"]) == set(draft.COHORT_PALETTE)
    with pytest.raises(
        PaperDraftPdfError,
        match="COHORT_RASTER_PALETTE_PAGE_INVALID",
    ):
        draft._cohort_figure_audit(
            [(1, width, height, pixels), (2, width, height, pixels)]
        )

    missing = _png(included_colors=frozenset({0, 1, 2}))
    width, height, _, pixels = draft._png_decode(missing)
    with pytest.raises(
        PaperDraftPdfError,
        match="COHORT_RASTER_PALETTE_PAGE_INVALID",
    ):
        draft._cohort_figure_audit([(1, width, height, pixels)])

    first = _png(included_colors=frozenset({0, 1}))
    second = _png(included_colors=frozenset({2, 3}))
    first_width, first_height, _, first_pixels = draft._png_decode(first)
    second_width, second_height, _, second_pixels = draft._png_decode(second)
    with pytest.raises(
        PaperDraftPdfError,
        match="COHORT_RASTER_PALETTE_PAGE_INVALID",
    ):
        draft._cohort_figure_audit(
            [
                (1, first_width, first_height, first_pixels),
                (2, second_width, second_height, second_pixels),
            ]
        )

    shrunk = _png(width=256, height=256)
    width, height, _, pixels = draft._png_decode(shrunk)
    with pytest.raises(PaperDraftPdfError, match="COHORT_RASTER_BBOX_INVALID"):
        draft._cohort_figure_audit([(1, width, height, pixels)])


def test_blank_page_two_signal_visibility_contract() -> None:
    assert draft._blank_page_signals(1.0, " \n", 0.9999) == (
        True,
        True,
        True,
        "blank",
    )
    with pytest.raises(
        PaperDraftPdfError,
        match="PDF_TEXT_RASTER_VISIBILITY_MISMATCH",
    ):
        draft._blank_page_signals(1.0, "invisible text", 0.9999)
    assert draft._blank_page_signals(0.99605573, "sparse text", 0.9999) == (
        False,
        False,
        False,
        "visible_text",
    )
    assert draft._blank_page_signals(0.95, "\f", 0.9999) == (
        False,
        True,
        False,
        "image_only",
    )


def test_ghostscript_xmp_is_exactly_allowlisted_and_date_free() -> None:
    title = "Synthetic Korean L2 Paper"
    producer = "GPL Ghostscript 10.02.1"
    xmp = f"""<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>
<?adobe-xap-filters esc="CRLF"?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='{draft.XMP_TOOLKIT}'>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#' xmlns:iX='http://ns.adobe.com/iX/1.0/'>
<rdf:Description rdf:about="" xmlns:pdf='http://ns.adobe.com/pdf/1.3/' pdf:Producer='{producer}'/>
<rdf:Description rdf:about="" xmlns:xmp='http://ns.adobe.com/xap/1.0/'><xmp:CreatorTool>{draft.XMP_CREATOR_TOOL}</xmp:CreatorTool></rdf:Description>
<rdf:Description rdf:about="" xmlns:xapMM='http://ns.adobe.com/xap/1.0/mm/' xapMM:DocumentID='uuid:5da5a840-1ec1-11fc-0000-f2e2554d57ff'/>
<rdf:Description rdf:about="" xmlns:dc='http://purl.org/dc/elements/1.1/' dc:format='application/pdf'><dc:title><rdf:Alt><rdf:li xml:lang='x-default'>{title}</rdf:li></rdf:Alt></dc:title></rdf:Description>
</rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>
""".encode()
    audit = draft._parse_xmp_metadata(
        xmp,
        expected_title=title,
        expected_producer=producer,
    )
    assert audit["date_fields_present"] is False
    assert audit["sha256"] == hashlib.sha256(xmp).hexdigest()
    for attacked in (
        xmp.replace(title.encode(), b"Wrong title"),
        xmp.replace(
            b"</rdf:RDF>",
            b"<xmp:CreateDate>2026-01-01</xmp:CreateDate></rdf:RDF>",
        ),
        xmp.replace(
            b"<rdf:RDF ",
            b"<!-- SECRET_LOCAL_PATH /home/user/.env.local --><rdf:RDF ",
            1,
        ),
        xmp.replace(
            b"<dc:title>",
            b"<dc:title>SECRET_LOCAL_PATH",
            1,
        ),
        xmp.replace(
            b"<dc:title>",
            b"<dc:title rdf:parseType='Resource'>",
            1,
        ),
        xmp.replace(
            b"<rdf:Alt>",
            b"<rdf:Alt rdf:ID='evil'>",
            1,
        ),
        xmp.replace(
            b"<rdf:RDF ",
            b"<?evil secret='true'?><rdf:RDF ",
            1,
        ),
        xmp.replace(
            b"xmlns:x='adobe:ns:meta/'",
            (b"xmlns:x='adobe:ns:meta/' xmlns:evil='file:///home/user/.env.local'"),
            1,
        ),
        b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>" + xmp,
    ):
        with pytest.raises(PaperDraftPdfError, match="PDF_XMP"):
            draft._parse_xmp_metadata(
                attacked,
                expected_title=title,
                expected_producer=producer,
            )


def test_pdfinfo_dates_are_absent_or_fixed_to_source_epoch() -> None:
    epoch = 1767225600
    assert draft._pdfinfo_fixed_date_status("", "CreationDate", epoch) == "absent"
    assert (
        draft._pdfinfo_fixed_date_status(
            "CreationDate: Thu Jan  1 00:00:00 2026 UTC\n",
            "CreationDate",
            epoch,
        )
        == "fixed_source_date_epoch"
    )
    with pytest.raises(PaperDraftPdfError, match="PDFINFO_METADATA_DATE_INVALID"):
        draft._pdfinfo_fixed_date_status(
            "CreationDate: Wed Jul 29 10:00:00 2026 UTC\n",
            "CreationDate",
            epoch,
        )


def test_synthetic_publish_is_deterministic_and_deep_valid(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    implementation = _implementation(config)
    backend = FakeBackend()
    first = build_and_publish(
        config,
        implementation=implementation,
        backend=backend,
    )
    second = build_and_publish(
        _load(fixture),
        implementation=_implementation(_load(fixture)),
        backend=backend,
    )
    assert first == second
    assert first["claims"] == {
        "draft": True,
        "final": False,
        "visual_review_status": "pending",
    }
    output = tmp_path / fixture.namespace.output_root
    assert stat.S_IMODE(output.stat().st_mode) == 0o750
    assert stat.S_IMODE((output / "generations").stat().st_mode) == 0o750
    generation = output / "generations" / first["generation_id"]
    assert stat.S_IMODE(generation.stat().st_mode) == 0o550
    assert stat.S_IMODE((generation / "pages").stat().st_mode) == 0o550
    for path in [
        output / "commit.json",
        generation / "draft.pdf",
        generation / "extracted-layout.txt",
        generation / "extracted-semantic.txt",
        generation / "extracted-table.txt",
        generation / "postflight.json",
        generation / "receipt.json",
        generation / "pages" / "page-001.png",
    ]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o440
        assert path.stat().st_nlink == 1
    assert (
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=backend,
        )
        == first
    )


def test_publish_enforces_exact_modes_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    previous_umask = os.umask(0o077)
    try:
        commit = build_and_publish(
            config,
            implementation=_implementation(config),
            backend=FakeBackend(),
        )
    finally:
        os.umask(previous_umask)
    output = tmp_path / fixture.namespace.output_root
    generation = output / "generations" / str(commit["generation_id"])
    assert stat.S_IMODE(output.stat().st_mode) == 0o750
    assert stat.S_IMODE((output / "generations").stat().st_mode) == 0o750
    assert stat.S_IMODE(generation.stat().st_mode) == 0o550
    assert stat.S_IMODE((generation / "pages").stat().st_mode) == 0o550


def test_deep_validation_never_runs_tool_version_subprocesses(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    expected = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    assert (
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=PostflightCallbackBackend(lambda: None),
        )
        == expected
    )


@pytest.mark.parametrize(
    "target_name",
    [
        "config",
        "manuscript",
        "module",
        "cli",
        "tool",
        "commit",
        "receipt",
        "draft",
        "page",
        "tree",
    ],
)
def test_deep_tail_revalidation_rejects_late_rebind_and_tamper(
    tmp_path: Path,
    target_name: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    output = tmp_path / fixture.namespace.output_root
    generation = output / "generations" / str(commit["generation_id"])
    targets = {
        "config": fixture.config_path,
        "manuscript": (tmp_path / fixture.namespace.input_paths["manuscript"]),
        "module": tmp_path / fixture.namespace.module_path,
        "cli": tmp_path / fixture.namespace.cli_path,
        "tool": Path(fixture.namespace.tool_paths["ghostscript"]),
        "commit": output / "commit.json",
        "receipt": generation / "receipt.json",
        "draft": generation / "draft.pdf",
        "page": generation / "pages" / "page-001.png",
    }
    validate_config = _load(fixture)
    implementation = _implementation(validate_config)

    def attack() -> None:
        if target_name == "tree":
            pages = generation / "pages"
            pages.chmod(0o750)
            extra = pages / "page-002.png"
            extra.write_bytes(_png())
            extra.chmod(0o440)
            pages.chmod(0o550)
            return
        target = targets[target_name]
        parent_mode = stat.S_IMODE(target.parent.stat().st_mode)
        target.parent.chmod(parent_mode | 0o200)
        _replace_inode_with_same_bytes(target)
        target.parent.chmod(parent_mode)

    with pytest.raises(PaperDraftPdfError):
        validate_committed(
            validate_config,
            implementation=implementation,
            backend=PostflightCallbackBackend(attack),
        )


def test_commit_receipt_size_is_exactly_bound(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    commit_path = tmp_path / fixture.namespace.commit_path
    value = json.loads(commit_path.read_text(encoding="utf-8"))
    value["receipt"]["size_bytes"] += 1
    without_binding = dict(value)
    without_binding.pop("binding_sha256")
    value["binding_sha256"] = hashlib.sha256(
        draft._canonical_json(without_binding)
    ).hexdigest()
    commit_path.chmod(0o640)
    commit_path.write_bytes(draft._canonical_json(value))
    commit_path.chmod(0o440)
    with pytest.raises(
        PaperDraftPdfError,
        match="RECEIPT_GENERATION_BINDING_INVALID",
    ):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


def test_output_guard_checks_rebind_on_exception_boundary(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    with pytest.raises(
        PaperDraftPdfError,
        match="OUTPUT_BOUNDARY_REVALIDATION_FAILED.*REBOUND",
    ):
        with draft._output_guard(config, create=True):
            parent = tmp_path / "outputs"
            parent.rename(tmp_path / "outputs-original")
            parent.mkdir(mode=0o750)
            raise PaperDraftPdfError("SYNTHETIC_BODY_FAILURE")


@pytest.mark.parametrize("scope", ["generation", "pages"])
def test_rollback_child_rebind_never_deletes_attacker_replacement(
    tmp_path: Path,
    scope: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    output = tmp_path / fixture.namespace.output_root
    first = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=VariantBackend("rollback-previous"),
    )
    previous_commit = (output / "commit.json").read_bytes()

    class RebindOnDeepBackend(VariantBackend):
        def __init__(self) -> None:
            super().__init__("rollback-replacement")
            self.builder_inode = -1
            self.attacker_inode = -1

        def postflight(
            self,
            config: RenderConfig,
            pdf: bytes,
            sources: draft.SourceBundle,
            html: draft.HtmlArtifact,
            tools: Mapping[str, draft.ToolSnapshot],
            work: Path,
        ) -> PostflightResult:
            result = super().postflight(
                config,
                pdf,
                sources,
                html,
                tools,
                work,
            )
            if self.postflight_count != 2:
                return result
            generations = output / "generations"
            generation = next(
                path for path in generations.iterdir() if len(path.name) == 64
            )
            if scope == "generation":
                self.builder_inode = generation.stat().st_ino
                generation.rename(generations / ".escaped-original")
                generation.mkdir(mode=0o750)
                self.attacker_inode = generation.stat().st_ino
                (generation / "attacker-marker").write_bytes(b"ATTACKER")
                generation.chmod(0o550)
            else:
                pages = generation / "pages"
                self.builder_inode = pages.stat().st_ino
                generation.chmod(0o750)
                pages.rename(generation / ".escaped-pages")
                pages.mkdir(mode=0o750)
                self.attacker_inode = pages.stat().st_ino
                (pages / "attacker-marker").write_bytes(b"ATTACKER")
                pages.chmod(0o550)
                generation.chmod(0o550)
            raise PaperDraftPdfError("SYNTHETIC_DEEP_REBIND_FAILURE")

    backend = RebindOnDeepBackend()
    with pytest.raises(PaperDraftPdfError, match="PUBLISH_ROLLBACK_FAILED"):
        build_and_publish(
            config,
            implementation=_implementation(config),
            backend=backend,
        )
    assert (output / "commit.json").read_bytes() == previous_commit
    assert {path.name for path in (output / "generations").iterdir()} == {
        first["generation_id"]
    }
    history = tmp_path / "outputs" / "paper_draft_pdf_history_unvalidated"
    observed_inodes = {
        path.stat().st_ino
        for path in (tmp_path / "outputs").rglob("*")
        if not path.is_symlink()
    }
    assert backend.builder_inode not in observed_inodes
    quarantined = [
        path
        for path in history.iterdir()
        if path.stat().st_ino == backend.attacker_inode
    ]
    assert len(quarantined) == 1
    assert (quarantined[0] / "attacker-marker").read_bytes() == b"ATTACKER"


def test_run_checked_timeout_kills_entire_process_group(tmp_path: Path) -> None:
    environment = draft._fixed_environment(tmp_path, 1767225600)
    command = [
        "/bin/sh",
        "-c",
        "/bin/sleep 30 & echo $! > child.pid; wait",
    ]
    started = time.monotonic()
    with pytest.raises(PaperDraftPdfError, match="PROCESS_GROUP_TIMEOUT"):
        draft._run_checked(
            command,
            timeout_seconds=0.1,
            environment=environment,
            working_directory=tmp_path,
            code="PROCESS_GROUP",
        )
    assert time.monotonic() - started < 3.0
    child_pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_run_checked_replaces_inherited_network_stdin_with_devnull(
    tmp_path: Path,
) -> None:
    environment = draft._fixed_environment(tmp_path, 1767225600)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(listener.getsockname())
    connection, _ = listener.accept()
    client.sendall(b"NETWORK_DATA\n")
    saved_stdin = os.dup(0)
    try:
        os.dup2(connection.fileno(), 0)
        completed = draft._run_checked(
            [
                "/bin/sh",
                "-c",
                "if read value; then printf 'TRANSFER:%s' \"$value\"; "
                "else printf 'EOF'; fi",
            ],
            timeout_seconds=1.0,
            environment=environment,
            working_directory=tmp_path,
            code="NETWORK_STDIN",
        )
    finally:
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)
        connection.close()
        client.close()
        listener.close()
    assert completed.stdout == b"EOF"
    assert b"NETWORK_DATA" not in completed.stdout


@pytest.mark.parametrize("return_code", [0, 7])
def test_run_checked_cleans_redirected_orphan_on_every_exit(
    tmp_path: Path,
    return_code: int,
) -> None:
    environment = draft._fixed_environment(tmp_path, 1767225600)
    command = [
        "/bin/sh",
        "-c",
        (f"/bin/sleep 30 >/dev/null 2>&1 & echo $! > child.pid; exit {return_code}"),
    ]
    if return_code:
        with pytest.raises(PaperDraftPdfError, match="ORPHAN_FAILED"):
            draft._run_checked(
                command,
                timeout_seconds=1.0,
                environment=environment,
                working_directory=tmp_path,
                code="ORPHAN",
            )
    else:
        draft._run_checked(
            command,
            timeout_seconds=1.0,
            environment=environment,
            working_directory=tmp_path,
            code="ORPHAN",
        )
    child_pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_strace_kill_on_exit_cleans_detached_tracee(tmp_path: Path) -> None:
    environment = draft._fixed_environment(tmp_path, 1767225600)
    with pytest.raises(PaperDraftPdfError, match="DETACHED_TRACE_TIMEOUT"):
        draft._run_file_traced_checked(
            [
                "/bin/sh",
                "-c",
                (
                    "/usr/bin/setsid /bin/sleep 30 >/dev/null 2>&1 & "
                    "echo $! > detached.pid; wait"
                ),
            ],
            strace_path="/usr/bin/strace",
            trace_path=tmp_path / "detached.file.strace",
            timeout_seconds=0.2,
            environment=environment,
            working_directory=tmp_path,
            workspace_root=tmp_path / "workspace-not-read",
            code="DETACHED_TRACE",
        )
    child_pid = int((tmp_path / "detached.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_combined_file_and_network_trace_uses_one_strace(tmp_path: Path) -> None:
    environment = draft._fixed_environment(tmp_path, 1767225600)
    completed, network = draft._run_combined_traced_checked(
        ["/bin/true"],
        strace_path="/usr/bin/strace",
        trace_path=tmp_path / "combined.strace",
        timeout_seconds=2.0,
        environment=environment,
        working_directory=tmp_path,
        workspace_root=tmp_path / "workspace-not-read",
        code="COMBINED",
    )
    assert completed.returncode == 0
    assert network["af_inet_count"] == 0
    assert network["af_inet6_count"] == 0


def test_combined_trace_rejects_network_hidden_from_file_only_trace(
    tmp_path: Path,
) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    environment = draft._fixed_environment(tmp_path, 1767225600)
    try:
        with pytest.raises(
            PaperDraftPdfError,
            match="NETWORK_FAMILY_FORBIDDEN",
        ):
            draft._run_offline_traced_checked(
                [
                    "/usr/bin/python3",
                    "-c",
                    (
                        "import socket;"
                        "s=socket.socket();"
                        f"s.connect(('127.0.0.1',{port}));"
                        "s.sendall(b'NETWORK_FROM_COMBINED')"
                    ),
                ],
                strace_path="/usr/bin/strace",
                trace_path=tmp_path / "network.combined.strace",
                timeout_seconds=2.0,
                environment=environment,
                working_directory=tmp_path,
                workspace_root=tmp_path / "workspace-not-read",
                code="NETWORK_COMBINED",
            )
    finally:
        listener.close()


def test_file_trace_audit_rejects_workspace_home_and_secrets(
    tmp_path: Path,
) -> None:
    isolation = tmp_path / "isolated"
    isolation.mkdir()
    safe = (
        f'1 openat(AT_FDCWD, "{isolation}/input.pdf", O_RDONLY) = 3\n'
        '1 openat(AT_FDCWD, "/usr/lib/libc.so.6", O_RDONLY) = 3\n'
    ).encode()
    assert (
        draft._file_trace_audit(
            safe,
            workspace_root=tmp_path / "workspace",
            isolation_root=isolation,
        )["workspace_access_count"]
        == 0
    )
    attacks = [
        tmp_path / "workspace" / "manuscript.md",
        Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / "secret",
        Path("/tmp/.env.local"),
        Path("/srv/restricted/raw.bin"),
    ]
    for path in attacks:
        trace = f'1 openat(AT_FDCWD, "{path}", O_RDONLY) = 3\n'.encode()
        with pytest.raises(PaperDraftPdfError, match="FILE_TRACE_FORBIDDEN"):
            draft._file_trace_audit(
                trace,
                workspace_root=tmp_path / "workspace",
                isolation_root=isolation,
            )


@pytest.mark.parametrize(
    "stage",
    [
        "inspect",
        "build-1",
        "build-2",
        "normalize-1",
        "normalize-2",
        "postflight",
        "deep-postflight",
    ],
)
def test_backend_stage_faults_leave_no_partial_commit(
    tmp_path: Path,
    stage: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    with pytest.raises(PaperDraftPdfError, match="SYNTHETIC_"):
        build_and_publish(
            config,
            implementation=_implementation(config),
            backend=VariantBackend("fault", fail_stage=stage),
        )
    _assert_no_committed_output(fixture)


def test_html_stage_fault_leaves_no_partial_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)

    def fail_html(_: RenderConfig, __: str) -> draft.HtmlArtifact:
        raise PaperDraftPdfError("SYNTHETIC_HTML_FAILED")

    monkeypatch.setattr(draft, "render_sanitized_html", fail_html)
    with pytest.raises(PaperDraftPdfError, match="SYNTHETIC_HTML_FAILED"):
        build_and_publish(
            config,
            implementation=_implementation(config),
            backend=FakeBackend(),
        )
    _assert_no_committed_output(fixture)


def test_mismatched_normalized_builds_are_never_published(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    with pytest.raises(
        PaperDraftPdfError,
        match="NORMALIZED_PDF_NOT_DETERMINISTIC",
    ):
        build_and_publish(
            config,
            implementation=_implementation(config),
            backend=NondeterministicBackend("mismatch"),
        )
    _assert_no_committed_output(fixture)


@pytest.mark.parametrize(
    ("target_name", "fail_after_replace"),
    [
        ("page-001.png", False),
        ("draft.pdf", False),
        ("extracted-layout.txt", False),
        ("extracted-semantic.txt", False),
        ("extracted-table.txt", False),
        ("postflight.json", False),
        ("receipt.json", False),
        ("commit.json", False),
        ("commit.json", True),
    ],
)
def test_publish_write_faults_roll_back_every_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
    fail_after_replace: bool,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    original = draft._write_file_at
    failed = False

    def failing_write(
        descriptor: int,
        name: str,
        payload: bytes,
        *,
        mode: int,
    ) -> None:
        nonlocal failed
        if name == target_name and not failed:
            failed = True
            if fail_after_replace:
                original(descriptor, name, payload, mode=mode)
            raise PaperDraftPdfError("SYNTHETIC_WRITE_FAULT", name)
        original(descriptor, name, payload, mode=mode)

    monkeypatch.setattr(draft, "_write_file_at", failing_write)
    with pytest.raises(PaperDraftPdfError, match="SYNTHETIC_WRITE_FAULT"):
        build_and_publish(
            config,
            implementation=_implementation(config),
            backend=FakeBackend(),
        )
    assert failed
    _assert_no_committed_output(fixture)


def test_failed_republish_restores_previous_commit_byte_for_byte(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    first = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=VariantBackend("previous"),
    )
    output = tmp_path / fixture.namespace.output_root
    commit_path = output / "commit.json"
    previous_commit = commit_path.read_bytes()
    previous_generations = {path.name for path in (output / "generations").iterdir()}
    with pytest.raises(
        PaperDraftPdfError,
        match="SYNTHETIC_DEEP_POSTFLIGHT_FAILED",
    ):
        build_and_publish(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=VariantBackend("replacement", fail_stage="deep-postflight"),
        )
    assert commit_path.read_bytes() == previous_commit
    assert (
        {path.name for path in (output / "generations").iterdir()}
        == previous_generations
        == {first["generation_id"]}
    )
    history = tmp_path / "outputs" / "paper_draft_pdf_history_unvalidated"
    assert list(history.iterdir()) == []


@pytest.mark.parametrize(
    "relative",
    [
        "draft.pdf",
        "extracted-layout.txt",
        "extracted-semantic.txt",
        "extracted-table.txt",
        "postflight.json",
        "receipt.json",
        "pages/page-001.png",
    ],
)
def test_deep_validation_rejects_every_artifact_byte_tamper(
    tmp_path: Path,
    relative: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    generation = (
        tmp_path
        / fixture.namespace.output_root
        / "generations"
        / str(commit["generation_id"])
    )
    target = generation / relative
    payload = bytearray(target.read_bytes())
    payload[-1] ^= 1
    target.chmod(0o640)
    target.write_bytes(payload)
    target.chmod(0o440)
    with pytest.raises(PaperDraftPdfError):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "generations",
        "generations/{generation}",
        "generations/{generation}/pages",
        "commit.json",
        "generations/{generation}/draft.pdf",
        "generations/{generation}/pages/page-001.png",
    ],
)
def test_deep_validation_rejects_relaxed_modes(
    tmp_path: Path,
    relative: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    output = tmp_path / fixture.namespace.output_root
    target = output / relative.format(generation=commit["generation_id"])
    target.chmod(stat.S_IMODE(target.stat().st_mode) | 0o022)
    with pytest.raises(PaperDraftPdfError):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


@pytest.mark.parametrize(
    "relative",
    [
        "commit.json",
        "generations/{generation}/draft.pdf",
        "generations/{generation}/pages/page-001.png",
    ],
)
def test_deep_validation_rejects_output_hardlinks(
    tmp_path: Path,
    relative: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    output = tmp_path / fixture.namespace.output_root
    target = output / relative.format(generation=commit["generation_id"])
    os.link(target, tmp_path / f"alias-{target.name}")
    with pytest.raises(PaperDraftPdfError, match="METADATA_INVALID"):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


@pytest.mark.parametrize(
    "relative",
    [
        "commit.json",
        "generations/{generation}",
        "generations/{generation}/pages",
        "generations/{generation}/draft.pdf",
        "generations/{generation}/pages/page-001.png",
    ],
)
def test_deep_validation_rejects_output_symlinks(
    tmp_path: Path,
    relative: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    output = tmp_path / fixture.namespace.output_root
    target = output / relative.format(generation=commit["generation_id"])
    backup = target.with_name(f".symlink-target-{target.name}")
    parent_mode = stat.S_IMODE(target.parent.stat().st_mode)
    target.parent.chmod(0o750)
    target.rename(backup)
    target.symlink_to(backup)
    target.parent.chmod(parent_mode)
    with pytest.raises(PaperDraftPdfError):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-commit",
        "missing-generation",
        "missing-pdf",
        "missing-page",
        "extra-root",
        "extra-generation",
        "extra-generation-file",
        "extra-page",
    ],
)
def test_deep_validation_rejects_missing_and_extra_tree_entries(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    output = tmp_path / fixture.namespace.output_root
    generations = output / "generations"
    generation = generations / str(commit["generation_id"])
    pages = generation / "pages"
    if mutation == "missing-commit":
        output.chmod(0o750)
        (output / "commit.json").unlink()
    elif mutation == "missing-generation":
        generation.rename(generations / ".removed-generation")
    elif mutation == "missing-pdf":
        generation.chmod(0o750)
        (generation / "draft.pdf").unlink()
        generation.chmod(0o550)
    elif mutation == "missing-page":
        pages.chmod(0o750)
        (pages / "page-001.png").unlink()
        pages.chmod(0o550)
    elif mutation == "extra-root":
        extra = output / "unexpected.txt"
        extra.write_bytes(b"x")
        extra.chmod(0o440)
    elif mutation == "extra-generation":
        extra = generations / ("f" * 64)
        extra.mkdir(mode=0o550)
    elif mutation == "extra-generation-file":
        generation.chmod(0o750)
        extra = generation / "unexpected.txt"
        extra.write_bytes(b"x")
        extra.chmod(0o440)
        generation.chmod(0o550)
    elif mutation == "extra-page":
        pages.chmod(0o750)
        extra = pages / "page-002.png"
        extra.write_bytes(_png())
        extra.chmod(0o440)
        pages.chmod(0o550)
    else:
        raise AssertionError(mutation)
    with pytest.raises(PaperDraftPdfError):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


def test_two_concurrent_writers_commit_one_consistent_generation(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def publish(_: int) -> Mapping[str, Any]:
        config = _load(fixture)
        return build_and_publish(
            config,
            implementation=_implementation(config),
            backend=FakeBackend(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    assert results[0] == results[1]
    generations = list(
        (tmp_path / fixture.namespace.output_root / "generations").iterdir()
    )
    assert len(generations) == 1


def test_forged_self_consistent_historical_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    commit = build_and_publish(
        config,
        implementation=_implementation(config),
        backend=FakeBackend(),
    )
    generations = tmp_path / fixture.namespace.output_root / "generations"
    current = generations / str(commit["generation_id"])
    forged_staging = generations / ("f" * 64)
    shutil.copytree(current, forged_staging)
    forged_staging.chmod(0o750)
    receipt_path = forged_staging / "receipt.json"
    receipt_path.chmod(0o640)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["forged_unvalidated_history_field"] = True
    forged_payload = draft._canonical_json(receipt)
    receipt_path.write_bytes(forged_payload)
    receipt_path.chmod(0o440)
    forged_staging.chmod(0o550)
    forged = generations / hashlib.sha256(forged_payload).hexdigest()
    forged_staging.rename(forged)
    with pytest.raises(
        PaperDraftPdfError,
        match="CANONICAL_GENERATION_COUNT_INVALID",
    ):
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=FakeBackend(),
        )


def test_prior_config_generation_moves_to_unvalidated_retention(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first_config = _load(fixture)
    first = build_and_publish(
        first_config,
        implementation=_implementation(first_config),
        backend=VariantBackend("prior-config"),
    )
    text = fixture.config_path.read_text(encoding="utf-8")
    fixture.config_path.write_text(
        text.replace(
            "source_date_epoch: 1767225600",
            "source_date_epoch: 1767225601",
            1,
        ),
        encoding="utf-8",
    )
    second_config = _load(fixture)
    second = build_and_publish(
        second_config,
        implementation=_implementation(second_config),
        backend=VariantBackend("current-config"),
    )
    output = tmp_path / fixture.namespace.output_root
    assert {path.name for path in (output / "generations").iterdir()} == {
        second["generation_id"]
    }
    history = tmp_path / "outputs" / "paper_draft_pdf_history_unvalidated"
    assert {path.name for path in history.iterdir()} == {first["generation_id"]}
    assert (
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=VariantBackend("current-config"),
        )
        == second
    )


def test_two_divergent_concurrent_writers_never_cross_bind_receipts(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def publish(index: int) -> tuple[int, Mapping[str, Any]]:
        config = _load(fixture)
        return (
            index,
            build_and_publish(
                config,
                implementation=_implementation(config),
                backend=VariantBackend(f"writer-{index}"),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    by_generation = {str(commit["generation_id"]): index for index, commit in results}
    assert len(by_generation) == 2
    output = tmp_path / fixture.namespace.output_root
    final_commit = json.loads((output / "commit.json").read_text(encoding="utf-8"))
    final_generation = str(final_commit["generation_id"])
    generation_names = {path.name for path in (output / "generations").iterdir()}
    assert generation_names == {final_generation}
    history = tmp_path / "outputs" / "paper_draft_pdf_history_unvalidated"
    retained_names = {path.name for path in history.iterdir()}
    assert retained_names == set(by_generation) - {final_generation}
    for generation_id, root in (
        (final_generation, output / "generations"),
        *[(name, history) for name in retained_names],
    ):
        receipt = (root / generation_id / "receipt.json").read_bytes()
        assert hashlib.sha256(receipt).hexdigest() == generation_id
    assert final_generation in by_generation
    final_writer = by_generation[final_generation]
    assert (
        validate_committed(
            _load(fixture),
            implementation=_implementation(_load(fixture)),
            backend=VariantBackend(f"writer-{final_writer}"),
        )
        == final_commit
    )
