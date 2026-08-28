"""Advice allowlist backed by the C01 Korean error ontology (17 tags).

The legacy adapter serves four hand-written messages. The ontology covers all
seventeen error tags the AI Hub 71490 annotation actually carries, so wiring it
widens what can be coached from a quarter of the annotated taxonomy to all of it.

What it does *not* do is raise the review bar. Both sets are unreviewed: the
legacy messages carry "no evidence of C03 expert review", and the ontology's
messages carry ``review_status: engineering_draft``. So this adapter refuses to
hide that:

* every served entry reports its own ``review_status``, and the caller surfaces it;
* the ``other`` / 기타 tag declares ``action: abstain_expert_review`` and therefore
  yields **no message at all** — an unclassified error routes to review instead of
  receiving a generic "try again", which is what a catch-all message would be;
* an entry whose ``review_status`` is not in the caller's accepted set is withheld,
  so promoting draft advice to production is a configuration change that has to be
  made explicitly rather than by forgetting.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_ONTOLOGY_PATH = Path("configs/coaching/korean_error_advice_ontology_v1.json")
CATALOG_ID = "c01-korean-error-advice-ontology-v1"
#: Which review states may be served. Draft is allowed only because the whole
#: pipeline is scoped ``engineering_provisional`` and says so to the learner.
DEFAULT_ACCEPTED_REVIEW_STATES: tuple[str, ...] = ("engineering_draft",)
ABSTAIN_ACTION = "abstain_expert_review"
COACH_ACTION = "draft_coach"


class AdviceOntologyError(ValueError):
    """Raised when the ontology artifact breaks the contract."""


@dataclass(frozen=True, slots=True)
class OntologyAdvice:
    """One servable advice entry with its provenance."""

    issue_type: str
    raw_label: str
    advice_id: str
    message: str
    review_status: str
    target_scope: str
    localization_policy: str

    def __post_init__(self) -> None:
        for name in (
            "issue_type",
            "raw_label",
            "advice_id",
            "message",
            "review_status",
            "target_scope",
            "localization_policy",
        ):
            if not getattr(self, name):
                raise AdviceOntologyError(f"{name} must not be empty")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdviceOntologyError(f"{name} must be an object")
    return value


class OntologyAdviceCatalog:
    """``AdviceCatalogPort`` over the C01 ontology.

    ``lookup`` keeps the port's two-tuple shape so the existing gate and feedback
    composition need no change; ``entry`` exposes the full record for callers that
    must report the review status.
    """

    def __init__(
        self,
        ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH,
        *,
        accepted_review_states: tuple[str, ...] = DEFAULT_ACCEPTED_REVIEW_STATES,
    ) -> None:
        if not accepted_review_states:
            raise AdviceOntologyError("accepted_review_states must not be empty")
        path = Path(ontology_path)
        if not path.is_file():
            raise AdviceOntologyError("advice ontology artifact is unavailable")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdviceOntologyError("advice ontology is not valid JSON") from error
        document = _mapping(document, "advice ontology")
        tags = document.get("tags")
        if not isinstance(tags, list) or not tags:
            raise AdviceOntologyError("advice ontology carries no tags")

        self._ontology_id = str(document.get("ontology_id") or "")
        if not self._ontology_id:
            raise AdviceOntologyError("advice ontology must name itself")
        self._accepted = set(accepted_review_states)
        self._entries: dict[str, OntologyAdvice] = {}
        self._abstain: dict[str, str] = {}
        self._withheld: dict[str, str] = {}

        for index, raw_tag in enumerate(tags):
            tag = _mapping(raw_tag, f"tag[{index}]")
            issue_type = str(tag.get("id") or "")
            raw_label = str(tag.get("raw_label") or "")
            if not issue_type or not raw_label:
                raise AdviceOntologyError(f"tag[{index}] must carry id and raw_label")
            if issue_type in self._entries or issue_type in self._abstain:
                raise AdviceOntologyError(f"duplicate tag id: {issue_type}")
            advice = _mapping(tag.get("advice"), f"tag[{index}].advice")
            action = str(advice.get("action") or "")
            if action == ABSTAIN_ACTION:
                # Declared abstention: no message exists, and none may be invented.
                if advice.get("advice_id") or advice.get("message_ko"):
                    raise AdviceOntologyError(
                        f"{issue_type} abstains but carries advice content"
                    )
                self._abstain[issue_type] = raw_label
                continue
            if action != COACH_ACTION:
                raise AdviceOntologyError(f"{issue_type} has an unknown action: {action}")
            review_status = str(advice.get("review_status") or "")
            if review_status not in self._accepted:
                self._withheld[issue_type] = review_status
                continue
            self._entries[issue_type] = OntologyAdvice(
                issue_type=issue_type,
                raw_label=raw_label,
                advice_id=str(advice.get("advice_id") or ""),
                message=str(advice.get("message_ko") or ""),
                review_status=review_status,
                target_scope=str(tag.get("target_scope") or ""),
                localization_policy=str(tag.get("localization_policy") or ""),
            )

        if not self._entries:
            raise AdviceOntologyError(
                "no ontology entry is servable under the accepted review states"
            )

    @property
    def catalog_id(self) -> str:
        return CATALOG_ID

    @property
    def ontology_id(self) -> str:
        return self._ontology_id

    @property
    def allowed_issue_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    @property
    def abstaining_issue_types(self) -> tuple[str, ...]:
        """Tags that route to expert review instead of producing a message."""

        return tuple(sorted(self._abstain))

    @property
    def withheld_issue_types(self) -> Mapping[str, str]:
        """Tags excluded by review status, mapped to the status that excluded them."""

        return dict(self._withheld)

    @property
    def review_states_served(self) -> tuple[str, ...]:
        return tuple(sorted({item.review_status for item in self._entries.values()}))

    def lookup(self, issue_type: str) -> tuple[str, str] | None:
        entry = self._entries.get(issue_type)
        if entry is None:
            return None
        return entry.advice_id, entry.message

    def entry(self, issue_type: str) -> OntologyAdvice | None:
        return self._entries.get(issue_type)

    def abstains_for(self, issue_type: str) -> bool:
        return issue_type in self._abstain


__all__ = [
    "ABSTAIN_ACTION",
    "CATALOG_ID",
    "COACH_ACTION",
    "DEFAULT_ACCEPTED_REVIEW_STATES",
    "DEFAULT_ONTOLOGY_PATH",
    "AdviceOntologyError",
    "OntologyAdvice",
    "OntologyAdviceCatalog",
]
