// Thin client for the AirGap API. Every request goes to the same origin under /api.

export type Mode = "grounded" | "general";

export interface Citation {
  n: number;
  quote: string;
  valid: boolean;
  reason: string;
  doc_id: string | null;
  doc_name: string | null;
  version: number | null;
  page: number | null;
  section: string | null;
  chunk_id: string | null;
}

export interface Segment {
  text: string;
  grounded: boolean;
  label: string | null;
}

export interface FinalEvent {
  type: "final";
  conversation_id: string;
  message_id: string;
  answer: string;
  mode: Mode;
  refused: boolean;
  flagged: boolean;
  model_id: string | null;
  citations: Citation[];
  segments: Segment[];
}

export type ChatEvent =
  | { type: "meta"; query: string; rewritten: boolean }
  | { type: "token"; text: string }
  | { type: "retry"; reason: string }
  | { type: "error"; message: string }
  | FinalEvent;

export interface DocumentRow {
  id: string;
  name: string;
  file_type: string;
  version: number;
  status: "queued" | "parsing" | "embedding" | "ready" | "failed";
  error: string | null;
  size_bytes: number;
  chunk_count: number;
  ocr_pages: number[];
  updated_at: string;
}

export interface UploadItem {
  filename: string;
  outcome: "queued" | "new_version" | "unchanged" | "duplicate" | "rejected";
  doc_id: string | null;
  version: number | null;
  message: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode: Mode | null;
  refused: boolean;
  flagged: boolean;
  citations: Citation[];
  segments: Segment[];
}

export interface Health {
  status: "ok" | "degraded";
  components: Record<string, string>;
}

const BASE = "/api";
const DEV_USER_KEY = "airgap.devUser";

export function getDevUser(): string {
  try {
    return localStorage.getItem(DEV_USER_KEY) ?? "dev";
  } catch {
    return "dev";
  }
}

export function setDevUser(user: string): void {
  try {
    localStorage.setItem(DEV_USER_KEY, user);
  } catch {
    /* storage unavailable: keep the default */
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  // Phase 1 dev stub only (ADR-0008); replaced by session auth in Phase 3.
  return { "X-Dev-User": getDevUser(), ...extra };
}

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export async function health(): Promise<Health> {
  return json(await fetch(`${BASE}/health`));
}

export async function listDocuments(): Promise<DocumentRow[]> {
  return json(await fetch(`${BASE}/documents`, { headers: headers() }));
}

export async function uploadFiles(files: File[]): Promise<UploadItem[]> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
    form.append("paths", file.webkitRelativePath || file.name);
  }
  const resp = await fetch(`${BASE}/documents`, { method: "POST", body: form, headers: headers() });
  return (await json<{ results: UploadItem[] }>(resp)).results;
}

export async function deleteDocument(id: string): Promise<void> {
  const resp = await fetch(`${BASE}/documents/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!resp.ok) throw new Error(`delete failed (${resp.status})`);
}

export function sourceUrl(c: Citation): string {
  const page = c.page ? `#page=${c.page}` : "";
  return `${BASE}/documents/${encodeURIComponent(c.doc_id ?? "")}/file${page}`;
}

export async function openSource(c: Citation): Promise<void> {
  // Fetch with the dev header, then open as a blob URL (a plain link cannot send headers).
  const resp = await fetch(sourceUrl(c).split("#")[0] ?? "", { headers: headers() });
  if (!resp.ok) throw new Error("source not available");
  const url = URL.createObjectURL(await resp.blob());
  window.open(url + (c.page ? `#page=${c.page}` : ""), "_blank", "noopener");
}

export async function conversations(): Promise<Conversation[]> {
  return json(await fetch(`${BASE}/chat/history`, { headers: headers() }));
}

export async function messages(conversationId: string): Promise<StoredMessage[]> {
  const q = new URLSearchParams({ conversation_id: conversationId });
  return json(await fetch(`${BASE}/chat/history?${q}`, { headers: headers() }));
}

export async function sendFeedback(
  messageId: string,
  rating: "up" | "down" | "wrong",
  comment?: string,
): Promise<void> {
  const resp = await fetch(`${BASE}/feedback`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message_id: messageId, rating, comment }),
  });
  if (!resp.ok) throw new Error(`feedback failed (${resp.status})`);
}

export async function* chat(
  question: string,
  mode: Mode,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question, mode, conversation_id: conversationId }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    await json(resp);
    return;
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6)) as ChatEvent;
    }
  }
}
