import { t, useLocale } from "../i18n";
import { Activity, CircleDot, Radio, X } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import type { RuntimeEvent } from "../types/world";
import { LifecycleStatus } from "../cards/LifecycleStatus";

function eventLabel(type: string): string {
  return type.replace(/[._-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function eventSubject(event: RuntimeEvent, cardNames: Map<string, string>): string {
  const id = event.node_id ?? event.agent_id ?? event.sandbox_id ?? event.resource_id;
  return id ? (cardNames.get(id) ?? id.slice(0, 8)) : t("World runtime");
}

export function ActivityPanel() {
  useLocale();
  const open = useWorldStore((state) => state.activityOpen);
  const events = useWorldStore((state) => state.events);
  const cards = useWorldStore((state) => state.cards);
  const socketState = useWorldStore((state) => state.socketState);
  const setOpen = useWorldStore((state) => state.setActivityOpen);
  const cardNames = new Map(cards.map((card) => [card.id, card.name]));

  return (
    <aside className={`activity-panel ${open ? "is-open" : ""}`} aria-hidden={!open} aria-label={t("Runtime activity")}>
      <header>
        <div className="activity-title">
          <span className="activity-icon"><Activity size={16} /></span>
          <div><strong>{t("Runtime activity")}</strong><span>{t("Operational events only")}</span></div>
        </div>
        <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label={t("Close runtime activity")}>
          <X size={16} />
        </button>
      </header>

      <div className={`stream-status stream-status--${socketState}`}>
        <Radio size={13} />
        <span>{socketState === "live" ? t("Event stream live") : socketState === "connecting" ? t("Connecting to event stream") : t("Event stream offline")}</span>
      </div>

      <div className="event-list" role="log" aria-live="polite">
        {open && <LifecycleStatus />}
        {events.length > 0 ? events.map((event) => (
          <article key={event.id} className={event.type.toLowerCase().includes("error") ? "is-error" : ""}>
            <div className="event-rail"><CircleDot size={12} /><i /></div>
            <div className="event-copy">
              <div><strong>{eventLabel(event.type)}</strong><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div>
              <span>{eventSubject(event, cardNames)}</span>
              {event.message ? <p>{event.message}</p> : null}
            </div>
          </article>
        )) : (
          <div className="activity-empty">
            <span><Activity size={22} /></span>
            <strong>{t("The instruments are quiet")}</strong>
            <p>{t("Agent runs, scoped tools, sandbox commands, resource changes, and errors will appear here.")}</p>
          </div>
        )}
      </div>
    </aside>
  );
}
