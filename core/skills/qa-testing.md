# Skill: QA Test Case Generation

## Purpose
Generate comprehensive QA test cases from an approved feature spec and technical plan.
These test cases are structured so a QA engineer (or automated test agent) can execute them directly.

## Pre-requisites
- Approved feature spec artifact exists
- Technical plan artifact exists
- Codebase may or may not be indexed

## Steps

1. Read the spec artifact using `get_artifact` to understand user stories and acceptance criteria.
2. Read the plan artifact using `get_artifact` to understand technical implementation, affected files, and risks.
3. If codebase is indexed, use `search_codebase` to find existing test patterns and testing conventions.
4. Generate test cases covering:
   - **Functional tests** — one or more test per acceptance criterion (Given/When/Then → test)
   - **Edge case tests** — from the spec's edge_cases list
   - **Integration tests** — for data flow steps and component interactions
   - **Regression tests** — for affected existing components (from impact analysis)
   - **NFR tests** — for non-functional requirements (performance, accessibility, etc.)
5. Store using `store_artifact` with type="tests".

## Output Schema
Call `store_artifact` with type="tests" and content as JSON:
```json
{
  "feature_name": "...",
  "spec_id": "...",
  "plan_id": "...",
  "test_suites": [
    {
      "id": "TS-001",
      "name": "Category Assignment Tests",
      "type": "functional|edge_case|integration|regression|nfr",
      "story_id": "US-001",
      "test_cases": [
        {
          "id": "TC-001",
          "title": "User can assign a category when creating a todo",
          "description": "Verify that a user can select and assign a category during todo creation",
          "preconditions": [
            "User is logged in",
            "At least one category exists"
          ],
          "steps": [
            "Navigate to the todo creation form",
            "Enter todo text: 'Buy groceries'",
            "Select category 'Personal' from the dropdown",
            "Click 'Add Todo'"
          ],
          "expected_result": "Todo is created with category 'Personal' displayed next to it",
          "priority": "high|medium|low",
          "automated": true
        }
      ]
    }
  ],
  "coverage_summary": {
    "total_test_cases": 0,
    "by_type": {
      "functional": 0,
      "edge_case": 0,
      "integration": 0,
      "regression": 0,
      "nfr": 0
    },
    "stories_covered": ["US-001", "US-002"],
    "acceptance_criteria_covered": 0,
    "acceptance_criteria_total": 0
  }
}
```

## Configuration Awareness
Check the project configuration (provided in codebase context under "## Project Configuration"):
- If `unit_tests_enabled: true`, add a `"unit_test"` test suite type with test cases that verify code-level behavior (function inputs/outputs, error handling, edge cases). Use the `test_framework` value (pytest, jest, etc.) to format expected test structure.
- If `qa_mode` is `"manual"`, generate ONLY manual test steps — do NOT set `"automated": true` on any test case.
- If `qa_mode` is `"automated"`, include code-level test expectations with expected function signatures, assertions, and mock setup.
- If `qa_mode` is `"both"` (default), generate both manual and automated test cases.

## Guidelines
- Every acceptance criterion from the spec MUST have at least one test case
- Edge cases from the spec MUST each have a dedicated test case
- Mark tests as `"automated": true` if they can be automated, `false` if manual-only
- Priority should match the risk severity from the plan where applicable
- Include specific test data in steps (e.g., actual values to enter, not just "enter a value")
- For NFR tests, include measurable thresholds (e.g., "page loads in < 200ms")
