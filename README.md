# 🧠 Synapse Backend

**AI-powered SDLC orchestration engine** — the brain behind Synapse's multi-agent pipeline that turns feature descriptions into specs, technical plans, QA test suites, and code scaffolds.

Built for the hackathon. Built to ship.

---

## What is Synapse?

Synapse is a full-stack platform that automates the software development lifecycle using AI agents. You describe a feature in plain English, and Synapse's agent pipeline produces:

1. **Product Spec** — structured requirements with user stories and acceptance criteria (PO Agent)
2. **Technical Plan** — implementation subtasks, architecture decisions, API contracts (Tech Lead Agent)
3. **QA Test Suite** — comprehensive test cases mapped to acceptance criteria (QA Agent)
4. **Code Scaffold** — starter code generated from the approved plan
5. **Traceability** — full lineage from code symbol → PR → feature → Jira ticket → spec

Each phase has a human-in-the-loop review gate: approve, request changes, or rollback.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI (async) |
| Database | PostgreSQL 16 (SQLAlchemy + Alembic) |
| Task Queue | Celery + Redis |
| LLM Providers | AWS Bedrock (Claude) / Ollama (local) |
| Vector Store | ChromaDB (local) / Qdrant (production) |
| Code Analysis | tree-sitter (AST parsing) |
| Auth | JWT (local) + AWS Cognito (production) |
| Observability | Langfuse (LLM tracing) + Sentry (errors) |
| Containerization | Docker |

## Project Structure

```
synapse-backend/
├── app/                        # FastAPI application
│   ├── main.py                 # App entrypoint, router registration, CORS
│   ├── config.py               # Pydantic settings (env-driven config)
│   ├── db.py                   # SQLAlchemy engine + session factory
│   ├── deps.py                 # FastAPI dependency injection
│   ├── api/                    # Route handlers (REST endpoints)
│   │   ├── auth.py             # Login/signup, JWT issuance
│   │   ├── projects.py         # Project CRUD
│   │   ├── features.py         # Feature lifecycle (create, approve, reject, rollback)
│   │   ├── artifacts.py        # Artifact CRUD (specs, plans, tests, scaffolds)
│   │   ├── stream.py           # SSE streaming for real-time agent updates
│   │   ├── code_trace.py       # Code lineage API (VS Code extension)
│   │   ├── repositories.py     # Repo upload + codebase analysis
│   │   ├── knowledge.py        # Knowledge base CRUD
│   │   ├── jira.py             # Jira integration endpoints
│   │   ├── pull_requests.py    # PR linking + GitHub integration
│   │   ├── webhooks.py         # GitHub/Jira webhook receivers
│   │   └── ...
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── project.py          # Project with multi-repo support
│   │   ├── feature.py          # Feature with phase state machine
│   │   ├── artifact.py         # Versioned artifacts (spec/plan/tests/scaffold)
│   │   ├── repository.py       # Linked repositories with analysis status
│   │   ├── message.py          # Chat message history
│   │   ├── knowledge_entry.py  # Accumulated patterns/decisions/lessons
│   │   ├── pr_link.py          # PR tracking with deployment status
│   │   └── ...
│   ├── schemas/                # Pydantic request/response schemas
│   ├── services/               # Business logic layer
│   │   ├── agent_service.py    # Core agent orchestration (phase transitions)
│   │   ├── code_trace_service.py   # Multi-signal code lineage scoring
│   │   ├── context_builder.py  # Rich multi-layered agent context assembly
│   │   ├── traceability_service.py # Spec→Plan→Tests gap detection
│   │   ├── github_service.py   # GitHub API integration
│   │   ├── jira_service.py     # Jira API integration
│   │   └── ...
│   ├── utils/                  # Auth helpers, crypto, event bus
│   └── workers/                # Celery async tasks
│       ├── celery_app.py       # Celery configuration
│       └── tasks.py            # Background jobs (repo analysis, etc.)
├── core/                       # AI orchestration engine
│   ├── orchestrator/
│   │   ├── loop.py             # Main agent loop (tool-use cycle)
│   │   ├── router.py           # Skill routing logic
│   │   ├── skill_loader.py     # Markdown skill file parser
│   │   ├── tracing.py          # Langfuse observability integration
│   │   └── providers/          # LLM provider adapters
│   │       ├── ollama_provider.py
│   │       └── bedrock_provider.py
│   ├── indexer/                # Codebase indexing pipeline
│   │   ├── static_analyzer.py  # tree-sitter AST analysis
│   │   ├── chunker.py          # Code chunking for embeddings
│   │   ├── embedder.py         # Embedding generation
│   │   ├── vector_store.py     # Vector store abstraction
│   │   ├── chroma_store.py     # ChromaDB implementation
│   │   └── qdrant_store.py     # Qdrant implementation
│   ├── skills/                 # Agent skill definitions (markdown)
│   │   ├── spec-drafting.md
│   │   ├── tech-planning.md
│   │   ├── qa-testing.md
│   │   ├── code-scaffold.md
│   │   ├── codebase-analysis.md
│   │   └── ...
│   ├── tools/                  # Agent tool implementations
│   │   ├── registry.py         # Tool registry
│   │   ├── sandbox.py          # File system sandboxing
│   │   ├── artifacts/          # store_artifact, get_artifact
│   │   ├── codebase/           # search_codebase, read_file
│   │   └── external/           # GitHub, Jira tool wrappers
│   └── schemas/                # Artifact JSON schemas
├── alembic/                    # Database migrations
├── tests/                      # Test suite
├── docker-compose.yml          # Local infra (Postgres, Redis)
├── Makefile                    # Dev commands
├── Dockerfile                  # Production container
└── requirements.txt            # Python dependencies
```

