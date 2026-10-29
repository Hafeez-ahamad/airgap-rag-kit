// Client for the AirGap API. Same origin, under /api. Session cookie + CSRF header (ADR-0010).

export type Mode = "assistant" | "grounded" | "general";
export type Role = "viewer" | "editor" | "admin";

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
  | { type: "error"; message: string; code?: string }
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
  acl_groups: string[];
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
  setup_complete?: boolean | null;
  version?: string;
}

export interface Me {
  id: string;
  username: string;
  role: Role;
  groups: string[];
  dev_auth: boolean;
}

// ---- capacity / setup types (mirror backend pydantic models) ----

export interface Note {
  code: string;
  message: string;
}

export interface Estimate {
  weights_bytes: number;
  kv_per_session_bytes: number;
  ram_needed_bytes: number;
  vram_needed_bytes: number;
  disk_needed_bytes: number;
  gpu_layers_fraction: number;
}

export interface ModelEvaluation {
  model_id: string;
  display_name: string;
  params_b: number;
  quantization: string;
  licence: string;
  download_bytes: number;
  eligible: boolean;
  fits_now: boolean | null;
  placement: "gpu" | "unified" | "partial_offload" | "cpu" | null;
  max_sessions: number;
  estimate: Estimate | null;
  rejections: Note[];
  warnings: Note[];
  ranked: boolean;
}

export interface GpuInfo {
  vendor: string;
  name: string;
  vram_total_bytes: number | null;
  vram_free_bytes: number | null;
  driver_version: string | null;
  unified_memory: boolean;
}

export interface RuntimeInfo {
  name: string;
  installed: boolean | null;
  version: string | null;
  reachable: boolean | null;
  detail: string | null;
}

export interface HardwareReport {
  source: "real" | "simulated";
  profile_name: string | null;
  os: string;
  os_version: string | null;
  arch: string;
  cpu_model: string | null;
  cpu_cores: number | null;
  cpu_threads: number | null;
  cpu_flags: string[] | null;
  ram_total_bytes: number | null;
  ram_available_bytes: number | null;
  disk_free_bytes: number | null;
  gpus: GpuInfo[];
  gpu_probe_ok: boolean;
  runtimes: RuntimeInfo[];
  limitations: string[];
}

export interface MemoryBudget {
  ram_total_bytes: number | null;
  ram_available_bytes: number | null;
  os_reserve_bytes: number;
  services_bytes: number;
  retrieval_bytes: number;
  llm_ram_budget_bytes: number;
  llm_ram_budget_now_bytes: number | null;
  gpu_name: string | null;
  gpu_usable_bytes: number;
  headroom_fraction: number;
}

export interface RuntimeSettings {
  runtime: string;
  model_tag: string;
  num_ctx: number;
  num_parallel: number;
  ingestion_batch_size: number;
  ingestion_workers: number;
  kv_cache_type: string;
}

export interface Assessment {
  source: "real" | "simulated";
  simulated: boolean;
  catalog_version: string;
  report: HardwareReport;
  deployment: "native" | "compose";
  tier: string;
  document_assistant_available: boolean;
  budget: MemoryBudget;
  recommended_model_id: string | null;
  settings: RuntimeSettings | null;
  evaluations: ModelEvaluation[];
  warnings: Note[];
  assumptions: string[];
}

export interface Check {
  id: string;
  title: string;
  required: boolean;
  status: "ok" | "missing" | "unhealthy" | "outdated" | "unknown" | "not_required";
  detail: string | null;
  actions: { kind: string; title: string; instructions: string; url: string | null }[];
}

export interface PlanStep {
  id: string;
  title: string;
  kind: string;
  bytes: number | null;
  needs_network: boolean;
  needs_user: boolean;
  detail: string | null;
}

export interface InstallPlan {
  ready: boolean;
  blocking: string[];
  model_ids: string[];
  steps: PlanStep[];
  total_download_bytes: number;
  warnings: Note[];
}

