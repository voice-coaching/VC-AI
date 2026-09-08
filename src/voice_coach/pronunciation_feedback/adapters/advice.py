"""Advice allowlist adapter.

The only messages this pipeline may emit today are the four legacy MVP entries
in :mod:`voice_coach.coaching.catalog`.  The 17-tag C01 ontology draft
(``configs/coaching/korean_error_advice_ontology_v1.json``) is not wired in
here: its target-phone mapping is still ``pending`` and C03 expert review has
not run, so serving it would present unreviewed advice as approved.

An issue type outside the allowlist yields no message.  The gate turns that into
``expert_review``/``uncertain`` rather than paraphrasing something else.
"""

from __future__ import annotations

from voice_coach.coaching.catalog import ADVICE_CATALOG

CATALOG_ID = "legacy-engineering-mvp-advice-v1"


class LegacyAdviceCatalog:
    """``AdviceCatalogPort`` implementation over the frozen MVP allowlist."""

    def __init__(self) -> None:
        self._entries = dict(ADVICE_CATALOG)

    @property
    def catalog_id(self) -> str:
        return CATALOG_ID

    @property
    def allowed_issue_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def lookup(self, issue_type: str) -> tuple[str, str] | None:
        entry = self._entries.get(issue_type)
        if entry is None:
            return None
        advice_id, message = entry
        return advice_id, message
