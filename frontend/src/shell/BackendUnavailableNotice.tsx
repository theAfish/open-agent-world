import { RefreshCw, ServerOff } from "lucide-react";
import { useWorldStore } from "../state/worldStore";

export function BackendUnavailableNotice() {
  const syncState = useWorldStore((state) => state.syncState);
  const syncError = useWorldStore((state) => state.syncError);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);

  if (syncState !== "offline") return null;

  return (
    <aside className="backend-unavailable" role="alert">
      <ServerOff size={17} aria-hidden="true" />
      <div>
        <strong>Local backend unavailable</strong>
        <p>Cannot reach <code>127.0.0.1:8000</code>. Start <code>./scripts/dev.ps1</code> in a terminal, then retry.</p>
        {syncError ? <small>{syncError}</small> : null}
      </div>
      <button type="button" onClick={() => void refreshWorld()} title="Retry backend connection">
        <RefreshCw size={15} aria-hidden="true" /> Retry
      </button>
    </aside>
  );
}
