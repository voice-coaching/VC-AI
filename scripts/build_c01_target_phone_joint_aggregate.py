#!/usr/bin/env python3
"""Resolve C01's ``pending_privacy_safe_joint_aggregate`` into an actual aggregate.

The ontology already fixes *how* an error tag may claim a target phone
(`target_phone_mapping.interval_rules`) and forbids the claim without positive
interval overlap. What it lacks is the joint evidence: it lists
``tag_by_target_phone_joint_counts`` under ``unavailable_evidence``. This script
computes exactly that, under those rules, and emits **counts only**.

Why the manifest rather than the raw label JSON: the manifest is SHA-pinned and is
the same source S104/S105 consume, so the aggregate inherits provenance the raw
files cannot give. Both `error_intervals` and `target_phone_intervals` live there.

Privacy: the output contains tag labels, phone labels and integer counts. No
utterance id, speaker id, path, duration, or per-row assignment is retained — the
ontology's ``access_scope`` is ``privacy_safe_aggregate_only`` and
``feature_separation`` forbids these annotations from ever becoming model input,
so an aggregate is the only representation permitted to leave this scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import pyarrow.dataset as ds  # noqa: E402

#: The shared manifest reader projects a fixed column set that omits
#: ``error_intervals``, and it lives in the SHA-pinned S104 implementation bundle,
#: so it cannot be widened. This scan reads the columns it needs directly.
_SCAN_COLUMNS = ("split", "error_intervals", "target_phone_intervals")


def _manifest_rows(manifest_path: Path, *, split: str):
    dataset = ds.dataset(str(manifest_path), format="parquet")
    scanner = dataset.scanner(
        columns=list(_SCAN_COLUMNS),
        filter=ds.field("split") == split,
        batch_size=512,
    )
    for batch in scanner.to_batches():
        yield from batch.to_pylist()

ARTIFACT_SCHEMA_VERSION = "voice_coach.c01_target_phone_joint_aggregate.v1"
DEFAULT_ONTOLOGY = Path("configs/coaching/korean_error_advice_ontology_v1.json")
DEFAULT_MANIFEST = Path("data/processed/aihub71490/manifests/aihub71490_modeling.parquet")
DEFAULT_OUTPUT = Path("configs/coaching/c01_target_phone_joint_aggregate_v1.json")
#: The ontology declares three localization policies and only one of them may
#: claim a target phone. Conflating them would attach a boundary-intonation error
#: to whichever phone happened to overlap it, which is precisely the unsupported
#: claim the contract forbids.
PHONE_CLAIM_POLICY = "phone_overlap_required"
NO_CLAIM_POLICY = "no_target_phone_claim"
PROSODIC_SPAN_POLICY = "prosodic_span_only"
KNOWN_POLICIES = frozenset({PHONE_CLAIM_POLICY, NO_CLAIM_POLICY, PROSODIC_SPAN_POLICY})
#: Annotation timestamps carry 0.01 s resolution; this tolerance keeps equal
#: durations comparing equal so the declared tie-breakers actually fire.
OVERLAP_EPSILON = 1e-9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute the C01 tag x target-phone joint aggregate.",
    )
    parser.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def match_target_phone(
    *,
    error_start: float,
    error_end: float,
    phones: list[dict],
) -> tuple[str | None, float]:
    """Apply the declared interval rules to one error interval.

    Half-open intervals, strictly positive overlap, maximum overlap duration as the
    primary match, then lowest phone index, then lexicographic label. Returns
    ``(None, 0.0)`` when no phone overlaps — the contract then requires abstention
    rather than the nearest guess.
    """

    best_label: str | None = None
    best_overlap = 0.0
    best_index = -1
    for index, phone in enumerate(phones):
        start = phone.get("start")
        end = phone.get("end")
        label = phone.get("label")
        if start is None or end is None or not label:
            continue
        overlap = min(error_end, float(end)) - max(error_start, float(start))
        if overlap <= OVERLAP_EPSILON:  # strictly positive duration required
            continue
        if overlap > best_overlap + OVERLAP_EPSILON:
            best_label, best_overlap, best_index = str(label), overlap, index
            continue
        if abs(overlap - best_overlap) <= OVERLAP_EPSILON and best_label is not None:
            # Genuine tie: lowest target phone index, then lexicographic label.
            # Compared with a tolerance because subtracting annotation timestamps
            # makes equal durations differ in the last bits (0.15-0.10 !=
            # 0.10-0.05), which would let float noise decide the claim instead of
            # the declared rule.
            if index < best_index or (
                index == best_index and str(label) < best_label
            ):
                best_label, best_index = str(label), index
    return best_label, best_overlap


def main() -> int:
    args = parse_args()
    ontology_path = _resolve(args.ontology)
    manifest_path = _resolve(args.manifest)
    ontology = json.loads(ontology_path.read_text(encoding="utf-8"))
    mapping = ontology["target_phone_mapping"]
    rules = mapping["interval_rules"]

    if rules.get("interval_semantics") != "half_open":
        print("unexpected interval semantics", file=sys.stderr)
        return 1
    if rules.get("overlap_requirement") != "strictly_positive_duration":
        print("unexpected overlap requirement", file=sys.stderr)
        return 1
    if rules.get("primary_match") != "maximum_overlap_duration":
        print("unexpected primary match rule", file=sys.stderr)
        return 1
    if mapping.get("automatic_target_claim") != (
        "prohibited_without_positive_interval_overlap"
    ):
        print("unexpected automatic target claim rule", file=sys.stderr)
        return 1

    by_raw_label = {str(tag["raw_label"]): tag for tag in ontology["tags"]}
    policy_by_label: dict[str, str] = {}
    for tag in ontology["tags"]:
        policy = str(tag.get("localization_policy"))
        if policy not in KNOWN_POLICIES:
            print(f"unknown localization policy: {policy}", file=sys.stderr)
            return 1
        policy_by_label[str(tag["raw_label"])] = policy

    joint: dict[str, dict[str, int]] = {}
    per_tag_total: dict[str, int] = {}
    per_tag_matched: dict[str, int] = {}
    per_tag_unmatched: dict[str, int] = {}
    per_tag_abstained: dict[str, int] = {}
    per_tag_prosodic: dict[str, int] = {}
    unknown_tags: dict[str, int] = {}
    phone_inventory: set[str] = set()
    target_phone_occurrences = 0
    utterances = 0

    for split in ("train", "dev"):
        for row in _manifest_rows(manifest_path, split=split):
            utterances += 1
            phones = row.get("target_phone_intervals") or []
            for phone in phones:
                if phone.get("label"):
                    phone_inventory.add(str(phone["label"]))
                    target_phone_occurrences += 1
            errors = row.get("error_intervals") or []
            for error in errors:
                label = error.get("label")
                start = error.get("start")
                end = error.get("end")
                if not label or start is None or end is None:
                    continue
                raw_label = str(label)
                per_tag_total[raw_label] = per_tag_total.get(raw_label, 0) + 1
                if raw_label not in by_raw_label:
                    unknown_tags[raw_label] = unknown_tags.get(raw_label, 0) + 1
                    continue
                policy = policy_by_label[raw_label]
                if policy == NO_CLAIM_POLICY:
                    # Preserved and routed to expert review; no phone claim.
                    per_tag_abstained[raw_label] = (
                        per_tag_abstained.get(raw_label, 0) + 1
                    )
                    continue
                if policy == PROSODIC_SPAN_POLICY:
                    # A prosodic span belongs to no single target phone.
                    per_tag_prosodic[raw_label] = per_tag_prosodic.get(raw_label, 0) + 1
                    continue
                matched, _overlap = match_target_phone(
                    error_start=float(start),
                    error_end=float(end),
                    phones=list(phones),
                )
                if matched is None:
                    per_tag_unmatched[raw_label] = (
                        per_tag_unmatched.get(raw_label, 0) + 1
                    )
                    continue
                per_tag_matched[raw_label] = per_tag_matched.get(raw_label, 0) + 1
                joint.setdefault(raw_label, {})
                joint[raw_label][matched] = joint[raw_label].get(matched, 0) + 1

    total_error_intervals = sum(per_tag_total.values())
    declared_total = ontology["source"].get("observed_error_interval_count")

    tag_support: dict[str, dict[str, object]] = {}
    for raw_label, tag in sorted(by_raw_label.items(), key=lambda kv: kv[1]["id"]):
        tag_id = str(tag["id"])
        counts = dict(sorted(joint.get(raw_label, {}).items()))
        matched = per_tag_matched.get(raw_label, 0)
        tag_support[tag_id] = {
            "raw_label": raw_label,
            "localization_policy": policy_by_label[raw_label],
            "interval_count": per_tag_total.get(raw_label, 0),
            "matched_count": matched,
            "unmatched_count": per_tag_unmatched.get(raw_label, 0),
            "abstained_count": per_tag_abstained.get(raw_label, 0),
            "prosodic_span_count": per_tag_prosodic.get(raw_label, 0),
            "distinct_target_phones": len(counts),
            "target_phone_counts": counts,
            "top_target_phones": [
                phone
                for phone, _count in sorted(
                    counts.items(), key=lambda kv: (-kv[1], kv[0])
                )[:5]
            ],
        }

    document = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "ontology_id": ontology["ontology_id"],
        "access_scope": "privacy_safe_aggregate_only",
        "retained_fields": ["error_tag_label", "target_phone_label", "integer_counts"],
        "withheld_fields": [
            "utterance_id",
            "speaker_id",
            "audio_path",
            "row_level_tag_phone_assignment",
            "interval_timestamps",
        ],
        "label_role": "supervision_label_and_audit_stratum_only",
        "model_input_use": "prohibited",
        "source": {
            "modeling_manifest": str(args.manifest),
            "modeling_manifest_sha256": _sha256_file(manifest_path),
            "splits_scanned": ["train", "dev"],
            "utterances_scanned": utterances,
        },
        "interval_rules_applied": rules,
        "totals": {
            "error_intervals_in_scanned_splits": total_error_intervals,
            # The data card's count spans every split, including locked_test,
            # calibration and stack. Those are not read here, so this aggregate
            # covers strictly fewer intervals by design; equality would mean the
            # access contract had been broken.
            "declared_all_split_error_interval_count": declared_total,
            "scan_covers_all_splits": False,
            "splits_withheld": ["stack", "calibration", "locked_test"],
            "splits_withheld_reason": "forbidden_by_split_access_contract",
            "matched": sum(per_tag_matched.values()),
            "unmatched": sum(per_tag_unmatched.values()),
            "abstained_no_phone_claim": sum(per_tag_abstained.values()),
            "prosodic_span_no_phone_claim": sum(per_tag_prosodic.values()),
            "target_phone_inventory_size": len(phone_inventory),
            "total_target_phone_occurrences": target_phone_occurrences,
            "unknown_error_tags": dict(sorted(unknown_tags.items())),
        },
        "tag_support": tag_support,
    }

    output = _resolve(args.output)
    if output.exists() and not args.overwrite:
        print(
            json.dumps({"status": "failed_closed", "error": "artifact already exists"}),
            file=sys.stderr,
        )
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.write_text(payload, encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "computed",
                "artifact": str(args.output),
                "artifact_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "error_intervals_in_scanned_splits": total_error_intervals,
                "matched": document["totals"]["matched"],
                "unmatched": document["totals"]["unmatched"],
                "abstained_no_phone_claim": document["totals"][
                    "abstained_no_phone_claim"
                ],
                "prosodic_span_no_phone_claim": document["totals"][
                    "prosodic_span_no_phone_claim"
                ],
                "unknown_error_tags": document["totals"]["unknown_error_tags"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
