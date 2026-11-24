"""Search and chat: hybrid retrieval, permissions, citations, no-evidence, conversations."""

import httpx
import pytest

from app.main import create_app
from tests.conftest import (
    API,
    Account,
    add_user,
    bearer,
    drain,
    make_settings,
    register_org,
    upload,
)

HANDBOOK = b"""# Employee handbook

## Leave
Full-time employees receive 24 days of paid leave per year.
Unused leave of up to 5 days can be carried over to the next year.

## Working hours
Core hours are 10:00 to 16:00, Monday to Friday.
Employees may work from home up to two days per week with manager approval.
"""

SALARIES = b"""# Salary bands 2027

Engineering salary band L3 ranges from 18 to 26 lakh rupees per year.
Bonus payouts happen every March after performance reviews.
"""

SECURITY = b"""# IT security policy

Passwords must be at least 14 characters long and are rotated every 180 days.
Report phishing emails to security@globex.example within one hour.
"""


async def _org_with_documents(client, ingestor) -> Account:
    admin = await register_org(client)
    await upload(client, admin, "handbook.md", HANDBOOK)
    await upload(client, admin, "salaries.md", SALARIES, min_role="org_admin")
    await upload(client, admin, "security.md", SECURITY)
    assert await drain(ingestor) == 3
    return admin


async def _search(client, account, query, **extra) -> httpx.Response:
    return await client.post(
        f"{API}/search", headers=bearer(account.token), json={"query": query, **extra}
    )


async def _ask(client, account, question, conversation_id=None) -> httpx.Response:
    body = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return await client.post(f"{API}/chat/ask", headers=bearer(account.token), json=body)


# --- search ---------------------------------------------------------------------------


