import { BarracksContainerNode } from "./Barracks";
import { ShadowCollectionNode } from "./ShadowCollection";
import type { NodeProps } from "@xyflow/react";
import { Boxes } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import { ContainerActions, ContainerFrame, AddSelectedMembers } from "./ContainerFrame";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { PluginSurface } from "../plugins/PluginSurface";
import { LegionCardNode } from "./LegionCard";
import { SkillContainerNode } from "./SkillContainer";
import type { CanvasNode } from "./types";
import { containerShowsWorkspace } from "../state/containers";

export function ContainerCardNode(props: NodeProps<CanvasNode>) {
  const level = useNodeSurfaceStore((state) => state.surfaceLevels[props.data.card.id]);
  const setWorkspace = (open: boolean) => open ? useNodeSurfaceStore.getState().openWorkspace(props.data.card.id) : useNodeSurfaceStore.getState().closeWorkspace(props.data.card.id);
  const catalog = useWorldStore((state) => state.catalog);
  if(props.data.card.type==="core.shadow-collection")return <ShadowCollectionNode {...props}/>;
  const card = props.data.card;
  const definition = catalog.node_types.find((type) => type.id === card.type)!;
  const workspace = containerShowsWorkspace(card, catalog, level);
  const workspaceOnly = definition.container?.member_display === "workspace";
  if (definition.traits.includes("ui.agent-barracks.v1")) return <BarracksContainerNode {...props} />;
  if (definition.traits.includes("ui.legion.v1")) return <LegionCardNode {...props} />;
  if (definition.traits.includes("ui.skill-package.v1")) return <SkillContainerNode {...props} />;
  return <ContainerFrame card={card} selected={props.selected} className="skill-container" label={`${card.name} container`} header={<><Boxes size={24} /><div><span>{definition.label}</span><strong>{card.name}</strong></div>{definition.frontend?.workspace && !workspaceOnly && <button className="secondary-button nodrag nopan" aria-pressed={workspace} onClick={() => setWorkspace(!workspace)}>{workspace ? "Show member cards" : "Open workspace"}</button>}{!workspaceOnly && <AddSelectedMembers card={card} />}<ContainerActions card={card} /></>}>
    {!workspace && <PluginSurface card={card} slot="body" level="inspector"><p className="skill-container-hint">Drag cards into or out of this space. Each member keeps its own connections.</p></PluginSurface>}
    {workspace && <div className="container-plugin-workspace nodrag nopan nowheel" role="region" aria-label={`${card.name} workspace`}><PluginSurface card={card} slot="workspace" level="workspace" /></div>}
  </ContainerFrame>;
}