## Quick Start

### Prerequisites
- Python 3.12+
- Docker & Docker Compose
- (Optional) Ollama for local LLM inference

### Setup

```bash
# 1. Clone and enter the repo
cd synapse-backend

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set JWT_SECRET and ENCRYPTION_KEY (see comments in file)

# 5. Start infrastructure (Postgres + Redis)
make infra

# 6. Run database migrations
make migrate

# 7. Start the API server (in one terminal)
make api

# 8. Start the Celery worker (in another terminal)
make worker
```

The API will be available at `http://localhost:8000`. Swagger docs at `http://localhost:8000/docs`.

### Key Make Commands

| Command | Description |
|---------|-------------|
| `make infra` | Start Postgres + Redis via Docker Compose |
| `make api` | Run FastAPI dev server on :8000 |
| `make worker` | Run Celery worker |
| `make migrate` | Apply pending Alembic migrations |
| `make makemigrations msg="description"` | Generate new migration |
| `make test` | Run test suite |

## Architecture Highlights

### Agent Pipeline (Phase State Machine)

```
gathering → spec_review → plan_review → qa_review → done → closed
    ↑           ↓              ↓             ↓
    └── reject ←┘     reject ←┘    reject ←─┘
                       rollback ←──────────←─┘
```

Each phase maps to a specialized AI agent skill. The `agent_service.py` orchestrates transitions, and artifacts are versioned with full history.

### Core Orchestrator (Agent Loop)

The agent loop (`core/orchestrator/loop.py`) is the heart of the system:
- Up to 30 turns per run: LLM call → tool execution → repeat
- Tools execute in parallel via `asyncio.gather()` with 30s timeout per tool
- Context compression after 12 messages (3-pass: tool results → assistant text → artifact content)
- Context budget: 180K chars (~45K tokens)
- Nudge mechanism: if agent finishes without storing an artifact, it gets one nudge
- Auto-save fallback: if agent still doesn't store, the loop saves the final text
- Langfuse tracing wraps every LLM call and tool execution

### Agent Tools

7 built-in tools available to every agent run:

| Tool | Description |
|------|-------------|
| `read_file` | Read file with optional line range (truncates at 10K chars) |
| `list_directory` | Recursive directory listing (max depth 2) |
| `search_codebase` | Semantic vector search across indexed repos |
| `grep_codebase` | Regex pattern search with context lines |
| `analyze_ast` | tree-sitter AST extraction (functions, classes, imports) |
| `store_artifact` | Store versioned artifact with schema validation + confidence scoring |
| `get_artifact` | Retrieve artifact (local cache → S3 → PostgreSQL) |

File sandbox restricts agent access to user's repos + artifacts only.

### Code Lineage (Trace)

The code trace system uses a multi-signal scoring pipeline:
1. Git blame SHA → PR commit lookup (fast path, confidence=1.0)
2. PR file-path scan
3. KB vector search (ChromaDB/Qdrant)
4. Codebase vector search
5. KB keyword match
6. Artifact content search (spec/plan/tests JSON)
7. Feature description keyword match

### Codebase Indexing Pipeline

When a repository is added:
1. Clone from GitHub → upload to S3
2. tree-sitter AST analysis (Python, TypeScript, JavaScript, Go, Rust, Java)
3. Chunk into embeddable pieces (file summaries, function-level, class-level)
4. Index into per-repo vector collection (ChromaDB or Qdrant)
5. Build codebase context summary
6. Run AI agent to generate architecture overview
7. If multi-repo project and all repos ready → synthesize unified architecture

