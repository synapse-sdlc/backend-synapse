import json as json_mod
import hashlib
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.repository import Repository
from app.models.artifact import Artifact
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.repository import RepositoryResponse
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _project_to_response(project: Project, db: Session) -> dict:
    """Build ProjectResponse with repositories eagerly loaded."""
    repos = db.execute(
        select(Repository)
        .where(Repository.project_id == project.id)
        .order_by(Repository.created_at)
    ).scalars().all()

    # Derive analysis_status from repos if any exist
    if repos:
        statuses = [r.analysis_status for r in repos]
        if all(s == "ready" for s in statuses):
            derived_status = "ready"
        elif any(s == "failed" for s in statuses):
            derived_status = "failed"
        elif any(s == "analyzing" for s in statuses):
            derived_status = "analyzing"
        else:
            derived_status = "pending"
    else:
        derived_status = project.analysis_status

    return {
        "id": project.id,
        "name": project.name,
        "github_url": project.github_url,
        "analysis_status": derived_status,
        "config": project.config,
        "repositories": [RepositoryResponse.model_validate(r) for r in repos],
        "created_at": project.created_at,
    }


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = Project(
        name=body.name,
        org_id=user.org_id,
        config=body.config,
    )

    # Backward compatibility: if single github_url provided (old format)
    if body.github_url and not body.repositories:
        project.github_url = body.github_url
        if body.github_token:
            from app.utils.crypto import encrypt_token
            project.github_token_encrypted = encrypt_token(body.github_token)

    db.add(project)
    db.commit()
    db.refresh(project)

    # Handle repositories list (new multi-repo format)
    if body.repositories:
        for repo_data in body.repositories:
            repo = Repository(
                project_id=project.id,
                name=repo_data.name,
                repo_type=repo_data.repo_type,
                github_url=repo_data.github_url,
                config=repo_data.config,
            )
            if repo_data.github_token:
                from app.utils.crypto import encrypt_token
                repo.github_token_encrypted = encrypt_token(repo_data.github_token)
            db.add(repo)
        db.commit()

        # Trigger analysis for each repo
        from app.workers.tasks import analyze_repository_task
        repos = db.execute(
            select(Repository).where(Repository.project_id == project.id)
        ).scalars().all()
        for repo in repos:
            analyze_repository_task.delay(str(repo.id))

    elif body.github_url:
        # Legacy single-repo: create a Repository row automatically
        repo = Repository(
            project_id=project.id,
            name="main",
            github_url=body.github_url,
        )
        if body.github_token:
            from app.utils.crypto import encrypt_token
            repo.github_token_encrypted = encrypt_token(body.github_token)
        db.add(repo)
        db.commit()

        from app.workers.tasks import analyze_repository_task
        db.refresh(repo)
        analyze_repository_task.delay(str(repo.id))

    return _project_to_response(project, db)


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    result = db.execute(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    if not projects:
        return []

    # Batch-load all repositories for all projects in ONE query (avoids N+1)
    project_ids = [p.id for p in projects]
    all_repos = db.execute(
        select(Repository).where(Repository.project_id.in_(project_ids)).order_by(Repository.created_at)
    ).scalars().all()
    repo_map = {}
    for r in all_repos:
        repo_map.setdefault(r.project_id, []).append(r)

    responses = []
    for p in projects:
        repos = repo_map.get(p.id, [])
        if repos:
            statuses = [r.analysis_status for r in repos]
            if all(s == "ready" for s in statuses):
                derived_status = "ready"
            elif any(s == "failed" for s in statuses):
                derived_status = "failed"
            elif any(s == "analyzing" for s in statuses):
                derived_status = "analyzing"
            else:
                derived_status = "pending"
        else:
            derived_status = p.analysis_status

        responses.append({
            "id": p.id,
            "name": p.name,
            "github_url": p.github_url,
            "analysis_status": derived_status,
            "config": p.config,
            "repositories": [RepositoryResponse.model_validate(r) for r in repos],
            "created_at": p.created_at,
        })
    return responses


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_response(project, db)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.config is not None:
        project.config = body.config

    db.commit()
    db.refresh(project)
    return _project_to_response(project, db)


@router.post("/projects/{project_id}/reanalyze", response_model=ProjectResponse)
def reanalyze_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Reanalyze all repositories
    repos = db.execute(
        select(Repository).where(Repository.project_id == project_id)
    ).scalars().all()

    if repos:
        from app.workers.tasks import analyze_repository_task
        for repo in repos:
            repo.analysis_status = "pending"
            analyze_repository_task.delay(str(repo.id))
        db.commit()
    elif project.github_url:
        # Legacy fallback
        project.analysis_status = "pending"
        db.commit()
        from app.workers.tasks import analyze_codebase_task
        analyze_codebase_task.delay(str(project.id), project.github_url)

    db.refresh(project)
    return _project_to_response(project, db)


@router.get("/projects/{project_id}/architecture")
def get_project_architecture(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.models.artifact import Artifact

    # Return all architecture artifacts for this project (one per repo + optional unified)
    result = db.execute(
        select(Artifact)
        .where(Artifact.project_id == str(project_id), Artifact.type.in_(["architecture", "project_architecture"]))
        .order_by(Artifact.created_at.desc())
    )
    artifacts = result.scalars().all()

    if not artifacts:
        raise HTTPException(status_code=404, detail="No architecture artifact found. Run codebase analysis first.")

    # If single artifact, return it directly (backward compatible)
    if len(artifacts) == 1:
        return artifacts[0]

    # Multiple: return list
    return artifacts


@router.post("/projects/{project_id}/architecture/upload")
async def upload_architecture(
    project_id: UUID,
    file: UploadFile = File(None),
    content: str = Form(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload an existing architecture document to seed codebase analysis.

    Accepts a file (.json, .md, .txt) or raw content via form field.
    """
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")

    raw_content = None
    if file:
        raw_content = (await file.read()).decode("utf-8")
    elif content:
        raw_content = content
    else:
        raise HTTPException(status_code=400, detail="Provide a file or content")

    # Try to parse as JSON, otherwise store as raw text
    try:
        parsed = json_mod.loads(raw_content)
    except (json_mod.JSONDecodeError, TypeError):
        parsed = {"raw_text": raw_content, "source": "uploaded"}

    # Generate artifact ID
    art_id = hashlib.md5(f"architecture:uploaded:{time.time()}".encode()).hexdigest()[:12]

    artifact = Artifact(
        id=art_id,
        type="architecture",
        name="Uploaded Architecture",
        content=parsed,
        status="approved",
        version=1,
        project_id=str(project_id),
    )
    db.merge(artifact)

    project.uploaded_architecture_id = art_id
    db.commit()

    return {"artifact_id": art_id, "status": "uploaded"}


@router.get("/projects/{project_id}/metrics")
def get_project_metrics(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Aggregate project health metrics across all features."""
    from collections import Counter
    from statistics import median
    from app.models.feature import Feature
    from app.models.knowledge_entry import KnowledgeEntry
    from app.models.jira_issue_link import JiraIssueLink
    from app.services.traceability_service import detect_gaps

    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")

    # --- Features ---
    features = db.execute(
        select(Feature).where(Feature.project_id == project_id).order_by(Feature.created_at)
    ).scalars().all()
    by_phase = dict(Counter(f.phase for f in features))

    # --- Artifacts ---
    artifacts = db.execute(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.type.in_(["spec", "plan", "tests", "scaffold"]),
        )
    ).scalars().all()

    # Group by type for metrics
    arts_by_type = {}
    for a in artifacts:
        arts_by_type.setdefault(a.type, []).append(a)

    # Avg confidence by type
    avg_confidence = {}
    for t, arts in arts_by_type.items():
        scores = [a.confidence_score for a in arts if a.confidence_score is not None]
        avg_confidence[t] = round(sum(scores) / len(scores)) if scores else 0

    # First-attempt approval rate (specs with version==1 and status==approved)
    specs = arts_by_type.get("spec", [])
    first_attempt = sum(1 for s in specs if s.version == 1 and s.status == "approved")
    total_specs = len([s for s in specs if s.status in ("approved", "draft")])
    first_attempt_rate = round((first_attempt / total_specs * 100)) if total_specs else 0

    # Avg revisions by type (max version per feature per type)
    avg_revisions = {}
    for t, arts in arts_by_type.items():
        versions = [a.version for a in arts]
        avg_revisions[t] = round(sum(versions) / len(versions), 1) if versions else 1.0

    # --- Traceability coverage ---
    coverage_scores = []
    completed = [f for f in features if f.phase in ("done", "closed")]
    for f in completed:
        if f.spec_artifact_id and f.plan_artifact_id and f.tests_artifact_id:
            spec_a = db.get(Artifact, f.spec_artifact_id)
            plan_a = db.get(Artifact, f.plan_artifact_id)
            tests_a = db.get(Artifact, f.tests_artifact_id)
            if spec_a and plan_a and tests_a:
                gaps = detect_gaps(
                    spec_a.content if isinstance(spec_a.content, dict) else {},
                    plan_a.content if isinstance(plan_a.content, dict) else {},
                    tests_a.content if isinstance(tests_a.content, dict) else {},
                )
                coverage_scores.append(gaps["coverage_percent"])
    avg_coverage = round(sum(coverage_scores) / len(coverage_scores)) if coverage_scores else 0

    # --- Effort ---
    turns_list = [f.total_turns for f in completed if f.total_turns > 0]
    duration_list = [f.total_duration_ms for f in completed if f.total_duration_ms > 0]
    total_hours = sum(f.estimated_hours_saved or 0 for f in features)

    # --- Knowledge ---
    kb_entries = db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    ).scalars().all()
    kb_by_type = dict(Counter(e.entry_type for e in kb_entries))

    # Confidence delta: compare features before vs after first KB entry
    confidence_delta = 0
    if kb_entries and len(features) > 1:
        first_kb_time = min(e.created_at for e in kb_entries)
        before = [f for f in features if f.created_at < first_kb_time]
        after = [f for f in features if f.created_at >= first_kb_time]
        before_conf = [a.confidence_score for f in before for a in artifacts if a.feature_id == f.id and a.type == "spec" and a.confidence_score]
        after_conf = [a.confidence_score for f in after for a in artifacts if a.feature_id == f.id and a.type == "spec" and a.confidence_score]
        if before_conf and after_conf:
            confidence_delta = round(sum(after_conf) / len(after_conf) - sum(before_conf) / len(before_conf))

    # Confidence trend (spec confidence per feature, ordered by created_at)
    confidence_trend = []
    for f in features:
        spec_arts = [a for a in artifacts if a.feature_id == f.id and a.type == "spec" and a.confidence_score]
        if spec_arts:
            confidence_trend.append(spec_arts[-1].confidence_score)

    # --- Test coverage distribution ---
    test_by_type = Counter()
    edge_cases_in_specs = 0
    edge_case_tests = 0
    for f in features:
        # Spec edge cases
        spec_a = db.get(Artifact, f.spec_artifact_id) if f.spec_artifact_id else None
        if spec_a and isinstance(spec_a.content, dict):
            edge_cases_in_specs += len(spec_a.content.get("edge_cases", []))
        # Test distribution
        tests_a = db.get(Artifact, f.tests_artifact_id) if f.tests_artifact_id else None
        if tests_a and isinstance(tests_a.content, dict):
            for suite in tests_a.content.get("test_suites", []):
                stype = suite.get("type", "other")
                count = len(suite.get("test_cases", []))
                test_by_type[stype] += count
                if stype == "edge_case":
                    edge_case_tests += count

    # --- Jira ---
    jira_links = db.execute(
        select(JiraIssueLink).join(Feature, JiraIssueLink.feature_id == Feature.id).where(Feature.project_id == project_id)
    ).scalars().all()
    jira_by_status = dict(Counter(l.status for l in jira_links))
    features_with_jira = sum(1 for f in features if f.jira_epic_key)
    export_rate = round(features_with_jira / len(completed) * 100) if completed else 0

    return {
        "feature_count": {
            "total": len(features),
            "by_phase": by_phase,
        },
        "process_health": {
            "first_attempt_approval_rate": first_attempt_rate,
            "avg_revisions": avg_revisions,
            "avg_traceability_coverage": avg_coverage,
        },
        "quality": {
            "avg_confidence": avg_confidence,
            "confidence_trend": confidence_trend,
        },
        "effort": {
            "median_turns_per_feature": round(median(turns_list)) if turns_list else 0,
            "median_duration_ms": round(median(duration_list)) if duration_list else 0,
            "total_estimated_hours_saved": round(total_hours, 1),
        },
        "knowledge": {
            "total_entries": len(kb_entries),
            "by_type": kb_by_type,
            "confidence_delta": confidence_delta,
        },
        "test_coverage": {
            "by_type": dict(test_by_type),
            "edge_cases_in_specs": edge_cases_in_specs,
            "edge_case_tests": edge_case_tests,
        },
        "jira": {
            "total_issues": len(jira_links),
            "by_status": jira_by_status,
            "export_rate": export_rate,
        },
    }
