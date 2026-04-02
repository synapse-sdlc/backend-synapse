from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.feature import Feature
from app.models.jira_config import JiraConfig
from app.models.jira_issue_link import JiraIssueLink
from app.schemas.jira import (
    JiraConfigCreate, JiraConfigResponse, JiraExportRequest,
    JiraIssueLinkResponse, JiraLinkRequest, JiraStatusResponse,
)
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _verify_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# --- Project-level Jira config ---

@router.post("/projects/{project_id}/jira-config", response_model=JiraConfigResponse, status_code=201)
async def save_jira_config(
    project_id: UUID,
    body: JiraConfigCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    # Test connection first
    from app.services.jira_service import JiraService
    svc = JiraService(body.site_url, body.user_email, body.api_token)
    try:
        await svc.test_connection()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Jira connection failed: {e}")

    from app.utils.crypto import encrypt_token

    # Upsert
    existing = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == project_id)
    ).scalars().first()

    if existing:
        existing.site_url = body.site_url
        existing.user_email = body.user_email
        existing.api_token_encrypted = encrypt_token(body.api_token)
        existing.default_project_key = body.default_project_key
        db.commit()
        db.refresh(existing)
        return existing
    else:
        config = JiraConfig(
            project_id=project_id,
            site_url=body.site_url,
            user_email=body.user_email,
            api_token_encrypted=encrypt_token(body.api_token),
            default_project_key=body.default_project_key,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        return config


@router.get("/projects/{project_id}/jira-config", response_model=JiraConfigResponse)
def get_jira_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Jira not configured for this project")
    return config


@router.delete("/projects/{project_id}/jira-config", status_code=204)
def delete_jira_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == project_id)
    ).scalars().first()
    if config:
        db.delete(config)
        db.commit()


@router.post("/projects/{project_id}/jira-config/test")
async def test_jira_connection(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Jira not configured")

    from app.utils.crypto import decrypt_token
    from app.services.jira_service import JiraService

    token = decrypt_token(config.api_token_encrypted)
    svc = JiraService(config.site_url, config.user_email, token)
    try:
        result = await svc.test_connection()
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")


# --- Feature-level Jira operations ---

@router.post("/features/{feature_id}/jira-export", status_code=202)
def export_to_jira(
    feature_id: UUID,
    body: JiraExportRequest = JiraExportRequest(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    if feature.phase == "gathering":
        raise HTTPException(status_code=400, detail="No artifacts to export yet. Wait for spec to be generated.")

    # Verify Jira is configured
    config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=400, detail="Jira not configured for this project")

    from app.workers.tasks import jira_export_task
    jira_export_task.delay(str(feature_id), body.project_key)
    return {"status": "accepted", "feature_id": str(feature_id)}


@router.get("/features/{feature_id}/jira-issues", response_model=list[JiraIssueLinkResponse])
def list_jira_issues(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    result = db.execute(
        select(JiraIssueLink)
        .where(JiraIssueLink.feature_id == feature_id)
        .order_by(JiraIssueLink.created_at)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.post("/features/{feature_id}/jira-link", response_model=JiraIssueLinkResponse, status_code=201)
async def link_jira_issue(
    feature_id: UUID,
    body: JiraLinkRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Manually link an existing Jira ticket to a feature."""
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=400, detail="Jira not configured")

    from app.utils.crypto import decrypt_token
    from app.services.jira_service import JiraService

    token = decrypt_token(config.api_token_encrypted)
    svc = JiraService(config.site_url, config.user_email, token)

    try:
        issue = await svc.get_issue(body.issue_key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch Jira issue: {e}")

    fields = issue.get("fields", {})
    link = JiraIssueLink(
        feature_id=feature_id,
        issue_key=issue["key"],
        issue_type=fields.get("issuetype", {}).get("name", "unknown").lower(),
        issue_url=f"{config.site_url}/browse/{issue['key']}",
        summary=fields.get("summary", ""),
        status=fields.get("status", {}).get("name", "Unknown"),
        parent_issue_key=fields.get("parent", {}).get("key") if fields.get("parent") else None,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.post("/features/{feature_id}/jira-sync", status_code=202)
def sync_jira_status(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    from app.workers.tasks import jira_sync_task
    jira_sync_task.delay(str(feature_id))
    return {"status": "accepted"}


@router.get("/features/{feature_id}/jira-status", response_model=JiraStatusResponse)
def get_jira_status(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    links = db.execute(
        select(JiraIssueLink).where(JiraIssueLink.feature_id == feature_id)
    ).scalars().all()

    total = len(links)
    done = sum(1 for l in links if l.status.lower() in ("done", "closed", "resolved"))
    in_progress = sum(1 for l in links if l.status.lower() in ("in progress", "in review", "in development"))
    todo = total - done - in_progress

    return JiraStatusResponse(total=total, done=done, in_progress=in_progress, todo=todo)
