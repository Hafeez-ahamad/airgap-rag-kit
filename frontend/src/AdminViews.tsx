import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  adminModels,
  audit,
  createUser,
  diagnostics,
  downloadText,
  gib,
  realReport,
  updateUser,
  users,
  type CatalogModel,
  type Me,
  type Role,
  type UserRow,
} from "./api";

function useLoad<T>(fn: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    fn()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [fn]);
  useEffect(() => {
    const t = window.setTimeout(reload, 0);
    return () => window.clearTimeout(t);
  }, [reload]);
  return { data, error, reload };
}

export function UsersView() {
  const { data, error, reload } = useLoad(users);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role, groups: "default" });
  const [msg, setMsg] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await createUser({ ...form, groups: form.groups.split(",").map((g) => g.trim()).filter(Boolean) });
      setForm({ username: "", password: "", role: "viewer", groups: "default" });
      setMsg("User created.");
      reload();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Could not create user");
    }
  }

  async function patch(u: UserRow, p: Parameters<typeof updateUser>[1]) {
    try {
      await updateUser(u.id, p);
      reload();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Update failed");
    }
  }

  return (
    <div className="page">
      <h2>Users and groups</h2>
      <p className="muted">
        Viewers can chat and read documents in their groups. Editors can also upload and share documents
        within their groups. Admins manage everything.
      </p>
      {error && <div className="callout bad">{error}</div>}
      {msg && <div className="callout">{msg}</div>}
      <div className="table-wrap card">
        <table>
          <thead>
            <tr>
              <th scope="col">User</th>
              <th scope="col">Role</th>
              <th scope="col">Groups</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>
                  <select
                    aria-label={`Role of ${u.username}`}
                    value={u.role}
                    onChange={(e) => void patch(u, { role: e.target.value as Role })}
                  >
                    <option value="viewer">Viewer</option>
                    <option value="editor">Editor</option>
                    <option value="admin">Admin</option>
                  </select>
                </td>
                <td>
                  <input
                    aria-label={`Groups of ${u.username}`}
                    defaultValue={u.groups.join(", ")}
                    onBlur={(e) => {
                      const groups = e.target.value.split(",").map((g) => g.trim()).filter(Boolean);
                      if (groups.join() !== u.groups.join()) void patch(u, { groups });
                    }}
                  />
                </td>
                <td>
                  <button type="button" onClick={() => void patch(u, { disabled: !u.disabled })}>
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="card form-grid" onSubmit={(e) => void add(e)}>
        <h3>Add a user</h3>
        <label>
          Username
          <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        </label>
        <label>
          Temporary password (12+ characters)
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            minLength={12}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label>
          Groups (comma separated)
          <input value={form.groups} onChange={(e) => setForm({ ...form, groups: e.target.value })} />
        </label>
        <button type="submit" className="primary">
          Add user
        </button>
      </form>
    </div>
  );
}

