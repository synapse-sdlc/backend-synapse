"""
Path sandboxing for file access tools.

Restricts file tools to only access user project repos and artifacts.
Prevents the agent from reading Synapse platform code, secrets, or other projects.
"""
import os
from pathlib import Path

_sandbox_roots = []


def set_sandbox(allowed_roots):
    """Set allowed root directories. Only paths under these can be accessed."""
    global _sandbox_roots
    _sandbox_roots = [os.path.realpath(str(p)) for p in allowed_roots if p]


def check_path(path_str):
    """Check if a path is within the sandbox. Returns (resolved_path, error_or_None).

    For relative paths, tries resolving against each sandbox root first.
    This handles cases where the agent uses 'src/App.js' instead of the full
    '/tmp/synapse/repos/{project_id}/{repo_id}/repo/src/App.js'.
    """
    if not _sandbox_roots:
        return Path(path_str), None

    p = Path(path_str)

    # If absolute, check directly
    if p.is_absolute():
        resolved = os.path.realpath(str(p))
        for root in _sandbox_roots:
            if resolved.startswith(root):
                return Path(resolved), None
        return None, "Access denied: path is outside the project. Only project repositories are accessible."

    # Relative path — try each sandbox root
    for root in _sandbox_roots:
        candidate = Path(root) / p
        if candidate.exists():
            resolved = os.path.realpath(str(candidate))
            if resolved.startswith(root):
                return Path(resolved), None

    # Also try resolving from CWD (might land inside sandbox)
    resolved = os.path.realpath(str(p.resolve()))
    for root in _sandbox_roots:
        if resolved.startswith(root):
            return Path(resolved), None

    # If file doesn't exist at any root, return the first root attempt
    # so the error message shows the expected path
    if _sandbox_roots:
        expected = Path(_sandbox_roots[0]) / p
        return expected, None  # Let the caller handle "file not found"

    return None, "Access denied: path is outside the project. Only project repositories are accessible."


def get_default_root():
    """Get the first sandbox root as default path for tools like grep."""
    if _sandbox_roots:
        return _sandbox_roots[0]
    return "."
