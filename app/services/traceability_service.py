"""
Cross-artifact traceability gap detection.

Analyzes spec -> plan -> tests artifact chain to find coverage gaps:
- Acceptance criteria without plan subtasks
- Acceptance criteria without test cases
- Edge cases without test coverage
"""


def detect_gaps(spec_content, plan_content, tests_content):
    """
    Cross-reference spec ACs, plan subtasks, and test cases.
    Returns gap report with coverage metrics.
    """
    if not isinstance(spec_content, dict):
        spec_content = {}
    if not isinstance(plan_content, dict):
        plan_content = {}
    if not isinstance(tests_content, dict):
        tests_content = {}

    # Extract all user story IDs and acceptance criteria from spec
    spec_stories = []
    spec_acs = []
    for story in spec_content.get("user_stories", []):
        story_id = story.get("id", "")
        if story_id:
            spec_stories.append(story_id)
        for i, ac in enumerate(story.get("acceptance_criteria", [])):
            spec_acs.append({
                "id": f"{story_id}-AC{i + 1}",
                "story_id": story_id,
                "description": f"{ac.get('given', '')} / {ac.get('when', '')} / {ac.get('then', '')}",
            })

    # Extract plan subtask story_id coverage
    plan_story_ids = set()
    for st in plan_content.get("subtasks", []):
        sid = st.get("story_id", "")
        if sid:
            plan_story_ids.add(sid)

    # Extract test case coverage by story
    test_story_ids = set()
    test_suite_types = set()
    for suite in tests_content.get("test_suites", []):
        suite_type = suite.get("type", "")
        if suite_type:
            test_suite_types.add(suite_type)
        sid = suite.get("story_id", "")
        if sid:
            test_story_ids.add(sid)

    # Detect gaps
    gaps = []
    covered = 0
    for ac in spec_acs:
        has_plan = ac["story_id"] in plan_story_ids
        has_test = ac["story_id"] in test_story_ids
        if has_plan and has_test:
            covered += 1
        elif has_plan and not has_test:
            gaps.append({
                "ac_id": ac["id"],
                "type": "no_test",
                "message": f"{ac['id']}: Has plan subtask but NO test case",
            })
        elif not has_plan and has_test:
            gaps.append({
                "ac_id": ac["id"],
                "type": "no_plan",
                "message": f"{ac['id']}: Has test case but NO plan subtask",
            })
        else:
            gaps.append({
                "ac_id": ac["id"],
                "type": "uncovered",
                "message": f"{ac['id']}: NO plan subtask AND NO test case",
            })

    # Edge case coverage
    spec_edge_cases = spec_content.get("edge_cases", [])
    has_edge_tests = "edge_case" in test_suite_types

    total = len(spec_acs) if spec_acs else 1
    coverage_percent = round((covered / total) * 100) if total > 0 else 0

    return {
        "total_acceptance_criteria": len(spec_acs),
        "covered": covered,
        "coverage_percent": coverage_percent,
        "gaps": gaps,
        "stories_in_spec": sorted(set(ac["story_id"] for ac in spec_acs)),
        "stories_in_plan": sorted(plan_story_ids),
        "stories_in_tests": sorted(test_story_ids),
        "edge_cases_in_spec": len(spec_edge_cases),
        "edge_case_tests_exist": has_edge_tests,
        "test_suite_types": sorted(test_suite_types),
        "summary": "Full coverage" if not gaps else f"{len(gaps)} gap(s) detected",
    }
