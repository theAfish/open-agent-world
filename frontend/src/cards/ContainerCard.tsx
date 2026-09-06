import { BarracksContainerNode } from "./Barracks";
import type { NodeProps } from "@xyflow/react";
import { Boxes } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import { ContainerActions, ContainerFrame, AddSelectedMembers } from "./ContainerFrame";
import { LegionCardNode } from "./LegionCard";
import { SkillContainerNode } from "./SkillContainer";
import type { CanvasNode } from "./types";

export function ContainerCardNode(props: NodeProps<CanvasNode>) {
  const catalog = useWorldStore((state) => state.catalog);
  const card = props.data.card;
  const definition = catalog.node_types.find((type) => type.id === card.type)!;
  if (definition.traits.includes("ui.agent-barracks.v1")) return <BarracksContainerNode {...props} />;
  if (definition.traits.includes("ui.legion.v1")) return <LegionCardNode {...props} />;
  if (definition.traits.includes("ui.skill-package.v1")) return <SkillContainerNode {...props} />;
  return <ContainerFrame card={card} selected={props.selected} className="skill-container" label={`${card.name} container`} header={<><Boxes size={24} /><div><span>{definition.label}</span><strong>{card.name}</strong></div><AddSelectedMembers card={card} /><ContainerActions card={card} /></>}>
    <p className="skill-container-hint">Drag cards into or out of this space. Each member keeps its own connections.</p>
  </ContainerFrame>;
}
