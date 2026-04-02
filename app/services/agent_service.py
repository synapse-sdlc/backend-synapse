"""
Agent service: bridges the core orchestrator loop with the web backend.

This is the most critical new file. It ports the phase transition logic
from code-to-arc/repl.py to work with database-backed state instead of
in-memory ConversationSession.
"""

# TODO: implement the following:
#
# PHASE_SKILL_MAP = {
#     "gathering": "spec-drafting",
#     "spec_review": "spec-drafting",
#     "plan_review": "tech-planning",
#     "qa_review": "qa-testing",
# }
#
# async def run_agent_turn(feature_id, user_message, db):
#     1. Load feature from DB
#     2. Load messages from DB, convert to loop format
#     3. Select skill from PHASE_SKILL_MAP[feature.phase]
#     4. Create on_event callback that publishes to Redis
#     5. Run core.orchestrator.loop.agent_loop()
#     6. Save new messages to DB
#     7. Check for new artifacts (port _check_for_new_artifacts from repl.py)
#     8. Update feature phase if artifact detected
#     9. Publish "done" event
#
# async def handle_approve(feature_id, db):
#     1. Load feature
#     2. Update current artifact status to "approved"
#     3. Determine next phase
#     4. If not done, enqueue next agent task
#     5. If done, generate Jira preview data
