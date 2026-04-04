import uuid
import logging

from qdrant_client import QdrantClient

logger = logging.getLogger("synapse.vector_store.qdrant")


class QdrantVectorStore:
    """Multi-collection vector store backed by Qdrant.

    Uses qdrant-client with fastembed for automatic embedding generation.
    Drop-in replacement for ChromaVectorStore — same public interface.
    """

    def __init__(self, collection_name: str = "codebase", url: str = "http://localhost:6333", api_key: str = None):
        connect_kwargs = {"url": url, "timeout": 60}
        if api_key:
            connect_kwargs["api_key"] = api_key
        self.client = QdrantClient(**connect_kwargs)
        self.default_collection = collection_name

    # ── Helpers ──

    @staticmethod
    def _to_uuid(string_id: str) -> str:
        """Convert an arbitrary string ID to a deterministic UUID for Qdrant."""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, string_id))

    def _collection_exists(self, name: str) -> bool:
        try:
            self.client.get_collection(name)
            return True
        except Exception:
            return False

    # ── Core methods (match ChromaVectorStore interface) ──

    def add_chunks(self, chunks: list[dict]):
        """Add code chunks with automatic embedding, batched for large repos."""
        batch_size = 64  # fastembed embeds in-process; keep batches moderate
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            self.client.add(
                collection_name=self.default_collection,
                documents=[c["content"] for c in batch],
                metadata=[{**c["metadata"], "_chunk_id": c["id"]} for c in batch],
                ids=[self._to_uuid(c["id"]) for c in batch],
            )
        logger.info("Indexed %d chunks into Qdrant collection '%s'", len(chunks), self.default_collection)

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        try:
            results = self.client.query(
                collection_name=self.default_collection,
                query_text=query,
                limit=n_results,
            )
        except Exception:
            return []
        return self._format_results(results)

    # ── Multi-collection methods ──

    def _repo_collection_name(self, project_id: str, repo_id: str) -> str:
        return f"{project_id[:8]}_repo_{repo_id[:8]}"

    def _knowledge_collection_name(self, project_id: str) -> str:
        return f"{project_id[:8]}_knowledge"

    def get_repo_collection(self, project_id: str, repo_id: str):
        """Return the repo collection name (Qdrant collections are referenced by name)."""
        return self._repo_collection_name(project_id, repo_id)

    def get_knowledge_collection(self, project_id: str):
        """Return the knowledge collection name."""
        return self._knowledge_collection_name(project_id)

    def add_chunks_to_repo(self, project_id: str, repo_id: str, chunks: list[dict]):
        """Index chunks into the per-repo collection."""
        col_name = self._repo_collection_name(project_id, repo_id)
        batch_size = 64
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            self.client.add(
                collection_name=col_name,
                documents=[c["content"] for c in batch],
                metadata=[{**c["metadata"], "_chunk_id": c["id"]} for c in batch],
                ids=[self._to_uuid(c["id"]) for c in batch],
            )
        logger.info("Indexed %d chunks into Qdrant repo collection '%s'", len(chunks), col_name)

    def search_repo(self, project_id: str, repo_id: str, query: str, n_results: int = 5) -> list[dict]:
        """Search within a specific repo's collection."""
        col_name = self._repo_collection_name(project_id, repo_id)
        try:
            results = self.client.query(
                collection_name=col_name,
                query_text=query,
                limit=n_results,
            )
        except Exception:
            return []
        return self._format_results(results, repo_id=repo_id)

    def search_all_repos(self, project_id: str, repo_ids: list[str], query: str, n_results: int = 5) -> list[dict]:
        """Search across all repos in a project, merge results by distance."""
        all_results = []
        for repo_id in repo_ids:
            try:
                results = self.search_repo(project_id, repo_id, query, n_results=n_results)
                all_results.extend(results)
            except Exception:
                continue
        all_results.sort(key=lambda r: r.get("distance", 999))
        return all_results[:n_results]

    def search_knowledge(self, project_id: str, query: str, n_results: int = 5) -> list[dict]:
        """Search the project's knowledge collection."""
        col_name = self._knowledge_collection_name(project_id)
        try:
            results = self.client.query(
                collection_name=col_name,
                query_text=query,
                limit=n_results,
            )
        except Exception:
            return []
        return self._format_results(results)

    def index_knowledge_entry(self, project_id: str, entry_id: str, title: str, content: str, tags: list = None):
        """Index a single knowledge entry in the project's knowledge collection."""
        col_name = self._knowledge_collection_name(project_id)
        text = f"{title}\n{content}"
        if tags:
            text += f"\nTags: {', '.join(tags)}"
        self.client.add(
            collection_name=col_name,
            documents=[text],
            metadata=[{"tags": ",".join(tags or []), "title": title[:100]}],
            ids=[self._to_uuid(entry_id)],
        )

    # ── Result formatting ──

    @staticmethod
    def _format_results(results, repo_id: str = None) -> list[dict]:
        """Convert Qdrant QueryResponse objects to the dict format consumers expect."""
        formatted = []
        for r in results:
            meta = {k: v for k, v in (r.metadata or {}).items() if k != "_chunk_id"}
            entry = {
                "content": r.document,
                "metadata": meta,
                # Qdrant returns cosine similarity (higher=better);
                # ChromaDB returns cosine distance (lower=better). Convert for compatibility.
                "distance": 1 - r.score,
            }
            if repo_id:
                entry["repo_id"] = repo_id
            formatted.append(entry)
        return formatted
