"""Test agent service: conversation history, phase transitions, artifact detection."""

import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from app.models.feature import Feature
from app.models.message import Message
from app.models.project import Project
from app.services.agent_service import (
    load_conversation_history,
    save_new_messages,
    check_for_new_artifacts,
    PHASE_SKILL_MAP,
    CONVERSATIONAL_PHASES,
)


def _create_project_and_feature(db_session, phase="gathering"):
    project = Project(name="test-project")
    db_session.add(project)
    db_session.commit()

    feature = Feature(
        project_id=project.id,
        description="Add OAuth SSO",
        phase=phase,
    )
    db_session.add(feature)
    db_session.commit()
    return project, feature


def test_load_empty_conversation(db_session):
    _, feature = _create_project_and_feature(db_session)
    history = load_conversation_history(db_session, str(feature.id))
    assert history == []


def test_load_conversation_with_messages(db_session):
    _, feature = _create_project_and_feature(db_session)

    # Add some messages
    db_session.add(Message(feature_id=feature.id, role="user", content="Add OAuth"))
    db_session.add(Message(feature_id=feature.id, role="assistant", content="Sure, let me ask questions"))
    db_session.add(Message(feature_id=feature.id, role="user", content="All users, P1"))
    db_session.commit()

    history = load_conversation_history(db_session, str(feature.id))
    assert len(history) == 3
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Add OAuth"
    assert history[1]["role"] == "assistant"
    assert history[2]["content"] == "All users, P1"


def test_save_new_messages(db_session):
    _, feature = _create_project_and_feature(db_session)

    # Simulate: old messages + new ones from agent
    messages = [
        {"role": "user", "content": "existing message"},
        {"role": "assistant", "content": "new response", "tool_calls": []},
        {"role": "tool", "content": '{"result": "ok"}', "tool_name": "read_file"},
    ]

    # old_count=1 means first message already existed, save the rest
    save_new_messages(db_session, str(feature.id), 1, messages)

    saved = db_session.query(Message).filter(Message.feature_id == feature.id).all()
    assert len(saved) == 2
    assert saved[0].role == "assistant"
    assert saved[1].role == "tool"
    assert saved[1].tool_name == "read_file"


def test_check_for_new_artifacts_spec(db_session, tmp_path):
    _, feature = _create_project_and_feature(db_session, phase="gathering")

    # Simulate agent storing an artifact (writes to ./artifacts/)
    artifact_id = "test12345678"
    artifacts_dir = Path("./artifacts")
    artifacts_dir.mkdir(exist_ok=True)
    artifact_data = {
        "id": artifact_id,
        "type": "spec",
        "name": "Test Spec",
        "content": json.dumps({"feature_name": "OAuth"}),
        "status": "draft",
        "version": 1,
    }
    (artifacts_dir / f"{artifact_id}.json").write_text(json.dumps(artifact_data))

    try:
        messages = [
            {"role": "tool", "tool_name": "store_artifact",
             "content": json.dumps({"artifact_id": artifact_id, "message": "stored"})},
        ]

        result = check_for_new_artifacts(db_session, feature, messages)

        assert result == artifact_id
        assert feature.spec_artifact_id == artifact_id
        assert feature.phase == "spec_review"
    finally:
        (artifacts_dir / f"{artifact_id}.json").unlink(missing_ok=True)


def test_check_for_new_artifacts_plan(db_session):
    _, feature = _create_project_and_feature(db_session, phase="plan_review")
    feature.spec_artifact_id = "specid000001"
    db_session.commit()

    # Create the parent spec artifact in DB first (FK constraint)
    from app.models.artifact import Artifact as ArtifactModel
    parent = ArtifactModel(id="specid000001", type="spec", name="Spec", content={}, status="approved", version=1, feature_id=feature.id)
    db_session.add(parent)
    db_session.commit()

    artifact_id = "plan12345678"
    artifacts_dir = Path("./artifacts")
    artifacts_dir.mkdir(exist_ok=True)
    artifact_data = {
        "id": artifact_id,
        "type": "plan",
        "name": "Test Plan",
        "content": json.dumps({"feature_name": "OAuth", "subtasks": []}),
        "parent_id": "specid000001",
        "status": "draft",
        "version": 1,
    }
    (artifacts_dir / f"{artifact_id}.json").write_text(json.dumps(artifact_data))

    try:
        messages = [
            {"role": "tool", "tool_name": "store_artifact",
             "content": json.dumps({"artifact_id": artifact_id})},
        ]

        result = check_for_new_artifacts(db_session, feature, messages)

        assert result == artifact_id
        assert feature.plan_artifact_id == artifact_id
        assert feature.phase == "plan_review"
    finally:
        (artifacts_dir / f"{artifact_id}.json").unlink(missing_ok=True)


def test_check_for_no_artifacts(db_session):
    _, feature = _create_project_and_feature(db_session)

    messages = [
        {"role": "assistant", "content": "Let me ask you some questions"},
    ]

    result = check_for_new_artifacts(db_session, feature, messages)
    assert result is None
    assert feature.phase == "gathering"


def test_phase_skill_mapping():
    assert PHASE_SKILL_MAP["gathering"] == "spec-drafting"
    assert PHASE_SKILL_MAP["spec_review"] == "spec-drafting"
    assert PHASE_SKILL_MAP["plan_review"] == "tech-planning"
    assert PHASE_SKILL_MAP["qa_review"] == "qa-testing"


def test_conversational_phases():
    assert "gathering" in CONVERSATIONAL_PHASES
    assert "spec_review" in CONVERSATIONAL_PHASES
    assert "plan_review" in CONVERSATIONAL_PHASES
    assert "qa_review" in CONVERSATIONAL_PHASES
    assert "done" not in CONVERSATIONAL_PHASES
