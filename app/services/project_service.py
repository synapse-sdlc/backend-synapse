"""
Project service: handles GitHub clone, S3 upload, and codebase analysis.

Supports both legacy single-repo (project_id scoped) and new multi-repo
(project_id/repo_id scoped) storage patterns.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
import logging
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError

from app.config import settings

logger = logging.getLogger(__name__)


def clone_repo_to_s3(
    project_id: str,
    github_url: str,
    github_token: str = None,
    repo_id: str = None,
) -> str:
    """Clone a GitHub repo and upload it to S3 as a tar.gz archive.

    If repo_id is provided, uses repos/{project_id}/{repo_id}/repo.tar.gz.
    Otherwise falls back to legacy repos/{project_id}/repo.tar.gz.
    """
    if repo_id:
        s3_key = f"{settings.s3_repos_prefix}/{project_id}/{repo_id}/repo.tar.gz"
    else:
        s3_key = f"{settings.s3_repos_prefix}/{project_id}/repo.tar.gz"

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = os.path.join(tmpdir, "repo")

        clone_url = github_url
        if github_token and "github.com" in github_url:
            clone_url = github_url.replace("https://", f"https://{github_token}@")

        logger.info(f"Cloning {github_url} to {clone_path}")

        result = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, clone_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr}")

        git_dir = os.path.join(clone_path, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)

        archive_path = os.path.join(tmpdir, "repo.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(clone_path, arcname="repo")

        try:
            s3 = boto3.client("s3", region_name=settings.aws_default_region)
            s3.upload_file(archive_path, settings.s3_bucket, s3_key)
            logger.info(f"Uploaded repo archive to s3://{settings.s3_bucket}/{s3_key}")
        except (ClientError, BotoCoreError, NoCredentialsError, Exception) as e:
            logger.warning(f"S3 upload failed ({e}), falling back to local storage")
            if repo_id:
                local_path = Path(settings.local_repos_dir) / project_id / repo_id
            else:
                local_path = Path(settings.local_repos_dir) / project_id
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(clone_path, str(local_path / "repo"), dirs_exist_ok=True)
            return f"local://{local_path}/repo"

    return f"s3://{settings.s3_bucket}/{s3_key}"


def download_repo_from_s3(project_id: str, s3_key: str, repo_id: str = None) -> str:
    """Download and extract repo from S3 to a local temp directory."""
    if repo_id:
        local_repo_path = Path(settings.local_repos_dir) / project_id / repo_id / "repo"
    else:
        local_repo_path = Path(settings.local_repos_dir) / project_id / "repo"

    if local_repo_path.exists():
        logger.info(f"Repo already cached at {local_repo_path}")
        return str(local_repo_path)

    local_repo_path.parent.mkdir(parents=True, exist_ok=True)

    if s3_key.startswith("local://"):
        return s3_key.replace("local://", "")

    archive_path = str(local_repo_path.parent / "repo.tar.gz")
    actual_s3_key = s3_key.replace(f"s3://{settings.s3_bucket}/", "")

    try:
        s3 = boto3.client("s3", region_name=settings.aws_default_region)
        s3.download_file(settings.s3_bucket, actual_s3_key, archive_path)
    except ClientError as e:
        raise RuntimeError(f"Failed to download repo from S3: {e}")

    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=str(local_repo_path.parent))

    os.remove(archive_path)

    logger.info(f"Extracted repo to {local_repo_path}")
    return str(local_repo_path)


def build_context_summary(analysis: dict, repo_path: str) -> str:
    """Build a compact codebase summary for the agent system prompt."""
    from collections import defaultdict

    results = analysis.get("results", [])
    files_analyzed = analysis.get("files_analyzed", 0)
    repo = Path(repo_path).resolve()

    lang_counts = defaultdict(int)
    for r in results:
        lang_counts[r.get("language", "unknown")] += 1
    lang_str = ", ".join(
        f"{lang}: {cnt}" for lang, cnt in
        sorted(lang_counts.items(), key=lambda x: -x[1])
    )

    dir_counts = defaultdict(int)
    for r in results:
        try:
            rel = Path(r.get("file", "")).resolve().relative_to(repo)
            parts = rel.parts[:-1]
            key = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else (parts[0] if parts else ".")
        except (ValueError, IndexError):
            key = "."
        dir_counts[key] += 1

    top_dirs = sorted(dir_counts.items(), key=lambda x: -x[1])[:20]
    dirs_lines = [f"  {d}: {c} files" for d, c in top_dirs]

    summary = "\n".join([
        f"Codebase: {files_analyzed} files. Languages: {lang_str}",
        "",
        "Top directories by file count:",
        *dirs_lines,
    ])

    return summary[:8000]


def cleanup_local_repo(project_id: str, repo_id: str = None):
    """Remove locally cached repo to free disk space."""
    if repo_id:
        local_path = Path(settings.local_repos_dir) / project_id / repo_id
    else:
        local_path = Path(settings.local_repos_dir) / project_id
    if local_path.exists():
        shutil.rmtree(str(local_path))
        logger.info(f"Cleaned up local repo cache at {local_path}")
