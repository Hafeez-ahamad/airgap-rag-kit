import { useEffect, useRef, useState } from "react";
import {
  chat,
  messages as loadMessages,
  openSource,
  sendFeedback,
  type Citation,
  type Mode,
  type Segment,
} from "./api";
import { Markdown } from "./Markdown";

export type Assistant = "general" | "documents";

interface Turn {
  question: string;
  answer: string;
  status: "streaming" | "done" | "error" | "stopped";
  mode: Mode;
  refused: boolean;
  flagged: boolean;
  citations: Citation[];
  segments: Segment[];
  messageId: string | null;
  rewritten: string | null;
  feedback: string | null;
}

interface Props {
  assistant: Assistant;
  conversationId: string | null;
  onConversation: (id: string | null) => void;
}

const COPY: Record<Assistant, { title: string; empty: string; placeholder: string }> = {
  general: {
    title: "General Assistant",
    empty:
      "Chat with the local model. It does not read your documents in this mode, so answers are " +
      "general knowledge, not citations.",
    placeholder: "Ask anything…",
  },
  documents: {
    title: "Document Assistant",
    empty:
      "Ask about your documents. Answers cite the exact passage they come from, or say that the " +
      "documents do not contain the answer.",
    placeholder: "Ask about your documents…",
  },
};

