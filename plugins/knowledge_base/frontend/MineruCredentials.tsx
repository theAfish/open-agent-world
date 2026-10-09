import { useEffect, useState } from "react";
import { t } from "@oaw/plugin-api";
import { deploymentApiBase } from "../../../frontend/src/deployment/api";

type Status = { configured: boolean; source: "card" | "external" | null };

export function MineruCredentials({ cardId, onSaved }: { cardId: string; onSaved: () => Promise<void> }) {
  const [status, setStatus] = useState<Status>();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = `${deploymentApiBase}/knowledge/${encodeURIComponent(cardId)}/mineru-token`;

  useEffect(() => {
    const controller = new AbortController();
    setStatus(undefined); setValue(""); setError("");
    fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(t("Could not load token status"));
      const data = await response.json() as Status;
      if (!controller.signal.aborted) setStatus(data);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)); });
    return () => controller.abort();
  }, [url]);

  const save = async (token: string | null) => {
    setBusy(true); setError("");
    try {
      const response = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: token }) });
      if (!response.ok) throw new Error(t("Could not save token"));
      setStatus(await response.json() as Status);
      setValue("");
      await onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Could not save token")); }
    finally { setBusy(false); }
  };

  return <div className="knowledge-credential">
    <label>{t("MinerU token")}<span className="knowledge-secret-label">{t("Secret")}</span>
      <input type="password" aria-label={t("MinerU token")} autoComplete="new-password" value={value} disabled={busy}
        placeholder={status?.configured ? t("Saved · leave blank to keep") : t("Enter token")}
        onChange={event => setValue(event.target.value)} />
    </label>
    <div className="knowledge-formbar">
      <button type="button" disabled={busy || !value.trim()} onClick={() => void save(value)}>{t("Save token")}</button>
      {status?.source === "card" && <button type="button" disabled={busy} onClick={() => void save(null)}>{t("Remove token")}</button>}
      <span className="knowledge-meta" role="status">{status?.source === "card" ? t("Saved securely")
        : status?.source === "external" ? t("Using external settings") : ""}</span>
    </div>
    <small>{t("Falls back to OAW_MINERU_TOKEN in global settings or the server environment.")}</small>
    {error && <p role="alert" className="knowledge-error">{error}</p>}
  </div>;
}
