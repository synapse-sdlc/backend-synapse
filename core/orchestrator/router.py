"""Intent detection and skill selection."""

SKILL_KEYWORDS = {
    "codebase-analysis": ["analyze", "architecture", "explore", "index", "understand", "codebase"],
    "spec-drafting": ["spec", "feature", "requirement", "user story", "acceptance criteria"],
    "tech-planning": ["plan", "technical", "implementation", "subtask", "migration"],
}


def detect_skill(user_message: str) -> str:
    """Simple keyword-based intent detection. Returns the best matching skill name."""
    message_lower = user_message.lower()
    scores = {}
    for skill, keywords in SKILL_KEYWORDS.items():
        scores[skill] = sum(1 for kw in keywords if kw in message_lower)

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "codebase-analysis"  # default
    return best
