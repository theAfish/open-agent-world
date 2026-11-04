import { AppearanceButtons, SettingsButton } from './PreferenceButtons';
import { Activity, LibraryBig, RefreshCw, Wifi, WifiOff, Menu, X, LayoutTemplate } from "lucide-react";
import { useEffect, useId, useRef, useState } from 'react';
import { useInteractionEnvironment } from '../interaction/environment';
import { Popover } from '../components/Floating';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { useLocale, t } from "../i18n";
import { HelpMenu } from './HelpMenu';
import { useCardLibrary } from "../state/cardLibrary";
import { useWorldStore } from "../state/worldStore";
import { AccountButton } from '../auth/AccountButton';

export function TopBar() {
  useLocale();
  const compact = useInteractionEnvironment(state => state.compact);
  const [expanded, setExpanded] = useState(false);
  const root = useRef<HTMLElement>(null);
  const actionsId = useId();
  const mobile = compact;
  useEffect(() => {
    if (!mobile || !expanded) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setExpanded(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape); };
  }, [mobile, expanded]);
  const cards = useWorldStore((state) => state.cards);
  const workspaces = cards.filter(card => card.type === 'legion');
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
    <aside ref={root} className="top-bar" data-mobile-menu={mobile ? String(expanded) : undefined} aria-label={t("World status and controls")}>
      {mobile && <button className="top-icon-button world-menu-toggle" type="button" aria-label={t(expanded ? 'Hide world controls' : 'World menu')}
        aria-expanded={expanded} aria-controls={actionsId} onClick={() => setExpanded(value => !value)}>{expanded ? <X size={20} /> : <Menu size={20} />}</button>}
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

      <div id={actionsId} className="top-actions">
        <AccountButton />
        {mobile && workspaces.length > 0 && <Popover label={t('Workspaces')} placement="right" className="mobile-workspace-menu"
          trigger={<button className="top-icon-button" type="button" aria-label={t('Workspaces')} title={t('Workspaces')}><LayoutTemplate size={18} /></button>}>
          {close => <nav aria-label={t('Workspaces')}>{workspaces.map(workspace => <button key={workspace.id} type="button" onClick={() => {
            close(); setExpanded(false); useLegionWorkspace.getState().open(workspace.id);
          }}>{workspace.name}</button>)}</nav>}
        </Popover>}
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
        <HelpMenu />
      </div>
    </aside>
  );
}
