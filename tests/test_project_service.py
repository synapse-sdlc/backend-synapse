"""Test project service: clone, S3 upload, download."""

import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.project_service import (
    clone_repo_to_s3,
    download_repo_from_s3,
    build_context_summary,
    cleanup_local_repo,
)


def test_clone_repo_falls_back_to_local():
    """When S3 is not available, clone should fall back to local storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake git repo to clone
        fake_repo = os.path.join(tmpdir, "fake-repo")
        os.makedirs(fake_repo)
        os.system(f"cd {fake_repo} && git init && touch README.md && git add . && git commit -m init 2>/dev/null")

        with patch("app.services.project_service.settings") as mock_settings:
            mock_settings.s3_bucket = "test-bucket"
            mock_settings.s3_repos_prefix = "repos"
            mock_settings.aws_default_region = "us-west-2"
            mock_settings.local_repos_dir = os.path.join(tmpdir, "local-repos")

            result = clone_repo_to_s3("test-project-id", fake_repo)

            # Should fall back to local:// since S3 is not available
            assert result.startswith("local://")
            assert "test-project-id" in result

            # Verify files exist locally
            local_path = result.replace("local://", "")
            assert os.path.exists(local_path)


def test_download_repo_from_local():
    """Download from local:// path should just return the path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_path = os.path.join(tmpdir, "repo")
        os.makedirs(fake_path)
        Path(fake_path, "README.md").write_text("hello")

        with patch("app.services.project_service.settings") as mock_settings:
            mock_settings.local_repos_dir = os.path.join(tmpdir, "cache")

            result = download_repo_from_s3("test-id", f"local://{fake_path}")
            assert result == fake_path


def test_build_context_summary():
    """Context summary should produce a string with file counts and languages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        analysis = {
            "files_analyzed": 5,
            "results": [
                {"file": os.path.join(tmpdir, "app/main.py"), "language": "python", "functions": [{"name": "main"}], "classes": []},
                {"file": os.path.join(tmpdir, "app/utils.py"), "language": "python", "functions": [], "classes": []},
                {"file": os.path.join(tmpdir, "src/App.js"), "language": "javascript", "functions": [], "classes": []},
            ],
        }

        summary = build_context_summary(analysis, tmpdir)
        assert "5 files" in summary
        assert "python" in summary.lower()


def test_cleanup_local_repo():
    """Cleanup should remove the local repo cache."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = os.path.join(tmpdir, "test-project")
        os.makedirs(os.path.join(project_dir, "repo"))
        Path(project_dir, "repo", "file.txt").write_text("data")

        with patch("app.services.project_service.settings") as mock_settings:
            mock_settings.local_repos_dir = tmpdir

            cleanup_local_repo("test-project")
            assert not os.path.exists(project_dir)