export function AuditView() {
  const [action, setAction] = useState("");
  const load = useCallback(() => audit({ action: action || undefined, limit: 300 }), [action]);
  const { data, error } = useLoad(load);
  return (
    <div className="page">
      <h2>Audit log</h2>
      <p className="muted">
        Append-only record of sign-ins, uploads, sharing changes and questions. Questions are stored as a
        fingerprint, not as text.
      </p>
      <div className="row-actions">
        <label>
          Action
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">All</option>
            {["auth.login", "chat.answer", "chat.refused", "document.upload", "document.acl", "document.delete", "user.create", "user.update", "setup.scan", "setup.install"].map(
              (a) => (
                <option key={a}>{a}</option>
              ),
            )}
          </select>
        </label>
        <a className="button" href="/api/admin/audit/export" download>
          Export (JSON lines)
        </a>
      </div>
      {error && <div className="callout bad">{error}</div>}
      <div className="table-wrap card">
        <table>
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Who</th>
              <th scope="col">Action</th>
              <th scope="col">Outcome</th>
              <th scope="col">Details</th>
            </tr>
          </thead>
          <tbody>
            {data?.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No events yet.
                </td>
              </tr>
            )}
            {data?.map((r) => (
              <tr key={r.id}>
                <td className="small">{new Date(r.at).toLocaleString()}</td>
                <td>{r.actor}</td>
                <td>
                  <code>{r.action}</code>
                </td>
                <td>
                  <span className={`badge ${r.outcome === "ok" ? "good" : "bad"}`}>{r.outcome}</span>
                </td>
                <td className="small mono">{JSON.stringify(r.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SettingsView({ me, onOpenLab, onRerunSetup }: { me: Me; onOpenLab: () => void; onRerunSetup: () => void }) {
  const isAdmin = me.role === "admin";
  const models = useLoad(useCallback(() => (isAdmin ? adminModels() : Promise.resolve(null)), [isAdmin]));
  const [diag, setDiag] = useState<string | null>(null);

  return (
    <div className="page">
      <h2>Settings</h2>
      <section className="card">
        <h3>Account</h3>
        <p>
          Signed in as <strong>{me.username}</strong> ({me.role}) · groups: {me.groups.join(", ")}
        </p>
      </section>

      <section className="card">
        <h3>Privacy</h3>
        <ul className="checklist">
          <li>Documents, questions and answers stay on this computer.</li>
          <li>The app makes no network requests while you use it; internet is used only to download models during setup.</li>
          <li>The audit log stores a fingerprint of each question, not its text.</li>
          <li>The app listens on this computer only (127.0.0.1) unless an administrator changes it.</li>
        </ul>
        <p className="muted small">
          A no-egress test that captures outbound traffic has not been run on this installation yet, so
          “fully offline” is not claimed.
        </p>
      </section>

      {isAdmin && (
        <section className="card">
          <h3>Models</h3>
          {models.error && <div className="callout bad">{models.error}</div>}
          {models.data && (
            <>
              <p className="muted small">
                Catalogue {models.data.catalog_version} · search models: {models.data.retrieval_profile} (managed by
                the app){models.data.index_version ? ` · index ${models.data.index_version}` : ""}
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Model</th>
                      <th scope="col">On this device</th>
                      <th scope="col">Installed</th>
                      <th scope="col">Size</th>
                      <th scope="col">Licence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.data.models.map((m: CatalogModel) => (
                      <tr key={m.id}>
                        <td>
                          {m.display_name} {m.active && <span className="badge good">Active</span>}
                          <div className="muted small">{m.tag}</div>
                        </td>
                        <td>
                          {m.fit === null ? (
                            <span className="muted">scan needed</span>
                          ) : (
                            <span className={`badge ${m.fit === "fits" ? "good" : "bad"}`} title={m.reasons.join(" ")}>
                              {m.fit === "fits" ? "fits" : "does not fit"}
                            </span>
                          )}
                        </td>
                        <td>{m.installed ? "yes" : "no"}</td>
                        <td>{gib(m.download_bytes)}</td>
                        <td>{m.licence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div className="row-actions">
            <button type="button" onClick={onRerunSetup}>
              Re-scan and change model
            </button>
          </div>
        </section>
      )}

      {isAdmin && (
        <section className="card">
          <h3>Diagnostics</h3>
          <p className="muted small">
            Safe support information: versions, component health and setup progress. Personal paths and secrets
            are removed.
          </p>
          <div className="row-actions">
            <button type="button" onClick={() => void diagnostics().then((d) => setDiag(JSON.stringify(d, null, 2)))}>
              Show diagnostics
            </button>
            <button
              type="button"
              onClick={() =>
                void Promise.all([diagnostics(), realReport().catch(() => null)]).then(([d, r]) =>
                  downloadText("airgap-diagnostics.json", JSON.stringify({ diagnostics: d, hardware: r }, null, 2)),
                )
              }
            >
              Export diagnostics
            </button>
            <button type="button" onClick={onOpenLab}>
              Open Compatibility Lab
            </button>
          </div>
          {diag && <pre className="diag">{diag}</pre>}
        </section>
      )}
    </div>
  );
}
