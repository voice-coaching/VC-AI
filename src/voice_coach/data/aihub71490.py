"""Dataset-specific annotation parsing for AI Hub dataset 71490."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .intervals import Interval


def _parse_interval_list(
    values: Any,
    *,
    label_key: str,
    field_name: str,
) -> list[Interval]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    output: list[Interval] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name}[{index}] must be an object")
        raw_label = value.get(label_key)
        if not isinstance(raw_label, str) or not raw_label.strip():
            # A JSON ``null``/missing/blank label previously fell through
            # str(value[label_key]) into the literal string "None" or ""
            # instead of being rejected, silently corrupting the target
            # phone sequence (surfaced by manual audit item #216: "target
            # 필드가 null"). Treat it as an invalid interval so the record
            # is excluded via the existing annotation_parse_error QC flag.
            raise ValueError(
                f"{field_name}[{index}].{label_key} must be a non-empty string"
            )
        try:
            label = raw_label
            start = float(value["start"])
            end = float(value["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field_name}[{index}] interval") from exc
        output.append(Interval(start=start, end=end, label=label))
    return output


def load_aihub71490_annotations(
    path: str | Path,
) -> dict[str, list[Interval]]:
    """Load target words/phones and error tags from a pronunciation JSON."""

    with Path(path).open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)
    try:
        phonemic = document["RecordingMetadata"]["phonemic"]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing RecordingMetadata.phonemic") from exc
    if not isinstance(phonemic, Mapping):
        raise ValueError("RecordingMetadata.phonemic must be an object")
    return {
        "words": _parse_interval_list(
            phonemic.get("words", []),
            label_key="word",
            field_name="words",
        ),
        "target_phones": _parse_interval_list(
            phonemic.get("phones", []),
            label_key="phone",
            field_name="phones",
        ),
        "error_tags": _parse_interval_list(
            phonemic.get("error_tags", []),
            label_key="error_tag",
            field_name="error_tags",
        ),
    }


def interval_to_dict(interval: Interval) -> dict[str, Any]:
    return {
        "start": interval.start,
        "end": interval.end,
        "label": interval.label,
    }
