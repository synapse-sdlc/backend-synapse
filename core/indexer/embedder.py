"""Generate embeddings using Ollama's nomic-embed-text model."""

import ollama


def embed_texts(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """Generate embeddings for a list of texts using Ollama."""
    embeddings = []
    for text in texts:
        response = ollama.embed(model=model, input=text)
        embeddings.append(response["embeddings"][0])
    return embeddings


def embed_single(text: str, model: str = "nomic-embed-text") -> list[float]:
    """Generate embedding for a single text."""
    response = ollama.embed(model=model, input=text)
    return response["embeddings"][0]
