"""
AI Task Prompt Builder.

Assembles structured prompts per subtask that any AI coding agent
(Cursor, Claude Code, Copilot) can use to implement the task with full context.
"""

CHARS_PER_TOKEN = 4
MAX_TOKENS = 8000


def build_task_prompt(
    subtask: dict,
    spec_content: dict,
    plan_content: dict,
    tests_content: dict = None,
    scaffold_content: dict = None,
    knowledge_entries: list = None,
    codebase_summary: str = "",
    repo_name: str = "",
    repo_type: str = "",
    feature_name: str = "",
) -> dict:
    """Assemble a complete AI coding prompt for a single subtask."""
    tests_content = tests_content or {}
    scaffold_content = scaffold_content or {}
    knowledge_entries = knowledge_entries or []

    story_id = subtask.get("story_id", "")
    subtask_id = subtask.get("id", "")

    # Find parent story
    story = None
    for s in spec_content.get("user_stories", []):
        if s.get("id") == story_id:
            story = s
            break

    sections = []
    included = []

    # --- 1. Task + Story + ACs (always) ---
    lines = [f"# Task: {subtask.get('title', 'Untitled')}"]
    lines.append("")
    lines.append("## Context")
    lines.append(f"You are implementing subtask **{subtask_id}** for the feature \"{feature_name}\".")
    if story:
        lines.append(f"This is part of user story **{story_id}**: As a {story.get('role', '...')}, I want {story.get('action', '...')}, so that {story.get('benefit', '...')}.")
    lines.append("")
    if spec_content.get("priority"):
        lines.append(f"**Priority:** {spec_content['priority']}")
    if subtask.get("estimated_hours"):
        lines.append(f"**Estimated effort:** {subtask['estimated_hours']}h")
    if repo_name:
        lines.append(f"**Repository:** {repo_name}" + (f" ({repo_type})" if repo_type else ""))
    lines.append("")

    lines.append("## What To Build")
    lines.append(subtask.get("description", "No description provided."))
    lines.append("")

    if story and story.get("acceptance_criteria"):
        lines.append("## Acceptance Criteria (must ALL pass)")
        for ac in story["acceptance_criteria"]:
            if isinstance(ac, dict):
                lines.append(f"- [ ] **GIVEN** {ac.get('given', '')} **WHEN** {ac.get('when', '')} **THEN** {ac.get('then', '')}")
            else:
                lines.append(f"- [ ] {ac}")
        lines.append("")

    sections.append("\n".join(lines))
    included.extend(["task", "story", "acs"])

    # --- 2. Scaffold code (if exists) ---
    scaffold_files = [f for f in scaffold_content.get("scaffold_files", []) if f.get("subtask_id") == subtask_id]
    if scaffold_files:
        s_lines = ["## Scaffold Code (start from here, don't write from scratch)"]
        for sf in scaffold_files:
            s_lines.append(f"\n### `{sf.get('path', '?')}`")
            if sf.get("functions"):
                s_lines.append(f"Functions to implement: {', '.join(sf['functions'])}")
            lang = sf.get("language", "")
            s_lines.append(f"```{lang}")
            s_lines.append(sf.get("content", "# No content"))
            s_lines.append("```")
        s_lines.append("")
        sections.append("\n".join(s_lines))
        included.append("scaffold")

    # --- 3. Files to modify + new files ---
    routes = plan_content.get("affected_routes", [])
    new_files = plan_content.get("new_files", [])
    if routes:
        f_lines = ["## Files To Modify"]
        for r in routes:
            if isinstance(r, dict):
                f_lines.append(f"- `{r.get('method', '')} {r.get('path', '')}` in `{r.get('file', '')}` — {r.get('change', '')}")
        f_lines.append("")
        sections.append("\n".join(f_lines))
        included.append("files")

    if new_files:
        nf_lines = ["## New Files To Create"]
        for nf in new_files:
            if isinstance(nf, dict):
                nf_lines.append(f"- `{nf.get('path', '')}` — {nf.get('purpose', '')}")
        nf_lines.append("")
        sections.append("\n".join(nf_lines))

    # --- 4. Test cases ---
    if tests_content.get("test_suites"):
        t_lines = ["## Tests That Must Pass"]
        for suite in tests_content["test_suites"]:
            if suite.get("story_id") == story_id or not story_id:
                t_lines.append(f"\n### {suite.get('name', 'Tests')} ({suite.get('type', '')})")
                for tc in suite.get("test_cases", [])[:5]:
                    t_lines.append(f"**{tc.get('id', '')}: {tc.get('title', '')}** ({tc.get('priority', 'medium')})")
                    if tc.get("preconditions"):
                        t_lines.append(f"- Preconditions: {'; '.join(tc['preconditions']) if isinstance(tc['preconditions'], list) else tc['preconditions']}")
                    if tc.get("steps"):
                        t_lines.append(f"- Steps: {'; '.join(tc['steps']) if isinstance(tc['steps'], list) else tc['steps']}")
                    if tc.get("expected_result"):
                        t_lines.append(f"- Expected: {tc['expected_result']}")
                    t_lines.append("")
        sections.append("\n".join(t_lines))
        included.append("tests")

    # --- 5. Data flow + migrations ---
    data_flow = plan_content.get("data_flow", [])
    if data_flow:
        df_lines = ["## Data Flow"]
        for step in data_flow:
            if isinstance(step, dict):
                df_lines.append(f"{step.get('step', '')}. **{step.get('component', '')}** — {step.get('description', '')}")
        df_lines.append("")
        sections.append("\n".join(df_lines))
        included.append("data_flow")

    migrations = plan_content.get("migrations", [])
    if migrations:
        m_lines = ["## Database Changes"]
        for m in migrations:
            if isinstance(m, dict):
                m_lines.append(f"- Table `{m.get('table', '')}`: {m.get('change', '')}")
                if m.get("sql_hint"):
                    m_lines.append(f"```sql\n{m['sql_hint']}\n```")
        m_lines.append("")
        sections.append("\n".join(m_lines))
        included.append("migrations")

    # --- 6. Edge cases + NFRs ---
    edge_cases = spec_content.get("edge_cases", [])
    if edge_cases:
        e_lines = ["## Edge Cases To Handle"]
        for ec in edge_cases:
            e_lines.append(f"- {ec}")
        e_lines.append("")
        sections.append("\n".join(e_lines))
        included.append("edge_cases")

    nfrs = spec_content.get("non_functional_requirements", [])
    if nfrs:
        n_lines = ["## Non-Functional Requirements"]
        for n in nfrs:
            n_lines.append(f"- {n}")
        n_lines.append("")
        sections.append("\n".join(n_lines))
        included.append("nfrs")

    # --- 7. Risks ---
    risks = plan_content.get("risks", [])
    if risks:
        r_lines = ["## Risks & Mitigations"]
        for r in risks:
            if isinstance(r, dict):
                r_lines.append(f"- **[{r.get('severity', 'medium').upper()}]** {r.get('description', '')} — Mitigation: {r.get('mitigation', '')}")
        r_lines.append("")
        sections.append("\n".join(r_lines))
        included.append("risks")

    # --- 8. Project patterns ---
    if knowledge_entries:
        k_lines = ["## Project Patterns (follow these)"]
        for e in knowledge_entries[:5]:
            title = e.title if hasattr(e, "title") else e.get("title", "")
            content = e.content if hasattr(e, "content") else e.get("content", "")
            k_lines.append(f"- **{title}**: {content[:200]}")
        k_lines.append("")
        sections.append("\n".join(k_lines))
        included.append("patterns")

    # --- 9. Rules ---
    rules = [
        "## Rules",
        "- Follow existing code patterns in this repository",
        "- All acceptance criteria must be testable",
        "- Handle the listed edge cases",
        "- Run existing tests to ensure no regressions",
        "- Keep changes scoped to this subtask only",
    ]
    sections.append("\n".join(rules))

    # --- Assemble + token budget ---
    full_prompt = "\n\n".join(sections)
    token_estimate = len(full_prompt) // CHARS_PER_TOKEN

    # Truncate if over budget
    if token_estimate > MAX_TOKENS:
        # Remove lower-priority sections from end
        while token_estimate > MAX_TOKENS and len(sections) > 3:
            sections.pop(-2)  # Keep rules at the end
            full_prompt = "\n\n".join(sections)
            token_estimate = len(full_prompt) // CHARS_PER_TOKEN

    return {
        "subtask_id": subtask_id,
        "subtask_title": subtask.get("title", ""),
        "story_id": story_id,
        "estimated_hours": subtask.get("estimated_hours", 0),
        "prompt": full_prompt,
        "token_estimate": token_estimate,
        "sections_included": included,
    }


def build_all_task_prompts(
    spec_content: dict,
    plan_content: dict,
    tests_content: dict = None,
    scaffold_content: dict = None,
    knowledge_entries: list = None,
    repo_name: str = "",
    repo_type: str = "",
    feature_name: str = "",
) -> list:
    """Build prompts for all subtasks in a plan."""
    subtasks = plan_content.get("subtasks", [])
    prompts = []
    for st in subtasks:
        p = build_task_prompt(
            subtask=st,
            spec_content=spec_content,
            plan_content=plan_content,
            tests_content=tests_content,
            scaffold_content=scaffold_content,
            knowledge_entries=knowledge_entries,
            repo_name=repo_name,
            repo_type=repo_type,
            feature_name=feature_name,
        )
        prompts.append(p)
    return prompts
