import chromadb


class VectorStore:
    def __init__(self, collection_name: str = "codebase"):
        self.client = chromadb.PersistentClient(path="./chroma_db")
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
