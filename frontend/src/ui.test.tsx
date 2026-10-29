// UI tests (mock-adapter level): real components, fetch mocked at the network boundary.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatView } from "./ChatView";
import { CompatLab, SIMULATION_BANNER } from "./CompatLab";
import { Login } from "./Login";
import { Markdown } from "./Markdown";
import { SetupWizard } from "./SetupWizard";

type Handler = (url: string, init?: RequestInit) => unknown;
let calls: { url: string; init?: RequestInit }[] = [];

function mockFetch(handler: Handler) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = handler(url, init);
      if (body instanceof Response) return body;
      return new Response(JSON.stringify(body ?? {}), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
}

const ASSESSMENT = {
  source: "simulated",
  simulated: true,
  catalog_version: "test",
  report: {
    source: "simulated", profile_name: "cpu-only-16", os: "linux", os_version: null, arch: "amd64",
    cpu_model: "Synthetic", cpu_cores: 6, cpu_threads: 12, cpu_flags: ["avx2"], ram_total_bytes: 16 * 2 ** 30,
    ram_available_bytes: 13 * 2 ** 30, disk_free_bytes: 200 * 2 ** 30, gpus: [], gpu_probe_ok: true, runtimes: [],
    limitations: [],
  },
  deployment: "native",
  tier: "constrained",
  document_assistant_available: true,
  budget: {
    ram_total_bytes: 1, ram_available_bytes: 1, os_reserve_bytes: 1, services_bytes: 1, retrieval_bytes: 1,
    llm_ram_budget_bytes: 1, llm_ram_budget_now_bytes: 1, gpu_name: null, gpu_usable_bytes: 0, headroom_fraction: 0.1,
  },
  recommended_model_id: "m1",
  settings: null,
  evaluations: [
    { model_id: "m1", display_name: "Model One", params_b: 4, quantization: "Q4", licence: "apache-2.0",
      download_bytes: 2 ** 31, eligible: true, fits_now: true, placement: "cpu", max_sessions: 1, estimate: null,
      rejections: [], warnings: [{ code: "cpu_only", message: "Runs on the CPU only" }], ranked: false },
    { model_id: "m2", display_name: "Model Two", params_b: 32, quantization: "Q4", licence: "apache-2.0",
      download_bytes: 2 ** 34, eligible: false, fits_now: null, placement: null, max_sessions: 0, estimate: null,
      rejections: [{ code: "insufficient_memory", message: "Needs about 20 GiB" }], warnings: [], ranked: false },
  ],
  warnings: [],
  assumptions: ["All figures are estimates."],
};

