# Skill: Project Architecture Synthesis

## Purpose
Combine per-repo architectures into a unified project view that captures cross-repo relationships: API contracts, shared data models, end-to-end request flows, and deployment dependencies.

## Pre-requisites
- Multiple per-repo architecture artifacts exist (provided as artifact IDs)
- Codebase contexts from all repos are available

## Steps
1. Read each repo's architecture artifact using `get_artifact`.
2. For each backend/API repo, extract all API routes (method, path, handler).
3. For each frontend/mobile repo, use `grep_codebase` to find API calls (fetch, axios, api., httpClient).
4. Match provider routes to consumer calls → build api_contracts list.
5. For each backend repo, extract data models (name, fields, relationships).
6. Search frontend/mobile repos for matching type definitions or interface declarations → build shared_models list.
7. Trace 3-5 key request flows end-to-end across repos (e.g., user registration: form → API call → handler → DB → response → redirect).
8. Check infra repo (if exists) for deployment configs, env vars, docker-compose references.
9. Store using `store_artifact` with type="project_architecture".

## Output Schema
Call `store_artifact` with type="project_architecture" and content as JSON:
```json
{
  "project_name": "...",
  "repos": [
    { "name": "backend", "type": "backend", "framework": "FastAPI", "summary": "..." }
  ],
  "api_contracts": [
    {
      "method": "POST", "path": "/api/users",
      "provider": "backend",
      "consumers": ["frontend", "mobile"],
      "request_schema": { "body": {} },
      "response_schema": { "status": 201, "body": {} }
    }
  ],
  "shared_models": [
    {
      "name": "User",
      "canonical_repo": "backend",
      "used_in": ["frontend", "mobile"],
      "fields": [{ "name": "id", "type": "UUID" }]
    }
  ],
  "request_flows": [
    {
      "name": "User Registration",
      "steps": [
        { "repo": "frontend", "component": "SignupForm", "action": "POST /api/users" },
        { "repo": "backend", "component": "auth.py:create_user", "action": "validate + hash" }
      ]
    }
  ],
  "deployment_dependencies": [
    { "repo": "infra", "affects": ["backend", "frontend"], "type": "environment variables" }
  ],
  "cross_repo_patterns": [
    { "pattern": "Manual API client", "description": "Frontend has fetch calls matching backend routes" }
  ]
}
```

## Quality Checklist
- [ ] ALL backend API routes matched to frontend consumers
- [ ] ALL shared data models identified with field comparison
- [ ] At least 3 end-to-end request flows documented
- [ ] Deployment dependencies captured
