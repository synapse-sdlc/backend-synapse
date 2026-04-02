from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.knowledge_entry import KnowledgeEntry
from app.models.api_contract import ApiContract
from app.models.shared_model import SharedModel
from app.models.artifact import Artifact
from app.schemas.knowledge import (
    KnowledgeEntryResponse, KnowledgeQueryRequest, KnowledgeQueryResponse,
    ApiContractResponse, SharedModelResponse,
)
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _verify_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/projects/{project_id}/knowledge/query")
async def query_knowledge(
    project_id: UUID,
    body: KnowledgeQueryRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Answer a knowledge question using two-tier retrieval + single LLM call.

    Tier 1: Vector search KB entries + codebase for targeted context (~1-2s)
    Tier 2: Single LLM call with pre-fetched context (~5-10s)

    No agent loop, no tool calls, no Celery — runs inline, returns answer directly.
    """
    import logging
    log = logging.getLogger(__name__)

    _verify_project(db, project_id, user)

    from app.models.repository import Repository

    # --- Persona-aware retrieval strategy ---
    # Different personas need different data, not just different prompts
    is_code_persona = body.persona in ("developer", "tech_lead")
    is_product_persona = body.persona in ("po",)
    is_qa_persona = body.persona in ("qa",)

    repos = db.execute(
        select(Repository).where(
            Repository.project_id == project_id,
            Repository.analysis_status == "ready",
        )
    ).scalars().all()

    # --- Tier 1a: KB entries (all personas get these, but weighted differently) ---
    kb_type_filter = None
    if is_product_persona:
        kb_type_filter = ["decision", "pattern"]  # PO cares about what was decided
    elif is_qa_persona:
        kb_type_filter = ["lesson", "pattern", "architecture_change"]  # QA cares about what changed and risks

    kb_query = select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    if kb_type_filter:
        kb_query = kb_query.where(KnowledgeEntry.entry_type.in_(kb_type_filter))
    entries = db.execute(
        kb_query.order_by(KnowledgeEntry.created_at.desc()).limit(20)
    ).scalars().all()

    kb_context = ""
    if entries:
        kb_context = "\n## Project Knowledge Base\n"
        for e in entries:
            kb_context += f"\n### [{e.entry_type}] {e.title}\n{e.content[:400]}\n"

    # --- Tier 1b: Artifact content (PO gets specs, QA gets tests, Dev gets plans) ---
    artifact_context = ""
    if is_product_persona or is_qa_persona:
        # Load relevant feature artifacts
        from app.models.feature import Feature as FeatureModel
        features = db.execute(
            select(FeatureModel).where(FeatureModel.project_id == project_id).limit(5)
        ).scalars().all()
        for f in features:
            if is_product_persona and f.spec_artifact_id:
                art = db.get(Artifact, f.spec_artifact_id)
                if art and isinstance(art.content, dict):
                    stories = art.content.get("user_stories", [])
                    if stories:
                        artifact_context += f"\n## Feature: {f.description[:80]}\n"
                        for s in stories[:5]:
                            if isinstance(s, dict):
                                artifact_context += f"- {s.get('id', '')}: As a {s.get('role', '?')}, I want {s.get('action', '?')}\n"
            if is_qa_persona and f.tests_artifact_id:
                art = db.get(Artifact, f.tests_artifact_id)
                if art and isinstance(art.content, dict):
                    suites = art.content.get("test_suites", [])
                    if suites:
                        artifact_context += f"\n## Tests for: {f.description[:80]}\n"
                        for suite in suites[:3]:
                            if isinstance(suite, dict):
                                artifact_context += f"- {suite.get('name', '?')} ({suite.get('type', '?')}): {len(suite.get('test_cases', []))} test cases\n"

    # --- Tier 1c: Repo codebase summaries (dev/TL get full, PO gets brief) ---
    repo_context = ""
    for r in repos:
        if r.codebase_context:
            if is_code_persona:
                repo_context += f"\n## Repository: {r.name}\n{r.codebase_context}\n"
            else:
                # PO/QA: just repo name and first 200 chars
                repo_context += f"\n## Repository: {r.name}\n{r.codebase_context[:200]}\n"

    # --- Tier 1d: Vector search (dev/TL get code, PO/QA get knowledge) ---
    vector_context = ""
    try:
        from core.indexer.vector_store import VectorStore
        store = VectorStore()

        # All personas: search knowledge entries
        kb_results = store.search_knowledge(str(project_id), body.question, n_results=5)
        if kb_results:
            vector_context += "\n## Relevant Knowledge (semantic search)\n"
            for r in kb_results:
                vector_context += f"\n- {r['content'][:500]}\n"

        # Dev/TL only: search codebase for actual code
        if is_code_persona:
            repo_ids = [str(r.id) for r in repos]
            if repo_ids:
                code_results = store.search_all_repos(str(project_id), repo_ids, body.question, n_results=8)
                if code_results:
                    vector_context += "\n## Relevant Code\n"
                    for r in code_results:
                        meta = r.get("metadata", {})
                        vector_context += f"\n### {meta.get('file', '?')} (lines {meta.get('line_start', '?')}-{meta.get('line_end', '?')})\n```\n{r['content'][:800]}\n```\n"
    except Exception as e:
        log.warning(f"Vector search failed: {e}")

    # --- Tier 2: Single LLM call with persona-tuned prompt ---
    persona_prompts = {
        "po": """You are answering a Product Owner. They care about:
- What features exist and their business purpose
- User stories, acceptance criteria, personas
- Decisions made and their rationale
- Feature status and what's been delivered
DO NOT show code, file paths, or technical implementation details.
Use plain business language. Reference user stories by ID (US-001).""",

        "qa": """You are answering a QA Engineer. They care about:
- What test coverage exists and what's missing
- Which areas are affected by recent changes
- Regression risks and edge cases
- Test case references (TC-001, TS-001)
Include component names and affected areas but not raw code.
Flag any areas without test coverage.""",

        "developer": """You are answering a Developer. They care about:
- Exact file paths, function names, line numbers
- Code patterns and how things are implemented
- API contracts between components
- Data flow and architecture decisions
Include code snippets when relevant. Be specific with file:line references.""",

        "tech_lead": """You are answering a Tech Lead. They care about:
- Architecture decisions and trade-offs
- Cross-repo dependencies and data flow
- Risk areas and technical debt
- Patterns established across the project
Include file paths and architectural context. Reference decisions from the KB.""",
    }

    system_prompt = f"""You are a Synapse knowledge assistant for a software project.
Answer the user's question based ONLY on the provided context below.

{persona_prompts.get(body.persona, persona_prompts['developer'])}

## Response Rules
- Answer directly — lead with the answer, not preamble
- Cite sources: [file:path:line] or [KB: title] or [US-001] or [TC-001]
- If the answer isn't in the context, say "I don't have enough information about that"
- Do NOT make up information not present in the context

{repo_context}
{artifact_context}
{kb_context}
{vector_context}
"""

    from app.config import get_provider
    provider = get_provider()

    try:
        result = await provider.chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": body.question}],
            tools=[],
            max_tokens=4096,
        )
        answer = result.get("content", "").strip()
        return {
            "answer": answer or "No answer found.",
            "confidence": "high" if len(answer) > 100 else "medium",
            "sources": [],
            "follow_up_questions": [],
        }
    except Exception as e:
        log.exception(f"Knowledge query LLM call failed: {body.question}")
        return {
            "answer": f"Sorry, I couldn't answer that question right now. Error: {str(e)[:200]}",
            "confidence": "low",
            "sources": [],
            "follow_up_questions": [],
        }


@router.get("/projects/{project_id}/knowledge/entries", response_model=list[KnowledgeEntryResponse])
def list_knowledge_entries(
    project_id: UUID,
    entry_type: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    query = select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    if entry_type:
        query = query.where(KnowledgeEntry.entry_type == entry_type)
    if tag:
        query = query.where(KnowledgeEntry.tags.any(tag))

    query = query.order_by(KnowledgeEntry.created_at.desc()).offset(offset).limit(limit)
    return db.execute(query).scalars().all()


@router.get("/projects/{project_id}/knowledge/entries/{entry_id}", response_model=KnowledgeEntryResponse)
def get_knowledge_entry(
    project_id: UUID,
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    entry = db.get(KnowledgeEntry, entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@router.get("/projects/{project_id}/knowledge/summary")
def get_knowledge_summary(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    # Find accumulated_kb artifact
    artifact = db.execute(
        select(Artifact)
        .where(Artifact.project_id == str(project_id), Artifact.type == "accumulated_kb")
        .order_by(Artifact.created_at.desc())
    ).scalars().first()

    if artifact:
        return artifact.content

    # Fallback: aggregate from knowledge_entries
    entries = db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    ).scalars().all()

    return {
        "total_entries": len(entries),
        "by_type": {
            t: sum(1 for e in entries if e.entry_type == t)
            for t in ("decision", "pattern", "lesson", "architecture_change")
        },
        "recent": [
            {"title": e.title, "type": e.entry_type, "content": e.content[:200]}
            for e in sorted(entries, key=lambda x: x.created_at, reverse=True)[:10]
        ],
    }


# --- API Contracts ---

@router.get("/projects/{project_id}/contracts", response_model=list[ApiContractResponse])
def list_contracts(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    _verify_project(db, project_id, user)
    result = db.execute(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.method, ApiContract.path)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/projects/{project_id}/shared-models", response_model=list[SharedModelResponse])
def list_shared_models(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    _verify_project(db, project_id, user)
    result = db.execute(
        select(SharedModel)
        .where(SharedModel.project_id == project_id)
        .order_by(SharedModel.name)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()
