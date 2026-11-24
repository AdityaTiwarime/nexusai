import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, type ChatMessage, type ConversationDetail } from "../api/client";
import { AnswerText, bestExcerpt, chunkLink, pagesLabel } from "../components/rag";
import { Button, ErrorAlert } from "../components/ui";

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function Sources({ message }: { message: ChatMessage }) {
  if (message.citations.length === 0) return null;
  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Sources</p>
      {message.citations.map((c) =>
        c.available && c.document_id && c.chunk_index !== null ? (
          <Link
            key={c.number}
            to={chunkLink(c.document_id, c.chunk_index)}
            className="block rounded-lg bg-slate-50 p-2.5 text-sm ring-1 ring-inset ring-slate-200 hover:bg-slate-100"
          >
            <span className="mr-2 inline-flex h-5 min-w-5 items-center justify-center rounded bg-brand-50 px-1 text-[11px] font-semibold text-brand-700">
              {c.number}
            </span>
            <span className="font-medium text-slate-800">{c.document_title}</span>
            {pagesLabel(c.page_start, c.page_end) && (
              <span className="ml-2 text-xs text-slate-500">{pagesLabel(c.page_start, c.page_end)}</span>
            )}
            {c.snippet && (
              // The excerpt around what the answer quoted, not the start of the chunk.
              <span className="mt-1 block text-xs text-slate-600">{bestExcerpt(c.snippet, message.content, 220)}</span>
            )}
          </Link>
        ) : (
          <div key={c.number} className="rounded-lg bg-slate-50 p-2.5 text-sm text-slate-400 ring-1 ring-inset ring-slate-200">
            [{c.number}] Source no longer available to you
          </div>
        ),
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2.5 text-sm text-white shadow-sm">
          {message.content}
        </div>
      </div>
    );
  }
  const noEvidence = message.status === "no_evidence";
  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] rounded-2xl rounded-bl-sm px-4 py-3 text-sm shadow-sm ring-1 ring-inset ${
          noEvidence ? "bg-amber-50 text-amber-900 ring-amber-600/20" : "bg-white text-slate-800 ring-slate-200"
        }`}
      >
        {noEvidence && <p className="mb-1 text-xs font-semibold uppercase tracking-wide">No supporting evidence</p>}
        <AnswerText text={message.content} citations={message.citations} />
        <Sources message={message} />
        <p className="mt-2 text-[11px] text-slate-400">
          {message.model === "extractive" ? "Quoted from documents (offline mode)" : message.model}
          {message.latency_ms !== null &&
            ` · ${message.latency_ms < 1000 ? `${message.latency_ms} ms` : `${(message.latency_ms / 1000).toFixed(1)} s`}`}
          {message.input_tokens !== null &&
            ` · ${message.input_tokens.toLocaleString()} + ${(message.output_tokens ?? 0).toLocaleString()} tokens`}
        </p>
      </div>
    </div>
  );
}

function ConversationList({ activeId }: { activeId?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.listConversations });
  const remove = useMutation({
    mutationFn: api.deleteConversation,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (id === activeId) navigate("/chat", { replace: true });
    },
  });

  return (
    <aside className="flex w-full flex-col rounded-xl border border-slate-200 bg-white shadow-sm lg:w-72">
      <div className="border-b border-slate-100 p-3">
        <Button className="w-full" onClick={() => navigate("/chat")}>
          New chat
        </Button>
      </div>
      <nav aria-label="Conversations" className="max-h-[60vh] flex-1 overflow-y-auto p-2">
        {conversations.data?.length === 0 && <p className="p-2 text-sm text-slate-500">No conversations yet.</p>}
        {conversations.data?.map((c) => (
          <div
            key={c.id}
            className={`group flex items-center gap-1 rounded-md ${c.id === activeId ? "bg-brand-50" : "hover:bg-slate-50"}`}
          >
            <Link
              to={`/chat/${c.id}`}
              className={`min-w-0 flex-1 truncate px-2.5 py-2 text-sm ${
                c.id === activeId ? "font-medium text-brand-700" : "text-slate-700"
              }`}
            >
              {c.title}
            </Link>
            <button
              aria-label={`Delete conversation ${c.title}`}
              onClick={() => remove.mutate(c.id)}
              className="mr-1 rounded px-1.5 py-1 text-xs text-slate-400 opacity-0 hover:bg-rose-50 hover:text-rose-600 focus:opacity-100 group-hover:opacity-100"
            >
              ✕
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}

export function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const info = useQuery({ queryKey: ["system", "info"], queryFn: api.systemInfo });
  const conversation = useQuery({
    queryKey: ["conversation", id],
    queryFn: () => api.getConversation(id as string),
    enabled: Boolean(id),
  });

  const ask = useMutation({
    mutationFn: (text: string) => api.ask(text, id),
    onMutate: (text) => setPending(text),
    onSuccess: (result) => {
      // Show the new messages immediately; the refetch below confirms them.
      queryClient.setQueryData<ConversationDetail>(["conversation", result.conversation_id], (old) =>
        old
          ? { ...old, messages: [...old.messages, result.question, result.answer] }
          : {
              id: result.conversation_id,
              title: result.question.content.slice(0, 80),
              created_at: result.question.created_at,
              updated_at: result.answer.created_at,
              messages: [result.question, result.answer],
            },
      );
      queryClient.invalidateQueries({ queryKey: ["conversation", result.conversation_id] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setQuestion("");
      if (result.conversation_id !== id) navigate(`/chat/${result.conversation_id}`);
    },
    onSettled: () => setPending(null),
  });

  const messages = id ? (conversation.data?.messages ?? []) : [];
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  const submit = () => {
    const text = question.trim();
    if (text && !ask.isPending) ask.mutate(text);
  };
  const offline = info.data?.answer_provider !== "openai";

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 lg:flex-row">
      <ConversationList activeId={id} />

      <section className="flex min-h-[70vh] min-w-0 flex-1 flex-col rounded-xl border border-slate-200 bg-slate-50/60 shadow-sm">
        <header className="border-b border-slate-200 bg-white px-5 py-3">
          <h1 className="text-lg font-semibold tracking-tight">{conversation.data?.title ?? "AI Chat"}</h1>
          <p className="text-xs text-slate-500">
            Answers come only from documents you can access, with numbered sources.
            {offline ? " Offline mode: answers quote the documents directly." : ` Model: ${info.data?.chat_model}.`}
          </p>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {id && conversation.isError && (
            <ErrorAlert
              message={
                conversation.error instanceof ApiError && conversation.error.status === 404
                  ? "Conversation not found."
                  : errorText(conversation.error)
              }
            />
          )}
          {!id && !pending && (
            <div className="mx-auto mt-16 max-w-md text-center">
              <p className="text-base font-medium text-slate-800">Ask a question about your documents</p>
              <p className="mt-1 text-sm text-slate-500">
                For example: “How many days of paid leave do employees get?” If the documents don't answer it,
                NexusAI will say so instead of guessing.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {pending && (
            <>
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600/80 px-4 py-2.5 text-sm text-white">
                  {pending}
                </div>
              </div>
              <p className="text-sm text-slate-500" role="status">
                Searching your documents…
              </p>
            </>
          )}
          <div ref={bottom} />
        </div>

        <form
          className="border-t border-slate-200 bg-white p-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <ErrorAlert message={errorText(ask.error)} />
          <div className="mt-1 flex items-end gap-2">
            <textarea
              aria-label="Your question"
              rows={2}
              maxLength={2000}
              placeholder="Ask about your documents…"
              className="block w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <Button type="submit" disabled={!question.trim() || ask.isPending}>
              {ask.isPending ? "Asking…" : "Ask"}
            </Button>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">Enter to send · Shift+Enter for a new line</p>
        </form>
      </section>
    </div>
  );
}
