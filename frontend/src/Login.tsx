import { useState, type FormEvent } from "react";
import { bootstrap, login } from "./api";

export function Login({ firstRun, onSignedIn }: { firstRun: boolean; onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (firstRun && password !== confirm) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await (firstRun ? bootstrap(username, password) : login(username, password));
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <form className="card auth-card" onSubmit={(e) => void submit(e)}>
        <div className="brand large">
          <span className="brand-mark" aria-hidden>◆</span> AirGap
        </div>
        <h1>{firstRun ? "Create the administrator account" : "Sign in"}</h1>
        <p className="muted">
          {firstRun
            ? "This account manages setup, users and models. It can only be created from this computer."
            : "Your private, on-device assistant."}
        </p>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={firstRun ? 3 : 1}
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={firstRun ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={firstRun ? 12 : 1}
          />
        </label>
        {firstRun && (
          <>
            <label>
              Repeat password
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
            </label>
            <p className="muted small">At least 12 characters. A passphrase of a few words works well.</p>
          </>
        )}
        {error && (
          <div className="callout bad" role="alert">
            {error}
          </div>
        )}
        <button type="submit" className="primary large" disabled={busy}>
          {busy ? "Please wait…" : firstRun ? "Create account" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