export type StepStatus = "pending" | "running" | "needs_action" | "failed" | "verified";

export interface JobSnapshot {
  id: string;
  status: string;
  error: string | null;
  items: Record<
    string,
    { status: string; detail?: string; completed?: number | null; total?: number | null; seconds?: number }
  >;
}

export interface SetupState {
  steps: Record<string, { status: StepStatus; detail: string | null }>;
  assessment: Assessment | null;
  active_model_id: string | null;
  complete: boolean;
  job: JobSnapshot | null;
}

export interface SimulatedSpec {
  id?: string;
  label?: string;
  description?: string;
  os: string;
  arch: string;
  cpu_model?: string | null;
  cpu_cores?: number | null;
  cpu_threads?: number | null;
  cpu_flags?: string[] | null;
  ram_total_gib: number | null;
  ram_available_gib: number | null;
  disk_free_gib: number | null;
  gpu_probe_ok?: boolean;
  gpus: {
    vendor: string;
    name: string;
    vram_total_gib?: number | null;
    vram_free_gib?: number | null;
    driver_version?: string | null;
    unified_memory?: boolean;
  }[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

const BASE = "/api";
const DEV_USER_KEY = "airgap.devUser";
let devAuth = false;

export function setDevAuth(on: boolean): void {
  devAuth = on;
}

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

function csrfToken(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)airgap_csrf=([^;]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : null;
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const csrf = csrfToken();
  if (csrf) h["X-CSRF-Token"] = csrf;
  if (devAuth) h["X-Dev-User"] = getDevUser();
  return h;
}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let message = `Request failed (${resp.status})`;
    let detail: unknown;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      detail = body.detail;
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail && typeof body.detail === "object" && "message" in body.detail)
        message = String((body.detail as { message: unknown }).message);
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`${BASE}${path}`, { headers: headers(), credentials: "same-origin" }));
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await fetch(`${BASE}${path}`, {
      method,
      credentials: "same-origin",
      headers: headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

// ---- auth ----
export const authStatus = () => get<{ dev_auth: boolean; needs_bootstrap: boolean }>("/auth/status");
export const me = () => get<Me>("/auth/me");
export const login = (username: string, password: string) =>
  send<{ username: string }>("POST", "/auth/login", { username, password });
export const bootstrap = (username: string, password: string) =>
  send<{ username: string }>("POST", "/auth/bootstrap", { username, password });
export const logout = () => send<undefined>("POST", "/auth/logout");

// ---- health ----
export const health = () => get<Health>("/health");

// ---- documents ----
export const listDocuments = () => get<DocumentRow[]>("/documents");

export async function uploadFiles(files: File[], groups?: string[]): Promise<UploadItem[]> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
    form.append("paths", file.webkitRelativePath || file.name);
  }
  for (const g of groups ?? []) form.append("groups", g);
  const resp = await fetch(`${BASE}/documents`, {
    method: "POST",
    body: form,
    headers: headers(),
    credentials: "same-origin",
  });
  return (await parse<{ results: UploadItem[] }>(resp)).results;
}

export const deleteDocument = (id: string) =>
  send<undefined>("DELETE", `/documents/${encodeURIComponent(id)}`);
export const setDocumentAcl = (id: string, groups: string[]) =>
  send<string[]>("PATCH", `/documents/${encodeURIComponent(id)}/acl`, { groups });

