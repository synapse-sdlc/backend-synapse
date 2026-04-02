import fnmatch
import tree_sitter_languages
from pathlib import Path

LANG_MAP = {
    ".py": "python", ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java",
}

# Always ignored regardless of .gitignore
HARDCODED_IGNORE = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    "coverage", ".coverage", "htmlcov", "egg-info",
}


def _load_gitignore(repo_root: Path) -> list[str]:
    """Parse .gitignore and return a list of patterns."""
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        return []
    patterns = []
    for line in gitignore.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _is_ignored(file_path: Path, repo_root: Path, gitignore_patterns: list[str]) -> bool:
    """Check if a file should be ignored based on hardcoded dirs + .gitignore patterns."""
    # Check hardcoded ignore dirs
    for part in file_path.relative_to(repo_root).parts:
        if part in HARDCODED_IGNORE or part.startswith("."):
            return True

    # Check .gitignore patterns
    rel = str(file_path.relative_to(repo_root))
    for pattern in gitignore_patterns:
        # Handle directory patterns (trailing /)
        clean = pattern.rstrip("/")
        # Match against relative path or any path component
        if fnmatch.fnmatch(rel, clean) or fnmatch.fnmatch(rel, f"*/{clean}"):
            return True
        if fnmatch.fnmatch(rel, f"{clean}/*") or fnmatch.fnmatch(rel, f"*/{clean}/*"):
            return True
        # Also match the pattern with ** prefix for nested matches
        if fnmatch.fnmatch(rel, f"**/{clean}") or fnmatch.fnmatch(rel, f"**/{clean}/**"):
            return True
        # Direct component match (e.g. "migrations" matches "app/migrations/0001.py")
        if clean in file_path.relative_to(repo_root).parts:
            return True
    return False


def analyze_file(file_path: str) -> dict:
    """Extract functions, classes, imports from a source file using tree-sitter."""
    path = Path(file_path)
    suffix = path.suffix
    if suffix not in LANG_MAP:
        return {"error": f"Unsupported language: {suffix}"}

    lang = LANG_MAP[suffix]
    parser = tree_sitter_languages.get_parser(lang)
    code = path.read_bytes()
    tree = parser.parse(code)

    result = {
        "file": str(path),
        "language": lang,
        "functions": [],
        "classes": [],
        "imports": [],
        "exports": [],
    }

    def visit(node, depth=0):
        # Functions
        if node.type in ("function_definition", "function_declaration",
                         "method_definition", "arrow_function"):
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode() if name_node else "<anonymous>"
            result["functions"].append({
                "name": name,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
            })

        # Classes
        if node.type in ("class_definition", "class_declaration"):
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode() if name_node else "<unknown>"
            result["classes"].append({
                "name": name,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
            })

        # Imports
        if node.type in ("import_statement", "import_from_statement",
                         "import_declaration"):
            result["imports"].append(node.text.decode())

        for child in node.children:
            visit(child, depth + 1)

    visit(tree.root_node)
    return result


def analyze_directory(dir_path: str) -> dict:
    """Analyze all source files in a directory, respecting .gitignore."""
    path = Path(dir_path).resolve()
    gitignore_patterns = _load_gitignore(path)

    if gitignore_patterns:
        print(f"  Loaded {len(gitignore_patterns)} .gitignore patterns")

    all_results = []
    skipped = 0
    for ext in LANG_MAP:
        for f in path.rglob(f"*{ext}"):
            if _is_ignored(f, path, gitignore_patterns):
                skipped += 1
                continue
            all_results.append(analyze_file(str(f)))

    if skipped:
        print(f"  Skipped {skipped} ignored files")

    return {
        "directory": str(path),
        "files_analyzed": len(all_results),
        "results": all_results,
    }
