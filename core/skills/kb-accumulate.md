# Skill: Knowledge Accumulation

## Purpose
Maintain a rolling project-level knowledge summary that grows with every completed feature.
This is the "institutional memory" of the project — patterns, decisions, and lessons that
inform future feature development.

## Input
You will receive:
- The new feature KB entry (artifact ID provided)
- The existing accumulated_kb artifact ID (if any)

## Steps
1. Read the new feature KB entry using `get_artifact`.
2. Read the existing accumulated_kb artifact using `get_artifact` (if provided).
3. Extract from the new KB entry:
   - Key decisions → each becomes a "decision" knowledge entry
   - Architecture changes → each becomes an "architecture_change" entry
   - Patterns discovered → each becomes a "pattern" entry
   - Lessons learned → each becomes a "lesson" entry
4. Merge new entries into the existing accumulated_kb, avoiding duplicates.
5. Regenerate the accumulated_kb summary.
6. Store using `store_artifact` with type="accumulated_kb".

## Output Schema
Call `store_artifact` with type="accumulated_kb" and content as JSON:
```json
{
  "project_summary": "Current state of the project in 2-3 paragraphs",
  "feature_count": 5,
  "patterns": [
    { "name": "Repository pattern", "description": "...", "established_in": "feature_name" }
  ],
  "decisions": [
    { "decision": "Use JWT over sessions", "rationale": "...", "feature": "Auth Feature", "date": "2026-04-03" }
  ],
  "architecture_evolution": [
    { "change": "Added Redis caching", "feature": "Performance", "affected_repos": ["backend"] }
  ],
  "lessons_learned": [
    { "lesson": "Always validate at API boundary", "context": "...", "feature": "..." }
  ],
  "cross_feature_dependencies": [
    { "feature_a": "User Auth", "feature_b": "Order System", "type": "Auth middleware shared" }
  ]
}
```

## Rules
- Keep project_summary under 300 words
- Deduplicate: if a pattern or decision already exists, update rather than duplicate
- Preserve ALL existing entries from the old accumulated_kb
- Add date context to new decisions
