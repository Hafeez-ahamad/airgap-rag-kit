import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteDocument,
  listDocuments,
  setDocumentAcl,
  uploadFiles,
  type DocumentRow,
  type Me,
  type UploadItem,
} from "./api";

const POLL_MS = 2000;
const STATUS: Record<DocumentRow["status"], string> = {
  queued: "Queued",
  parsing: "Reading",
  embedding: "Indexing",
  ready: "Ready",
  failed: "Failed",
};
const OUTCOME: Record<UploadItem["outcome"], string> = {
  queued: "added",
  new_version: "new version",
  unchanged: "unchanged (already up to date)",
  duplicate: "duplicate",
  rejected: "rejected",
};

export function DocumentsView({ me }: { me: Me }) {
  const canEdit = me.role !== "viewer";
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [results, setResults] = useState<UploadItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [group, setGroup] = useState(me.groups[0] ?? "default");
  const [dragging, setDragging] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load documents");
    }
  }, []);

  useEffect(() => {
    const first = window.setTimeout(() => void refresh(), 0);
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [refresh]);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      setResults(await uploadFiles(Array.from(files), [group]));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function share(d: DocumentRow) {
    const value = window.prompt(
      `Groups that can read “${d.name}” (comma separated). Your groups: ${me.groups.join(", ")}`,
      d.acl_groups.join(", "),
    );
    if (value === null) return;
    const groups = value.split(",").map((g) => g.trim()).filter(Boolean);
    try {
      await setDocumentAcl(d.id, groups);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change sharing");
    }
  }

  return (
    <div className="page documents">
      <h2>Document library</h2>
      <p className="muted">
        Supported: PDF (including scans, when OCR is installed), Word (.docx), text, Markdown, CSV and
        Excel (.xlsx), up to 50 MB each. Files are checked, stored on this computer and indexed for the
        Document Assistant.
      </p>
      {canEdit ? (
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void upload(e.dataTransfer.files);
          }}
        >
          <p>Drop files here, or</p>
          <label className="button">
            Choose files
            <input type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
          </label>
          <label className="button">
            Choose folder
            <input ref={folderInput} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
          </label>
          <label className="inline">
            Share with
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              {me.groups.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
          </label>
          {uploading && <p className="muted">Uploading…</p>}
        </div>
      ) : (
        <p className="callout">You can read and ask about these documents. Ask an editor to add new ones.</p>
      )}
      {error && (
        <div className="callout bad" role="alert">
          {error}
        </div>
      )}
      {results.length > 0 && (
        <ul className="upload-results" aria-live="polite">
          {results.map((r, i) => (
            <li key={i} className={r.outcome}>
              <strong>{r.filename}</strong>: {OUTCOME[r.outcome]}
              {r.message ? ` — ${r.message}` : ""}
            </li>
          ))}
        </ul>
      )}
      <div className="table-wrap card">
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Status</th>
              <th scope="col">Version</th>
              <th scope="col">Passages</th>
              <th scope="col">Shared with</th>
              {canEdit && <th scope="col">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {docs === null && (
              <tr>
                <td colSpan={6} className="muted">
                  Loading…
                </td>
              </tr>
            )}
            {docs?.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No documents yet.
                </td>
              </tr>
            )}
            {docs?.map((d) => (
              <tr key={d.id}>
                <td>
                  {d.name}
                  {d.ocr_pages.length > 0 && <div className="muted small">OCR on pages {d.ocr_pages.join(", ")}</div>}
                </td>
                <td>
                  <span className={`badge ${d.status === "ready" ? "good" : d.status === "failed" ? "bad" : "warn"}`}>
                    {STATUS[d.status]}
                  </span>
                  {d.error && <div className="small bad-text">{d.error}</div>}
                </td>
                <td>{d.version}</td>
                <td>{d.chunk_count}</td>
                <td>{d.acl_groups.join(", ")}</td>
                {canEdit && (
                  <td className="row-actions">
                    <button type="button" className="small" onClick={() => void share(d)}>
                      Sharing
                    </button>
                    <button
                      type="button"
                      className="small danger"
                      onClick={() => {
                        if (window.confirm(`Delete “${d.name}”, all its versions and its index entries?`)) {
                          void deleteDocument(d.id).then(refresh);
                        }
                      }}
                    >
                      Delete
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
