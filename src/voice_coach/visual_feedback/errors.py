"""Typed failures raised at visual-feedback contract boundaries."""


class VisualFeedbackContractError(ValueError):
    """A document or domain value violates the frozen visual contract."""


class SeungunEvidenceUnavailableError(VisualFeedbackContractError):
    """The immutable seungun evidence or its binding cannot be trusted."""


class VisualArtifactUnavailableError(VisualFeedbackContractError):
    """A calibration or claim artifact is absent, malformed, or unsupported."""


__all__ = [
    "SeungunEvidenceUnavailableError",
    "VisualArtifactUnavailableError",
    "VisualFeedbackContractError",
]
