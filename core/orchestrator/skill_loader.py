from pathlib import Path
from functools import lru_cache

SKILLS_DIR = Path(__file__).parent.parent / "skills"


@lru_cache(maxsize=16)
def _read_skill_file(skill_name: str) -> str:
    """Read skill file from disk (cached)."""
    skill_path = SKILLS_DIR / f"{skill_name}.md"
    if not skill_path.exists():
        available = [f.stem for f in SKILLS_DIR.glob("*.md")]
        raise FileNotFoundError(
            f"Skill '{skill_name}' not found. Available: {available}"
        )
    return skill_path.read_text()


def load_skill(skill_name: str, project_custom_skills: dict = None) -> str:
    """Load a skill markdown file by name.

    Checks project-level custom skills first, then falls back to cached filesystem read.
    """
    if project_custom_skills and skill_name in project_custom_skills:
        return project_custom_skills[skill_name]
    return _read_skill_file(skill_name)


def list_skills() -> list:
    """List all available skill names."""
    return [f.stem for f in SKILLS_DIR.glob("*.md")]
