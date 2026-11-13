import { t, useLocale } from "../i18n";
import { type NodeProps } from "@xyflow/react";
import { ExternalLink, X } from "lucide-react";
import { IconButton } from '../components/IconButton';
import { useState } from "react";
import { useHydrationLease } from '../canvas/useCardRendering';
import { createPortal } from "react-dom";
import { useWorldStore } from "../state/worldStore";
import { AddSelectedMembers, ContainerActions, ContainerFrame } from "./ContainerFrame";
import { SkillToolboxBody } from "./SkillToolbox";
import type { CanvasNode } from "./types";

export function SkillContainerNode({ data, selected }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const cards = useWorldStore((state) => state.cards);
  const [editing, setEditing] = useState(false);
  useHydrationLease(card.id, 'toolbox-dialog', editing);
  const members = cards.filter((member) => member.parent_id === card.id);
  return <ContainerFrame card={card} selected={selected} className="skill-container" label={t("{v0} skill space", { v0: String(card.name) })}
    primaryActions={<IconButton icon={ExternalLink} quiet label={t('Edit toolbox')} onClick={() => setEditing(!editing)} />}
    details={<>{members.length} {t("skills")}</>} menuActions={<>
      <AddSelectedMembers card={card} /><ContainerActions card={card} deleteLabel={t("Delete {v0} and skills", { v0: String(card.name) })} />
    </>}>
    <p className="skill-container-hint">{t("Drag skills into this space. Connect its border to use the whole toolbox, or connect a skill to use only that skill.")}</p>
    {editing && createPortal(<div className="skill-container-editor nodrag nopan nowheel" role="dialog" aria-label={t("{v0} workspace", { v0: String(card.name) })}><button className="skill-container-close" aria-label={t("Close toolbox editor")} onClick={() => setEditing(false)}><X size={16} /></button><SkillToolboxBody card={card} workspace showHeading /></div>, document.body)}
  </ContainerFrame>;
}
