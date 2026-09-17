# Source-pin reconciliation — 2026-09-17

The HTTP transport commit `b4057a008ad45c89f540e4cb6e19b6f29cd53aed` added
`httpx`, `jsonschema`, and `rfc8785` to `pyproject.toml`. Seven current research
configs still bound that file to its preceding SHA-256:

- Previous: `87fbe75e3df2ad7f64494cafb3744e7cfb815cfe1b8e9ea8d774d8b990c35e71`
- Current: `c18ea49da964dafcf3ead484c84b90e9cb5aee9086b23a8a233689b759c153d7`

This repair updates those seven bindings and their dependent config bindings.
The complete dependency closure changes 63 SHA-256 values in 18 current config
files. The affected roots are B05, B07, S200, S104 interval-tiebreak, S106, S107,
and S108 no-locked dry-run. Dependent bindings in C02, C03, D07, D09/H02/H03,
E00, E01, E02, R05, and S201–S203 are recomputed from the resulting file bytes.

Only digest values change in these configs. Model parameters, input paths,
thresholds, promotion restrictions, and historical snapshot exceptions remain
unchanged. Existing model artifacts, run receipts, and research results are not
rewritten or recertified. A prior receipt tied to an old config digest does not
validate the revised config; its normal validator must be run by the researcher
before that config is used for a new research operation.

Static validation uses the LF bytes stored in Git, matching Linux CI. The source
pin checker validates all 663 repository-current bindings with zero failures.
Historical snapshots remain subject to their existing exact-file-digest and
safety-field checks. No exclusions or relaxed hash checks were added.

This is source consistency validation, not a model evaluation or a new research
completion claim. No training, model download, locked-data access, or inference
is part of this repair.