async def test_search_ranks_the_relevant_passage_first(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    response = await _search(client, admin, "How many days of paid leave do employees get?")
    assert response.status_code == 200, response.text
    body = response.json()
    top = body["results"][0]
    assert top["document_title"] == "handbook"
    assert "24 days of paid leave" in top["content"]
    assert top["keyword_match"] is True
    assert 0 < top["score"] <= 1
    assert body["candidates"] >= len(body["results"])


async def test_search_never_returns_documents_above_the_callers_role(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    employee = await add_user(client, admin, "emp@acme.com", "employee")
    query = "engineering salary band bonus"

    admin_titles = {
        r["document_title"] for r in (await _search(client, admin, query)).json()["results"]
    }
    assert "salaries" in admin_titles
    employee_titles = {
        r["document_title"] for r in (await _search(client, employee, query)).json()["results"]
    }
    assert "salaries" not in employee_titles


async def test_search_is_isolated_between_organizations(client, ingestor):
    await _org_with_documents(client, ingestor)
    other = await register_org(client, "Other Org", "boss@other.com")
    response = await _search(client, other, "paid leave days")
    assert response.json()["results"] == []


async def test_documents_are_searchable_only_once_ready(client, ingestor):
    admin = await register_org(client)
    await upload(client, admin, "handbook.md", HANDBOOK)
    assert (await _search(client, admin, "paid leave")).json()["results"] == []
    await drain(ingestor)
    assert (await _search(client, admin, "paid leave")).json()["results"]


async def test_search_can_be_limited_to_documents(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    docs = (await client.get(f"{API}/documents", headers=bearer(admin.token))).json()["items"]
    security = next(d["id"] for d in docs if d["title"] == "security")
    response = await _search(client, admin, "days", document_ids=[security])
    assert {r["document_title"] for r in response.json()["results"]} <= {"security"}


async def test_search_with_only_stopwords_or_unrelated_words_finds_nothing(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    assert (await _search(client, admin, "what is the")).json()["results"] == []
    assert (await _search(client, admin, "quantum chromodynamics")).json()["results"] == []


async def test_search_validation(client):
    admin = await register_org(client)
    assert (await _search(client, admin, "")).status_code == 422
    assert (await _search(client, admin, "x", limit=500)).status_code == 422


# --- ask --------------------------------------------------------------------------------


async def test_answer_quotes_the_documents_with_citations(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    response = await _ask(client, admin, "How many days of paid leave do employees get?")
    assert response.status_code == 200, response.text
    body = response.json()
    answer = body["answer"]
    assert answer["status"] == "answered"
    assert "24 days of paid leave" in answer["content"]
    assert "[1]" in answer["content"]
    assert answer["model"] == "extractive"
    assert answer["latency_ms"] >= 0
    citation = answer["citations"][0]
    assert citation["number"] == 1
    assert citation["available"] is True
    assert citation["document_title"] == "handbook"
    assert "24 days" in citation["snippet"]
    assert body["question"]["content"] == "How many days of paid leave do employees get?"


async def test_unanswerable_question_says_so_instead_of_guessing(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    answer = (
        await _ask(client, admin, "What is the refund policy for enterprise customers?")
    ).json()["answer"]
    assert answer["status"] == "no_evidence"
    assert answer["citations"] == []
    assert "couldn't find" in answer["content"]


async def test_no_documents_means_no_evidence(client):
    admin = await register_org(client)
    answer = (await _ask(client, admin, "How many days of leave do we get?")).json()["answer"]
    assert answer["status"] == "no_evidence"


async def test_employee_cannot_get_answers_from_restricted_documents(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    employee = await add_user(client, admin, "emp@acme.com", "employee")
    question = "What is the salary band for engineering L3?"
    assert (await _ask(client, admin, question)).json()["answer"]["status"] == "answered"
    answer = (await _ask(client, employee, question)).json()["answer"]
    assert answer["status"] == "no_evidence"
    assert "lakh" not in answer["content"]


async def test_conversation_keeps_history_and_follow_ups(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    first = (await _ask(client, admin, "How many days of paid leave do employees get?")).json()
    conversation_id = first["conversation_id"]
    second = await _ask(client, admin, "How often are passwords rotated?", conversation_id)
    assert second.json()["conversation_id"] == conversation_id
    assert "180 days" in second.json()["answer"]["content"]

    detail = (
        await client.get(f"{API}/conversations/{conversation_id}", headers=bearer(admin.token))
    ).json()
    assert detail["title"] == "How many days of paid leave do employees get?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]

    listed = (await client.get(f"{API}/conversations", headers=bearer(admin.token))).json()
    assert [c["id"] for c in listed] == [conversation_id]


async def test_conversations_are_private_to_their_owner(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    colleague = await add_user(client, admin, "colleague@acme.com", "org_admin")
    conversation_id = (await _ask(client, admin, "paid leave days?")).json()["conversation_id"]
    url = f"{API}/conversations/{conversation_id}"
    headers = bearer(colleague.token)
    assert (await client.get(url, headers=headers)).status_code == 404
    assert (await client.patch(url, headers=headers, json={"title": "x"})).status_code == 404
    assert (await client.delete(url, headers=headers)).status_code == 404
    assert (await _ask(client, colleague, "more?", conversation_id)).status_code == 404
    assert (await client.get(f"{API}/conversations", headers=headers)).json() == []


async def test_citations_are_hidden_when_the_source_becomes_restricted(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    employee = await add_user(client, admin, "emp@acme.com", "employee")
    asked = (await _ask(client, employee, "How many days of paid leave do employees get?")).json()
    citation = asked["answer"]["citations"][0]
    assert citation["available"] is True

    await client.patch(
        f"{API}/documents/{citation['document_id']}",
        headers=bearer(admin.token),
        json={"min_role": "manager"},
    )
    detail = (
        await client.get(
            f"{API}/conversations/{asked['conversation_id']}", headers=bearer(employee.token)
        )
    ).json()
    hidden = detail["messages"][1]["citations"][0]
    assert hidden == {
        "number": 1,
        "available": False,
        "document_id": None,
        "document_title": None,
        "chunk_index": None,
        "page_start": None,
        "page_end": None,
        "snippet": None,
    }


async def test_citations_of_deleted_documents_become_unavailable(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    asked = (await _ask(client, admin, "How often are passwords rotated?")).json()
    document_id = asked["answer"]["citations"][0]["document_id"]
    await client.delete(f"{API}/documents/{document_id}", headers=bearer(admin.token))
    detail = (
        await client.get(
            f"{API}/conversations/{asked['conversation_id']}", headers=bearer(admin.token)
        )
    ).json()
    assert detail["messages"][1]["citations"][0]["available"] is False


async def test_rename_and_delete_conversation(client, ingestor):
    admin = await _org_with_documents(client, ingestor)
    conversation_id = (await _ask(client, admin, "paid leave?")).json()["conversation_id"]
    url = f"{API}/conversations/{conversation_id}"
    renamed = await client.patch(
        url, headers=bearer(admin.token), json={"title": "Leave questions"}
    )
    assert renamed.json()["title"] == "Leave questions"
    assert (await client.delete(url, headers=bearer(admin.token))).status_code == 204
    assert (await client.get(url, headers=bearer(admin.token))).status_code == 404


async def test_ask_validation_and_access(client):
    admin = await register_org(client)
    assert (await _ask(client, admin, "")).status_code == 422
    assert (await _ask(client, admin, "x" * 2001)).status_code == 422
    unknown = await _ask(client, admin, "hi", "00000000-0000-0000-0000-000000000000")
    assert unknown.status_code == 404
    no_token = await client.post(f"{API}/chat/ask", json={"question": "hi"})
    assert no_token.status_code == 401


async def test_questions_are_rate_limited_per_user(client_factory, tmp_path):
    from redis.asyncio import Redis

    from tests.conftest import TEST_REDIS_URL

    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.flushdb()
    settings = make_settings(
        rate_limit_enabled=True, chat_rate_limit_per_user=2, storage_dir=str(tmp_path)
    )
    try:
        async for client in client_factory(create_app(settings)):
            admin = await register_org(client)
            assert (await _ask(client, admin, "one")).status_code == 200
            assert (await _ask(client, admin, "two")).status_code == 200
            limited = await _ask(client, admin, "three")
            assert limited.status_code == 429
            assert "Retry-After" in limited.headers
    finally:
        await redis.flushdb()
        await redis.aclose()


@pytest.mark.parametrize("path", ["/search", "/chat/ask"])
async def test_super_admin_cannot_use_tenant_search(client, ingestor, path):
    from app.modules.auth.models import User
    from app.modules.auth.security import hash_password
    from tests.conftest import PASSWORD, login

    async with ingestor.sessions() as session:
        session.add(
            User(
                email="root@nexus.com",
                full_name="Root",
                password_hash=hash_password(PASSWORD),
                role="super_admin",
            )
        )
        await session.commit()
    root = await login(client, "root@nexus.com")
    body = {"query": "x"} if path == "/search" else {"question": "x"}
    response = await client.post(f"{API}{path}", headers=bearer(root.token), json=body)
    assert response.status_code == 403
