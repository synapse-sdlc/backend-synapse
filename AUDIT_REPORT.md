# Synapse Backend: Audit Report

> Generated: 2026-03-24
> Tests: 27 passing
> Files: 40 source + 7 test files

## Feature Completion Status

| Component | Status | Tests | Notes |
|-----------|--------|-------|-------|
| **Health endpoint** | DONE | 1 | |
| **Project CRUD** (create, list, get) | DONE | 6 | |
| **Project analysis trigger** (create with GitHub URL enqueues task) | DONE | 0 | Celery task enqueued but not integration tested |
| **Feature CRUD** (create, get) | DONE | 2 | |
| **Feature send message** (enqueues Celery task) | DONE | 1 | Returns 202, task not integration tested |
| **Feature approve** (phase transition + enqueue next agent) | DONE | 1 | Only tests wrong-phase rejection |
| **Artifact get** | DONE | 1 | |
| **Artifact get not found** | DONE | 1 | |
| **Artifact trace chain** (parent walk + children) | DONE | 1 | |
| **SSE streaming** (Redis pub/sub to browser) | DONE | 0 | No tests |
| **Agent service: load conversation** | DONE | 2 | Empty + with messages |
| **Agent service: save new messages** | DONE | 1 | |
| **Agent service: check for new artifacts** | DONE | 3 | Spec, plan, no artifacts |
| **Agent service: run_agent_turn** | DONE | 0 | Implemented but needs integration test with mock LLM |
| **Agent service: run_approval_agent** | DONE | 0 | Implemented but needs integration test |
| **Project service: clone to S3** | DONE | 1 | Tests local fallback |
| **Project service: download from S3** | DONE | 1 | Tests local path |
| **Project service: build context summary** | DONE | 1 | |
| **Project service: cleanup** | DONE | 1 | |
| **Celery: agent_run_task** | WIRED | 0 | Calls agent_service, publishes events, no unit test |
| **Celery: approval_agent_task** | WIRED | 0 | Same as above |
| **Celery: analyze_codebase_task** | WIRED | 0 | Full pipeline wired, no unit test |
| **Artifact service: WebToolRegistry** | STUB | 0 | TODO: DB-backed store/get artifact |
| **Config: get_provider** | DONE | 0 | Not tested |
| **Core: agent_loop** | DONE | 0 | Has on_event callback, no direct unit test in backend |
| **Core: tool registry** | DONE | 0 | Not tested in backend (tested in code-to-arc) |
| **Core: skill_loader** | DONE | 0 | Not tested in backend |
| **Core: router** | DONE | 0 | Not tested |
| **Core: ollama_provider** | DONE | 0 | Not tested |
| **Core: bedrock_provider** | DONE | 0 | Not tested |
| **Core: static_analyzer** | DONE | 0 | Not tested in backend |
| **Core: chunker** | DONE | 0 | Not tested in backend |
| **Core: vector_store** | DONE | 0 | Not tested in backend |
| **Core: embedder** | DONE | 0 | Not tested in backend |
| **Core: 7 tools** | DONE | 0 | Not tested in backend |

## Test Gap Analysis

### Priority 1: Must test (directly affects demo reliability)

| Missing Test | Why Critical | Difficulty |
|-------------|-------------|-----------|
| **Feature approve with artifacts** (spec_review -> plan_review, plan_review -> qa_review) | Core demo flow: approve triggers next agent | Easy |
| **Feature approve to done** (qa_review -> done) | Final step of demo | Easy |
| **Agent service: run_agent_turn with mock LLM** | Validates the full agent turn pipeline without real LLM | Medium |
| **Celery tasks with mock** | Validates task execution, event publishing, DB updates | Medium |
| **SSE streaming** | Validates events reach the browser | Medium |

### Priority 2: Should test (robustness)

| Missing Test | Why Important | Difficulty |
|-------------|--------------|-----------|
| **Config: get_provider** | Validates Ollama vs Bedrock switching | Easy |
| **Core: skill_loader** | Validates skill files are found and loaded | Easy |
| **Core: tool_registry** | Validates all 7 tools register correctly | Easy |
| **Project service: clone real repo** | End-to-end clone (slow, needs network) | Medium |
| **Agent service: check_for_new_artifacts for tests type** | Only spec and plan are tested, not tests | Easy |
| **Feature: list features for a project** | Missing endpoint entirely | Easy |

### Priority 3: Nice to have

| Missing Test | Notes |
|-------------|-------|
| Core indexer (static_analyzer, chunker, embedder, vector_store) | Already tested in code-to-arc, low risk |
| Core providers (Ollama, Bedrock) | Need real LLM or complex mocks |
| Core tools (read_file, list_directory, etc.) | Already tested in code-to-arc |
| Artifact markdown rendering | Already tested in code-to-arc |

## Missing Features / Known Gaps

| Gap | Impact | Fix |
|-----|--------|-----|
| **artifact_service.py is a stub** | Agents still write to filesystem, not DB. check_for_new_artifacts reads from filesystem and copies to DB as a workaround. | Implement WebToolRegistry with DB-backed store/get. Medium effort. |
| **No list features endpoint** | Frontend needs GET /api/projects/:id/features to show feature list | Add to features.py. Easy. |
| **No list messages endpoint** | Frontend needs GET /api/features/:id/messages for chat history | Add to features.py. Easy. |
| **No Jira preview endpoint** | Frontend needs GET /api/features/:id/jira-preview | Add endpoint that loads spec + plan + tests and formats. Easy. |
| **SSE events not tested** | Don't know if Redis pub/sub actually works end-to-end | Write integration test. Medium. |
| **Celery tasks not tested** | Tasks are wired but never executed in tests | Write tests with celery.conf.update(task_always_eager=True). Medium. |
| **No error recovery in agent_service** | If agent_loop throws, the feature is stuck | Add try/except, update feature phase to indicate error. Easy. |

## Current Test Count by File

| Test File | Tests | What's Covered |
|-----------|-------|---------------|
| test_health.py | 1 | Health check |
| test_projects.py | 6 | Project CRUD (create, create+github, list empty, list, get, 404) |
| test_features.py | 5 | Feature CRUD (create, get, 404, send message, approve wrong phase) |
| test_artifacts.py | 3 | Artifact get, 404, trace chain |
| test_agent_service.py | 8 | History load/save, artifact detection, phase mapping |
| test_project_service.py | 4 | Clone fallback, download, context summary, cleanup |
| **TOTAL** | **27** | |

## Recommended Next Steps (in order)

1. Add P1 tests (approve flow, mock agent turn, list endpoints)
2. Implement missing API endpoints (list features, list messages, jira preview)
3. Implement WebToolRegistry or keep filesystem workaround
4. Add SSE integration test
5. Add Celery eager-mode tests
