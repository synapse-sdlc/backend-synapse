"""
Webhook receiver endpoints for external services (Jira, GitHub).

These endpoints are NOT authenticated via JWT — external services can't send
auth headers. Instead, they use secret tokens in the URL path.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.jira_config import JiraConfig
from app.models.jira_issue_link import JiraIssueLink
from app.utils.events import publish_feature_event

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/jira/{webhook_secret}")
async def jira_webhook(
    webhook_secret: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive Jira webhook events for real-time status sync.

    URL contains a secret token for authentication since Jira system
    webhooks cannot send custom headers.

    Always returns 200 — Jira disables webhooks after repeated failures.
    """
    # 1. Authenticate via secret in URL path
    config = db.execute(
        select(JiraConfig).where(JiraConfig.webhook_secret == webhook_secret)
    ).scalars().first()
    if not config:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    # 2. Parse payload
    try:
        body = await request.body()
        payload = __import__("json").loads(body)
    except Exception:
        logger.warning("Jira webhook: invalid JSON payload")
        return JSONResponse(status_code=200, content={"status": "ok"})

    # 3. Verify Jira's HMAC signature if jira_webhook_secret is configured
    if config.jira_webhook_secret:
        import hashlib, hmac
        signature = request.headers.get("x-hub-signature")
        if signature:
            expected = hmac.new(
                config.jira_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            sig_value = signature.replace("sha256=", "")
            if not hmac.compare_digest(sig_value, expected):
                logger.warning(f"Jira webhook: HMAC signature mismatch for config {config.id}")
                return JSONResponse(status_code=403, content={"detail": "Invalid signature"})

    event_type = payload.get("webhookEvent", "")

    # Only handle issue updates
    if event_type != "jira:issue_updated":
        return JSONResponse(status_code=200, content={"status": "ignored", "event": event_type})

    # 3. Extract issue key and new status
    issue = payload.get("issue", {})
    issue_key = issue.get("key")
    new_status = issue.get("fields", {}).get("status", {}).get("name")

    if not issue_key or not new_status:
        logger.warning(f"Jira webhook: missing issue_key or status in payload")
        return JSONResponse(status_code=200, content={"status": "ok"})

    # 4. Find matching links in our DB
    links = db.execute(
        select(JiraIssueLink).where(JiraIssueLink.issue_key == issue_key)
    ).scalars().all()

    if not links:
        # Issue not tracked by Synapse — ignore
        return JSONResponse(status_code=200, content={"status": "ok"})

    # 5. Update statuses
    now = datetime.utcnow()
    updated_features = {}
    for link in links:
        old_status = link.status
        if old_status != new_status:
            link.status = new_status
            link.status_synced_at = now
            updated_features[str(link.feature_id)] = old_status

    if updated_features:
        db.commit()
        logger.info(f"Jira webhook: {issue_key} → {new_status} (updated {len(updated_features)} feature(s))")

        # 6. Push real-time SSE updates
        for feature_id, old_status in updated_features.items():
            publish_feature_event(feature_id, {
                "type": "jira_status_update",
                "issue_key": issue_key,
                "old_status": old_status,
                "new_status": new_status,
            })

    return JSONResponse(status_code=200, content={"status": "ok", "updated": len(updated_features)})
