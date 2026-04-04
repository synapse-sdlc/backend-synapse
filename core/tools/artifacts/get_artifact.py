import json
import logging
from pathlib import Path

ARTIFACT_DIR = Path("./artifacts")
logger = logging.getLogger("synapse.tools")


class GetArtifactTool:
    name = "get_artifact"
    _context_project_id = None

    @classmethod
    def set_context(cls, project_id=None):
        cls._context_project_id = project_id
    definition = {
        "name": "get_artifact",
        "description": "Retrieve a previously stored artifact by ID. Use this to read specs, plans, or architecture documents.",
        "input_schema": {
            "type": "object",
            "required": ["artifact_id"],
            "properties": {
                "artifact_id": {"type": "string", "description": "The artifact ID to retrieve"}
            }
        }
    }

    async def execute(self, arguments: dict) -> dict:
        artifact_id = arguments["artifact_id"]

        # 1. Project-scoped local cache (fast path)
        if self._context_project_id:
            scoped_dir = ARTIFACT_DIR / self._context_project_id
            scoped_path = scoped_dir / f"{artifact_id}.json"
            if scoped_path.exists():
                return json.loads(scoped_path.read_text())
            matches = list(scoped_dir.glob(f"{artifact_id}*.json")) if scoped_dir.exists() else []
            if matches:
                return json.loads(matches[0].read_text())

        # Flat local cache (backward compat)
        filepath = ARTIFACT_DIR / f"{artifact_id}.json"
        if filepath.exists():
            return json.loads(filepath.read_text())

        # Partial match on flat cache
        matches = list(ARTIFACT_DIR.glob(f"{artifact_id}*.json"))
        if matches:
            return json.loads(matches[0].read_text())

        # 2. Pull from S3 → local cache (if S3 configured)
        try:
            artifact = _pull_from_s3(artifact_id, self._context_project_id)
            if artifact:
                logger.info(f"get_artifact {artifact_id}: pulled from S3, cached locally")
                self._cache_locally(artifact_id, artifact)
                return artifact
        except Exception as e:
            logger.debug(f"S3 pull failed for {artifact_id}: {e}")

        # 3. Fallback: read from PostgreSQL artifacts table
        try:
            artifact = _pull_from_db(artifact_id)
            if artifact:
                logger.info(f"get_artifact {artifact_id}: loaded from DB, cached locally")
                self._cache_locally(artifact_id, artifact)
                return artifact
        except Exception as e:
            logger.debug(f"DB pull failed for {artifact_id}: {e}")

        return {"error": f"Artifact not found: {artifact_id}"}

    def _cache_locally(self, artifact_id, artifact):
        """Cache artifact to project-scoped local dir for fast subsequent reads."""
        try:
            if self._context_project_id:
                cache_dir = ARTIFACT_DIR / self._context_project_id
            else:
                cache_dir = ARTIFACT_DIR
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{artifact_id}.json").write_text(json.dumps(artifact, indent=2))
        except Exception as e:
            logger.debug(f"Failed to cache artifact {artifact_id} locally: {e}")


def _pull_from_s3(artifact_id: str, project_id: str = None):
    """Pull artifact JSON from S3."""
    import os, boto3
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return None
    prefix = os.environ.get("S3_ARTIFACTS_PREFIX", "artifacts")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)
    # Try project-scoped key first, then flat fallback
    keys = []
    if project_id:
        keys.append(f"{prefix}/{project_id}/{artifact_id}.json")
    keys.append(f"{prefix}/{artifact_id}.json")
    for key in keys:
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            return json.loads(resp["Body"].read().decode("utf-8"))
        except Exception:
            continue
    return None


def _pull_from_db(artifact_id: str):
    """Pull artifact from PostgreSQL as last resort."""
    import os
    from sqlalchemy import create_engine, text
    db_url = os.environ.get("DATABASE_URL_SYNC", "")
    if not db_url:
        db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        return None
    engine = create_engine(db_url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, type, name, content, parent_id, status, version FROM artifacts WHERE id = :id"),
            {"id": artifact_id}
        ).fetchone()
        if not row:
            row = conn.execute(
                text("SELECT id, type, name, content, parent_id, status, version FROM artifacts WHERE id LIKE :prefix LIMIT 1"),
                {"prefix": f"{artifact_id}%"}
            ).fetchone()
        if not row:
            return None
        content = row[3]
        return {
            "id": row[0], "type": row[1], "name": row[2],
            "content": json.dumps(content) if isinstance(content, dict) else content,
            "parent_id": row[4], "status": row[5], "version": row[6],
        }


get_artifact_tool = GetArtifactTool()
