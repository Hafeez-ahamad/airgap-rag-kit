import { useCallback, useEffect, useRef, useState } from "react";
import { deleteDocument, listDocuments, uploadFiles, type DocumentRow, type UploadItem } from "./api";

const POLL_MS = 2000;

export function DocumentsView() {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [results, setResults] = useState<UploadItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load documents");
    }
  }, []);

  useEffect(() => {
    // First load on the next tick, then poll while the page is open (status updates).
    const first = window.setTimeout(() => void refresh(), 0);
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [refresh]);

  useEffect(() => {
    // webkitdirectory is not in React's input typings; set it directly.
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      setResults(await uploadFiles(Array.from(files)));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="documents">
      <div
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          void upload(e.dataTransfer.files);
        }}
      >
        <p>Drop files here (PDF, DOCX, TXT, MD, CSV, XLSX)</p>
        <label className="button">
          Choose files
          <input type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
        </label>
        <label className="button">
          Choose folder
          <input ref={folderInput} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
        </label>
        {uploading && <p>Uploading…</p>}
      </div>
      {error && <div className="warning">{error}</div>}
      {results.length > 0 && (
        <ul className="upload-results">
          {results.map((r, i) => (
            <li key={i} className={r.outcome}>
              {r.filename}: {r.outcome.replace("_", " ")}
              {r.message ? ` (${r.message})` : ""}
            </li>
          ))}
        </ul>
      )}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Version</th>
            <th>Status</th>
            <th>Chunks</th>
            <th>OCR pages</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>{d.name}</td>
              <td>{d.version}</td>
              <td className={`status ${d.status}`} title={d.error ?? ""}>
                {d.status}
                {d.error ? `: ${d.error}` : ""}
              </td>
              <td>{d.chunk_count}</td>
              <td>{d.ocr_pages.length ? d.ocr_pages.join(", ") : "–"}</td>
              <td>
                <button
                  onClick={() => {
                    if (window.confirm(`Delete ${d.name} and all its versions?`)) {
                      void deleteDocument(d.id).then(refresh);
                    }
                  }}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
