import { t, useLocale } from "../i18n";
import { BarracksContainerNode } from "./Barracks";
import { ShadowCollectionNode } from "./ShadowCollection";
import type { NodeProps } from "@xyflow/react";
import { PanelsTopLeft } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import { ContainerActions, ContainerFrame, AddSelectedMembers } from "./ContainerFrame";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { PluginSurface } from "../plugins/PluginSurface";
import { LegionCardNode } from "./LegionCard";
import { SkillContainerNode } from "./SkillContainer";
import type { CanvasNode } from "./types";
import { containerShowsWorkspace } from "../state/containers";
import { WorldCardNode } from "./CardFrame";
import { IconButton } from '../components/IconButton';

export function ContainerCardNode(props: NodeProps<CanvasNode>) {
  useLocale();
  const level = useNodeSurfaceStore((state) => state.surfaceLevels[props.data.card.id]);
  const setWorkspace = (open: boolean) => open ? useNodeSurfaceStore.getState().openWorkspace(props.data.card.id) : useNodeSurfaceStore.getState().closeWorkspace(props.data.card.id);
  const catalog = useWorldStore((state) => state.catalog);
  if(props.data.card.type==="core.shadow-collection")return <ShadowCollectionNode {...props}/>;
  const card = props.data.card;
  const definition = catalog.node_types.find((type) => type.id === card.type);
  // The catalog can refresh before React Flow replaces its existing container
  // node during Pack changes. Use the normal missing-card surface in that gap.
  if (!definition?.container || card.missing_plugin) return <WorldCardNode {...props} />;
  const workspace = containerShowsWorkspace(card, catalog, level);
  const workspaceOnly = definition.container?.member_display === "workspace";
  if (definition.traits.includes("ui.agent-barracks.v1")) return <BarracksContainerNode {...props} />;
  if (definition.traits.includes("ui.legion.v1")) return <LegionCardNode {...props} />;
  if (definition.traits.includes("ui.skill-package.v1")) return <SkillContainerNode {...props} />;
  return <ContainerFrame card={card} selected={props.selected} className="skill-container" label={t("{v0} container", { v0: String(card.name) })}
    primaryActions={definition.frontend?.workspace && !workspaceOnly && <IconButton icon={PanelsTopLeft} quiet
      label={workspace ? t("Show member cards") : t("Open workspace")} aria-pressed={workspace} onClick={() => setWorkspace(!workspace)} />}
    menuActions={<>{!workspaceOnly && <AddSelectedMembers card={card} />}<ContainerActions card={card} /></>}>
    {!workspace && <PluginSurface card={card} slot="body" level="inspector"><p className="skill-container-hint">{t("Drag cards into or out of this space. Each member keeps its own connections.")}</p></PluginSurface>}
    {workspace && <div className="container-plugin-workspace nodrag nopan nowheel" role="region" aria-label={t("{v0} workspace", { v0: String(card.name) })}><PluginSurface card={card} slot="workspace" level="workspace" /></div>}
  </ContainerFrame>;
}