export async function openSource(c: Citation): Promise<void> {
  const resp = await fetch(`${BASE}/documents/${encodeURIComponent(c.doc_id ?? "")}/file`, {
    headers: headers(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error("source not available");
  const url = URL.createObjectURL(await resp.blob());
  window.open(url + (c.page ? `#page=${c.page}` : ""), "_blank", "noopener");
}

// ---- chat ----
export const conversations = () => get<Conversation[]>("/chat/history");
export const messages = (id: string) =>
  get<StoredMessage[]>(`/chat/history?${new URLSearchParams({ conversation_id: id })}`);
export const sendFeedback = (messageId: string, rating: "up" | "down" | "wrong", comment?: string) =>
  send<{ id: string }>("POST", "/feedback", { message_id: messageId, rating, comment });

export async function* chat(
  question: string,
  mode: Mode,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`${BASE}/chat`, {
    method: "POST",
    credentials: "same-origin",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question, mode, conversation_id: conversationId }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    await parse(resp);
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

// ---- setup (real hardware) ----
export const setupState = () => get<SetupState>("/setup/state");
export const scan = () => send<Assessment>("POST", "/setup/scan", { consent: true });
export const prerequisites = () => get<Check[]>("/setup/prerequisites");
export const plan = (model_ids: string[]) => send<InstallPlan>("POST", "/setup/plan", { model_ids });
export const install = (model_ids: string[]) =>
  send<JobSnapshot>("POST", "/setup/install", { model_ids });
export const cancelInstall = () => send<{ cancelled: boolean }>("POST", "/setup/install/cancel");
export const realReport = () => get<Record<string, unknown>>("/setup/report");

// ---- Compatibility Lab (simulated hardware) ----
export interface LabProfiles {
  banner: string;
  profiles: (SimulatedSpec & { id: string; label: string; description: string })[];
}
export const labProfiles = () => get<LabProfiles>("/admin/compat/profiles");
export type LabInput = { profile_id: string } | { spec: SimulatedSpec };
export const labAssess = (input: LabInput, context_tokens?: number) =>
  send<Assessment>("POST", "/admin/compat/assess", {
    ...input,
    options: context_tokens ? { context_tokens } : {},
  });

export async function labReport(input: LabInput, format: "json" | "markdown"): Promise<string> {
  const resp = await fetch(`${BASE}/admin/compat/report?format=${format}`, {
    method: "POST",
    credentials: "same-origin",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!resp.ok) await parse(resp);
  return format === "json" ? JSON.stringify(await resp.json(), null, 2) : await resp.text();
}

// ---- admin ----
export interface UserRow {
  id: string;
  username: string;
  role: Role;
  groups: string[];
  disabled: boolean;
}
export interface CatalogModel {
  id: string;
  display_name: string;
  params_b: number;
  quantization: string;
  licence: string;
  download_bytes: number;
  tag: string;
  installed: boolean;
  active: boolean;
  fit: "fits" | "does_not_fit" | null;
  placement: string | null;
  reasons: string[];
  quality_rank: number | null;
  notes: string[];
}
export const users = () => get<UserRow[]>("/admin/users");
export const createUser = (u: { username: string; password: string; role: Role; groups: string[] }) =>
  send<UserRow>("POST", "/admin/users", u);
export const updateUser = (id: string, patch: Partial<Omit<UserRow, "id" | "username">>) =>
  send<UserRow>("PATCH", `/admin/users/${encodeURIComponent(id)}`, patch);
export const audit = (q: { actor?: string; action?: string; limit?: number }) => {
  const p = new URLSearchParams();
  if (q.actor) p.set("actor", q.actor);
  if (q.action) p.set("action", q.action);
  p.set("limit", String(q.limit ?? 200));
  return get<
    {
      id: number;
      at: string;
      actor: string;
      action: string;
      target: string | null;
      outcome: string;
      detail: Record<string, unknown>;
    }[]
  >(`/admin/audit?${p}`);
};
export const diagnostics = () => get<Record<string, unknown>>("/admin/diagnostics");
export const adminModels = () =>
  get<{ catalog_version: string; retrieval_profile: string; index_version: string | null; models: CatalogModel[] }>(
    "/admin/models",
  );

// ---- formatting helpers ----
export function gib(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "unknown";
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

export function downloadText(filename: string, text: string, type = "text/plain"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
