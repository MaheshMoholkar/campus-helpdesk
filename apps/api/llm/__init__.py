"""Model clients, chosen by configuration."""

from dataclasses import dataclass

from apps.api.config import Settings
from apps.api.llm.base import Embedder, LLMClient, Reranker


@dataclass
class Models:
    embedder: Embedder
    llm: LLMClient
    reranker: Reranker | None


def build_models(settings: Settings) -> Models:
    if settings.ai_backend == "fake":
        from apps.api.llm.fake import FakeEmbedder, FakeLLM

        embedder: Embedder = FakeEmbedder(settings.embed_dim)
        llm: LLMClient = FakeLLM()
    elif settings.ai_backend == "ollama":
        from apps.api.llm.ollama import OllamaEmbedder, OllamaLLM

        embedder = OllamaEmbedder(settings.ollama_base_url, settings.embed_model, settings.embed_dim)
        llm = OllamaLLM(settings.ollama_base_url, settings.llm_model, settings.llm_seed)
    else:
        raise ValueError(f"unknown AI_BACKEND: {settings.ai_backend!r}")

    reranker: Reranker | None
    if settings.reranker == "none":
        reranker = None
    elif settings.reranker == "fake":
        from apps.api.llm.fake import FakeReranker

        reranker = FakeReranker()
    elif settings.reranker == "http":
        from apps.api.llm.ollama import HttpReranker

        reranker = HttpReranker(settings.rerank_url)
    else:
        raise ValueError(f"unknown RERANKER: {settings.reranker!r}")

    return Models(embedder, llm, reranker)
