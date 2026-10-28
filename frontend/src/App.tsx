import { useEffect, useState } from "react";
import { getDevUser, health, setDevUser, type Health } from "./api";
import { ChatView } from "./ChatView";
import { DocumentsView } from "./DocumentsView";
import { HistoryView } from "./HistoryView";

type Tab = "chat" | "documents" | "history";

export function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [status, setStatus] = useState<Health | null>(null);
  const [user, setUser] = useState(getDevUser());
  const [openConversation, setOpenConversation] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      health()
        .then((h) => alive && setStatus(h))
        .catch(() => alive && setStatus({ status: "degraded", components: { api: "down" } }));
    void poll();
    const id = window.setInterval(poll, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const down = status
    ? Object.entries(status.components)
        .filter(([, v]) => v !== "ok")
        .map(([k]) => k)
    : [];

  return (
    <div className="app">
      <header>
        <h1>AirGap RAG Kit</h1>
        <nav>
          {(["chat", "documents", "history"] as const).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t[0]?.toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
        <label className="dev-user" title="Development only: replaced by login in Phase 3">
          dev user
          <input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            onBlur={() => setDevUser(user.trim() || "dev")}
          />
        </label>
      </header>
      {status?.status === "degraded" && (
        <div className="banner" role="alert">
          Degraded mode: {down.join(", ") || "a component"} unavailable. Answers may fail until it
          recovers.
        </div>
      )}
      <main>
        {tab === "chat" && (
          <ChatView
            key={openConversation ?? "new"}
            conversationId={openConversation}
            onNewConversation={() => setOpenConversation(null)}
          />
        )}
        {tab === "documents" && <DocumentsView />}
        {tab === "history" && (
          <HistoryView
            onOpen={(id) => {
              setOpenConversation(id);
              setTab("chat");
            }}
          />
        )}
      </main>
    </div>
  );
}
