import { t, useLocale } from "../i18n";
import { EquipmentToggle } from "./Equipment";
import { ExecutionConfigurationBody } from "./ExecutionConfiguration";
import { BarracksBody } from "./Barracks";
import { Handle, NodeResizeControl, Position, type NodeProps } from "@xyflow/react";
import { BookOpen, Maximize2, Minus, ExternalLink, Trash2, X } from "lucide-react";
import { memo, type ComponentType, type CSSProperties, type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";
import { ConnectionHoverHint, clearConnectionHoverHint, updateConnectionHoverHint } from "./ConnectionHoverHint";
import { IconButton } from "../components/IconButton";
import { WORKSPACE_MIN_SIZE, nodeSurfaceSupport, surfaceLevelForNode, useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { type CardType, type WorldCard } from "../types/world";
import { TaskBoardBody } from "./TaskBoard";
import { SkillToolboxBody, SkillNodeBody } from "./SkillToolbox";
import { AgentCardBody } from "./AgentCard";
import { ConversationCardBody } from "./ConversationCard";
import { ImageCardBody } from "./ImageCard";
import { NodePreview } from "./NodePreview";
import { WorkspaceSurface } from "./NodeWorkspace";
import { SandboxCardBody } from "./SandboxCard";
import { TextCardBody } from "./TextCard";
import { RelationshipList } from "./CardUtilities";
import type { CanvasNode } from "./types";
import { ActivityGlow } from "../effects/ActivityGlow";
import { useNodeActivity } from "../effects/useNodeActivity";
import { useNodeGeneration } from "../effects/generation";
import { PluginSurface } from "../plugins/PluginSurface";
import { CatalogIcon } from "../components/CatalogIcon";
import { useCollectionHover } from "../state/shadowCollection";

const DRAG_THRESHOLD_PX = 5;
const NON_DRAG_SELECTOR = "button, input, textarea, select, label, a, summary, [role='button'], [role='separator'], [contenteditable='true'], .react-flow__handle";

// Element boxes include padding and empty line space. Only rendered text should
// take a mouse gesture away from dragging the surrounding inspector.
function hitsText(target: Element, x: number, y: number): boolean {
  const range = document.createRange();
  return Array.from(target.childNodes).some(node => {
    if (node.nodeType !== Node.TEXT_NODE || !node.textContent?.trim()) return false;
    range.selectNodeContents(node);
    return Array.from(range.getClientRects()).some(rect =>
      x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom);
  });
}

interface BodyProps { card: WorldCard; level: NodeSurfaceLevel }

const BODIES: Partial<Record<CardType, ComponentType<BodyProps>>> = {
  agent: AgentCardBody,
  conversation: ConversationCardBody,
  text: TextCardBody,
  image: ImageCardBody,
  sandbox: SandboxCardBody,
};

function GenericCardBody({ card }: BodyProps) {
  useLocale();
  const entries = Object.entries(card.config).filter(([, value]) => (
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  ));
  return (
    <div className="expanded-stack">
      <section className="card-section">
        <div className="section-heading"><span>{t("Plugin configuration")}</span><small>{t("catalog-driven")}</small></div>
        {entries.length > 0 ? (
          <dl className="plugin-config-list">
            {entries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}
          </dl>
        ) : <div className="mini-empty"><span>{t("No public configuration fields.")}</span></div>}
      </section>
      <section className="card-section">
        <div className="section-heading"><span>{t("Relationships")}</span><small>{t("backend-authoritative")}</small></div>
        <RelationshipList card={card} />
      </section>
    </div>
  );
}

export function CardContent({ card, level }: BodyProps) {
  useLocale();
  const catalog = useWorldStore((s) => s.catalog);
  const definition = catalog.node_types.find((t) => t.id === card.type);
  const Body = definition?.traits.includes("ui.agent-barracks.v1") ? BarracksBody : definition?.traits.includes("ui.skill.v1") ? SkillNodeBody : definition?.traits.includes("ui.skill-package.v1") ? SkillToolboxBody : definition?.traits.includes("ui.task-board.v1") ? TaskBoardBody : definition?.traits.includes("core.agent") ? AgentCardBody : BODIES[card.type] ?? GenericCardBody;
  return <PluginSurface card={card} slot="body" level={level}>{definition?.traits.includes("ui.execution-config.v1")
    ? <ExecutionConfigurationBody key={card.id} card={card} /> : <Body card={card} level={level} />}</PluginSurface>;
}

function statusLabel(status: WorldCard["status"]): string {
  return status.replaceAll("_", " ");
}

function WorldCardNodeComponent({ data, selected, dragging }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const activity = useNodeActivity(card);
  const generation = useNodeGeneration(card.id);
  const generationPhase = generation?.targetId === card.id ? generation.phase : undefined;
  const displayStatus = activity.phase === "idle" ? card.status : activity.phase;
  const catalog = useWorldStore((state) => state.catalog);
  const surfaceLevels = useNodeSurfaceStore((state) => state.surfaceLevels);
  const showPreview = useNodeSurfaceStore((state) => state.showPreview);
  const hidePreview = useNodeSurfaceStore((state) => state.hidePreview);
  const openInspector = useNodeSurfaceStore((state) => state.openInspector);
  const closeInspector = useNodeSurfaceStore((state) => state.closeInspector);
  const dismissSurface = useNodeSurfaceStore((state) => state.dismiss);
  const openWorkspace = useNodeSurfaceStore((state) => state.openWorkspace);
  const resizeWorkspace = useNodeSurfaceStore((state) => state.resizeWorkspace);
  const updateCard = useWorldStore((state) => state.updateCard);
  const deleteCard = useWorldStore((state) => state.deleteCard);
  const connectingNodeId = useNodeSurfaceStore((state) => state.connectingNodeId);
  const cardRef = useRef<HTMLElement>(null);
  const pointerStart = useRef<{ x: number; y: number; moved: boolean }>();
  const level = surfaceLevelForNode(card.id, surfaceLevels);
  const visualLevel = level;
  const definition = catalog.node_types.find((item) => item.id === card.type);
  const label = t(definition?.label ?? card.type);

  const support = nodeSurfaceSupport(card.type, catalog);

  useEffect(() => {
    if (dragging && pointerStart.current) pointerStart.current.moved = true;
  }, [dragging]);

  useEffect(() => {
    if (connectingNodeId === card.id) clearConnectionHoverHint(cardRef.current);
  }, [card.id, connectingNodeId]);

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (card.ephemeral || connectingNodeId === card.id) return;
    updateConnectionHoverHint(event, cardRef.current);
  };

  const onPointerDownCapture = (event: ReactPointerEvent<HTMLElement>) => {
    pointerStart.current = { x: event.clientX, y: event.clientY, moved: false };
  };

  return (
    <article
      ref={cardRef}
      className={`world-card node-surface world-card--${card.type} is-${visualLevel} ${selected ? "is-selected" : ""} ${card.status === "running" ? "is-running" : ""} ${card.status === "error" ? "is-error" : ""} ${card.ephemeral ? "is-ephemeral" : ""}`}
      style={{ "--card-kind": definition?.color } as CSSProperties}
      aria-label={`${label} ${card.name}`}
      data-card-id={card.id}
      data-card-type={card.type}
      data-card-expanded={visualLevel === "inspector" || visualLevel === "workspace" ? "true" : "false"}
      data-surface-level={level}
      data-activity={activity.phase}
      data-equipment-detail={data.equipmentDetail || undefined}
      data-generation={generationPhase}
      data-generation-source={generation?.sourceId === card.id && generation.phase === "flying" || undefined}
      onPointerLeave={() => clearConnectionHoverHint(cardRef.current)}
      onPointerMoveCapture={(event) => {
        const start = pointerStart.current;
        if (start && event.buttons && Math.hypot(event.clientX - start.x, event.clientY - start.y) >= DRAG_THRESHOLD_PX) start.moved = true;
      }}
      onPointerMove={onPointerMove}
      onPointerDownCapture={onPointerDownCapture}
      onPointerDown={event => {
        // Let child controls receive the gesture before isolating it from the canvas.
        if ((event.target as Element).closest(NON_DRAG_SELECTOR)) event.stopPropagation();
      }}
      onMouseDownCapture={event => {
        const target = event.target as Element;
        if (visualLevel === "inspector" && target.closest(".node-inspector-content, .node-inspector-footer")
          && !target.closest(NON_DRAG_SELECTOR)) {
          if (hitsText(target, event.clientX, event.clientY)) event.stopPropagation();
          else {
            event.preventDefault();
            window.getSelection()?.removeAllRanges();
          }
        }
      }}
      onMouseDown={event => {
        if ((event.target as Element).closest(NON_DRAG_SELECTOR)) event.stopPropagation();
      }}
      onClick={(event) => {
        const start = pointerStart.current;
        if ((visualLevel === "inspector" || visualLevel === "workspace") && window.getSelection()?.toString()) return;
        if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || connectingNodeId || dragging) return;
        if (event.detail !== 0 && start && (start.moved || Math.hypot(event.clientX - start.x, event.clientY - start.y) >= DRAG_THRESHOLD_PX)) return;
        if ((event.target as HTMLElement).closest("button, input, textarea, select, label, a, [contenteditable='true'], .react-flow__handle")) return;
        if (support.inspector && (visualLevel === "node" || visualLevel === "preview")) openInspector(card.id);
      }}
    >
      {visualLevel === "workspace" && selected && <NodeResizeControl
        className="container-resize-arc" position="bottom-right"
        minWidth={WORKSPACE_MIN_SIZE.width} minHeight={WORKSPACE_MIN_SIZE.height}
        maxWidth={4096} maxHeight={4096}
        onResizeEnd={(_event, size) => resizeWorkspace(card.id, size)} />}
      <ActivityGlow phase={activity.phase} />
      {!card.ephemeral ? (
        <ConnectionHoverHint />
      ) : null}
      {!card.ephemeral ? ([
        [Position.Top, "top"], [Position.Right, "right"], [Position.Bottom, "bottom"], [Position.Left, "left"],
      ] as const).map(([position, side]) => (
        <Handle key={side} id={`boundary-${side}`} type="source" position={position}
          className={`semantic-handle semantic-handle--${side}`} data-connection-side={side}
          aria-label={t("Start a relationship from the {v0} edge of {v1}", { v0: String(side), v1: String(card.name) })} />
      )) : null}

      {visualLevel !== "inspector" && <EquipmentToggle card={card} />}
      {visualLevel === "workspace" ? <WorkspaceSurface card={card} /> : <>
        <header className="card-header node-surface-header">
          <div className="card-kind-icon" aria-hidden="true"><CatalogIcon definition={definition} size={18} /></div>
          <div className="card-title-group">
            <span className="card-eyebrow">{label}</span>
            <h2 title={card.name}>{card.name}</h2>
            <input className="card-name-input nodrag nopan" defaultValue={card.name}
              aria-label={t("{v0} name", { v0: String(label) })}
              onBlur={(event) => {
                const name = event.currentTarget.value.trim();
                if (name && name !== card.name) void updateCard(card.id, { name });
              }}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
          </div>
          <div className="card-status" data-status={displayStatus} title={`${t('Status')}: ${t(statusLabel(displayStatus))}`}>
            <span aria-hidden="true" /><span>{t(statusLabel(displayStatus))}</span>
          </div>
          {(visualLevel === "node" || visualLevel === "preview") && support.preview ? (
            <IconButton
              icon={visualLevel === "node" ? Maximize2 : Minus}
              size={visualLevel === "node" ? "xs" : "sm"}
              quiet={visualLevel === "preview"}
              className={`node-surface-toggle ${visualLevel === "node" ? "node-surface-restore" : ""}`}
              label={t("{v0} {v1} card", { v0: String(visualLevel === "node" ? t("Expand") : t("Collapse")), v1: String(card.name) })}
              title={visualLevel === "node" ? t("Expand card") : t("Collapse to node")}
              onClick={() => {
                if (visualLevel === "node") showPreview(card.id);
                else hidePreview(card.id);
              }} />
          ) : null}
          <IconButton icon={X} size="sm" quiet className="node-surface-close"
            onClick={() => closeInspector(card.id)} label={t("Close {v0} inspector", { v0: String(card.name) })} />
        </header>

        <div className="node-preview-content" aria-hidden={visualLevel !== "preview"}>
          <NodePreview card={card} />
          <span className="node-preview-hint">{t("Click for details")}</span>
        </div>

        <div className="card-body node-inspector-content" aria-hidden={visualLevel !== "inspector"}>
          <CardContent card={card} level={level} />
        </div>

        <footer className="card-footer node-inspector-footer">
          {definition?.traits.includes("core.agent") && !card.ephemeral ? <EquipmentToggle card={card} />
            : <span className="card-id">{card.ephemeral ? "synthetic" : card.id.slice(0, 8)}</span>}
          <div className="card-footer-actions">
            {!card.ephemeral ? <IconButton icon={Trash2} danger
              onClick={() => { dismissSurface(card.id); void deleteCard(card.id); }} label={t("Remove {v0}", { v0: String(card.name) })}
              title={t("Remove object (Ctrl+Z to undo)")} /> : null}
            {support.workspace ? (
              <button type="button" className="card-expand-button" aria-label={definition?.traits.includes("library.readable") ? t("打开阅读器") : t("Open workspace")} title={definition?.traits.includes("library.readable") ? t("打开阅读器") : t("Open workspace")} onClick={() => {
                if (definition?.traits.includes("library.readable")) cardRef.current?.dispatchEvent(new Event("oaw:expand-reader"));
                else openWorkspace(card.id);
              }}>
                {definition?.traits.includes("library.readable") ? <BookOpen size={18}/> : <>{t("Open workspace")} <ExternalLink size={13}/></>}
              </button>
            ) : null}
          </div>
        </footer>
      </>}
    </article>
  );
}

