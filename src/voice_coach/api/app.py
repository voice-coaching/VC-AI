"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import Field

from voice_coach.application.closed_loop import ClosedLoopSession
from voice_coach.application.oracle import OracleAttempt, analyze_oracle_attempt
from voice_coach.domain.models import ContractModel, DiagnosticProfile

app = FastAPI(title="intelligentAI Voice Coach", version="0.1.0")


class ClosedLoopOracleRequest(ContractModel):
    """Stateless API envelope for one coaching/re-record transition."""

    attempt: OracleAttempt
    attempt_index: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/oracle-attempts", response_model=DiagnosticProfile)
def create_oracle_attempt(attempt: OracleAttempt) -> DiagnosticProfile:
    return analyze_oracle_attempt(attempt)


@app.post("/v1/closed-loop/oracle-attempts")
def create_closed_loop_oracle_attempt(request: ClosedLoopOracleRequest) -> dict[str, object]:
    profile = analyze_oracle_attempt(request.attempt)
    session = ClosedLoopSession(max_attempts=request.max_attempts)
    session.attempt_index = request.attempt_index - 1
    decision = session.decide(profile)
    return {
        "profile": profile.model_dump(mode="json", by_alias=True),
        "decision": decision.to_dict(),
    }
