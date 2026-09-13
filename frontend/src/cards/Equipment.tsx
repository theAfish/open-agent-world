import { t, useLocale } from "../i18n";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Backpack, ExternalLink, Plus, X } from "lucide-react";
import type { MouseEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import { canEquip, equipmentOwner, useEquipmentDrag, useEquipmentPanel } from "../state/equipment";
import { getConnectionOptions } from "../state/relationships";
import type { WorldCard } from "../types/world";
import type { CanvasNode } from "./types";
import { CatalogIcon } from "../components/CatalogIcon";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import "./equipment.css";
import { ActivityGlow } from "../effects/ActivityGlow";
import { useNodeActivity } from "../effects/useNodeActivity";

function useToggleEquipment(ownerId: string) {
  const cards = useWorldStore((state) => state.cards);
  return () => {
    const panel = useEquipmentPanel.getState();
    if (panel.openIds.includes(ownerId)) {
      cards.filter((item) => equipmentOwner(item, cards)?.id === ownerId)
        .forEach((item) => useNodeSurfaceStore.getState().dismiss(item.id));
    }
    panel.toggle(ownerId);
  };
}

export function EquipmentToggle({ card }: { card: WorldCard }) {
  useLocale();
  const catalog = useWorldStore((s) => s.catalog);
  const count = useWorldStore((s) => s.cards.filter((item) => item.equipment?.owner_id === card.id).length);
  const openIds = useEquipmentPanel((state) => state.openIds);
  const toggle = useToggleEquipment(card.id);
  if (card.ephemeral || !catalog.node_types.find((type) => type.id === card.type)?.traits.includes("core.agent")) return null;
  return <button className="equipment-toggle nodrag nopan" aria-label={t("Equipment for {v0}", { v0: String(card.name) })} aria-expanded={openIds.includes(card.id)}
    title={t("Equipment")} onClick={toggle}><Backpack size={14} /><span>{count}</span></button>;
}

export function EquipmentPanelNode({ data }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const cards = useWorldStore((s) => s.cards);
  const catalog = useWorldStore((s) => s.catalog);
  const { resource, targetId } = useEquipmentDrag();
  const toggle = useToggleEquipment(card.id);
  const eligible = resource && canEquip(resource, card, catalog, cards);
  const count = cards.filter((item) => equipmentOwner(item, cards)?.id === card.id).length;
  const slots = Math.max(2, count + 1);
  return <section className={`equipment-panel nodrag nopan ${eligible ? "is-eligible" : ""} ${targetId === card.id ? "is-active" : ""}`}
    data-equip-target={eligible ? card.id : undefined} data-equipment-panel={card.id} aria-label={t("{v0} equipment slots", { v0: String(card.name) })}>
    <header><Backpack size={14} /><span>{t("Equipment")}</span><small>{count}</small>
      <button aria-label={t("Close equipment slots")} onClick={toggle}><X size={13} /></button></header>
    <div className="equipment-slot-grid">{Array.from({ length: slots }, (_, index) =>
      <div className={`equipment-slot ${index < count ? "is-filled" : ""}`} key={index}>{index >= count && <Plus size={16} />}</div>)}</div>
  </section>;
}

export function EquipmentCardNode({ data }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const activity = useNodeActivity(card);
  const update = useWorldStore((s) => s.updateCard);
  const cards = useWorldStore((s) => s.cards);
  const catalog = useWorldStore((s) => s.catalog);
  const owner = equipmentOwner(card, cards);
  const origin = data.equipmentOrigin;
  const inspect = () => {
    const surfaces = useNodeSurfaceStore.getState();
    if (origin) { surfaces.dismiss(card.id); return; }
    // One detail per backpack keeps its source and controls unambiguous.
    cards.filter((item) => equipmentOwner(item, cards)?.id === owner?.id)
      .forEach((item) => surfaces.dismiss(item.id));
    surfaces.openInspector(card.id);
  };
  const bindingOwner = cards.find((item) => item.id === card.equipment?.owner_id);
  const options = bindingOwner ? getConnectionOptions(catalog, bindingOwner.type, card.type) : [];
  const unequip = () => void update(card.id, { equipment: null, parent_id: null,
    position: { x: (owner?.position.x ?? card.position.x) + 460, y: owner?.position.y ?? card.position.y } });
  const definition = catalog.node_types.find((type) => type.id === card.type);
  const onOriginClick = (event: MouseEvent<HTMLDivElement>) => {
    if (origin && !(event.target as HTMLElement).closest(".equipment-item-remove, select")) inspect();
  };
  return <div className={`equipment-card nodrag nopan ${origin ? "is-open-origin" : ""}`} data-card-id={origin ? undefined : card.id}
    data-equipment-origin={origin ? card.id : undefined} data-activity={activity.phase} aria-label={t("{v0} equipment", { v0: String(card.name) })} onClick={onOriginClick}>
    <ActivityGlow phase={activity.phase} />
    {!origin && <Handle type="source" position={Position.Left} id="boundary-left" aria-label={t("Connect {v0} left", { v0: String(card.name) })} />}
    <button type="button" className="equipment-item-open" onClick={inspect} title={origin ? t("Hide details") : card.name}
      aria-label={origin ? t("Hide {v0} details", { v0: String(card.name) }) : card.name} aria-expanded={!!origin}><CatalogIcon definition={definition} size={17} /><span>{card.name}</span></button>
    <button className="equipment-item-remove" onClick={unequip} aria-label={t("Unequip {v0}", { v0: String(card.name) })} title={t("Unequip")}><ExternalLink size={12} /></button>
    {options.length > 1 && <select aria-label={t("{v0} relationship", { v0: String(card.name) })} value={card.equipment?.relationship ?? options[0].value}
      onChange={(event) => void update(card.id, { equipment: { owner_id: bindingOwner!.id, relationship: event.target.value } })}>
      {options.map((option) => <option value={option.value} key={option.value}>{t(option.label)}</option>)}
    </select>}
    {!origin && <Handle type="source" position={Position.Right} id="boundary-right" aria-label={t("Connect {v0} right", { v0: String(card.name) })} />}
  </div>;
}