beforeEach(() => {
  document.cookie = "airgap_csrf=tok123";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Markdown", () => {
  it("renders code blocks with a copy button and never interprets HTML", () => {
    const { container } = render(
      <Markdown text={'Hello **world** [1]\n\n```python\nprint(1)\n```\n\n<img src=x onerror=alert(1)>'} />,
    );
    expect(container.querySelector("pre code")?.textContent).toBe("print(1)");
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(container.querySelector("strong")?.textContent).toBe("world");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});

describe("Compatibility Lab", () => {
  beforeEach(() =>
    mockFetch((url) => {
      if (url.includes("/admin/compat/profiles"))
        return {
          banner: "SIMULATION - NOT A REAL HARDWARE SCAN",
          profiles: [{ id: "cpu-only-16", label: "CPU-only desktop", description: "No GPU", os: "linux", arch: "amd64",
            ram_total_gib: 16, ram_available_gib: 13, disk_free_gib: 200, gpus: [] }],
        };
      if (url.includes("/admin/compat/assess")) return ASSESSMENT;
      return {};
    }),
  );

  it("shows the simulation banner, calls the real planner endpoint and lists reasons", async () => {
    const onExit = vi.fn();
    render(<CompatLab onExit={onExit} />);
    expect(screen.getByText(SIMULATION_BANNER)).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("option", { name: "CPU-only desktop" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Run assessment" }));
    await waitFor(() => expect(screen.getByText("Needs about 20 GiB")).toBeTruthy());
    expect(screen.getAllByText(SIMULATION_BANNER).length).toBe(2); // page banner + result tag
    const assess = calls.find((c) => c.url.includes("/admin/compat/assess"));
    expect(JSON.parse(String(assess?.init?.body))).toMatchObject({ profile_id: "cpu-only-16" });
    expect(calls.some((c) => c.url.includes("/setup/"))).toBe(false); // never touches real setup
    fireEvent.click(screen.getByRole("button", { name: "Back to real hardware" }));
    expect(onExit).toHaveBeenCalled();
  });

  it("validates custom specs before calling the API", async () => {
    render(<CompatLab onExit={() => undefined} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "CPU-only desktop" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Device profile"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("Total RAM (GiB)"), { target: { value: "-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Run assessment" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/must be 0 or more/));
    expect(calls.some((c) => c.url.includes("/admin/compat/assess"))).toBe(false);
  });
});

describe("Setup wizard", () => {
  it("does not scan until the user approves, then sends the CSRF token", async () => {
    mockFetch((url) => {
      if (url.endsWith("/setup/state")) return { steps: {}, assessment: null, complete: false, job: null };
      if (url.endsWith("/setup/scan")) return { ...ASSESSMENT, source: "real", simulated: false };
      if (url.endsWith("/setup/prerequisites")) return [];
      return {};
    });
    render(<SetupWizard onDone={() => undefined} />);
    expect(screen.getByText(/will read \(and not change\)/)).toBeTruthy();
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls.some((c) => c.url.endsWith("/setup/scan"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Scan my device" }));
    await waitFor(() => expect(screen.getByText("Your device")).toBeTruthy());
    const scan = calls.find((c) => c.url.endsWith("/setup/scan"));
    expect(JSON.parse(String(scan?.init?.body))).toEqual({ consent: true });
    expect((scan?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    expect(screen.getAllByText("Model One").length).toBeGreaterThan(0);
  });
});

describe("Chat", () => {
  it("labels the General Assistant as ungrounded and has no knowledge toggle", () => {
    render(<ChatView assistant="general" conversationId={null} onConversation={() => undefined} />);
    expect(screen.getByText(/answers are not grounded/)).toBeTruthy();
    expect(screen.queryByLabelText("Allow general knowledge")).toBeNull();
  });

  it("Document Assistant defaults to strict and streams a cited answer", async () => {
    const sse =
      'data: {"type":"meta","query":"q","rewritten":false}\n\n' +
      'data: {"type":"token","text":"Every 500 hours [1]"}\n\n' +
      'data: {"type":"final","conversation_id":"c1","message_id":"m1","answer":"Every 500 hours [1]",' +
      '"mode":"grounded","refused":false,"flagged":false,"model_id":"x","segments":[],' +
      '"citations":[{"n":1,"quote":"serviced every 500 operating hours","valid":true,"reason":"ok",' +
      '"doc_id":"d","doc_name":"pump.pdf","version":1,"page":3,"section":"1 Maintenance","chunk_id":"k"}]}\n\n';
    mockFetch(() => new Response(sse, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const onConv = vi.fn();
    render(<ChatView assistant="documents" conversationId={null} onConversation={onConv} />);
    expect(screen.getByText(/Strict: answers only from your documents/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "How often?" } });
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    await waitFor(() => expect(screen.getByRole("button", { name: /Source 1: pump.pdf/ })).toBeTruthy());
    expect(JSON.parse(String(calls[0]?.init?.body))).toMatchObject({ mode: "grounded" });
    expect(onConv).toHaveBeenCalledWith("c1");
    fireEvent.click(screen.getByRole("button", { name: /Source 1: pump.pdf/ }));
    expect(screen.getByText("serviced every 500 operating hours")).toBeTruthy();
    expect(screen.getByText(/Page 3/)).toBeTruthy();
  });

  it("shows a helpful message when the local model is down", async () => {
    mockFetch(
      () =>
        new Response('data: {"type":"error","code":"llm_unavailable","message":"The local model is not responding."}\n\n', {
          status: 200,
        }),
    );
    render(<ChatView assistant="general" conversationId={null} onConversation={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hi" } });
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    await waitFor(() => expect(screen.getByText("The local model is not responding.")).toBeTruthy());
  });
});

describe("Login", () => {
  it("first run checks that passwords match before calling the API", async () => {
    mockFetch(() => ({}));
    render(<Login firstRun onSignedIn={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long enough password" } });
    fireEvent.change(screen.getByLabelText("Repeat password"), { target: { value: "different password!" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert").textContent).toMatch(/do not match/);
    expect(calls.length).toBe(0);
  });
});
