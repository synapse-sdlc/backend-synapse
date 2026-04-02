"""Extended feature tests: approve flow, list features, messages, jira preview."""

from app.models.artifact import Artifact
from app.models.feature import Feature
from app.models.message import Message


def _create_project(client):
    resp = client.post("/api/projects", json={"name": "test-project"})
    return resp.json()["id"]


def _create_feature(client, project_id, description="Add tags"):
    resp = client.post(f"/api/projects/{project_id}/features", json={"description": description})
    return resp.json()


def test_approve_spec_transitions_to_plan_review(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    # Manually set feature to spec_review with a spec artifact
    feature = db_session.get(Feature, feature_id)
    spec = Artifact(id="spec00000001", type="spec", name="Test Spec", content={"feature_name": "Tags"}, status="draft", version=1, feature_id=feature.id)
    db_session.add(spec)
    feature.phase = "spec_review"
    feature.spec_artifact_id = "spec00000001"
    db_session.commit()

    resp = client.post(f"/api/features/{feature_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] == "plan_review"

    # Verify spec artifact is approved
    db_session.refresh(spec)
    assert spec.status == "approved"


def test_approve_plan_transitions_to_qa_review(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    feature = db_session.get(Feature, feature_id)
    spec = Artifact(id="spec00000002", type="spec", name="Spec", content={}, status="approved", version=1, feature_id=feature.id)
    plan = Artifact(id="plan00000002", type="plan", name="Plan", content={"subtasks": []}, parent_id="spec00000002", status="draft", version=1, feature_id=feature.id)
    db_session.add_all([spec, plan])
    feature.phase = "plan_review"
    feature.spec_artifact_id = "spec00000002"
    feature.plan_artifact_id = "plan00000002"
    db_session.commit()

    resp = client.post(f"/api/features/{feature_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "qa_review"

    db_session.refresh(plan)
    assert plan.status == "approved"


def test_approve_tests_transitions_to_done(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    feature = db_session.get(Feature, feature_id)
    spec = Artifact(id="spec00000003", type="spec", name="Spec", content={}, status="approved", version=1, feature_id=feature.id)
    plan = Artifact(id="plan00000003", type="plan", name="Plan", content={}, parent_id="spec00000003", status="approved", version=1, feature_id=feature.id)
    tests = Artifact(id="test00000003", type="tests", name="Tests", content={"test_suites": []}, parent_id="plan00000003", status="draft", version=1, feature_id=feature.id)
    db_session.add_all([spec, plan, tests])
    feature.phase = "qa_review"
    feature.spec_artifact_id = "spec00000003"
    feature.plan_artifact_id = "plan00000003"
    feature.tests_artifact_id = "test00000003"
    db_session.commit()

    resp = client.post(f"/api/features/{feature_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "done"

    db_session.refresh(tests)
    assert tests.status == "approved"


def test_list_features(client, db_session):
    project_id = _create_project(client)
    _create_feature(client, project_id, "Feature A")
    _create_feature(client, project_id, "Feature B")

    resp = client.get(f"/api/projects/{project_id}/features")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_list_features_empty(client):
    project_id = _create_project(client)
    resp = client.get(f"/api/projects/{project_id}/features")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_messages(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    # Add messages directly to DB
    db_session.add(Message(feature_id=feature_id, role="user", content="Add OAuth"))
    db_session.add(Message(feature_id=feature_id, role="assistant", content="Sure, some questions..."))
    db_session.add(Message(feature_id=feature_id, role="tool", content='{"result": "ok"}', tool_name="read_file"))
    db_session.add(Message(feature_id=feature_id, role="user", content="P1 priority"))
    db_session.commit()

    resp = client.get(f"/api/features/{feature_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    # Should only return user + assistant messages, not tool messages
    assert len(data) == 3
    assert all(m["role"] in ("user", "assistant") for m in data)


def test_jira_preview(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    feature = db_session.get(Feature, feature_id)
    spec = Artifact(
        id="spec00000004", type="spec", name="Spec", feature_id=feature.id,
        content={
            "feature_name": "OAuth SSO",
            "priority": "P1",
            "user_stories": [
                {"id": "US-001", "role": "user", "action": "login with Google", "benefit": "easy access"}
            ],
        },
        status="approved", version=1,
    )
    plan = Artifact(
        id="plan00000004", type="plan", name="Plan", feature_id=feature.id,
        parent_id="spec00000004",
        content={
            "feature_name": "OAuth SSO",
            "subtasks": [
                {"id": "ST-001", "title": "Setup OAuth", "story_id": "US-001", "estimated_hours": 4},
                {"id": "ST-002", "title": "UI changes", "story_id": "US-001", "estimated_hours": 3},
            ],
        },
        status="approved", version=1,
    )
    db_session.add_all([spec, plan])
    feature.spec_artifact_id = "spec00000004"
    feature.plan_artifact_id = "plan00000004"
    db_session.commit()

    resp = client.get(f"/api/features/{feature_id}/jira-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["feature_name"] == "OAuth SSO"
    assert data["priority"] == "P1"
    assert data["stories"] == 1
    assert data["tasks"] == 2
    assert data["estimated_human_hours"] == 7
    assert data["estimated_ai_hours"] == 2.8


def test_jira_preview_empty(client, db_session):
    project_id = _create_project(client)
    feature_data = _create_feature(client, project_id)
    feature_id = feature_data["id"]

    resp = client.get(f"/api/features/{feature_id}/jira-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stories"] == 0
    assert data["tasks"] == 0


def test_create_project_with_token(client):
    resp = client.post("/api/projects", json={
        "name": "private-repo",
        "github_url": "https://github.com/org/private-repo",
        "github_token": "ghp_test123456789",
    })
    assert resp.status_code == 201
    data = resp.json()
    # Token should NOT be in the response
    assert "github_token" not in data
    assert "github_token_encrypted" not in data
    assert data["name"] == "private-repo"
