import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  authStatus,
  conversations,
  getDevUser,
  health,
  logout,
  me as loadMe,
  setDevAuth,
  setDevUser,
  type Conversation,
  type Health,
  type Me,
} from "./api";
import { AuditView, SettingsView, UsersView } from "./AdminViews";
import { ChatView, type Assistant } from "./ChatView";
import { CompatLab } from "./CompatLab";
import { DocumentsView } from "./DocumentsView";
import { Login } from "./Login";
import { SetupWizard } from "./SetupWizard";

type Route = "general" | "documents" | "library" | "settings" | "users" | "audit" | "lab" | "setup";
const ROUTES: Route[] = ["general", "documents", "library", "settings", "users", "audit", "lab", "setup"];

function readRoute(): Route {
  const r = window.location.hash.replace(/^#\/?/, "") as Route;
  return ROUTES.includes(r) ? r : "documents";
}

const COMPONENT: Record<string, string> = {
  llm: "local model",
  vector_store: "search index",
  retrieval_models: "search models",
  database: "database",
  queue: "ingestion queue",
  api: "server",
};

type Auth = { state: "loading" } | { state: "login"; firstRun: boolean } | { state: "ready"; me: Me };

export function App() {
  const [auth, setAuth] = useState<Auth>({ state: "loading" });
  const [route, setRouteState] = useState<Route>(readRoute());
  const [status, setStatus] = useState<Health | null>(null);
  const [convs, setConvs] = useState<Conversation[]>([]);
  // opened: chosen in the sidebar (remounts the chat); active: highlighted (set by the chat itself).
  const [opened, setOpened] = useState<{ id: string | null; n: number }>({ id: null, n: 0 });
  const [activeConv, setActiveConv] = useState<string | null>(null);
  const openConversation = (id: string | null) => {
    setOpened((o) => ({ id, n: o.n + 1 }));
    setActiveConv(id);
  };
  const [navOpen, setNavOpen] = useState(false);
  const redirected = useRef(false);

  const setRoute = useCallback((r: Route) => {
    window.location.hash = `/${r}`;
    setRouteState(r);
    setNavOpen(false);
  }, []);

  const signIn = useCallback(async () => {
    try {
      const s = await authStatus();
      setDevAuth(s.dev_auth);
      if (s.needs_bootstrap && !s.dev_auth) {
        setAuth({ state: "login", firstRun: true });
        return;
      }
      setAuth({ state: "ready", me: await loadMe() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAuth({ state: "login", firstRun: false });
      else setAuth({ state: "login", firstRun: false });
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => void signIn(), 0);
    const onHash = () => setRouteState(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("hashchange", onHash);
    };
  }, [signIn]);

  // Health polling and first-run routing.
  useEffect(() => {
    if (auth.state !== "ready") return;
    let alive = true;
    const poll = () =>
      health()
        .then((h) => {
          if (!alive) return;
          setStatus(h);
          // Send an admin to setup once per session; they may leave it deliberately.
          if (h.setup_complete === false && auth.me.role === "admin" && !redirected.current) {
            redirected.current = true;
            if (readRoute() !== "lab") {
              window.location.hash = "/setup";
              setRouteState("setup");
            }
          }
        })
        .catch(() => alive && setStatus({ status: "degraded", components: { api: "down" } }));
    void poll();
    const id = window.setInterval(poll, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [auth]);

  const refreshConvs = useCallback(() => {
    conversations()
      .then(setConvs)
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (auth.state === "ready") {
      const t = window.setTimeout(refreshConvs, 0);
      return () => window.clearTimeout(t);
    }
  }, [auth, refreshConvs]);

  if (auth.state === "loading") {
    return (
      <main className="auth-screen">
        <p className="muted">Loading…</p>
      </main>
    );
  }
  if (auth.state === "login") {
    return <Login firstRun={auth.firstRun} onSignedIn={() => void signIn()} />;
  }

  const me = auth.me;
  const isAdmin = me.role === "admin";
  const down = status ? Object.entries(status.components).filter(([, v]) => v !== "ok").map(([k]) => k) : [];
  const llmOk = status?.components.llm === "ok";

  if (route === "setup" && isAdmin) {
    return (
      <div className="setup-shell">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark" aria-hidden>◆</span> AirGap
          </div>
          <button type="button" className="ghost" onClick={() => setRoute("documents")}>
            {status?.setup_complete ? "Exit setup" : "Leave setup for now"}
          </button>
        </header>
        <SetupWizard onDone={() => setRoute("documents")} />
      </div>
    );
  }

  const chatRoute = route === "general" || route === "documents";
  const nav = (r: Route, label: string) => (
    <button
      type="button"
      className={`nav-item ${route === r ? "active" : ""}`}
      aria-current={route === r ? "page" : undefined}
      onClick={() => {
        if (r !== route) openConversation(null);
        setRoute(r);
      }}
    >
      {label}
    </button>
  );

  return (
    <div className={`shell ${navOpen ? "nav-open" : ""}`}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar" aria-label="Navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◆</span> AirGap
        </div>
        <nav className="nav-group" aria-label="Assistants">
          {nav("documents", "Document Assistant")}
          {nav("general", "General Assistant")}
        </nav>
        {chatRoute && (
          <div className="nav-group conversations">
            <div className="nav-label">Recent conversations</div>
            {convs.length === 0 && <p className="muted small">No conversations yet.</p>}
            <ul>
              {convs.slice(0, 30).map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className={`conv ${c.id === activeConv ? "active" : ""}`}
                    title={c.title}
                    onClick={() => openConversation(c.id)}
                  >
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <nav className="nav-group" aria-label="Workspace">
          {nav("library", "Document library")}
          {nav("settings", "Settings")}
        </nav>
        {isAdmin && (
          <nav className="nav-group" aria-label="Administration">
            <div className="nav-label">Admin</div>
            {nav("users", "Users")}
            {nav("audit", "Audit log")}
            {nav("lab", "Compatibility Lab")}
            <button type="button" className="nav-item" onClick={() => setRoute("setup")}>
              Setup
            </button>
          </nav>
        )}
        <div className="sidebar-foot">
          <div className={`runtime ${llmOk ? "ok" : "down"}`} title="Local model runtime">
            <span className="dot" aria-hidden /> {llmOk ? "Local model ready" : "Local model unavailable"}
          </div>
          {me.dev_auth ? (
            <label className="small muted dev-user" title="Development mode only">
              Dev user
              <input defaultValue={getDevUser()} onBlur={(e) => setDevUser(e.target.value.trim() || "dev")} />
            </label>
          ) : (
            <div className="user-row">
              <span className="small">
                {me.username} · {me.role}
              </span>
              <button
                type="button"
                className="ghost small"
                onClick={() => void logout().finally(() => setAuth({ state: "login", firstRun: false }))}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="main-col">
        <header className="mobile-bar">
          <button type="button" className="ghost" aria-label="Open navigation" onClick={() => setNavOpen(!navOpen)}>
            ☰
          </button>
          <div className="brand">AirGap</div>
        </header>
        {status?.status === "degraded" && (
          <div className="callout warn banner" role="alert">
            Limited service: {down.map((d) => COMPONENT[d] ?? d).join(", ") || "a component"} unavailable.{" "}
            {down.includes("llm")
              ? "Check that Ollama is running and the selected model is installed (or finish setup). "
              : ""}
            {isAdmin ? "See Settings › Diagnostics." : "Contact your administrator if this persists."}
          </div>
        )}
        <main id="main" tabIndex={-1}>
          {chatRoute && (
            <ChatView
              key={`${route}:${opened.n}`}
              assistant={route as Assistant}
              conversationId={opened.id}
              onConversation={(id) => {
                if (id === null) openConversation(null);
                else setActiveConv(id);
                refreshConvs();
              }}
            />
          )}
          {route === "library" && <DocumentsView me={me} />}
          {route === "settings" && (
            <SettingsView me={me} onOpenLab={() => setRoute("lab")} onRerunSetup={() => setRoute("setup")} />
          )}
          {route === "users" && isAdmin && <UsersView />}
          {route === "audit" && isAdmin && <AuditView />}
          {route === "lab" && isAdmin && <CompatLab onExit={() => setRoute("settings")} />}
          {!isAdmin && ["users", "audit", "lab"].includes(route) && (
            <p className="callout">This area is for administrators.</p>
          )}
        </main>
      </div>
    </div>
  );
}
