"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from voice_coach.application.oracle import OracleAttempt, analyze_oracle_attempt
from voice_coach.domain.models import DiagnosticProfile

app = FastAPI(title="intelligentAI Voice Coach", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/oracle-attempts", response_model=DiagnosticProfile)
def create_oracle_attempt(attempt: OracleAttempt) -> DiagnosticProfile:
    return analyze_oracle_attempt(attempt)
