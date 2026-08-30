import { Activity, Moon, RefreshCw, Sun, Wifi, WifiOff } from "lucide-react";
import { useWorldStore } from "../state/worldStore";

export function TopBar() {
  const cards = useWorldStore((state) => state.cards);
  const edges = useWorldStore((state) => state.edges);
  const syncState = useWorldStore((state) => state.syncState);
  const socketState = useWorldStore((state) => state.socketState);
  const theme = useWorldStore((state) => state.theme);
  const toggleTheme = useWorldStore((state) => state.toggleTheme);
  const toggleActivity = useWorldStore((state) => state.toggleActivity);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const live = socketState === "live";

  return (
    <header className="top-bar">
      <div className="world-identity">
        <div className="world-mark" aria-hidden="true"><span /><i /><i /></div>
        <div><strong>Open Agent World</strong><span>Terrain 01 · local world</span></div>
      </div>

      <div className="topology-summary" aria-label={`${cards.length} objects and ${edges.length} relationships`}>
        <span><strong>{cards.length}</strong> objects</span>
        <i />
        <span><strong>{edges.length}</strong> relationships</span>
      </div>

      <div className="top-actions">
        <button
          type="button"
          className={`connection-status connection-status--${syncState}`}
          onClick={() => void refreshWorld()}
          title="Refresh authoritative world state"
        >
          {syncState === "syncing" || syncState === "loading"
            ? <RefreshCw size={14} className="is-spinning" />
            : syncState === "offline" ? <WifiOff size={14} /> : <Wifi size={14} />}
          <span>{syncState === "online" ? "World synced" : syncState}</span>
        </button>
        <button
          type="button"
          className={`top-icon-button ${live ? "has-live-dot" : ""}`}
          onClick={toggleActivity}
          aria-label={`Open runtime activity; event stream ${live ? "live" : "disconnected"}`}
          title={`Runtime stream: ${socketState}`}
        >
          <Activity size={17} />
        </button>
        <button
          type="button"
          className="top-icon-button"
          onClick={toggleTheme}
          aria-label={`Use ${theme === "light" ? "dark" : "light"} theme`}
          title={`Use ${theme === "light" ? "dark" : "light"} theme`}
        >
          {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </button>
      </div>
    </header>
  );
}
