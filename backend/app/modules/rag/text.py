"""Tiny text helpers shared by the reranker and the extractive answerer.

Deliberately simple (no NLP library): lower-case word tokens, a stop-word list
and crude suffix stripping, so "employees" matches "employee" and "leaves"
matches "leave". Good enough to measure how much of a question a passage covers.
"""

import re

_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

STOPWORDS = frozenset(
    """a about above after again all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from
    further get gets got had has have having he her here hers him his how i if in into is
    it its itself just me more most much many my no nor not of off on once only or other
    our ours out over own same she should so some such than that the their theirs them
    then there these they this those through to too under until up very was we were what
    when where which while who whom why will with would you your yours tell please know
    give explain describe""".split()
)


def stem(word: str) -> str:
    """Very light normalisation, applied the same way to questions and documents, so
    "employees"/"employee", "policies"/"policy" and "rotated"/"rotate" end up equal."""
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def terms(text: str) -> set[str]:
    """Content words of a text, stemmed."""
    return {stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS}


def coverage(query_terms: set[str], text: str) -> float:
    """Share of the query's content words that appear in `text` (0..1)."""
    if not query_terms:
        return 0.0
    return len(query_terms & terms(text)) / len(query_terms)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) > 2]
