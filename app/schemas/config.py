from typing import Optional
from pydantic import BaseModel


class RepoConfig(BaseModel):
    """Per-repository configuration."""
    unit_tests_enabled: bool = False
    test_framework: Optional[str] = None  # pytest, jest, junit, go_test, etc.
    qa_mode: str = "both"  # manual, automated, both
    pr_naming_convention: Optional[str] = None  # e.g. "feat/{jira_key}-{description}"
    branch_convention: Optional[str] = None  # e.g. "feature/{jira_key}-{slug}"
    ci_cd_provider: Optional[str] = None  # github_actions, gitlab_ci, none


class ProjectConfig(BaseModel):
    """Project-level configuration shared across repos."""
    jira_project_key: Optional[str] = None
    default_qa_mode: str = "both"
    require_unit_tests: bool = False
    auto_export_jira: bool = True  # Auto-create Jira tickets when QA approves (phase → done)
