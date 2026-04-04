# AI Agent Guide — synapse-backend

> This document helps AI coding agents (Copilot, Cursor, Kiro, Cline, etc.) understand and work with this codebase effectively.

## Project Identity

- **Name**: Synapse Backend
- **Type**: Python FastAPI REST API + AI orchestration engine
- **Runtime**: Python 3.12, async (uvicorn)
- **Database**: PostgreSQL 16 (async via SQLAlchemy + asyncpg)
- **Task Queue**: Celery + Redis
- **LLM**: AWS Bedrock (Claude) or Ollama (local)
- **Vector Store**: ChromaDB (local) or Qdrant (production)

## Key Conventions

### Code Style
- Python 3.12+ features (type hints, f-strings, walrus operator)
- Async endpoints use `async def`; sync DB sessions use `Session` from SQLAlchemy
- Pydantic v2 for all request/response schemas (`app/schemas/`)
- Pydantic Settings for configuration (`app/config.py`, env-driven)
- No ORMs for reads where raw SQL is faster — but most queries use SQLAlchemy ORM
- Logging via `logging.getLogger(__name__)` or named loggers like `synapse.orchestrator`

### Project Layout Pattern
```
app/api/{resource}.py      → FastAPI router (thin controller)
app/schemas/{resource}.py  → Pydantic request/response models
app/models/{resource}.py   → SQLAlchemy ORM model
app/services/{name}.py     → Business logic (heavy lifting)
app/workers/tasks.py       → Celery background tasks
app/utils/                 → Auth, crypto, event helpers
core/                      → AI engine (orchestrator, tools, skills, indexer)
```

### Naming Conventions
- API routers: `router = APIRouter()` in each file, mounted in `main.py`
- Models: singular PascalCase (`Feature`, `Artifact`, `Project`)
- Schemas: suffixed with purpose (`CodeLineageRequest`, `CodeLineageResponse`)
- Services: `{domain}_service.py` with plain functions (not classes)
- Database tables: snake_case plural (auto from SQLAlchemy model `__tablename__`)
- Celery tasks: `{name}_task` suffix, registered in `app/workers/tasks.py`

### Authentication
- Two auth systems coexist:
  1. Local JWT (`app/utils/auth.py`) — `get_current_user` dependency
  2. AWS Cognito (`app/utils/cognito_auth.py`) — when `COGNITO_USER_POOL_ID` is set
- Extension auth uses a static bearer token (`verify_extension_token` dependency)
- Auth dependencies are injected via `Depends()` in route handlers
- SSE and export endpoints accept `?token=` query param for browser/EventSource auth

### Database Patterns
- Alembic for migrations (Django-style via Makefile: `make makemigrations`, `make migrate`)
- UUIDs as primary keys (generated server-side)
- `created_at` / `updated_at` timestamps on all models
- Soft relationships via UUID foreign keys (not always enforced at DB level)
- Both sync and async DB URLs needed: async for FastAPI, sync for Celery workers
- Celery workers create their own sync engine via `_get_sync_session()`

## Feature Phase State Machine

This is the core domain model. Features progress through phases:

```
gathering → spec_review → plan_review → qa_review → done → closed
    ↑           ↓              ↓             ↓
    └── reject ←┘     reject ←┘    reject ←─┘
                       rollback ←──────────←─┘
```

Each phase maps to an AI agent skill:
- `gathering` / `spec_review` → `spec-drafting` skill (PO Agent)
- `plan_review` → `tech-planning` skill (Tech Lead Agent)
- `qa_review` → `qa-testing` skill (QA Agent)
- scaffold generation → `code-scaffold` skill

Phase transitions happen in `app/services/agent_service.py`:
- `check_for_new_artifacts()` — auto-advances phase when agent stores an artifact
- `run_approval_agent()` — triggers next agent after human approval
- `run_scaffold_agent()` — generates code from approved plan

Approval flow in `app/api/features.py`:
- `approve_feature()` — atomic phase transition via `UPDATE ... WHERE phase = current_phase`
- `reject_artifact()` — reverts to previous phase, marks artifact as superseded
- `rollback_feature()` — goes back to previous review phase without triggering revision
- `close_feature()` — triggers KB update task for knowledge accumulation

## Core Orchestrator (`core/`)

The AI engine is separate from the web app:

### Agent Loop (`core/orchestrator/loop.py`)
- Main loop: message → LLM call → tool execution → repeat (up to 30 turns)
- Tools execute in parallel via `asyncio.gather()`
- Each tool call has a 30-second timeout with one retry on transient errors
- Context compression kicks in after 12 messages (3-pass: tool results → assistant messages → artifact content)
- Context budget: 180K chars (~45K tokens), reserves space for system prompt + response
- Nudge mechanism: if agent finishes without calling `store_artifact`, it gets one nudge to produce output
- Auto-save fallback: if agent still doesn't store, the loop auto-saves the final text response
- `stop_on_text=True` for conversational phases (gathering, reviews) — returns immediately when agent produces text
- Langfuse tracing wraps every LLM call and tool execution for observability

