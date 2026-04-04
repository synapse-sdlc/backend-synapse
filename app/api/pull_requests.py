from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.feature import Feature
from app.models.project import Project
from app.models.pr_link import PullRequestLink
from app.schemas.pr import LinkPRRequest, PullRequestLinkResponse
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _verify_feature_access(db: Session, feature_id: UUID, user: CurrentUser) -> Feature:
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    project = db.get(Project, feature.project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Feature not found")
    return feature


def _get_github_token(db: Session, feature: Feature) -> str:
    """Get decrypted GitHub token from project or its repositories."""
    from app.models.repository import Repository
    from app.utils.crypto import decrypt_token

    # Try repos first
    repos = db.execute(
        select(Repository).where(Repository.project_id == feature.project_id)
    ).scalars().all()
    for r in repos:
        if r.github_token_encrypted:
            return decrypt_token(r.github_token_encrypted)

    # Fall back to project
    project = db.get(Project, feature.project_id)
    if project and project.github_token_encrypted:
        return decrypt_token(project.github_token_encrypted)

    raise HTTPException(
        status_code=400, detail="No GitHub token configured for this project")


@router.post("/features/{feature_id}/pr-links", response_model=PullRequestLinkResponse, status_code=201)
async def link_pr(
    feature_id: UUID,
    body: LinkPRRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    from app.services.github_service import GitHubService

    try:
        owner, repo, pr_number = GitHubService.parse_pr_url(body.pr_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = _get_github_token(db, feature)
    svc = GitHubService(token)

    try:
        pr_data = await svc.get_pull_request(owner, repo, pr_number)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch PR: {e}")

    link = PullRequestLink(
        feature_id=feature_id,
        repo_full_name=f"{owner}/{repo}",
        pr_number=pr_number,
        pr_url=body.pr_url,
        title=pr_data.get("title", ""),
        state="merged" if pr_data.get(
            "merged_at") else pr_data.get("state", "open"),
        merged_at=pr_data.get("merged_at"),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.get("/features/{feature_id}/pr-links", response_model=list[PullRequestLinkResponse])
def list_pr_links(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    feature = _verify_feature_access(db, feature_id, user)

    from app.models.jira_config import JiraConfig
    jira_config = db.execute(
        select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
    ).scalar_one_or_none()
    jira_site_url = jira_config.site_url if jira_config else None

    result = db.execute(
        select(PullRequestLink)
        .where(PullRequestLink.feature_id == feature_id)
        .order_by(PullRequestLink.created_at)
        .limit(limit).offset(offset)
    )
    links = result.scalars().all()

    responses = []
    for link in links:
        resp = PullRequestLinkResponse.model_validate(link)
        resp.jira_site_url = jira_site_url
        responses.append(resp)
    return responses


@router.post("/features/{feature_id}/pr-links/{pr_link_id}/sync", response_model=PullRequestLinkResponse)
async def sync_pr(
    feature_id: UUID,
    pr_link_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)
    link = db.get(PullRequestLink, pr_link_id)
    if not link or link.feature_id != feature_id:
        raise HTTPException(status_code=404, detail="PR link not found")

    from app.services.github_service import GitHubService
    token = _get_github_token(db, feature)
    svc = GitHubService(token)

    owner, repo = link.repo_full_name.split("/", 1)
    pr_data = await svc.get_pull_request(owner, repo, link.pr_number)

    was_open = link.state == "open"
    link.state = "merged" if pr_data.get(
        "merged_at") else pr_data.get("state", "open")
    link.merged_at = pr_data.get("merged_at")
    link.synced_at = datetime.utcnow()

    # Fetch diff details when merged and data is missing (on transition OR backfill)
    needs_files = link.state == "merged" and link.files_changed is None
    if needs_files:
        try:
            link.files_changed = await svc.get_pr_files(owner, repo, link.pr_number)
            link.commit_messages = await svc.get_pr_commits(owner, repo, link.pr_number)
            # Summarize diff (first 5000 chars)
            diff = await svc.get_pr_diff(owner, repo, link.pr_number)
            link.diff_summary = diff[:5000] if diff else None
        except Exception:
            pass  # Non-critical if diff fetch fails

        # Trigger KB update if not already done
        if not link.kb_updated:
            from app.workers.tasks import pr_kb_update_task
            pr_kb_update_task.delay(str(feature_id), str(pr_link_id))

    db.commit()
    db.refresh(link)
    return link


@router.post("/features/{feature_id}/pr-links/sync-all")
async def sync_all_prs(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    from app.workers.tasks import pr_sync_task
    pr_sync_task.delay(str(feature_id))
    return {"status": "accepted"}


@router.delete("/features/{feature_id}/pr-links/{pr_link_id}", status_code=204)
def unlink_pr(
    feature_id: UUID,
    pr_link_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_feature_access(db, feature_id, user)
    link = db.get(PullRequestLink, pr_link_id)
    if not link or link.feature_id != feature_id:
        raise HTTPException(status_code=404, detail="PR link not found")
    db.delete(link)
    db.commit()
