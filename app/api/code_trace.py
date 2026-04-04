from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import verify_extension_token
from app.models.project import Project
from app.schemas.code_trace import CodeLineageRequest, CodeLineageResponse
from app.services.code_trace_service import get_code_lineage

router = APIRouter()


@router.post(
    "/projects/{project_id}/code-lineage",
    response_model=CodeLineageResponse,
    summary="Get SDLC lineage for a code symbol",
    tags=["code-trace"],
)
def trace_code(
    project_id: UUID,
    body: CodeLineageRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(verify_extension_token),
):
    """Return the full SDLC lineage for a given code symbol within a project.

    Searches KB entries, codebase vector index, and PR file metadata to surface
    which feature, Jira tickets, PRs, tests, and deployments relate to the symbol.

    Auth is intentionally omitted here — add `Depends(get_current_user)` when
    the VS Code extension auth flow is implemented.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return get_code_lineage(db, project_id, body)
