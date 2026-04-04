import chromadb


class VectorStore:
    """Multi-collection vector store backed by ChromaDB.

    Supports per-repo code collections, a project-wide knowledge collection,
    and cross-repo search that merges results by distance.

    Backward compatible: default collection_name="codebase" still works.
    """

    def __init__(self, collection_name: str = "codebase", persist_path: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=persist_path)
        # Default collection for backward compatibility
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_chunks(self, chunks: list[dict]):
        """Add code chunks with embeddings, batched for large repos."""
        batch_size = 500
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            self.collection.add(
                ids=[c["id"] for c in batch],
                documents=[c["content"] for c in batch],
                metadatas=[c["metadata"] for c in batch],
            )

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        results = self.collection.query(query_texts=[query], n_results=n_results)
        return [
            {"content": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    # ── Multi-collection methods ──

    def get_repo_collection(self, project_id: str, repo_id: str):
        """Get or create a per-repo collection."""
        name = f"{project_id[:8]}_repo_{repo_id[:8]}"
        return self.client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    def get_knowledge_collection(self, project_id: str):
        """Get or create a project-wide knowledge collection."""
        name = f"{project_id[:8]}_knowledge"
        return self.client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    def search_repo(self, project_id: str, repo_id: str, query: str, n_results: int = 5) -> list[dict]:
        """Search within a specific repo's collection."""
        collection = self.get_repo_collection(project_id, repo_id)
        results = collection.query(query_texts=[query], n_results=n_results)
        if not results["documents"] or not results["documents"][0]:
            return []
        return [
            {"content": doc, "metadata": meta, "distance": dist, "repo_id": repo_id}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def search_all_repos(self, project_id: str, repo_ids: list[str], query: str, n_results: int = 5) -> list[dict]:
        """Search across all repos in a project in parallel, merge results by distance."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_results = []

        def _search_one(rid):
            try:
                return self.search_repo(project_id, rid, query, n_results=n_results)
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=min(len(repo_ids), 4)) as pool:
            futures = [pool.submit(_search_one, rid) for rid in repo_ids]
            for fut in as_completed(futures):
                all_results.extend(fut.result())

        all_results.sort(key=lambda r: r.get("distance", 999))
        return all_results[:n_results]

    def search_knowledge(self, project_id: str, query: str, n_results: int = 5) -> list[dict]:
        """Search the project's knowledge collection."""
        collection = self.get_knowledge_collection(project_id)
        try:
            results = collection.query(query_texts=[query], n_results=n_results)
        except Exception:
            return []
        if not results["documents"] or not results["documents"][0]:
            return []
        return [
            {"content": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def index_knowledge_entry(self, project_id: str, entry_id: str, title: str, content: str, tags: list = None):
        """Index a single knowledge entry in the project's knowledge collection."""
        collection = self.get_knowledge_collection(project_id)
        text = f"{title}\n{content}"
        if tags:
            text += f"\nTags: {', '.join(tags)}"
        collection.add(
            ids=[entry_id],
            documents=[text],
            metadatas=[{"tags": ",".join(tags or []), "title": title[:100]}],
        )
