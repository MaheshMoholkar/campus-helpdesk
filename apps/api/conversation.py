"""Level 3 helpers: language detection, follow-up rewriting, intent routing."""

import re

from apps.api import prompts
from apps.api.llm.base import LLMClient, Usage

INTENTS = ("faq", "personal", "action", "out_of_scope")

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Common words that differ between the two languages; both use the same script.
_MARATHI_WORDS = frozenset(
    "आहे आहेत काय कधी कुठे कसे कसा किती नाही मला माझे माझी माझा आणि साठी करा आहेस हवे हवी पाहिजे".split()
)
_HINDI_WORDS = frozenset("है हैं क्या कब कहाँ कहां कैसे कितना कितनी नहीं मुझे मेरा मेरी मेरे और लिए करें चाहिए हूँ हूं".split())
# Hindi written in Latin letters.
_HINGLISH_WORDS = frozenset(
    "kya hai hain kab kaise kahan kitna kitni kitne nahi nahin mera meri mere mujhe aur "
    "chahiye batao bataiye karna hoga kaun kyun kyon ka ki ke liye se mein hota hoti".split()
)


def detect_language(text: str) -> str:
    """Return "en", "hi", "mr" or "hinglish". A cheap heuristic, good enough to pick
    reply templates; the model itself is told to answer in the question's language."""
    if _DEVANAGARI.search(text):
        words = set(re.findall(r"[ऀ-ॿ]+", text))
        return "mr" if len(words & _MARATHI_WORDS) > len(words & _HINDI_WORDS) else "hi"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for word in words if word in _HINGLISH_WORDS)
    return "hinglish" if hits >= 2 or (words and hits / len(words) >= 0.34) else "en"


def rewrite_question(llm: LLMClient, history: list[dict], question: str, usage: Usage) -> str:
    """Make a follow-up standalone, e.g. "and for hostel?" -> "What is the hostel fee?".

    Search works on one self-contained query, so this runs before retrieval.
    """
    if not history:
        return question
    result = llm.chat(prompts.rewrite_messages(history, question), max_tokens=120)
    usage.add(result.usage)
    rewritten = result.text.strip().strip('"')
    # A rewrite that is empty or far longer than the question is the model going
    # off the rails; fall back to what the user typed.
    if not rewritten or len(rewritten) > max(300, 4 * len(question)):
        return question
    return rewritten


def classify_intent(llm: LLMClient, question: str, usage: Usage) -> str:
    result = llm.chat(prompts.intent_messages(question), max_tokens=10)
    usage.add(result.usage)
    reply = result.text.strip().lower()
    for intent in ("out_of_scope", "personal", "action", "faq"):
        if intent in reply:
            return intent
    return "faq"  # when unsure, try to answer from documents; the abstain check still applies
