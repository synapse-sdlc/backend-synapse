"""
Context builder: assembles rich multi-layered context for the agent.

Replaces the flat project.codebase_context string with a structured
context that includes repo summaries, project architecture, relevant
knowledge entries, and configuration.
"""

import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feature import Feature
from app.models.project import Project
from app.models.repository import Repository
from app.models.artifact import Artifact
from app.models.knowledge_entry import KnowledgeEntry

logger = logging.getLogger(__name__)

MAX_REPO_CONTEXT_CHARS = 30_000   # ~7.5K tokens per repo
MAX_TOTAL_CONTEXT_CHARS = 80_000  # ~20K tokens total


def build_agent_context(db: Session, feature: Feature, user_role: str = "developer") -> str:
    """Build rich multi-layered context for the agent.

    Layer 1: Per-repo codebase contexts (combined from all repos)
    Layer 2: Unified project architecture (cross-repo relationships)
    Layer 3: Relevant knowledge entries (from past features)
    Layer 4: Project and repo configuration
    """
    project = db.get(Project, feature.project_id)
    if not project:
        return ""

    sections = []

    # --- Layer 1: Combined repo contexts ---
    repos = db.execute(
        select(Repository)
        .where(Repository.project_id == feature.project_id, Repository.analysis_status == "ready")
    ).scalars().all()

    if repos:
        for r in repos:
            if r.codebase_context:
                header = f"## Repository: {r.name}"
                if r.repo_type:
                    header += f" ({r.repo_type})"
                header += f"\nURL: {r.github_url}"
                ctx = r.codebase_context
                if len(ctx) > MAX_REPO_CONTEXT_CHARS:
                    ctx = ctx[:MAX_REPO_CONTEXT_CHARS] + "\n\n[... truncated — use search_codebase and read_file tools for details]"
                sections.append(f"{header}\n{ctx}")
    elif project.codebase_context:
        sections.append(project.codebase_context)

    # --- Layer 2: Unified project architecture ---
    if project.uploaded_architecture_id:
        arch_artifact = db.get(Artifact, project.uploaded_architecture_id)
        if arch_artifact and arch_artifact.content:
            sections.append(_format_project_architecture(arch_artifact.content))

    # Also check for project_architecture type artifact
    proj_arch = db.execute(
        select(Artifact)
        .where(Artifact.project_id == str(feature.project_id), Artifact.type == "project_architecture")
        .order_by(Artifact.created_at.desc())
    ).scalars().first()
    if proj_arch and proj_arch.content:
        sections.append(_format_project_architecture(proj_arch.content))

    # --- Layer 3: Relevant knowledge from past features ---
    knowledge = _get_relevant_knowledge(db, feature.project_id, feature.description)
    if knowledge:
        sections.append(knowledge)

    # --- Layer 4: Configuration ---
    config_section = _build_config_section(project, repos)
    if config_section:
        sections.append(config_section)

    combined = "\n\n---\n\n".join(sections) if sections else ""
    if len(combined) > MAX_TOTAL_CONTEXT_CHARS:
        combined = combined[:MAX_TOTAL_CONTEXT_CHARS] + "\n\n[... context truncated — use search_codebase and read_file tools to explore further]"
    return combined


def _format_project_architecture(content: dict) -> str:
    """Format project architecture artifact content as context string."""
    lines = ["## Project Architecture (Unified)"]

    if isinstance(content, dict):
        # API contracts
        contracts = content.get("api_contracts", [])
        if contracts:
            lines.append("\n### API Contracts")
            for c in contracts[:20]:
                provider = c.get("provider", "?")
                consumers = ", ".join(c.get("consumers", []))
                lines.append(f"- {c.get('method', '?')} {c.get('path', '?')} — served by {provider}, consumed by {consumers}")

        # Shared models
        models = content.get("shared_models", [])
        if models:
            lines.append("\n### Shared Data Models")
            for m in models[:15]:
                used_in = ", ".join(m.get("used_in", []))
                lines.append(f"- {m.get('name', '?')} (canonical: {m.get('canonical_repo', '?')}, used in: {used_in})")

        # Request flows
        flows = content.get("request_flows", [])
        if flows:
            lines.append("\n### Key Request Flows")
            for f in flows[:5]:
                lines.append(f"\n**{f.get('name', 'Flow')}:**")
                for step in f.get("steps", []):
                    lines.append(f"  {step.get('repo', '?')}/{step.get('component', '?')}: {step.get('action', '?')}")

    return "\n".join(lines)


def _get_relevant_knowledge(db: Session, project_id, feature_description: str) -> str:
    """Load recent patterns and decisions from knowledge_entries table."""
    lines = []

    # Load established patterns
    patterns = db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.project_id == project_id, KnowledgeEntry.entry_type == "pattern")
        .order_by(KnowledgeEntry.created_at.desc())
        .limit(10)
    ).scalars().all()

    if patterns:
        lines.append("## Established Patterns (from past features)")
        for p in patterns:
            lines.append(f"- **{p.title}**: {p.content[:200]}")

    # Load recent decisions
    decisions = db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.project_id == project_id, KnowledgeEntry.entry_type == "decision")
        .order_by(KnowledgeEntry.created_at.desc())
        .limit(10)
    ).scalars().all()

    if decisions:
        lines.append("\n## Recent Decisions")
        for d in decisions:
            lines.append(f"- **{d.title}**: {d.content[:200]}")

    # Load recent lessons
    lessons = db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.project_id == project_id, KnowledgeEntry.entry_type == "lesson")
        .order_by(KnowledgeEntry.created_at.desc())
        .limit(5)
    ).scalars().all()

    if lessons:
        lines.append("\n## Lessons Learned")
        for l in lessons:
            lines.append(f"- **{l.title}**: {l.content[:200]}")

    return "\n".join(lines) if lines else ""


def _build_config_section(project: Project, repos: list) -> str:
    """Build configuration context string."""
    lines = []

    if project.config:
        lines.append("## Project Configuration")
        for k, v in project.config.items():
            lines.append(f"- {k}: {v}")

    for r in repos:
        if r.config:
            lines.append(f"\n### {r.name} Configuration")
            for k, v in r.config.items():
                lines.append(f"- {k}: {v}")

    return "\n".join(lines) if lines else ""
