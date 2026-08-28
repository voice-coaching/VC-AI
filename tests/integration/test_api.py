from uuid import uuid4

from fastapi.testclient import TestClient

from voice_coach.api.app import app

client = TestClient(app)


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
