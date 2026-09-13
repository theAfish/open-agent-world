import { t, useLocale } from "../i18n";
import { type NodeProps } from "@xyflow/react";
import { Boxes, ExternalLink, X } from "lucide-react";
import { useState } from "react";
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
  const members = cards.filter((member) => member.parent_id === card.id);
  return <ContainerFrame card={card} selected={selected} className="skill-container" label={t("{v0} skill space", { v0: String(card.name) })} header={<>
    <Boxes size={24} /><div><span>{t("SKILL TOOLBOX · OPEN SPACE")}</span><strong>{card.name}</strong></div>
      <span>{members.length} {t("skills")}</span><button className="secondary-button nodrag nopan" onClick={() => setEditing(!editing)}><ExternalLink size={14} /> {t("Edit toolbox")}</button>
      <AddSelectedMembers card={card} /><ContainerActions card={card} deleteLabel={t("Delete {v0} and skills", { v0: String(card.name) })} />
    </>}>
    <p className="skill-container-hint">{t("Drag skills into this space. Connect its border to use the whole toolbox, or connect a skill to use only that skill.")}</p>
    {editing && createPortal(<div className="skill-container-editor nodrag nopan nowheel" role="dialog" aria-label={t("{v0} workspace", { v0: String(card.name) })}><button className="skill-container-close" aria-label={t("Close toolbox editor")} onClick={() => setEditing(false)}><X size={16} /></button><SkillToolboxBody card={card} workspace /></div>, document.body)}
  </ContainerFrame>;
}
