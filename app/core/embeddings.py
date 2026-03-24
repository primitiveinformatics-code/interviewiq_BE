"""
Centralised embeddings helper.

Provider routing based on EMBEDDINGS_MODEL prefix:
  "cohere/<model>"  → Cohere Embed API  (requires COHERE_API_KEY)
  "ollama/<model>"  → local Ollama OpenAI-compatible endpoint (OLLAMA_BASE_URL)
  anything else     → OpenAI API         (requires OPENAI_API_KEY)

Usage:
    from app.core.embeddings import embed

    vector  = embed("some text")                             # → list[float]
    vectors = embed(["a", "b", "c"])                        # → list[list[float]]
    q_vec   = embed("search query", input_type="search_query")
"""

from __future__ import annotations

from openai import OpenAI

from app.core.config import settings


def embed(
    text: str | list[str],
    input_type: str = "search_document",
) -> list[float] | list[list[float]]:
    """Return embeddings for a single string or a list of strings.

    Args:
        text: Text or list of texts to embed.
        input_type: Cohere input_type hint — "search_document" for indexing,
                    "search_query" for queries. Ignored by OpenAI / Ollama.
    """
    model = settings.EMBEDDINGS_MODEL
    is_single = isinstance(text, str)
    inputs = [text] if is_single else text

    if model.startswith("cohere/"):
        import cohere
        cohere_model = model[len("cohere/"):]
        co = cohere.Client(api_key=settings.COHERE_API_KEY)
        response = co.embed(
            model=cohere_model,
            texts=inputs,
            input_type=input_type,
            truncate="END",
        )
        vectors = list(response.embeddings)
    elif model.startswith("ollama/"):
        client = OpenAI(base_url=settings.OLLAMA_BASE_URL, api_key="ollama")
        result = client.embeddings.create(model=model[len("ollama/"):], input=inputs)
        vectors = [d.embedding for d in result.data]
    else:
        result = OpenAI(api_key=settings.OPENAI_API_KEY).embeddings.create(
            model=model, input=inputs
        )
        vectors = [d.embedding for d in result.data]

    return vectors[0] if is_single else vectors
