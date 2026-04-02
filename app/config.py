from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
    database_url_sync: str = "postgresql://synapse:synapse@localhost:5432/synapse"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # LLM Provider
    synapse_provider: str = "ollama"
    synapse_model: str = "qwen3:8b"
    synapse_bedrock_model: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    aws_default_region: str = "us-west-2"

    class Config:
        env_file = ".env"


settings = Settings()


def get_provider():
    if settings.synapse_provider == "ollama":
        from core.orchestrator.providers.ollama_provider import OllamaProvider
        return OllamaProvider(model=settings.synapse_model)
    elif settings.synapse_provider == "bedrock":
        from core.orchestrator.providers.bedrock_provider import BedrockProvider
        return BedrockProvider(model=settings.synapse_bedrock_model)
    else:
        raise ValueError(f"Unknown provider: {settings.synapse_provider}")
