# Skill: Knowledge Base Update from PR

## Purpose
After a PR for a feature is merged, update the KB entry to reflect what was ACTUALLY
implemented vs what was PLANNED. This closes the feedback loop between planning and execution.

## Input
You will receive:
- The existing KB artifact (what was planned)
- The PR data: diff summary, files changed, commit messages
- The spec, plan, and tests artifact IDs (original plan)

## Steps
1. Read the existing KB artifact using `get_artifact`.
2. Read the spec and plan artifacts using `get_artifact`.
3. Analyze the PR data provided in the prompt.
4. Compare PLANNED (from spec + plan subtasks) vs ACTUAL (from PR files + commits):
   - Were all planned subtasks addressed in the PR?
   - Were there files changed that weren't in the plan?
   - Did the PR add anything beyond what was planned?
   - Were any planned items deferred?
5. Generate an UPDATED KB entry that reflects reality.
6. Add the `implementation_delta` section.
7. Store using `store_artifact` with type="kb" and parent_id of the existing KB.

## Output Schema (extends base KB schema)
Add these fields to the standard KB schema:
```json
{
  "...all existing KB fields...",
  "implementation_delta": {
    "completed_as_planned": ["subtask titles that were done as planned"],
    "deviated_from_plan": ["descriptions of how implementation differed"],
    "unplanned_additions": ["things added not in original plan"],
    "deferred_items": ["planned items not yet implemented"],
    "actual_files_changed": ["file paths from PR"]
  },
  "pr_reference": {
    "pr_url": "https://github.com/org/repo/pull/42",
    "pr_number": 42,
    "merged_at": "2026-04-05T..."
  }
}
```

## Rules
- Preserve ALL existing KB content — extend, don't replace
- Be specific in deviations: "Plan said X, PR did Y instead"
- If the PR matches the plan exactly, say so in implementation_delta
- Include actual file paths from the PR in actual_files_changed
