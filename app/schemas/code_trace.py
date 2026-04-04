from typing import Optional
from uuid import UUID
from pydantic import BaseModel


class CodeLineageRequest(BaseModel):
    symbol: str
    file_path: Optional[str] = None
    code_snippet: Optional[str] = None
    repo_id: Optional[UUID] = None
    line_number: Optional[int] = None
    commit_shas: list[str] = []


class JiraRef(BaseModel):
    issue_key: str
    issue_type: str
    summary: str
    status: str
    issue_url: str


class PRRef(BaseModel):
    pr_number: int
    title: str
    state: str
    pr_url: str
    repo_full_name: str
    merged_at: Optional[str] = None
    deployment_status: Optional[dict] = None


class TestRef(BaseModel):
    file: str


class MatchedFeatureLineage(BaseModel):
    feature_id: UUID
    feature_description: str
    phase: str
    confidence: float  # 0.0 – 1.0

    # Requirement
    requirement_title: Optional[str] = None
    requirement_summary: Optional[str] = None

    # Jira
    jira_epic: Optional[JiraRef] = None
    jira_tickets: list[JiraRef] = []

    # Pull Requests
    pull_requests: list[PRRef] = []

    # Tests
    tests: list[TestRef] = []

    # Deployment
    latest_deployment: Optional[dict] = None


class CodeLineageResponse(BaseModel):
    symbol: str
    file_path: Optional[str] = None
    matches: list[MatchedFeatureLineage] = []