### LLM Providers (`core/orchestrator/providers/`)
- `base.py` — abstract `LLMProvider` with `async chat()` method
- `ollama_provider.py` — local inference via Ollama Python SDK
  - Fallback parsing: extracts tool calls from text when model doesn't use native tool calling
  - Handles JSON-in-content, code-fenced JSON, and `{"function": ...}` variants
- `bedrock_provider.py` — AWS Bedrock Converse API
  - Supports IAM credentials (SigV4 via boto3) and bearer token (direct HTTP via httpx)
  - Converts internal message format to Bedrock's `toolUse`/`toolResult` format
  - Guardrail support via `guardrailConfig` parameter
  - 300-second timeout, 3 retries

### Skills (`core/skills/*.md`)
- Markdown files that become the system prompt for each agent persona
- Loaded by `core/orchestrator/skill_loader.py` (cached with `@lru_cache`)
- Project-level custom skills override built-in skills (stored in `project.custom_skills` JSON)
- Available skills: `spec-drafting`, `tech-planning`, `qa-testing`, `code-scaffold`, `codebase-analysis`, `kb-accumulate`, `kb-update`, `kb-update-from-pr`, `knowledge-query`, `project-synthesis`

### Tools (`core/tools/`)
All tools are registered in `core/tools/registry.py` and available to every agent run:

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional line range. Truncates at 10K chars. |
| `list_directory` | Recursive directory listing (max depth 2). Ignores `.git`, `node_modules`, `__pycache__`, etc. |
| `search_codebase` | Semantic vector search across indexed repos. Optionally includes knowledge base. |
| `grep_codebase` | Regex pattern search across file contents. Returns matches with 1 line of context. |
| `analyze_ast` | tree-sitter AST analysis — extracts functions, classes, imports. |
| `store_artifact` | Store versioned artifact (spec/plan/tests/scaffold/kb). Validates against schema, computes confidence score, syncs to S3. |
| `get_artifact` | Retrieve artifact by ID. Checks: local cache → S3 → PostgreSQL DB. |

- File sandbox (`core/tools/sandbox.py`) restricts agent file access to user's repos + artifacts only
- `SearchCodebaseTool.set_context()` and `StoreArtifactTool.set_context()` are called before each agent run to scope operations to the current project

### Indexer (`core/indexer/`)
- `static_analyzer.py` — tree-sitter AST parsing for Python, TypeScript, JavaScript, Go, Rust, Java. Respects `.gitignore`.
- `chunker.py` — splits analysis results into embeddable chunks (file summaries, function-level, class-level). Max 2000 chars per chunk. Includes actual source code.
- `vector_store.py` — factory that returns ChromaDB or Qdrant backend based on `VECTOR_STORE_PROVIDER`
- `chroma_store.py` / `qdrant_store.py` — vector store implementations with per-repo collections and cross-repo search

### Artifact Schemas (`core/schemas/artifact_schemas.py`)
- Pydantic models for spec, plan, tests, scaffold artifacts (all fields Optional for leniency)
- `validate_artifact()` — validates content and computes confidence score (0-100)
- Confidence scoring: counts completeness signals (stories, ACs, subtasks, test cases, etc.)
- Phase-aware type correction in `agent_service.py` — if agent mislabels artifact type, it's corrected based on current phase

## Celery Workers (`app/workers/`)

### Task Configuration (`celery_app.py`)
- Queue: `synapse-agents`
- `task_acks_late=True` — prevents task loss on worker crash
- `task_reject_on_worker_lost=True` — requeues on worker kill
- `worker_prefetch_multiplier=1` — one task at a time per worker
- Sentry integration for error monitoring

### Tasks (`tasks.py`)
| Task | Time Limit | Description |
|------|-----------|-------------|
| `agent_run_task` | 600s | Single agent turn for feature conversation |
| `approval_agent_task` | 600s | Next agent after approval (plan/QA generation) |
| `scaffold_generation_task` | 600s | Code scaffold from approved plan |
| `analyze_repository_task` | 600s | Full repo analysis pipeline (clone → AST → index → architecture) |
| `analyze_codebase_task` | 600s | Legacy single-repo analysis |
| `synthesize_project_task` | 600s | Cross-repo architecture synthesis (triggered when all repos ready) |
| `kb_update_task` | 600s | Generate KB entry from completed feature |
| `kb_update_from_pr_task` | 600s | Update KB from merged PR diff |
| `jira_export_task` | 120s | Export feature to Jira (epic + stories + subtasks) |

- All agent tasks use Redis distributed locks (`r.lock(f"agent:{feature_id}")`) to prevent concurrent runs
- Events published to Redis pub/sub for SSE streaming: `_publish_feature_event(feature_id, event)`
- Feature metrics tracked: `total_turns`, `total_duration_ms`, `estimated_hours_saved`
- Retry: 1 retry on transient errors (timeout, connection, 429, 503)

