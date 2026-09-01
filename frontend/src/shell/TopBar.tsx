import { Activity, Moon, Redo2, RefreshCw, Settings2, Sun, Undo2, Wifi, WifiOff } from "lucide-react";
import { useWorldStore } from "../state/worldStore";

export function TopBar() {
  const cards = useWorldStore((state) => state.cards);
  const edges = useWorldStore((state) => state.edges);
  const syncState = useWorldStore((state) => state.syncState);
  const socketState = useWorldStore((state) => state.socketState);
  const theme = useWorldStore((state) => state.theme);
  const toggleTheme = useWorldStore((state) => state.toggleTheme);
  const toggleActivity = useWorldStore((state) => state.toggleActivity);
  const toggleSettings = useWorldStore((state) => state.toggleSettings);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const undoStack = useWorldStore((state) => state.undoStack);
  const redoStack = useWorldStore((state) => state.redoStack);
  const historyBusy = useWorldStore((state) => state.historyBusy);
  const undo = useWorldStore((state) => state.undo);
  const redo = useWorldStore((state) => state.redo);
  const live = socketState === "live";
  const syncLabel = syncState === "online"
    ? "Synced"
    : syncState === "syncing" || syncState === "loading"
      ? "Syncing"
      : "Offline";

  return (
    <aside className="top-bar" aria-label="World status and controls">
      <div className="world-status">
        <button
          type="button"
          className={`connection-status connection-status--${syncState}`}
          onClick={() => void refreshWorld()}
          title="Refresh authoritative world state"
        >
          {syncState === "syncing" || syncState === "loading"
            ? <RefreshCw size={13} className="is-spinning" />
            : syncState === "offline" ? <WifiOff size={13} /> : <Wifi size={13} />}
          <span>{syncLabel}</span>
        </button>
        <div className="topology-summary" aria-label={`${cards.length} cards and ${edges.length} relationships`}>
          <span><strong>{cards.length}</strong> cards</span>
          <i />
          <span><strong>{edges.length}</strong> links</span>
        </div>
      </div>

      <div className="top-actions">
        <button type="button" className="top-icon-button" onClick={toggleSettings} aria-label="Open ADK model settings" title="ADK model settings">
          <Settings2 size={16} />
        </button>
        <button
          type="button"
          className="top-icon-button"
          onClick={() => void undo()}
          disabled={historyBusy || undoStack.length === 0}
          aria-label="Undo last canvas action"
          title={undoStack.at(-1) ? `Undo: ${undoStack.at(-1)?.label} (Ctrl+Z)` : "Nothing to undo"}
        >
          <Undo2 size={16} />
        </button>
        <button
          type="button"
          className="top-icon-button"
          onClick={() => void redo()}
          disabled={historyBusy || redoStack.length === 0}
          aria-label="Redo last canvas action"
          title={redoStack.at(-1) ? `Redo: ${redoStack.at(-1)?.label} (Ctrl+Y)` : "Nothing to redo"}
        >
          <Redo2 size={16} />
        </button>
        <button
          type="button"
          className={`top-icon-button ${live ? "has-live-dot" : ""}`}
          onClick={toggleActivity}
          aria-label={`Open runtime activity; event stream ${live ? "live" : "disconnected"}`}
          title={`Runtime stream: ${socketState}`}
        >
          <Activity size={16} />
        </button>
        <button
          type="button"
          className="top-icon-button"
          onClick={toggleTheme}
          aria-label={`Use ${theme === "light" ? "dark" : "light"} theme`}
          title={`Use ${theme === "light" ? "dark" : "light"} theme`}
        >
          {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
        </button>
      </div>
    </aside>
  );
}
