"""Vector store factory — returns ChromaDB or Qdrant backend based on config.

All consumers keep doing:
    from core.indexer.vector_store import VectorStore
    store = VectorStore()

The factory reads VECTOR_STORE_PROVIDER from settings and returns the
appropriate implementation.  Both backends expose the same public interface.
"""


def VectorStore(collection_name: str = "codebase", persist_path: str = "./chroma_db"):
    """Factory that returns the configured vector store backend."""
    from app.config import settings

    provider = getattr(settings, "vector_store_provider", "chromadb")

    if provider == "qdrant":
        from core.indexer.qdrant_store import QdrantVectorStore
        return QdrantVectorStore(
            collection_name=collection_name,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    else:
        from core.indexer.chroma_store import ChromaVectorStore
        return ChromaVectorStore(
            collection_name=collection_name,
            persist_path=persist_path,
        )
