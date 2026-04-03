"""
Jira Cloud REST API v3 integration service.

Uses Basic Auth (email:api_token) for authentication.
Descriptions use Atlassian Document Format (ADF).
Auto-discovers valid issue types per project (handles different Jira templates).
"""

import logging
from base64 import b64encode

import httpx

logger = logging.getLogger(__name__)

# Maps our desired type → possible Jira type names (tried in order)
ISSUE_TYPE_FALLBACKS = {
    "Epic": ["Epic"],
    "Story": ["Story", "User Story", "Task"],
    "Sub-task": ["Sub-task", "Subtask", "Sub-Task", "Child Issue", "Task"],
}


class JiraService:
    def __init__(self, site_url: str, email: str, api_token: str):
        self.base_url = site_url.rstrip("/")
        auth_str = b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {auth_str}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._type_cache = {}  # project_key → {our_name: jira_name}

    async def test_connection(self) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/myself",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"status": "ok", "user": data.get("displayName", data.get("emailAddress"))}

    async def get_project(self, project_key: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/project/{project_key}",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    async def _discover_issue_types(self, project_key: str) -> dict:
        """Discover valid issue type names for a project. Caches result."""
        if project_key in self._type_cache:
            return self._type_cache[project_key]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/issue/createmeta/{project_key}/issuetypes",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code >= 400:
                # Fallback: try older API format
                resp = await client.get(
                    f"{self.base_url}/rest/api/3/project/{project_key}",
                    headers=self.headers,
                    timeout=10,
                )

        available = set()
        try:
            data = resp.json()
            # createmeta format: {"issueTypes": [{"name": "Epic"}, ...]}
            if "issueTypes" in data:
                available = {t["name"] for t in data["issueTypes"]}
            # project format: may not have issue types directly
            elif "issueTypes" in data:
                available = {t["name"] for t in data["issueTypes"]}
        except Exception:
            pass

        if not available:
            # Last resort: query all issue types
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/rest/api/3/issuetype",
                    headers=self.headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    available = {t["name"] for t in resp.json()}

        logger.info(f"Jira project {project_key} issue types: {available}")

        # Build mapping: our desired type → actual Jira type name
        type_map = {}
        for our_type, candidates in ISSUE_TYPE_FALLBACKS.items():
            for candidate in candidates:
                if candidate in available:
                    type_map[our_type] = candidate
                    break
            if our_type not in type_map:
                # Use first available non-Epic as fallback for Story/Sub-task
                for t in available:
                    if t != "Epic" and our_type != "Epic":
                        type_map[our_type] = t
                        break

        logger.info(f"Jira type mapping: {type_map}")
        self._type_cache[project_key] = type_map
        return type_map

    async def _resolve_type(self, project_key: str, desired_type: str) -> str:
        """Resolve our desired issue type to the actual Jira type name."""
        type_map = await self._discover_issue_types(project_key)
        resolved = type_map.get(desired_type, desired_type)
        return resolved

    async def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str = "",
        parent_key: str = None,
        priority: str = None,
        labels: list = None,
    ) -> dict:
        """Create a Jira issue. Auto-resolves issue type names."""
        resolved_type = await self._resolve_type(project_key, issue_type)

        fields = {
            "project": {"key": project_key},
            "issuetype": {"name": resolved_type},
            "summary": summary,
        }
        if description:
            fields["description"] = self._to_adf(description)
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if priority:
            fields["priority"] = {"name": priority}
        if labels:
            fields["labels"] = labels

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/rest/api/3/issue",
                headers=self.headers,
                json={"fields": fields},
                timeout=30,
            )
            if resp.status_code >= 400:
                logger.error(f"Jira create_issue failed: {resp.status_code} {resp.text}")
                resp.raise_for_status()
            data = resp.json()
            return {
                "key": data["key"],
                "id": data["id"],
                "url": f"{self.base_url}/browse/{data['key']}",
            }

    async def create_epic(self, project_key: str, summary: str, description: str = "") -> dict:
        return await self.create_issue(project_key, "Epic", summary, description)

    async def create_story(self, project_key: str, summary: str, description: str = "", epic_key: str = None) -> dict:
        return await self.create_issue(project_key, "Story", summary, description, parent_key=epic_key)

    async def create_subtask(self, project_key: str, summary: str, description: str = "", parent_key: str = None) -> dict:
        return await self.create_issue(project_key, "Sub-task", summary, description, parent_key=parent_key)

    async def get_issue(self, issue_key: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/issue/{issue_key}",
                headers=self.headers,
                params={"fields": "status,summary,issuetype,parent"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    async def bulk_get_issues(self, issue_keys: list[str]) -> list[dict]:
        if not issue_keys:
            return []
        jql = f"key in ({','.join(issue_keys)})"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/rest/api/3/search",
                headers=self.headers,
                json={
                    "jql": jql,
                    "fields": ["status", "summary", "issuetype"],
                    "maxResults": len(issue_keys),
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("issues", [])

    @staticmethod
    def _to_adf(text: str) -> dict:
        """Convert markdown-like text to Atlassian Document Format (ADF).

        Supports: headings (##), bold (**), bullet lists (- ), code blocks (```), horizontal rules (---).
        """
        content = []
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Code block
            if line.strip().startswith("```"):
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing ```
                content.append({
                    "type": "codeBlock",
                    "attrs": {"language": "text"},
                    "content": [{"type": "text", "text": "\n".join(code_lines)}],
                })
                continue

            # Heading
            if line.startswith("### "):
                content.append({
                    "type": "heading", "attrs": {"level": 3},
                    "content": [{"type": "text", "text": line[4:].strip()}],
                })
                i += 1
                continue
            if line.startswith("## "):
                content.append({
                    "type": "heading", "attrs": {"level": 2},
                    "content": [{"type": "text", "text": line[3:].strip()}],
                })
                i += 1
                continue

            # Horizontal rule
            if line.strip() == "---":
                content.append({"type": "rule"})
                i += 1
                continue

            # Bullet list (collect consecutive - lines)
            if line.strip().startswith("- "):
                items = []
                while i < len(lines) and lines[i].strip().startswith("- "):
                    item_text = lines[i].strip()[2:]
                    items.append({
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": JiraService._parse_inline(item_text)}],
                    })
                    i += 1
                content.append({"type": "bulletList", "content": items})
                continue

            # Regular paragraph
            if line.strip():
                content.append({
                    "type": "paragraph",
                    "content": JiraService._parse_inline(line),
                })
            else:
                content.append({"type": "paragraph", "content": []})
            i += 1

        return {
            "type": "doc",
            "version": 1,
            "content": content if content else [
                {"type": "paragraph", "content": [{"type": "text", "text": text or ""}]}
            ],
        }

    @staticmethod
    def _parse_inline(text: str) -> list:
        """Parse inline bold (**text**) and code (`text`) into ADF marks."""
        import re
        parts = []
        remaining = text
        for segment in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', remaining):
            if not segment:
                continue
            if segment.startswith("**") and segment.endswith("**"):
                parts.append({"type": "text", "text": segment[2:-2], "marks": [{"type": "strong"}]})
            elif segment.startswith("`") and segment.endswith("`"):
                parts.append({"type": "text", "text": segment[1:-1], "marks": [{"type": "code"}]})
            else:
                parts.append({"type": "text", "text": segment})
        return parts if parts else [{"type": "text", "text": text}]
