from __future__ import annotations

import re
import fnmatch
from pathlib import Path

# Same ignore set as indexer/static_analyzer.py
IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    "coverage", ".coverage", "htmlcov", "egg-info",
}

MAX_OUTPUT_CHARS = 10000

# Extensions that are almost certainly binary
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".DS_Store",
}


def _is_ignored(path: Path) -> bool:
    """Check if any component of the path is in the ignore set."""
    for part in path.parts:
        if part in IGNORE_DIRS:
            return True
    return False


def _is_binary(path: Path) -> bool:
    """Quick check if a file is likely binary."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    # Sniff first 1024 bytes for null bytes
    try:
        chunk = path.read_bytes()[:1024]
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


def _collect_files(root: Path, file_glob: str | None) -> list[Path]:
    """Walk directory tree, respecting ignore rules and optional glob filter."""
    files = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if _is_ignored(item):
            continue
        if file_glob and not fnmatch.fnmatch(item.name, file_glob):
            continue
        if _is_binary(item):
            continue
        files.append(item)
    return files


class GrepCodebaseTool:
    name = "grep_codebase"
    definition = {
        "name": "grep_codebase",
        "description": (
            "Search file contents by regex pattern (grep). "
            "Use this to find exact string matches, function calls, class names, "
            "imports, route definitions, model fields, config keys, etc. "
            "Unlike search_codebase (semantic vector search), this performs "
            "literal/regex matching against actual file text."
        ),
        "input_schema": {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for (Python re syntax)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current working directory)",
                },
                "file_glob": {
                    "type": "string",
                    "description": 'Glob to filter filenames, e.g. "*.py", "*.ts"',
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching lines to return (default 20)",
                    "default": 20,
                },
            },
        },
    }

    async def execute(self, arguments: dict) -> dict:
        pattern_str = arguments["pattern"]
        search_path = Path(arguments.get("path", ".")).resolve()
        file_glob = arguments.get("file_glob")
        max_results = arguments.get("max_results", 20)

        if not search_path.exists():
            return {"error": f"Path does not exist: {search_path}"}
        if not search_path.is_dir():
            return {"error": f"Path is not a directory: {search_path}"}

        try:
            regex = re.compile(pattern_str, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex pattern: {e}"}

        files = _collect_files(search_path, file_glob)
        matches = []
        total_chars = 0

        for fpath in sorted(files):
            if len(matches) >= max_results:
                break
            try:
                lines = fpath.read_text(errors="replace").splitlines()
            except (OSError, PermissionError):
                continue

            for i, line in enumerate(lines):
                if len(matches) >= max_results:
                    break
                if regex.search(line):
                    # Gather 1 line of context before and after
                    context_before = lines[i - 1] if i > 0 else None
                    context_after = lines[i + 1] if i < len(lines) - 1 else None

                    entry = {
                        "file": str(fpath),
                        "line": i + 1,
                        "match": line.rstrip(),
                    }
                    if context_before is not None:
                        entry["context_before"] = context_before.rstrip()
                    if context_after is not None:
                        entry["context_after"] = context_after.rstrip()

                    # Estimate chars for this entry
                    entry_chars = sum(len(str(v)) for v in entry.values())
                    if total_chars + entry_chars > MAX_OUTPUT_CHARS:
                        matches.append({"note": "Output truncated to protect context window"})
                        break
                    total_chars += entry_chars
                    matches.append(entry)

            # Also break outer loop if we hit the char limit
            if matches and isinstance(matches[-1], dict) and matches[-1].get("note"):
                break

        return {
            "pattern": pattern_str,
            "files_searched": len(files),
            "total_matches": len(matches),
            "matches": matches,
        }


grep_codebase_tool = GrepCodebaseTool()
