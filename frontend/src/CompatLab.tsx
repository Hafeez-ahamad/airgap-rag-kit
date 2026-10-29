// Compatibility Lab (admin): runs the REAL planner against SIMULATED hardware via the API.
// It cannot install, download or run models. The banner stays visible while a simulation is shown.
import { useEffect, useState } from "react";
import {
  downloadText,
  labAssess,
  labProfiles,
  labReport,
  type Assessment,
  type LabInput,
  type LabProfiles,
  type SimulatedSpec,
} from "./api";
import { AssessmentView } from "./AssessmentView";

export const SIMULATION_BANNER = "SIMULATION — NOT A REAL HARDWARE SCAN";

const EMPTY_CUSTOM: SimulatedSpec = {
  id: "custom",
  label: "Custom synthetic device",
  os: "linux",
  arch: "amd64",
  ram_total_gib: 16,
  ram_available_gib: 12,
  disk_free_gib: 100,
  cpu_flags: ["avx2"],
  gpus: [],
};

type Field = "ram_total_gib" | "ram_available_gib" | "disk_free_gib";

export function CompatLab({ onExit }: { onExit: () => void }) {
  const [profiles, setProfiles] = useState<LabProfiles | null>(null);
  const [source, setSource] = useState<string>("constrained-gpu");
  const [custom, setCustom] = useState<SimulatedSpec>(EMPTY_CUSTOM);
  const [gpuVram, setGpuVram] = useState<string>("");
  const [context, setContext] = useState(4096);
  const [result, setResult] = useState<Assessment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    labProfiles()
      .then(setProfiles)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load profiles"));
  }, []);

  function input(): LabInput {
    if (source !== "custom") return { profile_id: source };
    const vram = gpuVram.trim() === "" ? null : Number(gpuVram);
    return {
      spec: {
        ...custom,
        gpus:
          vram === null
            ? []
            : [{ vendor: "nvidia", name: "Simulated NVIDIA GPU", vram_total_gib: vram, driver_version: "simulated" }],
      },
    };
  }

  function validate(): string | null {
    if (source !== "custom") return null;
    for (const f of ["ram_total_gib", "ram_available_gib", "disk_free_gib"] as Field[]) {
      const v = custom[f];
      if (v !== null && (Number.isNaN(v) || v < 0)) return `${f.replace(/_/g, " ")} must be 0 or more`;
    }
    if (gpuVram.trim() && (Number.isNaN(Number(gpuVram)) || Number(gpuVram) < 0)) return "GPU memory must be 0 or more";
    return null;
  }

  async function runAssessment() {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await labAssess(input(), context));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Assessment failed");
    } finally {
      setBusy(false);
    }
  }

  async function exportReport(format: "json" | "markdown") {
    try {
      const text = await labReport(input(), format);
      downloadText(`airgap-compat-${source}.${format === "json" ? "json" : "md"}`, text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }

  const num = (v: string): number | null => (v.trim() === "" ? null : Number(v));

  return (
    <div className="lab">
      <div className="sim-banner" role="status" aria-live="polite">
        <strong>{SIMULATION_BANNER}</strong>
        <span>Results come from the real planner using synthetic hardware. Nothing is installed or run.</span>
        <button
          type="button"
          onClick={() => {
            setResult(null);
            onExit();
          }}
        >
          Back to real hardware
        </button>
      </div>

      <section className="card">
        <h2>Compatibility Lab</h2>
        <p className="muted">
          Check what the installer would recommend on devices you do not have. Uses the same hardware
          assessment, resource planner and model catalogue as setup.
        </p>
        <div className="lab-form">
          <label>
            Device profile
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {profiles?.profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              <option value="custom">Custom…</option>
            </select>
          </label>
          <label>
            Context length (tokens)
            <select value={context} onChange={(e) => setContext(Number(e.target.value))}>
              {[2048, 4096, 8192, 16384, 32768].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>
        {source !== "custom" && profiles && (
          <p className="muted small">{profiles.profiles.find((p) => p.id === source)?.description}</p>
        )}
        {source === "custom" && (
          <fieldset className="custom-spec">
            <legend>Custom synthetic device</legend>
            <label>
              Operating system
              <select value={custom.os} onChange={(e) => setCustom({ ...custom, os: e.target.value })}>
                <option value="linux">Linux</option>
                <option value="windows">Windows</option>
                <option value="darwin">macOS</option>
                <option value="unknown">Unknown</option>
              </select>
            </label>
            <label>
              Architecture
              <select value={custom.arch} onChange={(e) => setCustom({ ...custom, arch: e.target.value })}>
                <option value="amd64">x86-64</option>
                <option value="arm64">ARM64</option>
                <option value="unknown">Unknown</option>
              </select>
            </label>
            {(
              [
                ["ram_total_gib", "Total RAM (GiB)"],
                ["ram_available_gib", "Free RAM now (GiB)"],
                ["disk_free_gib", "Free disk (GiB)"],
              ] as [Field, string][]
            ).map(([f, label]) => (
              <label key={f}>
                {label}
                <input
                  type="number"
                  min={0}
                  step="0.5"
                  value={custom[f] ?? ""}
                  placeholder="unknown"
                  onChange={(e) => setCustom({ ...custom, [f]: num(e.target.value) })}
                />
              </label>
            ))}
            <label>
              NVIDIA GPU memory (GiB, blank = none)
              <input type="number" min={0} step="1" value={gpuVram} onChange={(e) => setGpuVram(e.target.value)} />
            </label>
            <label className="toggle">
              <input
                type="checkbox"
                checked={(custom.cpu_flags ?? []).includes("avx2")}
                onChange={(e) => setCustom({ ...custom, cpu_flags: e.target.checked ? ["avx2"] : [] })}
              />
              CPU supports AVX2
            </label>
          </fieldset>
        )}
        <div className="row-actions">
          <button type="button" className="primary" disabled={busy || !profiles} onClick={() => void runAssessment()}>
            {busy ? "Assessing…" : "Run assessment"}
          </button>
          <button type="button" disabled={!result} onClick={() => void exportReport("markdown")}>
            Export report (Markdown)
          </button>
          <button type="button" disabled={!result} onClick={() => void exportReport("json")}>
            Export report (JSON)
          </button>
        </div>
        {error && (
          <div className="callout bad" role="alert">
            {error}
          </div>
        )}
      </section>

      {!result && !error && (
        <section className="card empty">
          <p>Choose a profile and run an assessment to see eligible and rejected models with reasons.</p>
        </section>
      )}
      {result && (
        <section className="card" aria-label="Simulated assessment">
          <div className="sim-tag">{SIMULATION_BANNER}</div>
          {result.tier === "unsupported" || result.tier === "insufficient_data" ? (
            <div className="callout bad">
              No model can be recommended for this device. The reasons for each model are listed below.
            </div>
          ) : null}
          <AssessmentView assessment={result} />
        </section>
      )}
    </div>
  );
}