## Real-time Streaming (`app/api/stream.py`)
- SSE endpoint at `/api/features/{feature_id}/stream`
- Subscribes to Redis pub/sub channel `feature:{feature_id}`
- Event types: `thinking`, `tool_call`, `tool_activity`, `response`, `done`, `error`, `artifact_stored`
- Keepalive comments sent every 100ms when no events

## Important Files to Know

| File | Purpose |
|------|---------|
| `app/main.py` | App factory, all routers registered here |
| `app/config.py` | All env vars, model tier config, provider factory |
| `app/services/agent_service.py` | Agent orchestration, phase transitions, artifact detection |
| `app/services/code_trace_service.py` | Multi-signal code lineage scoring pipeline |
| `app/services/context_builder.py` | Assembles rich 4-layer context for agent |
| `app/services/traceability_service.py` | Spec→Plan→Tests gap detection |
| `app/services/github_service.py` | GitHub REST API client (PRs, diffs, commits) |
| `app/services/jira_service.py` | Jira REST API client |
| `app/services/export_service.py` | XLSX + Markdown export |
| `app/services/prompt_builder.py` | Generates AI coding prompts per subtask |
| `app/api/features.py` | Feature CRUD + approve/reject/rollback/close/export |
| `app/api/stream.py` | SSE streaming via Redis pub/sub |
| `app/workers/tasks.py` | All Celery background tasks |
| `core/orchestrator/loop.py` | Core agent loop (30 turns max) |
| `core/orchestrator/tracing.py` | Langfuse observability (no-op when not configured) |
| `core/tools/registry.py` | Tool registry (7 built-in tools) |
| `core/tools/sandbox.py` | File system sandboxing |
| `core/schemas/artifact_schemas.py` | Artifact validation + confidence scoring |

## How to Add Things

### New API Endpoint
1. Create or edit `app/api/{resource}.py` with a new `APIRouter` route
2. Add Pydantic schemas in `app/schemas/{resource}.py`
3. Register the router in `app/main.py` via `app.include_router()`
4. Add business logic in `app/services/{resource}_service.py`
5. Use `Depends(get_current_user)` for auth, `Depends(get_db)` for DB session

### New Database Model
1. Create `app/models/{name}.py` with SQLAlchemy model
2. Import it in `app/main.py` (the `import app.models.xxx` block)
3. Also import it in `app/workers/tasks.py` (the noqa import block at top)
4. Run `make makemigrations msg="add xxx table"`
5. Run `make migrate`

### New Agent Skill
1. Create `core/skills/{skill-name}.md` with the skill prompt
2. Add the skill name to `PHASE_SKILL_MAP` in `agent_service.py` if phase-linked
3. Add to `SKILL_ARTIFACT_MAP` in `loop.py` for auto-save type mapping
4. The skill loader auto-discovers markdown files from the `core/skills/` directory

### New Agent Tool
1. Create tool class in `core/tools/{category}/{tool_name}.py` with `name`, `definition`, and `async execute()` method
2. Register it in `core/tools/registry.py` by importing and adding to the `_register_builtins` list
3. The tool will be available to all agent runs automatically

### New Celery Task
1. Add the task function in `app/workers/tasks.py` with `@celery_app.task` decorator
2. Use `_get_sync_session()` for DB access (Celery workers are synchronous)
3. Use `_publish_feature_event()` or `_publish_project_event()` for SSE streaming
4. Use Redis distributed locks for tasks that shouldn't run concurrently

## Common Commands

```bash
make infra          # Start Postgres + Redis (Docker)
make api            # Run FastAPI on :8000
make worker         # Run Celery worker (2 concurrent)
make migrate        # Apply migrations
make makemigrations msg="description"  # Generate migration
make test           # Run tests
```

## Gotchas & Pitfalls

- The `core/` directory is NOT a separate package — it's imported via `PYTHONPATH=.`
- Artifact content is stored as JSON dict in the `content` column, not as a string
- The agent can mislabel artifact types — `agent_service.py` has phase-aware correction logic
- Conversation history is truncated at 80 messages with smart summarization in `agent_service.py`
- Context compression in `loop.py` is separate — it compresses tool results and assistant messages when context exceeds budget
- File sandbox (`core/tools/sandbox.py`) restricts agent file access — new repos must be added to sandbox roots
- Vector store provider is swappable at runtime via `VECTOR_STORE_PROVIDER` env var
- Both sync and async DB URLs are needed (async for API, sync for Celery workers)
- Celery tasks use `asyncio.run()` to call async agent functions from sync context
- The Ollama provider has extensive fallback parsing for models that don't support native tool calling
- Bedrock provider supports both IAM (boto3) and bearer token (httpx) authentication
- `store_artifact` validates content against Pydantic schemas and rejects empty/short content
- Artifacts are synced to S3 in a background thread (fire-and-forget, never blocks)
- `get_artifact` has a 3-tier lookup: local file cache → S3 → PostgreSQL DB
- Feature approval uses atomic `UPDATE ... WHERE phase = current_phase` to prevent race conditions
- Auto Jira export triggers on phase transition to "done" if configured
- KB update task fires when a feature is closed, accumulating patterns/decisions/lessons
