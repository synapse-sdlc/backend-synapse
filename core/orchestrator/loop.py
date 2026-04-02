import json
from orchestrator.providers.base import LLMProvider
from tools.registry import ToolRegistry
from orchestrator.skill_loader import load_skill

# Approximate chars per token (conservative estimate)
CHARS_PER_TOKEN = 4
# Target context budget for messages (leave room for system prompt + response)
MAX_CONTEXT_CHARS = 180_000  # ~45k tokens


async def agent_loop(
    provider: LLMProvider,
    user_message: str,
    skill_name: str,
    codebase_context: str = "",
    max_turns: int = 30,
    conversation_history: list = None,
    stop_on_text: bool = False,
    on_event: callable = None,
) -> dict:
    """
    The single agentic loop. This is the entire orchestrator.

    1. Load skill into system prompt
    2. Loop: call LLM -> execute tools -> feed results -> repeat
    3. When LLM stops calling tools, return final output

    Args:
        conversation_history: When provided, append new user_message to it
            instead of creating fresh messages. Enables multi-turn conversation.
        stop_on_text: When True, return immediately when the LLM produces text
            (skip nudge/auto-save). Lets the agent ask questions and wait for input.
    """

    # Build system prompt: base + skill + codebase context
    skill_content = load_skill(skill_name)
    system_prompt = f"""You are the Synapse orchestrator. You analyze codebases,
generate specs, create technical plans, and produce test cases.

## Current Skill
{skill_content}

## Codebase Context
{codebase_context if codebase_context else "No codebase indexed yet."}

## Rules
- Use tools to gather information before generating output.
- CRITICAL: You MUST call store_artifact tool to save your final output BEFORE you finish. Never just print the result — always store it first.
- When reading large files, use start_line/end_line to read specific sections instead of the whole file.
- Use grep_codebase to find specific patterns, model definitions, route declarations, etc.
- Use search_codebase for semantic/conceptual searches.
- Be thorough: explore multiple directories, read key config files, trace imports and connections.
- Follow the skill instructions exactly.
- Output structured JSON when the skill specifies a schema.
"""

    # Initialize or continue conversation
    if conversation_history is not None:
        messages = conversation_history
        messages.append({"role": "user", "content": user_message})
    else:
        messages = [{"role": "user", "content": user_message}]

    # Get tool definitions from registry
    registry = ToolRegistry()
    tool_definitions = registry.get_definitions()

    for turn in range(max_turns):
        # Context management: compress old tool results if conversation is too large
        _compress_context(messages)

        # Call LLM
        response = await provider.chat(
            system_prompt=system_prompt,
            messages=messages,
            tools=tool_definitions,
            max_tokens=16384,
        )

        # Debug: show what the model is doing each turn
        tool_names = [tc["name"] for tc in response["tool_calls"]]
        if tool_names:
            print(f"  Turn {turn + 1}: calling {', '.join(tool_names)}")
            if on_event:
                on_event({"type": "tool_call", "tools": tool_names, "turn": turn + 1})
        else:
            preview = (response["content"] or "")[:100]
            print(f"  Turn {turn + 1}: finished — {preview}{'...' if len(response['content'] or '') > 100 else ''}")

        # Append assistant response
        messages.append({
            "role": "assistant",
            "content": response["content"],
            "tool_calls": response["tool_calls"],
        })

        # If no tool calls — check if the model actually produced output or just stalled
        if response["stop_reason"] != "tool_use":
            content = (response["content"] or "").strip()

            # In conversational mode, return immediately so the user can respond
            if stop_on_text and content:
                return {
                    "final_response": content,
                    "turns": turn + 1,
                    "messages": messages,
                    "artifact_id": None,
                }

            has_stored = any(
                m.get("tool_name") == "store_artifact"
                for m in messages if m["role"] == "tool"
            )

            # If the model returned empty/short content and hasn't stored anything,
            # nudge it ONCE to continue and produce the final artifact
            already_nudged = any(
                m["role"] == "user" and "haven't produced" in m.get("content", "")
                for m in messages
            )
            if not has_stored and len(content) < 50 and turn < max_turns - 1 and not already_nudged:
                messages.append({
                    "role": "user",
                    "content": "You haven't produced your final output yet. "
                               "Please synthesize all the information you've gathered "
                               "and call store_artifact with the complete structured result now.",
                })
                print(f"  Turn {turn + 1}: nudging model to produce output...")
                continue

            # Auto-save if the model forgot to call store_artifact
            artifact_id = None
            final_content = response["content"].strip()
            if not final_content:
                for m in reversed(messages):
                    if m["role"] == "assistant" and m.get("content", "").strip():
                        final_content = m["content"].strip()
                        break
            if not has_stored and final_content:
                save_result = await registry.execute("store_artifact", {
                    "type": _skill_to_artifact_type(skill_name),
                    "name": f"Auto-saved {skill_name} output",
                    "content": final_content,
                })
                artifact_id = save_result.get("artifact_id")
                print(f"  [auto-saved artifact: {artifact_id}]")

            result = {
                "final_response": final_content or response["content"],
                "turns": turn + 1,
                "messages": messages,
                "artifact_id": artifact_id,
            }
            if on_event:
                on_event({"type": "done", "turns": turn + 1, "artifact_id": artifact_id})
            return result

        # Execute each tool call
        for tool_call in response["tool_calls"]:
            try:
                result = await registry.execute(
                    tool_call["name"],
                    tool_call["arguments"],
                )
            except Exception as e:
                result = {"error": str(e)}
                print(f"  [tool error] {tool_call['name']}: {e}")
            if "error" in result and tool_call["name"] == "store_artifact":
                print(f"  [store_artifact error] {result['error']}")
            if tool_call["name"] == "store_artifact" and "artifact_id" in result and on_event:
                on_event({"type": "artifact_stored", "artifact_id": result["artifact_id"], "turn": turn + 1})
            messages.append({
                "role": "tool",
                "tool_name": tool_call["name"],
                "content": json.dumps(result),
            })

    return {"final_response": "Max turns reached", "turns": max_turns, "messages": messages, "artifact_id": None}


