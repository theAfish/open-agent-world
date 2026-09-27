import { Atom, Bot, Boxes, Crown, FileText, HardDrive, Image, MessagesSquare, Puzzle, Scan, Sparkles, Workflow, Wrench } from "lucide-react";
import type { NodeTypeCatalogItem } from "../types/world";

const icons = { "hard-drive": HardDrive, atom: Atom, bot: Bot, boxes: Boxes, crown: Crown, "file-text": FileText, image: Image, "messages-square": MessagesSquare, scan: Scan, sparkles: Sparkles, workflow: Workflow, wrench: Wrench };

export function CatalogIcon({ definition, size = 18 }: {
  definition?: Pick<NodeTypeCatalogItem, "icon" | "icon_url"> & { id?: string }; size?: number;
}) {
  // Keep existing catalogs in sync with the Science structure viewer's mark.
  if (definition?.id === "xrd.structure-canvas") return <Atom size={size} strokeWidth={1.7} aria-hidden="true" />;
  if (definition?.icon_url) return <span aria-hidden="true" className="catalog-asset-icon" style={{
    display: "inline-block", flexShrink: 0, width: `var(--catalog-icon-size, ${size}px)`, height: `var(--catalog-icon-size, ${size}px)`, backgroundColor: "currentColor",
    mask: `url("${definition.icon_url}") center / contain no-repeat`,
    WebkitMask: `url("${definition.icon_url}") center / contain no-repeat`,
  }} />;
  if (definition?.icon === "xrd-spectrum") return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="xrd-spectrum-icon">
    <path d="M2 20h20M2 17h3l1.5-7L8 17h2l2-14 2 14h3l1.5-9 1.5 9h2" />
  </svg>;
  const Icon = icons[definition?.icon as keyof typeof icons] ?? Puzzle;
  return <Icon size={size} strokeWidth={1.7} aria-hidden="true" />;
}
