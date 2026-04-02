"""Test artifact endpoints."""

from app.models.artifact import Artifact


def test_get_artifact(client, db_session):
    # Create artifact directly in DB
    artifact = Artifact(
        id="abc123def456",
        type="spec",
        name="Test Spec",
        content={"feature_name": "OAuth SSO", "user_stories": []},
        status="draft",
        version=1,
    )
    db_session.add(artifact)
    db_session.commit()

    resp = client.get("/api/artifacts/abc123def456")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Spec"
    assert data["type"] == "spec"
    assert data["content"]["feature_name"] == "OAuth SSO"


def test_get_artifact_not_found(client):
    resp = client.get("/api/artifacts/nonexistent1")
    assert resp.status_code == 404


def test_trace_chain(client, db_session):
    # Create a chain: architecture -> spec -> plan
    arch = Artifact(id="arch00000001", type="architecture", name="Arch", content={}, status="approved", version=1)
    spec = Artifact(id="spec00000001", type="spec", name="Spec", content={}, parent_id="arch00000001", status="approved", version=1)
    plan = Artifact(id="plan00000001", type="plan", name="Plan", content={}, parent_id="spec00000001", status="draft", version=1)

    db_session.add_all([arch, spec, plan])
    db_session.commit()

    # Trace from spec
    resp = client.get("/api/artifacts/spec00000001/trace")
    assert resp.status_code == 200
    data = resp.json()

    # Chain should have arch -> spec (walked up)
    assert len(data["chain"]) == 2
    assert data["chain"][0]["id"] == "arch00000001"
    assert data["chain"][1]["id"] == "spec00000001"

    # Children should have plan
    assert len(data["children"]) == 1
    assert data["children"][0]["id"] == "plan00000001"
