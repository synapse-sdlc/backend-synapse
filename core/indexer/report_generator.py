"""Generate a detailed markdown report directly from static analysis data.

This covers ALL files — no LLM needed. The LLM-generated architecture
overview is then appended on top for the narrative/design analysis.
"""

from pathlib import Path
from collections import defaultdict


def generate_codebase_report(analysis: dict, repo_path: str) -> str:
    """Generate a comprehensive codebase index from static analysis results."""
    path = Path(repo_path)
    repo_name = path.name
    results = [r for r in analysis.get("results", []) if "error" not in r]

    # Aggregate stats
    lang_counts = defaultdict(int)
    total_functions = 0
    total_classes = 0
    total_imports = set()
    files_by_lang = defaultdict(list)

    for r in results:
        lang = r["language"]
        lang_counts[lang] += 1
        total_functions += len(r["functions"])
        total_classes += len(r["classes"])
        for imp in r["imports"]:
            total_imports.add(imp.split()[1] if len(imp.split()) > 1 else imp)
        files_by_lang[lang].append(r)

    lines = []
    lines.append(f"# {repo_name} — Codebase Index")
    lines.append("")
    lines.append(f"> Auto-generated from static analysis of **{len(results)} source files**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary stats
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Source files analyzed | {len(results)} |")
    lines.append(f"| Total functions/methods | {total_functions} |")
    lines.append(f"| Total classes | {total_classes} |")
    lines.append(f"| Unique imports | {len(total_imports)} |")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {lang.title()} files | {count} |")
    lines.append("")

    # Directory structure with file counts
    lines.append("## Directory Breakdown")
    lines.append("")
    dir_stats = defaultdict(lambda: {"files": 0, "functions": 0, "classes": 0})
    for r in results:
        rel = _rel_path(r["file"], repo_path)
        parts = rel.split("/")
        # Use top 2 directory levels
        dir_key = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if parts else "."
        dir_stats[dir_key]["files"] += 1
        dir_stats[dir_key]["functions"] += len(r["functions"])
        dir_stats[dir_key]["classes"] += len(r["classes"])

    lines.append("| Directory | Files | Functions | Classes |")
    lines.append("|-----------|-------|-----------|---------|")
    for d, stats in sorted(dir_stats.items(), key=lambda x: -x[1]["files"]):
        lines.append(f"| `{d}` | {stats['files']} | {stats['functions']} | {stats['classes']} |")
    lines.append("")

    # Class index — every class in the codebase
    all_classes = []
    for r in results:
        rel = _rel_path(r["file"], repo_path)
        for cls in r["classes"]:
            all_classes.append({
                "name": cls["name"],
                "file": rel,
                "line_start": cls["line_start"],
                "line_end": cls["line_end"],
                "size": cls["line_end"] - cls["line_start"] + 1,
            })

    if all_classes:
        lines.append("## Class Index")
        lines.append("")
        lines.append(f"**{len(all_classes)} classes** across the codebase:")
        lines.append("")

        # Group by directory
        classes_by_dir = defaultdict(list)
        for c in all_classes:
            parts = c["file"].split("/")
            dir_key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
            classes_by_dir[dir_key].append(c)

        for dir_key in sorted(classes_by_dir.keys()):
            classes = sorted(classes_by_dir[dir_key], key=lambda x: x["file"])
            lines.append(f"### `{dir_key}`")
            lines.append("")
            lines.append("| Class | File | Lines | Size |")
            lines.append("|-------|------|-------|------|")
            for c in classes:
                lines.append(f"| `{c['name']}` | `{c['file']}` | {c['line_start']}-{c['line_end']} | {c['size']}L |")
            lines.append("")

    # Function index — grouped by file, showing the largest/most important
    all_functions = []
    for r in results:
        rel = _rel_path(r["file"], repo_path)
        for func in r["functions"]:
            all_functions.append({
                "name": func["name"],
                "file": rel,
                "line_start": func["line_start"],
                "line_end": func["line_end"],
                "size": func["line_end"] - func["line_start"] + 1,
            })

    if all_functions:
        lines.append("## Function Index")
        lines.append("")
        lines.append(f"**{len(all_functions)} functions/methods** total.")
        lines.append("")

        # Show top 50 largest functions
        largest = sorted(all_functions, key=lambda x: -x["size"])[:50]
        lines.append("### Largest Functions (potential complexity hotspots)")
        lines.append("")
        lines.append("| Function | File | Lines | Size |")
        lines.append("|----------|------|-------|------|")
        for f in largest:
            lines.append(f"| `{f['name']}` | `{f['file']}` | {f['line_start']}-{f['line_end']} | {f['size']}L |")
        lines.append("")

        # Functions per file — show files with most functions
        funcs_per_file = defaultdict(int)
        for f in all_functions:
            funcs_per_file[f["file"]] += 1

        lines.append("### Files by Function Count (top 30)")
        lines.append("")
        lines.append("| File | Functions |")
        lines.append("|------|-----------|")
        for fpath, count in sorted(funcs_per_file.items(), key=lambda x: -x[1])[:30]:
            lines.append(f"| `{fpath}` | {count} |")
        lines.append("")

    # Import analysis — most used packages
    if total_imports:
        lines.append("## Import Analysis")
        lines.append("")

        # Count top-level package usage across files
        pkg_usage = defaultdict(int)
        for r in results:
            seen_pkgs = set()
            for imp in r["imports"]:
                # Extract top-level package
                parts = imp.replace("from ", "").replace("import ", "").split(".")
                pkg = parts[0].split()[0].strip() if parts and parts[0].strip() else ""
                if pkg and pkg not in seen_pkgs:
                    seen_pkgs.add(pkg)
                    pkg_usage[pkg] += 1

        lines.append("### Most Used Packages (by file count)")
        lines.append("")
        lines.append("| Package | Used in N files |")
        lines.append("|---------|-----------------|")
        for pkg, count in sorted(pkg_usage.items(), key=lambda x: -x[1])[:40]:
            lines.append(f"| `{pkg}` | {count} |")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Synapse Orchestrator — Static Analysis*")
    return "\n".join(lines)


def _rel_path(file_path: str, repo_path: str) -> str:
    """Get relative path, handling both absolute and relative paths."""
    try:
        return str(Path(file_path).relative_to(Path(repo_path).resolve()))
    except ValueError:
        try:
            return str(Path(file_path).relative_to(Path(repo_path)))
        except ValueError:
            return file_path
