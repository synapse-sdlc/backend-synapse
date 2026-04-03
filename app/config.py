from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://synapse:synapse@localhost:5433/synapse"
    database_url_sync: str = "postgresql://synapse:synapse@localhost:5433/synapse"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Auth / JWT (REQUIRED — no defaults, must be set in .env)
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    jwt_secret: str = "CHANGE-ME-IN-ENV-FILE"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # Encryption (for GitHub tokens etc.) (REQUIRED — no defaults)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = "CHANGE-ME-IN-ENV-FILE"

    # S3
    s3_bucket: str = "synapse-data"
    s3_repos_prefix: str = "repos"
    s3_artifacts_prefix: str = "artifacts"

    # Local fallback (when S3 is not configured)
    local_repos_dir: str = "/tmp/synapse/repos"

    # CORS
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    # LLM Provider
    synapse_provider: str = "ollama"
    synapse_model: str = "qwen3:8b"
    synapse_bedrock_model: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    aws_default_region: str = "us-east-1"

    # AWS credentials — three options (checked in order):
    # 1. Bearer token (short-lived, 12 hrs): set AWS_BEARER_TOKEN_BEDROCK
    # 2. IAM keys (long-lived): set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
    # 3. Instance role (EC2/ECS): leave all empty, boto3 discovers automatically
    aws_bearer_token_bedrock: str = ""  # Presigned bearer token for Bedrock (expires in ~12 hrs)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""  # Only for temporary STS credentials

    class Config:
        env_file = ".env"


settings = Settings()


def get_provider():
    if settings.synapse_provider == "ollama":
        from core.orchestrator.providers.ollama_provider import OllamaProvider
        return OllamaProvider(model=settings.synapse_model)
    elif settings.synapse_provider == "bedrock":
        from core.orchestrator.providers.bedrock_provider import BedrockProvider
        return BedrockProvider(
            model=settings.synapse_bedrock_model,
            bearer_token=settings.aws_bearer_token_bedrock or None,
        )
    else:
        raise ValueError(f"Unknown provider: {settings.synapse_provider}")
