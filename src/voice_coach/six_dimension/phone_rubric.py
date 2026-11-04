"""Reuse the server's versioned calculator without interpreting raw GOP states.

Frozen H5 packages embed the calculator's original source at this module path;
the source evaluator imports the same implementation from backend_analysis.
"""
from voice_coach.backend_analysis.phone_rubric import (
    Unscorable, evidence_rows, validate_policy, validate_response, worksheet,
)
