import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { ChatMessage, ConversationDetail } from "../api/client";
import { jsonResponse, mockApi, renderApp, signedInRoutes } from "./helpers";

const question: ChatMessage = {
  id: "m-1",
  role: "user",
  content: "How many days of paid leave do employees get?",
  status: null,
  citations: [],
  model: null,
  input_tokens: null,
  output_tokens: null,
  latency_ms: null,
  created_at: "2026-11-24T10:00:00Z",
};

const answer: ChatMessage = {
  id: "m-2",
  role: "assistant",
  content: "From the documents: Full-time employees receive 24 days of paid leave per year. [1]",
  status: "answered",
  citations: [
    {
      number: 1,
      available: true,
      document_id: "d-1",
      document_title: "Employee Handbook",
      chunk_index: 3,
      page_start: 2,
      page_end: 2,
      snippet: "Full-time employees receive 24 days of paid leave per year.",
    },
  ],
  model: "extractive",
  input_tokens: null,
  output_tokens: null,
  latency_ms: 140,
  created_at: "2026-11-24T10:00:01Z",
};

const conversation: ConversationDetail = {
  id: "c-1",
  title: "How many days of paid leave do employees get?",
  created_at: "2026-11-24T10:00:00Z",
  updated_at: "2026-11-24T10:00:01Z",
  messages: [question, answer],
};

describe("AI Chat", () => {
  it("asks a question, shows the cited answer and opens the new conversation", async () => {
    let sent: unknown = null;
    mockApi({
      ...signedInRoutes("employee"),
      "GET /conversations": jsonResponse([]),
      "POST /chat/ask": (init) => {
        sent = JSON.parse(String(init?.body));
        return jsonResponse({ conversation_id: "c-1", question, answer });
      },
      "GET /conversations/c-1": jsonResponse(conversation),
    });
    renderApp("/chat");

    expect(await screen.findByText("Ask a question about your documents")).toBeInTheDocument();
    expect(await screen.findByText(/Offline mode: answers quote the documents directly/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: question.content } });
    fireEvent.keyDown(screen.getByLabelText("Your question"), { key: "Enter" });

    expect(await screen.findByText("Sources")).toBeInTheDocument();
    expect(screen.getByText(/From the documents: Full-time employees receive 24 days/)).toBeInTheDocument();
    expect(sent).toEqual({ question: question.content });
    // The [1] marker and the source card both link to the exact chunk.
    const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href") === "/documents/d-1?chunk=3");
    expect(links.length).toBe(2);
    expect(screen.getByText("p. 2")).toBeInTheDocument();
    expect(screen.getByText(/Quoted from documents \(offline mode\) · 140 ms/)).toBeInTheDocument();
  });

  it("sends follow-up questions in the same conversation", async () => {
    let sent: unknown = null;
    mockApi({
      ...signedInRoutes("employee"),
      "GET /conversations": jsonResponse([conversation]),
      "GET /conversations/c-1": jsonResponse(conversation),
      "POST /chat/ask": (init) => {
        sent = JSON.parse(String(init?.body));
        return jsonResponse({ conversation_id: "c-1", question, answer });
      },
    });
    renderApp("/chat/c-1");
    const list = await screen.findByRole("navigation", { name: "Conversations" });
    expect(await within(list).findByRole("link", { name: conversation.title })).toBeInTheDocument();

    fireEvent.change(await screen.findByLabelText("Your question"), { target: { value: "And sick leave?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(sent).toEqual({ question: "And sick leave?", conversation_id: "c-1" }));
  });

  it("marks answers without evidence clearly", async () => {
    const refusal: ChatMessage = {
      ...answer,
      status: "no_evidence",
      content: "I couldn't find this in the documents you have access to, so I won't guess.",
      citations: [],
    };
    mockApi({
      ...signedInRoutes("employee"),
      "GET /conversations": jsonResponse([conversation]),
      "GET /conversations/c-1": jsonResponse({ ...conversation, messages: [question, refusal] }),
    });
    renderApp("/chat/c-1");
    expect(await screen.findByText("No supporting evidence")).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
  });

  it("shows sources that were restricted later as unavailable, without links", async () => {
    const hidden: ChatMessage = {
      ...answer,
      citations: [{ number: 1, available: false, document_id: null, document_title: null, chunk_index: null, page_start: null, page_end: null, snippet: null }],
    };
    mockApi({
      ...signedInRoutes("employee"),
      "GET /conversations": jsonResponse([conversation]),
      "GET /conversations/c-1": jsonResponse({ ...conversation, messages: [question, hidden] }),
    });
    renderApp("/chat/c-1");
    expect(await screen.findByText(/Source no longer available to you/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "1" })).not.toBeInTheDocument();
  });

  it("shows the API's message when the AI provider is unavailable", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "GET /conversations": jsonResponse([]),
      "POST /chat/ask": jsonResponse({ detail: "The AI service is temporarily unavailable. Please try again." }, 503),
    });
    renderApp("/chat");
    fireEvent.change(await screen.findByLabelText("Your question"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
  });
});

describe("Search", () => {
  it("shows ranked passages with highlights and links to the chunk", async () => {
    let sent: unknown = null;
    mockApi({
      ...signedInRoutes("employee"),
      "POST /search": (init) => {
        sent = JSON.parse(String(init?.body));
        return jsonResponse({
          query: "password rotation",
          candidates: 7,
          results: [
            {
              document_id: "d-2",
              document_title: "IT Security Policy",
              chunk_index: 0,
              page_start: null,
              page_end: null,
              content: "Passwords are rotated every 180 days.",
              score: 0.87,
              similarity: 0.42,
              keyword_match: true,
            },
          ],
        });
      },
    });
    renderApp("/search");
    fireEvent.change(await screen.findByLabelText("Search query"), { target: { value: "password rotation" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const results = await screen.findByRole("region", { name: "Results" });
    expect(sent).toEqual({ query: "password rotation", limit: 10 });
    expect(within(results).getByText("1 passage (from 7 candidates)")).toBeInTheDocument();
    expect(within(results).getByText("keyword match")).toBeInTheDocument();
    expect(within(results).getByText("87%")).toBeInTheDocument();
    expect(within(results).getByRole("link", { name: /IT Security Policy/ })).toHaveAttribute("href", "/documents/d-2?chunk=0");
    // "password" is highlighted inside "Passwords".
    expect(within(results).getByText("Password", { selector: "mark" })).toBeInTheDocument();
  });

  it("says when nothing matches", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "POST /search": jsonResponse({ query: "x", candidates: 0, results: [] }),
    });
    renderApp("/search");
    fireEvent.change(await screen.findByLabelText("Search query"), { target: { value: "quantum" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("No matching passages in documents you can access.")).toBeInTheDocument();
  });
});
