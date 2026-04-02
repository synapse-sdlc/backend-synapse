"""
GitHub REST API integration service.

Reuses the project's encrypted GitHub PAT for authentication.
"""

import re
import logging

import httpx

logger = logging.getLogger(__name__)


class GitHubService:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.text

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            return [
                {
                    "filename": f["filename"],
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                }
                for f in resp.json()
            ]

    async def get_pr_commits(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/commits",
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            return [
                {"sha": c["sha"][:8], "message": c["commit"]["message"]}
                for c in resp.json()
            ]

    @staticmethod
    def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
        """Parse a GitHub PR URL into (owner, repo, pr_number).

        Accepts:
          https://github.com/org/repo/pull/42
          https://github.com/org/repo/pull/42/files
        """
        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
        if not match:
            raise ValueError(f"Invalid GitHub PR URL: {pr_url}")
        return match.group(1), match.group(2), int(match.group(3))

    @staticmethod
    def parse_repo_url(github_url: str) -> tuple[str, str]:
        """Parse a GitHub repo URL into (owner, repo)."""
        match = re.match(r"https?://github\.com/([^/]+)/([^/.]+)", github_url)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {github_url}")
        return match.group(1), match.group(2)
