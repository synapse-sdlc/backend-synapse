import hashlib

_chunk_counter = 0

MAX_CHUNK_CHARS = 2000


def _read_lines(file_path: str, line_start: int, line_end: int):
    """Read specific lines from a source file. Returns None on failure."""
    try:
        with open(file_path, "r", errors="replace") as f:
            lines = f.readlines()
        # line_start/line_end are 1-based
        selected = lines[line_start - 1 : line_end]
        return "".join(selected)
    except (OSError, IndexError):
        return None


def _read_imports(file_path: str, import_strings: list[str]) -> str:
    """Return the raw import lines joined, falling back to the parsed list."""
    # import_strings already come from tree-sitter as the actual source text
    if import_strings:
        return "\n".join(import_strings)
    return ""


def _truncate(text: str, max_chars: int = MAX_CHUNK_CHARS) -> str:
    """Truncate text to max_chars, appending a note if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n\n# ... truncated (too long for chunk)"


def chunk_analysis_results(analysis: dict) -> list[dict]:
    global _chunk_counter
    _chunk_counter = 0
    """Split analysis results into embeddable chunks for vector storage.

    Each function/class becomes its own chunk containing the actual source code.
    File-level summaries include the real import statements.
    """
    chunks = []

    for file_result in analysis.get("results", []):
        if "error" in file_result:
            continue

        file_path = file_result["file"]
        language = file_result["language"]

        # --- File-level summary chunk (enhanced with actual imports) ---
        summary_parts = [f"File: {file_path}", f"Language: {language}"]

        import_code = _read_imports(file_path, file_result.get("imports", []))
        if import_code:
            summary_parts.append(f"\n{import_code}")

        if file_result["functions"]:
            func_names = [f["name"] for f in file_result["functions"]]
            summary_parts.append(f"\nFunctions: {', '.join(func_names)}")
        if file_result["classes"]:
            class_names = [c["name"] for c in file_result["classes"]]
            summary_parts.append(f"Classes: {', '.join(class_names)}")

        summary_content = _truncate("\n".join(summary_parts))
        chunks.append({
            "id": _make_id(file_path, "summary"),
            "content": summary_content,
            "metadata": {
                "file": file_path,
                "language": language,
                "chunk_type": "file_summary",
            },
        })

        # --- Function-level chunks with actual source code ---
        for func in file_result["functions"]:
            source = _read_lines(file_path, func["line_start"], func["line_end"])
            if source is not None:
                content = (
                    f"# Function '{func['name']}' in {file_path} "
                    f"(lines {func['line_start']}-{func['line_end']})\n\n"
                    f"{source}"
                )
            else:
                # Fallback: metadata only (file unreadable)
                content = (
                    f"Function '{func['name']}' in {file_path} "
                    f"(lines {func['line_start']}-{func['line_end']}), "
                    f"language: {language}"
                )
            chunks.append({
                "id": _make_id(file_path, f"func_{func['name']}"),
                "content": _truncate(content),
                "metadata": {
                    "file": file_path,
                    "language": language,
                    "chunk_type": "function",
                    "name": func["name"],
                    "line_start": func["line_start"],
                    "line_end": func["line_end"],
                },
            })

        # --- Class-level chunks with actual source code ---
        for cls in file_result["classes"]:
            source = _read_lines(file_path, cls["line_start"], cls["line_end"])
            if source is not None:
                content = (
                    f"# Class '{cls['name']}' in {file_path} "
                    f"(lines {cls['line_start']}-{cls['line_end']})\n\n"
                    f"{source}"
                )
            else:
                # Fallback: metadata only (file unreadable)
                content = (
                    f"Class '{cls['name']}' in {file_path} "
                    f"(lines {cls['line_start']}-{cls['line_end']}), "
                    f"language: {language}"
                )
            chunks.append({
                "id": _make_id(file_path, f"class_{cls['name']}"),
                "content": _truncate(content),
                "metadata": {
                    "file": file_path,
                    "language": language,
                    "chunk_type": "class",
                    "name": cls["name"],
                    "line_start": cls["line_start"],
                    "line_end": cls["line_end"],
                },
            })

    return chunks


def _make_id(file_path: str, label: str) -> str:
    global _chunk_counter
    _chunk_counter += 1
    return hashlib.sha256(f"{file_path}:{label}:{_chunk_counter}".encode()).hexdigest()[:16]
