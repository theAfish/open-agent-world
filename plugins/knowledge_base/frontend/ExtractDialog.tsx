import { Modal, t } from "@oaw/plugin-api";
import { FileText } from "lucide-react";
import { useState } from "react";

type DialogFile = { source_id: string; filename: string; size: number;
  state: "pending" | "converting" | "converted" | "failed"; error: string | null };
type DialogBundle = { id: string; schema_id: string; name: string; notes: string; files: DialogFile[]; records: number };
type DialogSchema = { id: string; name: string };
export type ExtractDetails = { name: string; notes: string; schema_id: string; replace: boolean };

const bytes = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 / 1024).toFixed(1)} MiB`;

/** The one place an upload's metadata is confirmed: dropping files creates the
 * upload with a provisional name, and this window — opened by Extract records, or
 * by Edit details — lets the person settle the name, description and schema before
 * anything is read. The schema is fixed once records exist, since each was validated
 * against it; Re-extract keeps it and offers to replace the drafts instead. */
export function ExtractDialog({ bundle, schemas, mode, busy, canExtract, runHint, blocker, onClose, onConfirm }: {
  bundle: DialogBundle; schemas: DialogSchema[]; mode: "extract" | "edit"; busy: boolean;
  canExtract: boolean; runHint: string; blocker: string; onClose: () => void;
  onConfirm: (details: ExtractDetails) => void;
}) {
  const [name, setName] = useState(bundle.name);
  const [notes, setNotes] = useState(bundle.notes);
  const [schemaId, setSchemaId] = useState(bundle.schema_id);
  const [replace, setReplace] = useState(bundle.records > 0);
  const extracting = mode === "extract";
  const converted = bundle.files.length > 0 && bundle.files.every(file => file.state === "converted");
  const schemaLocked = bundle.records > 0;
  const confirmDisabled = busy || !name.trim() || !schemaId || (extracting && (!canExtract || !converted || !!blocker));
  const submit = () => { if (!confirmDisabled) onConfirm({ name: name.trim(), notes, schema_id: schemaId, replace }); };
  return <Modal title={extracting ? t("Extract records from this upload") : t("Upload details")} busy={busy}
    className="knowledge-modal knowledge-extract-dialog" onClose={onClose}
    actions={<>
      <button type="button" className="knowledge-primary" disabled={confirmDisabled} onClick={submit}>
        {extracting ? (bundle.records > 0 && replace ? t("Re-extract records") : t("Extract records")) : t("Save details")}</button>
      <button type="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button>
    </>}>
    <form className="knowledge-form" onSubmit={event => { event.preventDefault(); submit(); }}>
      <label>{t("Upload name")}
        <input value={name} disabled={busy} data-autofocus placeholder={t("e.g. Batch 7 conductivity")}
          onChange={event => setName(event.target.value)} /></label>
      <label>{t("Description (optional)")}
        <textarea value={notes} disabled={busy} rows={3} placeholder={t("What the files show, units, which specimen a photo is of…")}
          onChange={event => setNotes(event.target.value)} /></label>
      <label>{t("Experiment schema")}
        <select value={schemaId} disabled={busy || schemaLocked} onChange={event => setSchemaId(event.target.value)}>
          {!schemas.some(item => item.id === schemaId) && <option value={schemaId}>{t("Unknown schema")}</option>}
          {schemas.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {schemaLocked && <p className="knowledge-meta">{t("The schema is fixed while this upload has records.")}</p>}
      <div className="knowledge-dialog-files">
        <span className="knowledge-dialog-files-heading">{t("Files")} <span className="knowledge-total">{bundle.files.length}</span></span>
        {!bundle.files.length
          ? <p className="knowledge-empty">{t("No files yet — add some before extracting.")}</p>
          : <ul className="knowledge-list knowledge-bundle-files">
            {bundle.files.map(file => <li key={file.source_id} className="knowledge-bundle-file">
              <FileText size={14} />
              <span className="knowledge-checkrow-label"><span>{file.filename}</span>
                <small>{bytes(file.size)} · {file.state === "converted" ? t("converted") : file.state === "converting" ? t("converting…")
                  : file.state === "failed" ? (file.error || t("conversion failed")) : t("waiting")}</small></span>
            </li>)}
          </ul>}
      </div>
      {extracting && bundle.records > 0 && <label className="knowledge-dialog-check">
        <input type="checkbox" checked={replace} disabled={busy} onChange={event => setReplace(event.target.checked)} />
        <span>{t("Replace the {v0} existing draft record(s) of this upload (published records are kept)", { v0: bundle.records })}</span>
      </label>}
      {extracting && <p className="knowledge-meta">{blocker || (!converted && bundle.files.length
        ? t("Extraction starts once every file is converted.") : runHint)}</p>}
    </form>
  </Modal>;
}
