import { useEffect, useState } from "react";
import { conversations, type Conversation } from "./api";

export function HistoryView({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    conversations()
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "failed to load"));
  }, []);

  if (error) return <div className="warning">{error}</div>;
  if (!items) return <p>Loading…</p>;
  if (items.length === 0) return <p className="hint">No conversations yet.</p>;
  return (
    <ul className="history">
      {items.map((c) => (
        <li key={c.id}>
          <button onClick={() => onOpen(c.id)}>
            {c.title} <span className="when">{new Date(c.updated_at).toLocaleString()}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
