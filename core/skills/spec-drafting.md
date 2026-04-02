# Skill: Feature Spec Drafting (Conversational)

## Purpose
Generate a structured feature spec through conversational discovery with a Product Owner.
This skill operates in three phases: gathering requirements, generating the spec, and refining it.

## Pre-requisites
- User provides a feature description in natural language
- Codebase may or may not be indexed (adapt accordingly)

## Phase 1: Requirements Gathering (MANDATORY)

Before generating ANY spec content, you MUST ask 3-5 clarifying questions.
Do NOT generate a spec until the user has answered your questions.

Focus your questions on:
1. **Target Users / Personas** — Who are the primary users? Are there admin/internal users too?
2. **Business Priority** — Is this a P0 (launch blocker), P1 (important), or P2 (nice-to-have)?
3. **Constraints** — Any technical constraints, regulatory requirements, or deadlines?
4. **Non-Functional Requirements** — Performance expectations, scalability needs, security requirements?
5. **Dependencies** — Does this depend on other features, external APIs, or third-party services?
6. **Edge Cases** — What should happen in error/edge scenarios the user cares about most?

Format your questions clearly as a numbered list. Be conversational and specific to the feature described.

Example:
```
Great feature idea! Before I draft the spec, I have a few questions:

1. **Target Users:** Who will primarily use this? End customers, internal admins, or both?
2. **Priority:** How critical is this — is it blocking a release?
3. **Security:** Are there specific auth/permission requirements?
4. **Scale:** How many concurrent users do you expect?
5. **Dependencies:** Does this integrate with any external services?
```

## Phase 2: Spec Generation

After the user answers your questions, generate a comprehensive spec.

Steps:
1. If available, read the architecture artifact using `get_artifact` to understand the codebase.
2. Call `search_codebase` for terms related to the feature to assess existing code.
3. Generate the structured spec incorporating ALL information from the conversation.
4. Store using `store_artifact` with type="spec".

### Output Schema
Call `store_artifact` with type="spec" and content as JSON:
```json
{
  "feature_name": "...",
  "business_context": "...",
  "personas": [
    { "name": "...", "description": "..." }
  ],
  "priority": "P0|P1|P2",
  "user_stories": [
    {
      "id": "US-001",
      "role": "...",
      "action": "...",
      "benefit": "...",
      "acceptance_criteria": [
        { "given": "...", "when": "...", "then": "..." }
      ]
    }
  ],
  "non_functional_requirements": [
    "Response time < 200ms for search queries",
    "..."
  ],
  "edge_cases": [
    "What happens when...",
    "..."
  ],
  "out_of_scope": [
    "Feature X is not included in this iteration",
    "..."
  ],
  "dependencies": [
    "Requires OAuth provider integration",
    "..."
  ],
  "success_metrics": [
    "80% of patients can view results within 3 clicks",
    "..."
  ],
  "impact_analysis": {
    "affected_components": ["..."],
    "affected_routes": ["..."],
    "risk_areas": ["..."]
  },
  "open_questions": ["..."]
}
```

## Phase 3: Refinement

When the user requests changes to the spec:

1. Call `get_artifact` with the current spec's artifact_id to retrieve it.
2. Parse which section(s) need modification based on the user's feedback.
3. Apply the requested changes to the spec JSON.
4. Store the updated spec using `store_artifact` with the SAME name but updated content.
5. Summarize what changed in your response.

Examples of refinement requests:
- "Add an edge case for expired lab results" → add to edge_cases array
- "Change story 2 acceptance criteria" → modify the specific story's criteria
- "Remove the admin persona" → remove from personas array
- "Make this P0 priority" → update priority field

Always confirm what you changed and show the updated section.
