# Voice fixtures for the required pre-merge analysis check

The user authorized real voice analysis tests before merging AI changes on 2026-11-05.
The audio remains local and ignored; do not commit or upload the media to CI artifacts.
`cases.json` pins the source digests and scripts. `testaudio1.m4a` is byte-identical
to `testaudio.m4a` and is recorded as an alias rather than an independent observation.

The gate runs each distinct fixture through both candidate CORE/H5 lanes, including
real MFA/native/GOP, one GPT completion, final server verification and result JSON.
It requires ACCEPT, successful coaching, the expected numeric score, one POST and
unchanged raw CORE bytes. Readiness alone is insufficient. Archive/history delivery
is outside this isolated analysis check and no production user job is replayed.
