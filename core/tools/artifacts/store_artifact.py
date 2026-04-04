import json
import hashlib
import re
from pathlib import Path
from datetime import datetime

ARTIFACT_DIR = Path("./artifacts")

TYPE_LABELS = {
    "architecture": "Architecture Overview",
    "spec": "Feature Specification",
    "plan": "Technical Plan",
    "tests": "Test Cases",
    "scaffold": "Code Scaffold",
    "kb": "Knowledge Base",
}


class StoreArtifactTool:
    name = "store_artifact"
    definition = {
        "name": "store_artifact",
        "description": "Store a versioned artifact (spec, plan, architecture, test cases). Every output MUST be stored as an artifact.",
        "input_schema": {
            "type": "object",
            "required": ["type", "name", "content"],
            "properties": {
                "type": {"type": "string", "enum": ["spec", "plan", "architecture", "tests", "scaffold", "kb"], "description": "Artifact type"},
                "name": {"type": "string", "description": "Human-readable name (e.g., 'OAuth SSO Feature Spec')"},
                "content": {"type": "string", "description": "The full artifact content as JSON string"},
                "parent_id": {"type": "string", "description": "ID of parent artifact for traceability linking"},
                "status": {"type": "string", "enum": ["draft", "approved", "superseded"], "description": "Artifact status (default: draft)"},
                "artifact_id": {"type": "string", "description": "Existing artifact ID to update (creates new version)"}
            }
        }
    }

    async def execute(self, arguments: dict) -> dict:
        ARTIFACT_DIR.mkdir(exist_ok=True)

        art_type = arguments.get("type", "kb")
        art_name = arguments.get("name", "Untitled")
        art_content = arguments.get("content", "")
        art_status = arguments.get("status", "draft")
        existing_id = arguments.get("artifact_id")
        import logging
        logging.getLogger("synapse.tools").info(f"store_artifact type={art_type}, name={art_name}, content_len={len(art_content)}, status={art_status}")

        if not art_content or len(art_content.strip()) < 10:
            return {
                "error": "Content is empty or too short. You must provide the full artifact content as a JSON string in the 'content' parameter. If the content is large, produce a more concise version but never send empty content."
            }

        # Validate content against schema and compute confidence score
        confidence_score = None
        try:
            from core.schemas.artifact_schemas import validate_artifact
            parsed_for_validation = json.loads(art_content) if isinstance(art_content, str) else art_content
            validation = validate_artifact(art_type, parsed_for_validation)
            confidence_score = validation.get("confidence_score")
            if validation["errors"]:
                return {
                    "error": f"Artifact validation failed: {validation['errors'][0]}",
                    "hint": "Check the Output Schema in your skill instructions and fix the missing/invalid fields, then call store_artifact again."
                }
        except json.JSONDecodeError:
            pass  # Content is not JSON — skip validation (e.g. raw markdown)

        # If updating an existing artifact, load it and increment version
        version = 1
        if existing_id:
            existing_path = ARTIFACT_DIR / f"{existing_id}.json"
            if existing_path.exists():
                existing = json.loads(existing_path.read_text())
                version = existing.get("version", 1) + 1
                # Mark old version as superseded
                existing["status"] = "superseded"
                existing_path.write_text(json.dumps(existing, indent=2))

        artifact_id = existing_id or hashlib.sha256(
            f"{art_type}:{art_name}:{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]

        artifact = {
            "id": artifact_id,
            "type": art_type,
            "name": art_name,
            "content": art_content,
            "parent_id": arguments.get("parent_id"),
            "status": art_status,
            "created_at": datetime.now().isoformat(),
            "version": version,
            "confidence_score": confidence_score,
        }

        # Save JSON (always succeeds)
        json_path = ARTIFACT_DIR / f"{artifact_id}.json"
        json_path.write_text(json.dumps(artifact, indent=2))

        # Save readable Markdown (best-effort — never block the JSON save)
        md_path = ARTIFACT_DIR / f"{artifact_id}.md"
        try:
            md_path.write_text(_to_markdown(artifact))
        except Exception as e:
            # Fallback: dump raw content as markdown
            md_path.write_text(
                f"# {art_name}\n\n> Markdown rendering failed: {e}\n\n"
                f"---\n\n```json\n{art_content}\n```\n"
            )

        # Sync to S3 (fire-and-forget in background thread — never block)
        s3_synced = "pending"
        try:
            import threading
            threading.Thread(target=_upload_to_s3, args=(artifact_id, json_path), daemon=True).start()
        except Exception as e:
            s3_synced = False
            logging.getLogger("synapse.tools").warning(f"S3 upload schedule failed (non-fatal): {e}")

        return {
            "artifact_id": artifact_id,
            "path": str(json_path),
            "markdown_path": str(md_path),
            "version": version,
            "status": art_status,
            "confidence_score": confidence_score,
            "s3_synced": s3_synced,
            "message": f"Artifact stored: {art_name} (v{version}, {art_status})" + (f" [confidence: {confidence_score}/100]" if confidence_score is not None else ""),
        }


def _upload_to_s3(artifact_id: str, local_path: Path):
    """Upload artifact JSON to S3 for durability. Fails silently if S3 not configured."""
    import os, boto3
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return  # S3 not configured — skip
    prefix = os.environ.get("S3_ARTIFACTS_PREFIX", "artifacts")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    try:
        s3 = boto3.client("s3", region_name=region)
        s3.upload_file(str(local_path), bucket, f"{prefix}/{artifact_id}.json")
        logging.getLogger("synapse.tools").info(f"Artifact {artifact_id} synced to s3://{bucket}/{prefix}/{artifact_id}.json")
    except Exception as e:
        logging.getLogger("synapse.tools").warning(f"S3 upload failed for {artifact_id}: {e}")


def _to_markdown(artifact: dict) -> str:
    art_type = artifact["type"]
    title = artifact["name"]
    type_label = TYPE_LABELS.get(art_type, art_type.title())
    created = artifact["created_at"]
    parent_id = artifact.get("parent_id")
    content = artifact["content"]

    status = artifact.get("status", "draft")
    version = artifact.get("version", 1)

    lines = [
        f"# {title}",
        "",
        f"> **Type:** {type_label}  ",
        f"> **ID:** `{artifact['id']}`  ",
        f"> **Version:** {version}  ",
        f"> **Status:** {status}  ",
        f"> **Created:** {created}  ",
    ]
    if parent_id:
        lines.append(f"> **Parent:** `{parent_id}`  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    try:
        data = json.loads(content)
        if art_type == "architecture":
            lines.append(_render_architecture(data))
        elif art_type == "spec":
            lines.append(_render_spec(data))
        elif art_type == "plan":
            lines.append(_render_plan(data))
        else:
            lines.append(_render_generic(data))
    except (json.JSONDecodeError, TypeError):
        lines.append(content)

    lines.append("")
    lines.append("---")
    lines.append("*Generated by Synapse Orchestrator*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mermaid diagram helpers
# ---------------------------------------------------------------------------

_SANITIZE_RE = re.compile(r"[\s/.()\-]+")


def _sanitize_node_id(name: str) -> str:
    """Turn a human-readable name into a safe Mermaid node ID."""
    sanitized = _SANITIZE_RE.sub("_", name).strip("_")
    return sanitized or "node"


def _generate_mermaid_layers(data: dict) -> str:
    """Return a ```mermaid``` code block showing architecture layers as a top-down flowchart.

    Each layer becomes a subgraph containing its components.  Layers are
    connected vertically in order.  Returns an empty string when there are
    no layers to render.
    """
    layers = data.get("layers")
    if not layers or not isinstance(layers, list):
        return ""

    lines = ["```mermaid", "graph TD"]
    node_count = 0
    max_nodes = 30
    layer_ids = []

    for layer in layers:
        if node_count >= max_nodes:
            break

        layer_label = layer.get("label", layer.get("id", "Layer"))
        layer_id = _sanitize_node_id(layer_label)
        # Ensure unique subgraph ids by appending index
        sg_id = f"sg_{layer_id}_{len(layer_ids)}"
        layer_ids.append(sg_id)

        lines.append(f'    subgraph {sg_id}["{layer_label}"]')

        components = layer.get("components", [])
        first_in_layer = True
        for comp in components:
            if node_count >= max_nodes:
                break
            if isinstance(comp, dict):
                comp_name = (
                    comp.get("id")
                    or comp.get("name")
                    or comp.get("file")
                    or comp.get("path")
                    or "component"
                )
            else:
                comp_name = str(comp)

            comp_id = _sanitize_node_id(comp_name) + f"_{node_count}"
            lines.append(f'        {comp_id}["{comp_name}"]')
            node_count += 1

        lines.append("    end")

    # Connect layers vertically in order
    for i in range(len(layer_ids) - 1):
        lines.append(f"    {layer_ids[i]} --> {layer_ids[i + 1]}")

    lines.append("```")
    return "\n".join(lines)


def _generate_mermaid_connections(data: dict) -> str:
    """Return a ```mermaid``` code block showing component connections as a left-right flowchart.

    Each connection becomes a labeled edge.  Returns an empty string when
    there are no connections to render.
    """
    connections = data.get("connections")
    if not connections or not isinstance(connections, list):
        return ""

    lines = ["```mermaid", "graph LR"]
    seen_nodes: dict[str, str] = {}  # original name -> sanitized id
    node_count = 0
    max_nodes = 30
    edges = []

    for conn in connections:
        frm = conn.get("from", "")
        to = conn.get("to", "")
        if not frm or not to:
            continue

        # Register nodes (respect limit)
        for name in (frm, to):
            if name not in seen_nodes:
                if node_count >= max_nodes:
                    break
                nid = _sanitize_node_id(name) + f"_{node_count}"
                seen_nodes[name] = nid
                lines.append(f'    {nid}["{name}"]')
                node_count += 1

        # Only add edge if both nodes were registered
        if frm in seen_nodes and to in seen_nodes:
            proto = conn.get("protocol", "")
            if proto:
                edges.append(f"    {seen_nodes[frm]} -->|{proto}| {seen_nodes[to]}")
            else:
                edges.append(f"    {seen_nodes[frm]} --> {seen_nodes[to]}")

    lines.extend(edges)
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Architecture renderer — handles rich nested JSON from Claude/Qwen
# ---------------------------------------------------------------------------

def _render_architecture(data: dict) -> str:
    lines = []

    # Project header
    name = data.get("name", "")
    if name:
        lines.append(f"## Project: {name}")
        lines.append("")

    # Summary table
    meta_rows = []
    for key in ("language", "framework", "entry_point", "description"):
        val = data.get(key)
        if val:
            meta_rows.append((key.replace("_", " ").title(), val))
    if meta_rows:
        lines.append("| Property | Value |")
        lines.append("|----------|-------|")
        for k, v in meta_rows:
            lines.append(f"| **{k}** | {v} |")
        lines.append("")

    # Mermaid architecture layer diagram
    mermaid_layers = _generate_mermaid_layers(data)
    if mermaid_layers:
        lines.append("## Architecture Diagram")
        lines.append("")
        lines.append(mermaid_layers)
        lines.append("")

    # Mermaid connection / data flow diagram
    mermaid_conns = _generate_mermaid_connections(data)
    if mermaid_conns:
        lines.append("## Connection / Data Flow Diagram")
        lines.append("")
        lines.append(mermaid_conns)
        lines.append("")

    # Dependencies
    deps = data.get("dependencies")
    if deps and isinstance(deps, dict):
        lines.append("## Dependencies")
        lines.append("")
        for pkg, desc in deps.items():
            lines.append(f"- **`{pkg}`** — {desc}")
        lines.append("")

    # File map
    file_map = data.get("file_map")
    if file_map and isinstance(file_map, dict):
        lines.append("## File Map")
        lines.append("")
        lines.append("```")
        for fpath, desc in file_map.items():
            lines.append(f"{fpath:40s}  # {desc}")
        lines.append("```")
        lines.append("")

    # Layers
    if data.get("layers"):
        lines.append("## Architecture Layers")
        lines.append("")
        for layer in data["layers"]:
            label = layer.get("label", layer.get("id", "Layer"))
            lines.append(f"### {label}")
            lines.append("")
            for comp in layer.get("components", []):
                if isinstance(comp, dict):
                    _render_component(comp, lines)
                else:
                    lines.append(f"- `{comp}`")
            lines.append("")

    # Connections
    if data.get("connections"):
        lines.append("## Connections")
        lines.append("")
        for conn in data["connections"]:
            frm = conn.get("from", "?")
            to = conn.get("to", "?")
            proto = conn.get("protocol", "")
            lines.append(f"- **`{frm}`** → **`{to}`**")
            if proto:
                lines.append(f"  - {proto}")
        lines.append("")

    # Data models
    if data.get("data_models"):
        lines.append("## Data Models")
        lines.append("")
        for model in data["data_models"]:
            if isinstance(model, dict):
                mname = model.get("name", "Unknown")
                mfile = model.get("file", "")
                mtype = model.get("type", "")
                header = f"### `{mname}`"
                if mfile:
                    header += f" (`{mfile}`)"
                lines.append(header)
                if mtype:
                    lines.append(f"*{mtype}*")
                lines.append("")
                # Fields
                fields = model.get("fields", [])
                if fields:
                    lines.append("| Field | Type | Required |")
                    lines.append("|-------|------|----------|")
                    for f in fields:
                        if isinstance(f, dict):
                            lines.append(f"| `{f.get('name', '')}` | `{f.get('type', '')}` | {f.get('required', '')} |")
                        else:
                            lines.append(f"| {f} | | |")
                    lines.append("")
                note = model.get("note")
                if note:
                    lines.append(f"> {note}")
                    lines.append("")
            else:
                lines.append(f"- `{model}`")
        lines.append("")

    # API routes / interactions
    if data.get("api_routes"):
        lines.append("## API / Interactions")
        lines.append("")
        for route in data["api_routes"]:
            if isinstance(route, dict):
                note = route.get("note")
                if note:
                    lines.append(f"*{note}*")
                    lines.append("")
                interactions = route.get("interactions", [])
                for inter in interactions:
                    lines.append(f"- {inter}")
                method = route.get("method")
                path = route.get("path")
                if method and path:
                    lines.append(f"- `{method} {path}`")
            else:
                lines.append(f"- {route}")
        lines.append("")

    # Execution flow
    flow = data.get("execution_flow")
    if flow:
        lines.append("## Execution Flow")
        lines.append("")
        if isinstance(flow, dict):
            desc = flow.get("description")
            if desc:
                lines.append(f"*{desc}*")
                lines.append("")
            for step in flow.get("steps", []):
                lines.append(f"{step}")
        elif isinstance(flow, list):
            for step in flow:
                lines.append(f"- {step}")
        lines.append("")

    # External services
    if data.get("external_services"):
        lines.append("## External Services")
        lines.append("")
        for svc in data["external_services"]:
            if isinstance(svc, dict):
                sname = svc.get("name", "Unknown")
                lines.append(f"### {sname}")
                lines.append("")
                for k, v in svc.items():
                    if k == "name":
                        continue
                    if isinstance(v, list):
                        lines.append(f"- **{_humanize(k)}:** {', '.join(str(x) for x in v)}")
                    else:
                        lines.append(f"- **{_humanize(k)}:** {v}")
                lines.append("")
            else:
                lines.append(f"- {svc}")
        lines.append("")

    # Design patterns
    patterns = data.get("design_patterns")
    if patterns:
        lines.append("## Design Patterns")
        lines.append("")
        for p in patterns:
            if isinstance(p, dict):
                lines.append(f"### {p.get('pattern', 'Pattern')}")
                lines.append("")
                lines.append(p.get("description", ""))
                lines.append("")
            else:
                lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


def _render_component(comp: dict, lines: list):
    """Render a single component within a layer."""
    # Determine the display name
    name = comp.get("id") or comp.get("file") or comp.get("path") or comp.get("name", "")
    desc = comp.get("description") or comp.get("role", "")

    if name:
        lines.append(f"**`{name}`**")
    if desc:
        lines.append(f": {desc}")
    lines.append("")

    # Key functions
    for func in comp.get("key_functions", []):
        lines.append(f"  - `{func}`")

    # Tools
    for tool in comp.get("tools", []):
        if isinstance(tool, dict):
            lines.append(f"  - Tool: **`{tool.get('name', '')}`** — {tool.get('description', '')}")
        else:
            lines.append(f"  - Tool: `{tool}`")

    # Models/schemas within component
    for model in comp.get("models", []):
        if isinstance(model, dict):
            mname = model.get("name", "")
            lines.append(f"  - Model: **`{mname}`**")
            fields = model.get("fields", {})
            if isinstance(fields, dict):
                for fname, ftype in fields.items():
                    lines.append(f"    - `{fname}`: `{ftype}`")
            elif isinstance(fields, list):
                for f in fields:
                    if isinstance(f, dict):
                        lines.append(f"    - `{f.get('name', '')}`: `{f.get('type', '')}`")
                    else:
                        lines.append(f"    - {f}")

    # Constants
    consts = comp.get("constants")
    if consts and isinstance(consts, dict):
        for cname, cval in consts.items():
            lines.append(f"  - `{cname}` = {cval}")

    # Structure (e.g. for data directories)
    structure = comp.get("structure")
    if structure and isinstance(structure, dict):
        for sname, sdesc in structure.items():
            lines.append(f"  - `{sname}` — {sdesc}")

    # Sample data
    samples = comp.get("sample_claims", comp.get("samples", []))
    if samples:
        lines.append(f"  - Samples: {', '.join(str(s) for s in samples)}")

    # Extra metadata
    for key in ("class", "deps_type", "output_type", "system_prompt_summary"):
        val = comp.get(key)
        if val:
            lines.append(f"  - *{_humanize(key)}:* {val}")

    lines.append("")


# ---------------------------------------------------------------------------
# Spec renderer
# ---------------------------------------------------------------------------

def _render_spec(data: dict) -> str:
    lines = []
    if data.get("feature_name"):
        lines.append(f"## Feature: {data['feature_name']}")
        lines.append("")
    if data.get("priority"):
        lines.append(f"**Priority:** {data['priority']}")
        lines.append("")
    if data.get("business_context"):
        lines.append("## Business Context")
        lines.append("")
        lines.append(data["business_context"])
        lines.append("")

    if data.get("personas"):
        lines.append("## Personas")
        lines.append("")
        for p in data["personas"]:
            if isinstance(p, dict):
                lines.append(f"- **{p.get('name', 'User')}**: {p.get('description', '')}")
            else:
                lines.append(f"- {p}")
        lines.append("")

    if data.get("user_stories"):
        lines.append("## User Stories")
        lines.append("")
        for story in data["user_stories"]:
            sid = story.get("id", "")
            lines.append(f"### {sid}: As a {story.get('role', '...')}")
            lines.append(f"I want **{story.get('action', '...')}** so that **{story.get('benefit', '...')}**")
            lines.append("")
            if story.get("acceptance_criteria"):
                lines.append("**Acceptance Criteria:**")
                lines.append("")
                for ac in story["acceptance_criteria"]:
                    lines.append(f"- **Given** {ac.get('given', '')} **When** {ac.get('when', '')} **Then** {ac.get('then', '')}")
                lines.append("")

    if data.get("non_functional_requirements"):
        lines.append("## Non-Functional Requirements")
        lines.append("")
        for r in data["non_functional_requirements"]:
            lines.append(f"- {r}")
        lines.append("")

    if data.get("edge_cases"):
        lines.append("## Edge Cases")
        lines.append("")
        for e in data["edge_cases"]:
            lines.append(f"- {e}")
        lines.append("")

    if data.get("out_of_scope"):
        lines.append("## Out of Scope")
        lines.append("")
        for o in data["out_of_scope"]:
            lines.append(f"- {o}")
        lines.append("")

    if data.get("dependencies"):
        lines.append("## Dependencies")
        lines.append("")
        for d in data["dependencies"]:
            lines.append(f"- {d}")
        lines.append("")

    if data.get("success_metrics"):
        lines.append("## Success Metrics")
        lines.append("")
        for m in data["success_metrics"]:
            lines.append(f"- {m}")
        lines.append("")

    if data.get("impact_analysis"):
        lines.append("## Impact Analysis")
        lines.append("")
        ia = data["impact_analysis"]
        if ia.get("affected_components"):
            lines.append("**Affected Components:**")
            for c in ia["affected_components"]:
                lines.append(f"- {c}")
            lines.append("")
        if ia.get("affected_routes"):
            lines.append("**Affected Routes:**")
            for r in ia["affected_routes"]:
                lines.append(f"- {r}")
            lines.append("")
        if ia.get("risk_areas"):
            lines.append("**Risk Areas:**")
            for r in ia["risk_areas"]:
                lines.append(f"- {r}")
            lines.append("")

    if data.get("open_questions"):
        lines.append("## Open Questions")
        lines.append("")
        for q in data["open_questions"]:
            lines.append(f"- {q}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan renderer
# ---------------------------------------------------------------------------

def _render_plan(data: dict) -> str:
    lines = []
    if data.get("feature_name"):
        lines.append(f"## Feature: {data['feature_name']}")
    if data.get("spec_id"):
        lines.append(f"**Spec:** `{data['spec_id']}`")
    lines.append("")

    if data.get("affected_routes"):
        lines.append("## Affected Routes")
        lines.append("")
        for r in data["affected_routes"]:
            if isinstance(r, dict):
                lines.append(f"- `{r.get('path', '')}` in `{r.get('file', '')}` — {r.get('change', '')}")
            else:
                lines.append(f"- {r}")
        lines.append("")

    if data.get("data_flow"):
        lines.append("## Data Flow")
        lines.append("")
        for step in data["data_flow"]:
            if isinstance(step, dict):
                lines.append(f"{step.get('step', '')}. **{step.get('component', '')}** — {step.get('description', '')}")
            else:
                lines.append(f"- {step}")
        lines.append("")

    if data.get("migrations"):
        lines.append("## Migrations")
        lines.append("")
        for m in data["migrations"]:
            if isinstance(m, dict):
                lines.append(f"- `{m.get('table', '')}`: {m.get('change', '')} (`{m.get('sql_hint', '')}`)")
            else:
                lines.append(f"- {m}")
        lines.append("")

    if data.get("new_files"):
        lines.append("## New Files")
        lines.append("")
        for f in data["new_files"]:
            if isinstance(f, dict):
                lines.append(f"- `{f.get('path', '')}` — {f.get('purpose', '')}")
            else:
                lines.append(f"- {f}")
        lines.append("")

    if data.get("risks"):
        lines.append("## Risks")
        lines.append("")
        for r in data["risks"]:
            if isinstance(r, dict):
                sev = r.get("severity", "medium").upper()
                lines.append(f"- **[{sev}]** {r.get('description', '')} — *Mitigation:* {r.get('mitigation', '')}")
            else:
                lines.append(f"- {r}")
        lines.append("")

    if data.get("subtasks"):
        lines.append("## Subtasks")
        lines.append("")
        lines.append("| ID | Title | Story | Est. Hours |")
        lines.append("|---|---|---|---|")
        for t in data["subtasks"]:
            if isinstance(t, dict):
                lines.append(f"| {t.get('id', '')} | {t.get('title', '')} | {t.get('story_id', '')} | {t.get('estimated_hours', '')} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generic fallback renderer
# ---------------------------------------------------------------------------

def _render_generic(data, depth=0) -> str:
    lines = []
    if isinstance(data, dict):
        prefix = "#" * min(depth + 2, 6)
        for key, val in data.items():
            if isinstance(val, dict):
                lines.append(f"{prefix} {_humanize(key)}")
                lines.append("")
                lines.append(_render_generic(val, depth + 1))
            elif isinstance(val, list):
                lines.append(f"{prefix} {_humanize(key)}")
                lines.append("")
                for item in val:
                    if isinstance(item, dict):
                        lines.append(_render_generic(item, depth + 1))
                    else:
                        lines.append(f"- {item}")
                lines.append("")
            else:
                lines.append(f"**{_humanize(key)}:** {val}")
                lines.append("")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                lines.append(_render_generic(item, depth))
            else:
                lines.append(f"- {item}")
    else:
        lines.append(str(data))
    return "\n".join(lines)


def _humanize(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").title()


store_artifact_tool = StoreArtifactTool()
