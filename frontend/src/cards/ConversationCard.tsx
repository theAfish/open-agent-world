import { t, useLocale } from "../i18n";
import { MessageCircleMore, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { worldApi } from "../api/client";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { RelationshipList } from "./CardUtilities";

export function ConversationCardBody({ card }: { card: WorldCard; level: NodeSurfaceLevel }) {
  useLocale();
  const accessEventId = useWorldStore((state) => state.events.find((event) => {
    if (event.type !== "permission_changed") return false;
    const edge = event.payload.edge as Record<string, unknown> | undefined;
    return edge?.source === card.id || edge?.target === card.id;
  })?.id);
  const eventId = useWorldStore((state) => state.events.find(
    (event) => event.conversation_id === card.id
      && event.type === "conversation_session_created",
  )?.id);
  const [sessionCount, setSessionCount] = useState(0);
  const [agentCount, setAgentCount] = useState(0);

  useEffect(() => {
    let current = true;
    void worldApi.getConversation(card.id).then((summary) => {
      if (current) {
        setSessionCount(summary.sessions.length);
        setAgentCount(summary.agents.filter((agent) => agent.connected).length);
      }
    }).catch(() => undefined);
    return () => { current = false; };
  }, [accessEventId, card.id, eventId]);

  return (
    <div className="expanded-stack">
      <section className="conversation-field-summary">
        <MessageCircleMore size={19} />
        <div>
          <strong>{sessionCount} {t("sessions")}</strong>
          <span>{agentCount} {t("connected agents")}</span>
        </div>
      </section>
      <p className="conversation-field-description">
        {String(card.config.description ?? t("A durable field for human and agent conversations."))}
      </p>
      <section className="card-section">
        <div className="section-heading"><span>{t("Available participants")}</span><small>{t("live graph access")}</small></div>
        <div className="capability-chips">
          {agentCount > 0
            ? <span><Users size={10} /> {agentCount} {t("agents available in the workspace")}</span>
            : <em>{t("Connect an Agent with Participate to make it available here.")}</em>}
        </div>
      </section>
      <section className="card-section">
        <div className="section-heading"><span>{t("Relationships")}</span><small>{t("backend-authoritative")}</small></div>
        <RelationshipList card={card} />
      </section>
    </div>
  );
}
