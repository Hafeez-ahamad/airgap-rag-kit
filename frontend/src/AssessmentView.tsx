import { gib, type Assessment } from "./api";
import { placementLabel } from "./SetupWizard";

const TIER: Record<string, string> = {
  insufficient_data: "Not enough information",
  unsupported: "Not supported",
  constrained: "Constrained",
  balanced: "Balanced",
  gpu_workstation: "GPU workstation",
  high_capacity: "High capacity",
};

export function AssessmentView({ assessment: a, compact = false }: { assessment: Assessment; compact?: boolean }) {
  const r = a.report;
  const rec = a.evaluations.find((e) => e.model_id === a.recommended_model_id);
  return (
    <div className="assessment">
      <dl className="facts">
        <div>
          <dt>System</dt>
          <dd>
            {r.os} {r.os_version ?? ""} · {r.arch}
          </dd>
        </div>
        <div>
          <dt>Processor</dt>
          <dd>
            {r.cpu_model ?? "unknown"}
            {r.cpu_threads ? ` · ${r.cpu_threads} threads` : ""}
          </dd>
        </div>
        <div>
          <dt>Memory</dt>
          <dd>
            {gib(r.ram_total_bytes)} total · {gib(r.ram_available_bytes)} free now
          </dd>
        </div>
        <div>
          <dt>Graphics</dt>
          <dd>
            {r.gpus.length
              ? r.gpus
                  .map((g) => `${g.name}${g.unified_memory ? " (unified memory)" : ` · ${gib(g.vram_total_bytes)}`}`)
                  .join(", ")
              : r.gpu_probe_ok
                ? "No supported GPU found"
                : "Could not be checked"}
          </dd>
        </div>
        <div>
          <dt>Free disk</dt>
          <dd>{gib(r.disk_free_bytes)}</dd>
        </div>
        <div>
          <dt>Runtime</dt>
          <dd>
            {r.runtimes.length === 0
              ? "not checked"
              : r.runtimes
                  .filter((x) => x.name === "ollama")
                  .map((x) => `Ollama ${x.installed ? (x.reachable ? `${x.version ?? ""} running` : "installed, not running") : "not installed"}`)
                  .join("") || "not checked"}
          </dd>
        </div>
      </dl>

      <div className={`tier tier-${a.tier}`}>
        <div>
          <span className="muted small">Device profile</span>
          <strong>{TIER[a.tier] ?? a.tier}</strong>
        </div>
        <div>
          <span className="muted small">Recommended</span>
          <strong>{rec ? rec.display_name : "None"}</strong>
          {rec && <span className="muted small">{placementLabel(rec.placement)}</span>}
        </div>
        <div>
          <span className="muted small">Document Assistant</span>
          <strong>{a.document_assistant_available ? "Available" : "Not available"}</strong>
        </div>
      </div>

      {a.warnings.length > 0 && (
        <ul className="notes warn-list">
          {a.warnings.map((w) => (
            <li key={w.code}>{w.message}</li>
          ))}
        </ul>
      )}

      {!compact || a.evaluations.length > 0 ? (
        <div className="table-wrap">
          <table className="eval-table">
            <caption className="sr-only">Model compatibility</caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">Fits</th>
                <th scope="col">Placement</th>
                <th scope="col">Memory (est.)</th>
                <th scope="col">Download</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              {a.evaluations.map((e) => (
                <tr key={e.model_id} className={e.eligible ? "" : "rejected"}>
                  <td>
                    {e.display_name}
                    <div className="muted small">
                      {e.quantization} · {e.licence}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${e.eligible ? (e.fits_now === false ? "warn" : "good") : "bad"}`}>
                      {e.eligible ? (e.fits_now === false ? "after freeing RAM" : "yes") : "no"}
                    </span>
                  </td>
                  <td>{e.placement ? placementLabel(e.placement) : "–"}</td>
                  <td className="small">
                    {e.estimate
                      ? `${gib(e.estimate.ram_needed_bytes)} RAM${e.estimate.vram_needed_bytes ? ` · ${gib(e.estimate.vram_needed_bytes)} VRAM` : ""}`
                      : "–"}
                  </td>
                  <td>{gib(e.download_bytes)}</td>
                  <td className="small">
                    {[...e.rejections, ...e.warnings.filter((w) => w.code !== "unranked")].map((n) => (
                      <div key={n.code}>{n.message}</div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {!compact && (
        <div className="two-col">
          <section>
            <h4>Budget (estimates)</h4>
            <ul className="small">
              <li>OS and desktop reserve: {gib(a.budget.os_reserve_bytes)}</li>
              <li>App services: {gib(a.budget.services_bytes)}</li>
              <li>Search models: {gib(a.budget.retrieval_bytes)}</li>
              <li>Left for the model (RAM): {gib(a.budget.llm_ram_budget_bytes)}</li>
              <li>GPU memory usable: {gib(a.budget.gpu_usable_bytes)}</li>
              {a.settings && (
                <li>
                  Settings: context {a.settings.num_ctx}, parallel {a.settings.num_parallel}, ingestion batch{" "}
                  {a.settings.ingestion_batch_size}
                </li>
              )}
            </ul>
          </section>
          <section>
            <h4>Limitations and assumptions</h4>
            <ul className="small">
              {r.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
              {a.assumptions.map((l) => (
                <li key={l} className="muted">
                  {l}
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
      <p className="muted small">
        Estimates from catalogue {a.catalog_version}. A model is only confirmed after a real test on the
        device.
      </p>
    </div>
  );
}
