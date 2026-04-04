import json
import time
import logging
from core.orchestrator.providers.base import LLMProvider
from core.tools.registry import ToolRegistry
from core.orchestrator.skill_loader import load_skill
from core.orchestrator.tracing import start_trace

logger = logging.getLogger("synapse.orchestrator")

# Approximate chars per token (conservative estimate)
CHARS_PER_TOKEN = 4
# Target context budget for messages (leave room for system prompt + response)
MAX_CONTEXT_CHARS = 180_000  # ~45k tokens

NUDGE_SENTINEL = "__SYNAPSE_NUDGE_PRODUCE_OUTPUT__"


async def agent_loop(
    provider: LLMProvider,
    user_message: str,
    skill_name: str,
    codebase_context: str = "",
    max_turns: int = 30,
    conversation_history: list = None,
    stop_on_text: bool = False,
    on_event: callable = None,
    custom_skills: dict = None,
    # Observability — all optional, tracing is a no-op when not set
    trace_session_id: str = None,   # e.g. feature_id — groups turns in Langfuse
    trace_user_id: str = None,       # e.g. org_id or user UUID
    trace_metadata: dict = None,     # arbitrary key/value added to trace
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
    skill_content = load_skill(skill_name, project_custom_skills=custom_skills)
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

    # --- Observability: create a top-level Langfuse trace for this loop ---
    _meta = {"skill": skill_name, **(trace_metadata or {})}
    lf_trace = start_trace(
        name=f"agent-loop:{skill_name}",
        session_id=trace_session_id,
        user_id=trace_user_id,
        input=user_message,
        metadata=_meta,
        tags=[skill_name],
    )

    loop_start = time.monotonic()
    try:
        for turn in range(max_turns):
            # Context management: skip on early turns (not enough messages to overflow)
            if len(messages) > 12:
                _compress_context(
                    messages, system_prompt_len=len(system_prompt))

            # Emit thinking event before LLM call — context-aware message
            if on_event:
                has_stored = any(m.get(
                    "tool_name") == "store_artifact" for m in messages if m.get("role") == "tool")
                if turn > 4 and has_stored:
                    on_event(
                        {"type": "thinking", "message": f"Turn {turn + 1}: Reviewing stored artifact...", "turn": turn + 1})
                elif turn > 3:
                    on_event(
                        {"type": "thinking", "message": f"Turn {turn + 1}: Generating output (may take a few minutes for large artifacts)...", "turn": turn + 1})
                else:
                    on_event(
                        {"type": "thinking", "message": f"Turn {turn + 1}/{max_turns}: Reasoning about next action...", "turn": turn + 1})

            # Langfuse: open a generation span for this LLM call
            model_name = getattr(provider, "model", "unknown")
            lf_gen = lf_trace.start_generation(
                f"llm-turn-{turn + 1}",
                model=model_name,
                input=[{"role": "system", "content": system_prompt}] + messages,
                metadata={"turn": turn + 1, "max_turns": max_turns},
            )

            try:
                # Call LLM
                response = await provider.chat(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tool_definitions,
                    max_tokens=16384,
                )

                # Langfuse: close generation with output + token usage
                lf_gen.end(
                    output={
                        "content": (response.get("content") or "")[:2000],
                        "tool_calls": [{"name": tc["name"], "arguments": tc.get("arguments", {})} for tc in response.get("tool_calls", [])],
                        "stop_reason": response.get("stop_reason"),
                    },
                    usage=response.get("usage"),  # {"input": N, "output": N}
                )
            except Exception:
                lf_gen.end(output={"error": "LLM call failed"})
                raise
            finally:
                # Flush immediately so this turn appears in the Langfuse dashboard in real-time
                from core.orchestrator.tracing import flush as _lf_flush
                _lf_flush()

            # Debug: show what the model is doing each turn
            tool_names = [tc["name"] for tc in response["tool_calls"]]
            if tool_names:
                logger.info(
                    f"Turn {turn + 1}: calling {', '.join(tool_names)}")
                if on_event:
                    on_event(
                        {"type": "tool_call", "tools": tool_names, "turn": turn + 1})
            else:
                preview = (response["content"] or "")[:100]
                logger.info(
                    f"Turn {turn + 1}: finished — {preview}{'...' if len(response['content'] or '') > 100 else ''}")

            # Append assistant response
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": response["tool_calls"],
            })

            # If there are tool calls, ALWAYS execute them first — even if stop_reason
            # is "end_turn". Bedrock sometimes returns both text AND tool_calls together.
            if response["tool_calls"]:
                # Execute tools below (fall through to tool execution block)
                pass
            elif response["stop_reason"] != "tool_use":
                content = (response["content"] or "").strip()

                # In conversational mode, return immediately so the user can respond
                if stop_on_text and content:
                    lf_trace.end(output={
                                 "final_response": content[:500], "turns": turn + 1, "mode": "conversational"})
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
                    m["role"] == "user" and NUDGE_SENTINEL in m.get(
                        "content", "")
                    for m in messages
                )
                if not has_stored and len(content) < 50 and turn < max_turns - 1 and not already_nudged:
                    messages.append({
                        "role": "user",
                        "content": f"{NUDGE_SENTINEL} You haven't produced your final output yet. "
                                   "Please synthesize all the information you've gathered "
                                   "and call store_artifact with the complete structured result now.",
                    })
                    logger.info(
                        f"Turn {turn + 1}: nudging model to produce output")
                    continue

                # Extract artifact_id from store_artifact tool results (if agent already stored one)
                artifact_id = None
                if has_stored:
                    for m in reversed(messages):
                        if m.get("tool_name") == "store_artifact" and m["role"] == "tool":
                            try:
                                tool_result = json.loads(m["content"])
                                artifact_id = tool_result.get("artifact_id")
                                if artifact_id:
                                    break
                            except (json.JSONDecodeError, TypeError):
                                pass

                # Auto-save if the model forgot to call store_artifact
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
                    logger.info(f"Auto-saved artifact: {artifact_id}")

                result = {
                    "final_response": final_content or response["content"],
                    "turns": turn + 1,
                    "messages": messages,
                    "artifact_id": artifact_id,
                }
                elapsed = time.monotonic() - loop_start
                logger.info(
                    f"Agent loop completed in {elapsed:.1f}s ({turn + 1} turns, artifact={artifact_id})")
                # Langfuse: finalise trace with output summary
                lf_trace.end(
                    output={
                        "artifact_id": artifact_id,
                        "turns": turn + 1,
                        "elapsed_seconds": round(elapsed, 2),
                        "final_response": (final_content or "")[:500],
                    },
                )
                if on_event:
                    on_event({"type": "done", "turns": turn +
                             1, "artifact_id": artifact_id})
                return result

            # Execute all tool calls in parallel (tools are async and independent)
            import asyncio as _asyncio

            async def _exec_tool(tc):
                try:
                    return await _asyncio.wait_for(
                        registry.execute(tc["name"], tc["arguments"]),
                        timeout=30.0,
                    )
                except _asyncio.TimeoutError:
                    logger.warning(f"Tool {tc['name']} timed out after 30s")
                    return {"error": f"Tool {tc['name']} timed out after 30s"}
                except Exception as e:
                    # Retry once on transient errors
                    err_msg = str(e).lower()
                    if any(k in err_msg for k in ("timeout", "connection", "429", "503")):
                        logger.warning(
                            f"Tool {tc['name']} transient error, retrying in 2s: {e}")
                        import asyncio as _aio
                        await _aio.sleep(2)
                        try:
                            return await _asyncio.wait_for(
                                registry.execute(tc["name"], tc["arguments"]),
                                timeout=30.0,
                            )
                        except Exception as e2:
                            logger.warning(
                                f"Tool {tc['name']} retry failed: {e2}")
                            return {"error": str(e2)}
                    logger.warning(f"Tool error {tc['name']}: {e}")
                    return {"error": str(e)}

            import asyncio as _asyncio

            # Langfuse: wrap each tool call in its own child span for full granularity
            async def _exec_tool_traced(tc):
                t_span = lf_trace.start_span(
                    f"tool:{tc['name']}",
                    input=tc.get("arguments", {}),
                    metadata={"turn": turn + 1},
                )
                res = await _exec_tool(tc)
                t_span.end(output=res)
                return res

            results = await _asyncio.gather(*[
                _exec_tool_traced(tc) for tc in response["tool_calls"]
            ])

            # Emit per-tool activity descriptions for the frontend thought stream
            if on_event:
                for tc, res in zip(response["tool_calls"], results):
                    desc = _describe_tool_call(tc, res)
                    if desc:
                        on_event({"type": "tool_activity", "message": desc,
                                 "tool": tc["name"], "turn": turn + 1})

            validation_retry_needed = False
            for tool_call, result in zip(response["tool_calls"], results):
                if tool_call["name"] == "store_artifact" and "error" in result:
                    logger.warning(
                        f"store_artifact validation error: {result['error']}")
                    validation_retry_needed = True
                if tool_call["name"] == "store_artifact" and "artifact_id" in result and on_event:
                    on_event({"type": "artifact_stored",
                             "artifact_id": result["artifact_id"], "turn": turn + 1})
                messages.append({
                    "role": "tool",
                    "tool_name": tool_call["name"],
                    "content": json.dumps(result),
                })

            # If store_artifact returned a validation error, tell the agent to fix and retry
            if validation_retry_needed and turn < max_turns - 1:
                messages.append({
                    "role": "user",
                    "content": "Your artifact was rejected by validation. Check the error above and fix the issues, then call store_artifact again with corrected content.",
                })
                logger.info(
                    f"Turn {turn + 1}: validation retry — telling agent to fix artifact")
                continue

        lf_trace.end(
            output={"final_response": "Max turns reached", "turns": max_turns})
        return {"final_response": "Max turns reached", "turns": max_turns, "messages": messages, "artifact_id": None}
    except Exception:
        lf_trace.end(output={"error": "agent_loop exception"})
        raise
    finally:
        from core.orchestrator.tracing import flush as _lf_flush
        _lf_flush(blocking=True)


