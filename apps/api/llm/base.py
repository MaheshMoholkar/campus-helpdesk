"""The three model interfaces the app depends on (docs/spec.md section 10.6).

Protocols are structural interfaces: any class with these methods fits, no
inheritance needed. Swapping a model means writing one small class and changing
a setting.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]  # OpenAI chat format: {"role": ..., "content": ...}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class Embedder(Protocol):
    model_id: str  # recorded with each document, so a model change forces a re-embed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per input text, in the same order."""
        ...


class LLMClient(Protocol):
    model: str

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None, max_tokens: int = 400
    ) -> ChatResult:
        """One complete reply, possibly asking for tool calls."""
        ...

    def stream(self, messages: list[Message], usage: Usage, max_tokens: int = 800) -> Iterator[str]:
        """Yield the reply piece by piece; fill `usage` once the stream ends."""
        ...


class Reranker(Protocol):
    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """One relevance score per text, higher is better, in the same order."""
        ...
