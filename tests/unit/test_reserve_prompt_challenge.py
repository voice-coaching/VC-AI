from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "reserve_prompt_challenge.py"
)
SPEC = importlib.util.spec_from_file_location("reserve_prompt_challenge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _prompt_hash(index: int) -> str:
    return hashlib.sha256(f"synthetic-prompt-{index}".encode()).hexdigest()


def _write_manifest(path: Path, rows: list[dict]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def _run(
    manifest: Path,
    output: Path,
    *,
    prompt_count: int,
    seed: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--modeling-manifest",
            str(manifest),
            "--output",
            str(output),
            "--prompt-count",
            str(prompt_count),
            "--seed",
            seed,
            "--batch-size",
            "2",
        ],
        capture_output=True,
        text=True,
    )


def test_default_selection_fixes_twenty_prompts_in_seed_hash_order() -> None:
    candidates = [_prompt_hash(index) for index in range(25)]

    selected = MODULE.select_reserved_prompts(
        reversed(candidates),
        seed=MODULE.DEFAULT_SEED,
        prompt_count=MODULE.DEFAULT_PROMPT_COUNT,
    )

    expected = sorted(
        (
            (
                MODULE.selection_rank_sha256(MODULE.DEFAULT_SEED, prompt_hash),
                prompt_hash,
            )
            for prompt_hash in candidates
        )
    )[:20]
    assert selected == [
        (prompt_hash, rank_hash) for rank_hash, prompt_hash in expected
    ]
    assert len(selected) == 20


def test_scanner_projects_only_prompt_hash_and_split(
    monkeypatch,
    tmp_path: Path,
) -> None:
    requested_columns: list[list[str]] = []
    prompt_hash = _prompt_hash(0)

    class FakeParquet:
        schema_arrow = pa.schema(
            [
                ("prompt_sha256", pa.string()),
                ("split", pa.string()),
                ("locked_test_metric_must_not_be_read", pa.float64()),
                ("prompt_text_must_not_be_read", pa.string()),
            ]
        )

        def iter_batches(self, *, batch_size: int, columns: list[str]):
            assert batch_size == 7
            requested_columns.append(columns)
            yield pa.RecordBatch.from_pylist(
                [{"prompt_sha256": prompt_hash, "split": "locked_test"}],
                schema=pa.schema(
                    [("prompt_sha256", pa.string()), ("split", pa.string())]
                ),
            )

    monkeypatch.setattr(MODULE.pq, "ParquetFile", lambda _: FakeParquet())

    split_counts, prompt_counts = MODULE.scan_projected_columns(
        tmp_path / "never-opened.parquet",
        batch_size=7,
    )

    assert requested_columns == [["prompt_sha256", "split"]]
    assert split_counts == {"locked_test": 1}
    assert prompt_counts == {prompt_hash: {"locked_test": 1}}


def test_cli_writes_rules_counts_source_sha_and_no_plaintext(
    tmp_path: Path,
) -> None:
    hashes = [_prompt_hash(index) for index in range(4)]
    manifest = tmp_path / "modeling.parquet"
    output = tmp_path / "prompt_challenge.json"
    _write_manifest(
        manifest,
        [
            {
                "prompt_sha256": hashes[0],
                "split": "locked_test",
                "prompt": "SYNTHETIC SECRET ZERO",
                "locked_test_metric": 0.123,
            },
            {
                "prompt_sha256": hashes[0],
                "split": "train",
                "prompt": "SYNTHETIC SECRET ZERO",
                "locked_test_metric": 0.456,
            },
            {
                "prompt_sha256": hashes[1],
                "split": "locked_test",
                "prompt": "SYNTHETIC SECRET ONE",
                "locked_test_metric": 0.789,
            },
            {
                "prompt_sha256": hashes[1],
                "split": "dev",
                "prompt": "SYNTHETIC SECRET ONE",
                "locked_test_metric": 0.111,
            },
            {
                "prompt_sha256": hashes[2],
                "split": "locked_test",
                "prompt": "SYNTHETIC SECRET TWO",
                "locked_test_metric": 0.222,
            },
            {
                "prompt_sha256": hashes[3],
                "split": "train",
                "prompt": "SYNTHETIC SECRET THREE",
                "locked_test_metric": 0.333,
            },
        ],
    )

    result = _run(
        manifest,
        output,
        prompt_count=2,
        seed="unit-test-seed",
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(output.read_text(encoding="utf-8"))
    selected = {
        prompt["prompt_sha256"] for prompt in artifact["reserved_prompts"]
    }
    expected = {
        prompt_hash
        for prompt_hash, _ in MODULE.select_reserved_prompts(
            hashes[:3],
            seed="unit-test-seed",
            prompt_count=2,
        )
    }
    assert selected == expected
    assert artifact["source"] == {
        "modeling_manifest_sha256": hashlib.sha256(
            manifest.read_bytes()
        ).hexdigest(),
        "row_count": 6,
        "projected_columns": ["prompt_sha256", "split"],
        "non_projected_value_columns_read": False,
    }
    assert artifact["selection"]["requested_prompt_count"] == 2
    assert artifact["selection"]["locked_test_candidate_prompt_count"] == 3
    assert artifact["counts"]["source_rows_by_split"] == {
        "dev": 1,
        "locked_test": 3,
        "train": 2,
    }
    expected_reserved_by_split: dict[str, int] = {}
    for prompt_hash in expected:
        for row in pq.read_table(
            manifest,
            columns=["prompt_sha256", "split"],
        ).to_pylist():
            if row["prompt_sha256"] == prompt_hash:
                split = row["split"]
                expected_reserved_by_split[split] = (
                    expected_reserved_by_split.get(split, 0) + 1
                )
    assert artifact["counts"]["reserved_rows_by_source_split"] == dict(
        sorted(expected_reserved_by_split.items())
    )
    challenge_count = expected_reserved_by_split.get("locked_test", 0)
    excluded_count = sum(expected_reserved_by_split.values()) - challenge_count
    assert artifact["counts"]["row_dispositions"] == {
        "prompt_challenge": challenge_count,
        "exclude_from_training_and_tuning": excluded_count,
        "preserve_source_split": 6 - challenge_count - excluded_count,
    }
    rules = artifact["row_disposition_rules"]
    assert rules[0]["disposition"] == "prompt_challenge"
    assert rules[0]["when"]["source_split"] == "locked_test"
    assert rules[1]["disposition"] == "exclude_from_training_and_tuning"
    assert rules[1]["when"]["source_split"] == "not_locked_test"
    serialized = output.read_text(encoding="utf-8") + result.stdout
    assert "SYNTHETIC SECRET" not in serialized
    assert "locked_test_metric" not in serialized


def test_cli_fails_atomically_when_locked_test_has_too_few_prompts(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "modeling.parquet"
    output = tmp_path / "prompt_challenge.json"
    _write_manifest(
        manifest,
        [
            {"prompt_sha256": _prompt_hash(0), "split": "locked_test"},
            {"prompt_sha256": _prompt_hash(1), "split": "train"},
        ],
    )

    result = _run(
        manifest,
        output,
        prompt_count=2,
        seed="unit-test-seed",
    )

    assert result.returncode != 0
    assert "need 2, found 1" in result.stderr
    assert not output.exists()
    assert not output.with_name(output.name + ".part").exists()
