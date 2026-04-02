# Skill: Technical Plan Generation

## Purpose
Generate a technical implementation plan from an approved feature spec.

## Pre-requisites
- Feature spec artifact exists (parent_id required)
- Codebase must be indexed

## Steps
1. Read the spec artifact using `get_artifact`.
2. Read the architecture artifact.
3. For each user story, call `search_codebase` to find relevant existing code.
4. Call `read_file` on critical files identified in the search.
5. Generate a plan covering:
   - Affected routes and endpoints (with file paths)
   - Data flow changes (step by step: request -> middleware -> service -> DB)
   - Database migrations needed
   - New files/modules to create
   - Risk assessment
   - Sub-task breakdown (each linkable to a Jira story)
6. Store using `store_artifact` with type="plan", parent_id=spec_id.

## Output Schema
Call `store_artifact` with type="plan", parent_id="<spec_artifact_id>" and content as JSON:
{
  "feature_name": "...",
  "spec_id": "...",
  "affected_routes": [ { "path": "...", "file": "...", "change": "..." } ],
  "data_flow": [ { "step": 1, "description": "...", "component": "..." } ],
  "migrations": [ { "table": "...", "change": "...", "sql_hint": "..." } ],
  "new_files": [ { "path": "...", "purpose": "..." } ],
  "risks": [ { "description": "...", "severity": "high|medium|low", "mitigation": "..." } ],
  "subtasks": [
    { "id": "ST-001", "title": "...", "description": "...", "story_id": "US-001", "estimated_hours": 0 }
  ]
}
