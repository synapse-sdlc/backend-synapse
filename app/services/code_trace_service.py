"""Code lineage service: given a symbol + file path, return full SDLC trace.

Strategy:
  Fast path  — if commit SHAs are provided (from git blame), look up the PR
               that contains those commits directly in the DB. If found, return
               the linked feature immediately with confidence=1.0. No scoring.

  Fallback   — when no SHA hit (uncommitted code, PR not yet synced, etc.),
               run the multi-signal scoring pipeline:
               1. PR file-path scan
               2. KB vector search
               3. Codebase vector search
               4. KB keyword match
               5. Artifact content search (spec/plan/tests JSON)
               6. Feature description keyword match
               Return ranked top-5.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.artifact import Artifact
from app.models.feature import Feature
from app.models.jira_issue_link import JiraIssueLink
from app.models.pr_link import PullRequestLink
from app.models.knowledge_entry import KnowledgeEntry
from app.models.repository import Repository
from app.schemas.code_trace import (
    CodeLineageRequest,
    CodeLineageResponse,
    JiraRef,
    MatchedFeatureLineage,
    PRRef,
    TestRef,
)

log = logging.getLogger(__name__)


def _extract_test_files(content: dict) -> list[TestRef]:
    """Pull test file names out of a tests artifact content dict."""
    files: list[TestRef] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        # Normalise: strip trailing qualifiers, keep basename
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            files.append(TestRef(file=name))

    for suite in content.get("test_suites", []):
        if isinstance(suite, dict):
            suite_name = suite.get("name", "")
            if suite_name:
                _add(suite_name)
            for tc in suite.get("test_cases", []):
                if isinstance(tc, dict):
                    tc_file = tc.get("file") or tc.get("test_file") or ""
                    if tc_file:
                        _add(tc_file)

    for tc in content.get("test_cases", []):
        if isinstance(tc, dict):
            tc_file = tc.get("file") or tc.get("test_file") or ""
            if tc_file:
                _add(tc_file)

    return files


def _spec_summary(content: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (title, summary) from a spec artifact content dict."""
    title: Optional[str] = content.get("title") or content.get("feature_name")
    summary: Optional[str] = content.get(
        "summary") or content.get("description")
    if not summary:
        # Try first user story
        stories = content.get("user_stories", [])
        if stories and isinstance(stories[0], dict):
            summary = stories[0].get("description") or stories[0].get("action")
    return title, summary


def _enrich_feature(
    db: Session,
    feat: Feature,
    confidence: float,
) -> MatchedFeatureLineage:
    """Load all SDLC artefacts for one feature and return a lineage record."""
    req_title: Optional[str] = None
    req_summary: Optional[str] = None
    if feat.spec_artifact_id:
        spec = db.get(Artifact, feat.spec_artifact_id)
        if spec and isinstance(spec.content, dict):
            req_title, req_summary = _spec_summary(spec.content)

    test_refs: list[TestRef] = []
    if feat.tests_artifact_id:
        tests_art = db.get(Artifact, feat.tests_artifact_id)
        if tests_art and isinstance(tests_art.content, dict):
            test_refs = _extract_test_files(tests_art.content)

    jira_rows = db.execute(
        select(JiraIssueLink).where(JiraIssueLink.feature_id == feat.id)
    ).scalars().all()
    jira_epic: Optional[JiraRef] = None
    jira_tickets: list[JiraRef] = []
    for row in jira_rows:
        ref = JiraRef(
            issue_key=row.issue_key,
            issue_type=row.issue_type,
            summary=row.summary,
            status=row.status,
            issue_url=row.issue_url,
        )
        if row.issue_type == "epic":
            jira_epic = ref
        else:
            jira_tickets.append(ref)

    pr_rows = db.execute(
        select(PullRequestLink).where(PullRequestLink.feature_id == feat.id)
    ).scalars().all()
    pr_refs: list[PRRef] = []
    latest_deployment: Optional[dict] = None
    for row in pr_rows:
        pr_refs.append(PRRef(
            pr_number=row.pr_number,
            title=row.title,
            state=row.state,
            pr_url=row.pr_url,
            repo_full_name=row.repo_full_name,
            merged_at=row.merged_at.isoformat() if row.merged_at else None,
            deployment_status=row.deployment_status,
        ))
        if row.deployment_status and not latest_deployment:
            latest_deployment = row.deployment_status

    return MatchedFeatureLineage(
        feature_id=feat.id,
        feature_description=feat.description[:200],
        phase=feat.phase,
        confidence=round(min(1.0, confidence), 2),
        requirement_title=req_title,
        requirement_summary=req_summary,
        jira_epic=jira_epic,
        jira_tickets=jira_tickets,
        pull_requests=pr_refs,
        tests=test_refs,
        latest_deployment=latest_deployment,
    )


