"""Real models, reached through Ollama's OpenAI-compatible API."""

import json
from collections.abc import Iterator, Sequence

import httpx
from openai import OpenAI

from apps.api.llm.base import ChatResult, Message, ToolCall, Usage


def _client(base_url: str) -> OpenAI:
    # Ollama ignores the key, but the SDK insists on one.
    return OpenAI(base_url=base_url, api_key="ollama", timeout=180.0, max_retries=1)


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, expected_dim: int) -> None:
        self._client = _client(base_url)
        self._model = model
        self.model_id = f"ollama:{model}"
        self._expected_dim = expected_dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(model=self._model, input=list(texts))
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if len(vectors[0]) != self._expected_dim:
            # The vector column size is fixed; a different model needs a re-embed.
            raise RuntimeError(
                f"{self._model} returned {len(vectors[0])}-dim vectors, "
                f"but the database column is {self._expected_dim}-dim"
            )
        return vectors


class OllamaLLM:
    def __init__(self, base_url: str, model: str, seed: int) -> None:
        self._client = _client(base_url)
        self.model = model
        self._seed = seed

    def _options(self, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "temperature": 0,  # with a fixed seed, the same prompt gives the same answer
            "seed": self._seed,
            "max_tokens": max_tokens,
            # qwen "thinks" by default, which is slow; switch it off.
            "extra_body": {"reasoning_effort": "none"},
        }

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None, max_tokens: int = 400
    ) -> ChatResult:
        kwargs = self._options(max_tokens)
        if tools:
            kwargs["tools"] = tools
        response = self._client.chat.completions.create(messages=messages, **kwargs)
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(call.id, call.function.name, arguments))
        usage = Usage()
        if response.usage:
            usage = Usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return ChatResult(text=message.content or "", tool_calls=calls, usage=usage)

    def stream(self, messages: list[Message], usage: Usage, max_tokens: int = 800) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **self._options(max_tokens),
        )
        for chunk in stream:
            if chunk.usage:
                usage.add(Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens))
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class HttpReranker:
    """Calls a rerank service that speaks the Hugging Face text-embeddings-inference API:
    POST {"query": ..., "texts": [...]} -> [{"index": 0, "score": 0.93}, ...]
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._http = httpx.Client(timeout=60.0)

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        response = self._http.post(self._url, json={"query": query, "texts": list(texts)})
        response.raise_for_status()
        scores = [0.0] * len(texts)
        for item in response.json():
            scores[item["index"]] = float(item["score"])
        return scores
