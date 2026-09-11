import { Atom, Bot, Boxes, FileText, Image, MessagesSquare, Puzzle, Scan, Sparkles, Workflow, Wrench } from "lucide-react";
import type { NodeTypeCatalogItem } from "../types/world";

const icons = { atom: Atom, bot: Bot, boxes: Boxes, "file-text": FileText, image: Image, "messages-square": MessagesSquare, scan: Scan, sparkles: Sparkles, workflow: Workflow, wrench: Wrench };

export function CatalogIcon({ definition, size = 18 }: {
  definition?: Pick<NodeTypeCatalogItem, "icon" | "icon_url">; size?: number;
}) {
  if (definition?.icon_url) return <span aria-hidden="true" className="catalog-asset-icon" style={{
    display: "inline-block", flexShrink: 0, width: size, height: size, backgroundColor: "currentColor",
    mask: `url("${definition.icon_url}") center / contain no-repeat`,
    WebkitMask: `url("${definition.icon_url}") center / contain no-repeat`,
  }} />;
  const Icon = icons[definition?.icon as keyof typeof icons] ?? Puzzle;
  return <Icon size={size} strokeWidth={1.7} aria-hidden="true" />;
}
