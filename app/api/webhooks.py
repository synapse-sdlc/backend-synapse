"""
Webhook receiver endpoints for external services (Jira, GitHub).

Handles:
  - pull_request events: opened, synchronize (push to PR), closed (merged or not)
  - workflow_run events:  completed with conclusion=success (Actions deployment)

Authentication: HMAC-SHA256 signature in X-Hub-Signature-256 header.
No user JWT required — GitHub signs every delivery with the shared secret.

GitHub can deliver webhooks as either:
  - application/json  → body is the raw JSON
  - application/x-www-form-urlencoded → body is form-encoded; JSON lives in the
    'payload' field (URL-decoded)
Both content types are handled transparently.
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, Header, HTTPException, Request


from app.db import get_db
from app.models.github_config import GithubConfig
from app.models.jira_config import JiraConfig
from app.utils.events import publish_feature_event
from app.models.jira_issue_link import JiraIssueLink


from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_signature(body: bytes, signature_header: str) -> None:
    """Raise 403 if the HMAC-SHA256 signature doesn't match."""
    if not settings.github_webhook_secret:
        # Webhook secret not configured — skip validation (dev/test only)
        logger.warning(
            "GITHUB_WEBHOOK_SECRET not set; skipping signature validation")
        return

    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=403, detail="Missing or invalid X-Hub-Signature-256")

    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=403, detail="Signature mismatch")


async def _parse_payload(request: Request, body: bytes) -> dict:
    """Parse the webhook payload regardless of Content-Type.

    GitHub sends either:
      - application/json                  → body is raw JSON
      - application/x-www-form-urlencoded → JSON is in the 'payload' form field
    """
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        try:
            form_data = urllib.parse.parse_qs(body.decode("utf-8"))
            raw_json = form_data.get("payload", [None])[0]
            if raw_json is None:
                raise ValueError("'payload' field missing from form body")
            return json.loads(raw_json)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid form-encoded payload: {exc}")
    else:
        try:
            return json.loads(body)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid JSON payload: {exc}")


@router.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    # ping is GitHub confirming the webhook was configured — always accept
    if x_github_event == "ping":
        logger.info("GitHub webhook ping received (hook_id=%s)",
                    request.headers.get("X-Github-Hook-Id", ""))
        return {"accepted": True, "event": "ping"}

    payload = await _parse_payload(request, body)

    if x_github_event == "pull_request":
        _handle_pull_request(payload)
    elif x_github_event == "workflow_run":
        _handle_workflow_run(payload)
    else:
        logger.debug("Unhandled GitHub event: %s", x_github_event)

    return {"accepted": True}


# ---------------------------------------------------------------------------
# Event dispatchers
# ---------------------------------------------------------------------------

def _handle_pull_request(payload: dict) -> None:
    action = payload.get("action")
    if action not in ("opened", "reopened", "edited", "synchronize", "closed"):
        return

    # Normalise: GitHub sends 'reopened' when a closed PR is re-opened
    if action == "reopened":
        action = "opened"

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", "")
    pr_number = pr.get("number")

    if not repo_full_name or not pr_number:
        logger.warning("pull_request payload missing repo/pr_number")
        return

    from app.workers.tasks import webhook_pr_update_task

    webhook_pr_update_task.delay(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        action=action,
        pr_payload={
            "title": pr.get("title", ""),
            "state": pr.get("state", "open"),
            "merged": pr.get("merged", False),
            "merged_at": pr.get("merged_at"),
            "head_sha": pr.get("head", {}).get("sha", ""),
            "head_branch": pr.get("head", {}).get("ref", ""),
        },
    )


def _handle_workflow_run(payload: dict) -> None:
    action = payload.get("action")
    if action != "completed":
        return

    run = payload.get("workflow_run", {})
    conclusion = run.get("conclusion")
    if conclusion != "success":
        # Only track successful deployments
        return

    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", "")
    head_branch = run.get("head_branch", "")

    if not repo_full_name:
        logger.warning("workflow_run payload missing repository full_name")
        return

    from app.workers.tasks import webhook_deployment_task

    webhook_deployment_task.delay(
        repo_full_name=repo_full_name,
        head_branch=head_branch,
        run_payload={
            "run_id": run.get("id"),
            "run_url": run.get("html_url", ""),
            "name": run.get("name", ""),
            "conclusion": conclusion,
            "completed_at": run.get("updated_at"),
            "head_sha": run.get("head_sha", ""),
            "pull_requests": run.get("pull_requests", []),
        },
    )


@router.post("/webhooks/github/{webhook_secret}", status_code=202)
async def github_webhook_per_project(
    webhook_secret: str,
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    """Per-project GitHub webhook receiver.

    The ``webhook_secret`` in the URL path routes the delivery to the correct
    project config.  Payload integrity is then verified using the project's
    HMAC signing secret.
    """
    body = await request.body()

    # 1. Look up project config by URL routing token
    config = db.execute(
        select(GithubConfig).where(
            GithubConfig.webhook_secret == webhook_secret)
    ).scalars().first()
    if not config:
        # Return 200 so GitHub doesn't disable the webhook for repeated 4xx
        logger.warning("GitHub per-project webhook: unknown routing secret")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    # 2. Verify HMAC signature if a signing secret is configured
    if config.signing_secret:
        if not x_hub_signature_256 or not x_hub_signature_256.startswith("sha256="):
            logger.warning(
                "GitHub per-project webhook: missing X-Hub-Signature-256 for project %s",
                config.project_id,
            )
            return JSONResponse(status_code=403, content={"detail": "Missing signature"})

        expected = "sha256=" + hmac.new(
            config.signing_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, x_hub_signature_256):
            logger.warning(
                "GitHub per-project webhook: signature mismatch for project %s",
                config.project_id,
            )
            return JSONResponse(status_code=403, content={"detail": "Signature mismatch"})

    if x_github_event == "ping":
        logger.info(
            "GitHub per-project ping for project %s (hook_id=%s)",
            config.project_id,
            request.headers.get("X-Github-Hook-Id", ""),
        )
        return {"accepted": True, "event": "ping"}

    payload = await _parse_payload(request, body)

    if x_github_event == "pull_request":
        _handle_pull_request(payload)
    elif x_github_event == "workflow_run":
        _handle_workflow_run(payload)
    else:
        logger.debug(
            "Unhandled GitHub event '%s' for project %s", x_github_event, config.project_id
        )

    return {"accepted": True}


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
        import hashlib
        import hmac
        signature = request.headers.get("x-hub-signature")
        if signature:
            expected = hmac.new(
                config.jira_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            sig_value = signature.replace("sha256=", "")
            if not hmac.compare_digest(sig_value, expected):
                logger.warning(
                    f"Jira webhook: HMAC signature mismatch for config {config.id}")
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
        logger.info(
            f"Jira webhook: {issue_key} → {new_status} (updated {len(updated_features)} feature(s))")

        # 6. Push real-time SSE updates
        for feature_id, old_status in updated_features.items():
            publish_feature_event(feature_id, {
                "type": "jira_status_update",
                "issue_key": issue_key,
                "old_status": old_status,
                "new_status": new_status,
            })

    return JSONResponse(status_code=200, content={"status": "ok", "updated": len(updated_features)})
