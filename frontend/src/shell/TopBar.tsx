import { AppearanceButtons, SettingsButton } from './PreferenceButtons';
import { Activity, Compass, LibraryBig, RefreshCw, Wifi, WifiOff, Plus } from "lucide-react";
import { useLocale, t } from "../i18n";
import { tutorial, useTutorialStore } from '../onboarding/controller';
import { useCardLibrary } from "../state/cardLibrary";
import { useWorldStore } from "../state/worldStore";

export function TopBar() {
  useLocale();
  const tutorialBusy = useTutorialStore(state => state.busy);
  const tutorialSession = useTutorialStore(state => state.session);
  const cards = useWorldStore((state) => state.cards);
  const edges = useWorldStore((state) => state.edges);
  const syncState = useWorldStore((state) => state.syncState);
  const socketState = useWorldStore((state) => state.socketState);
  const toggleActivity = useWorldStore((state) => state.toggleActivity);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const live = socketState === "live";
  const syncLabel = syncState === "online"
    ? t("Synced")
    : syncState === "syncing" || syncState === "loading"
      ? t("Syncing")
      : t("Offline");

  return (
    <aside className="top-bar" aria-label={t("World status and controls")}>
      <div className="world-status">
        <button
          type="button"
          className={`connection-status connection-status--${syncState}`}
          onClick={() => void refreshWorld()}
          title={t("Refresh authoritative world state")}
        >
          {syncState === "syncing" || syncState === "loading"
            ? <RefreshCw size={13} className="is-spinning" />
            : syncState === "offline" ? <WifiOff size={13} /> : <Wifi size={13} />}
          <span>{syncLabel}</span>
        </button>
        <div className="topology-summary" aria-label={t('{cards} cards and {links} relationships', { cards: cards.length, links: edges.length })}>
          <span><strong>{cards.length}</strong> {t("cards")}</span>
          <i />
          <span><strong>{edges.length}</strong> {t("links")}</span>
        </div>
      </div>

      <div className="top-actions">
        <button type="button" className="top-icon-button" disabled={tutorialBusy || !!tutorialSession} onClick={() => tutorial.chooseWorkspace()} aria-label={t('New workspace')} title={t('New workspace')}><Plus size={16} /></button>
        <button type="button" className="top-icon-button" data-tutorial="library" onClick={useCardLibrary.getState().show} aria-label={t("Open Pack and Card Library")} title={t("Packs, Cards and Decks")}><LibraryBig size={16} /></button>
        <SettingsButton />
        <button
          type="button"
          className={`top-icon-button ${live ? "has-live-dot" : ""}`}
          onClick={toggleActivity}
          aria-label={t('Open runtime activity; event stream {status}', { status: t(live ? 'live' : 'disconnected') })}
          title={t('Runtime stream: {status}', { status: t(socketState) })}
        >
          <Activity size={16} />
        </button>
        <AppearanceButtons />
        <button type="button" className="top-icon-button" disabled={tutorialBusy} onClick={() => void tutorial.replay()} aria-label={t("Replay Tutorial")} title={t("Replay Tutorial")}><Compass size={16} /></button>
      </div>
    </aside>
  );
}
