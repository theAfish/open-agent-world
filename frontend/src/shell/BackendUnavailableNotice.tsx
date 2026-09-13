import { t, useLocale } from "../i18n";
import { RefreshCw, ServerOff } from "lucide-react";
import { useWorldStore } from "../state/worldStore";

export function BackendUnavailableNotice() {
  useLocale();
  const syncState = useWorldStore((state) => state.syncState);
  const syncError = useWorldStore((state) => state.syncError);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);

  if (syncState !== "offline") return null;

  return (
    <aside className="backend-unavailable" role="alert">
      <ServerOff size={17} aria-hidden="true" />
      <div>
        <strong>{t("Local backend unavailable")}</strong>
        <p>{t("Cannot reach the local backend. Start")} <code>./scripts/dev.ps1</code> {t("in a terminal, then retry.")}</p>
        {syncError ? <small>{syncError}</small> : null}
      </div>
      <button type="button" onClick={() => void refreshWorld()} title={t("Retry backend connection")}>
        <RefreshCw size={15} aria-hidden="true" /> {t("Retry")} </button>
    </aside>
  );
}
