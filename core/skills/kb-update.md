# Skill: Knowledge Base Update

## Purpose
After a feature is completed and closed, consolidate all artifacts (spec, plan, test cases) into a concise knowledge base entry. This entry becomes part of the project's institutional memory.

## Input
You will receive the feature description and IDs of the spec, plan, and test artifacts. Use `get_artifact` to read each one.

## Output
Generate a structured KB entry and store it using `store_artifact` with `type="kb"`.

## KB Entry Schema (JSON)

```json
{
  "feature_name": "...",
  "summary": "One paragraph describing what was built and why",
  "key_decisions": ["Decision 1 with rationale", "Decision 2..."],
  "architecture_changes": ["Component X modified to...", "New file Y added for..."],
  "affected_components": ["component1", "component2"],
  "test_coverage": "Brief summary of test coverage",
  "risks_mitigated": ["Risk 1 and how it was handled"],
  "lessons_learned": ["Any notable patterns or gotchas"],
  "related_artifacts": {
    "spec_id": "...",
    "plan_id": "...",
    "tests_id": "..."
  }
}
```

## Instructions

1. Read the spec artifact using `get_artifact`
2. Read the plan artifact using `get_artifact`
3. Read the tests artifact using `get_artifact`
4. Synthesize a concise KB entry from all three
5. Focus on WHAT was decided and WHY — not raw details
6. Store the result using `store_artifact` with type="kb"

## Rules
- Keep the summary under 200 words
- Key decisions should capture the "why" not just the "what"
- Do NOT repeat the full spec/plan content — summarize
- Include the artifact IDs in `related_artifacts` for traceability
