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

## Example Output

Here is an example of a high-quality test artifact for reference:

```json
{
  "feature_name": "Password Reset via Email",
  "spec_id": "a1b2c3d4e5f6",
  "plan_id": "b2c3d4e5f6a1",
  "test_suites": [
    {
      "id": "TS-001",
      "name": "Password Reset Functional Tests",
      "type": "functional",
      "story_id": "US-001",
      "test_cases": [
        {
          "id": "TC-001",
          "title": "User receives reset email after submitting valid email",
          "description": "Verify the forgot-password flow sends a reset email within 60 seconds",
          "preconditions": ["User account exists with email user@example.com", "SendGrid is configured"],
          "steps": [
            "Navigate to /login",
            "Click 'Forgot Password' link",
            "Enter 'user@example.com' in the email field",
            "Click 'Send Reset Link'"
          ],
          "expected_result": "Success message displayed: 'Check your email for a reset link'. Email received within 60 seconds containing a valid reset URL.",
          "priority": "high",
          "automated": true
        },
        {
          "id": "TC-002",
          "title": "User can set new password via reset link",
          "description": "Verify clicking a valid reset link allows password change",
          "preconditions": ["User has received a valid reset email", "Reset token is not expired"],
          "steps": [
            "Click the reset link from the email",
            "Enter new password 'NewSecure@123' in both password fields",
            "Click 'Reset Password'"
          ],
          "expected_result": "Password updated successfully. User is logged in automatically and redirected to dashboard.",
          "priority": "high",
          "automated": true
        }
      ]
    },
    {
      "id": "TS-002",
      "name": "Password Reset Edge Cases",
      "type": "edge_case",
      "story_id": "US-001",
      "test_cases": [
        {
          "id": "TC-003",
          "title": "Reset request for non-existent email shows same success message",
          "description": "Verify no email enumeration is possible through the reset flow",
          "preconditions": ["No account exists with email nonexistent@example.com"],
          "steps": [
            "Navigate to forgot-password page",
            "Enter 'nonexistent@example.com'",
            "Click 'Send Reset Link'"
          ],
          "expected_result": "Same success message shown as for valid emails. No email is sent. Response time is similar to valid email request.",
          "priority": "high",
          "automated": true
        },
        {
          "id": "TC-004",
          "title": "Expired reset link shows error with retry option",
          "description": "Verify expired tokens are handled gracefully",
          "preconditions": ["User has a reset token that was generated >1 hour ago"],
          "steps": [
            "Click the expired reset link",
            "Observe the error page"
          ],
          "expected_result": "Error message: 'This reset link has expired.' with a 'Request New Link' button that navigates to forgot-password page.",
          "priority": "medium",
          "automated": true
        },
        {
          "id": "TC-005",
          "title": "Multiple reset requests invalidate previous tokens",
          "description": "Verify only the latest reset token is valid",
          "preconditions": ["User account exists with email user@example.com"],
          "steps": [
            "Request password reset (get token A)",
            "Request password reset again (get token B)",
            "Try to use token A to reset password"
          ],
          "expected_result": "Token A is rejected with 'Invalid or expired link' error. Token B works correctly.",
          "priority": "medium",
          "automated": true
        }
      ]
    },
    {
      "id": "TS-003",
      "name": "Rate Limiting Integration Tests",
      "type": "integration",
      "story_id": "US-001",
      "test_cases": [
        {
          "id": "TC-006",
          "title": "Rate limit blocks after 5 requests per email per hour",
          "description": "Verify rate limiting prevents abuse of the reset endpoint",
          "preconditions": ["Rate limiting is enabled", "No prior requests for test email"],
          "steps": [
            "Send 5 POST requests to /api/auth/forgot-password with same email",
            "Send a 6th request with the same email"
          ],
          "expected_result": "First 5 requests return 200. 6th request returns 429 Too Many Requests with 'retry-after' header.",
          "priority": "high",
          "automated": true
        }
      ]
    }
  ],
  "coverage_summary": {
    "total_test_cases": 6,
    "by_type": { "functional": 2, "edge_case": 3, "integration": 1, "regression": 0, "nfr": 0 },
    "stories_covered": ["US-001"],
    "acceptance_criteria_covered": 3,
    "acceptance_criteria_total": 4
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