def _describe_tool_call(tool_call: dict, result: dict) -> str:
    """Generate human-readable description of what the tool did."""
    name = tool_call["name"]
    args = tool_call.get("arguments", {})

    if "error" in result:
        return f"{name} failed: {str(result['error'])[:80]}"

    if name == "read_file":
        path = args.get("path", "?")
        # Shorten path for readability
        if "/" in path:
            path = "/".join(path.split("/")[-3:])
        lines = result.get("total_lines", "?")
        return f"Reading {path} ({lines} lines)"

    elif name == "list_directory":
        path = args.get("path", "?")
        if "/" in path:
            path = "/".join(path.split("/")[-2:])
        count = len(result.get("tree", result.get("entries", [])))
        return f"Exploring {path} ({count} entries)"

    elif name == "grep_codebase":
        pattern = args.get("pattern", "?")
        matches = len(result.get("matches", []))
        return f"Searching for '{pattern}' ({matches} matches)"

    elif name == "search_codebase":
        query = args.get("query", "?")
        results_count = len(result.get("results", []))
        return f"Semantic search: '{query}' ({results_count} results)"

    elif name == "analyze_ast":
        path = args.get("file_path", "?")
        if "/" in path:
            path = path.split("/")[-1]
        funcs = len(result.get("functions", []))
        classes = len(result.get("classes", []))
        return f"Analyzing {path} ({funcs} functions, {classes} classes)"

    elif name == "store_artifact":
        art_type = args.get("type", "?")
        art_name = args.get("name", "?")
        score = result.get("confidence_score")
        desc = f"Saving {art_type}: {art_name}"
        if score is not None:
            desc += f" (confidence: {score}/100)"
        return desc

    elif name == "get_artifact":
        aid = args.get("artifact_id", "?")
        return f"Retrieving artifact {aid}"

    return f"Running {name}"


