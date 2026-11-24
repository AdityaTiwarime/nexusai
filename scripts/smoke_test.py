"""End-to-end smoke test against a running stack (used by CI after `docker compose up`).

Registers a new organization, uploads a document through the frontend's nginx
proxy, waits for the worker to process it, checks that chunks were created, and
asks a question that must be answered with a citation, plus one that must not.
Standard library only, so it runs on any machine with Python 3.

    python scripts/smoke_test.py [base_url]     # default http://localhost:5173
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173").rstrip("/") + "/api/v1"


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


if __name__ == "__main__":
    main()
