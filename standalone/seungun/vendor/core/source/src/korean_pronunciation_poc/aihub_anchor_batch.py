from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .aihub_anchor import AnchorRecord, NormalizationStatus
from .config import PocConfig
from .errors import AnalysisStatus, ErrorCode, PipelineFailure
from .pipeline import AnalysisPipeline, PipelineRunResult
from .schemas import AnalysisRequest, AnalysisResult, Sha256


_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NON_SPEECH_LABELS = {"", "sil", "sp", "noise"}

BATCH_SUMMARY_COLUMNS = (
    "sample_id",
    "speaker_id",
    "job",
    "sex",
    "age",
    "script_id",
    "normalized_script",
    "analysis_status",
    "actual_word_label_count",
    "actual_phone_label_count",
    "empty_word_interval_count",
    "empty_phone_interval_count",
    "syllable_count",
    "utterance_duration",
    "speaking_rate",
    "articulation_rate",
    "internal_pause_count",
    "internal_pause_total_duration",
    "speech_f0_valid_ratio",
    "f0_semitone_percentile_range",
    "speech_intensity_percentile_range",
)


class BatchStatus(str, Enum):
    PENDING = "pending"
    SKIPPED_UNSUPPORTED_SCRIPT = "skipped_unsupported_script"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class BatchIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_sha256: Sha256
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_manifest_sha256: Sha256


class BatchManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str
    speaker_id: str
    job: str
    sex: str
    age: str
    script_id: str
    audio_path: str
    label_path: str
    audio_sha256: Sha256
    label_sha256: Sha256
    original_script: str
    normalized_script: str | None
    normalization_status: NormalizationStatus
    normalization_rules_applied: tuple[str, ...] = ()
    batch_status: BatchStatus = BatchStatus.PENDING
    analysis_id: UUID | None = None
    result_path: str | None = None
    analysis_status: AnalysisStatus | None = None
    error_codes: list[str] = Field(default_factory=list)
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    elapsed_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_sha256: Sha256
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_manifest_sha256: Sha256


class BatchExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    manifest_path: Path
    summary_path: Path
    report_path: Path
    selected_count: int = Field(ge=0)
    processed_count: int = Field(ge=0)
    resumed_count: int = Field(ge=0)
    total_elapsed_s: float = Field(ge=0, allow_inf_nan=False)
    status_counts: dict[str, int]


class Analyzer(Protocol):
    def analyze(self, request: AnalysisRequest) -> PipelineRunResult: ...


Clock = Callable[[], datetime]
Timer = Callable[[], float]


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_batch_identity(config: PocConfig) -> BatchIdentity:
    root = config.paths.project_root.resolve(strict=True)
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    git_commit = completed.stdout.strip()
    if completed.returncode != 0 or len(git_commit) != 40:
        raise RuntimeError("Git commit could not be resolved")
    config_path = root / "config/poc.toml"
    model_manifest = config.paths.mfa_root_dir / "manifests/korean_mfa.json"
    if not model_manifest.is_file():
        raise RuntimeError("MFA model manifest is unavailable")
    return BatchIdentity(
        git_commit=git_commit,
        config_sha256=_file_sha256(config_path),
        model_revision=config.alignment.model_revision,
        model_manifest_sha256=_file_sha256(model_manifest),
    )


def _relative_to_project(path: Path, project_root: Path) -> str:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(project_root):
        raise ValueError("batch path escaped project root")
    return resolved.relative_to(project_root).as_posix()


def _base_entry(
    record: AnchorRecord,
    identity: BatchIdentity,
    project_root: Path,
) -> BatchManifestEntry:
    return BatchManifestEntry(
        sample_id=record.sample_id,
        speaker_id=record.speaker_id,
        job=record.job,
        sex=record.sex,
        age=record.age,
        script_id=record.script_id,
        audio_path=_relative_to_project(record.audio_path, project_root),
        label_path=_relative_to_project(record.label_path, project_root),
        audio_sha256=record.audio_sha256,
        label_sha256=record.label_sha256,
        original_script=record.original_script,
        normalized_script=record.normalized_script,
        normalization_status=record.normalization_status,
        normalization_rules_applied=record.normalization_rules_applied,
        git_commit=identity.git_commit,
        config_sha256=identity.config_sha256,
        model_revision=identity.model_revision,
        model_manifest_sha256=identity.model_manifest_sha256,
    )


