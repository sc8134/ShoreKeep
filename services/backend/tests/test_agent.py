from datetime import timedelta

from sqlmodel import Session

from backend.agent_auth import hash_agent_token
from backend.db import engine
from backend.models import AgentPair, utcnow


def test_pair_requires_sign_in(client):
    assert client.post("/v1/agent/pair").status_code == 401


def test_pair_claim_and_device(client, register):
    register()
    pair = client.post("/v1/agent/pair")
    assert pair.status_code == 201, pair.text
    body = pair.json()
    assert body["pair_token"]
    assert body["claim_url"].endswith("/v1/agent/pair/claim")

    claim = client.post(
        "/v1/agent/pair/claim",
        json={"pair_token": body["pair_token"], "label": "Test PC"},
    )
    assert claim.status_code == 200, claim.text
    claimed = claim.json()
    assert claimed["user"]["email"] == "user@example.com"
    assert claimed["limits"]["plan"] == "free"
    assert "1080p" in claimed["limits"]["presets"]
    device_token = claimed["device_token"]
    assert device_token

    me = client.get("/v1/agent/me", headers={"Authorization": f"Bearer {device_token}"})
    assert me.status_code == 200, me.text
    assert me.json()["label"] == "Test PC"
    assert me.json()["user"]["email"] == "user@example.com"


def test_pair_token_is_single_use(client, register):
    register()
    token = client.post("/v1/agent/pair").json()["pair_token"]
    assert client.post("/v1/agent/pair/claim", json={"pair_token": token}).status_code == 200
    second = client.post("/v1/agent/pair/claim", json={"pair_token": token})
    assert second.status_code == 400


def test_expired_pair_is_rejected(client, register):
    register()
    token = client.post("/v1/agent/pair").json()["pair_token"]
    with Session(engine) as session:
        pair = session.get(AgentPair, hash_agent_token(token))
        assert pair is not None
        pair.expires_at = utcnow() - timedelta(seconds=1)
        session.add(pair)
        session.commit()

    claim = client.post("/v1/agent/pair/claim", json={"pair_token": token})
    assert claim.status_code == 400
    with Session(engine) as session:
        assert session.get(AgentPair, hash_agent_token(token)) is None


def test_unknown_pair_and_device_tokens_are_rejected(client):
    claim = client.post("/v1/agent/pair/claim", json={"pair_token": "x" * 32})
    assert claim.status_code == 400

    me = client.get("/v1/agent/me", headers={"Authorization": "Bearer not-a-token"})
    assert me.status_code == 401
