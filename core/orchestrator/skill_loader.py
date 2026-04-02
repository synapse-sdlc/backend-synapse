from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def load_skill(skill_name: str) -> str:
    """Load a skill markdown file by name."""
    skill_path = SKILLS_DIR / f"{skill_name}.md"
    if not skill_path.exists():
        available = [f.stem for f in SKILLS_DIR.glob("*.md")]
        raise FileNotFoundError(
            f"Skill '{skill_name}' not found. Available: {available}"
        )
    return skill_path.read_text()


def list_skills() -> list[str]:
    """List all available skill names."""
    return [f.stem for f in SKILLS_DIR.glob("*.md")]
