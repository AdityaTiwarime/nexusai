"""End-to-end smoke test against a running stack (used by CI after `docker compose up`).

Registers a new organization, uploads a document through the frontend's nginx
proxy, waits for the worker to process it, checks that chunks were created, and
asks a question that must be answered with a citation, plus one that must not.
Finally an agent run creates a ticket and an email only after a second user
(a manager) approves it.
Standard library only, so it runs on any machine with Python 3.

    python scripts/smoke_test.py [base_url]     # default http://localhost:5173
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173").rstrip("/") + "/api/v1"
# The mock services as seen by the backend container, and by this script.
MOCK_FROM_BACKEND = os.environ.get("MOCK_URL_FROM_BACKEND", "http://mock-services:9000")
MOCK_FROM_HERE = os.environ.get("MOCK_URL_FROM_HERE", "http://localhost:9000")


def call(method: str, path: str, token: str | None = None, body=None, content_type=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None and content_type is None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif body is not None:
        data, headers["Content-Type"] = body, content_type
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def multipart(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    status, tokens = call("POST", "/auth/register", body={
        "organization_name": f"Smoke {suffix}",
        "full_name": "Smoke Test",
        "email": f"smoke-{suffix}@example.com",
        "password": "Smoke-Test-Password-1",
    })  # fmt: skip
    assert status == 201, (status, tokens)
    token = tokens["access_token"]

    text = b"# Travel policy\n\nTrain travel is preferred for trips under four hours."
    status, doc = call("POST", "/documents", token, *multipart("travel-policy.md", text))
    assert status == 201, (status, doc)
    print(f"uploaded document {doc['id']} (status={doc['status']})")

    for _ in range(60):
        status, doc = call("GET", f"/documents/{doc['id']}", token)
        if doc["status"] in ("ready", "failed"):
            break
        time.sleep(1)
    assert doc["status"] == "ready", f"document did not become ready: {doc}"

    status, chunks = call("GET", f"/documents/{doc['id']}/chunks", token)
    assert status == 200 and chunks["total"] >= 1, chunks
    assert "Train travel" in chunks["items"][0]["content"]
    print(f"ok: worker processed the document into {chunks['total']} chunk(s)")

    status, reply = call("POST", "/chat/ask", token, {"question": "Is train travel preferred for short trips?"})
    assert status == 200, (status, reply)
    answer = reply["answer"]
    assert answer["status"] == "answered" and answer["citations"], answer
    assert answer["citations"][0]["document_id"] == doc["id"], answer
    print(f"ok: answered with a citation: {answer['content'][:80]}")

    status, reply = call("POST", "/chat/ask", token, {"question": "Who is the CEO of the company?"})
    assert status == 200 and reply["answer"]["status"] == "no_evidence", reply
    print("ok: unanswerable question was refused instead of guessed")

    status, run = call("POST", "/agents/runs", token, {
        "request": "Customer jane@example.com can't log in. Open a high priority ticket and email her."
    })  # fmt: skip
    assert status == 201 and run["status"] == "awaiting_approval", (status, run)
    status, _ = call("POST", f"/approvals/{run['approval']['id']}/approve", token, {})
    assert status == 403, "the requester must not be able to approve their own run"

    manager_email = f"manager-{suffix}@example.com"
    status, _ = call("POST", "/users", token, {
        "email": manager_email, "full_name": "Smoke Manager",
        "password": "Smoke-Manager-Password-1", "role": "manager",
    })  # fmt: skip
    assert status == 201, status
    status, login = call("POST", "/auth/login", body={"email": manager_email, "password": "Smoke-Manager-Password-1"})
    assert status == 200, login
    status, done = call("POST", f"/approvals/{run['approval']['id']}/approve", login["access_token"], {})
    assert status == 200 and done["status"] == "completed", done
    print(f"ok: agent run approved by a manager: {done['summary']}")

    # Phase 6: connect the mock ServiceNow, Salesforce and email services.
    for spec in (
        {"kind": "servicenow", "name": "ServiceNow", "secrets": {"username": "nexus", "password": "servicenow-demo-password"}},
        {"kind": "salesforce", "name": "Salesforce", "secrets": {"client_id": "nexus-client", "client_secret": "salesforce-demo-secret"}},
        {"kind": "email", "name": "Mail", "config": {"sender": "support@example.com"}, "secrets": {"api_key": "email-demo-key"}},
    ):  # fmt: skip
        status, created = call("POST", "/integrations", token, {**spec, "base_url": MOCK_FROM_BACKEND})
        assert status == 201 and created["health_status"] == "ok", (status, created)
    print("ok: connected ServiceNow, Salesforce and email (health checks passed)")

    status, run = call("POST", "/agents/runs", token, {
        "request": "Customer jane@globex.com can't log in. Open a high priority ticket and email her."
    })  # fmt: skip
    status, done = call("POST", f"/approvals/{run['approval']['id']}/approve", login["access_token"], {})
    assert status == 200 and done["status"] == "completed", done
    assert "ServiceNow INC" in done["summary"] and "Sent an email" in done["summary"], done["summary"]
    number = done["steps"][1]["result"]["external_number"]
    print(f"ok: {done['summary']}")

    with urllib.request.urlopen(
        urllib.request.Request(f"{MOCK_FROM_HERE}/_mock/servicenow/incidents/{number}/resolve", method="POST"),
        timeout=10,
    ) as response:
        deliveries = json.loads(response.read())["deliveries"]
    assert deliveries and deliveries[0].get("status") == 200, deliveries
    status, tickets = call("GET", "/tickets", token)
    assert any(t["external_number"] == number and t["status"] == "resolved" for t in tickets), tickets
    print(f"ok: resolving {number} in ServiceNow updated the ticket through a signed webhook")


if __name__ == "__main__":
    main()
