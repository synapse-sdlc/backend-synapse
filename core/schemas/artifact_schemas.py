"""
Artifact content validation schemas and confidence scoring.

These are intentionally lenient — all fields Optional — so the agent can
store partial artifacts while still getting a quality signal.
"""
import logging
from typing import List, Optional, Dict, Any

from pydantic import BaseModel

logger = logging.getLogger("synapse.schemas")


# ── Spec Schema ──────────────────────────────────────────────────────

class AcceptanceCriterion(BaseModel):
    given: str = ""
    when: str = ""
    then: str = ""


class UserStory(BaseModel):
    id: str = ""
    role: str = ""
    action: str = ""
    benefit: str = ""
    acceptance_criteria: List[AcceptanceCriterion] = []


class SpecSchema(BaseModel):
    feature_name: str = ""
    business_context: Optional[str] = None
    personas: List[Dict[str, Any]] = []
    priority: Optional[str] = None
    user_stories: List[UserStory] = []
    non_functional_requirements: List[str] = []
    edge_cases: List[str] = []
    out_of_scope: List[str] = []
    dependencies: List[str] = []
    success_metrics: List[str] = []
    impact_analysis: Optional[Dict[str, Any]] = None
    open_questions: List[str] = []


# ── Plan Schema ──────────────────────────────────────────────────────

class SubTask(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    story_id: str = ""
    estimated_hours: float = 0


class PlanSchema(BaseModel):
    feature_name: str = ""
    spec_id: str = ""
    affected_routes: List[Dict[str, Any]] = []
    data_flow: List[Dict[str, Any]] = []
    migrations: List[Dict[str, Any]] = []
    new_files: List[Dict[str, Any]] = []
    risks: List[Dict[str, Any]] = []
    subtasks: List[SubTask] = []


# ── Tests Schema ─────────────────────────────────────────────────────

class TestCase(BaseModel):
    id: str = ""
    title: str = ""
    description: Optional[str] = None
    preconditions: List[str] = []
    steps: List[str] = []
    expected_result: str = ""
    priority: str = "medium"
    automated: bool = False


class TestSuite(BaseModel):
    id: str = ""
    name: str = ""
    type: str = ""  # functional, edge_case, integration, regression, nfr
    story_id: str = ""
    test_cases: List[TestCase] = []


class TestsSchema(BaseModel):
    feature_name: str = ""
    spec_id: str = ""
    plan_id: str = ""
    test_suites: List[TestSuite] = []
    coverage_summary: Optional[Dict[str, Any]] = None


# ── Scaffold Schema ──────────────────────────────────────────────────

class ScaffoldFile(BaseModel):
    path: str = ""
    language: str = ""
    subtask_id: str = ""
    story_id: str = ""
    description: str = ""
    functions: List[str] = []
    content: str = ""


class ScaffoldSchema(BaseModel):
    feature_name: str = ""
    plan_id: str = ""
    spec_id: str = ""
    scaffold_files: List[ScaffoldFile] = []
    summary: Optional[Dict[str, Any]] = None


# ── Schema Map ───────────────────────────────────────────────────────

SCHEMA_MAP = {
    "spec": SpecSchema,
    "plan": PlanSchema,
    "tests": TestsSchema,
    "scaffold": ScaffoldSchema,
}


# ── Confidence Scoring ───────────────────────────────────────────────

def compute_confidence(artifact_type: str, content: dict) -> int:
    """Score 0-100 based on completeness and quality signals."""
    score = 0

    if artifact_type == "spec":
        stories = content.get("user_stories", [])
        score += min(30, len(stories) * 10)  # Up to 30 for stories
        total_acs = sum(len(s.get("acceptance_criteria", [])) for s in stories)
        score += min(20, total_acs * 3)  # Up to 20 for ACs
        score += 10 if content.get("personas") else 0
        score += 10 if content.get("edge_cases") else 0
        score += 10 if content.get("non_functional_requirements") else 0
        score += 10 if content.get("impact_analysis") else 0
        score += 10 if content.get("success_metrics") else 0

    elif artifact_type == "plan":
        subtasks = content.get("subtasks", [])
        score += min(30, len(subtasks) * 5)
        score += 20 if content.get("affected_routes") else 0
        score += 15 if content.get("data_flow") else 0
        score += 15 if content.get("migrations") is not None else 0
        score += 10 if content.get("risks") else 0
        score += 10 if subtasks and all(
            s.get("estimated_hours", 0) > 0 for s in subtasks
        ) else 0

    elif artifact_type == "tests":
        suites = content.get("test_suites", [])
        total_cases = sum(len(s.get("test_cases", [])) for s in suites)
        score += min(40, total_cases * 3)
        suite_types = {s.get("type") for s in suites}
        score += min(30, len(suite_types) * 10)  # Diversity of test types
        score += 15 if content.get("coverage_summary") else 0
        score += 15 if total_cases >= 10 else 0

    elif artifact_type == "scaffold":
        files = content.get("scaffold_files", [])
        score += min(30, len(files) * 5)  # Up to 30 for file count
        total_funcs = sum(len(f.get("functions", [])) for f in files)
        score += min(20, total_funcs * 2)  # Up to 20 for functions
        test_files = [f for f in files if "test" in f.get("path", "").lower()]
        score += 15 if test_files else 0  # Has test skeletons
        score += 10 if all(f.get("subtask_id") for f in files) else 0  # All linked to subtasks
        score += 10 if all(f.get("content") for f in files) else 0  # All have content
        score += 15 if content.get("summary") else 0

    return min(100, score)


def validate_artifact(artifact_type: str, content: dict) -> dict:
    """
    Validate artifact content against schema and compute confidence.

    Returns dict with:
      - valid: bool
      - confidence_score: int (0-100)
      - errors: list of error strings (empty if valid)
    """
    schema_cls = SCHEMA_MAP.get(artifact_type)
    if not schema_cls:
        # No schema for this type (architecture, kb) — skip validation
        return {"valid": True, "confidence_score": None, "errors": []}

    errors = []
    try:
        schema_cls.model_validate(content)
    except Exception as e:
        # Extract first few errors for the agent to fix
        error_str = str(e)
        # Truncate long error messages
        if len(error_str) > 500:
            error_str = error_str[:500] + "..."
        errors.append(error_str)
        logger.warning("Artifact validation failed for %s: %s", artifact_type, error_str[:200])

    confidence = compute_confidence(artifact_type, content)

    return {
        "valid": len(errors) == 0,
        "confidence_score": confidence,
        "errors": errors,
    }
