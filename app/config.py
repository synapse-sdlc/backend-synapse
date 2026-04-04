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

    # Vector Store
    vector_store_provider: str = "chromadb"  # "chromadb" or "qdrant"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Public URL (for webhook URLs — set to ngrok/production URL)
    public_url: str = "http://localhost:8000"

    # CORS
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    # LLM Provider
    synapse_provider: str = "ollama"
    synapse_model: str = "qwen3:8b"
    synapse_bedrock_model: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    bedrock_model_fast: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_model_balanced: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_model_powerful: str = "us.anthropic.claude-opus-4-6"
    aws_default_region: str = "us-east-1"

    # AWS credentials — three options (checked in order):
    # 1. Bearer token (short-lived, 12 hrs): set AWS_BEARER_TOKEN_BEDROCK
    # 2. IAM keys (long-lived): set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
    # 3. Instance role (EC2/ECS): leave all empty, boto3 discovers automatically
    # Presigned bearer token for Bedrock (expires in ~12 hrs)
    aws_bearer_token_bedrock: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""  # Only for temporary STS credentials

    # Bedrock Guardrails (optional — set to enable content filtering)
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_arn: str = ""
    bedrock_guardrail_version: str = "DRAFT"

    # Cognito (optional — when set, Cognito JWTs are accepted alongside local JWTs)
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_region: str = "us-east-1"

    @property
    def cognito_jwks_url(self) -> str:
        if not self.cognito_user_pool_id:
            return ""
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}/.well-known/jwks.json"

    # GitHub Webhooks — generate with: python -c "import secrets; print(secrets.token_hex(32))"
    github_webhook_secret: str = ""

    # Langfuse observability (optional — leave empty to disable)
    # Sign up at https://cloud.langfuse.com or self-host
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://us.cloud.langfuse.com"
    # SDK-standard alias — takes precedence over langfuse_host when set
    langfuse_base_url: str = ""

    # Sentry error monitoring (optional — leave empty to disable)
    # Get your DSN at https://sentry.io
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.1  # 10% of transactions

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def _build_model_tiers():
    return {
        "bedrock": {
            "fast": {
                "model_id": settings.bedrock_model_fast,
                "label": "Fast",
                "description": "Quick drafts and simple tasks (~3x faster)",
                "speed": "~200 tok/s",
                "icon": "zap",
            },
            "balanced": {
                "model_id": settings.bedrock_model_balanced,
                "label": "Balanced",
                "description": "Best for most tasks (recommended)",
                "speed": "~70 tok/s",
                "icon": "star",
            },
            "powerful": {
                "model_id": settings.bedrock_model_powerful,
                "label": "Powerful",
                "description": "Complex features, large codebases",
                "speed": "~30 tok/s",
                "icon": "diamond",
            },
        },
        "ollama": {
            "fast": {"model_id": "qwen3:8b", "label": "Fast", "description": "Quick local inference", "speed": "Fast", "icon": "zap"},
            "balanced": {"model_id": "qwen3:8b", "label": "Balanced", "description": "Default local model", "speed": "Medium", "icon": "star"},
            "powerful": {"model_id": "qwen3:32b", "label": "Powerful", "description": "Higher quality, slower", "speed": "Slow", "icon": "diamond"},
        },
    }


MODEL_TIERS = _build_model_tiers()


def get_provider(model_tier=None):
    provider_name = settings.synapse_provider
    tier = model_tier or "balanced"
    tier_config = MODEL_TIERS.get(provider_name, {}).get(tier)

    if provider_name == "ollama":
        from core.orchestrator.providers.ollama_provider import OllamaProvider
        model = tier_config["model_id"] if tier_config else settings.synapse_model
        return OllamaProvider(model=model)
    elif provider_name == "bedrock":
        from core.orchestrator.providers.bedrock_provider import BedrockProvider
        model = tier_config["model_id"] if tier_config else settings.synapse_bedrock_model
        return BedrockProvider(
            model=model,
            bearer_token=settings.aws_bearer_token_bedrock or None,
            region=settings.aws_default_region,
            guardrail_id=settings.bedrock_guardrail_id or None,
            guardrail_version=settings.bedrock_guardrail_version or "DRAFT",
        )
    else:
        raise ValueError(
            f"Unknown provider: {provider_name}. Set SYNAPSE_PROVIDER to 'ollama' or 'bedrock'.")


def get_available_tiers():
    """Return tier metadata for frontend display."""
    tiers = MODEL_TIERS.get(settings.synapse_provider, {})
    return [{"tier": k, **{kk: vv for kk, vv in v.items() if kk != "model_id"}} for k, v in tiers.items()]
