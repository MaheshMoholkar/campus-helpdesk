"""Split a document into chunks small enough to fit several into an 8k prompt.

Chunks follow the document's own structure: a Markdown heading starts a new
chunk, paragraphs are kept whole where possible, and each chunk remembers the
heading it sits under.
"""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")  # "।" ends a sentence in Hindi and Marathi

# Words, not tokens: about 220 English words is roughly 300 tokens. Devanagari
# text costs more tokens per word, which the prompt budget leaves room for.
MAX_WORDS = 220
OVERLAP_WORDS = 30


@dataclass(frozen=True)
class Chunk:
    heading: str | None
    text: str


def _word_count(text: str) -> int:
    return len(text.split())


def _split_long(paragraph: str, max_words: int) -> list[str]:
    """Break one oversized paragraph at sentence ends (or by words if a sentence is huge)."""
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in _SENTENCE_END.split(paragraph):
        words = sentence.split()
        while len(words) > max_words:  # a single enormous sentence
            if current:
                pieces.append(" ".join(current))
                current, count = [], 0
            pieces.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if count + len(words) > max_words and current:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.extend(words)
        count += len(words)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _tail(text: str, max_words: int) -> str:
    """Last few whole sentences of `text`, used as overlap into the next chunk."""
    kept: list[str] = []
    count = 0
    for sentence in reversed(_SENTENCE_END.split(text)):
        words = len(sentence.split())
        if count + words > max_words:
            break
        kept.insert(0, sentence)
        count += words
    return " ".join(kept)


def chunk_document(body: str, max_words: int = MAX_WORDS, overlap_words: int = OVERLAP_WORDS) -> list[Chunk]:
    # 1. Group paragraphs under their nearest heading.
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    paragraph: list[str] = []

    def end_paragraph() -> None:
        if paragraph:
            sections[-1][1].append(" ".join(paragraph))
            paragraph.clear()

    for line in body.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        if heading:
            end_paragraph()
            sections.append((heading.group(1).strip(), []))
        elif not stripped:
            end_paragraph()
        else:
            paragraph.append(stripped)
    end_paragraph()

    # 2. Pack each section's paragraphs into chunks of at most max_words.
    chunks: list[Chunk] = []
    for heading, paragraphs in sections:
        pieces: list[str] = []
        for text in paragraphs:
            pieces.extend(_split_long(text, max_words) if _word_count(text) > max_words else [text])

        current: list[str] = []
        count = 0
        for piece in pieces:
            words = _word_count(piece)
            if current and count + words > max_words:
                finished = "\n\n".join(current)
                chunks.append(Chunk(heading, finished))
                # Start the next chunk with the end of this one, so a fact that
                # straddles the cut is still whole in one of them.
                overlap = _tail(finished, overlap_words)
                current = [overlap] if overlap else []
                count = _word_count(overlap)
            current.append(piece)
            count += words
        if current:
            chunks.append(Chunk(heading, "\n\n".join(current)))

    return chunks
