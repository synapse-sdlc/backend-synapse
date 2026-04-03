from uuid import UUID
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import get_db
from app.models.project import Project
from app.schemas.skill import SkillUpdate, SkillResponse
from app.deps import get_current_user, CurrentUser

router = APIRouter()

SKILLS_DIR = Path(__file__).parent.parent.parent / "core" / "skills"


def _get_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _skill_description(content: str) -> str:
    """Extract first non-empty, non-heading line as description."""
    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("---"):
            return line[:120]
    return ""


@router.get("/projects/{project_id}/skills")
def list_skills(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List all skills (built-in + custom) for a project."""
    project = _get_project(db, project_id, user)

    builtin = sorted([f.stem for f in SKILLS_DIR.glob("*.md")])
    custom_skills = project.custom_skills or {}
    custom = sorted(custom_skills.keys())
    overridden = [s for s in custom if s in builtin]

    # Build full list with metadata
    all_skills = []
    for name in builtin:
        source = "overridden" if name in custom_skills else "builtin"
        if source == "overridden":
            content = custom_skills[name]
        else:
            content = (SKILLS_DIR / f"{name}.md").read_text()
        all_skills.append({
            "name": name,
            "source": source,
            "description": _skill_description(content),
        })
    # Add custom-only skills (not overriding a built-in)
    for name in custom:
        if name not in builtin:
            all_skills.append({
                "name": name,
                "source": "custom",
                "description": _skill_description(custom_skills[name]),
            })

    return {
        "builtin": builtin,
        "custom": custom,
        "overridden": overridden,
        "skills": all_skills,
    }


@router.get("/projects/{project_id}/skills/{skill_name}", response_model=SkillResponse)
def get_skill(
    project_id: UUID,
    skill_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get skill content (custom override or built-in)."""
    project = _get_project(db, project_id, user)
    custom_skills = project.custom_skills or {}

    if skill_name in custom_skills:
        content = custom_skills[skill_name]
        source = "custom"
    else:
        skill_path = SKILLS_DIR / f"{skill_name}.md"
        if not skill_path.exists():
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        content = skill_path.read_text()
        source = "builtin"

    return SkillResponse(
        name=skill_name,
        content=content,
        source=source,
        description=_skill_description(content),
    )


@router.put("/projects/{project_id}/skills/{skill_name}")
def save_skill(
    project_id: UUID,
    skill_name: str,
    body: SkillUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create or update a custom skill for this project."""
    project = _get_project(db, project_id, user)

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Skill content cannot be empty")

    skills = project.custom_skills or {}
    skills[skill_name] = body.content
    project.custom_skills = skills
    flag_modified(project, "custom_skills")
    db.commit()

    is_override = (SKILLS_DIR / f"{skill_name}.md").exists()
    return {
        "name": skill_name,
        "source": "overridden" if is_override else "custom",
        "status": "saved",
    }


@router.delete("/projects/{project_id}/skills/{skill_name}")
def delete_skill(
    project_id: UUID,
    skill_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Delete a custom skill (reverts to built-in if one exists)."""
    project = _get_project(db, project_id, user)

    skills = project.custom_skills or {}
    if skill_name not in skills:
        raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")

    del skills[skill_name]
    project.custom_skills = skills
    flag_modified(project, "custom_skills")
    db.commit()

    has_builtin = (SKILLS_DIR / f"{skill_name}.md").exists()
    return {
        "status": "deleted",
        "reverted_to": "builtin" if has_builtin else None,
    }
