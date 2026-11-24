"""Retrieval and answer quality on a small synthetic benchmark (backend/evals/).

5 company documents, 22 answerable questions (each with the document that holds
the answer and a phrase the answer must contain), 6 questions the documents
can't answer, and 8 paraphrased questions that avoid the documents' wording.
The extractive threshold was tuned on the first two groups, so their scores
are optimistic; the paraphrased group was written afterwards and is the more
honest signal. The numbers printed here are the ones reported in docs/testing.md;
run `pytest tests/test_rag_eval.py -s` to see them.

The thresholds are regression guards for the default offline setup (hashing
embeddings + extractive answers), not claims about real-world accuracy.
"""

import json
from pathlib import Path

from tests.conftest import API, bearer, drain, register_org, upload

EVALS = Path(__file__).resolve().parents[1] / "evals"


async def test_rag_benchmark(client, ingestor):
    admin = await register_org(client)
    for path in sorted((EVALS / "corpus").glob("*.md")):
        assert (await upload(client, admin, path.name, path.read_bytes())).status_code == 201
    await drain(ingestor)
    questions = json.loads((EVALS / "questions.json").read_text())
    headers = bearer(admin.token)

    hit1 = hit3 = correct = 0
    failures = []
    for item in questions["answerable"]:
        search = await client.post(
            f"{API}/search", headers=headers, json={"query": item["q"], "limit": 3}
        )
        titles = [r["document_title"] for r in search.json()["results"]]
        hit1 += titles[:1] == [item["doc"]]
        hit3 += item["doc"] in titles

        answer = (
            await client.post(f"{API}/chat/ask", headers=headers, json={"question": item["q"]})
        ).json()["answer"]
        cited_docs = {c["document_title"] for c in answer["citations"]}
        ok = (
            answer["status"] == "answered"
            and item["expect"].lower() in answer["content"].lower()
            and item["doc"] in cited_docs
        )
        correct += ok
        if not ok:
            failures.append(item["q"])

    refused = 0
    for question in questions["unanswerable"]:
        answer = (
            await client.post(f"{API}/chat/ask", headers=headers, json={"question": question})
        ).json()["answer"]
        refused += answer["status"] == "no_evidence"
        if answer["status"] != "no_evidence":
            failures.append(question)

    # Paraphrased questions use different words than the documents. They were written
    # AFTER the thresholds were tuned and are reported, not asserted: they show where
    # the offline setup (lexical, not semantic) falls short.
    para_hit3 = para_correct = para_wrong = 0
    for item in questions["paraphrased"]:
        search = await client.post(
            f"{API}/search", headers=headers, json={"query": item["q"], "limit": 3}
        )
        para_hit3 += item["doc"] in [r["document_title"] for r in search.json()["results"]]
        answer = (
            await client.post(f"{API}/chat/ask", headers=headers, json={"question": item["q"]})
        ).json()["answer"]
        right = item["expect"].lower() in answer["content"].lower()
        para_correct += answer["status"] == "answered" and right
        para_wrong += answer["status"] == "answered" and not right
    p = len(questions["paraphrased"])

    n, m = len(questions["answerable"]), len(questions["unanswerable"])
    print(
        f"\nRAG benchmark ({n} answerable, {m} unanswerable, {p} paraphrased questions)\n"
        f"  retrieval hit@1           {hit1}/{n} = {hit1 / n:.0%}\n"
        f"  retrieval hit@3           {hit3}/{n} = {hit3 / n:.0%}\n"
        f"  correct cited answers     {correct}/{n} = {correct / n:.0%}\n"
        f"  correct 'no evidence'     {refused}/{m} = {refused / m:.0%}\n"
        f"  paraphrased: hit@3        {para_hit3}/{p} = {para_hit3 / p:.0%}\n"
        f"  paraphrased: answered     {para_correct}/{p} = {para_correct / p:.0%}\n"
        f"  paraphrased: wrong answer {para_wrong}/{p}\n"
        f"  missed: {failures}"
    )
    assert hit3 / n >= 0.9
    assert correct / n >= 0.8
    assert refused == m  # never answer a question the documents can't support
    assert para_wrong == 0  # refusing is acceptable; a wrong answer is not