function CollectionAwareCard(props:NodeProps<CanvasNode>) {
  useLocale();
  const owner=props.data.collectionOwner as string|undefined;
  const hovered=useCollectionHover(s=>owner?s.members[owner]:undefined);
  const setHover=useCollectionHover(s=>s.set);
  const cards=useWorldStore(s=>s.cards);
  if(!owner)return <WorldCardNodeComponent {...props}/>;
  const collection=cards.find(c=>c.id===owner);
  const neighbor=hovered&&Math.abs(cards.filter(c=>c.parent_id===owner).findIndex(c=>c.id===hovered)-Number(props.data.stackIndex))===1;
  return <div className={`shadow-stack-member ${hovered===props.id?"is-hovered":neighbor?"is-neighbor":""}`} onPointerEnter={()=>setHover(owner,props.id)} onPointerLeave={()=>setHover(owner)}>
    <div className="shadow-stack-face" {...{inert:""}} aria-hidden="true"><WorldCardNodeComponent {...props}/></div>
    <button className="nodrag nopan" disabled={Boolean(props.data.collectionFading)} aria-label={t("展开集合 · {v0}", { v0: String(props.data.card.name) })} onClick={e=>{e.stopPropagation();if(collection)void useWorldStore.getState().updateCard(owner,{config:{...collection.config,display_state:"expanded"}});}}/>
  </div>;
}
export const WorldCardNode = memo(CollectionAwareCard);
