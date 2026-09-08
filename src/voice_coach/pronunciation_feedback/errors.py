"""Typed failures for the pronunciation feedback pipeline."""

from __future__ import annotations

from .domain import FailureCode


class PronunciationFeedbackError(RuntimeError):
    """Base class for pipeline-visible failures."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


class BackendUnavailableError(PronunciationFeedbackError):
    """A required backend, artifact, or revision could not be used.

    Raised instead of substituting a default score.  The pipeline maps this to
    ``failed_closed`` with the carried :class:`FailureCode`.
    """


class PromptUnavailableError(PronunciationFeedbackError):
    """The requested prompt is not in the catalog, or the catalog is unusable."""


class AudioRejectedError(PronunciationFeedbackError):
    """The submitted audio could not be decoded or failed quality control."""


class ContractViolationError(PronunciationFeedbackError):
    """An adapter returned a result that breaks the port contract."""
