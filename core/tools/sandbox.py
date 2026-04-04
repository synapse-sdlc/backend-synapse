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
    """Check if a path is within the sandbox. Returns (resolved_path, error_or_None)."""
    if not _sandbox_roots:
        # No sandbox configured — allow all (backward compat for analysis tasks)
        return Path(path_str), None

    resolved = os.path.realpath(str(Path(path_str).resolve()))

    for root in _sandbox_roots:
        if resolved.startswith(root):
            return Path(resolved), None

    return None, "Access denied: path is outside the project. Only project repositories are accessible."


def get_default_root():
    """Get the first sandbox root as default path for tools like grep."""
    if _sandbox_roots:
        return _sandbox_roots[0]
    return "."
