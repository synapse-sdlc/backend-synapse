# Skill: Codebase Analysis

## Purpose
Analyze a repository and generate a comprehensive, team-ready architecture document.

## Strategy — Be Thorough
You have grep_codebase, search_codebase, read_file (with line ranges), analyze_ast, and list_directory.
Use ALL of them. A 900-file codebase needs 15+ turns of exploration, not 5.

**Use grep_codebase to find:**
- Model/schema definitions: `grep_codebase` for `class.*Model`, `class.*Schema`, `BaseModel`
- Route/endpoint definitions: grep for `@app.route`, `path(`, `@router`, `urlpatterns`
- Database/ORM usage: grep for `models.`, `ForeignKey`, `relationship`, `Column`
- Config/env vars: grep for `os.getenv`, `os.environ`, `settings.`, `config.`
- External API calls: grep for `requests.`, `httpx.`, `boto3.`, `client.`
- Celery/async tasks: grep for `@task`, `@shared_task`, `celery`, `.delay(`
- Auth patterns: grep for `authenticate`, `permission`, `login`, `jwt`, `token`
- Error handling: grep for `try:`, `except`, `raise`, `Error`

**Use read_file with line ranges for large files:**
- Don't read entire 500-line files. Read the first 50 lines to understand structure, then specific sections.
- Read models.py in chunks: first 100 lines, then grep for specific class names and read those sections.
- Read settings/config files fully (usually small).

**Use analyze_ast on key files to get function/class listings, then read_file on specific sections.**

## Steps
1. Call `list_directory` on the repo root with max_depth=3 to understand full structure.
2. Call `read_file` on config files: package.json, requirements.txt, pyproject.toml, go.mod, Cargo.toml, settings.py, .env.example.
3. Call `grep_codebase` for model/schema definitions to find ALL data models.
4. Call `read_file` (with line ranges) on model files to get actual field definitions.
5. Call `grep_codebase` for route/endpoint definitions to map the full API surface.
6. Call `read_file` on route files to get endpoint details (methods, permissions, serializers).
7. Call `grep_codebase` for external service integrations (API clients, SDKs, webhooks).
8. Call `grep_codebase` for env vars / config to document deployment requirements.
9. Call `grep_codebase` for auth patterns to document security model.
10. Call `analyze_ast` on entry points and service files for function signatures.
11. Call `search_codebase` for conceptual queries: "database migration", "background job", "caching", "logging".
12. For 3-5 critical code sections (main entry point, core business logic, key patterns), call `read_file` with specific line ranges to capture representative code snippets (10-30 lines each).
13. Synthesize ALL findings into a deeply detailed architecture document.

## Output Schema
Call `store_artifact` with type="architecture" and content as JSON:
{
  "name": "<project name>",
  "description": "<what this project does, in 2-3 sentences>",
  "framework": "<detected framework + version>",
  "language": "<primary language + version>",
  "entry_point": "<main entry file>",
  "dependencies": { "<package>": "<purpose>" },
  "file_map": { "<path>": "<description>" },
  "layers": [
    {
      "id": "...",
      "label": "...",
      "components": [
        {
          "id": "...",
          "file": "...",
          "description": "...",
          "key_functions": ["func_name() — what it does"],
          "tools": [{ "name": "...", "description": "..." }]
        }
      ]
    }
  ],
  "connections": [
    { "from": "component_id", "to": "component_id", "protocol": "detailed description" }
  ],
  "data_models": [
    {
      "name": "ModelName",
      "file": "path/to/models.py",
      "type": "Django Model / Pydantic / SQLAlchemy / etc",
      "fields": [
        { "name": "field_name", "type": "field_type", "required": true/false }
      ],
      "relationships": ["ForeignKey to OtherModel", "ManyToMany with X"],
      "note": "business purpose of this model"
    }
  ],
  "api_routes": [
    {
      "method": "GET/POST/PUT/DELETE",
      "path": "/api/...",
      "handler": "file:function_name",
      "auth": "required/optional/none",
      "description": "what this endpoint does"
    }
  ],
  "external_services": [
    {
      "name": "Service Name",
      "type": "what kind of service",
      "endpoint": "URL or config var",
      "protocol": "REST/gRPC/SOAP/etc",
      "purpose": "why this project uses it",
      "config_vars": ["ENV_VAR_1", "ENV_VAR_2"]
    }
  ],
  "execution_flow": {
    "description": "how a request flows through the system",
    "steps": ["1. ...", "2. ...", "3. ..."]
  },
  "async_architecture": {
    "broker": "what message broker",
    "tasks": ["task_name — what it does"],
    "queues": ["queue names"]
  },
  "security": {
    "auth_method": "JWT/Token/Session/etc",
    "permissions": "how permissions work",
    "notes": ["HIPAA/GDPR/etc considerations"]
  },
  "config_vars": [
    { "name": "ENV_VAR", "purpose": "what it configures", "required": true/false }
  ],
  "design_patterns": [
    { "pattern": "Pattern Name", "description": "how it's used in this codebase" }
  ],
  "key_code_examples": [
    {
      "title": "descriptive title",
      "file": "path/to/file.py",
      "lines": "start-end",
      "description": "why this code matters",
      "code": "actual code snippet"
    }
  ]
}

## Merging with Existing Architecture

If the codebase context includes an "Existing Architecture Document (provided by team)" section:
1. Read it carefully first — this is ground truth from the team.
2. Use it as your starting framework for the output.
3. VALIDATE claims against the actual codebase (grep for mentioned files, models, routes).
4. ADD what you discover that isn't in the document.
5. CORRECT anything in the document that contradicts the codebase.
6. PRESERVE all information from the uploaded document unless you can prove it's wrong.

## Quality Checklist
- [ ] ALL directories explored (not just top-level)
- [ ] ALL model/schema classes documented with their fields
- [ ] ALL API routes listed with methods and auth requirements
- [ ] ALL external services identified with config vars
- [ ] Data flow documented end-to-end
- [ ] Auth/security model documented
- [ ] Background jobs / async tasks documented
- [ ] Config/env vars comprehensively listed
- [ ] Design patterns identified and explained
- [ ] 3-5 key code examples included with explanations