def _atomic_text(path: Path, text: str, *, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=prefix,
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_batch_manifest(
    path: Path,
    entries: Sequence[BatchManifestEntry],
) -> Path:
    text = "".join(
        json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        for entry in entries
    )
    _atomic_text(path, text, prefix=".manifest-")
    return path


def read_batch_manifest(path: Path) -> list[BatchManifestEntry]:
    if not path.is_file():
        return []
    return [
        BatchManifestEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _same_identity_and_input(
    entry: BatchManifestEntry,
    record: AnchorRecord,
    identity: BatchIdentity,
) -> bool:
    return (
        entry.sample_id == record.sample_id
        and entry.audio_sha256 == record.audio_sha256
        and entry.label_sha256 == record.label_sha256
        and entry.config_sha256 == identity.config_sha256
        and entry.git_commit == identity.git_commit
        and entry.model_revision == identity.model_revision
        and entry.model_manifest_sha256 == identity.model_manifest_sha256
    )


def _load_valid_result(
    entry: BatchManifestEntry,
    project_root: Path,
) -> AnalysisResult | None:
    if entry.result_path is None:
        return None
    path = (project_root / entry.result_path).resolve(strict=False)
    if not path.is_relative_to(project_root) or not path.is_file():
        return None
    try:
        result = AnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if (
        entry.analysis_id != result.analysis_id
        or entry.analysis_status is not result.status
    ):
        return None
    return result


def _is_resumable(
    entry: BatchManifestEntry,
    record: AnchorRecord,
    identity: BatchIdentity,
    project_root: Path,
    *,
    retry_failed: bool,
) -> bool:
    if entry.batch_status is BatchStatus.FAILED and not retry_failed:
        return True
    if not _same_identity_and_input(entry, record, identity):
        return False
    if entry.batch_status is BatchStatus.SKIPPED_UNSUPPORTED_SCRIPT:
        return record.normalization_status is NormalizationStatus.UNSUPPORTED
    if entry.batch_status in {BatchStatus.SUCCESS, BatchStatus.PARTIAL}:
        return _load_valid_result(entry, project_root) is not None
    return False


def _batch_status(status: AnalysisStatus) -> BatchStatus:
    return {
        AnalysisStatus.SUCCESS: BatchStatus.SUCCESS,
        AnalysisStatus.PARTIAL: BatchStatus.PARTIAL,
        AnalysisStatus.FAILED: BatchStatus.FAILED,
    }[status]


def _feature_value(result: AnalysisResult, name: str) -> float | None:
    feature = next(
        (feature for feature in result.acoustic_features if feature.name == name),
        None,
    )
    return feature.summary.get("value") if feature is not None else None


def _summary_row(
    entry: BatchManifestEntry,
    project_root: Path,
) -> dict[str, str | int | float | None]:
    result = _load_valid_result(entry, project_root)
    row: dict[str, str | int | float | None] = {
        "sample_id": entry.sample_id,
        "speaker_id": entry.speaker_id,
        "job": entry.job,
        "sex": entry.sex,
        "age": entry.age,
        "script_id": entry.script_id,
        "normalized_script": entry.normalized_script,
        "analysis_status": (
            entry.analysis_status.value if entry.analysis_status is not None else None
        ),
    }
    if result is None:
        return row
    row.update(
        {
            "actual_word_label_count": sum(
                interval.label not in _NON_SPEECH_LABELS
                for interval in result.word_intervals
            ),
            "actual_phone_label_count": sum(
                interval.label not in _NON_SPEECH_LABELS
                for interval in result.phone_intervals
            ),
            "empty_word_interval_count": sum(
                interval.label == "" for interval in result.word_intervals
            ),
            "empty_phone_interval_count": sum(
                interval.label == "" for interval in result.phone_intervals
            ),
        }
    )
    for name in BATCH_SUMMARY_COLUMNS[12:]:
        row[name] = _feature_value(result, name)
    return row


def _write_summary(
    path: Path,
    entries: Sequence[BatchManifestEntry],
    project_root: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=".summary-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(
                handle,
                fieldnames=BATCH_SUMMARY_COLUMNS,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for entry in entries:
                writer.writerow(_summary_row(entry, project_root))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_report(
    path: Path,
    report: BatchExecutionReport,
) -> Path:
    text = (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _atomic_text(path, text, prefix=".batch-report-")
    return path


def run_anchor_batch(
    records: Sequence[AnchorRecord],
    config: PocConfig,
    *,
    batch_id: str,
    identity: BatchIdentity,
    analyzer: Analyzer | None = None,
    retry_failed: bool = False,
    clock: Clock = lambda: datetime.now(timezone.utc),
    timer: Timer = monotonic,
) -> BatchExecutionReport:
    if not _BATCH_ID_PATTERN.fullmatch(batch_id) or batch_id in {".", ".."}:
        raise ValueError("batch_id contains unsupported characters")
    project_root = config.paths.project_root.resolve(strict=True)
    batch_dir = (config.paths.output_dir / "batches" / batch_id).resolve(strict=False)
    if not batch_dir.is_relative_to(project_root):
        raise ValueError("batch output escaped project root")
    manifest_path = batch_dir / "manifest.jsonl"
    summary_path = batch_dir / "summary.csv"
    report_path = batch_dir / "batch_report.json"
    existing_by_id = {
        entry.sample_id: entry for entry in read_batch_manifest(manifest_path)
    }
    ordered_records = sorted(records, key=lambda record: record.sample_id)
    entries = [
        existing_by_id.get(
            record.sample_id,
            _base_entry(record, identity, project_root),
        )
        for record in ordered_records
    ]
    write_batch_manifest(manifest_path, entries)

    active_analyzer = analyzer or AnalysisPipeline(
        config,
        platform_role="mac_smoke",
    )
    processed_count = 0
    resumed_count = 0
    batch_started = timer()
    for index, record in enumerate(ordered_records):
        existing = entries[index]
        if _is_resumable(
            existing,
            record,
            identity,
            project_root,
            retry_failed=retry_failed,
        ):
            resumed_count += 1
            continue

        base = _base_entry(record, identity, project_root)
        if record.normalization_status is NormalizationStatus.UNSUPPORTED:
            now = clock()
            entries[index] = base.model_copy(
                update={
                    "batch_status": BatchStatus.SKIPPED_UNSUPPORTED_SCRIPT,
                    "started_at_utc": now,
                    "finished_at_utc": now,
                    "elapsed_s": 0.0,
                }
            )
            processed_count += 1
            write_batch_manifest(manifest_path, entries)
            continue

        if record.normalized_script is None:
            raise ValueError("supported record is missing normalized_script")
        started_at = clock()
        record_started = timer()
        try:
            run = active_analyzer.analyze(
                AnalysisRequest(
                    script=record.normalized_script,
                    audio_path=record.audio_path,
                    source="test",
                )
            )
            elapsed_s = max(0.0, timer() - record_started)
            entries[index] = base.model_copy(
                update={
                    "batch_status": _batch_status(run.result.status),
                    "analysis_id": run.result.analysis_id,
                    "result_path": _relative_to_project(
                        run.result_path,
                        project_root,
                    ),
                    "analysis_status": run.result.status,
                    "error_codes": [error.code.value for error in run.result.errors],
                    "started_at_utc": started_at,
                    "finished_at_utc": clock(),
                    "elapsed_s": elapsed_s,
                }
            )
        except PipelineFailure as exc:
            entries[index] = base.model_copy(
                update={
                    "batch_status": BatchStatus.FAILED,
                    "error_codes": [exc.error.code.value],
                    "started_at_utc": started_at,
                    "finished_at_utc": clock(),
                    "elapsed_s": max(0.0, timer() - record_started),
                }
            )
        except Exception:
            entries[index] = base.model_copy(
                update={
                    "batch_status": BatchStatus.FAILED,
                    "error_codes": [ErrorCode.INTERNAL_ERROR.value],
                    "started_at_utc": started_at,
                    "finished_at_utc": clock(),
                    "elapsed_s": max(0.0, timer() - record_started),
                }
            )
        processed_count += 1
        write_batch_manifest(manifest_path, entries)

    _write_summary(summary_path, entries, project_root)
    status_counts = Counter(entry.batch_status.value for entry in entries)
    report = BatchExecutionReport(
        batch_id=batch_id,
        manifest_path=manifest_path,
        summary_path=summary_path,
        report_path=report_path,
        selected_count=len(entries),
        processed_count=processed_count,
        resumed_count=resumed_count,
        total_elapsed_s=max(0.0, timer() - batch_started),
        status_counts=dict(sorted(status_counts.items())),
    )
    _write_report(report_path, report)
    return report