### LLM Provider Abstraction

Supports tiered model selection:
- **Fast** — quick drafts (Claude Haiku / Qwen 8B)
- **Balanced** — most tasks (Claude Sonnet / Qwen 8B)
- **Powerful** — complex features (Claude Opus / Qwen 32B)

Switch between Ollama (local) and Bedrock (cloud) via `SYNAPSE_PROVIDER` env var.

Ollama provider includes fallback parsing for models that don't support native tool calling (extracts tool calls from JSON in text). Bedrock provider supports both IAM credentials and bearer token auth.

### Celery Background Tasks

All heavy work runs in Celery workers:
- Agent conversations, approvals, scaffold generation (600s timeout)
- Repository analysis pipeline
- Cross-repo architecture synthesis
- Knowledge base updates (on feature close)
- Jira export (on approval to "done")
- Redis distributed locks prevent concurrent agent runs on the same feature
- Events published to Redis pub/sub for real-time SSE streaming

## API Overview

All endpoints are prefixed with `/api` except webhooks.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/auth/login` | POST | JWT login |
| `/api/auth/signup` | POST | User registration |
| `/api/auth/me` | GET | Current user info |
| `/api/projects` | GET/POST | Project CRUD |
| `/api/projects/{id}` | GET/PATCH | Project detail/update |
| `/api/projects/{id}/features` | GET/POST | Feature management |
| `/api/projects/{id}/repositories` | GET/POST | Repository management |
| `/api/projects/{id}/architecture` | GET | Unified project architecture |
| `/api/projects/{id}/knowledge/query` | POST | AI knowledge query |
| `/api/projects/{id}/knowledge/entries` | GET | Knowledge base entries |
| `/api/projects/{id}/contracts` | GET | API contracts |
| `/api/projects/{id}/shared-models` | GET | Shared data models |
| `/api/projects/{id}/skills` | GET/PUT/DELETE | Custom skill management |
| `/api/projects/{id}/jira-config` | GET/POST/DELETE | Jira configuration |
| `/api/projects/{id}/github-config` | GET/POST/DELETE | GitHub configuration |
| `/api/projects/{id}/extension-config` | GET/POST/DELETE | VS Code extension config |
| `/api/features/{id}` | GET | Feature detail |
| `/api/features/{id}/message` | POST | Send message to agent |
| `/api/features/{id}/approve` | POST | Approve current phase |
| `/api/features/{id}/reject` | POST | Request changes with feedback |
| `/api/features/{id}/rollback` | POST | Roll back to previous phase |
| `/api/features/{id}/close` | POST | Finalize feature (triggers KB update) |
| `/api/features/{id}/stream` | GET (SSE) | Real-time agent event stream |
| `/api/features/{id}/messages` | GET | Chat message history |
| `/api/features/{id}/traceability` | GET | Spec→Plan→Tests gap analysis |
| `/api/features/{id}/generate-scaffold` | POST | Generate code scaffold |
| `/api/features/{id}/task-prompts` | GET | AI coding prompts per subtask |
| `/api/features/{id}/jira-export` | POST | Export to Jira |
| `/api/features/{id}/jira-issues` | GET | Linked Jira issues |
| `/api/features/{id}/pr-links` | GET/POST/DELETE | PR link management |
| `/api/features/{id}/export/xlsx` | GET | Excel export |
| `/api/features/{id}/export/markdown` | GET | Markdown export |
| `/api/features/{id}/tests/export` | GET | Test cases CSV export |
| `/api/projects/{id}/code-lineage` | POST | Code trace (VS Code ext) |
| `/api/projects/{id}/metrics` | GET | Project analytics |
| `/api/artifacts/{id}` | GET | Fetch artifact |
| `/api/artifacts/{id}/trace` | GET | Artifact chain trace |
| `/api/artifacts/{id}/diff` | GET | Artifact version diff |
| `/api/model-tiers` | GET | Available LLM model tiers |
| `/webhooks/github` | POST | GitHub webhook receiver |
| `/webhooks/jira/{secret}` | POST | Jira webhook receiver |

## Environment Variables

See `.env.example` for the full list. Key ones:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET` | Yes | Secret for signing JWTs |
| `ENCRYPTION_KEY` | Yes | Fernet key for encrypting tokens |
| `SYNAPSE_PROVIDER` | No | `ollama` (default) or `bedrock` |
| `DATABASE_URL` | No | PostgreSQL connection string |
| `REDIS_URL` | No | Redis connection string |
| `VECTOR_STORE_PROVIDER` | No | `chromadb` (default) or `qdrant` |

## License

Hackathon project — Synapse Team