export function ChatView({ assistant, conversationId, onConversation }: Props) {
  const [strict, setStrict] = useState(true);
  const mode: Mode = assistant === "general" ? "assistant" : strict ? "grounded" : "general";
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [convId, setConvId] = useState<string | null>(conversationId);
  const [selected, setSelected] = useState<Citation | null>(null);
  const busy = turns.some((t) => t.status === "streaming");
  const abort = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!conversationId) return;
    let alive = true;
    void loadMessages(conversationId).then((msgs) => {
      if (!alive) return;
      const loaded: Turn[] = [];
      for (let i = 0; i < msgs.length - 1; i += 2) {
        const q = msgs[i];
        const a = msgs[i + 1];
        if (!q || !a) continue;
        loaded.push({
          question: q.content,
          answer: a.content,
          status: "done",
          mode: a.mode ?? "grounded",
          refused: a.refused,
          flagged: a.flagged,
          citations: a.citations,
          segments: a.segments,
          messageId: a.id,
          rewritten: null,
          feedback: null,
        });
      }
      setTurns(loaded);
    });
    return () => {
      alive = false;
      abort.current?.abort();
    };
  }, [conversationId]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);

  const update = (i: number, patch: Partial<Turn>) =>
    setTurns((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  async function ask() {
    const q = question.trim();
    if (!q || busy) return;
    setQuestion("");
    const index = turns.length;
    setTurns((ts) => [
      ...ts,
      {
        question: q,
        answer: "",
        status: "streaming",
        mode,
        refused: false,
        flagged: false,
        citations: [],
        segments: [],
        messageId: null,
        rewritten: null,
        feedback: null,
      },
    ]);
    abort.current = new AbortController();
    let text = "";
    try {
      for await (const ev of chat(q, mode, convId, abort.current.signal)) {
        if (ev.type === "meta") update(index, { rewritten: ev.rewritten ? ev.query : null });
        else if (ev.type === "token") update(index, { answer: (text += ev.text) });
        else if (ev.type === "retry") update(index, { answer: (text = "") });
        else if (ev.type === "error") update(index, { status: "error", answer: ev.message });
        else if (ev.type === "final") {
          if (ev.conversation_id !== convId) {
            setConvId(ev.conversation_id);
            onConversation(ev.conversation_id);
          }
          update(index, {
            status: "done",
            answer: ev.answer,
            refused: ev.refused,
            flagged: ev.flagged,
            citations: ev.citations,
            segments: ev.segments,
            messageId: ev.message_id,
          });
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        update(index, { status: "stopped" });
      } else {
        update(index, { status: "error", answer: err instanceof Error ? err.message : "failed" });
      }
    } finally {
      inputRef.current?.focus();
    }
  }

  async function rate(i: number, rating: "up" | "down" | "wrong") {
    const t = turns[i];
    if (!t?.messageId) return;
    const comment = rating === "wrong" ? (window.prompt("What is wrong with this answer?") ?? undefined) : undefined;
    try {
      await sendFeedback(t.messageId, rating, comment);
      update(i, { feedback: rating === "up" ? "Thanks!" : "Feedback recorded" });
    } catch {
      update(i, { feedback: "Could not send feedback" });
    }
  }

  const copy = COPY[assistant];
  return (
    <div className={`chat ${selected ? "with-viewer" : ""}`}>
      <header className="chat-header">
        <div>
          <h2>{copy.title}</h2>
          <p className="muted small">
            {assistant === "general"
              ? "No document access · answers are not grounded"
              : strict
                ? "Strict: answers only from your documents, with citations"
                : "Relaxed: uses documents when relevant; other content is labelled"}
          </p>
        </div>
        <div className="chat-header-actions">
          {assistant === "documents" && (
            <label className="toggle">
              <input type="checkbox" checked={!strict} onChange={(e) => setStrict(!e.target.checked)} />
              Allow general knowledge
            </label>
          )}
          <button
            type="button"
            onClick={() => {
              abort.current?.abort();
              setTurns([]);
              setConvId(null);
              onConversation(null);
            }}
          >
            New chat
          </button>
        </div>
      </header>

      <section className="conversation" aria-live="polite">
        {turns.length === 0 && (
          <div className="empty">
            <p>{copy.empty}</p>
          </div>
        )}
        {turns.map((t, i) => (
          <article key={i} className="turn">
            <div className="bubble user">{t.question}</div>
            {t.rewritten && <div className="rewritten muted small">Searched for: {t.rewritten}</div>}
            <div className={`bubble assistant ${t.refused ? "refused" : ""} ${t.status}`}>
              {t.status === "streaming" && !t.answer && <span className="typing" aria-label="thinking" />}
              {t.mode === "general" && t.segments.length > 0 ? (
                <p>
                  {t.segments.map((s, k) => (
                    <span key={k} className={s.grounded ? "" : "general"} title={s.label ?? ""}>
                      {s.text}{" "}
                    </span>
                  ))}
                </p>
              ) : (
                t.answer && <Markdown text={t.answer} />
              )}
              {t.status === "stopped" && <p className="muted small">Stopped.</p>}
            </div>
            {t.segments.some((s) => !s.grounded) && (
              <div className="label small muted">
                Highlighted text is general knowledge, not from your documents.
              </div>
            )}
            {t.flagged && (
              <div className="callout warn small" role="status">
                Some citations could not be verified against the sources and are marked unverified.
              </div>
            )}
            {t.citations.length > 0 && (
              <ul className="citations">
                {t.citations.map((c, k) => (
                  <li key={k}>
                    <button
                      type="button"
                      className={`citation ${c.valid ? "" : "invalid"}`}
                      onClick={() => setSelected(c)}
                      aria-label={`Source ${c.n}: ${c.doc_name ?? "unknown"}`}
                    >
                      <span className="cite-n">{c.n}</span>
                      <span>
                        {c.doc_name ?? "unknown"}
                        {c.page ? `, p. ${c.page}` : ""}
                        {c.section ? ` · ${c.section}` : ""}
                      </span>
                      {!c.valid && <span className="badge bad">unverified</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {(t.status === "done" || t.status === "error") && (
              <div className="turn-actions">
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => void navigator.clipboard?.writeText(t.answer)}
                >
                  Copy
                </button>
                {t.messageId && (
                  <>
                    <button type="button" className="ghost small" aria-label="Helpful" onClick={() => rate(i, "up")}>
                      👍
                    </button>
                    <button type="button" className="ghost small" aria-label="Not helpful" onClick={() => rate(i, "down")}>
                      👎
                    </button>
                    <button type="button" className="ghost small" onClick={() => rate(i, "wrong")}>
                      Report wrong answer
                    </button>
                  </>
                )}
                {t.feedback && <span className="muted small">{t.feedback}</span>}
              </div>
            )}
          </article>
        ))}
        <div ref={endRef} />
      </section>

      {selected && (
        <aside className="citation-viewer" aria-label="Source passage">
          <header>
            <strong>{selected.doc_name}</strong>
            <button type="button" className="ghost" onClick={() => setSelected(null)} aria-label="Close source">
              ✕
            </button>
          </header>
          <p className="muted small">
            {selected.page ? `Page ${selected.page}` : "No page number"}
            {selected.section ? ` · ${selected.section}` : ""}
            {selected.version ? ` · version ${selected.version}` : ""}
          </p>
          {!selected.valid && (
            <div className="callout bad small">
              Not verified: {selected.reason}. Do not rely on this quote.
            </div>
          )}
          <blockquote>{selected.quote}</blockquote>
          <button
            type="button"
            onClick={() => {
              openSource(selected).catch(() => window.alert("The source document is not available."));
            }}
          >
            Open source document
          </button>
        </aside>
      )}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <textarea
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask();
            }
          }}
          placeholder={copy.placeholder}
          maxLength={4000}
          rows={1}
          aria-label="Message"
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="primary" disabled={!question.trim()}>
            Send
          </button>
        )}
      </form>
      <p className="composer-hint muted small">Enter to send · Shift+Enter for a new line · runs on this computer</p>
    </div>
  );
}
