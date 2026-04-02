"""Test feature CRUD and phase transition endpoints."""


def _create_project(client):
    resp = client.post("/api/projects", json={"name": "test-project"})
    return resp.json()["id"]


def test_create_feature(client):
    project_id = _create_project(client)
    resp = client.post(f"/api/projects/{project_id}/features", json={
        "description": "Add OAuth SSO with Google"
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["description"] == "Add OAuth SSO with Google"
    assert data["phase"] == "gathering"
    assert data["spec_artifact_id"] is None
    assert data["plan_artifact_id"] is None
    assert data["tests_artifact_id"] is None


def test_get_feature(client):
    project_id = _create_project(client)
    create_resp = client.post(f"/api/projects/{project_id}/features", json={
        "description": "Add tags"
    })
    feature_id = create_resp.json()["id"]

    resp = client.get(f"/api/features/{feature_id}")
    assert resp.status_code == 200
    assert resp.json()["description"] == "Add tags"


def test_get_feature_not_found(client):
    resp = client.get("/api/features/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_send_message(client):
    project_id = _create_project(client)
    create_resp = client.post(f"/api/projects/{project_id}/features", json={
        "description": "Add categories"
    })
    feature_id = create_resp.json()["id"]

    resp = client.post(f"/api/features/{feature_id}/message", json={
        "content": "All users, P1 priority"
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"


def test_approve_wrong_phase(client):
    project_id = _create_project(client)
    create_resp = client.post(f"/api/projects/{project_id}/features", json={
        "description": "Add tags"
    })
    feature_id = create_resp.json()["id"]

    # Can't approve in "gathering" phase (no artifact yet)
    resp = client.post(f"/api/features/{feature_id}/approve")
    assert resp.status_code == 400
