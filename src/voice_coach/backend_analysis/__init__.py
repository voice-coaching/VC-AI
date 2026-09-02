"""Private worker boundary for VC-BE Redis Stream analysis requests.

The package owns neither a public FastAPI route nor a pronunciation model.  It
accepts the versioned VC-BE request contract, checks authority before media
access, and delegates an admitted attempt to a Seungun-owned executor.
"""

from .domain import (
    ANALYSIS_REQUEST_SCHEMA_VERSION,
    ANALYSIS_RESULT_SCHEMA_VERSION,
    AnalysisOutcome,
    AnalysisRequest,
    AnalysisWorkerResult,
    BackendAnalysisContractError,
    PronunciationEvidence,
)
from .service import BackendAnalysisWorker

__all__ = [
    "ANALYSIS_REQUEST_SCHEMA_VERSION",
    "ANALYSIS_RESULT_SCHEMA_VERSION",
    "AnalysisOutcome",
    "AnalysisRequest",
    "AnalysisWorkerResult",
    "BackendAnalysisContractError",
    "PronunciationEvidence",
    "BackendAnalysisWorker",
]