def _compress_context(messages: list[dict], system_prompt_len: int = 0):
    """Compress conversation when it exceeds the context budget.

    Strategy (multi-pass, increasingly aggressive):
    1. Compress old tool results (>200 chars, outside last 8 messages)
    2. Compress old assistant messages (long reasoning, outside last 8)
    3. Compress get_artifact results (large JSON artifacts anywhere except last 4)

    The budget accounts for system prompt size so we don't overflow the LLM context.
    """
    # Actual available budget = total - system prompt - response tokens
    available = MAX_CONTEXT_CHARS - \
        system_prompt_len - (16384 * CHARS_PER_TOKEN)
    if available < 50000:
        available = 50000  # Floor: always keep at least 50K for history

    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    if total_chars < available:
        return

    target = int(available * 0.7)
    compressed = 0
    keep_recent = 8  # Protect last 8 messages

    # Pass 1: Compress old tool results (oldest first, >200 chars)
    for i in range(max(0, len(messages) - keep_recent)):
        if total_chars - compressed < target:
            break
        m = messages[i]
        if m["role"] != "tool":
            continue
        content = m.get("content", "")
        if len(content) <= 200 or m.get("_compressed"):
            continue

        tool_name = m.get("tool_name", "unknown")
        try:
            data = json.loads(content)
            summary = _summarize_tool_result(tool_name, data)
        except (json.JSONDecodeError, TypeError):
            summary = f"[{tool_name} result: {len(content)} chars — compressed]"

        old_len = len(content)
        messages[i]["content"] = json.dumps(
            {"_compressed": True, "summary": summary})
        messages[i]["_compressed"] = True
        compressed += old_len - len(messages[i]["content"])

    # Pass 2: Compress long assistant messages (>1000 chars, outside recent)
    if total_chars - compressed > target:
        for i in range(max(0, len(messages) - keep_recent)):
            if total_chars - compressed < target:
                break
            m = messages[i]
            if m["role"] != "assistant" or m.get("_compressed"):
                continue
            content = m.get("content", "")
            if len(content) <= 1000:
                continue
            # Keep first 300 chars of agent reasoning
            old_len = len(content)
            messages[i]["content"] = content[:300] + \
                f"\n[...{len(content) - 300} chars truncated]"
            messages[i]["_compressed"] = True
            compressed += old_len - len(messages[i]["content"])

    # Pass 3: Compress get_artifact results (large artifact JSON, anywhere except last 4)
    if total_chars - compressed > target:
        for i in range(max(0, len(messages) - 4)):
            if total_chars - compressed < target:
                break
            m = messages[i]
            if m.get("tool_name") != "get_artifact" or m.get("_compressed"):
                continue
            content = m.get("content", "")
            if len(content) <= 2000:
                continue
            try:
                data = json.loads(content)
                art_type = data.get("type", "?")
                art_name = data.get("name", "?")
                art_id = data.get("id", "?")
                summary = f"[Retrieved {art_type} artifact: {art_name} (ID: {art_id}) — full content compressed, use get_artifact to re-read if needed]"
            except (json.JSONDecodeError, TypeError):
                summary = f"[Artifact content: {len(content)} chars — compressed]"

            old_len = len(content)
            messages[i]["content"] = summary
            messages[i]["_compressed"] = True
            compressed += old_len - len(messages[i]["content"])

    if compressed > 0:
        logger.info(
            f"Context compressed: {compressed:,} chars (budget: {available:,}, history: {total_chars - compressed:,})")


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

    if tool_name == "get_artifact":
        art_id = data.get("id", "?")
        art_type = data.get("type", "?")
        art_name = data.get("name", "?")
        return f"Retrieved {art_type} artifact: {art_name} (ID: {art_id})"

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
