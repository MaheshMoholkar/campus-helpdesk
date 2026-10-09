"""Deterministic stand-ins for the models.

They let the whole pipeline run in tests and offline, with no Ollama. They are
word-matching toys, not models: results from them say the plumbing works, and
nothing about answer quality.
"""

import hashlib
import math
import re
from collections.abc import Iterator, Sequence

from apps.api import prompts
from apps.api.llm.base import ChatResult, Message, ToolCall, Usage

_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    "a an and are as at be by for from how i in is it my of on or the to was what when "
    "where which who will with do does can me".split()
)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


class FakeEmbedder:
    """Bag-of-words hashed into a fixed-size vector: texts sharing words score as similar."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim
        self.model_id = f"fake-bow-{dim}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for word in _words(text):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        if not any(vector):
            vector[0] = 1.0  # pgvector cannot take the cosine of an all-zero vector
        return [v / norm for v in vector]


class FakeReranker:
    """Score = share of the query's words that appear in the text."""

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        wanted = set(_words(query))
        if not wanted:
            return [0.0] * len(texts)
        return [len(wanted & set(_words(text))) / len(wanted) for text in texts]


class FakeLLM:
    model = "fake-llm"

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None, max_tokens: int = 400
    ) -> ChatResult:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        last = messages[-1]
        usage = Usage(sum(len(str(m.get("content") or "")) for m in messages) // 4, 0)

        if system.startswith(prompts.TOOLS_HEAD):
            result = self._tools(messages, tools or [])
        elif system.startswith(prompts.REWRITE_HEAD):
            result = ChatResult(self._rewrite(last["content"]))
        elif system.startswith(prompts.INTENT_HEAD):
            result = ChatResult(self._intent(last["content"]))
        elif system.startswith(prompts.JUDGE_HEAD):
            result = ChatResult(self._judge(last["content"]))
        else:
            result = ChatResult(self._answer(last["content"]))

        usage.completion_tokens = len(result.text) // 4
        result.usage = usage
        return result

    def stream(self, messages: list[Message], usage: Usage, max_tokens: int = 800) -> Iterator[str]:
        result = self.chat(messages, max_tokens=max_tokens)
        yield from re.findall(r"\S+\s*", result.text)
        usage.add(result.usage)

    # --- per-task behaviour -----------------------------------------------------

    @staticmethod
    def _answer(user: str) -> str:
        """Quote the source that shares most words with the question, and cite it."""
        question = user.rsplit("Question:", 1)[-1]
        wanted = set(_words(question))
        sources = re.findall(r"^\[(\d+)\] [^\n]*\n(.*?)(?=\n\n\[\d+\] |\n\nQuestion:)", user, re.S | re.M)
        best_number, best_text, best_overlap = None, "", 0
        for number, text in sources:
            overlap = len(wanted & set(_words(text)))
            if overlap > best_overlap:
                best_number, best_text, best_overlap = number, text, overlap
        if best_number is None:
            return prompts.NO_ANSWER
        sentences = re.split(r"(?<=[.!?।])\s+", best_text.strip())
        ranked = sorted(sentences, key=lambda s: -len(wanted & set(_words(s))))
        return f"{ranked[0].strip()} [{best_number}]"

    @staticmethod
    def _rewrite(user: str) -> str:
        """Glue the previous user message onto a short follow-up."""
        latest = user.rsplit("Latest message:", 1)[-1].strip()
        earlier = re.findall(r"^user: (.*)$", user, re.M)
        if earlier and len(_words(latest)) <= 4:
            return f"{earlier[-1].rstrip('?. ')} {latest}"
        return latest

    @staticmethod
    def _intent(question: str) -> str:
        text = question.lower()
        if "bonafide" in text:
            return "action"
        if re.search(r"\b(my|mera|meri|mere|majha|majhi|maza|mazi)\b", text) and re.search(
            r"fee|attendance|result|sgpa|marks|leave|hajeri|upasthiti", text
        ):
            return "personal"
        if re.search(r"weather|cricket|movie|recipe|stock price|bitcoin", text):
            return "out_of_scope"
        return "faq"

    @staticmethod
    def _judge(user: str) -> str:
        source, _, claim = user.partition("CLAIM:")
        claim_words = set(_words(re.sub(r"\[\d+\]", "", claim)))
        if not claim_words:
            return "supported"
        share = len(claim_words & set(_words(source))) / len(claim_words)
        return "supported" if share >= 0.6 else "not_supported"

    @staticmethod
    def _tools(messages: list[Message], tools: list[dict]) -> ChatResult:
        last = messages[-1]
        if last["role"] == "tool":
            return ChatResult(f"Here is what I found: {last['content']}")
        available = {tool["function"]["name"] for tool in tools}
        text = str(last.get("content") or "").lower()
        wanted = None
        if "bonafide" in text:
            wanted = ToolCall("call_1", "request_bonafide", {"purpose": "as requested by the student"})
        elif "fee" in text:
            wanted = ToolCall("call_1", "get_my_fees", {})
        elif "attendance" in text:
            wanted = ToolCall("call_1", "get_my_attendance", {})
        elif "result" in text or "sgpa" in text or "marks" in text:
            wanted = ToolCall("call_1", "get_my_results", {})
        elif "leave" in text:
            wanted = ToolCall("call_1", "get_my_leave", {})
        if wanted and wanted.name in available:
            return ChatResult("", [wanted])
        return ChatResult(
            "I can look up your fees, attendance, results, leave balance, or request a bonafide certificate."
        )
