import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Backpack, ExternalLink, Plus, X, Puzzle } from "lucide-react";
import { createPortal } from "react-dom";
import { useWorldStore } from "../state/worldStore";
import { canEquip, equipmentOwner, useEquipmentDrag, useEquipmentPanel } from "../state/equipment";
import { getConnectionOptions } from "../state/relationships";
import type { WorldCard } from "../types/world";
import type { CanvasNode } from "./types";
import { CardContent, CATALOG_ICONS } from "./CardFrame";
import { WorkspaceSurface } from "./NodeWorkspace";
import { nodeSurfaceSupport } from "../state/nodeSurfaces";
import "./equipment.css";

export function EquipmentToggle({ card }: { card: WorldCard }) {
  const catalog = useWorldStore((s) => s.catalog);
  const count = useWorldStore((s) => s.cards.filter((item) => item.equipment?.owner_id === card.id).length);
  const { openIds, toggle } = useEquipmentPanel();
  if (card.ephemeral || !catalog.node_types.find((type) => type.id === card.type)?.traits.includes("core.agent")) return null;
  return <button className="equipment-toggle nodrag nopan" aria-label={`Equipment for ${card.name}`} aria-expanded={openIds.includes(card.id)}
    title="Equipment" onClick={() => toggle(card.id)}><Backpack size={14} /><span>{count}</span></button>;
}

export function EquipmentPanelNode({ data }: NodeProps<CanvasNode>) {
  const card = data.card;
  const cards = useWorldStore((s) => s.cards);
  const catalog = useWorldStore((s) => s.catalog);
  const { resource, targetId } = useEquipmentDrag();
  const toggle = useEquipmentPanel((s) => s.toggle);
  const eligible = resource && canEquip(resource, card, catalog, cards);
  const count = cards.filter((item) => equipmentOwner(item, cards)?.id === card.id).length;
  const slots = Math.max(2, count + 1);
  return <section className={`equipment-panel nodrag nopan ${eligible ? "is-eligible" : ""} ${targetId === card.id ? "is-active" : ""}`}
    data-equip-target={eligible ? card.id : undefined} data-equipment-panel={card.id} aria-label={`${card.name} equipment slots`}>
    <header><Backpack size={14} /><span>Equipment</span><small>{count}</small>
      <button aria-label="Close equipment slots" onClick={() => toggle(card.id)}><X size={13} /></button></header>
    <div className="equipment-slot-grid">{Array.from({ length: slots }, (_, index) =>
      <div className={`equipment-slot ${index < count ? "is-filled" : ""}`} key={index}>{index >= count && <Plus size={16} />}</div>)}</div>
  </section>;
}

export function EquipmentCardNode({ data }: NodeProps<CanvasNode>) {
  const card = data.card;
  const inspect = useEquipmentPanel((state) => state.inspect);
  const update = useWorldStore((s) => s.updateCard);
  const cards = useWorldStore((s) => s.cards);
  const catalog = useWorldStore((s) => s.catalog);
  const owner = equipmentOwner(card, cards);
  const bindingOwner = cards.find((item) => item.id === card.equipment?.owner_id);
  const options = bindingOwner ? getConnectionOptions(catalog, bindingOwner.type, card.type) : [];
  const unequip = () => void update(card.id, { equipment: null, parent_id: null,
    position: { x: (owner?.position.x ?? card.position.x) + 460, y: owner?.position.y ?? card.position.y } });
  const Icon = CATALOG_ICONS[catalog.node_types.find((type) => type.id === card.type)?.icon ?? ""] ?? Puzzle;
  return <div className="equipment-card nodrag nopan" data-card-id={card.id} aria-label={`${card.name} equipment`}>
    <Handle type="source" position={Position.Left} id="boundary-left" aria-label={`Connect ${card.name} left`} />
    <button className="equipment-item-open" onClick={() => inspect(card.id)} title={card.name}><Icon size={17} /><span>{card.name}</span></button>
    <button className="equipment-item-remove" onClick={unequip} aria-label={`Unequip ${card.name}`} title="Unequip"><ExternalLink size={12} /></button>
    {options.length > 1 && <select aria-label={`${card.name} relationship`} value={card.equipment?.relationship ?? options[0].value}
      onChange={(event) => void update(card.id, { equipment: { owner_id: bindingOwner!.id, relationship: event.target.value } })}>
      {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
    </select>}
    <Handle type="source" position={Position.Right} id="boundary-right" aria-label={`Connect ${card.name} right`} />
  </div>;
}

export function EquipmentInspector() {
  const { inspectedId, inspect } = useEquipmentPanel();
  const card = useWorldStore((state) => state.cards.find((item) => item.id === inspectedId));
  const catalog = useWorldStore((state) => state.catalog);
  if (!card) return null;
  const workspace = nodeSurfaceSupport(card.type, catalog).workspace;
  return createPortal(<section key={card.id} className={`equipment-inspector nodrag nopan nowheel ${workspace ? "has-workspace" : ""}`} role="dialog" aria-label={`${card.name} equipment details`}>
    <header><strong>{card.name}</strong><button onClick={() => inspect()} aria-label="Close equipment"><X size={18} /></button></header>
    {workspace ? <WorkspaceSurface card={card} onClose={() => inspect()} /> : <CardContent card={card} level="inspector" />}
  </section>, document.body);
}
