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

interface Turn {
  question: string;
  answer: string;
  status: "streaming" | "done" | "error";
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
  conversationId: string | null;
  onNewConversation: () => void;
}

export function ChatView({ conversationId, onNewConversation }: Props) {
  const [mode, setMode] = useState<Mode>("grounded");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [convId, setConvId] = useState<string | null>(conversationId);
  const [selected, setSelected] = useState<Citation | null>(null);
  const busy = turns.some((t) => t.status === "streaming");
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!conversationId) return;
    void loadMessages(conversationId).then((msgs) => {
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
    return () => abort.current?.abort();
  }, [conversationId]);

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
          setConvId(ev.conversation_id);
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
      update(index, { status: "error", answer: err instanceof Error ? err.message : "failed" });
    }
  }

  async function rate(i: number, rating: "up" | "down" | "wrong") {
    const t = turns[i];
    if (!t?.messageId) return;
    const comment = rating === "wrong" ? (window.prompt("What is wrong?") ?? undefined) : undefined;
    try {
      await sendFeedback(t.messageId, rating, comment);
      update(i, { feedback: rating });
    } catch {
      update(i, { feedback: "failed to send" });
    }
  }

  return (
    <div className="chat">
      <section className="conversation">
        {turns.length === 0 && <p className="hint">Ask a question about your documents.</p>}
        {turns.map((t, i) => (
          <article key={i} className="turn">
            <div className="question">{t.question}</div>
            {t.rewritten && <div className="rewritten">searched for: {t.rewritten}</div>}
            <div className={`answer ${t.refused ? "refused" : ""} ${t.status}`}>
              {t.mode === "general" && t.segments.length > 0 ? (
                t.segments.map((s, k) => (
                  <span key={k} className={s.grounded ? "" : "general"} title={s.label ?? ""}>
                    {s.text}{" "}
                  </span>
                ))
              ) : (
                <span>{t.answer || "…"}</span>
              )}
            </div>
            {t.segments.some((s) => !s.grounded) && (
              <div className="label">Highlighted text: general knowledge, not from your documents.</div>
            )}
            {t.flagged && (
              <div className="warning">
                Warning: some citations could not be verified against the sources.
              </div>
            )}
            {t.citations.length > 0 && (
              <ul className="citations">
                {t.citations.map((c, k) => (
                  <li key={k} className={c.valid ? "" : "invalid"}>
                    <button onClick={() => setSelected(c)}>
                      [{c.n}] {c.doc_name ?? "unknown"}
                      {c.page ? `, p. ${c.page}` : ""}
                      {c.section ? ` · ${c.section}` : ""}
                      {!c.valid && ` (unverified: ${c.reason})`}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {t.status === "done" && t.messageId && (
              <div className="feedback">
                <button aria-label="helpful" onClick={() => rate(i, "up")}>👍</button>
                <button aria-label="not helpful" onClick={() => rate(i, "down")}>👎</button>
                <button onClick={() => rate(i, "wrong")}>Report wrong answer</button>
                {t.feedback && <span className="sent">feedback: {t.feedback}</span>}
              </div>
            )}
          </article>
        ))}
      </section>
      {selected && (
        <aside className="citation-viewer">
          <header>
            <strong>{selected.doc_name}</strong>
            <button onClick={() => setSelected(null)} aria-label="close">✕</button>
          </header>
          <p className="meta">
            {selected.page ? `Page ${selected.page}` : "No page"}
            {selected.section ? ` · ${selected.section}` : ""}
            {selected.version ? ` · version ${selected.version}` : ""}
          </p>
          <blockquote>{selected.quote}</blockquote>
          <button
            onClick={() => {
              openSource(selected).catch(() => window.alert("Source is not available"));
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
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-label="mode">
          <option value="grounded">Grounded only (cite or refuse)</option>
          <option value="general">General assistant</option>
        </select>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question…"
          maxLength={4000}
        />
        <button type="submit" disabled={busy || !question.trim()}>
          Ask
        </button>
        <button
          type="button"
          onClick={() => {
            setTurns([]);
            setConvId(null);
            onNewConversation();
          }}
        >
          New chat
        </button>
      </form>
    </div>
  );
}
