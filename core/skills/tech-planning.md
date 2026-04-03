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
6. **Check project configuration** (provided in codebase context under "## Project Configuration"):
   - If `unit_tests_enabled: true` for any repository, include subtasks for writing unit tests. For each service/module affected, add a subtask like "ST-0XX: Write unit tests for {Component}.{method}()" with the test file path and test framework (from `test_framework` config).
   - If `unit_tests_enabled` is not set or false, do NOT include unit test subtasks.
   - Note the `qa_mode` setting — if "manual" only, subtasks should not reference automated test setup.
7. Store using `store_artifact` with type="plan", parent_id=spec_id.

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

## Example Output

Here is an example of a high-quality plan artifact for reference:

```json
{
  "feature_name": "Password Reset via Email",
  "spec_id": "a1b2c3d4e5f6",
  "affected_routes": [
    { "path": "/api/auth/forgot-password", "file": "src/routes/auth.py", "change": "Add new POST endpoint" },
    { "path": "/api/auth/reset-password", "file": "src/routes/auth.py", "change": "Add new POST endpoint" }
  ],
  "data_flow": [
    { "step": 1, "description": "User submits email to /api/auth/forgot-password", "component": "AuthController" },
    { "step": 2, "description": "Generate cryptographic token (32 bytes, urlsafe_b64)", "component": "TokenService" },
    { "step": 3, "description": "Store hashed token + expiry in password_resets table", "component": "Database" },
    { "step": 4, "description": "Queue email with reset link via SendGrid", "component": "EmailService" },
    { "step": 5, "description": "User clicks link, frontend calls /api/auth/reset-password with token + new password", "component": "Frontend" },
    { "step": 6, "description": "Validate token hash, check expiry, update password hash, invalidate all tokens", "component": "AuthService" }
  ],
  "migrations": [
    { "table": "password_resets", "change": "CREATE TABLE with columns: id, user_id, token_hash, expires_at, created_at, used_at", "sql_hint": "CREATE TABLE password_resets (id UUID PRIMARY KEY, user_id UUID REFERENCES users(id), token_hash VARCHAR(128) NOT NULL, expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP DEFAULT now(), used_at TIMESTAMP)" }
  ],
  "new_files": [
    { "path": "src/services/token_service.py", "purpose": "Cryptographic token generation and validation" },
    { "path": "src/services/email_service.py", "purpose": "SendGrid integration for transactional emails" },
    { "path": "src/templates/reset_email.html", "purpose": "Email template for password reset link" }
  ],
  "risks": [
    { "description": "Token brute-force attack if rate limiting not implemented", "severity": "high", "mitigation": "Implement per-IP and per-email rate limiting (5 requests/hour)" },
    { "description": "Email deliverability issues with SendGrid", "severity": "medium", "mitigation": "Add email delivery status webhook, log failures, alert on >5% bounce rate" }
  ],
  "subtasks": [
    { "id": "ST-001", "title": "Create password_resets migration", "description": "Add password_resets table with token_hash, expiry, and user_id FK", "story_id": "US-001", "estimated_hours": 1 },
    { "id": "ST-002", "title": "Implement TokenService", "description": "Generate secure random tokens, hash with SHA256, validate against DB", "story_id": "US-001", "estimated_hours": 2 },
    { "id": "ST-003", "title": "Implement forgot-password endpoint", "description": "POST /api/auth/forgot-password — validate email, create token, queue email", "story_id": "US-001", "estimated_hours": 2 },
    { "id": "ST-004", "title": "Implement reset-password endpoint", "description": "POST /api/auth/reset-password — validate token, update password, invalidate tokens", "story_id": "US-001", "estimated_hours": 2 },
    { "id": "ST-005", "title": "Implement EmailService with SendGrid", "description": "Send transactional email with reset link, handle errors gracefully", "story_id": "US-001", "estimated_hours": 2 },
    { "id": "ST-006", "title": "Add rate limiting middleware", "description": "Rate limit forgot-password to 5 req/email/hour and 20 req/IP/hour", "story_id": "US-001", "estimated_hours": 1.5 },
    { "id": "ST-007", "title": "Implement admin reset activity log", "description": "Query password_resets table with filters for admin dashboard", "story_id": "US-002", "estimated_hours": 1.5 }
  ]
}
```