def get_code_lineage(
    db: Session,
    project_id: UUID,
    request: CodeLineageRequest,
) -> CodeLineageResponse:
    """Build SDLC lineage for the given code symbol within a project."""

    # ── Fast path: git blame SHA → PR commit → feature (deterministic) ────────
    # If the extension sent commit SHAs from git blame, do a direct DB lookup:
    # find which PullRequestLink contains those commits, get the linked feature,
    # and return immediately — no scoring needed.
    if request.commit_shas:
        pr_links_all = db.execute(
            select(PullRequestLink).where(
                PullRequestLink.feature_id.in_(
                    select(Feature.id).where(Feature.project_id == project_id)
                )
            )
        ).scalars().all()

        # feature_id → best confidence
        matched_feature_ids: dict[UUID, float] = {}
        for pr in pr_links_all:
            commits = pr.commit_messages
            if not isinstance(commits, list):
                continue
            for commit_entry in commits:
                if not isinstance(commit_entry, dict):
                    continue
                stored_sha = commit_entry.get("sha", "")
                if not stored_sha:
                    continue
                for req_sha in request.commit_shas:
                    # Prefix match handles full 40-char vs abbreviated SHAs
                    if req_sha.startswith(stored_sha) or stored_sha.startswith(req_sha):
                        matched_feature_ids[pr.feature_id] = 1.0
                        break

        if matched_feature_ids:
            log.debug(
                "git blame fast path hit: %d feature(s) for project %s",
                len(matched_feature_ids),
                project_id,
            )
            matches = []
            for fid, confidence in matched_feature_ids.items():
                feat = db.get(Feature, fid)
                if feat and str(feat.project_id) == str(project_id):
                    matches.append(_enrich_feature(db, feat, confidence))
            return CodeLineageResponse(
                symbol=request.symbol,
                file_path=request.file_path,
                matches=matches,
            )

        log.debug(
            "git blame fast path miss (no stored commits matched) — falling through to scoring"
        )

    # ── Scoring fallback ───────────────────────────────────────────────────────
    # Runs when: no SHAs were sent, or SHAs didn't match any stored PR commits
    # (uncommitted code, PR not yet synced with GitHub, etc.)

    query_parts = [request.symbol]
    if request.file_path:
        query_parts.append(request.file_path)
    if request.code_snippet:
        query_parts.append(request.code_snippet[:300])
    query = " ".join(query_parts)

    scores: dict[UUID, float] = {}

    def _add_score(fid: UUID, delta: float) -> None:
        scores[fid] = scores.get(fid, 0.0) + delta

    # ── Score 1: PR file-path scan ─────────────────────────────────────────────
    if request.file_path:
        basename = request.file_path.split("/")[-1]
        pr_links_with_file = db.execute(
            select(PullRequestLink).where(
                PullRequestLink.feature_id.in_(
                    select(Feature.id).where(Feature.project_id == project_id)
                ),
                text("files_changed::text ILIKE :pat"),
            ).params(pat=f"%{basename}%")
        ).scalars().all()
        for pr in pr_links_with_file:
            _add_score(pr.feature_id, 0.5)

    # ── Score 2: Vector search — KB entries ────────────────────────────────────
    try:
        from core.indexer.vector_store import VectorStore
        store = VectorStore()
        kb_hits = store.search_knowledge(str(project_id), query, n_results=10)
        for hit in kb_hits:
            meta = hit.get("metadata") or {}
            fid_raw = meta.get("feature_id")
            if fid_raw:
                try:
                    fid = UUID(str(fid_raw))
                    _add_score(
                        fid, max(0.0, 1.0 - float(hit.get("distance", 1.0))) * 0.4)
                except (ValueError, TypeError):
                    pass
    except Exception as exc:
        log.warning("KB vector search failed: %s", exc)

    # ── Score 3: Vector search — codebase repos ────────────────────────────────
    try:
        repos = db.execute(
            select(Repository).where(
                Repository.project_id == project_id,
                Repository.analysis_status == "ready",
            )
        ).scalars().all()
        if repos:
            store = VectorStore()
            repo_ids = [str(r.id) for r in repos]
            code_hits = store.search_all_repos(
                str(project_id), repo_ids, query, n_results=10)
            for hit in code_hits:
                meta = hit.get("metadata") or {}
                fid_raw = meta.get("feature_id")
                if fid_raw:
                    try:
                        fid = UUID(str(fid_raw))
                        _add_score(
                            fid, max(0.0, 1.0 - float(hit.get("distance", 1.0))) * 0.3)
                    except (ValueError, TypeError):
                        pass
    except Exception as exc:
        log.warning("Codebase vector search failed: %s", exc)

    # ── Score 4: KB keyword match ──────────────────────────────────────────────
    symbol_lower = request.symbol.lower()
    kb_entries = db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.project_id == project_id)
        .limit(200)
    ).scalars().all()
    for entry in kb_entries:
        text_body = f"{entry.title} {entry.content}".lower()
        if symbol_lower in text_body and entry.feature_id:
            _add_score(entry.feature_id, 0.3)

    # ── Score 5: Artifact content search (spec/plan/tests JSON) ───────────────
    import json as _json

    all_features = db.execute(
        select(Feature).where(Feature.project_id == project_id)
    ).scalars().all()

    if request.file_path or len(request.symbol) > 4:
        art_ids: list[UUID] = []
        feat_art_ids: dict[UUID, list[UUID]] = {}
        for feat in all_features:
            aids = [
                aid for aid in [
                    feat.spec_artifact_id,
                    feat.plan_artifact_id,
                    feat.tests_artifact_id,
                ]
                if aid
            ]
            if aids:
                feat_art_ids[feat.id] = aids
                art_ids.extend(aids)

        if art_ids:
            bulk_arts = db.execute(
                select(Artifact).where(Artifact.id.in_(art_ids))
            ).scalars().all()
            art_by_id = {a.id: a for a in bulk_arts}

            file_basename = (
                request.file_path.split(
                    "/")[-1].lower() if request.file_path else ""
            )
            sym_lower_long = request.symbol.lower() if len(request.symbol) > 4 else ""
            file_scored: set[UUID] = set()
            sym_scored: set[UUID] = set()

            for feat in all_features:
                for art_id in feat_art_ids.get(feat.id, []):
                    art = art_by_id.get(art_id)
                    if not art or not isinstance(art.content, dict):
                        continue
                    content_text = _json.dumps(art.content).lower()
                    if file_basename and feat.id not in file_scored and file_basename in content_text:
                        _add_score(feat.id, 0.35)
                        file_scored.add(feat.id)
                    if sym_lower_long and feat.id not in sym_scored and sym_lower_long in content_text:
                        _add_score(feat.id, 0.15)
                        sym_scored.add(feat.id)

    # ── Score 6: Feature description keyword match ─────────────────────────────
    for feat in all_features:
        if symbol_lower in feat.description.lower():
            _add_score(feat.id, 0.2)

    # ── Return top matches ─────────────────────────────────────────────────────
    if not scores:
        return CodeLineageResponse(
            symbol=request.symbol,
            file_path=request.file_path,
            matches=[],
        )

    lineage_map = {f.id: f for f in all_features}
    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)[:5]
    matches = []
    for fid in sorted_ids:
        feat = lineage_map.get(fid)
        if feat is not None:
            matches.append(_enrich_feature(db, feat, scores[fid]))

    return CodeLineageResponse(
        symbol=request.symbol,
        file_path=request.file_path,
        matches=matches,
    )
