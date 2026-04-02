from pathlib import Path


class ReadFileTool:
    name = "read_file"
    definition = {
        "name": "read_file",
        "description": (
            "Read the contents of a file at the given path. Use this when you need to see exact code. "
            "Optionally specify start_line and end_line (1-indexed, inclusive) to read a specific range. "
            "Output includes line numbers. If the file is large, use line ranges to read in chunks."
        ),
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
                "start_line": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed). Optional.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Line number to stop reading at (inclusive). Optional.",
                },
            },
        },
    }

    async def execute(self, arguments: dict) -> dict:
        path = Path(arguments["path"])
        if not path.exists():
            return {"error": f"File not found: {path}"}
        if not path.is_file():
            return {"error": f"Not a file: {path}"}

        raw = path.read_text(errors="replace")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")

        # Determine the slice (1-indexed, inclusive on both ends)
        start_idx = (start_line - 1) if start_line and start_line >= 1 else 0
        end_idx = end_line if end_line and end_line >= 1 else total_lines

        # Clamp to valid range
        start_idx = max(0, min(start_idx, total_lines))
        end_idx = max(start_idx, min(end_idx, total_lines))

        selected = all_lines[start_idx:end_idx]

        # Format with line numbers (like cat -n)
        numbered = []
        for i, line in enumerate(selected, start=start_idx + 1):
            numbered.append(f"{i:6d}\t{line}")
        content = "\n".join(numbered)

        # Truncate if too large (protect context window)
        truncated = False
        if len(content) > 10000:
            content = content[:10000] + "\n\n... [TRUNCATED — use start_line/end_line to read in chunks]"
            truncated = True

        return {
            "path": str(path),
            "content": content,
            "total_lines": total_lines,
            "showing": f"{start_idx + 1}-{end_idx}",
            "truncated": truncated,
        }


read_file_tool = ReadFileTool()