def _compress_context(messages: list[dict]):
    """Compress old tool results when conversation gets too large.

    Strategy: keep the most recent tool results full, but summarize older ones.
    This prevents context overflow while preserving the model's recent working memory.
    """
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    if total_chars < MAX_CONTEXT_CHARS:
        return

    compressed = 0
    # Work backwards — keep the last 6 messages untouched (recent context)
    # Compress tool results from oldest to newest
    for i in range(len(messages) - 6):
        m = messages[i]
        if m["role"] != "tool":
            continue
        content = m.get("content", "")
        if len(content) <= 500:
            continue

        # Summarize the tool result
        tool_name = m.get("tool_name", "unknown")
        try:
            data = json.loads(content)
            summary = _summarize_tool_result(tool_name, data)
        except (json.JSONDecodeError, TypeError):
            summary = f"[{tool_name} result: {len(content)} chars — compressed]"

        old_len = len(content)
        messages[i]["content"] = json.dumps({"_compressed": True, "summary": summary})
        compressed += old_len - len(messages[i]["content"])

        # Check if we've compressed enough
        total_chars -= compressed
        if total_chars < MAX_CONTEXT_CHARS * 0.7:
            break

    if compressed > 0:
        print(f"  [context] compressed {compressed:,} chars from old tool results")


def _summarize_tool_result(tool_name: str, data: dict) -> str:
    """Create a compact summary of a tool result."""
    if tool_name == "read_file":
        path = data.get("path", "?")
        total = data.get("total_lines", "?")
        showing = data.get("showing", "?")
        return f"Read {path} (lines {showing} of {total} total)"

    if tool_name == "list_directory":
        path = data.get("path", "?")
        tree = data.get("tree", [])
        names = [e["name"] for e in tree[:15]]
        more = f" +{len(tree) - 15} more" if len(tree) > 15 else ""
        return f"Listed {path}: {', '.join(names)}{more}"

    if tool_name == "analyze_ast":
        fpath = data.get("file", "?")
        funcs = [f["name"] for f in data.get("functions", [])[:10]]
        classes = [c["name"] for c in data.get("classes", [])[:10]]
        return f"AST of {fpath}: functions={funcs}, classes={classes}"

    if tool_name == "search_codebase":
        query = data.get("query", "?")
        results = data.get("results", [])
        files = [r.get("metadata", {}).get("file", "?") for r in results[:5]]
        return f"Search '{query}': {len(results)} results in {files}"

    if tool_name == "grep_codebase":
        pattern = data.get("pattern", "?")
        matches = data.get("matches", [])
        return f"Grep '{pattern}': {len(matches)} matches"

    if tool_name == "store_artifact":
        return f"Stored artifact: {data.get('artifact_id', '?')}"

    # Generic fallback
    return f"{tool_name} result ({len(json.dumps(data))} chars)"


SKILL_ARTIFACT_MAP = {
    "codebase-analysis": "architecture",
    "spec-drafting": "spec",
    "tech-planning": "plan",
    "qa-testing": "tests",
}


def _skill_to_artifact_type(skill_name: str) -> str:
    return SKILL_ARTIFACT_MAP.get(skill_name, "kb")
