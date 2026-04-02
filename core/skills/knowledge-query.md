# Skill: Knowledge Query

## Purpose
Answer natural language questions about the project using the accumulated knowledge base,
architecture documents, and codebase. Adapt answers based on the user's persona.

## Input
You will receive:
- The user's question
- Their persona (po, qa, developer, tech_lead)
- Pre-fetched relevant knowledge entries
- Project architecture summary
- Codebase context from all repositories

## Steps
1. Analyze the question to determine what type of information is needed:
   - Architecture/code → search codebase, read relevant files
   - Decisions/history → read knowledge entries
   - "How to" → search for patterns in KB + code examples
   - Data flow → trace through architecture layers
2. Use `search_codebase` to find relevant code if the question is about implementation.
3. Use `read_file` on specific files for detailed answers.
4. Synthesize an answer from all sources.
5. Include citations pointing to specific files, artifacts, or knowledge entries.

## Persona Adaptation
- **po** (Product Owner): Focus on business context, user impact, feature scope, success metrics. Avoid code details. Use business language.
- **qa** (QA Engineer): Focus on test coverage, affected areas, regression risk, edge cases. Include which repos/components are impacted.
- **developer** / **tech_lead**: Focus on code patterns, API contracts, implementation details, architecture decisions. Include file paths and function signatures.

## Output Format
Return a clear, structured response. Do NOT store as artifact — just respond with text.

Structure your answer as:
1. **Direct answer** to the question
2. **Details** supporting the answer (persona-appropriate depth)
3. **Sources** cited: [file:path:line] or [KB: entry_title] or [Architecture: section]
4. **Related questions** the user might want to ask next

## Rules
- Always cite sources — never answer from general knowledge alone
- If you can't find the answer in the codebase/KB, say so clearly
- Keep answers concise: PO answers under 200 words, Dev answers can be longer with code
- For cross-repo questions, trace the flow through all relevant repos
