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
            artifact = _pull_from_s3(artifact_id)
            if artifact:
                logger.info(f"get_artifact {artifact_id}: pulled from S3, cached locally")
                ARTIFACT_DIR.mkdir(exist_ok=True)
                filepath.write_text(json.dumps(artifact, indent=2))
                return artifact
        except Exception as e:
            logger.debug(f"S3 pull failed for {artifact_id}: {e}")

        # 3. Fallback: read from PostgreSQL artifacts table
        try:
            artifact = _pull_from_db(artifact_id)
            if artifact:
                logger.info(f"get_artifact {artifact_id}: loaded from DB")
                return artifact
        except Exception as e:
            logger.debug(f"DB pull failed for {artifact_id}: {e}")

        return {"error": f"Artifact not found: {artifact_id}"}


def _pull_from_s3(artifact_id: str):
    """Pull artifact JSON from S3."""
    import os, boto3
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return None
    prefix = os.environ.get("S3_ARTIFACTS_PREFIX", "artifacts")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)
    try:
        resp = s3.get_object(Bucket=bucket, Key=f"{prefix}/{artifact_id}.json")
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
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
