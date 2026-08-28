import os
import subprocess
import sys
import textwrap
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from voice_coach.api.app import app

client = TestClient(app)
WORKSPACE = Path(__file__).resolve().parents[2]


def test_base_api_import_does_not_require_torch() -> None:
    script = textwrap.dedent(
        """
        import builtins
        import sys
        from typing import get_type_hints

        original_import = builtins.__import__

        def block_torch(name, *args, **kwargs):
            if name == "torch" or name.startswith("torch."):
                raise ModuleNotFoundError(
                    "torch deliberately blocked for base dependency test",
                    name="torch",
                )
            return original_import(name, *args, **kwargs)

        builtins.__import__ = block_torch

        from voice_coach.api.app import app
        from voice_coach.application.closed_loop import (
            ClosedLoopApplicationError,
            ClosedLoopSession,
            ClosedLoopTTSService,
        )

        assert any(route.path == "/health" for route in app.routes)
        assert "torch" not in sys.modules
        assert (
            get_type_hints(ClosedLoopSession.decide)["return"].__name__
            == "ClosedLoopDecision"
        )

        class UnusedGeneratorChain:
            def generate_candidates(self, *args, **kwargs):
                raise AssertionError(
                    "generator chain must not run without scorer dependencies"
                )

        try:
            ClosedLoopTTSService(UnusedGeneratorChain()).generate_reference(
                object(), object(), object(), families=()
            )
        except ClosedLoopApplicationError as exc:
            assert "voice-coach[scorer]" in str(exc)
        else:
            raise AssertionError(
                "TTS generation must fail closed when torch is unavailable"
            )
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=WORKSPACE,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_oracle_attempt_returns_evidence_constrained_coaching() -> None:
    response = client.post(
        "/v1/oracle-attempts",
        json={
            "attemptId": str(uuid4()),
            "targetText": "오늘은 날씨가 좋습니다.",
            "contentMatch": {"score": 0.96, "confidence": 0.93},
            "aspects": {
                "segmental": 0.72,
                "fluency": 0.68,
                "prosody": 0.61,
                "comprehensibility": 0.76,
            },
            "goldIssues": [
                {
                    "type": "final_consonant_deletion",
                    "location": {"startMs": 1320, "endMs": 1540},
                    "target": "ㄹ",
                    "observed": None,
                    "severity": 0.78,
                    "confidence": 0.87,
                    "evidence": ["gold_label"],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysisSource"] == "oracle"
    assert payload["coaching"]["action"] == "coach"
    assert payload["coaching"]["adviceId"] == "ARTICULATION_RULE_017"


def test_closed_loop_endpoint_returns_rerecord_transition() -> None:
    response = client.post(
        "/v1/closed-loop/oracle-attempts",
        json={
            "attempt": {
                "attemptId": str(uuid4()),
                "targetText": "가",
                "contentMatch": {"score": 0.4, "confidence": 0.95},
                "aspects": {
                    "segmental": 0.5,
                    "fluency": 0.5,
                    "prosody": 0.5,
                    "comprehensibility": 0.5,
                },
                "goldIssues": [],
            },
            "attemptIndex": 1,
            "maxAttempts": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["action"] == "rerecord"
    assert payload["decision"]["next_state"] == "awaiting_rerecord"
    assert payload["profile"]["coaching"]["action"] == "rerecord"
