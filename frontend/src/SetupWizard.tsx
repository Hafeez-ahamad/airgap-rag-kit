import { useCallback, useEffect, useState } from "react";
import {
  cancelInstall,
  gib,
  install,
  plan as dryRun,
  prerequisites,
  scan,
  setupState,
  type Assessment,
  type Check,
  type InstallPlan,
  type JobSnapshot,
} from "./api";
import { AssessmentView } from "./AssessmentView";

type Stage = "welcome" | "scanning" | "summary" | "prereqs" | "models" | "confirm" | "install" | "done";

const STAGES: { id: Stage; label: string }[] = [
  { id: "welcome", label: "Welcome" },
  { id: "summary", label: "Your device" },
  { id: "prereqs", label: "Requirements" },
  { id: "models", label: "Model" },
  { id: "confirm", label: "Plan" },
  { id: "install", label: "Install" },
  { id: "done", label: "Ready" },
];

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  verified: "Verified",
  failed: "Failed",
  cancelled: "Cancelled",
  needs_action: "Needs action",
};

export function SetupWizard({ onDone }: { onDone: () => void }) {
  const [stage, setStage] = useState<Stage>("welcome");
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [planned, setPlanned] = useState<InstallPlan | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Resume where setup stopped (after a restart or reboot).
  useEffect(() => {
    void setupState()
      .then((s) => {
        if (s.complete) setStage("done");
        else if (s.job?.status === "running") {
          setJob(s.job);
          setStage("install");
        } else if (s.assessment) {
          setAssessment(s.assessment);
          setSelected(s.assessment.recommended_model_id);
          setStage("summary");
        }
      })
      .catch(() => undefined);
  }, []);

  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }, []);

  const doScan = () =>
    run(async () => {
      setStage("scanning");
      try {
        const a = await scan();
        setAssessment(a);
        setSelected(a.recommended_model_id);
        setChecks(await prerequisites());
        setStage("summary");
      } catch (e) {
        setStage("welcome");
        throw e;
      }
    });

  // Poll real job state while installing.
  useEffect(() => {
    if (stage !== "install") return;
    const id = window.setInterval(() => {
      void setupState().then((s) => {
        if (s.job) setJob(s.job);
        if (s.complete) setStage("done");
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [stage]);

  const index = STAGES.findIndex((s) => s.id === (stage === "scanning" ? "welcome" : stage));
  const blocking = checks.filter((c) => c.required && !["ok", "not_required"].includes(c.status));

  return (
    <div className="wizard">
      <ol className="stepper" aria-label="Setup progress">
        {STAGES.map((s, i) => (
          <li key={s.id} className={i < index ? "done" : i === index ? "current" : ""} aria-current={i === index}>
            <span className="dot">{i < index ? "✓" : i + 1}</span>
            {s.label}
          </li>
        ))}
      </ol>
      {error && (
        <div className="callout bad" role="alert">
          {error}
        </div>
      )}

      {(stage === "welcome" || stage === "scanning") && (
        <section className="card wizard-card">
          <h2>Set up your private assistant</h2>
          <p>
            Everything runs on this computer. Your documents and questions are not sent anywhere.
            Setup downloads models once; after that the assistant works without the internet.
          </p>
          <h3>First, a quick device check</h3>
          <p>To recommend models that fit, the app will read (and not change):</p>
          <ul className="checklist">
            <li>Operating system, processor and its features</li>
            <li>Total and currently free memory</li>
            <li>Graphics card and its memory, if any</li>
            <li>Free disk space for models and data</li>
            <li>Whether the local model runtime is installed and running</li>
          </ul>
          <p className="muted small">
            Nothing is installed and no settings are changed during the check. Results stay on this
            device.
          </p>
          <button type="button" className="primary large" disabled={busy} onClick={() => void doScan()}>
            {stage === "scanning" ? "Scanning…" : "Scan my device"}
          </button>
        </section>
      )}

      {stage === "summary" && assessment && (
        <section className="card wizard-card">
          <h2>Your device</h2>
          <AssessmentView assessment={assessment} compact />
          <div className="wizard-nav">
            <button type="button" onClick={() => void doScan()} disabled={busy}>
              Scan again
            </button>
            <button
              type="button"
              className="primary"
              disabled={!assessment.recommended_model_id}
              onClick={() =>
                void run(async () => {
                  setChecks(await prerequisites());
                  setStage("prereqs");
                })
              }
            >
              Continue
            </button>
          </div>
          {!assessment.recommended_model_id && (
            <p className="callout warn">
              No supported model fits this device safely. See the reasons above; a device with more
              memory or free disk is needed.
            </p>
          )}
        </section>
      )}

      {stage === "prereqs" && (
        <section className="card wizard-card">
          <h2>Requirements</h2>
          <ul className="checks">
            {checks.map((c) => (
              <li key={c.id} className={`check ${c.status}`}>
                <div className="check-head">
                  <span className={`badge ${badge(c.status)}`}>{c.status.replace("_", " ")}</span>
                  <strong>{c.title}</strong>
                  {!c.required && <span className="muted small">optional</span>}
                </div>
                {c.detail && <p className="muted small">{c.detail}</p>}
                {c.status !== "ok" &&
                  c.actions.map((a, k) => (
                    <div key={k} className="action">
                      <strong>{a.title}</strong>
                      <p className="small">{a.instructions}</p>
                      {a.url && <code className="small">{a.url}</code>}
                    </div>
                  ))}
              </li>
            ))}
          </ul>
          <div className="wizard-nav">
            <button type="button" onClick={() => setStage("summary")}>
              Back
            </button>
            <button type="button" disabled={busy} onClick={() => void doScan()}>
              Check again
            </button>
            <button type="button" className="primary" disabled={blocking.length > 0} onClick={() => setStage("models")}>
              Continue
            </button>
          </div>
          {blocking.length > 0 && (
            <p className="muted small">Finish the required steps above, then choose “Check again”.</p>
          )}
        </section>
      )}

      {stage === "models" && assessment && (
        <section className="card wizard-card">
          <h2>Choose a model</h2>
          <p className="muted">
            Only models that fit this device are offered. Sizes and memory are estimates; a real test
            runs before setup finishes.
          </p>
          <div className="model-options" role="radiogroup" aria-label="Model">
            {assessment.evaluations
              .filter((e) => e.eligible)
              .map((e) => (
                <label key={e.model_id} className={`model-option ${selected === e.model_id ? "selected" : ""}`}>
                  <input
                    type="radio"
                    name="model"
                    checked={selected === e.model_id}
                    onChange={() => setSelected(e.model_id)}
                  />
                  <div>
                    <div className="model-title">
                      {e.display_name}
                      {e.model_id === assessment.recommended_model_id && <span className="badge good">Recommended</span>}
                    </div>
                    <div className="muted small">
                      {e.quantization} · download {gib(e.download_bytes)} · licence {e.licence} ·{" "}
                      {placementLabel(e.placement)}
                    </div>
                    {e.warnings.map((w) => (
                      <div key={w.code} className="small warn-text">
                        {w.message}
                      </div>
                    ))}
                  </div>
                </label>
              ))}
          </div>
          <details className="small">
            <summary>Models that do not fit ({assessment.evaluations.filter((e) => !e.eligible).length})</summary>
            <ul>
              {assessment.evaluations
                .filter((e) => !e.eligible)
                .map((e) => (
                  <li key={e.model_id}>
                    <strong>{e.display_name}</strong>: {e.rejections.map((r) => r.message).join(" ")}
                  </li>
                ))}
            </ul>
          </details>
          <p className="muted small">
            Search and reranking models are managed by the app ({assessment.document_assistant_available
              ? "multilingual retrieval, about 4.6 GB download"
              : "not available on this device: General Assistant only"}).
          </p>
          <div className="wizard-nav">
            <button type="button" onClick={() => setStage("prereqs")}>
              Back
            </button>
            <button
              type="button"
              className="primary"
              disabled={!selected || busy}
              onClick={() =>
                void run(async () => {
                  setPlanned(await dryRun([selected ?? ""]));
                  setStage("confirm");
                })
              }
            >
              Review plan
            </button>
          </div>
        </section>
      )}

      {stage === "confirm" && planned && (
        <section className="card wizard-card">
          <h2>Installation plan</h2>
          <p className="muted">Nothing has been changed yet. This is what setup will do:</p>
          <ol className="plan-steps">
            {planned.steps.map((s) => (
              <li key={s.id}>
                <strong>{s.title}</strong>
                {s.bytes ? <span className="muted"> · {gib(s.bytes)}</span> : null}
                {s.needs_network && <span className="badge">network</span>}
                {s.detail && <div className="muted small">{s.detail}</div>}
              </li>
            ))}
          </ol>
          <p>
            Total download: <strong>{gib(planned.total_download_bytes)}</strong> (estimate). Downloads
            resume if interrupted and are checked before use.
          </p>
          <div className="wizard-nav">
            <button type="button" onClick={() => setStage("models")}>
              Back
            </button>
            <button
              type="button"
              className="primary"
              disabled={!planned.ready || busy}
              onClick={() =>
                void run(async () => {
                  setJob(await install(planned.model_ids));
                  setStage("install");
                })
              }
            >
              Install
            </button>
          </div>
        </section>
      )}

      {stage === "install" && (
        <section className="card wizard-card">
          <h2>Installing</h2>
          {job ? (
            <ul className="progress-list">
              {Object.entries(job.items).map(([k, v]) => (
                <li key={k}>
                  <div className="progress-head">
                    <span>{itemLabel(k)}</span>
                    <span className={`badge ${badge(v.status)}`}>{STATUS_LABEL[v.status] ?? v.status}</span>
                  </div>
                  {v.total ? (
                    <progress value={v.completed ?? 0} max={v.total} aria-label={`${itemLabel(k)} progress`} />
                  ) : null}
                  {v.detail && <div className="muted small">{v.detail}</div>}
                </li>
              ))}
            </ul>
          ) : (
            <p>Starting…</p>
          )}
          {job?.status === "failed" && (
            <div className="callout bad">
              <strong>Installation failed.</strong> {job.error} Fix the problem and try again; finished
              downloads are kept.
            </div>
          )}
          {job?.status === "cancelled" && (
            <div className="callout warn">Cancelled. Partial downloads are kept and resume next time.</div>
          )}
          <div className="wizard-nav">
            {job?.status === "running" ? (
              <button type="button" onClick={() => void cancelInstall()}>
                Cancel
              </button>
            ) : (
              <button type="button" onClick={() => setStage("models")}>
                Back
              </button>
            )}
          </div>
        </section>
      )}

      {stage === "done" && (
        <section className="card wizard-card">
          <h2>You are ready</h2>
          <p>The model loaded and answered a real test request on this device.</p>
          <button type="button" className="primary large" onClick={onDone}>
            Open the workspace
          </button>
        </section>
      )}
    </div>
  );
}

export function badge(status: string): string {
  if (["ok", "verified", "fits"].includes(status)) return "good";
  if (["failed", "missing", "unhealthy", "outdated"].includes(status)) return "bad";
  if (["needs_action", "unknown", "cancelled"].includes(status)) return "warn";
  return "";
}

export function placementLabel(p: string | null): string {
  return (
    { gpu: "runs on the GPU", unified: "runs on unified memory", partial_offload: "split GPU/CPU (slower)", cpu: "CPU only (slow)" }[
      p ?? ""
    ] ?? "unknown placement"
  );
}

function itemLabel(key: string): string {
  if (key.startsWith("model:")) return `Model ${key.slice(6)}`;
  if (key === "retrieval") return "Search models";
  if (key === "verify") return "Real inference test";
  return key;
}
